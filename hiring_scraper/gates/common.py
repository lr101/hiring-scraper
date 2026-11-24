"""Shared text-parsing, parenthetical masking, and non-requirement helpers for v2 gates."""
from __future__ import annotations

from functools import lru_cache
import re

from hiring_scraper.requirements import (
    scoped_sentences,
    structured_requirement_texts,
    structured_source_scope,
    structured_text,
)

_PAREN_RE = re.compile(r'\([^()]*\)')


def mask_parens(text: str) -> tuple[str, dict[str, str]]:
    """Replace flat parenthetical spans with private-use placeholders."""
    protected: dict[str, str] = {}
    working = text
    for idx, m in reversed(list(enumerate(_PAREN_RE.finditer(working)))):
        marker = f'\ue000p{idx}\ue001'
        protected[marker] = m.group()
        working = working[:m.start()] + marker + working[m.end():]
    return working, protected


def unmask_parens(text: str, protected: dict[str, str]) -> str:
    """Restore placeholders produced by ``mask_parens``."""
    for marker, orig in protected.items():
        text = text.replace(marker, orig)
    return text

# Expanded non-requirement detector (company boasts, employer offers, client language).
NON_REQUIREMENT_V2 = re.compile(
    r'(?:^|[.;:!?\n]\s*)(?:(?:about|über)\s+)?'
    r'(?:our\s+(?:company|team|teams|firm|organi[sz]ation)\s+'
    r'(?:(?:has|have|was|were|speaks?|uses?|runs?|operates?|builds?|develops?|provides?|offers?|delivers?|serves?|employs?|consists?|boasts?|brings?)\b|'
    r'(?:is|are)\s+(?!(?:looking\s+for|seeking|hiring|recruiting|requiring)\b))|'
    r'(?:unser\s+unternehmen|unsere\s+firma|unser\s+team)\s+'
    r'(?:(?:hat|haben|war|waren|bietet|entwickelt|nutzt|arbeitet|spricht|beschäftigt|verfügt|blickt|besteht)\b|'
    r'(?:ist|sind)\s+(?!(?:auf\s+der\s+suche|suchend)\b)))|'
    r'\b(?:we have|the company has|'
    r'we offer|benefits|perks|you receive|you get|you can take|we provide you with|'
    r'du erhältst|sie erhalten|wir bieten|'
    r'wir haben|wir verfügen über|wir blicken|blicken wir|das unternehmen hat|das unternehmen verfügt|'
    r'seit \d{4}|since \d{4}|founded in \d{4}|gegründet|'
    r'in business|am markt|auf dem markt|'
    r'common among our clients|spoken by our clients|our clients speak|our customers speak|our team speaks)\b',
    re.I,
)


_OTHER_ACTOR_SUBJECT = re.compile(
    r'^(?:(?:our|the|a|another|other)\s+)?(?:finance|people|hr|recruiting|legal|design|innovation)\s+(?:team|department)\b|'
    r'^(?:another|other|andere\w*)\s+(?:[\w-]+\s+){0,2}(?:teams?|departments?|abteilung\w*)\b|'
    r'^(?:our|their|the|my|another|other|unsere?\w*|deren|die|das)\s+(?:[\w-]+\s+){0,4}'
    r'(?:staff|employees?|workers?|department|partners?|contractors?|colleague\w*|kolleg\w*|abteilung|'
    r'clients?|customers?|kunden|kundinnen|'
    r'[\w-]*mitarbeit\w*|[\w-]*kräfte\w*|[\w-]*kraefte\w*|[\w-]*personal)\b|'
    r'^(?:(?:(?:a|the|our|ein\w*|der|die)\s+)?senior\s+(?:colleague\w*|manager\w*|kolleg\w*)|'
    r'the\s+director|die\s+personalabteilung|unsere\s+personalabteilung)\b',
    re.I,
)
_HIRING_TEAM_LEAD = re.compile(
    r'^(?:our|the|unser\w*)\s+(?:[\w-]+\s+){0,2}(?:team|department|abteilung)\s+'
    r'(?:(?:is|are)\s+(?:looking\s+for|seeking|hiring|recruiting|requiring)|'
    r'sucht|ist\s+auf\s+der\s+suche|benötigt|erfordert)\b',
    re.I,
)


def _is_other_actor_subject(text: str) -> bool:
    """Return True when ``text`` starts with a third-party/other-staff subject rather than the vacancy/applicant."""
    stripped = (text or '').strip()
    if not stripped or _HIRING_TEAM_LEAD.search(stripped):
        return False
    return bool(_OTHER_ACTOR_SUBJECT.search(stripped))


def job_requirement_texts(job: dict) -> tuple:
    """The content and source scopes that determine the parsed posting cache key."""
    raw = job.get('raw_metadata') or {}
    detail_req = raw.get('detail_requirements') or {}

    description = str(job.get('description') or '')
    texts: list[tuple[str, str, bool]] = [('description', description, False)]

    # Include structured metadata fields (qualifications, skills, languages, visa/auth, etc.).
    for prefix, container in (('structured', raw), ('detail', detail_req)):
        if not isinstance(container, dict):
            continue
        for field in (
            'responsibilities',
            'duties',
            'skills',
            'qualifications',
            'experienceRequirements',
            'educationRequirements',
        ):
            val = container.get(field)
            if val:
                for src, txt in structured_requirement_texts({field: val}, prefix):
                    texts.append((src, str(txt), False))
        # Explicit language lists in structured metadata are requirements-scoped.
        langs_meta = container.get('languages')
        if langs_meta:
            for src, txt in structured_requirement_texts({'languages': langs_meta}, prefix):
                texts.append((src, str(txt), True))
        for auth_field in ('work_authorization', 'work_permit', 'arbeitserlaubnis', 'visa'):
            val = container.get(auth_field)
            if isinstance(val, str) and val.strip():
                texts.append((f'{prefix}.{auth_field}', f'{auth_field}: {val}', True))

    return tuple(texts)


@lru_cache(maxsize=256)
def parse_job_requirement_texts(texts: tuple) -> tuple:
    return tuple(
        (source, force_req, tuple(dict.fromkeys(
            scoped_sentences(
                structured_text(text),
                initial_scope='requirements' if force_req else structured_source_scope(source),
            )
        )))
        for source, text, force_req in texts if text
    )
