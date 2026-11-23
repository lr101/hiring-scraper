"""Flowing single-column DOCX CV → ``CvDocument`` importer.

Pure and database-free. The file is read as a ZIP archive with ``zipfile`` and parsed
with ``xml.etree``; nothing in it is executed. Archives with macros, DTDs/entities,
too many members or oversized parts are rejected with ``DocxImportError``.

Heuristics target the common flowing CV layout: the name on the first bold or large
paragraph, the headline on the next line, a contact line (phone/email/location, mailto
links), bold all-caps section headings, entries as a company line (optionally a tab or
right-aligned location) followed by an italic/bold title line ending in a date range,
numbered or glyph bullets, "•"/"|" separated skill lines and "Lead: text" bold lead-ins.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from hiring_scraper.documents.import_tex import _classify_contact, _detect_language, _slugify
from hiring_scraper.documents.schema import (
    MAX_SECTION_UNITS,
    MAX_UNIT_TEXT,
    Contact,
    CvDocument,
    DocumentLanguage,
    Entry,
    Header,
    LockedEntry,
    PageSize,
    Section,
    SectionKind,
    SectionLayout,
    Unit,
)

MAX_DOCX_BYTES = 15 * 1024 * 1024
MAX_MEMBERS = 500
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 60 * 1024 * 1024
MAX_PARAGRAPHS = 3000

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_FALSE = frozenset({'0', 'false', 'off', 'none'})
_BULLET_GLYPHS = '•●▪■◦○‣∙·*-–—'
_PRESENT = r'present|current|today|now|ongoing|heute|aktuell|jetzt|laufend|dato'
_DATE = r'(?:(\d{1,2})\s*[/.]\s*)?((?:19|20)\d{2})'
_RANGE_RE = re.compile(
    _DATE + r'\s*(?:–|—|-|bis|to|until)\s*(?:' + _DATE + r'|(' + _PRESENT + r'))',
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(r'(?:^|[\s(|–,])' + _DATE + r'\s*\)?\s*$')
_SECTION_KEYWORDS: tuple[tuple[SectionKind, tuple[str, ...]], ...] = (
    ('experience', ('experience', 'erfahrung', 'werdegang', 'employment', 'beruf', 'career', 'karriere')),
    ('education', ('education', 'ausbildung', 'studium', 'bildung', 'academic', 'studies', 'qualifikation')),
    ('profile', ('profile', 'profil', 'summary', 'about me', 'über mich', 'kurzprofil', 'zusammenfassung')),
    ('list', ('language', 'sprach', 'award', 'auszeichnung', 'certif', 'zertifi', 'interest', 'interessen',
              'hobbies', 'ehrenamt', 'volunteer')),
)


class DocxImportError(ImportError):
    """The DOCX file is unsafe, malformed or contains no recognizable CV."""


@dataclass
class _Run:
    text: str
    bold: bool
    italic: bool
    size: int


@dataclass
class _Para:
    runs: list[_Run]
    numbered: bool
    drawing: bool
    style: str
    links: list[tuple[str, str]] = field(default_factory=list)

    @property
    def raw(self) -> str:
        return ''.join(r.text for r in self.runs)

    @property
    def plain(self) -> str:
        return _collapse(self.raw)

    def _visible(self) -> list[_Run]:
        return [r for r in self.runs if r.text.strip()]

    @property
    def all_bold(self) -> bool:
        runs = self._visible()
        return bool(runs) and all(r.bold for r in runs)

    @property
    def starts_bold(self) -> bool:
        runs = self._visible()
        return bool(runs) and runs[0].bold

    @property
    def italic_share(self) -> float:
        runs = [r for r in self._visible() if not _RANGE_RE.search(r.text) and not _SINGLE_RE.search(r.text)]
        total = sum(len(r.text.strip()) for r in runs)
        if not total:
            return 0.0
        return sum(len(r.text.strip()) for r in runs if r.italic) / total

    @property
    def max_size(self) -> int:
        return max((r.size for r in self._visible()), default=0)


@dataclass
class _EntryDraft:
    entry_id: str
    kind: str
    locked: LockedEntry
    intro: list[tuple[str, str]] = field(default_factory=list)
    units: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _SectionDraft:
    title: str
    kind: SectionKind | None
    layout: SectionLayout = 'bullets'
    units: list[tuple[str, str]] = field(default_factory=list)
    entries: list[_EntryDraft] = field(default_factory=list)
    inline: bool = False
    paragraphs: bool = False


def _collapse(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace(' ', ' ')).strip()


def _flag(element: ET.Element | None) -> bool:
    if element is None:
        return False
    return (element.get(_W + 'val') or 'true').lower() not in _FALSE


def _read_archive(data: bytes) -> dict[str, bytes]:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise DocxImportError('The DOCX file is empty.')
    if len(data) > MAX_DOCX_BYTES:
        raise DocxImportError('The DOCX file is too large.')
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except zipfile.BadZipFile:
        raise DocxImportError('The file is not a DOCX (ZIP) document.') from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise DocxImportError('The DOCX file has too many parts.')
        names = {info.filename for info in infos}
        lowered = {name.lower() for name in names}
        if any(name.endswith('vbaproject.bin') or name.endswith('.bin') and 'word/' in name for name in lowered):
            raise DocxImportError('Documents with macros or embedded binaries are not supported.')
        if 'word/document.xml' not in names:
            raise DocxImportError('The DOCX file has no main document part.')
        if sum(info.file_size for info in infos) > MAX_TOTAL_UNCOMPRESSED:
            raise DocxImportError('The DOCX file expands beyond the allowed size.')
        wanted = ('[Content_Types].xml', 'word/document.xml', 'word/_rels/document.xml.rels')
        parts: dict[str, bytes] = {}
        for name in wanted:
            if name not in names:
                continue
            info = archive.getinfo(name)
            if info.file_size > MAX_XML_BYTES:
                raise DocxImportError(f'The DOCX part {name} is too large.')
            with archive.open(info) as handle:
                content = handle.read(MAX_XML_BYTES + 1)
            if len(content) > MAX_XML_BYTES:
                raise DocxImportError(f'The DOCX part {name} is too large.')
            head = content.upper()
            if b'<!DOCTYPE' in head or b'<!ENTITY' in head:
                raise DocxImportError('DOCX parts with DTDs or entity declarations are not supported.')
            parts[name] = content
        content_types = parts.get('[Content_Types].xml', b'').lower()
        if b'macroenabled' in content_types or b'vbaproject' in content_types:
            raise DocxImportError('Documents with macros are not supported.')
        parts['__has_media__'] = b'1' if any(n.startswith('word/media/') for n in lowered) else b''
        return parts


def _parse_xml(content: bytes, name: str) -> ET.Element:
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        raise DocxImportError(f'The DOCX part {name} is not well-formed XML.') from None


def _relationships(content: bytes | None) -> dict[str, str]:
    if not content:
        return {}
    root = _parse_xml(content, 'word/_rels/document.xml.rels')
    rels: dict[str, str] = {}
    for rel in root.iter(_REL + 'Relationship'):
        rel_id = rel.get('Id')
        target = rel.get('Target') or ''
        if rel_id and (rel.get('TargetMode') == 'External' or target.startswith(('mailto:', 'http'))):
            rels[rel_id] = target
    return rels


def _run(element: ET.Element) -> _Run:
    rpr = element.find(_W + 'rPr')
    bold = italic = False
    size = 0
    if rpr is not None:
        bold = _flag(rpr.find(_W + 'b'))
        italic = _flag(rpr.find(_W + 'i'))
        sz = rpr.find(_W + 'sz')
        if sz is not None and (sz.get(_W + 'val') or '').isdigit():
            size = int(sz.get(_W + 'val') or 0)
    pieces: list[str] = []
    for child in element:
        tag = child.tag
        if tag == _W + 't':
            pieces.append(child.text or '')
        elif tag == _W + 'tab' or tag == _W + 'ptab':
            pieces.append('\t')
        elif tag in (_W + 'br', _W + 'cr'):
            pieces.append(' ')
        elif tag in (_W + 'noBreakHyphen', _W + 'softHyphen'):
            pieces.append('-' if tag.endswith('noBreakHyphen') else '')
    return _Run(''.join(pieces), bold, italic, size)


def _paragraph(element: ET.Element, rels: dict[str, str]) -> _Para:
    ppr = element.find(_W + 'pPr')
    numbered = False
    style = ''
    if ppr is not None:
        num = ppr.find(_W + 'numPr')
        if num is not None:
            num_id = num.find(_W + 'numId')
            numbered = num_id is None or (num_id.get(_W + 'val') or '1') != '0'
        pstyle = ppr.find(_W + 'pStyle')
        if pstyle is not None:
            style = pstyle.get(_W + 'val') or ''
    drawing = any(node.tag.endswith('}drawing') or node.tag.endswith('}pict') for node in element.iter())
    runs: list[_Run] = []
    links: list[tuple[str, str]] = []

    def visit(node: ET.Element) -> None:
        for child in node:
            if child.tag == _W + 'r':
                runs.append(_run(child))
            elif child.tag == _W + 'hyperlink':
                start = len(runs)
                visit(child)
                href = rels.get(child.get(_R + 'id') or '', '')
                text = _collapse(''.join(r.text for r in runs[start:]))
                if href and text:
                    links.append((text, href))
            elif child.tag in (_W + 'ins', _W + 'smartTag', _W + 'sdt', _W + 'sdtContent', _W + 'fldSimple'):
                visit(child)
            elif child.tag.endswith('}AlternateContent') or child.tag.endswith('}Fallback'):
                continue

    visit(element)
    return _Para(runs=runs, numbered=numbered, drawing=drawing, style=style, links=links)


def _paragraphs(root: ET.Element, rels: dict[str, str]) -> list[_Para]:
    body = root.find(_W + 'body')
    if body is None:
        raise DocxImportError('The DOCX document has no body.')
    out: list[_Para] = []

    def walk(container: ET.Element) -> None:
        for child in container:
            if child.tag == _W + 'p':
                out.append(_paragraph(child, rels))
            elif child.tag in (_W + 'tbl', _W + 'tr', _W + 'tc', _W + 'sdt', _W + 'sdtContent'):
                walk(child)
            if len(out) > MAX_PARAGRAPHS:
                raise DocxImportError('The DOCX document has too many paragraphs.')

    walk(body)
    return out


def _page_size(root: ET.Element) -> PageSize:
    body = root.find(_W + 'body')
    sect = body.find(_W + 'sectPr') if body is not None else None
    pg = sect.find(_W + 'pgSz') if sect is not None else None
    if pg is None:
        return 'a4'
    try:
        width = int(pg.get(_W + 'w') or 0)
        height = int(pg.get(_W + 'h') or 0)
    except ValueError:
        return 'a4'
    short = min(width, height)
    # Letter is 12240 twips wide (8.5 in); A4 is 11906 (210 mm).
    return 'letter' if short and abs(short - 12240) < abs(short - 11906) else 'a4'


def _strip_bullet(text: str) -> str:
    return text.lstrip().lstrip(_BULLET_GLYPHS).strip() if text.lstrip()[:1] in _BULLET_GLYPHS else text.strip()


def _is_glyph_bullet(para: _Para) -> bool:
    first = para.raw.lstrip()[:1]
    return bool(first) and first in '•●▪■◦○‣∙*–-' and len(para.plain) > 2 and para.plain[1:2] == ' '


def _is_heading(para: _Para) -> bool:
    text = para.plain
    if not text or para.numbered or len(text) > 80:
        return False
    if re.match(r'(?i)^(heading|überschrift|berschrift|title)', para.style) and len(text) <= 80:
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 3 or not para.all_bold:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / len(letters) >= 0.85


def _parse_dates(text: str) -> tuple[str, str | None, bool, int, int] | None:
    """Return (start, end, current, match_start, match_end) of the last date range in ``text``."""

    def fmt(month: str | None, year: str) -> str:
        if month and month.isdigit() and 1 <= int(month) <= 12:
            return f'{year}-{int(month):02d}'
        return year

    matches = list(_RANGE_RE.finditer(text))
    if matches:
        m = matches[-1]
        start = fmt(m.group(1), m.group(2))
        if m.group(5):
            return start, None, True, m.start(), m.end()
        end = fmt(m.group(3), m.group(4))
        return start, end, False, m.start(), m.end()
    single = _SINGLE_RE.search(text)
    if single:
        year = fmt(single.group(1), single.group(2))
        offset = single.start() + (0 if single.group(0)[:1].isdigit() else 1)
        return year, year, False, offset, single.end()
    return None


def _clean_edge(text: str) -> str:
    return _collapse(text).strip(' |–—-,;:/(').strip()


def _split_company(para: _Para) -> tuple[str, str]:
    lead: list[str] = []
    rest: list[str] = []
    for run in para.runs:
        if not rest and (run.bold or not run.text.strip()):
            lead.append(run.text)
        else:
            rest.append(run.text)
    company_raw = ''.join(lead)
    remainder = ''.join(rest)
    parts = [p for p in re.split(r'\t+|\s{3,}', company_raw.strip()) if p.strip()]
    company = _clean_edge(parts[0]) if parts else ''
    location = _clean_edge(remainder) or (_clean_edge(parts[-1]) if len(parts) > 1 else '')
    if not company:
        whole = [p for p in re.split(r'\t+|\s{3,}', para.raw.strip()) if p.strip()]
        company = _clean_edge(whole[0]) if whole else ''
        location = _clean_edge(whole[-1]) if len(whole) > 1 else location
    return company, location


def _entry_from(head: _Para, title_line: _Para) -> tuple[str, str, LockedEntry] | None:
    if head.numbered or title_line.numbered or not head.starts_bold or len(head.plain) > 160:
        return None
    raw = title_line.raw
    parsed = _parse_dates(raw)
    if parsed is None:
        return None
    start, end, current, m_start, m_end = parsed
    title = _clean_edge(raw[:m_start] + ' ' + raw[m_end:])
    if not title or len(title) > 300:
        return None
    if title_line.italic_share < 0.5 and not title_line.starts_bold:
        return None
    company, location = _split_company(head)
    if not company:
        return None
    try:
        locked = LockedEntry(
            company=company[:300], title=title, location=location[:300],
            start=start, end=end, current=current,
        )
    except ValueError:
        return None
    return company, title, locked


def _entry_three(head: _Para, title_line: _Para, detail: _Para) -> tuple[str, str, LockedEntry] | None:
    """Institution / title / ``detail · place · dates`` layout. Returns (company, detail, locked)."""
    if head.numbered or detail.numbered or not head.starts_bold or len(head.plain) > 160:
        return None
    parsed = _parse_dates(detail.raw)
    if parsed is None:
        return None
    start, end, current, m_start, m_end = parsed
    title = _clean_edge(title_line.plain.strip(' ·'))
    company = _clean_edge(head.plain)
    remainder = _clean_edge(detail.raw[:m_start] + ' ' + detail.raw[m_end:]).strip(' ·')
    pieces = [_clean_edge(p) for p in re.split(r'\s[·|]\s', remainder) if _clean_edge(p)]
    location = pieces[-1] if len(pieces) > 1 else ''
    if not title or not company or len(title) > 300:
        return None
    try:
        locked = LockedEntry(company=company[:300], title=title, location=location[:300],
                             start=start, end=end, current=current)
    except ValueError:
        return None
    detail_text = ' · '.join(p for p in pieces if p != location)
    return company, detail_text, locked


def _lead_in(para: _Para) -> tuple[str, str]:
    """Split a leading bold ``Lead:`` prefix from the paragraph text."""
    runs = list(para.runs)
    prefix: list[str] = []
    index = 0
    while index < len(runs):
        run = runs[index]
        if run.bold or not run.text.strip() or re.fullmatch(r'[\s–—\-:|]+', run.text):
            prefix.append(run.text)
            index += 1
            continue
        break
    joined = _strip_bullet(_collapse(''.join(prefix)))
    rest = _collapse(''.join(r.text for r in runs[index:]))
    if joined.endswith(':') and rest and len(joined) <= 200:
        return joined, rest
    return '', _strip_bullet(para.plain)


def _split_inline(text: str) -> list[str] | None:
    body = _strip_bullet(text)
    if body.count('•') >= 1:
        items = [part.strip(' ·') for part in body.split('•')]
    elif body.count(' | ') >= 2 or body.count('|') >= 3:
        items = [part.strip() for part in body.split('|')]
    elif body.count(' · ') >= 2:
        items = [part.strip() for part in body.split(' · ')]
    else:
        return None
    items = [item for item in items if item]
    return items if len(items) >= 2 else None


def _chunk(text: str) -> list[str]:
    if len(text) <= MAX_UNIT_TEXT:
        return [text]
    chunks: list[str] = []
    current = ''
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        while len(sentence) > MAX_UNIT_TEXT:
            cut = sentence.rfind(' ', 0, MAX_UNIT_TEXT) or MAX_UNIT_TEXT
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > MAX_UNIT_TEXT:
            chunks.append(current)
            current = sentence
        else:
            current = f'{current} {sentence}'.strip()
    if current:
        chunks.append(current)
    return chunks


def _contact_like(para: _Para) -> bool:
    text = para.plain
    return bool(para.links) or '@' in text or bool(re.search(r'\+?\d[\d\s()/-]{6,}\d', text))


def _contacts(paras: list[_Para]) -> list[Contact]:
    contacts: list[Contact] = []
    seen: set[str] = set()
    for para in paras:
        link_by_text = {text: href for text, href in para.links}
        pieces = [p for p in re.split(r'\t+|\s{2,}|\s[·|•]\s|[·|•]', para.raw) if p.strip()]
        for piece in pieces:
            text = _collapse(piece).strip(' ,;')
            if not text or text in seen or len(text) > 200:
                continue
            href = link_by_text.get(text, '')
            if not href:
                href = next((h for t, h in para.links if t in text or text in t), '')
            seen.add(text)
            contact = _classify_contact(text, href if href.startswith(('mailto:', 'http')) else '')
            contacts.append(contact)
            if len(contacts) >= 12:
                return contacts
    return contacts


def _section_kind(title: str) -> SectionKind | None:
    lowered = title.casefold()
    for kind, words in _SECTION_KEYWORDS:
        if any(word in lowered for word in words):
            return kind
    if any(word in lowered for word in ('kompetenz', 'skill', 'tool', 'kenntnisse', 'fähigkeit', 'technolog',
                                        'competenc', 'tech stack', 'expertise')):
        return 'skills'
    return None


def import_docx(data: bytes, *, language: DocumentLanguage | None = None) -> CvDocument:
    """Parse a flowing single-column DOCX CV into a strict ``CvDocument``; never executes content."""
    parts = _read_archive(data)
    root = _parse_xml(parts['word/document.xml'], 'word/document.xml')
    rels = _relationships(parts.get('word/_rels/document.xml.rels'))
    page = _page_size(root)
    paras = [p for p in _paragraphs(root, rels) if p.plain or p.drawing]
    if not any(p.plain for p in paras):
        raise DocxImportError('The DOCX document contains no text.')

    # ---- header: name, headline, contacts --------------------------------------------------
    texts = [i for i, p in enumerate(paras) if p.plain]
    name_index = next(
        (i for i in texts[:3] if paras[i].all_bold or paras[i].max_size >= 24),
        texts[0],
    )
    name = paras[name_index].plain[:200]
    cursor = name_index + 1
    header_paras = [paras[name_index]]
    headline = ''
    contact_paras: list[_Para] = []
    while cursor < len(paras) and len(header_paras) < 4:
        para = paras[cursor]
        if not para.plain:
            header_paras.append(para)
            cursor += 1
            continue
        if _is_heading(para):
            break
        if _contact_like(para) and len(contact_paras) < 2:
            contact_paras.append(para)
        elif not headline and not contact_paras:
            headline = para.plain
        else:
            break
        header_paras.append(para)
        cursor += 1
    photo = bool(parts.get('__has_media__')) and any(p.drawing for p in paras[: cursor + 1])
    contacts = _contacts(contact_paras)

    # ---- body ----------------------------------------------------------------------------
    sections: list[_SectionDraft] = [_SectionDraft(title='', kind='profile', layout='paragraphs', paragraphs=True)]
    work_counter = 0
    study_counter = 0
    current_entry: _EntryDraft | None = None
    body = [p for p in paras[cursor:] if p.plain]
    index = 0
    while index < len(body):
        para = body[index]
        section = sections[-1]
        nxt = body[index + 1] if index + 1 < len(body) else None
        # Entries only after the first heading; the leading block is the profile.
        entry = _entry_from(para, nxt) if nxt is not None and len(sections) > 1 else None
        consumed = 2
        extra_intro = ''
        if entry is None and nxt is not None and len(sections) > 1 and index + 2 < len(body):
            # Three-line variant: institution line, bold title line, detail line ending in dates.
            third = body[index + 2]
            if (nxt.starts_bold and not nxt.numbered and not _is_heading(para)
                    and _parse_dates(nxt.raw) is None and _entry_from(nxt, third) is None):
                entry = _entry_three(para, nxt, third)
                if entry is not None:
                    consumed = 3
                    extra_intro = entry[1]
        if entry is not None:
            locked = entry[2]
            kind = section.kind or _section_kind(section.title)
            is_study = kind == 'education'
            if is_study:
                study_counter += 1
                entry_id = f'study-{study_counter}'
            else:
                work_counter += 1
                entry_id = f'work-{work_counter}'
            if section.kind not in ('experience', 'education'):
                section.kind = 'education' if is_study else 'experience'
            current_entry = _EntryDraft(entry_id=entry_id, kind='study' if is_study else 'work', locked=locked)
            if extra_intro:
                current_entry.intro.append(('', extra_intro))
            section.entries.append(current_entry)
            index += consumed
            continue
        if _is_heading(para):
            title = para.plain[:200]
            sections.append(_SectionDraft(title=title, kind=_section_kind(title)))
            current_entry = None
            index += 1
            continue
        bullet = para.numbered or _is_glyph_bullet(para)
        inline_items = None if bullet and current_entry is not None else _split_inline(para.plain)
        if inline_items and current_entry is None:
            section.inline = True
            section.units.extend(('', item) for item in inline_items)
        elif current_entry is not None:
            lead, text = _lead_in(para)
            target = current_entry.units if (bullet or current_entry.units) else current_entry.intro
            target.extend((lead if i == 0 else '', chunk) for i, chunk in enumerate(_chunk(text)))
        else:
            lead, text = _lead_in(para)
            if not bullet:
                section.paragraphs = True
            section.units.extend((lead if i == 0 else '', chunk) for i, chunk in enumerate(_chunk(text)))
        index += 1

    lang: DocumentLanguage = language or _detect_language(
        ' '.join(p.plain for p in paras)
    )
    return _build(name, headline, contacts, sections, page=page, photo=photo, language=lang)


def _build(
    name: str,
    headline: str,
    contacts: list[Contact],
    drafts: list[_SectionDraft],
    *,
    page: PageSize,
    photo: bool,
    language: DocumentLanguage,
) -> CvDocument:
    used_units: set[str] = {'u-headline'}
    used_sections: set[str] = set()

    def unit_id(base: str) -> str:
        candidate = base[:64]
        n = 2
        while candidate in used_units:
            suffix = f'-{n}'
            candidate = base[: 64 - len(suffix)] + suffix
            n += 1
        used_units.add(candidate)
        return candidate

    def section_id(kind: SectionKind, title: str) -> str:
        base = 's-' + (kind if kind in ('profile', 'experience', 'education') else _slugify(title, kind))
        base = base[:60].rstrip('-')
        candidate = base
        n = 2
        while candidate in used_sections:
            candidate = f'{base}-{n}'
            n += 1
        used_sections.add(candidate)
        return candidate

    sections: list[Section] = []
    for draft in drafts:
        if not draft.units and not draft.entries:
            continue
        kind: SectionKind = draft.kind or ('skills' if draft.inline else 'text')
        if kind in ('experience', 'education') and not draft.entries:
            kind = 'list'
        if draft.entries and kind not in ('experience', 'education'):
            kind = 'experience'
        if kind == 'profile':
            layout: SectionLayout = 'paragraphs'
        elif draft.inline:
            layout = 'inline'
        elif draft.paragraphs and not draft.entries and kind == 'text':
            layout = 'paragraphs'
        else:
            layout = 'bullets'
        sid = section_id(kind, draft.title)
        prefix = 'profile' if kind == 'profile' else sid[2:]
        budget = MAX_SECTION_UNITS
        units: list[Unit] = []
        for n, (lead, text) in enumerate(draft.units[:budget], 1):
            units.append(Unit(id=unit_id(f'u-{prefix}-{n}'), text=text, lead_in=lead, origin='source'))
        budget -= len(units)
        entries: list[Entry] = []
        for edraft in draft.entries[:20]:
            slug = edraft.entry_id.lower()
            intro = [
                Unit(id=unit_id(f'u-{slug}-i{n}'), text=text, lead_in=lead, origin='source')
                for n, (lead, text) in enumerate(edraft.intro[: min(20, max(budget, 0))], 1)
            ]
            budget -= len(intro)
            bullets = [
                Unit(id=unit_id(f'u-{slug}-b{n}'), text=text, lead_in=lead, origin='source')
                for n, (lead, text) in enumerate(edraft.units[: max(budget, 0)], 1)
            ]
            budget -= len(bullets)
            entries.append(Entry(id=edraft.entry_id, kind=edraft.kind, locked=edraft.locked,  # type: ignore[arg-type]
                                 intro=intro, units=bullets))
        sections.append(Section(id=sid, kind=kind, title=draft.title if kind != 'profile' else '',
                                layout=layout, units=units, entries=entries))
        if len(sections) >= 30:
            break
    if not any(s.entries for s in sections) and not any(s.units for s in sections):
        raise DocxImportError('No CV sections were recognized in the DOCX document.')
    return CvDocument(
        language=language,
        page=page,
        photo=photo,
        header=Header(name=name or 'Name', contacts=contacts),
        headline=Unit(id='u-headline', text=headline[:MAX_UNIT_TEXT], origin='source'),
        sections=sections,
    )
