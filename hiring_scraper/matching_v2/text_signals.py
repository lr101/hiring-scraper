"""Text/clause classification, seniority/tenure parsing, and discipline detection for v2."""
from __future__ import annotations

import re
from typing import Any

from hiring_scraper.matching import (
    EXPERIENCE_PATTERN,
    OPTIONAL,
    REQUIREMENT,
    _experience_years,
    _is_experience_match,
    plain_text,
)
from hiring_scraper.matching_v2.gates import (
    NON_REQUIREMENT_V2,
    is_language_or_visa_clause,
    mask_parens,
    unmask_parens,
)
from hiring_scraper.matching_v2.taxonomy import (
    DISCIPLINES,
    _CONTENT_STOPWORDS,
    _CONTENT_TOK_RE,
    _ROLE_GENDER_RE,
    _ROLE_GROUP_PATTERNS,
    _ROLE_STOP,
    _ROLE_TOK_RE,
    _SENIORITY_LEVELS,
)
from hiring_scraper.requirements import DEGREE, is_source_instruction, requirement_polarity
from hiring_scraper.task_alignment import other_actor

_LOCAL_TRAINING_OFFERED_RE = re.compile(
    r'\b(?:we\s+(?:will\s+)?(?:train|teach)|training\s+(?:is\s+)?(?:provided|offered)|'
    r'no\s+(?:prior\s+)?experience\s+(?:required|needed)|keine\s+vorkenntnisse)\b',
    re.I,
)

# ---------------------------------------------------------------------------
# Substantive skill mention classification (ported & improved from Phase 1)
# ---------------------------------------------------------------------------

_INCIDENTAL_TOOL_RE = re.compile(
    r'\b(?:we\s+use|our\s+(?:stack|team|company)\s+(?:uses?|runs?|works?\s+with)|'
    r'such\s+as|for\s+example|e\.g\.|z\.b\.|wie\s+z\.?b\.?|'
    r'via|through|über|using\s+our|in\s+our|auf\s+unserer|'
    r'training\s+(?:provided|offered|on)|we\s+(?:train|teach)\s+you|schulung\s+wird\s+angeboten|'
    r'no\s+prior\s+experience\s+(?:in|with)\s+needed|keine\s+vorkenntnisse)\b',
    re.I,
)
_PASSIVE_CONTAINER_RE = re.compile(
    r'\b(?:documented\s+in|stored\s+in|tracked\s+in|hosted\s+on|communicated\s+via|'
    r'managed\s+in|maintained\s+in|shared\s+via|published\s+on|'
    r'dokumentiert\s+in|gepflegt\s+in|verwaltet\s+in|kommuniziert\s+über)\b',
    re.I,
)
_PASSIVE_SYSTEM_MODIFIER = re.compile(
    r'^\s*[-–]?\s*(?:based|basiert\w*|gestützt\w*|powered|driven|hosted)\s+'
    r'(?:[\w-]+\s+){0,2}(?:system\w*|tool\w*|portal\w*|plattform\w*|platform\w*|software|app\w*|'
    r'anwendung\w*|datenbank\w*|database\w*|lösung\w*|solution\w*)\b',
    re.I,
)
_EMPLOYER_CONTAINER_PREFIX = re.compile(
    r'\b(?:our|unser\w*)\s+(?:(?:internal|in-house|custom|company|hospital|warehouse|existing|modern|intern\w*|eigen\w*)\s+){0,2}$',
    re.I,
)
_EMPLOYER_CONTAINER_SUFFIX = re.compile(
    r'^\s+(?:system\w*|portal\w*|plattform\w*|platform\w*|tool\w*|app\w*|anwendung\w*|software|infrastructure|environment)\b',
    re.I,
)
_ACTIVE_TOOL_VERB = re.compile(
    r'\b(?:develop\w*|build\w*|built|write|writing|code|coding|program\w*|script\w*|query\w*|'
    r'architect\w*|engineer\w*|deploy\w*|implement\w*|maintain\w*|administer\w*|automate\w*|'
    r'configure\w*|debug\w*|entwickel\w*|entwicklung|erstell\w*|bau\w*|entwerf\w*|programmier\w*|'
    r'cod\w*|skript\w*|abfrag\w*|schreib\w*|pfleg\w*|wart\w*|betreib\w*|implementier\w*|'
    r'administrier\w*|automatisier\w*|konfigurier\w*)\b',
    re.I,
)
_TRAINING_OFFERED = re.compile(
    r'\b(?:(?:free|paid|complimentary|internal|optional|kostenlos\w*|bezahlt\w*|intern\w*)\s+'
    r'(?:[\w-]+\s+){0,3}(?:training|courses?|workshops?|schulung\w*|weiterbildung\w*|kurs\w*)|'
    r'(?:training|courses?|workshops?|schulung\w*|weiterbildung\w*)\s+(?:offered|provided|available|included|angeboten|möglich))\b',
    re.I,
)
_OTHER_ACTOR_RELATIVE = re.compile(
    r'\b(?:engineers?|developers?|programmers?|analysts?|specialists?|colleagues?|teams?|'
    r'it\s+team|data\s+team|entwickler\w*|ingenieur\w*|programmierer\w*|analyst\w*|spezialist\w*|'
    r'kolleg\w*|fachabteilung\w*|team\w*|it.team|datenteam)\s*(?:,\s*)?'
    r'(?:who|that|which|die|der|welche[rs]?)\b[^.;]{0,60}$',
    re.I,
)


