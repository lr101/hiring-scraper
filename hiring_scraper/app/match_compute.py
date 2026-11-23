"""Shared profile/job match computation, stored explanations and version checks."""
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
from typing import Any

from sqlalchemy import and_, select, tuple_
from sqlalchemy.orm import aliased, object_session

from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.models import (
    Job,
    MatchDirtyJob,
    MatchSemanticDocument,
    ProfileJobMatch,
    UserProfile,
)
from hiring_scraper.app.profile_semantics import cached_semantic_evidence, load_semantic_documents
from hiring_scraper.matching import VERSION as MATCHING_VERSION, match_job
from hiring_scraper.matching_v2 import VERSION as MATCHING_V2_VERSION, match_job_v2
from hiring_scraper.semantic_matching import VERSION as SEMANTIC_VERSION

EXPLANATION_MAX_BYTES = 16 * 1024
_TRUNCATED_ITEM = '... (truncated)'
_JSONB_UNSAFE_RE = re.compile(r'[\x00\ud800-\udfff]')
_STAMP_KEY = '_stamp'

_STRING_LIST_FIELDS = (
    'reasons',
    'unknowns',
    'conflicts',
    'soft_conflicts',
    'requirement_gaps',
    'matched_skills',
    'missing_skills',
)
_DUTY_FIELDS = (
    'job_quote',
    'status',
    'evidence_source',
    'evidence_quote',
    'evidence_reference',
    'evidence_context',
)
_REQUIREMENT_OBSERVATION_FIELDS = (
    'kind',
    'requirement',
    'status',
    'job_source',
    'job_quote',
    'evidence_source',
    'evidence_quote',
    'evidence_context',
    'explanation',
    'unit',
    'scope',
    'modality',
)
_REQUIREMENT_OBSERVATION_SHORT_FIELDS = frozenset({
    'kind',
    'status',
    'job_source',
    'evidence_source',
    'evidence_context',
})
_CITY_ELECTION_TEXT_FIELDS = (
    'municipality_code',
    'municipality',
    'state',
    'party',
    'leading_party',
    'election',
    'vote_type',
    'source',
    'source_url',
)
_CITY_ELECTION_SCALAR_FIELDS = (
    'leading_party_vote_share_percent',
    'vote_share_percent',
    'party_votes',
    'valid_second_votes',
    'postal_ballots_included',
)


@dataclass(frozen=True)
class MatchRowState:
    """Immutable view of one ProfileJobMatch row for pure currency validation."""
    match_score: int
    match_tier: str
    match_uncertain: bool
    explanation: dict[str, Any] | None
    score_version: str | None


@dataclass(frozen=True)
class MatchSnapshot:
    """Single-statement snapshot of a (profile, job) match row and all invalidation markers."""
    profile_id: int
    job_id: int
    profile_index_version: str | None
    profile_source_version: int | None
    match: MatchRowState | None
    dirty_revision: str | None
    job_semantic_hash: str | None
    profile_semantic_hash: str | None

    @property
    def dirty(self) -> bool:
        return self.dirty_revision is not None


def _session_for(*objects):
    for obj in objects:
        if obj is None:
            continue
        try:
            session = object_session(obj)
        except Exception:
            session = None
        if session is not None:
            return session
    return None


def apply_profile_match_scope(match: dict, scope: dict | None) -> dict:
    """External geography remains authoritative after local scoring."""
    if scope is not None:
        scope_unknowns = scope.get('unknowns') or []
        match['unknowns'] = list(dict.fromkeys((match.get('unknowns') or []) + scope_unknowns))
        if scope_unknowns:
            match['uncertain'] = True
            if match.get('fit_tier') == 'recommended':
                match['fit_tier'] = 'possible'
        if not scope['eligible']:
            match['eligible'] = False
            match['fit_tier'] = 'unlikely'
            match['conflicts'] = (match.get('conflicts') or []) + [
                'Search scope: ' + scope['reason'].replace('_', ' ')]
    return match


def _active_matcher():
    setting = os.environ.get('RADIUS_MATCHER', 'v1').strip().lower()
    if setting == 'v1':
        return match_job, MATCHING_VERSION
    if setting == 'v2':
        return match_job_v2, MATCHING_V2_VERSION
    raise ValueError('RADIUS_MATCHER must be v1 or v2')


