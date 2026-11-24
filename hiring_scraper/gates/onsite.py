"""Explicit on-site attendance and residency location gate for v2."""
from __future__ import annotations

import re
from typing import Any

from hiring_scraper.gates.common import _is_other_actor_subject
from hiring_scraper.requirements import (
    is_source_instruction,
    requirement_polarity,
    scoped_sentences,
    structured_text,
)

_RESIDENCY_REQUIREMENT_RE = re.compile(
    r'\b(?:must\s+reside|must\s+live|residency\s+is\s+required|required\s+residence|'
    r'wohnsitz\s+erforderlich|wohnsitz\s+in)\b',
    re.I,
)

_NO_REMOTE_EXP_GUARD = (
    r'(?!\s*(?:[-–]\s*)?(?:work\s+|arbeit[s-]*)?'
    r'(?:experience|background|history|skills?|knowledge|setup|equipment|tools?|'
    r'erfahrung\w*|vorkenntnis\w*|kenntnis\w*|ausstattung|'
    r'(?:is\s+)?(?:required|needed|necessary|erforderlich|notwendig))\b)'
)

_EXPLICIT_NO_REMOTE = re.compile(
    r'\b(?:'
    r'no\s+(?:remote(?:\s+work(?:ing)?)?|home\s*office|work\s+from\s+home)' + _NO_REMOTE_EXP_GUARD + r'|'
    r'(?:does|do)\s+not\s+(?:offer|allow|permit|support)\s+(?:remote(?:\s+work(?:ing)?)?|home\s*office|work\s+from\s+home)|'
    r'(?:kein\w*|ohne)\s+(?:remote(?:[- ]?(?:work|arbeit|möglichkeit))?|home\s*office|mobiles?\s+arbeiten)' + _NO_REMOTE_EXP_GUARD + r'|'
    r'(?:remote(?:\s+work(?:ing)?)?|home\s*office|mobiles?\s+arbeiten)\s+(?:is|ist)?\s*(?:not|nicht)\s+'
    r'(?:possible|available|offered|permitted|an\s+option|möglich|vorgesehen|verfügbar)|'
    r'100\s*%\s*(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|präsenz(?:pflicht)?)|'
    r'präsenzpflicht|anwesenheitspflicht)\b',
    re.I,
)
_ONSITE_LOCATION_TERM = re.compile(
    r'\b(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|präsenz(?:arbeit|tätigkeit)?|'
    r'büroanwesenheit|anwesenheit\s+(?:im\s+büro|vor[- ]?ort|am\s+standort)|'
    r'im\s+büro|in\s+unserem\s+büro|am\s+standort|an\s+unserem\s+(?:standort|hauptsitz|sitz)|'
    r'in\s+the\s+office|in\s+our\s+office|'
    r'at\s+our\s+(?:office|site|plant|facility|warehouse|location|headquarters|hq|premises|branch|campus))\b',
    re.I,
)
_ONSITE_DAYS = (
    r'(?:(?:[2-5]|two|three|four|five|zwei|drei|vier|fünf|fuenf)\s+'
    r'(?:days?|tage)\s*(?:/|\s+(?:per|a|pro|in\s+der)\s+)(?:week|wk|woche))'
)
_ONSITE_INTERVENING_LOC = (
    r'(?:(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|im\s+büro|am\s+standort(?:\s+[\wäöüß-]+)?|'
    r'(?:in|at|am)\s+(?:(?:our|the|unserem|dem)\s+)?[\wäöüß-]+)\s+){0,3}'
)
_ONSITE_MANDATE = re.compile(
    r'\b(?:'
    r'must\s+(?:work|be|attend|report)\b|'
    r'required\s+to\s+(?:work|be|attend|report)\b|'
    r'(?:role|position|job|vacancy|stelle|tätigkeit|arbeit)\s+(?:requires?|demands?|erfordert|verlangt)\b|'
    r'requires?\s+(?:(?:regular|daily|full[- ]?time|100\s*%|physical|in[- ]?person)\s+)?'
    r'(?:on[- ]?site|in[- ]?office|in[- ]?person|office)\s+(?:work|attendance|presence)\b|'
    r'(?:erfordert|verlangt)\s+(?:(?:eine?\s+)?(?:tägliche[rnms]?|regelmäßige[rnms]?|vollständige[rnms]?|durchgehende[rnms]?|100\s*%)\s+)?'
    r'(?:anwesenheit|präsenz|büroanwesenheit|arbeit)\b|'
    r'(?:muss|müssen)\s+[^.;:!?\n]{0,45}\b(?:vor[- ]?ort|im\s+büro|in\s+präsenz|am\s+standort)\b|'
    r'(?:vor[- ]?ort|im\s+büro|in\s+präsenz|am\s+standort)\s+[^.;:!?\n]{0,40}\b(?:arbeiten|tätig\s+sein|anwesend\s+sein)\s+(?:muss|müssen)\b|'
    r'(?:attendance|presence|büroanwesenheit|anwesenheit|präsenz|work|working|arbeit)\s+'
    + _ONSITE_INTERVENING_LOC
    + r'(?:is|ist)?\s*(?:required|mandatory|expected|essential|erforderlich|notwendig|verpflichtend|pflicht|zwingend|vorausgesetzt)|'
    r'(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|präsenz)\s+'
    r'(?:(?:work|attendance|presence|location|role|position|tätigkeit|arbeit|anwesenheit)\s+)?'
    + _ONSITE_INTERVENING_LOC
    + r'(?:is|ist)?\s*(?:required|mandatory|expected|essential|erforderlich|notwendig|verpflichtend|pflicht|zwingend|vorausgesetzt)|'
    r'(?:full[- ]?time|fully|strictly|exclusively|purely|solely|ausschließlich\w*|vollständig\w*|komplett|rein|vollzeit|täglich\w*|daily)\s+'
    r'(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|im\s+büro|in\s+präsenz|präsenz|anwesenheit|at\s+our\s+(?:office|site|plant|warehouse|location|facility))|'
    r'(?:this\s+(?:is\s+an?|(?:role|position|job|vacancy)\s+is)|(?:role|position|job|vacancy)\s+is)\s+'
    r'(?:a\s+)?(?:(?:full[- ]?time|100\s*%)\s+)?(?:on[- ]?site|in[- ]?office|in[- ]?person)\b|'
    r'(?:stelle|position|tätigkeit|arbeit|ausschreibung)\s+(?:ist|erfolgt|findet)\s+'
    r'(?:(?:eine|vollständig|ausschließlich|komplett|rein|vollzeit|überwiegend|100\s*%)\s+){0,2}(?:vor[- ]?ort|im\s+büro|in\s+präsenz|am\s+standort)|'
    r'(?:monday\s*(?:to|through|-|–)\s*friday|mon\s*[-–]\s*fri|'
    r'montag\s*(?:bis|-|–)\s*freitag|mo\.?\s*[-–]\s*fr\.?|'
    + _ONSITE_DAYS
    + r'))\b',
    re.I,
)
_ONSITE_NEGATIVE = re.compile(
    r'\b(?:'
    r'no\s+(?:prior\s+)?(?:remote|home\s*office|on[- ]?site|in[- ]?office)(?:\s+work(?:ing)?)?\s+'
    r'(?:experience|background|history|skills?|knowledge|setup|equipment)\b|'
    r'kein\w*\s+(?:remote|home\s*office|vor[- ]?ort)[- ]?(?:erfahrung\w*|vorkenntnis\w*|kenntnis\w*|ausstattung)\b|'
    r'(?:on[- ]?site|in[- ]?office|vor[- ]?ort)[- ]+(?:work[- ]+|arbeits?)?'
    r'(?:experience|background|history|skills?|knowledge|erfahrung\w*|vorkenntnis\w*)|'
    r'no\s+(?:on[- ]?site|in[- ]?office|office)\s+(?:attendance|presence|work)\s+(?:is\s+)?required|'
    r'kein\w*\s+(?:präsenz|anwesenheit)\s+(?:vor[- ]?ort\s+)?(?:erforderlich|notwendig)|'
    r'(?:fully|vollständig|100\s*%)\s+remote|remote[- ]first|'
    r'(?:optional|occasional|gelegentlich\w*)\s+(?:on[- ]?site|office|in[- ]?office|vor[- ]?ort)|'
    r'(?:travel|reisebereitschaft|kundenbesuch\w*|vor[- ]?ort[- ]?termin\w*)\b[^.!?\n]{0,35}\b(?:client|customer|kunden)|'
    r'(?:on[- ]?site|vor[- ]?ort)\s+(?:at|with|beim?|bei\s+den)\s+(?:client|customer|kunden))\b',
    re.I,
)


