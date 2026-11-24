"""Render structured CV/letter documents to PDF with headless Chromium.

The worker prints the built ``frontend/dist/print.html`` (the same React view the editor
uses). The document JSON and optional portrait are injected with an init script; every
request that is not a file inside the dist folder is aborted, so rendering never touches
the network. The built page references absolute ``/assets/...`` paths and ES modules,
which Chromium refuses over ``file://``; the dist folder is therefore served from a
virtual origin through request routing instead of a file URL.

One sandboxed browser per process runs on a dedicated asyncio thread. It prints one
document at a time and relaunches after a crash. Document text is never
logged.
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import io
import json
import logging
import math
import mimetypes
import os
import re
import threading
import unicodedata
from bisect import bisect_right
from collections import Counter
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import closing
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from hiring_scraper.documents.schema import CvDocument, LetterDocument, walk_units
from hiring_scraper.documents.text import cv_text, letter_text, unit_full_text

LOG = logging.getLogger(__name__)

ORIGIN = 'http://radius-print.invalid'
PAGE_LIMITS = {'cv': 2, 'letter': 1}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 192 * 1024
MAX_PDF_PAGES = 12
_MM_TO_PX = 96 / 25.4
_PAGE_MM = {'a4': (210.0, 297.0), 'letter': (215.9, 279.4)}
_MIN_FIRST_PAGE_FILL = 0.80
_PAGE_MARGIN_MM = 16.0  # @page vertical margin in classic-serif-v1.css
# Rendering returns callers to their worker threads before text extraction.
_PDFIUM_LOCK = threading.Lock()


class RenderUnavailable(RuntimeError):
    """Chromium or the built print entry is not available."""


class RenderFailed(RuntimeError):
    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code


@dataclass
class RenderResult:
    pdf: bytes
    pages: int
    text: str
    checks: list[dict]
    page_limit_exceeded: bool
    telemetry: dict = field(default_factory=dict)


def export_verified(checks: list[dict]) -> bool:
    """Require readable text and applicant details.

    Layout and pdfium checks are advisory: pdfium infers word gaps heuristically and
    can join words on valid Chromium PDFs, so it must never block a download.
    """
    required = {'text_extractable', 'glyphs', 'special_characters', 'name_present', 'contacts_present'}
    passed = {check['code'] for check in checks if check.get('status') == 'passed'}
    return required <= passed


def _dist() -> Path:
    configured = os.environ.get('RADIUS_FRONTEND_DIST')
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[2] / 'frontend' / 'dist').resolve()


def _chromium_installed() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    roots = []
    if os.environ.get('PLAYWRIGHT_BROWSERS_PATH') not in (None, '', '0'):
        roots.append(Path(os.environ['PLAYWRIGHT_BROWSERS_PATH']))
    roots.append(Path.home() / '.cache' / 'ms-playwright')
    for root in roots:
        try:
            if any(p.name.startswith(('chromium-', 'chromium_headless_shell-')) for p in root.iterdir()):
                return True
        except OSError:
            continue
    return False


def available() -> bool:
    """Chromium (Playwright) and the built ``print.html`` are present."""
    return (_dist() / 'print.html').is_file() and _chromium_installed()


class _Renderer:
    """Owns one asyncio loop thread and one lazily launched Chromium."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._semaphore: asyncio.Semaphore | None = None
        self._launch_lock: asyncio.Lock | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                asyncio.set_event_loop(loop)
                self._semaphore = asyncio.Semaphore(1)
                self._launch_lock = asyncio.Lock()
                ready.set()
                loop.run_forever()

            thread = threading.Thread(target=run, name='radius-pdf-render', daemon=True)
            thread.start()
            ready.wait(5)
            self._loop, self._thread = loop, thread
            return loop

    async def _browser_instance(self) -> Any:
        assert self._launch_lock is not None
        async with self._launch_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            await self._close_browser()
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise RenderUnavailable('Playwright is not installed.') from None
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True, chromium_sandbox=True,
                    args=['--disable-dev-shm-usage', '--no-first-run', '--disable-extensions'],
                )
            except Exception as error:  # noqa: BLE001 - missing executable or sandbox
                await self._close_browser()
                raise RenderUnavailable(f'Chromium could not start: {type(error).__name__}') from None
            return self._browser

    async def _close_browser(self) -> None:
        browser, pw = self._browser, self._playwright
        self._browser = self._playwright = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:  # noqa: BLE001
                pass

    async def _render(self, payload: dict, page_format: str, timeout: float) -> tuple[bytes, dict]:
        assert self._semaphore is not None
        async with self._semaphore:
            for attempt in (1, 2):
                browser = await self._browser_instance()
                try:
                    return await asyncio.wait_for(self._render_once(browser, payload, page_format), timeout)
                except asyncio.TimeoutError:
                    raise RenderFailed('timeout', 'Rendering timed out.') from None
                except RenderFailed:
                    raise
                except Exception as error:  # noqa: BLE001 - crash or protocol error
                    if attempt == 1 and not browser.is_connected():
                        LOG.warning('PDF renderer browser crashed; relaunching')
                        continue
                    raise RenderFailed('browser_error', type(error).__name__) from None
        raise RenderFailed('browser_error')

    async def _render_once(self, browser: Any, payload: dict, page_format: str) -> tuple[bytes, dict]:
        dist = _dist()
        width_mm, height_mm = _PAGE_MM[page_format]
        context = await browser.new_context(
            viewport={'width': math.ceil(width_mm * _MM_TO_PX), 'height': math.ceil(height_mm * _MM_TO_PX)},
            service_workers='block',
            accept_downloads=False,
        )
        try:
            async def route(route: Any) -> None:
                url = route.request.url
                if not url.startswith(ORIGIN + '/'):
                    await route.abort('blockedbyclient')
                    return
                rel = unquote(urlsplit(url).path).lstrip('/')
                target = (dist / rel).resolve()
                if not target.is_relative_to(dist) or not target.is_file():
                    await route.abort('blockedbyclient')
                    return
                content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                if target.suffix in ('.js', '.mjs'):
                    content_type = 'text/javascript'
                await route.fulfill(status=200, body=target.read_bytes(), content_type=content_type)

            await context.route('**/*', route)
            await context.add_init_script(
                'window.__RADIUS_DOCUMENT__ = ' + json.dumps(payload, ensure_ascii=True) + ';'
            )
            page = await context.new_page()
            await page.emulate_media(media='print')
            await page.goto(ORIGIN + '/print.html', wait_until='load')
            await page.wait_for_function('() => Boolean(document.body && document.body.dataset.ready)')
            state = await page.evaluate('() => document.body.dataset.ready')
            if state != '1':
                raise RenderFailed('view_error', 'The document view reported an error.')
            printable_px = (height_mm - 2 * _PAGE_MARGIN_MM) * _MM_TO_PX
            limit = PAGE_LIMITS[payload['kind']]
            root = page.locator('.doc-page-content')
            height = await root.evaluate('(el) => el.scrollHeight')
            if height > printable_px * MAX_PDF_PAGES:
                raise RenderFailed('document_too_large', 'The document exceeds the PDF page budget.')
            # Include one sentinel page so a clipped PDF can never pass verification.
            pdf_options = {'prefer_css_page_size': True, 'print_background': True,
                           'page_ranges': f'1-{MAX_PDF_PAGES + 1}'}
            pdf = await page.pdf(**pdf_options)
            compact = False
            if payload['kind'] == 'cv':
                from pypdf import PdfReader

                doc_model = CvDocument.model_validate(payload['document'])
                page_root = page.locator('.doc-page')
                reader = PdfReader(io.BytesIO(pdf))
                if len(reader.pages) > 1:
                    first_fill = _page_fill_ratios(reader)[0]
                    if first_fill < _MIN_FIRST_PAGE_FILL or len(reader.pages) > limit:
                        await page_root.evaluate("(el) => { el.classList.add('doc-page--fill-first'); window.__RADIUS_SYNC_LAYOUT__?.(); }")
                        candidate = await page.pdf(**pdf_options)
                        relaxed = PdfReader(io.BytesIO(candidate))
                        improved = (len(relaxed.pages) < len(reader.pages)
                                    or (len(relaxed.pages) == len(reader.pages)
                                        and _page_fill_ratios(relaxed)[0] > first_fill))
                        if improved and not _orphan_entry_breaks(
                            doc_model,
                            [part.extract_text() for part in relaxed.pages],
                        ):
                            pdf, reader = candidate, relaxed
                        else:
                            await page_root.evaluate("(el) => { el.classList.remove('doc-page--fill-first'); window.__RADIUS_SYNC_LAYOUT__?.(); }")
                if height <= printable_px * limit and len(reader.pages) > limit:
                    # Keeping entries together can add a page even when their total height fits.
                    await page_root.evaluate("(el) => { el.classList.add('doc-page--compact'); window.__RADIUS_SYNC_LAYOUT__?.(); }")
                    candidate = await page.pdf(**pdf_options)
                    if len(PdfReader(io.BytesIO(candidate)).pages) <= limit:
                        pdf, compact = candidate, True
                    else:
                        await page_root.evaluate("(el) => { el.classList.remove('doc-page--compact'); window.__RADIUS_SYNC_LAYOUT__?.(); }")
            telemetry = await page.evaluate(
                """([printable, limit]) => {
                    const root = document.querySelector('.doc-page-content') || document.body;
                    const top = root.getBoundingClientRect().top;
                    const height = root.scrollHeight;
                    const units = Array.from(document.querySelectorAll('[data-unit-id]'));
                    const photo = root.querySelector('.doc-cv-photo');
                    const beyond = [];
                    for (const el of units) {
                        const r = el.getBoundingClientRect();
                        if (r.bottom - top > printable * limit + 0.5) beyond.push(el.dataset.unitId);
                    }
                    return {content_height_px: height, beyond_limit_units: beyond,
                            photo_used: Boolean(photo?.naturalWidth)};
                }""",
                [printable_px, limit],
            )
            height = float(telemetry.get('content_height_px') or 0)
            est_pages = max(1, math.ceil(height / printable_px)) if height else 1
            overflow_by_page = []
            for index in range(est_pages):
                used = min(printable_px, max(0.0, height - index * printable_px))
                overflow_by_page.append(round(used if index >= limit else 0.0, 1))
            telemetry.update({
                'printable_height_px': round(printable_px, 1),
                'estimated_pages': est_pages,
                'overflow_px': round(max(0.0, height - limit * printable_px), 1),
                'overflow_px_by_page': overflow_by_page,
                'compact_layout': compact,
            })
            return pdf, telemetry
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass

    def render(self, payload: dict, page_format: str, timeout: float) -> tuple[bytes, dict]:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._render(payload, page_format, timeout), loop)
        try:
            return future.result(timeout + 30)
        except FutureTimeout:
            future.cancel()
            raise RenderFailed('timeout', 'Rendering timed out.') from None

    def shutdown(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_browser(), loop).result(10)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)


