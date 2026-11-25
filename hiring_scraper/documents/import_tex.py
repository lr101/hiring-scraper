"""Text-only importer for Radius marked LaTeX CVs -> CvDocument.

Never executes TeX. Parses marker blocks, fixed-coordinate page-1 layouts and
flowing sections into strict ``CvDocument`` structures with deterministic IDs.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from hiring_scraper.documents.editorial import normalize_internship, normalize_skills
from hiring_scraper.documents.schema import (
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

MAX_SOURCE_CHARS = 100_000

_ENTRY_MARKER_KEYS = frozenset({'id', 'kind', 'company', 'title', 'location', 'start', 'end', 'current'})
_MACRO_DEF_RE = re.compile(r'^\s*\\newcommand\{\\(Radius[A-Za-z]+)\}\{(.*)\}[ \t]*$')
_HELPER_MACRO_NAMES = frozenset({'CVSection', 'CVSubsection', 'CVRow', 'CVListBegin', 'CVListEnd'})

_BODY_ALLOWED_COMMANDS = frozenset({
    'begin', 'end',
    'AddToShipoutPictureFG', 'AtPageLowerLeft', 'setlength', 'unitlength',
    'put', 'includegraphics', 'hbox', 'fontsize', 'selectfont',
    'bfseries', 'itshape', 'makebox', 'color', 'href', 'CVBulletFont', 'rule',
    'mbox', 'newpage', 'clearpage', 'pagebreak', 'newcommand',
    'parindent', 'parskip', 'justifying', 'hyphenpenalty', 'emergencystretch',
    'par', 'nobreak', 'vspace', 'addvspace', 'noindent', 'linewidth',
    'CVSection', 'CVSubsection', 'CVRow', 'CVListBegin', 'CVListEnd',
    'textbf', 'textit', 'emph', 'textnormal', 'textsf', 'textrm',
    'small', 'footnotesize', 'normalsize', 'large', 'Large',
    'item', 'raisebox', 'hfill', 'quad', 'qquad', 'hspace', 'textbar',
    'section', 'subsection', 'newline', 'textbullet',
    'textbackslash', 'textasciitilde', 'textasciicircum',
})

_MACRO_BODY_ALLOWED_COMMANDS = frozenset({
    'textbf', 'textit', 'emph', 'textnormal',
    'textbackslash', 'textasciitilde', 'textasciicircum',
})

_ALLOWED_ENVIRONMENTS = frozenset({'document', 'picture', 'minipage', 'itemize'})

_DANGEROUS_COMMANDS = frozenset({
    'input', 'include', 'write', 'openin', 'openout', 'read', 'catcode',
    'def', 'edef', 'gdef', 'xdef', 'let', 'csname', 'expandafter',
    'directlua', 'luaexec', 'ShellEscape', 'immediate', 'loop',
    'iffalse', 'iftrue', 'ifx', 'ifnum', 'ifdefined',
})

_SUSPENDED_HYPHEN_WORDS = frozenset({'und', 'oder', 'bzw', 'sowie', 'and', 'or'})

_EXPERIENCE_TITLES = frozenset({
    'berufserfahrung', 'erfahrung', 'praxiserfahrung', 'beruflicher werdegang',
    'experience', 'work experience', 'professional experience', 'employment', 'career',
})
_EDUCATION_TITLES = frozenset({
    'ausbildung', 'studium', 'bildungsweg', 'akademischer werdegang', 'bildung',
    'education', 'academic background', 'studies',
})
_SKILLS_KEYWORDS = (
    'kompetenz', 'skill', 'tool', 'technische kenntnisse', 'kenntnisse',
    'fähigkeiten', 'faehigkeiten', 'technolog', 'competenc', 'tech stack',
)

_DE_STOPWORDS = frozenset({
    'und', 'mit', 'für', 'von', 'zur', 'zum', 'bei', 'durch', 'über', 'sowie',
    'eine', 'einer', 'einem', 'einen', 'der', 'die', 'das', 'dem', 'den',
    'ausbildung', 'berufserfahrung', 'sprachen', 'kenntnisse', 'fähigkeiten',
    'projektentwicklung', 'erfahrung', 'studium', 'abschluss', 'aufgaben',
})
_EN_STOPWORDS = frozenset({
    'and', 'with', 'for', 'from', 'the', 'that', 'this', 'across', 'through',
    'experience', 'education', 'skills', 'languages', 'projects', 'developed',
    'managed', 'coordinated', 'led', 'built', 'created', 'supported', 'team',
})


@dataclass
class _EntryMeta:
    id: str
    kind: Literal['work', 'study']
    locked: LockedEntry
    macros: list[tuple[str, str, int]] = field(default_factory=list)
    inline_bullets: list[str] = field(default_factory=list)


@dataclass
class _SectionDraft:
    title: str
    kind: SectionKind
    layout: SectionLayout = 'bullets'
    group: str = ''
    units: list[tuple[str, str]] = field(default_factory=list)  # (lead_in, text)
    entries: list[_EntryDraft] = field(default_factory=list)


@dataclass
class _EntryDraft:
    meta: _EntryMeta
    label: str = ''
    intro: list[tuple[str, str]] = field(default_factory=list)
    bullets: list[tuple[str, str]] = field(default_factory=list)


def _line_of(source: str, offset: int) -> int:
    return source.count('\n', 0, offset) + 1


def _strip_comments_preserve_lines(text: str) -> str:
    """Remove unescaped TeX comments while keeping line count identical."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        ending = ''
        if line.endswith('\r\n'):
            body, ending = line[:-2], '\r\n'
        elif line.endswith('\n'):
            body, ending = line[:-1], '\n'
        else:
            body = line
        i = 0
        cut = len(body)
        while i < len(body):
            ch = body[i]
            if ch == '\\':
                i += 2
                continue
            if ch == '%':
                cut = i
                break
            i += 1
        out.append(body[:cut] + ending)
    return ''.join(out)


def _find_matching_brace(text: str, open_pos: int, base_line: int) -> int:
    """Given ``text[open_pos] == '{'``, return index of matching ``'}'``."""
    if open_pos >= len(text) or text[open_pos] != '{':
        raise ImportError(f'Line {base_line + text.count(chr(10), 0, open_pos)}: Expected "{{" in LaTeX.')
    depth = 0
    i = open_pos
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ImportError(f'Line {base_line + text.count(chr(10), 0, open_pos)}: Unclosed "{{" in LaTeX.')


def _skip_optional_bracket(text: str, pos: int) -> tuple[str | None, int]:
    """If ``text[pos:]`` starts with ``[...]``, return ``(content, next_pos)``."""
    i = pos
    while i < len(text) and text[i] in ' \t':
        i += 1
    if i < len(text) and text[i] == '[':
        depth = 0
        start = i + 1
        while i < len(text):
            ch = text[i]
            if ch == '\\':
                i += 2
                continue
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return text[start:i], i + 1
            i += 1
    return None, pos


