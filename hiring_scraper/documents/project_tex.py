"""Project a structured CvDocument to flowing marked LaTeX for matcher compatibility."""
from __future__ import annotations

import json
import re
from typing import Any

from hiring_scraper.documents.schema import Contact, CvDocument, Entry, Section, Unit
from hiring_scraper.documents.text import unit_full_text

_TEX_ESCAPE_RE = re.compile(r'([\\{}&%$#_~^])')
_TEX_ESCAPES = {
    '\\': r'\textbackslash{}',
    '{': r'\{',
    '}': r'\}',
    '&': r'\&',
    '%': r'\%',
    '$': r'\$',
    '#': r'\#',
    '_': r'\_',
    '~': r'\textasciitilde{}',
    '^': r'\textasciicircum{}',
}

_LANGUAGE_SECTION_TITLES = frozenset({
    'languages',
    'language skills',
    'sprachen',
    'sprachkenntnisse',
    'idiomas',
    'langues',
})


def escape_tex(text: str) -> str:
    """Escape plain Unicode text for safe inclusion in LaTeX."""
    return _TEX_ESCAPE_RE.sub(lambda m: _TEX_ESCAPES[m.group(1)], text)


def _alpha_index(index: int, *, upper: bool = False) -> str:
    """Convert 1-based positive integer to alphabetic suffix (1->a, 26->z, 27->aa)."""
    if index < 1:
        raise ValueError('Index must be >= 1')
    base = ord('A') if upper else ord('a')
    chars: list[str] = []
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(base + rem))
    return ''.join(reversed(chars))


def _format_unit_tex(unit: Unit) -> str:
    lead = unit.lead_in.strip()
    body = unit.text.strip()
    if lead and body:
        return rf'\textbf{{{escape_tex(lead)}}} {escape_tex(body)}'
    if lead:
        return rf'\textbf{{{escape_tex(lead)}}}'
    return escape_tex(body)


def _format_contact_tex(contact: Contact) -> str:
    text = escape_tex(contact.text.strip())
    href = contact.href.strip()
    if href and (href.startswith('mailto:') or href.startswith('https://') or href.startswith('http://')):
        return rf'\href{{{escape_tex(href)}}}{{{text}}}'
    return text


def _entry_date_tex(entry: Entry, language: str) -> str:
    locked = entry.locked
    if locked.current:
        present = 'heute' if language == 'de' else 'present'
        return f'{locked.start}--{present}'
    if not locked.end or locked.end == locked.start:
        return locked.start
    return f'{locked.start}--{locked.end}'


def facts_from_document(doc: CvDocument) -> dict[str, list[dict[str, Any]]]:
    """Derive career facts directly from a ``CvDocument`` matching ``career_facts_from_tex``."""
    experiences: list[dict[str, Any]] = []
    education: list[dict[str, Any]] = []
    for section in doc.sections:
        for entry in section.entries:
            bullet_lines = [
                line for unit in entry.units
                if not unit.hidden and (line := unit_full_text(unit))
            ]
            row = {
                'company': entry.locked.company,
                'title': entry.locked.title,
                'location': entry.locked.location,
                'description': '\n'.join(bullet_lines),
                'start': entry.locked.start,
                'end': entry.locked.end,
                'current': entry.locked.current,
            }
            if entry.kind == 'work':
                experiences.append(row)
            else:
                education.append(row)
    result: dict[str, list[dict[str, Any]]] = {}
    if experiences:
        result['experiences'] = experiences
    if education:
        result['education'] = education
    return result