_RENDERER = _Renderer()
atexit.register(_RENDERER.shutdown)


def _squash(text: str) -> str:
    # Case-insensitive: the template uppercases names and headings with CSS.
    return ''.join(unicodedata.normalize('NFC', text).casefold().split())


def _check(code: str, status: str, detail: str) -> dict:
    return {'code': code, 'status': status, 'detail': detail}


def _reading_order_check(model: CvDocument | LetterDocument, text: str) -> dict:
    def normalize(value: str) -> str:
        normalized = ' '.join(unicodedata.normalize('NFC', value).casefold().split())
        # pypdf inserts whitespace when a compound wraps at its existing hyphen.
        return re.sub(r'(?<=\w)([-\u2010]) (?=\w)', r'\1', normalized)

    extracted = normalize(text)
    cursor = 0
    for unit in walk_units(model):
        if unit.hidden or not (prefix := normalize(unit_full_text(unit))[:30]):
            continue
        index = extracted.find(prefix, cursor)
        if index == -1:
            return _check('reading_order', 'failed', 'A document unit was missing or out of order.')
        cursor = index + len(prefix)
    return _check('reading_order', 'passed', 'Document units appear in reading order.')


def _orphan_entry_breaks(model: CvDocument, page_texts: list[str]) -> list[str]:
    """Find entries with orphaned headings/headers, dangling intro captions, or lone trailing bullets."""
    if len(page_texts) < 2:
        return []
    runs = [_squash(page) for page in page_texts]
    boundaries = list(accumulate(map(len, runs)))
    extracted = ''.join(runs)
    broken: list[str] = []
    cursor = 0
    for section in model.sections:
        section_start = cursor
        for entry_idx, entry in enumerate(section.entries):
            start = cursor
            first_unit = None
            intro_ends: list[tuple[int, bool]] = []
            bullet_ends: list[int] = []
            bullets = [unit for unit in entry.units if not unit.hidden]
            intro = [unit for unit in entry.intro if not unit.hidden]
            for idx, unit in enumerate(intro + bullets):
                run = _squash(unit_full_text(unit))
                if not run or (offset := extracted.find(run, cursor)) == -1:
                    continue
                cursor = offset + len(run)
                if first_unit is None:
                    first_unit = offset
                if idx < len(intro):
                    intro_ends.append((cursor - 1, unit_full_text(unit).rstrip().endswith(':')))
                else:
                    bullet_ends.append(cursor - 1)
            if first_unit is None or not (intro_ends or bullet_ends):
                continue
            # The company is the first header line. A generic import placeholder
            # may render the title in its place.
            header = -1
            for value in (entry.locked.company, entry.locked.title):
                if value.strip():
                    header = extracted.rfind(_squash(value), start, first_unit)
                    if header != -1:
                        break
            if header == -1:
                continue
            header_page = bisect_right(boundaries, header)
            if entry_idx == 0 and section.title.strip():
                section_heading = extracted.rfind(_squash(section.title), section_start, header)
                if section_heading != -1 and bisect_right(boundaries, section_heading) < header_page:
                    broken.append(entry.id)
                    continue
            bullet_pages = Counter(bisect_right(boundaries, end) for end in bullet_ends)
            if not bullet_ends:
                continue
            first_bullet_page = bisect_right(boundaries, bullet_ends[0])
            dangling_caption = any(
                is_colon and bisect_right(boundaries, end) < first_bullet_page
                for end, is_colon in intro_ends
            )
            spills = any(page > header_page for page in bullet_pages)
            if not spills and not dangling_caption:
                continue
            has_bullet_on_header = bullet_pages[header_page] >= 1
            lone_continuation = len(bullet_ends) >= 3 and any(
                count < 2 for page, count in bullet_pages.items() if page > header_page
            )
            is_project_list = entry.kind != 'study' and any(unit.lead_in.strip() for unit in bullets)
            lone_project_split = is_project_list and len(bullet_pages) > 1 and any(
                count < 2 for count in bullet_pages.values()
            )
            if not has_bullet_on_header or dangling_caption or lone_continuation or lone_project_split:
                broken.append(entry.id)
    return broken


