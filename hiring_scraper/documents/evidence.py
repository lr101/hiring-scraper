"""Build the deterministic evidence pack (evidence id -> text) from base CV and profile."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Mapping
from typing import Any

from hiring_scraper.documents.schema import CvDocument, Entry, Unit
from hiring_scraper.documents.text import unit_full_text


def skill_slug(name: str) -> str:
    """Return a stable lowercase slug for a skill name."""
    cleaned = name.strip()
    special = {
        'c++': 'cpp',
        'c#': 'csharp',
        '.net': 'dotnet',
        'node.js': 'nodejs',
    }
    if cleaned.casefold() in special:
        return special[cleaned.casefold()]
    umlauts = {'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', 'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue'}
    text = ''.join(umlauts.get(ch, ch) for ch in cleaned)
    text = text.replace('++', '-plus-plus').replace('#', '-sharp')
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()
    return slug or 'skill'


def _format_education(items: Any) -> str:
    if isinstance(items, str):
        return items.strip()
    if not isinstance(items, list):
        return ''
    parts: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif isinstance(item, Mapping):
            level = str(item.get('level') or '').strip()
            field_name = str(item.get('field') or '').strip()
            note = str(item.get('note') or '').strip()
            head = ' '.join(p for p in (level, field_name) if p)
            if head and note:
                parts.append(f'{head} ({note})')
            elif head or note:
                parts.append(head or note)
    return '; '.join(parts)


def _format_experience_years(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ''
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ''
    rendered = str(int(num)) if num.is_integer() else f'{num:g}'
    return f'{rendered} years'


def _format_skill_text(skill: str, evidence_entry: Any) -> str:
    if isinstance(evidence_entry, Mapping):
        context = str(evidence_entry.get('context') or '').strip()
        note = str(evidence_entry.get('note') or '').strip()
        if context and note:
            return f'{skill} ({context}): {note}'
        if note:
            return f'{skill}: {note}'
        if context:
            return f'{skill} ({context})'
    elif isinstance(evidence_entry, str) and evidence_entry.strip():
        return f'{skill}: {evidence_entry.strip()}'
    return skill


def entry_bullets(entry: Entry) -> Iterator[tuple[str, Unit]]:
    """Yield evidence IDs and their visible base bullets in source order."""
    visible = (unit for unit in entry.units if not unit.hidden and unit_full_text(unit))
    for index, unit in enumerate(visible, 1):
        yield f'cv:{entry.id}:b{index}', unit


def build_evidence(base_cv: CvDocument, profile: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Build the evidence dictionary ``{evidence_id: text}`` from ``base_cv`` and ``profile``."""
    evidence: dict[str, str] = {}
    prof: Mapping[str, Any] = profile or {}
    localized: Mapping[str, Any] = {}
    raw_localized = prof.get('localized_content')
    if isinstance(raw_localized, Mapping):
        lang_block = raw_localized.get(base_cv.language)
        if isinstance(lang_block, Mapping):
            localized = lang_block

    # 1. Base CV units
    if not base_cv.headline.hidden:
        headline_text = unit_full_text(base_cv.headline)
        if headline_text:
            evidence['cv:headline'] = headline_text

    profile_idx = 0
    for section in base_cv.sections:
        if section.kind == 'profile':
            for unit in section.units:
                if unit.hidden:
                    continue
                text = unit_full_text(unit)
                if text:
                    profile_idx += 1
                    evidence[f'cv:profile:p{profile_idx}'] = text
            continue

        if section.kind in ('experience', 'education'):
            for u_idx, unit in enumerate(
                (u for u in section.units if not u.hidden and unit_full_text(u)), 1
            ):
                evidence[f'cv:{section.id}:u{u_idx}'] = unit_full_text(unit)
            for entry in section.entries:
                intro_idx = 0
                for unit in entry.intro:
                    if unit.hidden:
                        continue
                    text = unit_full_text(unit)
                    if text:
                        intro_idx += 1
                        evidence[f'cv:{entry.id}:i{intro_idx}'] = text
                for source, unit in entry_bullets(entry):
                    evidence[source] = unit_full_text(unit)
            continue

        u_idx = 0
        for unit in section.units:
            if unit.hidden:
                continue
            text = unit_full_text(unit)
            if text:
                u_idx += 1
                evidence[f'cv:{section.id}:u{u_idx}'] = text

    # 2. Profile skills (top-level + localized + skill_evidence)
    skill_evidence: dict[str, Any] = {}
    for src in (prof.get('skill_evidence'), localized.get('skill_evidence')):
        if isinstance(src, Mapping):
            for k, v in src.items():
                if isinstance(k, str) and k.strip():
                    skill_evidence[k.strip()] = v

    raw_skills: list[str] = []
    for src in (prof.get('skills'), localized.get('skills')):
        if isinstance(src, list):
            for item in src:
                if isinstance(item, str) and item.strip():
                    raw_skills.append(item.strip())
    for k in skill_evidence:
        if k not in raw_skills:
            raw_skills.append(k)

    for skill in raw_skills:
        key = f'skill:{skill_slug(skill)}'
        text = _format_skill_text(skill, skill_evidence.get(skill))
        if key not in evidence or len(text) > len(evidence[key]):
            evidence[key] = text

    # 3. Career evidence lines from application_personalization.career_evidence
    personalization = prof.get('application_personalization')
    loc_personalization = localized.get('application_personalization')
    career_evidence_raw = ''
    if isinstance(loc_personalization, Mapping) and str(loc_personalization.get('career_evidence') or '').strip():
        career_evidence_raw = str(loc_personalization['career_evidence'])
    elif isinstance(personalization, Mapping) and str(personalization.get('career_evidence') or '').strip():
        career_evidence_raw = str(personalization['career_evidence'])

    if career_evidence_raw:
        ev_idx = 0
        for raw_line in career_evidence_raw.splitlines():
            line = re.sub(r'^\s*[-•*]\s+', '', raw_line).strip()
            if line:
                ev_idx += 1
                evidence[f'evidence:{ev_idx}'] = line

    # 4. Structured profile:<field> entries
    summary = str(localized.get('summary') or prof.get('summary') or '').strip()
    if summary:
        evidence['profile:summary'] = summary

    education = _format_education(localized.get('education') or prof.get('education'))
    if education:
        evidence['profile:education'] = education

    raw_certs = localized.get('certifications') or prof.get('certifications')
    if isinstance(raw_certs, list):
        certs = [str(c).strip() for c in raw_certs if str(c).strip()]
        if certs:
            evidence['profile:certifications'] = ', '.join(certs)
    elif isinstance(raw_certs, str) and raw_certs.strip():
        evidence['profile:certifications'] = raw_certs.strip()

    lang_levels = prof.get('language_levels')
    if isinstance(lang_levels, Mapping) and lang_levels:
        pairs = [f'{str(lang).strip()}: {str(level).strip()}' for lang, level in lang_levels.items() if str(lang).strip() and str(level).strip()]
        if pairs:
            evidence['profile:language_levels'] = ', '.join(pairs)

    exp_years = _format_experience_years(prof.get('experience_years'))
    if exp_years:
        evidence['profile:experience_years'] = exp_years

    # Optional structured application preference facts if explicitly saved
    app_details = prof.get('application_details')
    if isinstance(app_details, Mapping):
        prefs = app_details.get('preferences')
        loc_prefs = localized.get('application_preferences')
        if isinstance(prefs, Mapping):
            notice = str(
                (loc_prefs.get('notice_period') if isinstance(loc_prefs, Mapping) else '')
                or prefs.get('notice_period')
                or ''
            ).strip()
            if notice:
                evidence['profile:notice_period'] = notice
            available_from = str(prefs.get('available_from') or '').strip()
            if available_from:
                evidence['profile:available_from'] = available_from
            salary_amount = str(prefs.get('salary_amount') or '').strip()
            if salary_amount:
                currency = str(prefs.get('salary_currency') or '').strip()
                period = str(prefs.get('salary_period') or '').strip()
                parts = [p for p in (salary_amount, currency, f'/{period}' if period else '') if p]
                evidence['profile:salary'] = ' '.join(parts)

    return evidence