def _explicit_onsite_from_sentences(sentences: Sequence[tuple[str, str]]) -> str | None:
    for sentence, scope in sentences:
        if (
            is_source_instruction(sentence)
            or _is_other_actor_subject(sentence)
            or _ONSITE_NEGATIVE.search(sentence)
        ):
            continue
        if (hit := _EXPLICIT_NO_REMOTE.search(sentence)):
            if requirement_polarity(sentence, hit.start(), hit.end()) == 'required':
                return sentence[:240]
        if scope == 'optional':
            continue
        if (loc := _ONSITE_LOCATION_TERM.search(sentence)) and _ONSITE_MANDATE.search(sentence):
            if requirement_polarity(sentence, loc.start(), loc.end()) == 'required':
                return sentence[:240]
    return None


def explicit_onsite_evidence(text: str) -> str | None:
    """Return explicit mandatory on-site attendance evidence from posting text, if any."""
    if not text or not isinstance(text, str):
        return None
    return _explicit_onsite_from_sentences(list(scoped_sentences(structured_text(text))))



def _evaluate_location(
    job: dict,
    parsed_texts: tuple[tuple[str, bool, tuple[tuple[str, str], ...]], ...],
    profile: dict,
    enriched: dict,
) -> dict[str, Any]:
    preferences = (
        profile.get('application_preferences')
        or (profile.get('application_details') or {}).get('preferences')
        or {}
    )
    refused_onsite = preferences.get('willing_to_work_onsite') is False
    work_style = job.get('work_arrangement') or ('remote' if job.get('is_remote') else enriched.get('work_style'))

    onsite_quote: str | None = None
    onsite_source = 'description'
    for source, _, sentences in parsed_texts:
        found = _explicit_onsite_from_sentences(sentences)
        if found:
            onsite_quote = found
            onsite_source = source
            break

    hard_fails: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    metadata_only_onsite = False

    if refused_onsite and onsite_quote:
        hard_fails.append({
            'kind': 'declined_onsite',
            'gate': 'location',
            'detail': f'Saved preferences decline explicit on-site attendance: {onsite_quote}',
            'job_source': onsite_source,
            'job_quote': onsite_quote,
            'profile_source': 'profile.application_preferences.willing_to_work_onsite',
            'profile_quote': 'willing_to_work_onsite=False',
        })
    elif refused_onsite and work_style in {'onsite', 'hybrid'}:
        metadata_only_onsite = True
        flags.append({
            'kind': 'metadata_onsite',
            'gate': 'location',
            'detail': (
                f'Metadata lists {work_style} without explicit on-site attendance text; '
                f'verify attendance expectations ({job.get("location_text") or "location unspecified"}).'
            ),
            'job_source': 'metadata',
            'job_quote': f'work_arrangement={work_style}',
            'profile_source': 'profile.application_preferences.willing_to_work_onsite',
            'profile_quote': 'willing_to_work_onsite=False',
        })
    elif onsite_quote and preferences.get('willing_to_work_onsite') is not True:
        flags.append({
            'kind': 'onsite_verify',
            'gate': 'location',
            'detail': f'Verify on-site attendance expectations: {onsite_quote}',
            'job_source': onsite_source,
            'job_quote': onsite_quote,
            'profile_source': 'profile.application_preferences.willing_to_work_onsite',
            'profile_quote': str(preferences.get('willing_to_work_onsite')),
        })

    # Residence restriction check (flag only, never hard fail).
    found_residence = False
    for source, _, sentences in parsed_texts:
        if found_residence:
            break
        for sentence, scope in sentences:
            if scope in {'benefits', 'optional'} or is_source_instruction(sentence):
                continue
            res = _RESIDENCY_REQUIREMENT_RE.search(sentence)
            if res and requirement_polarity(sentence, res.start(), res.end()) == 'required':
                flags.append({
                    'kind': 'residence',
                    'gate': 'location',
                    'detail': f'Verify required residence/payroll jurisdiction: {sentence[:220]}',
                    'job_source': source,
                    'job_quote': sentence[:240],
                    'profile_source': '',
                    'profile_quote': '',
                })
                found_residence = True
                break

    status = 'fail' if hard_fails else 'unknown' if flags else 'pass'
    reason = (
        hard_fails[0]['detail']
        if hard_fails
        else '; '.join(f['detail'] for f in flags)[:500]
        if flags
        else 'No mandatory workplace condition conflicted with saved preferences.'
    )
    return {
        'status': status,
        'reason': reason,
        'hard_fails': hard_fails,
        'flags': flags,
        'onsite_evidence': onsite_quote,
        'metadata_only_onsite': metadata_only_onsite,
    }