def _text_checks(
    kind: str,
    model: CvDocument | LetterDocument,
    text: str,
    pages: int,
    *,
    page_texts: list[str] | None = None,
) -> list[dict]:
    checks: list[dict] = []
    limit = PAGE_LIMITS[kind]
    squashed = _squash(text)
    checks.append(_check('page_count', 'passed' if pages <= limit else 'warning',
                         f'{pages} page(s); limit {limit}.'))
    checks.append(_check('text_extractable', 'passed' if squashed else 'failed',
                         f'{len(text)} characters extracted.'))
    broken = text.count('\ufffd') + text.count('(cid:')
    checks.append(_check('glyphs', 'passed' if not broken else 'failed', f'{broken} unmapped glyph(s).'))
    checks.append(_reading_order_check(model, text))
    source = cv_text(model) if isinstance(model, CvDocument) else letter_text(model)
    folded_text = unicodedata.normalize('NFC', text).casefold()
    special = {f for ch in unicodedata.normalize('NFC', source).casefold()
               if ord(ch) > 127 and ch.isalpha() and len(f := ch.casefold()) == 1}
    missing_chars = sorted(ch for ch in special if ch not in folded_text)
    checks.append(_check('special_characters', 'passed' if not missing_chars else 'failed',
                         f'{len(special) - len(missing_chars)} of {len(special)} non-ASCII letters present.'))
    if isinstance(model, CvDocument):
        name = model.header.name
        contacts = model.header.contacts
        missing = [c.kind for c in contacts if _squash(c.text) not in squashed]
        checks.append(_check('contacts_present', 'passed' if not missing else 'failed',
                             f'{len(contacts) - len(missing)} of {len(contacts)} contacts present'
                             + (f' (missing: {", ".join(missing)}).' if missing else '.')))
        orphan_breaks = _orphan_entry_breaks(model, page_texts or [])
        checks.append(_check('entry_pagination', 'passed' if not orphan_breaks else 'failed',
                             'Entries break cleanly across pages.'
                             if not orphan_breaks else f'{len(orphan_breaks)} entry split with orphan bullets.'))
    else:
        name = model.sender.name
        contacts = model.sender.lines
        present = sum(_squash(line) in squashed for line in contacts)
        checks.append(_check('contacts_present', 'passed' if present == len(contacts) else 'failed',
                             f'{present} of {len(contacts)} sender lines present.'))
    normalized_name = _squash(name)
    has_name = bool(normalized_name and normalized_name in squashed)
    checks.append(_check('name_present', 'passed' if has_name else 'failed',
                         'Name found in PDF text.' if has_name else 'Name missing from PDF text.'))
    return checks