def classify_skill_mention(sentence: str, start: int, end: int, scope: str) -> str:
    """Classify a skill mention as 'required', 'duty', 'preferred', or 'incidental'."""
    if is_source_instruction(sentence) or is_language_or_visa_clause(sentence):
        return 'incidental'
    if scope == 'benefits':
        return 'incidental'
    if NON_REQUIREMENT_V2.search(sentence) and not REQUIREMENT.search(sentence):
        return 'incidental'
    if other_actor(sentence):
        return 'incidental'

    before_full = sentence[:start]
    after_full = sentence[end:]
    before = sentence[max(0, start - 50):start]
    local = sentence[max(0, start - 50):min(len(sentence), end + 50)]
    if _OTHER_ACTOR_RELATIVE.search(before_full):
        return 'incidental'
    if _PASSIVE_CONTAINER_RE.search(before):
        return 'incidental'
    if scope != 'requirements' and not REQUIREMENT.search(sentence) and _TRAINING_OFFERED.search(sentence):
        return 'incidental'
    if _LOCAL_TRAINING_OFFERED_RE.search(local):
        return 'incidental'
    if not _ACTIVE_TOOL_VERB.search(sentence):
        if _PASSIVE_SYSTEM_MODIFIER.match(after_full[:60]):
            return 'incidental'
        if (
            scope != 'requirements'
            and not REQUIREMENT.search(sentence)
            and _EMPLOYER_CONTAINER_PREFIX.search(before)
            and _EMPLOYER_CONTAINER_SUFFIX.match(after_full[:40])
        ):
            return 'incidental'

    pol = requirement_polarity(sentence, start, end)
    if pol in {'waived', 'instruction'}:
        return 'incidental'
    if pol == 'optional' or scope == 'optional' or OPTIONAL.search(local):
        return 'preferred'
    if _INCIDENTAL_TOOL_RE.search(before) and scope not in {'requirements', 'duties'}:
        return 'incidental'
    if scope == 'requirements' or REQUIREMENT.search(sentence):
        return 'required'
    if scope == 'duties':
        return 'duty'
    return 'duty'


# ---------------------------------------------------------------------------
# Company-subject experience boast guard & OR-arm parsing
# ---------------------------------------------------------------------------

_COMPANY_EXPERIENCE_CLAIM = re.compile(
    r'\b(?:'
    r'(?:our|the)\s+(?:company|firm|team|organi[sz]ation|agency|group|brand|startup|business)\s+'
    r'(?:has|have|boasts?|brings?|celebrates?|looks?\s+back\s+on|with)\b|'
    r'(?:we|wir)\s+(?:have|haben|verfügen\s+über|blicken\s+auf|bringen)\b|'
    r'(?:unser\w*)\s+(?:unternehmen|firma|team|gruppe|agentur)\s+'
    r'(?:hat|haben|verfügt|blickt|besteht|existiert)\b|'
    r'(?:over|more\s+than|über|mehr\s+als)\s+\d{1,2}\s+(?:years?|jahre?n?)\s+'
    r'(?:of\s+)?(?:history|heritage|tradition|market\s+(?:presence|leadership)|in\s+business|'
    r'geschichte|tradition|markterfahrung|erfahrung\s+am\s+markt|bestehen)|'
    r'(?:founded|established|gegründet)\s+(?:in\s+\d{4}|over\s+\d{1,2}\s+years?\s+ago|vor\s+\d{1,2}\s+jahren)'
    r')\b',
    re.I,
)


