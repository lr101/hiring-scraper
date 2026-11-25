"""Inactive credential and formal prerequisite conflict checks for v2."""
from __future__ import annotations

import re

from hiring_scraper.matching import (
    REQUIREMENT,
    _LICENSE_CONDITION,
    _credential_evidence,
)
from hiring_scraper.gates.authorization import _condition_waived
from hiring_scraper.requirements import is_source_instruction, requirement_polarity

_CREDENTIAL_ALT_OR_RENEW_RE = re.compile(r'\b(?:or|oder|renew|verlänger\w*)\b', re.I)

_INACTIVE_CREDENTIAL = re.compile(
    r'^(?:expired|revoked|inactive|lapsed|abgelaufen|widerrufen)\s+|'
    r'\s+\(?(?:expired|revoked|inactive|lapsed|abgelaufen|widerrufen)\)?$',
    re.I,
)


def _inactive_credential_conflicts(parsed_texts, profile):
    certifications = [str(value).strip() for value in profile.get('certifications') or []]
    unavailable = [
        (index, credential, _INACTIVE_CREDENTIAL.sub(' current ', credential).strip())
        for index, credential in enumerate(certifications)
        if _INACTIVE_CREDENTIAL.search(credential)
    ]
    if not unavailable:
        return []
    failures = []
    seen = set()
    for source, _, sentences in parsed_texts:
        for sentence, scope in sentences:
            hit = _LICENSE_CONDITION.search(sentence)
            if not hit or scope in {'duties', 'optional', 'benefits'} or is_source_instruction(sentence):
                continue
            if scope != 'requirements' and not REQUIREMENT.search(sentence):
                continue
            if _CREDENTIAL_ALT_OR_RENEW_RE.search(sentence):
                continue
            if requirement_polarity(sentence, hit.start(), hit.end()) != 'required' or _condition_waived(sentence, hit):
                continue
            if hit.group().casefold() in {'active license', 'active licence', 'current license', 'current licence'}:
                continue
            if _credential_evidence(sentence, hit, certifications):
                continue
            for index, quote, current_form in unavailable:
                key = (sentence.casefold(), quote.casefold())
                if key in seen or not _credential_evidence(sentence, hit, [current_form]):
                    continue
                seen.add(key)
                failures.append({
                    'kind': 'license', 'gate': 'eligibility',
                    'detail': f'Saved credential is inactive: {quote}',
                    'job_source': source, 'job_quote': sentence[:260],
                    'profile_source': f'profile.certifications[{index}]', 'profile_quote': quote,
                })
                break
    return failures