def _page_fill_ratios(reader: Any) -> list[float]:
    """Estimate vertical fill ratio (0.0–1.0) of each rendered PDF page's printable area."""
    margin_pt = _PAGE_MARGIN_MM * 72.0 / 25.4
    ratios: list[float] = []
    for page in reader.pages:
        height_pt = float(page.mediabox.height)
        printable_pt = max(1.0, height_pt - 2.0 * margin_pt)
        lowest_y: list[float] = []

        def visitor(text: str, cm: list[float], tm: list[float], _font_dict: Any, _font_size: float) -> None:
            if text.strip():
                ty = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
                lowest_y.append(ty)

        try:
            page.extract_text(visitor_text=visitor)
        except Exception:  # noqa: BLE001 - defensive against PDF edge cases
            ratios.append(0.0)
            continue
        if not lowest_y:
            ratios.append(0.0)
            continue
        used_pt = (height_pt - margin_pt) - min(lowest_y)
        ratios.append(round(max(0.0, min(1.0, used_pt / printable_pt)), 3))
    return ratios


def _extract_pdfium_text(pdf: bytes) -> str:
    import pypdfium2 as pdfium

    # PDFium's native calls must not overlap, even for distinct documents.
    with _PDFIUM_LOCK, pdfium.PdfDocument(pdf) as doc:
        pages: list[str] = []
        for page in doc:
            with closing(page), closing(page.get_textpage()) as textpage:
                pages.append(textpage.get_text_bounded())
        return '\n'.join(pages)