def _check_macro_body_commands(body: str, lineno: int) -> None:
    i = 0
    n = len(body)
    while i < n:
        if body[i] == '\\':
            if i + 1 < n and body[i + 1].isalpha():
                j = i + 1
                while j < n and body[j].isalpha():
                    j += 1
                cmd = body[i + 1:j]
                if cmd not in _MACRO_BODY_ALLOWED_COMMANDS:
                    raise ImportError(f"Line {lineno}: Unsupported LaTeX command '\\{cmd}' inside macro body.")
                i = j
                continue
            i += 2
            continue
        i += 1


def _validate_commands(text: str, allowed: frozenset[str], defined_macros: set[str], base_line: int) -> None:
    """Reject unknown commands or environments anywhere in parsed LaTeX text."""
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '\\':
            if i + 1 >= n:
                break
            nxt = text[i + 1]
            if nxt.isalpha():
                j = i + 1
                while j < n and text[j].isalpha():
                    j += 1
                cmd = text[i + 1:j]
                lineno = base_line + text.count('\n', 0, i)
                if cmd in _DANGEROUS_COMMANDS:
                    raise ImportError(f"Line {lineno}: Forbidden LaTeX command '\\{cmd}'.")
                if cmd not in allowed and cmd not in defined_macros:
                    if cmd.startswith('Radius'):
                        raise ImportError(f"Line {lineno}: Undefined Radius macro '\\{cmd}'.")
                    raise ImportError(f"Line {lineno}: Unsupported LaTeX command '\\{cmd}'.")
                if cmd in ('begin', 'end'):
                    k = j
                    while k < n and text[k] in ' \t':
                        k += 1
                    if k < n and text[k] == '{':
                        close = _find_matching_brace(text, k, base_line)
                        env = text[k + 1:close].strip()
                        if env not in _ALLOWED_ENVIRONMENTS:
                            raise ImportError(f"Line {lineno}: Unsupported LaTeX environment '{env}'.")
                i = j
                continue
            i += 2
            continue
        i += 1