def active_matcher_version() -> str:
    return _active_matcher()[1]


def _compose_score_version(components: dict[str, str]) -> str:
    payload = json.dumps(components, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:40]
    return f"{components['matcher'][:39]}:{digest}"


def current_score_version() -> str:
    """Composite version stamping stored local explanations (<= 80 chars)."""
    return _compose_score_version({
        'matcher': active_matcher_version(),
        'semantic': SEMANTIC_VERSION,
    })


def job_revision(job, *, source: dict | None = None) -> str:
    """Stable digest of a job's match-relevant source inputs and stored enrichment."""
    resolved_source = job_input(job) if source is None else source
    payload = {
        'source': resolved_source,
        'enrichment': getattr(job, 'enrichment', None) or {},
        'is_active': bool(getattr(job, 'is_active', True)),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:32]


def semantic_stamp(job_semantic_hash: str | None, profile_semantic_hash: str | None) -> str:
    """Combined revision of the job and profile semantic vector rows (or empty when absent)."""
    return f'{job_semantic_hash or ""}:{profile_semantic_hash or ""}'


def documents_semantic_stamp(documents, job_id: int | None, profile_id: int | None) -> str:
    """Read semantic document hashes from a loaded SemanticDocuments bundle."""
    if documents is None or job_id is None or profile_id is None:
        return semantic_stamp(None, None)
    job_doc = documents.get(('job', job_id)) if hasattr(documents, 'get') else None
    profile_doc = documents.get(('profile', profile_id)) if hasattr(documents, 'get') else None
    job_hash = getattr(job_doc, 'source_hash', None) if getattr(job_doc, 'version', SEMANTIC_VERSION) == SEMANTIC_VERSION else None
    profile_hash = getattr(profile_doc, 'source_hash', None) if getattr(profile_doc, 'version', SEMANTIC_VERSION) == SEMANTIC_VERSION else None
    return semantic_stamp(job_hash, profile_hash)


def provenance_stamp(
    job,
    profile_id: int,
    documents,
    *,
    source: dict | None = None,
) -> dict[str, Any]:
    """Capture the exact job and semantic revisions that produced a stored match."""
    return {
        'job': job_revision(job, source=source),
        'sem': documents_semantic_stamp(documents, getattr(job, 'id', None), profile_id),
    }


def compute_match(
    job,
    profile,
    documents=None,
    *,
    scope: dict | None = None,
    source: dict | None = None,
    enrichment: dict | None = None,
    matcher=None,
) -> dict:
    """Single shared local match computation for indexers, detail views and Journey."""
    resolved_source = job_input(job) if source is None else source
    resolved_enrichment = current_enrichment(job) if enrichment is None else enrichment
    if documents is None:
        documents = load_semantic_documents(_session_for(job, profile), [job], [profile])
    match_fn = _active_matcher()[0] if matcher is None else matcher
    preferences = profile.matching_preferences
    semantic = cached_semantic_evidence(
        resolved_source, preferences, job.id, profile.id, documents, aggregate=match_fn is match_job_v2,
    )
    local = match_fn(
        resolved_source,
        preferences,
        resolved_enrichment,
        **({'semantic': semantic} if semantic else {}),
    )
    return apply_profile_match_scope(local, scope)


