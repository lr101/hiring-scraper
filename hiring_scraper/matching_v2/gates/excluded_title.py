"""Excluded title term gate for v2."""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Any

from hiring_scraper.matching import _pattern, plain_text


_CASE_TRANSLATION = str.maketrans({'İ': 'i', 'ı': 'i', 'ſ': 's'})


@lru_cache(maxsize=256)
def _cached_excluded_pattern(term: str) -> re.Pattern[str]:
    return _pattern(term)


def _evaluate_excluded_terms(job: dict, profile: dict) -> dict[str, Any]:
    title = plain_text(job.get('title'))
    title_norm = title.translate(_CASE_TRANSLATION).lower()
    hard_fails: list[dict[str, Any]] = []
    for term in profile.get('excluded_terms') or []:
        term_str = str(term or '').strip()
        if (
            term_str
            and term_str.translate(_CASE_TRANSLATION).lower() in title_norm
            and _cached_excluded_pattern(term_str).search(title)
        ):
            hard_fails.append({
                'kind': 'excluded_term',
                'gate': 'excluded_terms',
                'detail': f'Excluded title term: {term_str}',
                'job_source': 'title',
                'job_quote': title[:240],
                'profile_source': 'profile.excluded_terms',
                'profile_quote': term_str,
            })
    status = 'fail' if hard_fails else 'pass'
    reason = '; '.join(item['detail'] for item in hard_fails)[:500] if hard_fails else 'No excluded title term matched.'
    return {
        'status': status,
        'reason': reason,
        'hard_fails': hard_fails,
        'flags': [],
    }