def _unescape_tex(raw: str) -> str:
    """Convert supported LaTeX formatting and escapes to clean plain Unicode."""
    text = raw
    placeholders: list[str] = []

    def _stash(value: str) -> str:
        placeholders.append(value)
        return f'\x00{len(placeholders) - 1}\x00'

    text = re.sub(
        r'\\([&%$#_{}])|\\(textbackslash|textasciitilde|textasciicircum)\{\}',
        lambda m: _stash(
            m.group(1)
            if m.group(1) is not None
            else {'textbackslash': '\\', 'textasciitilde': '~', 'textasciicircum': '^'}[m.group(2)]
        ),
        text,
    )
    # Strip known formatting/layout wrappers while preserving inner text.
    text = re.sub(r'\\href\{[^{}]*\}\{', '{', text)
    text = re.sub(r'\\fontsize\{[^{}]*\}\{[^{}]*\}\\selectfont\b', ' ', text)
    text = re.sub(r'\\color(?:\[[^\]]*\])?\{[^{}]*\}', ' ', text)
    text = re.sub(r'\\makebox(?:\[[^\]]*\])*\{', '{', text)
    text = re.sub(r'\\raisebox\{[^{}]*\}\{', '{', text)
    text = re.sub(r'\\(?:textbf|textit|emph|textnormal|textsf|textrm|mbox)\{', '{', text)
    text = re.sub(
        r'\\(?:bfseries|itshape|selectfont|CVBulletFont|noindent|nobreak|par|'
        r'small|footnotesize|normalsize|large|Large|hfill|quad|qquad|'
        r'justifying|newpage|clearpage|pagebreak)\b',
        ' ',
        text,
    )
    text = re.sub(r'\\(?:vspace|addvspace|hspace)\*?\{[^{}]*\}', ' ', text)
    text = re.sub(r'\\textbar\b\\?', '|', text)
    text = re.sub(r'\\textbullet\b', '●', text)
    text = text.replace('---', '—').replace('--', '–')
    text = text.replace('``', '“').replace("''", '”')
    text = text.replace('~', ' ')
    text = text.replace('{', '').replace('}', '')
    text = re.sub(r'\x00(\d+)\x00', lambda m: placeholders[int(m.group(1))], text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _split_lead_in(raw_tex: str) -> tuple[str, str]:
    """Extract ``\\textbf{Lead:} rest`` (or ``\\textbf{Lead}: rest``) into ``(lead_in, text)``."""
    stripped = raw_tex.lstrip()
    if stripped.startswith(r'\textbf{'):
        close = _find_matching_brace(stripped, len(r'\textbf'), 1)
        inner = stripped[len(r'\textbf{'):close]
        after = stripped[close + 1:]
        lead_text = _unescape_tex(inner)
        if lead_text.endswith(':'):
            rest = _unescape_tex(after)
            if rest:
                return lead_text, rest
        elif after.startswith(':'):
            rest = _unescape_tex(after[1:])
            if rest and lead_text:
                return f'{lead_text}:', rest
    return '', _unescape_tex(raw_tex)


def _join_raw_fragments(fragments: list[str]) -> str:
    """Join wrapped line fragments with a single space, fixing soft hyphen line breaks."""
    cleaned = [frag.strip() for frag in fragments if frag.strip()]
    if not cleaned:
        return ''
    acc = cleaned[0]
    for nxt in cleaned[1:]:
        acc_plain = _unescape_tex(acc)
        nxt_plain = _unescape_tex(nxt)
        first_word = re.match(r'^([A-Za-zÄÖÜäöüß]+)', nxt_plain)
        first_word_str = first_word.group(1).casefold() if first_word else ''
        if (
            acc_plain.endswith('-')
            and not acc_plain.endswith('--')
            and not acc_plain.endswith(' -')
            and nxt_plain
            and nxt_plain[0].islower()
            and first_word_str not in _SUSPENDED_HYPHEN_WORDS
            and acc.endswith('-')
        ):
            acc = acc[:-1] + nxt
        else:
            acc = acc + ' ' + nxt
    return acc


def _slugify(text: str, fallback: str = 'section') -> str:
    replacements = {'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', 'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue'}
    out = ''.join(replacements.get(ch, ch) for ch in text)
    out = unicodedata.normalize('NFKD', out).encode('ascii', 'ignore').decode('ascii')
    out = re.sub(r'[^a-zA-Z0-9]+', '-', out).strip('-').lower()
    return out or fallback


def _classify_contact(text: str, href: str) -> Contact:
    if href.lower().startswith('mailto:') or ('@' in text and ' ' not in text):
        return Contact(kind='email', text=text, href=href or f'mailto:{text}')
    if href.lower().startswith(('http://', 'https://')):
        return Contact(kind='link', text=text, href=href)
    digits = re.sub(r'\D', '', text)
    if len(digits) >= 6 and re.fullmatch(r'[\d\s()+\-/.]+', text):
        return Contact(kind='phone', text=text, href=href)
    return Contact(kind='location', text=text, href=href)


def _classify_section_kind(title: str, default: SectionKind = 'text') -> SectionKind:
    lowered = title.strip().casefold()
    if lowered in _EXPERIENCE_TITLES:
        return 'experience'
    if lowered in _EDUCATION_TITLES:
        return 'education'
    if any(keyword in lowered for keyword in _SKILLS_KEYWORDS):
        return 'skills'
    return default


def _detect_language(text: str) -> DocumentLanguage:
    words = re.findall(r'[A-Za-zÄÖÜäöüß]{2,}', text.casefold())
    de_score = sum(1 for w in words if w in _DE_STOPWORDS)
    en_score = sum(1 for w in words if w in _EN_STOPWORDS)
    de_score += sum(1 for ch in text if ch in 'äöüÄÖÜß') // 4
    return 'de' if de_score > en_score else 'en'


def _parse_markers(source: str) -> tuple[
    list[tuple[str, str, int]],
    list[str],
    list[_EntryMeta],
    dict[str, tuple[str, str, int]],
]:
    """Parse ``% radius:*`` blocks from raw source.

    Returns ``(profile_macros, profile_inline, entries, macro_index)`` where
    ``macro_index`` maps macro name -> ``(owner, raw_body, lineno)`` with owner
    being ``'profile'`` or ``entry.id``.
    """
    profile_macros: list[tuple[str, str, int]] = []
    profile_inline: list[str] = []
    entries: list[_EntryMeta] = []
    macro_index: dict[str, tuple[str, str, int]] = {}
    seen_entry_ids: set[str] = set()

    in_profile = False
    profile_seen = False
    current_entry: _EntryMeta | None = None
    in_responsibilities = False
    entry_start_line = 0
    profile_start_line = 0

    for lineno, line in enumerate(source.splitlines(), 1):
        marker = re.fullmatch(r'\s*% radius:(.*)', line)
        if marker:
            token = marker.group(1)
            if token == 'profile':
                if in_profile or profile_seen or current_entry is not None:
                    raise ImportError(f'Line {lineno}: Unexpected % radius:profile marker.')
                in_profile = True
                profile_seen = True
                profile_start_line = lineno
            elif token == 'endprofile':
                if not in_profile:
                    raise ImportError(f'Line {lineno}: Unexpected % radius:endprofile without matching profile.')
                in_profile = False
            elif token.startswith('entry '):
                if in_profile or current_entry is not None:
                    raise ImportError(f'Line {lineno}: Nested or unclosed % radius:entry marker.')
                raw_json = token[6:].strip()
                try:
                    payload = json.loads(raw_json)
                except json.JSONDecodeError as exc:
                    raise ImportError(f'Line {lineno}: Invalid JSON in % radius:entry marker: {exc}') from exc
                if not isinstance(payload, dict) or set(payload) != _ENTRY_MARKER_KEYS:
                    raise ImportError(
                        f'Line {lineno}: % radius:entry JSON must contain exact keys '
                        f'{sorted(_ENTRY_MARKER_KEYS)}.'
                    )
                kind = payload['kind']
                if kind not in ('work', 'study'):
                    raise ImportError(f"Line {lineno}: Invalid entry kind {kind!r}; expected 'work' or 'study'.")
                entry_id = payload['id']
                if not isinstance(entry_id, str) or not entry_id or entry_id in seen_entry_ids:
                    raise ImportError(f'Line {lineno}: Invalid or duplicate entry id {entry_id!r}.')
                try:
                    locked = LockedEntry.model_validate({
                        'company': payload['company'],
                        'title': payload['title'],
                        'location': payload['location'],
                        'start': payload['start'],
                        'end': payload['end'],
                        'current': payload['current'],
                    })
                except ValidationError as exc:
                    raise ImportError(f'Line {lineno}: Invalid % radius:entry fields: {exc}') from exc
                seen_entry_ids.add(entry_id)
                current_entry = _EntryMeta(id=entry_id, kind=kind, locked=locked)
                in_responsibilities = False
                entry_start_line = lineno
            elif token == 'responsibilities':
                if current_entry is None or in_responsibilities:
                    raise ImportError(f'Line {lineno}: Unexpected % radius:responsibilities marker.')
                in_responsibilities = True
            elif token == 'end':
                if current_entry is None or not in_responsibilities:
                    raise ImportError(f'Line {lineno}: Unexpected % radius:end marker.')
                entries.append(current_entry)
                current_entry = None
                in_responsibilities = False
            else:
                raise ImportError(f'Line {lineno}: Unknown % radius directive {token!r}.')
            continue

        if in_profile:
            stripped = line.strip()
            if not stripped or stripped.startswith('%'):
                continue
            match = _MACRO_DEF_RE.fullmatch(line)
            if match:
                name, body = match.group(1), match.group(2)
                if name in macro_index:
                    raise ImportError(f"Line {lineno}: Duplicate macro '\\{name}'.")
                _check_macro_body_commands(body, lineno)
                profile_macros.append((name, body, lineno))
                macro_index[name] = ('profile', body, lineno)
            else:
                profile_inline.append(stripped)
        elif in_responsibilities and current_entry is not None:
            stripped = line.strip()
            if not stripped or stripped.startswith('%'):
                continue
            match = _MACRO_DEF_RE.fullmatch(line)
            if match:
                name, body = match.group(1), match.group(2)
                if name in macro_index:
                    raise ImportError(f"Line {lineno}: Duplicate macro '\\{name}'.")
                _check_macro_body_commands(body, lineno)
                current_entry.macros.append((name, body, lineno))
                macro_index[name] = (current_entry.id, body, lineno)
            else:
                if stripped not in (r'\begin{itemize}', r'\end{itemize}'):
                    item_text = re.sub(r'^\\item\b\s*', '', stripped)
                    if item_text:
                        current_entry.inline_bullets.append(item_text)

    if in_profile:
        raise ImportError(f'Line {profile_start_line}: Unclosed % radius:profile block.')
    if current_entry is not None:
        raise ImportError(f'Line {entry_start_line}: Unclosed % radius:entry block ({current_entry.id}).')
    if not entries:
        raise ImportError('Line 1: No % radius:entry markers found in LaTeX source.')

    return profile_macros, profile_inline, entries, macro_index


def _remove_helper_newcommand_defs(body: str, base_line: int) -> str:
    """Strip known layout helper ``\\newcommand{\\CV...}[n]{...}`` definitions, preserving line numbers."""
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        idx = body.find(r'\newcommand', i)
        if idx < 0:
            out.append(body[i:])
            break
        out.append(body[i:idx])
        pos = idx + len(r'\newcommand')
        while pos < n and body[pos] in ' \t':
            pos += 1
        if pos >= n or body[pos] != '{':
            lineno = base_line + body.count('\n', 0, idx)
            raise ImportError(f'Line {lineno}: Malformed \\newcommand in document body.')
        name_end = _find_matching_brace(body, pos, base_line)
        macro_token = body[pos + 1:name_end].strip()
        macro_name = macro_token[1:] if macro_token.startswith('\\') else macro_token
        lineno = base_line + body.count('\n', 0, idx)
        if macro_name not in _HELPER_MACRO_NAMES and not macro_name.startswith('Radius'):
            raise ImportError(f"Line {lineno}: Unsupported \\newcommand definition '\\{macro_name}' in body.")
        pos = name_end + 1
        _, pos = _skip_optional_bracket(body, pos)
        _, pos = _skip_optional_bracket(body, pos)
        while pos < n and body[pos] in ' \t\r\n':
            pos += 1
        if pos >= n or body[pos] != '{':
            raise ImportError(f'Line {lineno}: Malformed \\newcommand body for \\{macro_name}.')
        def_end = _find_matching_brace(body, pos, base_line)
        newline_count = body.count('\n', idx, def_end + 1)
        out.append('\n' * newline_count)
        i = def_end + 1
    return ''.join(out)


@dataclass
class _HboxPut:
    x: float
    y: float
    lineno: int
    raw: str
    is_bold: bool
    is_italic: bool
    is_bullet_glyph: bool
    macro_names: list[str]
    href: str
    text: str


def _parse_hbox_content(x: float, y: float, inner: str, lineno: int, defined_macros: set[str]) -> _HboxPut:
    is_bold = bool(re.search(r'\\(?:bfseries\b|textbf\{)', inner))
    is_italic = bool(re.search(r'\\(?:itshape\b|textit\{)', inner))
    macro_names = [m.group(1) for m in re.finditer(r'\\(Radius[A-Za-z]+)(?:\{\})?\b', inner)]
    for name in macro_names:
        if name not in defined_macros:
            raise ImportError(f"Line {lineno}: Undefined Radius macro '\\{name}'.")
    href = ''
    href_match = re.search(r'\\href\{([^{}]*)\}', inner)
    if href_match:
        href = href_match.group(1).strip()
    plain = _unescape_tex(re.sub(r'\\Radius[A-Za-z]+(?:\{\})?', '', inner))
    is_bullet_glyph = plain in ('●', '•') and not macro_names
    return _HboxPut(
        x=x,
        y=y,
        lineno=lineno,
        raw=inner,
        is_bold=is_bold,
        is_italic=is_italic,
        is_bullet_glyph=is_bullet_glyph,
        macro_names=macro_names,
        href=href,
        text=plain,
    )


def _extract_picture_puts(picture_body: str, base_line: int) -> list[tuple[float, float, str, int]]:
    """Extract all top-level ``\\put(x,y){...}`` calls inside a ``picture`` environment."""
    puts: list[tuple[float, float, str, int]] = []
    i = 0
    n = len(picture_body)
    put_re = re.compile(r'\\put\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\)\s*\{')
    while i < n:
        match = put_re.search(picture_body, i)
        if not match:
            break
        lineno = base_line + picture_body.count('\n', 0, match.start())
        open_brace = match.end() - 1
        close_brace = _find_matching_brace(picture_body, open_brace, base_line)
        x = float(match.group(1))
        y = float(match.group(2))
        puts.append((x, y, picture_body[open_brace + 1:close_brace], lineno))
        i = close_brace + 1
    return puts


def _is_uppercase_heading(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) >= 2 and all(ch.isupper() for ch in letters)


class _DocumentBuilder:
    def __init__(
        self,
        entries: list[_EntryMeta],
        macro_index: dict[str, tuple[str, str, int]],
    ) -> None:
        self.entries = entries
        self.entries_by_id = {e.id: e for e in entries}
        self.macro_index = macro_index
        self.defined_macros = set(macro_index)

        self.photo = False
        self.header_name: str = ''
        self.headline_text: str = ''
        self.contacts: list[Contact] = []
        self.sections: list[_SectionDraft] = []
        self.entry_drafts: dict[str, _EntryDraft] = {}
        self.current_section: _SectionDraft | None = None
        self.current_entry: _EntryDraft | None = None
        self.pending_label: str = ''
        self.header_done = False

    def _match_entry_by_company_or_title(self, text: str) -> _EntryMeta | None:
        needle = re.sub(r'\s+', ' ', text).strip().casefold()
        if not needle:
            return None
        for entry in self.entries:
            if entry.id in self.entry_drafts:
                continue
            if entry.locked.company.strip().casefold() == needle:
                return entry
        for entry in self.entries:
            if entry.id in self.entry_drafts:
                continue
            if entry.locked.title.strip().casefold() == needle:
                return entry
        return None

    def _is_known_company(self, text: str) -> bool:
        needle = re.sub(r'\s+', ' ', text).strip().casefold()
        return any(e.locked.company.strip().casefold() == needle for e in self.entries)

    def _start_section(self, title: str, kind: SectionKind | None = None, layout: SectionLayout = 'bullets') -> _SectionDraft:
        self.current_entry = None
        self.pending_label = ''
        resolved_kind = kind or _classify_section_kind(title, default='text')
        sec = _SectionDraft(title=title, kind=resolved_kind, layout=layout)
        self.sections.append(sec)
        self.current_section = sec
        return sec

    def _start_subsection(self, title: str) -> None:
        parent = self.current_section
        group = (parent.group or parent.title) if parent is not None else ''
        if parent is not None and not parent.group:
            if parent.units or parent.entries:
                parent.group, parent.title = group, ''
            else:
                self.sections.pop()
        self._start_section(title).group = group

    def _ensure_entry(self, entry_id: str) -> _EntryDraft:
        if entry_id in self.entry_drafts:
            draft = self.entry_drafts[entry_id]
            self.current_entry = draft
            return draft
        meta = self.entries_by_id[entry_id]
        expected_kind: SectionKind = 'experience' if meta.kind == 'work' else 'education'
        if self.current_section is None or self.current_section.kind != expected_kind:
            if (
                self.current_section is not None
                and not self.current_section.units
                and not self.current_section.entries
            ):
                self.current_section.kind = expected_kind
                self.current_section.layout = 'bullets'
            else:
                default_title = 'Experience' if expected_kind == 'experience' else 'Education'
                self._start_section(default_title, kind=expected_kind, layout='bullets')
        draft = _EntryDraft(meta=meta, label=self.pending_label)
        self.pending_label = ''
        assert self.current_section is not None
        self.current_section.entries.append(draft)
        self.entry_drafts[entry_id] = draft
        self.current_entry = draft
        return draft

    def parse_picture_page(self, picture_body: str, base_line: int) -> None:
        puts = _extract_picture_puts(picture_body, base_line)
        hbox_puts: list[_HboxPut] = []

        for x, y, content, lineno in puts:
            stripped = content.strip()
            if not stripped:
                continue
            if r'\includegraphics' in stripped:
                self.photo = True
                continue
            if re.fullmatch(r'(?:\\color(?:\[[^\]]*\])?\{[^{}]*\}\s*)?\\rule\{[^{}]*\}\{[^{}]*\}', stripped):
                continue
            if r'\begin{minipage}' in stripped:
                inner_start = stripped.find(r'\begin{minipage}')
                self.parse_flowing(stripped[inner_start:], lineno)
                continue
            if stripped.startswith(r'\hbox'):
                brace_pos = stripped.find('{', len(r'\hbox'))
                if brace_pos < 0:
                    raise ImportError(f'Line {lineno}: Malformed \\hbox inside \\put.')
                close = _find_matching_brace(stripped, brace_pos, lineno)
                inner = stripped[brace_pos + 1:close]
                hbox_puts.append(_parse_hbox_content(x, y, inner, lineno, self.defined_macros))
                continue
            raise ImportError(f'Line {lineno}: Unsupported \\put content in picture environment.')

        if not hbox_puts:
            return

        profile_frags: list[tuple[float, str]] = []
        active_bullet_entry: str | None = None
        active_bullet_frags: list[str] = []

        def flush_bullet() -> None:
            nonlocal active_bullet_entry, active_bullet_frags
            if active_bullet_entry and active_bullet_frags:
                draft = self._ensure_entry(active_bullet_entry)
                joined = _join_raw_fragments(active_bullet_frags)
                draft.bullets.append(_split_lead_in(joined))
            active_bullet_frags = []

        def flush_profile() -> None:
            if not profile_frags:
                return
            paragraphs: list[list[str]] = [[profile_frags[0][1]]]
            prev_y = profile_frags[0][0]
            for py, frag in profile_frags[1:]:
                if prev_y - py > 18.0:
                    paragraphs.append([frag])
                else:
                    paragraphs[-1].append(frag)
                prev_y = py
            profile_sec = _SectionDraft(title='', kind='profile', layout='paragraphs')
            for para_frags in paragraphs:
                joined = _join_raw_fragments(para_frags)
                profile_sec.units.append(_split_lead_in(joined))
            self.sections.append(profile_sec)
            self.current_section = profile_sec
            profile_frags.clear()

        in_header = not self.header_done
        for item in hbox_puts:
            if item.macro_names:
                in_header = False
                self.header_done = True
                for mname in item.macro_names:
                    owner, raw_body, _ = self.macro_index[mname]
                    if owner == 'profile':
                        flush_bullet()
                        profile_frags.append((item.y, raw_body))
                    else:
                        flush_profile()
                        if active_bullet_entry != owner:
                            flush_bullet()
                            active_bullet_entry = owner
                            self._ensure_entry(owner)
                        active_bullet_frags.append(raw_body)
                continue

            if item.is_bullet_glyph:
                in_header = False
                self.header_done = True
                flush_profile()
                flush_bullet()
                if self.current_entry is not None:
                    active_bullet_entry = self.current_entry.meta.id
                continue

            if not item.text:
                continue

            if in_header:
                if not self.header_name and item.is_bold:
                    self.header_name = item.text
                    continue
                if self.header_name and not self.headline_text and not item.href and not item.is_bold:
                    if not re.fullmatch(r'[\d\s()+\-/.]+', item.text) and '@' not in item.text:
                        self.headline_text = item.text
                        continue
                if item.is_bold and _is_uppercase_heading(item.text) and not self._is_known_company(item.text):
                    in_header = False
                    self.header_done = True
                    # Fall through to section heading handling below.
                else:
                    self.contacts.append(_classify_contact(item.text, item.href))
                    continue

            flush_profile()

            if item.is_bold and not item.is_italic:
                matched_entry = self._match_entry_by_company_or_title(item.text)
                if matched_entry is not None:
                    flush_bullet()
                    active_bullet_entry = matched_entry.id
                    self._ensure_entry(matched_entry.id)
                    continue
                if _is_uppercase_heading(item.text) and not self._is_known_company(item.text):
                    flush_bullet()
                    active_bullet_entry = None
                    self._start_section(item.text, kind=_classify_section_kind(item.text, default='experience'))
                    continue

        flush_profile()
        flush_bullet()
        self.header_done = True

    def parse_flowing(self, content: str, base_line: int) -> None:
        """Parse flowing LaTeX sections, rows, paragraphs, lists and minipages."""
        # Unwrap a single outer page-level minipage if present.
        stripped_outer = content.strip()
        if stripped_outer.startswith(r'\begin{minipage}'):
            inner, end_pos = self._extract_environment(content, content.find(r'\begin{minipage}'), 'minipage', base_line)
            trailing = content[end_pos:].strip()
            if not trailing:
                inner_line = base_line + content.count('\n', 0, content.find(r'\begin{minipage}'))
                # Strip optional [t]{width} args right after \begin{minipage}
                pos = 0
                _, pos = _skip_optional_bracket(inner, pos)
                while pos < len(inner) and inner[pos] in ' \t':
                    pos += 1
                if pos < len(inner) and inner[pos] == '{':
                    close = _find_matching_brace(inner, pos, inner_line)
                    pos = close + 1
                self.parse_flowing(inner[pos:], inner_line + inner.count('\n', 0, pos))
                return

        i = 0
        n = len(content)
        para_buf: list[str] = []
        skip_next_entry_title_line = False

        def flush_para() -> None:
            nonlocal skip_next_entry_title_line
            if not para_buf:
                return
            raw = ' '.join(part.strip() for part in para_buf if part.strip()).strip()
            para_buf.clear()
            if not raw:
                return

            # Check for pure profile macro reference(s)
            macro_refs = [m.group(1) for m in re.finditer(r'\\(Radius[A-Za-z]+)(?:\{\})?\b', raw)]
            if macro_refs:
                remainder = _unescape_tex(re.sub(r'\\Radius[A-Za-z]+(?:\{\})?', '', raw))
                if not remainder:
                    for mname in macro_refs:
                        owner, mbody, _ = self.macro_index[mname]
                        if owner == 'profile':
                            if self.current_section is None or self.current_section.kind != 'profile':
                                self._start_section('', kind='profile', layout='paragraphs')
                            assert self.current_section is not None
                            self.current_section.units.append(_split_lead_in(mbody))
                        else:
                            draft = self._ensure_entry(owner)
                            draft.bullets.append(_split_lead_in(mbody))
                    return

            # Standalone bold heading line: {\bfseries TITLE}
            bold_group_match = re.fullmatch(r'\{\\bfseries\s+(.+)\}', raw, re.DOTALL)
            if bold_group_match and r'\itshape' not in bold_group_match.group(1):
                heading_text = _unescape_tex(bold_group_match.group(1))
                if heading_text:
                    if not self.header_done and not self.header_name:
                        self.header_name = heading_text
                        return
                    # Inside experience/education, a short uppercase bold line before a company is an entry label.
                    if (
                        self.current_section is not None
                        and self.current_section.kind in ('experience', 'education')
                        and not self._is_known_company(heading_text)
                        and _classify_section_kind(heading_text, default='text') == 'text'
                        and len(self.entry_drafts) < len(self.entries)
                    ):
                        self.pending_label = heading_text
                        return
                    parent = self.current_section
                    if parent is not None and (
                        parent.group or (
                            parent.title and not parent.units and not parent.entries
                            and parent.kind not in ('profile', 'experience', 'education')
                        )
                    ):
                        self._start_subsection(heading_text)
                    else:
                        self._start_section(heading_text)
                    return

            # Header handling for pure flowing CVs (no Page 1 picture)
            if not self.header_done and self.header_name:
                if r'\textbar' in raw or r'\href' in raw:
                    parts = re.split(r'\\textbar\\?\s*', raw)
                    for part in parts:
                        part = part.strip()
                        if not part:
                            continue
                        href_match = re.search(r'\\href\{([^{}]*)\}', part)
                        href = href_match.group(1).strip() if href_match else ''
                        text = _unescape_tex(part)
                        if text:
                            self.contacts.append(_classify_contact(text, href))
                    self.header_done = True
                    return
                plain_hdr = _unescape_tex(raw)
                if plain_hdr and not self.headline_text:
                    self.headline_text = plain_hdr
                    return

            # Flowing entry header: \textbf{Company} \hfill Location
            if raw.startswith(r'\textbf{'):
                close = _find_matching_brace(raw, len(r'\textbf'), base_line)
                bold_inner = raw[len(r'\textbf{'):close]
                if not bold_inner.startswith(r'\textit{'):
                    candidate = _unescape_tex(bold_inner)
                    matched = self._match_entry_by_company_or_title(candidate)
                    if matched is not None:
                        self.header_done = True
                        self._ensure_entry(matched.id)
                        skip_next_entry_title_line = True
                        return
                elif skip_next_entry_title_line:
                    skip_next_entry_title_line = False
                    return

            skip_next_entry_title_line = False
            self.header_done = True

            # Free prose around an open entry before its bullets -> Entry.intro
            if (
                self.current_entry is not None
                and self.current_section is not None
                and self.current_section.kind in ('experience', 'education')
                and not self.current_entry.bullets
            ):
                lead, text = _split_lead_in(raw)
                if lead or text:
                    self.current_entry.intro.append((lead, text))
                return

            if self.current_section is None:
                self._start_section('', kind='text', layout='paragraphs')
            assert self.current_section is not None

            # Inline bullet-dot separated skills / competencies list
            plain_all = _unescape_tex(raw)
            if not plain_all:
                return
            if '•' in plain_all and (self.current_section.kind == 'skills' or plain_all.count('•') >= 2):
                items = [part.strip() for part in plain_all.split('•') if part.strip()]
                if items:
                    self.current_section.layout = 'inline'
                    if self.current_section.kind == 'text':
                        self.current_section.kind = 'skills'
                    for item in items:
                        self.current_section.units.append(('', item))
                    return

            if self.current_section.kind == 'text':
                self.current_section.layout = 'paragraphs'
            lead, text = _split_lead_in(raw)
            if lead or text:
                self.current_section.units.append((lead, text))

        while i < n:
            # Blank line flushes paragraph
            if content[i] == '\n':
                j = i + 1
                while j < n and content[j] in ' \t':
                    j += 1
                if j < n and content[j] == '\n':
                    flush_para()
                i += 1
                continue

            if content[i] in ' \t\r':
                i += 1
                continue

            # \begin{minipage} ... \end{minipage}
            if content.startswith(r'\begin{minipage}', i):
                flush_para()
                inner, end_pos = self._extract_environment(content, i, 'minipage', base_line)
                inner_line = base_line + content.count('\n', 0, i)
                pos = 0
                _, pos = _skip_optional_bracket(inner, pos)
                while pos < len(inner) and inner[pos] in ' \t':
                    pos += 1
                if pos < len(inner) and inner[pos] == '{':
                    close = _find_matching_brace(inner, pos, inner_line)
                    pos = close + 1
                self.parse_flowing(inner[pos:], inner_line + inner.count('\n', 0, pos))
                i = end_pos
                continue

            # \begin{itemize} ... \end{itemize} or \CVListBegin ... \CVListEnd
            if content.startswith(r'\begin{itemize}', i):
                flush_para()
                inner, end_pos = self._extract_environment(content, i, 'itemize', base_line)
                inner_line = base_line + content.count('\n', 0, i)
                pos = 0
                _, pos = _skip_optional_bracket(inner, pos)
                self._parse_list_items(inner[pos:], inner_line + inner.count('\n', 0, pos))
                i = end_pos
                continue

            if content.startswith(r'\CVListBegin', i) and (i + 12 >= n or not content[i + 12].isalpha()):
                flush_para()
                end_idx = content.find(r'\CVListEnd', i + 12)
                if end_idx < 0:
                    raise ImportError(f'Line {base_line + content.count(chr(10), 0, i)}: Unclosed \\CVListBegin.')
                inner_line = base_line + content.count('\n', 0, i)
                self._parse_list_items(content[i + 12:end_idx], inner_line)
                i = end_idx + len(r'\CVListEnd')
                continue

            # Section / subsection commands
            sec_match = re.match(r'\\(CVSection|CVSubsection|section\*?|subsection\*?)\s*\{', content[i:])
            if sec_match:
                flush_para()
                self.header_done = True
                open_brace = i + sec_match.end() - 1
                close = _find_matching_brace(content, open_brace, base_line)
                title = _unescape_tex(content[open_brace + 1:close])
                if 'subsection' in sec_match[1].lower():
                    self._start_subsection(title)
                else:
                    self._start_section(title)
                i = close + 1
                continue

            # \CVRow{left}{right}
            if content.startswith(r'\CVRow', i) and (i + 6 >= n or not content[i + 6].isalpha()):
                flush_para()
                self.header_done = True
                pos = i + 6
                while pos < n and content[pos] in ' \t':
                    pos += 1
                left_close = _find_matching_brace(content, pos, base_line)
                left_raw = content[pos + 1:left_close]
                pos = left_close + 1
                while pos < n and content[pos] in ' \t':
                    pos += 1
                right_close = _find_matching_brace(content, pos, base_line)
                left_text = _unescape_tex(left_raw)
                matched = self._match_entry_by_company_or_title(left_text)
                if matched is not None:
                    self._ensure_entry(matched.id)
                elif r'\bfseries' in left_raw and left_text and not self._is_known_company(left_text):
                    self.pending_label = left_text
                i = right_close + 1
                continue

            # Ignore pure layout commands / rules / spacing / penalties
            layout_skip = re.match(
                r'(?:'
                r'\\fontsize\{[^{}]*\}\{[^{}]*\}\\selectfont|'
                r'\\setlength\{[^{}]*\}\{[^{}]*\}|'
                r'\\(?:vspace|addvspace|hspace)\*?\{[^{}]*\}|'
                r'\\rule\{[^{}]*\}\{[^{}]*\}|'
                r'\\mbox\{[^{}]*\}|'
                r'\\(?:hyphenpenalty|emergencystretch)\s*=\s*[^\s\\]+|'
                r'\\(?:justifying|nobreak|noindent|hfill|newpage|clearpage|pagebreak)\b'
                r')',
                content[i:],
            )
            if layout_skip:
                i += layout_skip.end()
                continue

            # \par flushes current paragraph
            if content.startswith(r'\par', i) and (i + 4 >= n or not content[i + 4].isalpha()):
                flush_para()
                i += 4
                continue

            # Consume up to end of line or next major token
            line_end = content.find('\n', i)
            if line_end < 0:
                line_end = n
            chunk = content[i:line_end]
            # Check if a \par appears mid-line (common in cv-de.tex)
            par_match = re.search(r'\\par(?![A-Za-z])', chunk)
            if par_match:
                before = chunk[:par_match.start()].strip()
                if before:
                    para_buf.append(before)
                flush_para()
                i += par_match.end()
                continue
            # Check if \begin{ or \CVListBegin or \CVSection or \CVSubsection or \CVRow appears mid-line
            mid_match = re.search(
                r'\\(?:begin\{(?:minipage|itemize)\}|CVListBegin\b|CVSection\b|CVSubsection\b|CVRow\b|section\*?\{|subsection\*?\{)',
                chunk,
            )
            if mid_match and mid_match.start() > 0:
                before = chunk[:mid_match.start()].strip()
                if before:
                    para_buf.append(before)
                i += mid_match.start()
                continue
            if chunk.strip():
                para_buf.append(chunk.strip())
            i = line_end

        flush_para()

    def _extract_environment(self, text: str, start_pos: int, env_name: str, base_line: int) -> tuple[str, int]:
        open_token = f'\\begin{{{env_name}}}'
        close_token = f'\\end{{{env_name}}}'
        assert text.startswith(open_token, start_pos)
        depth = 1
        pos = start_pos + len(open_token)
        inner_start = pos
        while pos < len(text):
            next_open = text.find(open_token, pos)
            next_close = text.find(close_token, pos)
            if next_close < 0:
                raise ImportError(
                    f'Line {base_line + text.count(chr(10), 0, start_pos)}: Unclosed \\begin{{{env_name}}}.'
                )
            if 0 <= next_open < next_close:
                depth += 1
                pos = next_open + len(open_token)
            else:
                depth -= 1
                if depth == 0:
                    return text[inner_start:next_close], next_close + len(close_token)
                pos = next_close + len(close_token)
        raise ImportError(
            f'Line {base_line + text.count(chr(10), 0, start_pos)}: Unclosed \\begin{{{env_name}}}.'
        )

    def _parse_list_items(self, body: str, base_line: int) -> None:
        parts = re.split(r'\\item(?![A-Za-z])', body)
        for raw_item in parts[1:]:
            item = raw_item.strip()
            if not item:
                continue
            macro_refs = [m.group(1) for m in re.finditer(r'\\(Radius[A-Za-z]+)(?:\{\})?\b', item)]
            if macro_refs:
                remainder = _unescape_tex(re.sub(r'\\Radius[A-Za-z]+(?:\{\})?', '', item))
                if not remainder:
                    for mname in macro_refs:
                        owner, mbody, _ = self.macro_index[mname]
                        if owner == 'profile':
                            if self.current_section is None or self.current_section.kind != 'profile':
                                self._start_section('', kind='profile', layout='paragraphs')
                            assert self.current_section is not None
                            self.current_section.units.append(_split_lead_in(mbody))
                        else:
                            draft = self._ensure_entry(owner)
                            draft.bullets.append(_split_lead_in(mbody))
                    continue
            if (
                self.current_entry is not None
                and self.current_section is not None
                and self.current_section.kind in ('experience', 'education')
            ):
                self.current_entry.bullets.append(_split_lead_in(item))
                continue
            if self.current_section is None:
                self._start_section('', kind='list', layout='bullets')
            assert self.current_section is not None
            if self.current_section.kind == 'text':
                self.current_section.kind = 'list'
            self.current_section.layout = 'bullets'
            lead, text = _split_lead_in(item)
            if lead or text:
                self.current_section.units.append((lead, text))

    def finalize(
        self,
        *,
        page: PageSize,
        language: DocumentLanguage | None,
        profile_macros: list[tuple[str, str, int]],
        profile_inline: list[str],
    ) -> CvDocument:
        # Fallback: ensure profile from markers is present if body didn't expand it.
        has_profile = any(sec.kind == 'profile' and sec.units for sec in self.sections)
        if not has_profile and (profile_macros or profile_inline):
            raw_frags = [body for _, body, _ in profile_macros] or profile_inline
            joined = _join_raw_fragments(raw_frags)
            lead, text = _split_lead_in(joined)
            if lead or text:
                self.sections.insert(0, _SectionDraft(title='', kind='profile', layout='paragraphs', units=[(lead, text)]))

        # Ensure every entry from % radius:entry markers is present with its bullets.
        for meta in self.entries:
            if meta.id not in self.entry_drafts:
                draft = self._ensure_entry(meta.id)
                if meta.macros:
                    for _, body, _ in meta.macros:
                        draft.bullets.append(_split_lead_in(body))
                elif meta.inline_bullets:
                    for raw_b in meta.inline_bullets:
                        draft.bullets.append(_split_lead_in(raw_b))
            else:
                draft = self.entry_drafts[meta.id]
                if not draft.bullets and (meta.macros or meta.inline_bullets):
                    if meta.macros:
                        for _, body, _ in meta.macros:
                            draft.bullets.append(_split_lead_in(body))
                    else:
                        for raw_b in meta.inline_bullets:
                            draft.bullets.append(_split_lead_in(raw_b))

        if not self.header_name:
            raise ImportError('Line 1: Could not locate header name in LaTeX document.')

        # Build deterministic IDs and strict Pydantic models.
        used_section_ids: set[str] = set()
        used_unit_ids: set[str] = {'u-headline'}
        built_sections: list[Section] = []

        def unique_sec_id(base: str) -> str:
            candidate = base[:64].rstrip('-') or 's-section'
            if candidate not in used_section_ids:
                used_section_ids.add(candidate)
                return candidate
            idx = 2
            while True:
                suffix = f'-{idx}'
                cand = f'{candidate[:64 - len(suffix)].rstrip("-")}{suffix}'
                if cand not in used_section_ids:
                    used_section_ids.add(cand)
                    return cand
                idx += 1

        def unique_unit_id(base: str) -> str:
            candidate = base[:64].rstrip('-') or 'u-item'
            if candidate not in used_unit_ids:
                used_unit_ids.add(candidate)
                return candidate
            idx = 2
            while True:
                suffix = f'-{idx}'
                cand = f'{candidate[:64 - len(suffix)].rstrip("-")}{suffix}'
                if cand not in used_unit_ids:
                    used_unit_ids.add(cand)
                    return cand
                idx += 1

        all_text_samples: list[str] = [self.headline_text]
        for sec in self.sections:
            if not sec.units and not sec.entries:
                continue
            all_text_samples.append(sec.title)
            if sec.kind == 'profile':
                sec_id = unique_sec_id('s-profile')
                prefix = 'profile'
            elif sec.kind == 'experience':
                sec_id = unique_sec_id('s-experience')
                prefix = 'experience'
            elif sec.kind == 'education':
                sec_id = unique_sec_id('s-education')
                prefix = 'education'
            else:
                slug = _slugify(sec.title, fallback=sec.kind)
                sec_id = unique_sec_id(f's-{slug}')
                prefix = slug[:48]

            built_units: list[Unit] = []
            for idx, (lead, text) in enumerate(sec.units, 1):
                if not lead and not text:
                    continue
                all_text_samples.append(f'{lead} {text}')
                uid = unique_unit_id(f'u-{prefix}-{idx}')
                built_units.append(Unit(id=uid, text=text, lead_in=lead, origin='source'))

            built_entries: list[Entry] = []
            for edraft in sec.entries:
                eslug = _slugify(edraft.meta.id, fallback='entry')[:48]
                intro_units: list[Unit] = []
                for idx, (lead, text) in enumerate(edraft.intro, 1):
                    if not lead and not text:
                        continue
                    all_text_samples.append(f'{lead} {text}')
                    intro_units.append(
                        Unit(id=unique_unit_id(f'u-{eslug}-i{idx}'), text=text, lead_in=lead, origin='source')
                    )
                bullet_units: list[Unit] = []
                for idx, (lead, text) in enumerate(edraft.bullets, 1):
                    if not lead and not text:
                        continue
                    all_text_samples.append(f'{lead} {text}')
                    bullet_units.append(
                        Unit(id=unique_unit_id(f'u-{eslug}-b{idx}'), text=text, lead_in=lead, origin='source')
                    )
                built_entries.append(
                    Entry(
                        id=edraft.meta.id,
                        kind=edraft.meta.kind,
                        locked=edraft.meta.locked,
                        label=edraft.label,
                        intro=intro_units,
                        units=bullet_units,
                    )
                )

            built_sections.append(
                Section(
                    id=sec_id,
                    kind=sec.kind,
                    title=sec.title,
                    group=sec.group,
                    layout=sec.layout,
                    units=built_units,
                    entries=built_entries,
                )
            )

        resolved_lang: DocumentLanguage = language or _detect_language(' '.join(all_text_samples))
        for section in built_sections:
            if section.kind == 'skills':
                normalize_skills(section)
            for entry in section.entries:
                normalize_internship(entry, resolved_lang)
        return CvDocument(
            format='radius-cv-v2',
            language=resolved_lang,
            template='classic-serif-v1',
            page=page,
            photo=self.photo,
            header=Header(name=self.header_name, contacts=self.contacts),
            headline=Unit(id='u-headline', text=self.headline_text, origin='source'),
            sections=built_sections,
        )


def import_marked_tex(
    source: str,
    *,
    language: DocumentLanguage | None = None,
) -> CvDocument:
    """Parse a Radius marked LaTeX CV into a strict ``CvDocument`` without executing TeX."""
    if not isinstance(source, str) or not source.strip():
        raise ImportError('Line 1: LaTeX source must be a non-empty string.')
    if len(source) > MAX_SOURCE_CHARS:
        raise ImportError(f'Line 1: LaTeX source exceeds {MAX_SOURCE_CHARS} characters.')
    if '^^' in source:
        lineno = _line_of(source, source.index('^^'))
        raise ImportError(f'Line {lineno}: LaTeX character-code escapes (^^) are not supported.')

    begin_match = re.search(r'\\begin\{document\}', source)
    end_match = re.search(r'\\end\{document\}', source)
    if not begin_match or not end_match or begin_match.end() > end_match.start():
        raise ImportError('Line 1: Missing or invalid \\begin{document} ... \\end{document} wrapper.')

    preamble_raw = source[:begin_match.start()]
    preamble_clean = _strip_comments_preserve_lines(preamble_raw)
    # Check preamble for dangerous commands (e.g. \input, \write18, \catcode).
    for cmd_match in re.finditer(r'\\([A-Za-z]+)', preamble_clean):
        cmd = cmd_match.group(1)
        if cmd in _DANGEROUS_COMMANDS:
            lineno = _line_of(preamble_clean, cmd_match.start())
            raise ImportError(f"Line {lineno}: Forbidden LaTeX command '\\{cmd}' in preamble.")

    page: PageSize = 'letter' if re.search(r'\bletterpaper\b', preamble_clean) else 'a4'

    profile_macros, profile_inline, entries, macro_index = _parse_markers(source)

    body_raw = source[begin_match.end():end_match.start()]
    body_base_line = _line_of(source, begin_match.end())
    body_no_comments = _strip_comments_preserve_lines(body_raw)
    body_clean = _remove_helper_newcommand_defs(body_no_comments, body_base_line)

    _validate_commands(body_clean, _BODY_ALLOWED_COMMANDS, set(macro_index), body_base_line)

    builder = _DocumentBuilder(entries, macro_index)

    # Walk top-level document body: handle \AddToShipoutPictureFG* picture blocks and flowing text.
    pos = 0
    n = len(body_clean)
    while pos < n:
        pic_idx = body_clean.find(r'\AddToShipoutPictureFG', pos)
        if pic_idx < 0:
            trailing = body_clean[pos:]
            if trailing.strip():
                builder.parse_flowing(trailing, body_base_line + body_clean.count('\n', 0, pos))
            break

        before = body_clean[pos:pic_idx]
        if before.strip():
            builder.parse_flowing(before, body_base_line + body_clean.count('\n', 0, pos))

        brace_pos = body_clean.find('{', pic_idx)
        if brace_pos < 0:
            raise ImportError(
                f'Line {body_base_line + body_clean.count(chr(10), 0, pic_idx)}: '
                f'Malformed \\AddToShipoutPictureFG block.'
            )
        block_end = _find_matching_brace(body_clean, brace_pos, body_base_line)
        shipout_block = body_clean[brace_pos + 1:block_end]
        shipout_line = body_base_line + body_clean.count('\n', 0, brace_pos + 1)

        begin_pic = re.search(r'\\begin\{picture\}\([^)]*\)', shipout_block)
        end_pic = shipout_block.rfind(r'\end{picture}')
        if not begin_pic or end_pic < 0 or end_pic < begin_pic.end():
            raise ImportError(f'Line {shipout_line}: Expected \\begin{{picture}} inside \\AddToShipoutPictureFG.')
        pic_content = shipout_block[begin_pic.end():end_pic]
        pic_line = shipout_line + shipout_block.count('\n', 0, begin_pic.end())
        builder.parse_picture_page(pic_content, pic_line)
        pos = block_end + 1

    return builder.finalize(
        page=page,
        language=language,
        profile_macros=profile_macros,
        profile_inline=profile_inline,
    )