def _pdfium_merged_tokens(pypdf_text: str, pdfium_text: str) -> list[str]:
    """Return tokens in pdfium_text that merge two or more adjacent tokens from pypdf_text."""
    py_tokens = unicodedata.normalize('NFC', pypdf_text).split()
    pf_tokens = unicodedata.normalize('NFC', pdfium_text).split()
    py_set = set(py_tokens)
    adjacent_merges = {
        py_tokens[i] + py_tokens[i + 1]
        for i in range(len(py_tokens) - 1)
    }
    merged: list[str] = []
    for token in pf_tokens:
        if token in py_set:
            continue
        if token in adjacent_merges or any(
            token[:idx] in py_set and token[idx:] in py_set
            for idx in range(1, len(token))
        ):
            merged.append(token)
    return merged


def _pdfium_checks(
    model: CvDocument | LetterDocument,
    pypdf_text: str,
    pdfium_text: str,
) -> tuple[list[dict], dict[str, int | bool]]:
    ctrl_count = sum(
        1 for ch in pdfium_text
        if (unicodedata.category(ch) == 'Cc' and ch not in '\r\n\t') or ch == '\ufffd'
    )
    merged = _pdfium_merged_tokens(pypdf_text, pdfium_text)
    order_passed = _reading_order_check(model, pdfium_text)['status'] == 'passed'
    checks = [
        _check(
            'pdfium_control_chars',
            'passed' if ctrl_count == 0 else 'failed',
            'No control or replacement characters in PDFium text.'
            if ctrl_count == 0 else f'{ctrl_count} control or replacement character(s) in PDFium text.',
        ),
        _check(
            'pdfium_word_boundaries',
            'passed' if order_passed and not merged else 'failed',
            'No merged tokens detected and document units appear in order.'
            if order_passed and not merged else (
                f'{len(merged)} merged token(s) in PDFium text.'
                if merged else 'PDFium reading order did not match document units.'
            ),
        ),
    ]
    return checks, {
        'pdfium_control_chars': ctrl_count,
        'pdfium_merged_tokens': len(merged),
        'pdfium_text_parity': (
            unicodedata.normalize('NFC', pypdf_text).split()
            == unicodedata.normalize('NFC', pdfium_text).split()
        ),
    }


