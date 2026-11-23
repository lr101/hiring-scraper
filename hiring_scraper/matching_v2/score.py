"""Additive v2 job scoring, monotone calibration, and consumer-compatible output."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from hiring_scraper.matching import job_city_vote_results, plain_text
from hiring_scraper.matching_v2.calibrate import calibration_payload
from hiring_scraper.matching_v2.features import extract_features, precompute_profile
from hiring_scraper.matching_v2.gates import evaluate_gates
from hiring_scraper.matching_v2.weights import (
    CALIBRATION_KNOTS,
    CALIBRATION_TAIL_SLOPE,
    FEATURE_WEIGHTS,
    TIER_POSSIBLE_MIN,
    TIER_RECOMMENDED_MIN,
    VERSION,
)

_LIGHT_REMOTE_RE = re.compile(r'\b(?:100\s*%\s*remote|fully\s+remote|remote[- ]first|full[- ]remote|vollständig\s+remote)\b', re.I)
_LIGHT_HYBRID_RE = re.compile(r'\b(?:hybrid|mobiles?\s+arbeiten|home[- ]?office\s+(?:möglich|anteil|tage))\b', re.I)
_LIGHT_ONSITE_RE = re.compile(r'\b(?:on[- ]?site|in[- ]?office|präsenzpflicht|vor\s+ort)\b', re.I)
_LIGHT_EMP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('internship', re.compile(r'\b(?:intern|internship|praktikant\w*|praktikum)\b', re.I)),
    ('working-student', re.compile(r'\b(?:working\s+student|werkstudent\w*|studentische\s+hilfskraft)\b', re.I)),
    ('part-time', re.compile(r'\b(?:part[- ]?time|teilzeit)\b', re.I)),
    ('contract', re.compile(r'\b(?:freelance|contractor|contract\s+role|befristet)\b', re.I)),
    ('full-time', re.compile(r'\b(?:full[- ]?time|vollzeit|unbefristet|permanent)\b', re.I)),
)


def _lightweight_enrichment(job: dict) -> dict[str, Any]:
    """Fast fallback for work_style and employment_type when precomputed enrichment is not passed."""
    work_style = str(job.get('work_arrangement') or '').strip().lower()
    if not work_style:
        if job.get('is_remote') is True:
            work_style = 'remote'
        else:
            head = f"{plain_text(job.get('title'))}\n{plain_text(job.get('location_text'))}\n{str(job.get('description') or '')[:1500]}"
            if _LIGHT_REMOTE_RE.search(head):
                work_style = 'remote'
            elif _LIGHT_HYBRID_RE.search(head):
                work_style = 'hybrid'
            elif _LIGHT_ONSITE_RE.search(head):
                work_style = 'onsite'

    emp_type = str(job.get('employment_type') or '').strip().lower()
    if not emp_type:
        head = f"{plain_text(job.get('title'))}\n{str(job.get('description') or '')[:800]}"
        for label, pat in _LIGHT_EMP_PATTERNS:
            if pat.search(head):
                emp_type = label
                break

    return {
        'work_style': work_style or None,
        'employment_type': emp_type or None,
    }


def _compute_local_context(
    job: dict,
    profile: dict,
    gate_result: dict[str, Any],
) -> dict[str, Any]:
    """Compute v1-compatible local_context (election vote results + optional AfD weight adjustment)."""
    fully_remote = (
        not gate_result['location'].get('onsite_evidence')
        and (job.get('is_remote') is True or job.get('work_arrangement') == 'remote')
    )
    vote_results = [] if fully_remote else job_city_vote_results(job)
    scoring_vote_results = [row for row in vote_results if row.get('postal_ballots_included')]
    defaults = profile.get('matching_defaults') or {}
    try:
        location_weight = max(0, min(20, int(defaults.get('low_afd_vote_share_weight', 0))))
    except (TypeError, ValueError):
        location_weight = 0
    average_vote_share = (
        round(
            sum(row['vote_share_percent'] for row in scoring_vote_results) / len(scoring_vote_results),
            2,
        )
        if scoring_vote_results
        else None
    )
    location_adjustment = (
        int(
            max(
                -location_weight,
                min(location_weight, round((25 - average_vote_share) / 25 * location_weight)),
            )
        )
        if average_vote_share is not None and location_weight
        else 0
    )
    return {
        'city_election_results': vote_results,
        'average_afd_vote_share_percent': average_vote_share,
        'score_adjustment': int(location_adjustment),
        'weight': int(location_weight),
    }


def compute_ledger(
    feature_bundle: dict[str, Any],
    *,
    weights: Mapping[str, float] = FEATURE_WEIGHTS,
    local_adjustment: int = 0,
    local_detail: str = '',
) -> tuple[float, list[dict[str, Any]]]:
    """Compute additive raw_score and exact explanation ledger from extracted features.

    Invariant: ``round(sum(entry['contribution'] for entry in explanation), 4) == raw_score``.
    """
    features: dict[str, float] = feature_bundle['features']
    citations: dict[str, dict[str, str]] = feature_bundle['feature_citations']
    penalties: list[dict[str, Any]] = feature_bundle['penalties']

    explanation: list[dict[str, Any]] = []
    for name, weight in weights.items():
        val = float(features.get(name, 0.0))
        contrib = round(float(weight) * val, 4)
        cite = citations.get(name, {})
        explanation.append({
            'signal': name,
            'contribution': contrib,
            'detail': cite.get('detail', f'{name}={val:.3f} (weight {weight:g})'),
            'job_quote': cite.get('job_quote', ''),
            'profile_source': cite.get('profile_source', ''),
            'profile_quote': cite.get('profile_quote', ''),
        })

    for pen in penalties:
        amt = round(float(pen['amount']), 4)
        if amt <= 0:
            continue
        explanation.append({
            'signal': pen['signal'],
            'contribution': round(-amt, 4),
            'detail': pen.get('detail', ''),
            'job_quote': pen.get('job_quote', ''),
            'profile_source': pen.get('profile_source', ''),
            'profile_quote': pen.get('profile_quote', ''),
        })

    if local_adjustment:
        explanation.append({
            'signal': 'local_context:afd_vote_share',
            'contribution': round(float(local_adjustment), 4),
            'detail': local_detail or f'Local context adjustment ({local_adjustment:+d})',
            'job_quote': '',
            'profile_source': 'profile.matching_defaults.low_afd_vote_share_weight',
            'profile_quote': '',
        })

    raw_score = round(sum(entry['contribution'] for entry in explanation), 4)
    return raw_score, explanation


def assemble_result(
    job: dict,
    profile: dict,
    enriched: dict,
    gate_result: dict[str, Any],
    feature_bundle: dict[str, Any],
    *,
    semantic: dict | None = None,
    weights: Mapping[str, float] = FEATURE_WEIGHTS,
    knots: Sequence[tuple[float, float]] = CALIBRATION_KNOTS,
    tail_slope: float = CALIBRATION_TAIL_SLOPE,
    version: str = VERSION,
) -> dict[str, Any]:
    """Assemble the complete v2 match dict from gates and extracted features."""
    local_context = _compute_local_context(job, profile, gate_result)
    local_adj = int(local_context['score_adjustment'])
    local_detail = (
        f"Local AfD vote share {local_context['average_afd_vote_share_percent']}% "
        f"(weight {local_context['weight']}, adjustment {local_adj:+d})"
        if local_adj
        else ''
    )

    raw_score, explanation = compute_ledger(
        feature_bundle,
        weights=weights,
        local_adjustment=local_adj,
        local_detail=local_detail,
    )
    calibration = calibration_payload(
        raw_score,
        knots=knots,
        tail_slope=tail_slope,
        version=version,
    )
    score = int(calibration['calibrated'])

    eligible = bool(gate_result['eligible'])
    conflicts = [item['detail'] for item in gate_result['hard_fails']]
    experience_review = [
        {
            'kind': row['kind'], 'gate': 'qualification', 'status': 'unknown',
            'detail': f"{row['explanation']} ({row['job_quote']})",
            'job_source': row['job_source'], 'job_quote': row['job_quote'],
            'profile_source': row['evidence_source'], 'profile_quote': row['evidence_quote'],
        }
        for row in feature_bundle['requirement_observations']
        if row['kind'] == 'experience' and row['status'] == 'unknown'
    ]
    formal_conditions = feature_bundle['formal_prereq_conditions']
    qualification_review = [
        *experience_review,
        *(row for row in formal_conditions if row['status'] == 'unknown'),
    ]
    unknowns = [
        *(row['detail'] for row in qualification_review),
        *(item['detail'] for item in gate_result['flags']),
        *(feature_bundle.get('extra_unknowns') or []),
    ]
    soft_conflicts = list(feature_bundle['soft_conflicts'])

    # Build human-readable reasons from the top positive ledger contributors.
    positive_entries = sorted(
        (e for e in explanation if e['contribution'] > 0.5),
        key=lambda e: -e['contribution'],
    )
    reasons = [
        f'{e["detail"]} (+{e["contribution"]:.1f})'
        for e in positive_entries[:5]
    ]
    if not reasons and gate_result['language']['met']:
        reasons.append(gate_result['language']['met'][0])

    features = feature_bundle['features']
    task_fit = int(round(max(0.0, min(1.0, features['duty_coverage'])) * 100))
    qualification_fit = int(round(max(0.0, min(1.0, features['requirement_coverage'])) * 100))

    language_review = bool(gate_result['language_review'])
    mandatory_review = bool(gate_result['authorization_review'] or qualification_review)
    has_hard_review_flags = bool(
        language_review
        or mandatory_review
        or gate_result['location']['status'] == 'unknown'
    )

    if not eligible or score < TIER_POSSIBLE_MIN:
        fit_tier = 'unlikely'
    elif score >= TIER_RECOMMENDED_MIN and not has_hard_review_flags:
        fit_tier = 'recommended'
    else:
        fit_tier = 'possible'

    if not eligible:
        label = 'Preference conflict'
    elif fit_tier == 'recommended':
        label = 'Strong overlap'
    elif score >= TIER_RECOMMENDED_MIN:
        label = 'Strong overlap (verify requirements)'
    elif score >= TIER_POSSIBLE_MIN:
        label = 'Some overlap'
    else:
        label = 'Little evidence of a match'

    duty_evidence = feature_bundle['duty_evidence']
    has_duties = bool(duty_evidence.get('duties'))

    # Structured prerequisite conditions (with both v1 'reason' and v2 'detail' + citations).
    prerequisite_conditions: list[dict[str, Any]] = []
    for f in gate_result['hard_fails']:
        text = str(f.get('detail') or '')
        prerequisite_conditions.append({
            'kind': f.get('kind', 'flag'),
            'gate': f.get('gate', 'eligibility'),
            'status': 'fail',
            'reason': text,
            'detail': text,
            'job_source': f.get('job_source', 'description'),
            'job_quote': f.get('job_quote', ''),
            'profile_source': f.get('profile_source', ''),
            'profile_quote': f.get('profile_quote', ''),
        })
    for f in gate_result['flags']:
        text = str(f.get('detail') or '')
        prerequisite_conditions.append({
            'kind': f.get('kind', 'flag'),
            'gate': f.get('gate', 'eligibility'),
            'status': 'unknown',
            'reason': text,
            'detail': text,
            'job_source': f.get('job_source', 'description'),
            'job_quote': f.get('job_quote', ''),
            'profile_source': f.get('profile_source', ''),
            'profile_quote': f.get('profile_quote', ''),
        })
    for met_item in (
        *(gate_result['authorization'].get('met_details') or []),
        *(gate_result['language'].get('met_details') or []),
    ):
        text = str(met_item.get('detail') or '')
        prerequisite_conditions.append({
            'kind': met_item.get('kind', 'eligibility'),
            'gate': met_item.get('gate', 'eligibility'),
            'status': 'pass',
            'reason': text,
            'detail': text,
            'job_source': met_item.get('job_source', 'description'),
            'job_quote': met_item.get('job_quote', ''),
            'profile_source': met_item.get('profile_source', ''),
            'profile_quote': met_item.get('profile_quote', ''),
        })
    prerequisite_conditions.extend(formal_conditions)

    prerequisite_conditions.extend({**row, 'reason': row['detail']} for row in experience_review)

    # Requirement observations (features.py qualification/experience/skill observations + gate items).
    requirement_observations: list[dict[str, Any]] = list(
        feature_bundle.get('requirement_observations') or []
    )
    for item in gate_result['hard_fails']:
        requirement_observations.append({
            'kind': item.get('kind', 'prerequisite'),
            'requirement': str(item.get('job_quote') or item.get('detail') or '')[:200],
            'status': 'conflict',
            'job_source': item.get('job_source', 'description'),
            'job_quote': str(item.get('job_quote') or '')[:240],
            'evidence_source': str(item.get('profile_source') or ''),
            'evidence_quote': str(item.get('profile_quote') or ''),
            'evidence_context': 'declared' if item.get('profile_source') else 'unspecified',
            'explanation': str(item.get('detail') or ''),
        })
    for item in gate_result['flags']:
        requirement_observations.append({
            'kind': item.get('kind', 'prerequisite'),
            'requirement': str(item.get('job_quote') or item.get('detail') or '')[:200],
            'status': 'unknown',
            'job_source': item.get('job_source', 'description'),
            'job_quote': str(item.get('job_quote') or '')[:240],
            'evidence_source': str(item.get('profile_source') or ''),
            'evidence_quote': str(item.get('profile_quote') or ''),
            'evidence_context': 'declared' if item.get('profile_source') else 'unspecified',
            'explanation': str(item.get('detail') or ''),
        })
    for item in (
        *(gate_result['authorization'].get('met_details') or []),
        *(gate_result['language'].get('met_details') or []),
    ):
        requirement_observations.append({
            'kind': item.get('kind', 'prerequisite'),
            'requirement': str(item.get('job_quote') or item.get('detail') or '')[:200],
            'status': 'supported',
            'job_source': item.get('job_source', 'description'),
            'job_quote': str(item.get('job_quote') or '')[:240],
            'evidence_source': str(item.get('profile_source') or ''),
            'evidence_quote': str(item.get('profile_quote') or ''),
            'evidence_context': 'declared' if item.get('profile_source') else 'unspecified',
            'explanation': str(item.get('detail') or ''),
        })

    sem_out = (
        semantic
        if isinstance(semantic, dict)
        and (float(semantic.get('score') or 0) > 0 or float(semantic.get('similarity') or 0) > 0)
        else None
    )

    skill_cov = float(features['skill_overlap'])
    content_match = {
        'score': int(round(max(0.0, min(1.0, skill_cov)) * 100)),
        'coverage': round(skill_cov, 4),
        'matches': list(feature_bundle.get('content_match_matches') or []),
        'job_disciplines': feature_bundle['job_disciplines'],
        'profile_disciplines': feature_bundle['profile_disciplines'],
        'uncovered_specialist': feature_bundle['uncovered_specialist'],
    }

    return {
        'score': score,
        'eligible': eligible,
        'uncertain': bool(unknowns or soft_conflicts),
        'fit_tier': fit_tier,
        'label': label,
        'reasons': reasons,
        'unknowns': unknowns,
        'conflicts': conflicts,
        'soft_conflicts': soft_conflicts,
        'requirement_gaps': feature_bundle['requirement_gaps'],
        'matched_skills': feature_bundle['matched_skills'],
        'missing_skills': feature_bundle['missing_skills'],
        'gates': gate_result['gates'],
        'task_fit': task_fit,
        'qualification_fit': qualification_fit,
        'mandatory_review': mandatory_review,
        'language_review': language_review,
        'duty_evidence': duty_evidence,
        'semantic_match': sem_out,
        'static_rank_basis': 'duties' if has_duties else 'discovery',
        # Compatibility fields expected by app consumers.
        'requirement_observations': requirement_observations,
        'prerequisite_conditions': prerequisite_conditions,
        'profile_skill_context': feature_bundle['profile_skill_context'],
        'custom_skill_evidence': feature_bundle['custom_skill_evidence'],
        'content_match': content_match,
        'local_context': local_context,
        'score_source': 'v2',
        'version': version,
        # v2 structured outputs.
        'explanation': explanation,
        'calibration': calibration,
    }


def match_job_v2(
    job: dict,
    profile: dict,
    enrichment: dict | None = None,
    *,
    semantic: dict | None = None,
) -> dict[str, Any]:
    """Score a job against a profile using the v2 gates + additive features + monotone calibration."""
    enriched = enrichment if isinstance(enrichment, dict) else _lightweight_enrichment(job)
    profile_cache = precompute_profile(profile)
    gate_result = evaluate_gates(job, profile, enriched)
    feature_bundle = extract_features(
        job,
        profile,
        enriched,
        gate_result,
        semantic=semantic,
        profile_cache=profile_cache,
    )
    return assemble_result(
        job,
        profile,
        enriched,
        gate_result,
        feature_bundle,
        semantic=semantic,
    )