def is_company_experience_claim(sentence: str) -> bool:
    """Return True when a tenure/years mention describes company history rather than applicant tenure."""
    return bool(_COMPANY_EXPERIENCE_CLAIM.search(sentence))


_OR_ARM_SPLIT_RE = re.compile(r'\s+\b(?:or|oder|alternatively|alternativ)\b\s+', re.I)
_TENURE_NON_YEARS_ALT_RE = re.compile(
    r'\b(?:equivalent|comparable|vergleichbar|ausbildung|studium|degree|quereinsteiger)\b',
    re.I,
)


def split_or_arms(clause: str) -> list[str]:
    """Split a requirement clause into top-level OR alternatives."""
    if not clause:
        return []
    working, protected = mask_parens(clause)
    parts = [p.strip(' ,;.') for p in _OR_ARM_SPLIT_RE.split(working) if p.strip(' ,;.')]
    if len(parts) <= 1:
        return [clause.strip()]
    return [unmask_parens(part, protected).strip() for part in parts]



def detect_title_seniority(title: str) -> int | None:
    """Detect seniority tier (0=intern .. 5=director/head) from a job or role title."""
    if not title:
        return None
    # Check extreme bands first (intern/working_student and director/lead/senior/junior),
    # falling back to mid-level (2) only if nothing stronger matches.
    for rank in (0, 5, 4, 3, 1, 2):
        for lv, pat in _SENIORITY_LEVELS:
            if lv == rank and pat.search(title):
                return lv
    return None


def _tenure_minimums(
    filtered_sentences: list[tuple[str, str, str]],
) -> list[tuple[float, str, str]]:
    """Extract stated tenure minima from requirements in the scoring evidence."""
    minimums = []
    for source, sentence, scope in filtered_sentences:
        if scope not in {'neutral', 'requirements'} or not EXPERIENCE_PATTERN.search(sentence):
            continue
        if is_source_instruction(sentence) or is_company_experience_claim(sentence):
            continue
        if NON_REQUIREMENT_V2.search(sentence) and not REQUIREMENT.search(sentence):
            continue
        if OPTIONAL.search(sentence) and not REQUIREMENT.search(sentence):
            continue

        arms = split_or_arms(sentence)
        arm_years: list[float] = []
        has_non_years_alternative = False
        for arm in arms:
            matches = [m for m in EXPERIENCE_PATTERN.finditer(arm) if _is_experience_match(m)]
            if not matches:
                if len(arms) > 1 and (
                    DEGREE.search(arm) or _TENURE_NON_YEARS_ALT_RE.search(arm)
                ):
                    has_non_years_alternative = True
                continue
            yrs_list = [y for m in matches if (y := _experience_years(m)) is not None and 0.5 <= y <= 20]
            if not yrs_list:
                continue
            min_arm = min(yrs_list)
            first = matches[0]
            has_explicit_min = bool(
                first.group('range_max')
                or first.group('marker')
                or first.group('reverse_marker')
                or first.group('plus')
                or REQUIREMENT.search(arm)
            )
            if scope == 'requirements' or (scope == 'neutral' and has_explicit_min):
                if requirement_polarity(arm, first.start(), first.end()) == 'required':
                    arm_years.append(min_arm)

        if not arm_years:
            continue
        effective = min(arm_years)
        if has_non_years_alternative:
            effective = max(0.0, effective - 1.5)
        minimums.append((effective, sentence, source))
    return minimums


def _profile_experience_titles(profile: dict) -> list[str]:
    """Collect demonstrated past role titles from experience containers (never desired_roles)."""
    titles: list[str] = []
    seen: set[str] = set()

    def _add(val: Any) -> None:
        s = str(val or '').strip()
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            titles.append(s)

    for entry in (profile.get('experience_entries') or []):
        if isinstance(entry, dict):
            _add(entry.get('title') or entry.get('role'))
    app_details = profile.get('application_details') or {}
    if isinstance(app_details, dict):
        for entry in (app_details.get('experiences') or []):
            if isinstance(entry, dict):
                _add(entry.get('title') or entry.get('role'))
    facts = profile.get('cv_career_facts') or {}
    if isinstance(facts, dict):
        for entry in (facts.get('experiences') or []):
            if isinstance(entry, dict):
                _add(entry.get('title') or entry.get('role'))
    return titles