def render_document(
    kind: Literal['cv', 'letter'],
    document: dict,
    *,
    photo_png: bytes | None = None,
    timeout: float = 30,
) -> RenderResult:
    """Render ``document`` to PDF and return pages, extracted text and verification checks."""
    if kind not in PAGE_LIMITS:
        raise RenderFailed('invalid_kind', 'Unknown document kind.')
    if not available():
        raise RenderUnavailable('Chromium or frontend/dist/print.html is not available.')
    try:
        model: CvDocument | LetterDocument = (
            CvDocument.model_validate(document) if kind == 'cv' else LetterDocument.model_validate(document)
        )
    except ValidationError:
        raise RenderFailed('invalid_document', 'The document does not match the schema.') from None
    if len(model.model_dump_json().encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise RenderFailed('document_too_large', 'The document exceeds the PDF input budget.')
    photo = None
    if photo_png and kind == 'cv':
        if len(photo_png) > MAX_PHOTO_BYTES:
            raise RenderFailed('photo_too_large', 'The portrait is too large.')
        mime = 'image/jpeg' if photo_png[:3] == b'\xff\xd8\xff' else 'image/png'
        photo = f'data:{mime};base64,' + base64.b64encode(photo_png).decode('ascii')
    payload = {'kind': kind, 'document': model.model_dump(mode='json'), 'photo': photo}
    pdf, telemetry = _RENDERER.render(payload, model.page, timeout)

    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(pdf))
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise RenderFailed('document_too_large', 'The document exceeds the PDF page budget.')
        page_texts = [page.extract_text() or '' for page in reader.pages]
        page_fill_ratios = _page_fill_ratios(reader)
        text = '\n'.join(page_texts)
        if isinstance(model, CvDocument):
            author = model.header.name.strip()
            title = 'Lebenslauf' if model.language == 'de' else 'CV'
        else:
            author = model.sender.name.strip()
            title = 'Anschreiben' if model.language == 'de' else 'Cover letter'
        writer = PdfWriter(clone_from=reader)
        writer.add_metadata({'/Title': f'{author} – {title}', '/Author': author})
        with io.BytesIO() as output:
            writer.write(output)
            pdf = output.getvalue()
        pdfium_text = _extract_pdfium_text(pdf)
    except RenderFailed:
        raise
    except Exception:  # noqa: BLE001 - corrupt output
        raise RenderFailed('pdf_unreadable', 'The rendered PDF could not be read.') from None
    limit = PAGE_LIMITS[kind]
    exceeded = pages > limit
    checks = _text_checks(kind, model, text, pages, page_texts=page_texts)
    pdfium_checks, pdfium_metrics = _pdfium_checks(model, text, pdfium_text)
    checks.extend(pdfium_checks)
    telemetry.update(pdfium_metrics)
    if exceeded or telemetry.get('overflow_px', 0) > 0:
        checks.append(_check('overflow', 'warning',
                             f'{telemetry.get("overflow_px", 0)} px beyond page {limit}; '
                             f'{len(telemetry.get("beyond_limit_units", []))} unit(s) past the limit.'))
    telemetry['pages'] = pages
    telemetry['page_fill_ratios'] = page_fill_ratios
    if isinstance(model, CvDocument):
        telemetry['orphan_entry_breaks'] = _orphan_entry_breaks(model, page_texts)
    # Chromium can move blocks between pages to honor CSS break rules. Only report
    # a split when the extracted unit crosses an actual PDF page boundary.
    page_runs = [_squash(page) for page in page_texts]
    all_runs = ''.join(page_runs)
    telemetry['split_units'] = [
        unit.id for unit in walk_units(model)
        if not unit.hidden and (unit_run := _squash(unit_full_text(unit)))
        and unit_run in all_runs and not any(unit_run in page for page in page_runs)
    ]
    telemetry['pdf_bytes'] = len(pdf)
    LOG.info('Rendered %s: %d page(s), %d check(s) not passed', kind, pages,
             sum(1 for c in checks if c['status'] != 'passed'))
    return RenderResult(pdf=pdf, pages=pages, text=text, checks=checks,
                        page_limit_exceeded=exceeded, telemetry=telemetry)