def _sanitize_str(value: Any) -> str:
    """Strip NUL and lone UTF-16 surrogates rejected by PostgreSQL JSONB."""
    if value is None:
        return ''
    text = value if isinstance(value, str) else str(value)
    return _JSONB_UNSAFE_RE.sub('', text)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {_sanitize_str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return _sanitize_str(value)


def _trim_text(value: Any, limit: int) -> str:
    text = _sanitize_str(value)
    if len(text) <= limit:
        return text
    if limit <= 16:
        return text[:limit]
    return text[:limit - 13].rstrip() + ' [truncated]'


def _trim_string_list(values: Any, max_items: int, max_chars: int) -> tuple[list[str], bool]:
    if not isinstance(values, (list, tuple)):
        return [], False
    sanitized = [_sanitize_str(item) for item in values if item is not None]
    cleaned = [_trim_text(item, max_chars) for item in sanitized]
    was_trimmed = any(len(item) > max_chars for item in sanitized)
    if len(cleaned) > max_items:
        keep = max(0, max_items - 1)
        cleaned = cleaned[:keep] + [_TRUNCATED_ITEM]
        was_trimmed = True
    return cleaned, was_trimmed


def _project_gates(raw_gates: Any, reason_chars: int = 300) -> tuple[dict[str, dict[str, str]], bool]:
    if not isinstance(raw_gates, dict):
        return {}, False
    gates: dict[str, dict[str, str]] = {}
    trimmed = False
    for name, gate in raw_gates.items():
        if not isinstance(gate, dict):
            continue
        raw_reason = _sanitize_str(gate.get('reason') or '')
        if len(raw_reason) > reason_chars:
            trimmed = True
        gates[_trim_text(name, 80)] = {
            'status': _trim_text(gate.get('status') or 'unknown', 40),
            'reason': _trim_text(raw_reason, reason_chars),
        }
    return gates, trimmed


def _project_duty_evidence(raw: Any, *, max_duties: int = 16, quote_chars: int = 240) -> tuple[dict | None, bool]:
    if not isinstance(raw, dict):
        return None, False
    duties_raw = raw.get('duties')
    duties: list[dict[str, Any]] = []
    trimmed = False
    if isinstance(duties_raw, list):
        if len(duties_raw) > max_duties:
            trimmed = True
        for row in duties_raw[:max_duties]:
            if not isinstance(row, dict):
                continue
            projected: dict[str, Any] = {}
            for field in _DUTY_FIELDS:
                if field in row and row[field] is not None:
                    raw_val = _sanitize_str(row[field])
                    if len(raw_val) > quote_chars:
                        trimmed = True
                    projected[field] = _trim_text(raw_val, quote_chars)
            projected.setdefault('job_quote', '')
            projected.setdefault('status', 'unverified')
            projected.setdefault('evidence_source', '')
            projected.setdefault('evidence_quote', '')
            duties.append(projected)
    result: dict[str, Any] = {
        'score': _json_safe(raw.get('score')),
        'coverage': _json_safe(raw.get('coverage', 0)),
        'duties': duties,
    }
    if trimmed:
        result['truncated'] = True
    return result, trimmed


def _project_requirement_observations(
    raw: Any,
    *,
    max_items: int = 16,
    text_chars: int = 220,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(raw, list):
        return [], False
    trimmed = len(raw) > max_items
    items: list[dict[str, Any]] = []
    short_limit = min(80, text_chars)
    for row in raw[:max_items]:
        if not isinstance(row, dict):
            continue
        entry: dict[str, Any] = {}
        for field in _REQUIREMENT_OBSERVATION_FIELDS:
            if field not in row or row[field] is None:
                continue
            raw_val = _sanitize_str(row[field])
            limit = short_limit if field in _REQUIREMENT_OBSERVATION_SHORT_FIELDS else text_chars
            if len(raw_val) > limit:
                trimmed = True
            entry[field] = _trim_text(raw_val, limit)
        for field in ('number', 'minimum_years'):
            if isinstance(row.get(field), (int, float)):
                entry[field] = _json_safe(row[field])
        if entry:
            items.append(entry)
    return items, trimmed


def _project_semantic_match(raw: Any, *, text_chars: int = 240) -> tuple[dict | None, bool]:
    if not isinstance(raw, dict):
        return None, False
    trimmed = False
    projected: dict[str, Any] = {
        'score': _json_safe(raw.get('score')),
        'similarity': _json_safe(raw.get('similarity')),
        'model': _trim_text(raw.get('model'), 120) if raw.get('model') is not None else None,
        'job_source': _trim_text(raw.get('job_source'), 80) if raw.get('job_source') is not None else None,
        'profile_source': _trim_text(raw.get('profile_source'), 80) if raw.get('profile_source') is not None else None,
    }
    for field in ('job_evidence', 'profile_evidence'):
        if field in raw and raw[field] is not None:
            raw_val = _sanitize_str(raw[field])
            if len(raw_val) > text_chars:
                trimmed = True
            projected[field] = _trim_text(raw_val, text_chars)
    return projected, trimmed


def _project_custom_skill_evidence(raw: Any, *, max_items: int = 12, text_chars: int = 200) -> tuple[list[dict], bool]:
    if not isinstance(raw, list):
        return [], False
    trimmed = len(raw) > max_items
    items = []
    for row in raw[:max_items]:
        if not isinstance(row, dict):
            continue
        evidence_raw = _sanitize_str(row.get('evidence') or '')
        if len(evidence_raw) > text_chars:
            trimmed = True
        items.append({
            'name': _trim_text(row.get('name') or '', 80),
            'evidence': _trim_text(evidence_raw, text_chars),
            'source': _trim_text(row.get('source') or '', 80),
        })
    return items, trimmed


def _project_local_context(
    raw: Any,
    *,
    max_cities: int = 8,
    text_chars: int = 160,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, dict):
        return {
            'score_adjustment': 0,
            'weight': 0,
            'average_afd_vote_share_percent': None,
            'city_election_results': [],
        }, False
    cities_raw = raw.get('city_election_results')
    cities: list[dict[str, Any]] = []
    trimmed = False
    if isinstance(cities_raw, list):
        if len(cities_raw) > max_cities:
            trimmed = True
        for row in cities_raw[:max_cities]:
            if not isinstance(row, dict):
                continue
            entry: dict[str, Any] = {}
            for field in _CITY_ELECTION_TEXT_FIELDS:
                if field in row and row[field] is not None:
                    raw_val = _sanitize_str(row[field])
                    if len(raw_val) > text_chars:
                        trimmed = True
                    entry[field] = _trim_text(raw_val, text_chars)
            for field in _CITY_ELECTION_SCALAR_FIELDS:
                if field in row:
                    entry[field] = _json_safe(row[field])
            if entry:
                cities.append(entry)
    projected: dict[str, Any] = {
        'score_adjustment': int(raw.get('score_adjustment') or 0),
        'weight': int(raw.get('weight') or 0),
        'average_afd_vote_share_percent': _json_safe(raw.get('average_afd_vote_share_percent')),
        'city_election_results': cities,
    }
    if trimmed:
        projected['truncated'] = True
    return projected, trimmed


def serialize_explanation(payload: dict[str, Any]) -> str:
    """Serialize with the same default json.dumps serializer SQLAlchemy/psycopg sends to JSONB."""
    return json.dumps(payload, allow_nan=False)


def _encoded_size(payload: dict[str, Any]) -> int:
    return len(serialize_explanation(payload).encode('utf-8'))


def _project_cited_rows(rows, fields, *, max_items, text_chars, numeric_fields=()):
    if not isinstance(rows, list):
        return [], False
    projected = []
    trimmed = len(rows) > max_items
    for row in rows[:max_items]:
        if not isinstance(row, dict):
            continue
        entry = {}
        for field in fields:
            if field not in row:
                continue
            text = _sanitize_str(row[field])
            limit = min(text_chars, 80) if field in {'signal', 'status', 'kind', 'gate'} else text_chars
            trimmed = trimmed or len(text) > limit
            entry[field] = _trim_text(text, limit)
        for field in numeric_fields:
            entry[field] = _json_safe(row.get(field))
        projected.append(entry)
    return projected, trimmed


def _build_explanation_pass(
    result: dict,
    *,
    list_limit: int,
    str_chars: int,
    duty_limit: int,
    obs_limit: int,
    city_limit: int,
    nested_chars: int,
    gate_chars: int,
    custom_limit: int,
    stamp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    truncated = False
    score = int(result.get('score', 0))
    raw_label = _sanitize_str(result.get('label') or '')
    explanation: dict[str, Any] = {
        'score': score,
        'fit_tier': _trim_text(result.get('fit_tier') or 'possible', 32),
        'label': _trim_text(raw_label, gate_chars),
        'eligible': bool(result.get('eligible', False)),
        'uncertain': bool(result.get('uncertain', False)),
        'mandatory_review': bool(result.get('mandatory_review', False)),
        'language_review': bool(result.get('language_review', False)),
        'task_fit': _json_safe(result.get('task_fit')),
        'qualification_fit': _json_safe(result.get('qualification_fit')),
        'static_rank_basis': _trim_text(result.get('static_rank_basis'), 80)
        if result.get('static_rank_basis') is not None else None,
    }
    if len(raw_label) > gate_chars:
        truncated = True
    for field in _STRING_LIST_FIELDS:
        items, trimmed = _trim_string_list(result.get(field), list_limit, str_chars)
        explanation[field] = items
        truncated = truncated or trimmed
    observations, obs_trimmed = _project_requirement_observations(
        result.get('requirement_observations'), max_items=obs_limit, text_chars=nested_chars,
    )
    explanation['requirement_observations'] = observations
    truncated = truncated or obs_trimmed
    gates, gates_trimmed = _project_gates(result.get('gates'), reason_chars=gate_chars)
    explanation['gates'] = gates
    truncated = truncated or gates_trimmed
    duty_evidence, duty_trimmed = _project_duty_evidence(
        result.get('duty_evidence'), max_duties=duty_limit, quote_chars=nested_chars,
    )
    explanation['duty_evidence'] = duty_evidence
    truncated = truncated or duty_trimmed
    semantic_match, sem_trimmed = _project_semantic_match(
        result.get('semantic_match'), text_chars=nested_chars,
    )
    explanation['semantic_match'] = semantic_match
    truncated = truncated or sem_trimmed
    local_context, ctx_trimmed = _project_local_context(
        result.get('local_context'), max_cities=city_limit, text_chars=nested_chars,
    )
    explanation['local_context'] = local_context
    truncated = truncated or ctx_trimmed
    custom_evidence, custom_trimmed = _project_custom_skill_evidence(
        result.get('custom_skill_evidence'), max_items=custom_limit, text_chars=nested_chars,
    )
    if custom_evidence:
        explanation['custom_skill_evidence'] = custom_evidence
    truncated = truncated or custom_trimmed
    calibration = result.get('calibration')
    if isinstance(calibration, dict):
        explanation['calibration'] = {
            'raw': _json_safe(calibration.get('raw')),
            'calibrated': score,
            'version': _trim_text(calibration.get('version'), 80),
        }
        # The fixed-size v2 ledger always keeps every numeric contribution.
        # Shorten citations as necessary so it still reconciles with the raw score.
        ledger = result.get('explanation') or []
        explanation['explanation'], ledger_trimmed = _project_cited_rows(
            ledger, ('signal', 'detail', 'job_quote', 'profile_source', 'profile_quote'),
            max_items=len(ledger), text_chars=min(nested_chars, 160), numeric_fields=('contribution',),
        )
        explanation['prerequisite_conditions'], conditions_trimmed = _project_cited_rows(
            result.get('prerequisite_conditions'),
            ('kind', 'gate', 'status', 'reason', 'job_source', 'job_quote', 'profile_source', 'profile_quote'),
            max_items=max(2, obs_limit), text_chars=nested_chars,
        )
        truncated = truncated or ledger_trimmed or conditions_trimmed
    if stamp is not None:
        explanation[_STAMP_KEY] = _json_safe(stamp)
    if truncated:
        explanation['truncated'] = True
    return explanation


def build_explanation(
    result: dict,
    *,
    max_bytes: int = EXPLANATION_MAX_BYTES,
    stamp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a compact, JSONB-safe explanation bounded on its final serialized bytes."""
    tiers = (
        dict(list_limit=20, str_chars=260, duty_limit=14, obs_limit=14, city_limit=8,
             nested_chars=220, gate_chars=280, custom_limit=10),
        dict(list_limit=12, str_chars=180, duty_limit=8, obs_limit=8, city_limit=4,
             nested_chars=150, gate_chars=200, custom_limit=5),
        dict(list_limit=6, str_chars=120, duty_limit=4, obs_limit=4, city_limit=2,
             nested_chars=100, gate_chars=140, custom_limit=2),
        dict(list_limit=3, str_chars=80, duty_limit=2, obs_limit=2, city_limit=1,
             nested_chars=64, gate_chars=90, custom_limit=0),
        dict(list_limit=1, str_chars=48, duty_limit=0, obs_limit=0, city_limit=0,
             nested_chars=40, gate_chars=50, custom_limit=0),
    )
    explanation: dict[str, Any] = {}
    for params in tiers:
        explanation = _build_explanation_pass(result, stamp=stamp, **params)
        if _encoded_size(explanation) <= max_bytes:
            return explanation
    # Extreme fallback cascade: guarantees _encoded_size(explanation) <= max_bytes
    # even for adversarial payloads with huge gate/key counts or non-ASCII text.
    explanation['truncated'] = True
    explanation['requirement_observations'] = []
    if isinstance(explanation.get('local_context'), dict):
        explanation['local_context']['city_election_results'] = []
        explanation['local_context']['truncated'] = True
    for field in _STRING_LIST_FIELDS:
        if explanation[field]:
            explanation[field] = [_TRUNCATED_ITEM]
    if isinstance(explanation.get('duty_evidence'), dict):
        explanation['duty_evidence'] = {
            'score': explanation['duty_evidence'].get('score'),
            'coverage': explanation['duty_evidence'].get('coverage', 0),
            'duties': [],
            'truncated': True,
        }
    if _encoded_size(explanation) <= max_bytes:
        return explanation

    explanation['gates'] = {}
    explanation['semantic_match'] = None
    explanation.pop('custom_skill_evidence', None)
    explanation['label'] = _trim_text(explanation.get('label', ''), 48)
    if _encoded_size(explanation) <= max_bytes:
        return explanation

    for field in _STRING_LIST_FIELDS:
        explanation[field] = []
    explanation['duty_evidence'] = None
    if _encoded_size(explanation) <= max_bytes:
        return explanation

    minimal: dict[str, Any] = {
        'score': int(result.get('score', 0)),
        'fit_tier': _trim_text(result.get('fit_tier') or 'possible', 16),
        'label': '',
        'eligible': bool(result.get('eligible', False)),
        'uncertain': bool(result.get('uncertain', False)),
        'mandatory_review': bool(result.get('mandatory_review', False)),
        'language_review': bool(result.get('language_review', False)),
        'truncated': True,
    }
    if stamp is not None:
        candidate = {**minimal, _STAMP_KEY: _json_safe(stamp)}
        if _encoded_size(candidate) <= max_bytes:
            return candidate
    if _encoded_size(minimal) <= max_bytes:
        return minimal
    for drop_key in ('language_review', 'mandatory_review', 'label', 'fit_tier', 'uncertain', 'eligible'):
        minimal.pop(drop_key, None)
        if _encoded_size(minimal) <= max_bytes:
            return minimal
    return minimal


def public_explanation(explanation: dict[str, Any], *, scope: dict | None = None) -> dict[str, Any]:
    """Return a caller-safe copy of a stored explanation with internal stamp stripped and scope applied."""
    copied = deepcopy(explanation)
    copied.pop(_STAMP_KEY, None)
    return apply_profile_match_scope(copied, scope)


def match_row_payload(
    profile_id: int,
    job_id: int,
    result: dict,
    score_version: str,
    *,
    job=None,
    documents=None,
    source: dict | None = None,
    stamp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ProfileJobMatch upsert row with guaranteed explanation.score == match_score."""
    resolved_stamp = stamp
    if resolved_stamp is None and job is not None:
        resolved_stamp = provenance_stamp(job, profile_id, documents, source=source)
    explanation = build_explanation(result, stamp=resolved_stamp)
    return {
        'profile_id': profile_id,
        'job_id': job_id,
        'match_score': explanation['score'],
        'match_tier': result.get('fit_tier', 'possible'),
        'match_uncertain': bool(result.get('uncertain')),
        'explanation': explanation,
        'score_version': score_version,
    }


def explanation_is_current(
    match_row: MatchRowState | Any | None,
    *,
    dirty: bool,
    profile_stale: bool,
    job_revision: str,
    semantic_hash: str,
    expected_version: str | None = None,
) -> bool:
    """Pure decision: whether a stored ProfileJobMatch explanation is current and faithful."""
    if match_row is None:
        return False
    explanation = getattr(match_row, 'explanation', None)
    if not isinstance(explanation, dict):
        return False
    if explanation.get('score') != getattr(match_row, 'match_score', None):
        return False
    if dirty or profile_stale:
        return False
    version = current_score_version() if expected_version is None else expected_version
    if getattr(match_row, 'score_version', None) != version:
        return False
    stamp = explanation.get(_STAMP_KEY)
    if not isinstance(stamp, dict):
        return False
    if stamp.get('job') != job_revision:
        return False
    return stamp.get('sem') == semantic_hash


def select_journey_card(
    match_row: MatchRowState | Any | None,
    *,
    ranked_score: int,
    is_current: bool,
) -> tuple[str, dict[str, Any] | None]:
    """Pure decision for Journey card rendering that keeps rank and card score consistent.

    Returns:
      - ``('stored', card)`` when the stored explanation is current and matches ``ranked_score``.
      - ``('refreshing', card)`` when the stored row is stale/dirty but its stored explanation is
        faithful to ``ranked_score`` (shows the same score Journey ranked by, flagged refreshing).
      - ``('recompute', None)`` when no faithful stored explanation matches ``ranked_score``
        (missing/legacy row or concurrent score drift), so the caller computes a fallback card.
    """
    if match_row is None:
        return 'recompute', None
    explanation = getattr(match_row, 'explanation', None)
    match_score = getattr(match_row, 'match_score', None)
    if not isinstance(explanation, dict) or explanation.get('score') != match_score or match_score != ranked_score:
        return 'recompute', None
    card = public_explanation(explanation)
    if is_current:
        return 'stored', card
    card['refreshing'] = True
    return 'refreshing', card


def indexed_missing_match_action(*, profile_stale: bool, job_dirty: bool) -> str:
    """Pure decision for a missing ProfileJobMatch row in indexed search.

    Only treat a missing row as an authoritative ``profile_conflict`` when the
    profile's index is current and the job is not dirty. Otherwise recompute.
    """
    if profile_stale or job_dirty:
        return 'recompute'
    return 'profile_conflict'


def build_stored_match_snapshot_query(pairs: list[tuple[int, int]]):
    """Single-snapshot SQL query joining match rows with dirty and vector metadata.

    Loads zero vector blobs or section JSON; all invalidation markers come from
    the same PostgreSQL MVCC snapshot as ``ProfileJobMatch``.
    """
    job_sem = aliased(MatchSemanticDocument)
    prof_sem = aliased(MatchSemanticDocument)
    profile_ids = sorted({int(profile_id) for profile_id, _ in pairs})
    job_ids = sorted({int(job_id) for _, job_id in pairs})
    pair_filter = and_(
        UserProfile.id.in_(profile_ids),
        Job.id.in_(job_ids),
    )
    if len(profile_ids) > 1 and len(job_ids) > 1:
        pair_filter = and_(pair_filter, tuple_(UserProfile.id, Job.id).in_(pairs))
    return (
        select(
            UserProfile.id.label('profile_id'),
            Job.id.label('job_id'),
            UserProfile.match_index_version.label('profile_index_version'),
            UserProfile.source_version.label('profile_source_version'),
            ProfileJobMatch.match_score.label('match_score'),
            ProfileJobMatch.match_tier.label('match_tier'),
            ProfileJobMatch.match_uncertain.label('match_uncertain'),
            ProfileJobMatch.explanation.label('explanation'),
            ProfileJobMatch.score_version.label('score_version'),
            MatchDirtyJob.revision.label('dirty_revision'),
            job_sem.source_hash.label('job_semantic_hash'),
            prof_sem.source_hash.label('profile_semantic_hash'),
        )
        .select_from(UserProfile)
        .join(Job, pair_filter)
        .outerjoin(
            ProfileJobMatch,
            and_(
                ProfileJobMatch.profile_id == UserProfile.id,
                ProfileJobMatch.job_id == Job.id,
            ),
        )
        .outerjoin(MatchDirtyJob, MatchDirtyJob.job_id == Job.id)
        .outerjoin(
            job_sem,
            and_(
                job_sem.job_id == Job.id,
                job_sem.version == SEMANTIC_VERSION,
            ),
        )
        .outerjoin(
            prof_sem,
            and_(
                prof_sem.profile_id == UserProfile.id,
                prof_sem.version == SEMANTIC_VERSION,
            ),
        )
    )


def load_stored_match_snapshots(
    session,
    pairs: Iterable[tuple[int, int]],
    *,
    batch_size: int = 400,
) -> dict[tuple[int, int], MatchSnapshot]:
    """Load consistent single-statement match snapshots for exact ``(profile_id, job_id)`` pairs."""
    unique_pairs = sorted({
        (int(profile_id), int(job_id))
        for profile_id, job_id in pairs
        if profile_id is not None and job_id is not None
    })
    if session is None or not unique_pairs:
        return {}
    snapshots: dict[tuple[int, int], MatchSnapshot] = {}
    for start in range(0, len(unique_pairs), batch_size):
        batch = unique_pairs[start:start + batch_size]
        rows = session.execute(build_stored_match_snapshot_query(batch)).all()
        for row in rows:
            match = (
                MatchRowState(
                    match_score=row.match_score,
                    match_tier=row.match_tier,
                    match_uncertain=bool(row.match_uncertain),
                    explanation=row.explanation,
                    score_version=row.score_version,
                )
                if row.match_score is not None
                else None
            )
            snapshots[(row.profile_id, row.job_id)] = MatchSnapshot(
                profile_id=row.profile_id,
                job_id=row.job_id,
                profile_index_version=row.profile_index_version,
                profile_source_version=row.profile_source_version,
                match=match,
                dirty_revision=row.dirty_revision,
                job_semantic_hash=row.job_semantic_hash,
                profile_semantic_hash=row.profile_semantic_hash,
            )
    return snapshots


def snapshot_explanation_is_current(
    snapshot: MatchSnapshot | None,
    job,
    profile,
    *,
    expected_profile_index_version: str,
    job_rev: str | None = None,
) -> bool:
    """Validate a loaded MatchSnapshot against live job/profile inputs using pure explanation_is_current."""
    if snapshot is None or snapshot.match is None:
        return False
    profile_stale = snapshot.profile_index_version != expected_profile_index_version
    resolved_job_rev = job_revision(job) if job_rev is None else job_rev
    sem_hash = semantic_stamp(snapshot.job_semantic_hash, snapshot.profile_semantic_hash)
    return explanation_is_current(
        snapshot.match,
        dirty=snapshot.dirty,
        profile_stale=profile_stale,
        job_revision=resolved_job_rev,
        semantic_hash=sem_hash,
        expected_version=current_score_version(),
    )


def stored_match_explanation(
    indexed,
    job,
    profile,
    documents=None,
    *,
    expected_version: str | None = None,
    dirty_job_ids: set[int] | None = None,
    profile_stale: bool | None = None,
    scope: dict | None = None,
    snapshot: MatchSnapshot | None = None,
    expected_profile_index_version: str | None = None,
) -> dict | None:
    """Return caller-safe stored explanation (with scope applied) if current and faithful, else None."""
    if snapshot is not None:
        if expected_profile_index_version is None:
            from hiring_scraper.app.job_search import _profile_index_version
            expected_profile_index_version = _profile_index_version(profile)
        if not snapshot_explanation_is_current(
            snapshot,
            job,
            profile,
            expected_profile_index_version=expected_profile_index_version,
        ):
            return None
        return public_explanation(snapshot.match.explanation, scope=scope)

    if indexed is None or not isinstance(getattr(indexed, 'explanation', None), dict):
        return None
    is_current = explanation_is_current(
        indexed,
        dirty=bool(dirty_job_ids is not None and job.id in dirty_job_ids),
        profile_stale=bool(profile_stale),
        job_revision=job_revision(job),
        semantic_hash=documents_semantic_stamp(documents, getattr(job, 'id', None), getattr(profile, 'id', None)),
        expected_version=expected_version or current_score_version(),
    )
    if not is_current:
        return None
    return public_explanation(indexed.explanation, scope=scope)