def _profile_seniority_and_years(profile: dict) -> tuple[int, float | None, str]:
    """Infer profile seniority level (0..5) and experience years from demonstrated profile facts."""
    years = profile.get('experience_years')
    if years is None:
        facts = profile.get('cv_career_facts') or {}
        if isinstance(facts, dict) and isinstance(facts.get('experience_years'), (int, float)):
            years = float(facts['experience_years'])
    else:
        years = float(years)

    explicit_sen = str(profile.get('seniority') or '').strip()
    sen_lv = detect_title_seniority(explicit_sen)
    if sen_lv is None:
        exp_lvs = [
            lv
            for title in _profile_experience_titles(profile)
            if (lv := detect_title_seniority(title)) is not None
        ]
        if exp_lvs:
            sen_lv = max(exp_lvs)
    if sen_lv is None and years is not None:
        if years < 1.0:
            sen_lv = 1
        elif years < 4.5:
            sen_lv = 2
        elif years < 8.5:
            sen_lv = 3
        else:
            sen_lv = 4
    if sen_lv is None:
        sen_lv = 2

    quote_parts = []
    if explicit_sen:
        quote_parts.append(f'seniority={explicit_sen}')
    if years is not None:
        quote_parts.append(f'experience_years={years:g}')
    return sen_lv, years, '; '.join(quote_parts)


# ---------------------------------------------------------------------------
# Discipline detection helpers
# ---------------------------------------------------------------------------

def _detect_disciplines(title: str, body_texts: list[str]) -> tuple[set[str], set[str], dict[str, int]]:
    """Return (title_disciplines, body_disciplines, hit_counts) from title + filtered body text."""
    title_hits: set[str] = set()
    body_hits: set[str] = set()
    counts: dict[str, int] = {}

    joined_body = '\n'.join(body_texts)
    for name, spec in DISCIPLINES.items():
        if title and spec['title'].search(title):
            title_hits.add(name)
            counts[name] = counts.get(name, 0) + 4
        if joined_body:
            t_matches = len(spec['title'].findall(joined_body))
            b_matches = len(spec['body'].findall(joined_body))
            total = t_matches * 2 + b_matches
            if total >= 2 or (name in title_hits and total >= 1):
                body_hits.add(name)
                counts[name] = counts.get(name, 0) + min(6, total)
    return title_hits, body_hits, counts


def _profile_disciplines(profile: dict, sources: tuple) -> tuple[set[str], dict[str, int]]:
    """Detect the profile's DEMONSTRATED professional disciplines (desired roles are preference only)."""
    exp_titles = _profile_experience_titles(profile)
    role_text = ' | '.join(exp_titles)
    skill_text = ' | '.join(str(s) for s in (profile.get('skills') or []) if s)
    edu_parts: list[str] = []
    for edu in (profile.get('education') or []):
        if isinstance(edu, dict):
            edu_parts.append(
                ' '.join(str(edu.get(k) or '') for k in ('degree', 'field', 'major', 'subject', 'title')).strip()
            )
        elif isinstance(edu, str) and edu.strip():
            edu_parts.append(edu.strip())
    edu_text = ' | '.join(p for p in edu_parts if p)
    content_text = '\n'.join(
        part for part in (*(text for _, text in sources if text), edu_text) if part
    )

    found: set[str] = set()
    strength: dict[str, int] = {}
    for name, spec in DISCIPLINES.items():
        score = 0
        role_hit = bool(role_text and spec['title'].search(role_text))
        if role_hit:
            score += 4
        if skill_text and (spec['title'].search(skill_text) or spec['body'].search(skill_text)):
            score += 2
        if content_text:
            t_cnt = len(spec['title'].findall(content_text))
            b_cnt = len(spec['body'].findall(content_text))
            score += min(6, t_cnt * 2 + b_cnt)
        # Specialist disciplines require either an explicit demonstrated experience role
        # or substantial multi-hit evidence so incidental mentions never qualify as specialist.
        min_threshold = 4 if spec['specialist'] else 2
        if role_hit or score >= min_threshold:
            found.add(name)
            strength[name] = score
    return found, strength



def _extract_content_tokens(text: str) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(
        tok
        for tok in _CONTENT_TOK_RE.findall(text.casefold())
        if tok not in _CONTENT_STOPWORDS
    )



def _role_tokens(text: str) -> frozenset[str]:
    clean = _ROLE_GENDER_RE.sub(' ', text)
    return frozenset(
        tok
        for tok in _ROLE_TOK_RE.findall(clean.casefold())
        if tok not in _ROLE_STOP
    )



def _role_group_ids(text: str) -> frozenset[int]:
    if not text:
        return frozenset()
    return frozenset(
        idx
        for idx, pat in enumerate(_ROLE_GROUP_PATTERNS)
        if pat.search(text)
    )
