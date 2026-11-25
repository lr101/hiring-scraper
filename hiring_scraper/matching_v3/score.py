"""v3 scoring: eligibility gates plus reranker-checked duty and requirement evidence.

The score is an exact additive ledger over five components (duties, mandatory and
preferred requirements, role and occupational field), each in [0, 1], followed by
bounded soft penalties and a monotone calibration. Every component cites the job
text and the CV line that supports it. Without a current job analysis and pair
evidence the result is ``pending`` and Journey hides the job.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from hiring_scraper.matching import job_city_vote_results, plain_text
from hiring_scraper.gates import evaluate_gates
from hiring_scraper.matching_v3 import weights as W
from hiring_scraper.matching_v3.evidence import GATED_KINDS, is_soft_requirement

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
_FULL_TIME = {'full-time', 'fulltime', 'permanent', 'vollzeit'}


def lightweight_enrichment(job: dict) -> dict[str, Any]:
    """Work style and employment type when no stored enrichment is passed."""
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
        for label, pattern in _LIGHT_EMP_PATTERNS:
            if pattern.search(head):
                emp_type = label
                break
    return {'work_style': work_style or None, 'employment_type': emp_type or None}


def local_context(job: dict, profile: dict, gate_result: dict[str, Any]) -> dict[str, Any]:
    """Election context near the workplace and the optional user-set adjustment."""
    fully_remote = (not gate_result['location'].get('onsite_evidence')
                    and (job.get('is_remote') is True or job.get('work_arrangement') == 'remote'))
    vote_results = [] if fully_remote else job_city_vote_results(job)
    scoring = [row for row in vote_results if row.get('postal_ballots_included')]
    defaults = profile.get('matching_defaults') or {}
    try:
        weight = max(0, min(20, int(defaults.get('low_afd_vote_share_weight', 0))))
    except (TypeError, ValueError):
        weight = 0
    average = (round(sum(row['vote_share_percent'] for row in scoring) / len(scoring), 2)
               if scoring else None)
    adjustment = (int(max(-weight, min(weight, round((25 - average) / 25 * weight))))
                  if average is not None and weight else 0)
    return {'city_election_results': vote_results, 'average_afd_vote_share_percent': average,
            'score_adjustment': adjustment, 'weight': weight}


def support(probability: float | None) -> float:
    """Map a reranker probability to [0, 1], linear in log-odds."""
    if probability is None:
        return 0.0
    p = min(max(float(probability), 1e-6), 1 - 1e-6)
    low, high = math.log(W.SUPPORT_LOW / (1 - W.SUPPORT_LOW)), math.log(W.SUPPORT_HIGH / (1 - W.SUPPORT_HIGH))
    return max(0.0, min(1.0, (math.log(p / (1 - p)) - low) / (high - low)))


def _shrunk_mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return (sum(values) + W.PRIOR_WEIGHT * W.PRIOR) / (len(values) + W.PRIOR_WEIGHT)


def calibrate(raw: float, knots: Sequence[tuple[float, float]] = W.CALIBRATION_KNOTS,
              tail_slope: float = W.CALIBRATION_TAIL_SLOPE) -> float:
    x0, y0 = knots[0]
    xn, yn = knots[-1]
    if raw <= x0:
        return max(0.0, min(100.0, y0 + (raw - x0) * tail_slope))
    if raw >= xn:
        return max(0.0, min(100.0, yn + (raw - xn) * tail_slope))
    for (lx, ly), (rx, ry) in zip(knots, knots[1:]):
        if lx <= raw <= rx:
            return max(0.0, min(100.0, ly + (raw - lx) / (rx - lx) * (ry - ly)))
    return max(0.0, min(100.0, yn))


def _short(text: str, limit: int = 60) -> str:
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _status(value: float) -> str:
    return 'direct' if value >= W.DIRECT_SUPPORT else 'transferable' if value >= W.PARTIAL_SUPPORT else 'unverified'


def _items(analysis: dict, evidence: dict) -> tuple[list[dict], list[dict]]:
    """Join extracted duties/requirements with their pair evidence by ref."""
    by_ref = {row['ref']: row for row in evidence.get('items') or [] if isinstance(row, dict)}
    duties, requirements = [], []
    for index, duty in enumerate(analysis.get('duties') or []):
        row = by_ref.get(f'd{index}')
        if row is None:
            continue
        duties.append({**duty, 'support': support(row.get('support')), 'probability': row.get('support'),
                       'evidence_quote': row.get('quote') or '', 'evidence_source': row.get('source') or ''})
    for index, requirement in enumerate(analysis.get('requirements') or []):
        row = by_ref.get(f'r{index}')
        gated = requirement.get('kind') in GATED_KINDS
        if row is None and not gated:
            continue
        requirements.append({
            **requirement,
            'gated': gated,
            'support': support(row.get('support')) if row else None,
            'probability': row.get('support') if row else None,
            'evidence_quote': (row or {}).get('quote') or '',
            'evidence_source': (row or {}).get('source') or '',
        })
    return duties, requirements


def _penalties(job: dict, profile: dict, enriched: dict, gate_result: dict,
               formal_gaps: list[dict]) -> tuple[list[dict], list[str]]:
    penalties: list[dict[str, Any]] = []
    soft_conflicts: list[str] = []
    pref_styles = {str(style).casefold() for style in profile.get('work_styles') or []}
    job_style = str(job.get('work_arrangement') or ('remote' if job.get('is_remote') else enriched.get('work_style') or '')
                    ).strip().casefold()
    if pref_styles and job_style and job_style not in pref_styles and gate_result['location']['status'] != 'fail':
        amount = W.SOFT_PENALTIES['work_style_mismatch'] * (0.5 if job_style == 'hybrid' else 1.0)
        detail = f'Work arrangement ({job_style}) differs from preferred {", ".join(sorted(pref_styles))}'
        soft_conflicts.append(detail)
        penalties.append({'signal': 'penalty:work_style', 'amount': amount, 'detail': detail,
                          'job_quote': f'work_arrangement={job_style}', 'profile_source': 'profile.work_styles',
                          'profile_quote': ', '.join(sorted(pref_styles))})
    pref_emp = {str(kind).casefold().replace('_', '-').replace(' ', '-') for kind in profile.get('employment_types') or []}
    job_emp = str(job.get('employment_type') or enriched.get('employment_type') or '').strip().casefold()
    job_emp = job_emp.replace('_', '-').replace(' ', '-')
    if pref_emp and job_emp and job_emp not in pref_emp and not (job_emp in _FULL_TIME and pref_emp & _FULL_TIME):
        detail = f'Employment type ({job_emp}) differs from preferred {", ".join(sorted(pref_emp))}'
        soft_conflicts.append(detail)
        penalties.append({'signal': 'penalty:employment_type', 'amount': W.SOFT_PENALTIES['employment_type_mismatch'],
                          'detail': detail, 'job_quote': f'employment_type={job_emp}',
                          'profile_source': 'profile.employment_types', 'profile_quote': ', '.join(sorted(pref_emp))})
    if formal_gaps:
        amount = min(W.SOFT_PENALTIES['formal_prerequisite_max'], 3.0 * len(formal_gaps))
        detail = f'Unverified formal prerequisite: {formal_gaps[0]["text"][:140]}'
        soft_conflicts.append(detail)
        penalties.append({'signal': 'penalty:formal_prerequisite', 'amount': amount, 'detail': detail,
                          'job_quote': formal_gaps[0]['text'][:240], 'profile_source': 'profile.education',
                          'profile_quote': ''})
    lang_visa_flags = [flag for flag in gate_result['flags'] if flag.get('gate') in {'language', 'eligibility'}]
    if lang_visa_flags:
        cap = min(5.0, W.SOFT_PENALTIES['language_visa_flag_max'])
        first = lang_visa_flags[0]
        penalties.append({
            'signal': 'penalty:language_visa_flag',
            'amount': cap if len(lang_visa_flags) > 1 else 0.75 * cap,
            'detail': f'Language/work-authorization verification flag ({len(lang_visa_flags)}): {first["detail"][:140]}',
            'job_quote': first.get('job_quote', ''), 'profile_source': first.get('profile_source', ''),
            'profile_quote': first.get('profile_quote', ''),
        })
    total = sum(row['amount'] for row in penalties)
    if total > W.SOFT_PENALTIES['total_soft_penalty_cap']:
        scale = W.SOFT_PENALTIES['total_soft_penalty_cap'] / total
        for row in penalties:
            row['amount'] *= scale
    for row in penalties:
        row['amount'] = round(row['amount'], 2)
    return penalties, soft_conflicts


def components(analysis: dict, evidence: dict) -> dict[str, Any]:
    """Component values in [0, 1] (None when the posting gives no basis) and joined items."""
    duties, requirements = _items(analysis, evidence)
    scored_req = [row for row in requirements if not row['gated']]
    must = [row['support'] for row in scored_req if row.get('need') != 'nice']
    nice = [row['support'] for row in scored_req if row.get('need') == 'nice']
    role = evidence.get('role') or None
    domain = evidence.get('domain') or None
    return {
        'values': {
            'duties': _shrunk_mean([row['support'] for row in duties]),
            'must': _shrunk_mean(must),
            'nice': _shrunk_mean(nice),
            'role': support(role['support']) if role else None,
            'domain': support(domain['support']) if domain else None,
        },
        'duties': duties,
        'requirements': requirements,
        'role': role,
        'domain': domain,
    }


def raw_score(values: Mapping[str, float | None], weights: Mapping[str, float] = W.COMPONENT_WEIGHTS
              ) -> tuple[float, dict[str, float]]:
    present = {name: weight for name, weight in weights.items() if values.get(name) is not None}
    total = sum(present.values())
    if not total:
        return 0.0, {}
    contributions = {name: round(100.0 * weight / total * float(values[name]), 2) for name, weight in present.items()}
    return round(sum(contributions.values()), 2), contributions


def _gate_conditions(gate_result: dict) -> tuple[list[dict], list[dict]]:
    """Cited gate outcomes as prerequisite conditions and conflict observations."""
    conditions: list[dict[str, Any]] = []
    for status, rows in (('fail', gate_result['hard_fails']), ('unknown', gate_result['flags']),
                         ('pass', [*(gate_result['authorization'].get('met_details') or []),
                                   *(gate_result['language'].get('met_details') or [])])):
        for row in rows:
            text = str(row.get('detail') or '')
            conditions.append({
                'kind': row.get('kind', 'flag'), 'gate': row.get('gate', 'eligibility'), 'status': status,
                'reason': text, 'detail': text, 'job_source': row.get('job_source', 'description'),
                'job_quote': row.get('job_quote', ''), 'profile_source': row.get('profile_source', ''),
                'profile_quote': row.get('profile_quote', ''),
            })
    observations = [{
        'kind': item.get('kind', 'prerequisite'), 'requirement': str(item.get('job_quote') or item.get('detail') or '')[:200],
        'status': 'conflict', 'job_source': item.get('job_source', 'description'),
        'job_quote': str(item.get('job_quote') or '')[:240], 'evidence_source': str(item.get('profile_source') or ''),
        'evidence_quote': str(item.get('profile_quote') or ''), 'evidence_context': 'declared',
        'explanation': str(item.get('detail') or ''),
    } for item in gate_result['hard_fails']]
    return conditions, observations


def _pending_result(job: dict, profile: dict, enriched: dict, gate_result: dict) -> dict[str, Any]:
    conflicts = [item['detail'] for item in gate_result['hard_fails']]
    conditions, observations = _gate_conditions(gate_result)
    return {
        'score': 0, 'eligible': bool(gate_result['eligible']), 'pending': True, 'uncertain': True,
        'fit_tier': 'unlikely', 'label': 'Analysing job', 'reasons': [],
        'unknowns': ['The local job analysis has not finished yet.'], 'conflicts': conflicts,
        'soft_conflicts': [], 'requirement_gaps': [], 'matched_skills': [], 'missing_skills': [],
        'gates': gate_result['gates'], 'task_fit': None, 'qualification_fit': None,
        'mandatory_review': False, 'language_review': bool(gate_result['language_review']),
        'duty_evidence': {'score': None, 'coverage': 0.0, 'duties': []}, 'semantic_match': None,
        'requirement_observations': observations, 'prerequisite_conditions': conditions, 'profile_skill_context': [],
        'custom_skill_evidence': [], 'content_match': None,
        'local_context': local_context(job, profile, gate_result),
        'score_source': 'v3', 'version': W.VERSION, 'explanation': [], 'calibration': None,
    }


def match_job_v3(
    job: dict,
    profile: dict,
    enrichment: dict | None = None,
    *,
    analysis: dict | None = None,
    evidence: dict | None = None,
    weights: Mapping[str, float] = W.COMPONENT_WEIGHTS,
    knots: Sequence[tuple[float, float]] | None = None,
    tail_slope: float = W.CALIBRATION_TAIL_SLOPE,
) -> dict[str, Any]:
    if knots is None:
        # Each evidence provider has its own calibration so scores stay comparable.
        luna = isinstance(evidence, dict) and evidence.get('provider') == 'luna'
        knots = W.LUNA_CALIBRATION_KNOTS if luna else W.CALIBRATION_KNOTS
    enriched = enrichment if isinstance(enrichment, dict) else lightweight_enrichment(job)
    gate_result = evaluate_gates(job, profile, enriched)
    if not isinstance(analysis, dict) or not isinstance(evidence, dict):
        return _pending_result(job, profile, enriched, gate_result)

    parts = components(analysis, evidence)
    evidence_refs = {row.get('ref') for row in evidence.get('items') or [] if isinstance(row, dict)}
    values = parts['values']
    raw, contributions = raw_score(values, weights)
    context = ' · '.join(part for part in (analysis.get('occupation'), analysis.get('industry')) if part)
    formal_gaps = [row for row in parts['requirements']
                   if not row['gated'] and row.get('need') != 'nice' and row.get('kind') in {'education', 'certification'}
                   and row['support'] < W.PARTIAL_SUPPORT]
    penalties, soft_conflicts = _penalties(job, profile, enriched, gate_result, formal_gaps)
    local = local_context(job, profile, gate_result)

    explanation: list[dict[str, Any]] = []
    duties = parts['duties']
    best_duty = max(duties, key=lambda row: row['support'], default=None)
    scored_req = [row for row in parts['requirements'] if not row['gated']]
    must_rows = [row for row in scored_req if row.get('need') != 'nice']
    nice_rows = [row for row in scored_req if row.get('need') == 'nice']
    weakest_must = min(must_rows, key=lambda row: row['support'], default=None)
    best_nice = max(nice_rows, key=lambda row: row['support'], default=None)
    details = {
        'duties': (f'Duty evidence {values["duties"]:.2f} ({sum(row["support"] >= W.PARTIAL_SUPPORT for row in duties)}'
                   f'/{len(duties)} duties shown in your CV)' if values['duties'] is not None else '', best_duty),
        'must': (f'Mandatory requirements {values["must"]:.2f} ({sum(row["support"] >= W.PARTIAL_SUPPORT for row in must_rows)}'
                 f'/{len(must_rows)} supported; weakest shown)' if values['must'] is not None else '', weakest_must),
        'nice': (f'Preferred requirements {values["nice"]:.2f} ({len(nice_rows)} listed)'
                 if values['nice'] is not None else '', best_nice),
        'role': (f'Role fit {values["role"]:.2f}' if values['role'] is not None else '', None),
        'domain': (f'Occupational field fit {values["domain"]:.2f}' + (f' ({context})' if context else '')
                   if values['domain'] is not None else '', None),
    }
    present_weight = sum(weight for name, weight in weights.items() if values.get(name) is not None)
    counts = {
        'duties': (sum(row['support'] >= W.PARTIAL_SUPPORT for row in duties), len(duties)),
        'must': (sum(row['support'] >= W.PARTIAL_SUPPORT for row in must_rows), len(must_rows)),
        'nice': (sum(row['support'] >= W.PARTIAL_SUPPORT for row in nice_rows), len(nice_rows)),
    }
    for name, contribution in contributions.items():
        detail, row = details[name]
        supported, total = counts.get(name, (None, None))
        # max_points: what this component adds at full support, so the page can show earned vs possible.
        entry = {'signal': name, 'contribution': contribution, 'detail': detail,
                 'max_points': round(100.0 * weights[name] / present_weight, 2),
                 'supported': supported, 'total': total,
                 'job_quote': '', 'profile_source': '', 'profile_quote': ''}
        if row is not None:
            entry.update(job_quote=row['text'][:240], profile_source=row['evidence_source'],
                         profile_quote=row['evidence_quote'][:240] if row['support'] >= W.PARTIAL_SUPPORT else '')
        elif name == 'role' and parts['role']:
            entry.update(job_quote=str(job.get('title') or '')[:180], profile_source='profile.desired_roles',
                         profile_quote=parts['role'].get('role', ''))
        elif name == 'domain' and parts['domain']:
            entry.update(job_quote=parts['domain'].get('field', context)[:180],
                         profile_source=parts['domain'].get('source', ''),
                         profile_quote=parts['domain'].get('quote', '') if values['domain'] >= W.PARTIAL_SUPPORT else '')
        explanation.append(entry)
    for penalty in penalties:
        if penalty['amount'] > 0:
            explanation.append({'signal': penalty['signal'], 'contribution': -penalty['amount'],
                                'detail': penalty['detail'], 'job_quote': penalty['job_quote'],
                                'profile_source': penalty['profile_source'], 'profile_quote': penalty['profile_quote']})
    if local['score_adjustment']:
        explanation.append({
            'signal': 'local_context:afd_vote_share', 'contribution': float(local['score_adjustment']),
            'detail': (f"Local AfD vote share {local['average_afd_vote_share_percent']}% "
                       f"(weight {local['weight']}, adjustment {local['score_adjustment']:+d})"),
            'job_quote': '', 'profile_source': 'profile.matching_defaults.low_afd_vote_share_weight', 'profile_quote': '',
        })
    total_raw = round(sum(entry['contribution'] for entry in explanation), 2)
    score = int(round(calibrate(total_raw, knots, tail_slope)))

    eligible = bool(gate_result['eligible'])
    conflicts = [item['detail'] for item in gate_result['hard_fails']]
    qualification_review = [{
        'kind': row.get('kind', 'education'), 'gate': 'qualification', 'status': 'unknown',
        'reason': f'Required {row.get("kind", "qualification")} is unverified: {row["text"][:160]}',
        'detail': f'Required {row.get("kind", "qualification")} is unverified: {row["text"][:160]}',
        'job_source': 'description', 'job_quote': row['text'][:240],
        'profile_source': 'profile.education', 'profile_quote': '',
    } for row in formal_gaps]
    unknowns = [*(row['detail'] for row in qualification_review), *(flag['detail'] for flag in gate_result['flags'])]
    if not duties and not scored_req:
        unknowns.append('The posting lists no duties or requirements to compare with your CV.')
    unchecked = [row for index, row in enumerate(analysis.get('requirements') or [])
                 if row.get('kind') not in GATED_KINDS and not is_soft_requirement(row)
                 and f'r{index}' not in evidence_refs]
    if unchecked:
        unknowns.append(f'{len(unchecked)} further requirement(s) of this long posting were not checked against '
                        f'your CV, for example: {unchecked[0].get("text", "")[:120]}')
    language_review = bool(gate_result['language_review'])
    mandatory_review = bool(gate_result['authorization_review'] or qualification_review)
    hard_review = bool(language_review or mandatory_review or gate_result['location']['status'] == 'unknown')
    if not eligible or score < W.TIER_POSSIBLE_MIN:
        fit_tier = 'unlikely'
    elif score >= W.TIER_RECOMMENDED_MIN and not hard_review:
        fit_tier = 'recommended'
    else:
        fit_tier = 'possible'
    label = ('Preference conflict' if not eligible else 'Strong overlap' if fit_tier == 'recommended'
             else 'Strong overlap (verify requirements)' if score >= W.TIER_RECOMMENDED_MIN
             else 'Some overlap' if score >= W.TIER_POSSIBLE_MIN else 'Little evidence of a match')

    def observation(row: dict) -> dict[str, Any]:
        supported = row['support'] is not None and row['support'] >= W.DIRECT_SUPPORT
        partial = row['support'] is not None and row['support'] >= W.PARTIAL_SUPPORT
        return {
            'kind': row.get('kind', 'qualification'), 'requirement': row['text'][:200],
            'status': 'supported' if supported else 'unknown', 'job_source': 'description',
            'job_quote': row['text'][:240], 'evidence_source': row['evidence_source'] if partial else '',
            'evidence_quote': row['evidence_quote'][:240] if partial else '',
            'evidence_context': 'professional' if partial else 'unspecified',
            'explanation': (f'Your CV shows this {"directly" if supported else "partly"}' if partial
                            else 'No CV line shows this yet'),
            'strength': 'direct' if supported else 'partial' if partial else 'none',
            'modality': row.get('need', 'must'),
        }

    gate_conditions, gate_observations = _gate_conditions(gate_result)
    requirement_observations = [observation(row) for row in scored_req] + gate_observations
    prerequisite_conditions = gate_conditions + qualification_review

    duty_score = int(round(100 * values['duties'])) if values['duties'] is not None else None
    task_basis = values['duties'] if values['duties'] is not None else values['must']
    positive = sorted((entry for entry in explanation if entry['contribution'] > 0.5), key=lambda e: -e['contribution'])
    return {
        'score': score,
        'eligible': eligible,
        'pending': False,
        'uncertain': bool(unknowns or soft_conflicts),
        'fit_tier': fit_tier,
        'label': label,
        'reasons': [f'{entry["detail"]} (+{entry["contribution"]:.1f})' for entry in positive[:5]],
        'unknowns': unknowns,
        'conflicts': conflicts,
        'soft_conflicts': soft_conflicts,
        'requirement_gaps': [row['text'][:180] for row in must_rows if row['support'] < W.PARTIAL_SUPPORT],
        'matched_skills': [_short(row['text']) for row in sorted(
            (row for row in scored_req if row['support'] >= W.DIRECT_SUPPORT), key=lambda row: -row['support'])][:4],
        'missing_skills': [],
        'gates': gate_result['gates'],
        'task_fit': int(round(100 * task_basis)) if task_basis is not None else None,
        'qualification_fit': int(round(100 * values['must'])) if values['must'] is not None else None,
        'mandatory_review': mandatory_review,
        'language_review': language_review,
        'duty_evidence': {
            'score': duty_score,
            'coverage': round(sum(row['support'] >= W.PARTIAL_SUPPORT for row in duties) / len(duties), 4) if duties else 0.0,
            'duties': [{
                'job_quote': row['text'][:240], 'status': _status(row['support']),
                'evidence_source': row['evidence_source'] if row['support'] >= W.PARTIAL_SUPPORT else '',
                'evidence_quote': row['evidence_quote'][:240] if row['support'] >= W.PARTIAL_SUPPORT else '',
                'evidence_reference': '', 'evidence_context': context or 'unspecified',
            } for row in duties],
        },
        'semantic_match': None,
        'requirement_observations': requirement_observations,
        # Personal traits are not scored: no CV line can prove them, the cover letter can show them.
        'soft_requirements': [{'text': row['text'][:240], 'modality': row.get('need', 'must')}
                              for row in analysis.get('requirements') or [] if is_soft_requirement(row)],
        'prerequisite_conditions': prerequisite_conditions,
        'profile_skill_context': [],
        'custom_skill_evidence': [],
        'content_match': None,
        'local_context': local,
        'job_context': {'occupation': analysis.get('occupation') or '', 'industry': analysis.get('industry') or ''},
        'score_source': 'v3',
        'version': W.VERSION,
        'explanation': explanation,
        'calibration': {'raw': total_raw, 'calibrated': score, 'version': W.VERSION},
    }
