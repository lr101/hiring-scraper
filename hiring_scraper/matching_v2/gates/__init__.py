"""Deterministic gate evaluator over full posting context and saved profile facts.

Hard fails (eligible=False) are emitted ONLY for explicit, cited incompatibilities:
  (a) Stated CEFR minimum or unambiguous mandatory native / native-level /
      near-native / Muttersprache above the profile's saved level for that language.
  (b) Excluded title term matching the job title.
  (c) Explicit mandatory on-site attendance text vs willing_to_work_onsite=False.
  (d) Explicitly denied work authorisation for the jurisdiction bound to the
      permit phrase (with no sponsorship alternative).

Every hard fail carries ``job_quote``, ``profile_source``, and ``profile_quote``.
Qualitative language wording, metadata-only onsite, sponsorship alternatives,
missing profile fields, and unverified formal prerequisites produce structured
verify flags only — never hard fails.
"""
from __future__ import annotations

from typing import Any

from hiring_scraper.matching_v2.gates.authorization import (
    AUTHORIZATION_CONDITION,
    _evaluate_authorization,
    authorization_jurisdictions,
    has_sponsorship_alternative,
    normalize_jurisdictions,
)
from hiring_scraper.matching_v2.gates.clause_filter import (
    is_language_or_visa_clause,
    strip_language_or_visa_subclauses,
)
from hiring_scraper.matching_v2.gates.common import (
    NON_REQUIREMENT_V2,
    job_requirement_texts,
    mask_parens,
    parse_job_requirement_texts,
    unmask_parens,
)
from hiring_scraper.matching_v2.gates.excluded_title import _evaluate_excluded_terms
from hiring_scraper.matching_v2.gates.language import (
    _evaluate_languages,
    canonical_language,
    normalize_profile_level,
)
from hiring_scraper.matching_v2.gates.onsite import (
    _evaluate_location,
    explicit_onsite_evidence,
)
from hiring_scraper.matching_v2.gates.prerequisites import _inactive_credential_conflicts

__all__ = [
    'AUTHORIZATION_CONDITION',
    'NON_REQUIREMENT_V2',
    'authorization_jurisdictions',
    'canonical_language',
    'evaluate_gates',
    'explicit_onsite_evidence',
    'has_sponsorship_alternative',
    'is_language_or_visa_clause',
    'job_requirement_texts',
    'mask_parens',
    'normalize_jurisdictions',
    'normalize_profile_level',
    'parse_job_requirement_texts',
    'strip_language_or_visa_subclauses',
    'unmask_parens',
]


def evaluate_gates(job: dict, profile: dict, enriched: dict) -> dict[str, Any]:
    """Run the single deterministic gate evaluator over full posting context + profile facts."""
    raw = job.get('raw_metadata') or {}
    parsed_texts = parse_job_requirement_texts(job_requirement_texts(job))

    lang_res = _evaluate_languages(parsed_texts, profile)
    auth_res = _evaluate_authorization(parsed_texts, profile, raw)
    loc_res = _evaluate_location(job, parsed_texts, profile, enriched)
    excl_res = _evaluate_excluded_terms(job, profile)
    credential_fails = _inactive_credential_conflicts(parsed_texts, profile)

    hard_fails = [
        *lang_res['hard_fails'],
        *auth_res['hard_fails'],
        *credential_fails,
        *loc_res['hard_fails'],
        *excl_res['hard_fails'],
    ]
    flags = [
        *lang_res['flags'],
        *auth_res['flags'],
        *loc_res['flags'],
    ]

    return {
        'eligible': bool(not hard_fails),
        'hard_fails': hard_fails,
        'flags': flags,
        'language': lang_res,
        'authorization': auth_res,
        'location': loc_res,
        'excluded_terms': excl_res,
        'gates': {
            'language': {'status': lang_res['status'], 'reason': lang_res['reason']},
            'location': {'status': loc_res['status'], 'reason': loc_res['reason']},
            'eligibility': (
                {'status': 'fail', 'reason': credential_fails[0]['detail']} if credential_fails
                else {'status': auth_res['status'], 'reason': auth_res['reason']}
            ),
            'excluded_terms': {'status': excl_res['status'], 'reason': excl_res['reason']},
        },
        'language_review': lang_res['status'] == 'unknown',
        'authorization_review': auth_res['status'] == 'unknown',
        'metadata_only_onsite': loc_res['metadata_only_onsite'],
        'parsed_texts': parsed_texts,
    }