def project_tex(doc: CvDocument) -> str:
    """Project a ``CvDocument`` to flowing marked LaTeX without absolute coordinates.

    Emits ``% radius:profile`` and ``% radius:entry`` / ``% radius:responsibilities`` /
    ``% radius:end`` blocks with one single-line ``\\newcommand{\\Radius...}{...}`` per
    visible bullet so ``hiring_scraper.app.tex_cv.parse_tex_cv`` and
    ``career_facts_from_tex`` parse it directly.
    """
    paper = 'letterpaper' if doc.page == 'letter' else 'a4paper'
    lines: list[str] = [
        f'\\documentclass[10pt,{paper}]{{article}}',
        f'\\usepackage[{paper},margin=20mm]{{geometry}}',
        r'\usepackage[T1]{fontenc}',
        r'\usepackage[utf8]{inputenc}',
        r'\usepackage[unicode,hidelinks]{hyperref}',
        r'\pagestyle{empty}',
        r'\setlength{\parindent}{0pt}',
    ]

    profile_macros: list[str] = []
    profile_units: list[Unit] = []
    for section in doc.sections:
        if section.kind == 'profile':
            profile_units.extend(u for u in section.units if not u.hidden and unit_full_text(u))

    if profile_units:
        lines.append('% radius:profile')
        for idx, unit in enumerate(profile_units, 1):
            macro_name = f'RadiusProfile{_alpha_index(idx, upper=True)}'
            profile_macros.append(macro_name)
            lines.append(f'\\newcommand{{\\{macro_name}}}{{{_format_unit_tex(unit)}}}')
        lines.append('% radius:endprofile')

    entry_macros: dict[str, list[str]] = {}
    entry_counter = 0
    for section in doc.sections:
        for entry in section.entries:
            entry_counter += 1
            entry_code = _alpha_index(entry_counter, upper=False)
            marker_payload = {
                'id': entry.id,
                'kind': entry.kind,
                'company': entry.locked.company,
                'title': entry.locked.title,
                'location': entry.locked.location,
                'start': entry.locked.start,
                'end': entry.locked.end,
                'current': entry.locked.current,
            }
            lines.append('% radius:entry ' + json.dumps(marker_payload, ensure_ascii=False))
            lines.append('% radius:responsibilities')
            macros_for_entry: list[str] = []
            visible_bullets = [u for u in entry.units if not u.hidden and unit_full_text(u)]
            for b_idx, unit in enumerate(visible_bullets, 1):
                macro_name = f'RadiusE{entry_code}B{_alpha_index(b_idx, upper=False)}'
                macros_for_entry.append(macro_name)
                lines.append(f'\\newcommand{{\\{macro_name}}}{{{_format_unit_tex(unit)}}}')
            entry_macros[entry.id] = macros_for_entry
            lines.append('% radius:end')

    lines.append(r'\begin{document}')
    lines.append(f'{{\\bfseries {escape_tex(doc.header.name.strip())}}}\\par')
    if not doc.headline.hidden and unit_full_text(doc.headline):
        lines.append(f'{_format_unit_tex(doc.headline)}\\par')
    if doc.header.contacts:
        contact_tex = r' \textbar\ '.join(_format_contact_tex(c) for c in doc.header.contacts)
        lines.append(f'{contact_tex}\\par')

    profile_emitted = False
    for section in doc.sections:
        if section.kind == 'profile':
            if not profile_macros or profile_emitted:
                continue
            profile_emitted = True
            if section.title.strip():
                lines.append(f'\\section*{{{escape_tex(section.title.strip())}}}')
            for macro_name in profile_macros:
                lines.append(f'\\{macro_name}{{}}\\par')
            continue

        if section.kind in ('experience', 'education'):
            if not section.entries and not any(not u.hidden and unit_full_text(u) for u in section.units):
                continue
            if section.title.strip():
                lines.append(f'\\section*{{{escape_tex(section.title.strip())}}}')
            for unit in section.units:
                if not unit.hidden and unit_full_text(unit):
                    lines.append(f'{_format_unit_tex(unit)}\\par')
            for entry in section.entries:
                if entry.label.strip():
                    lines.append(f'{{\\bfseries {escape_tex(entry.label.strip())}}}\\par')
                company_tex = f'\\textbf{{{escape_tex(entry.locked.company.strip())}}}'
                if entry.locked.location.strip():
                    lines.append(f'{company_tex} \\hfill {escape_tex(entry.locked.location.strip())}\\par')
                else:
                    lines.append(f'{company_tex}\\par')
                title_tex = f'\\textbf{{\\textit{{{escape_tex(entry.locked.title.strip())}}}}}'
                lines.append(f'{title_tex} \\hfill {_entry_date_tex(entry, doc.language)}\\par')
                for intro_unit in entry.intro:
                    if not intro_unit.hidden and unit_full_text(intro_unit):
                        lines.append(f'{_format_unit_tex(intro_unit)}\\par')
                macros = entry_macros.get(entry.id, [])
                if macros:
                    lines.append(r'\begin{itemize}')
                    for macro_name in macros:
                        lines.append(f'\\item \\{macro_name}{{}}')
                    lines.append(r'\end{itemize}')
            continue

        visible_units = [u for u in section.units if not u.hidden and unit_full_text(u)]
        if not visible_units:
            continue
        title = section.title.strip()
        if title:
            lines.append(f'\\section*{{{escape_tex(title)}}}')
        is_language_section = title.casefold() in _LANGUAGE_SECTION_TITLES
        if is_language_section:
            for unit in visible_units:
                lines.append(f'\\noindent {_format_unit_tex(unit)}\\par')
        elif section.layout == 'inline':
            joined = ' • '.join(_format_unit_tex(u) for u in visible_units)
            lines.append(f'{joined}\\par')
        elif section.layout == 'bullets':
            lines.append(r'\begin{itemize}')
            for unit in visible_units:
                lines.append(f'\\item {_format_unit_tex(unit)}')
            lines.append(r'\end{itemize}')
        else:
            for unit in visible_units:
                lines.append(f'{_format_unit_tex(unit)}\\par')

    lines.append(r'\end{document}')
    return '\n'.join(lines) + '\n'
