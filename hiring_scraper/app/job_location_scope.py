"""Precomputed Germany and German-state candidate flags for job search scopes.

Search snapshots and semantic search narrow candidate jobs in SQL before Python
runs exact workplace and remote checks. Evaluating hundreds of ``ILIKE``
patterns against ``jobs.location_text`` and ``job_locations.label`` on every
snapshot forces full inventory scans. This module evaluates the exact same
pattern and bounding-box rules once per job write, stores the result on
``jobs``, and stamps a deterministic version hash derived from the rules so any
place-name or boundary update triggers a safe fallback and bounded backfill.
"""
from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
import hashlib
import json
import re
import unicodedata
from typing import Any

from sqlalchemy import Connection, or_, select, text
from sqlalchemy.orm import Session

from hiring_scraper.app.board_scope import german_location_text_names, german_state_bounds
from hiring_scraper.geography import GERMAN_STATES


GERMANY_COUNTRY_CODE_PATTERNS: tuple[str, ...] = (
    'DE', 'DE %', 'DE,%', 'DE/%', 'DE-%', 'DE)%',
    '%, DE', '%, DE %', '%, DE,%', '%,DE', '%,DE %',
    '% DE', '% DE %', '% DE,%', '%/DE', '%/DE %', '%/DE,%',
    '%(DE)%', '% (DE)%', '%(DE %', '% (DE %',
    '%;DE%', '%; DE%', '%|DE%', '%| DE%', '%\nDE%',
)


def location_text_patterns(value: str) -> set[str]:
    """SQL ILIKE patterns matching a German place name and its ASCII variants."""
    source = value.strip().casefold().replace('ß', 'ss')
    normalized = unicodedata.normalize('NFKD', source)
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    transliterated = source.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue'}))
    patterns: set[str] = set()
    for raw in {value.strip().casefold(), value.strip().upper()}:
        raw_words = re.sub(r'[^A-Za-z0-9äöüÄÖÜ]+', '%', raw).strip('%')
        if raw_words:
            patterns.add(f'%{raw_words}%')
    for variant in {normalized, transliterated}:
        words = re.sub(r'[^a-z0-9]+', '%', variant).strip('%')
        if words:
            patterns.add(f'%{words}%')
    return patterns


@lru_cache(maxsize=32)
def state_text_patterns(state: str) -> frozenset[str]:
    return frozenset(
        pattern
        for value in german_location_text_names(state)
        for pattern in location_text_patterns(value)
    )


@lru_cache(maxsize=1)
def germany_text_patterns() -> frozenset[str]:
    return frozenset(
        pattern
        for value in ('Germany', 'Deutschland', *german_location_text_names())
        for pattern in location_text_patterns(value)
    )


@lru_cache(maxsize=1)
def germany_bounds() -> tuple[float, float, float, float] | None:
    bounds = tuple(german_state_bounds().values())
    if not bounds:
        return None
    return (
        min(bound[0] for bound in bounds),
        min(bound[1] for bound in bounds),
        max(bound[2] for bound in bounds),
        max(bound[3] for bound in bounds),
    )


def _like_pattern_to_regex(pattern: str) -> str:
    """Translate a PostgreSQL ILIKE pattern (no escapes) to a regex over lowercased text."""
    lowered = pattern.lower()
    leading_wild = lowered.startswith('%')
    trailing_wild = lowered.endswith('%')
    core = lowered.lstrip('%').rstrip('%')
    if not core and (leading_wild or trailing_wild):
        return ''
    parts: list[str] = [] if leading_wild else ['\\A']
    index = 0
    while index < len(core):
        char = core[index]
        if char == '%':
            while index + 1 < len(core) and core[index + 1] == '%':
                index += 1
            parts.append('.*')
        elif char == '_':
            parts.append('.')
        else:
            parts.append(re.escape(char))
        index += 1
    if not trailing_wild:
        parts.append('\\Z')
    return ''.join(parts)


def _compile_like_patterns(patterns: Iterable[str]) -> re.Pattern[str] | None:
    branches = sorted({_like_pattern_to_regex(pattern) for pattern in patterns})
    if not branches:
        return None
    return re.compile('|'.join(f'(?:{branch})' for branch in branches), re.DOTALL)


@lru_cache(maxsize=1)
def _compiled_matchers() -> tuple[
    tuple[tuple[str, re.Pattern[str] | None, tuple[float, float, float, float] | None], ...],
    re.Pattern[str] | None,
    tuple[float, float, float, float] | None,
]:
    bounds_by_state = german_state_bounds()
    all_state_patterns_lower: set[str] = set()
    state_matchers: list[tuple[str, re.Pattern[str] | None, tuple[float, float, float, float] | None]] = []
    for state in GERMAN_STATES:
        patterns = state_text_patterns(state)
        all_state_patterns_lower.update(pattern.lower() for pattern in patterns)
        state_matchers.append((state, _compile_like_patterns(patterns), bounds_by_state.get(state)))

    germany_extra_patterns = {
        pattern
        for pattern in (*germany_text_patterns(), *GERMANY_COUNTRY_CODE_PATTERNS)
        if pattern.lower() not in all_state_patterns_lower
    }
    return tuple(state_matchers), _compile_like_patterns(germany_extra_patterns), germany_bounds()


def _compute_location_scope_version() -> str:
    payload = {
        'schema': 1,
        'germany_patterns': sorted({pattern.lower() for pattern in germany_text_patterns()}),
        'country_code_patterns': sorted({pattern.lower() for pattern in GERMANY_COUNTRY_CODE_PATTERNS}),
        'state_patterns': {
            state: sorted({pattern.lower() for pattern in state_text_patterns(state)})
            for state in sorted(GERMAN_STATES)
        },
        'state_bounds': {
            state: list(bounds)
            for state, bounds in sorted(german_state_bounds().items())
        },
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ).hexdigest()[:16]
    return f'loc-scope-v1-{digest}'


LOCATION_SCOPE_VERSION = _compute_location_scope_version()


def _in_bounds(coords: list[tuple[float, float]], bounds: tuple[float, float, float, float] | None) -> bool:
    if bounds is None or not coords:
        return False
    west, south, east, north = bounds
    return any(south <= latitude <= north and west <= longitude <= east for latitude, longitude in coords)


def compute_job_location_scope(
    location_text: str | None,
    locations: Iterable[tuple[str | None, str | None, float | None, float | None]],
) -> tuple[bool, list[str]]:
    """Compute ``(germany_candidate, location_states)`` matching the SQL scope predicates.

    ``germany_candidate`` matches ``Job.locations.any(lower(country_code) == 'de')``
    OR ``_job_germany_text_candidates()``. ``location_states`` lists every German
    state for which ``_job_state_text_candidates(state)`` matches.
    """
    texts: list[str] = []
    if isinstance(location_text, str) and location_text:
        texts.append(location_text.lower())

    coords: list[tuple[float, float]] = []
    has_de_country_code = False
    for label, country_code, latitude, longitude in locations:
        if isinstance(label, str) and label:
            texts.append(label.lower())
        if isinstance(country_code, str) and country_code.lower() == 'de':
            has_de_country_code = True
        if latitude is not None and longitude is not None:
            coords.append((latitude, longitude))

    state_matchers, germany_extra_regex, overall_bounds = _compiled_matchers()
    matched_states: list[str] = []
    for state, regex, bounds in state_matchers:
        if _in_bounds(coords, bounds) or (regex is not None and any(regex.search(value) for value in texts)):
            matched_states.append(state)

    germany_candidate = (
        bool(matched_states)
        or has_de_country_code
        or _in_bounds(coords, overall_bounds)
        or (germany_extra_regex is not None and any(germany_extra_regex.search(value) for value in texts))
    )
    return germany_candidate, matched_states


def apply_scope_updates(connection: Connection, rows: list[dict[str, Any]], version: str) -> int:
    """Batch-update precomputed scope columns without touching semantic-trigger columns."""
    if not rows:
        return 0
    result = connection.execute(
        text("""
            UPDATE jobs AS j
               SET germany_candidate = v.germany_candidate,
                   location_states = v.location_states,
                   location_scope_version = :version
              FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                AS v(id integer, germany_candidate boolean, location_states text[])
             WHERE j.id = v.id
        """),
        {'rows': json.dumps(rows, ensure_ascii=False, separators=(',', ':')), 'version': version},
    )
    return int(result.rowcount or 0)


def apply_job_location_scope(job: Any) -> tuple[bool, list[str]]:
    """Compute and assign precomputed location scope columns on a Job before flush.

    Assigns directly without reading the deferred columns first so callers like
    ``replace_job_locations`` never trigger lazy-load SELECTs or a second UPDATE.
    """
    locations = [
        (loc.label, loc.country_code, loc.latitude, loc.longitude)
        for loc in (job.locations or ())
    ]
    germany_candidate, location_states = compute_job_location_scope(job.location_text, locations)
    job.germany_candidate = germany_candidate
    job.location_states = location_states
    job.location_scope_version = LOCATION_SCOPE_VERSION
    return germany_candidate, location_states


def refresh_job_location_scopes(session: Session, jobs: Iterable[Any]) -> int:
    """Update precomputed location scope columns on loaded Job rows in-place."""
    version = LOCATION_SCOPE_VERSION
    updated = 0
    for job in jobs:
        locations = [
            (loc.label, loc.country_code, loc.latitude, loc.longitude)
            for loc in (job.locations or ())
        ]
        germany_candidate, location_states = compute_job_location_scope(job.location_text, locations)
        if (
            job.germany_candidate != germany_candidate
            or job.location_states != location_states
            or job.location_scope_version != version
        ):
            job.germany_candidate = germany_candidate
            job.location_states = location_states
            job.location_scope_version = version
            updated += 1
    if updated:
        session.flush()
    return updated


def backfill_job_location_scopes_connection(
    connection: Connection,
    *,
    batch_size: int = 500,
    version: str | None = None,
) -> int:
    """Backfill all uncomputed or stale jobs on a connection in bounded id-ordered batches.

    Intended for Alembic migrations running inside ``autocommit_block()`` so each
    batch commits immediately and holds row locks only briefly.
    """
    target_version = version or LOCATION_SCOPE_VERSION
    total = 0
    last_id = 0
    while True:
        job_rows = connection.execute(
            text("""
                SELECT id, location_text
                  FROM jobs
                 WHERE id > :last_id
                   AND location_scope_version IS DISTINCT FROM :version
                 ORDER BY id ASC
                 LIMIT :batch_size
            """),
            {'last_id': last_id, 'version': target_version, 'batch_size': batch_size},
        ).all()
        if not job_rows:
            break
        ids = [int(row[0]) for row in job_rows]
        last_id = ids[-1]
        loc_rows = connection.execute(
            text("""
                SELECT job_id, label, country_code, latitude, longitude
                  FROM job_locations
                 WHERE job_id = ANY(:ids)
            """),
            {'ids': ids},
        ).all()
        by_job: dict[int, list[tuple[str | None, str | None, float | None, float | None]]] = {}
        for job_id, label, country_code, latitude, longitude in loc_rows:
            by_job.setdefault(int(job_id), []).append((label, country_code, latitude, longitude))
        payload = []
        for job_id, location_text in job_rows:
            germany_candidate, location_states = compute_job_location_scope(
                location_text, by_job.get(int(job_id), ()),
            )
            payload.append({
                'id': int(job_id),
                'germany_candidate': germany_candidate,
                'location_states': location_states,
            })
        total += apply_scope_updates(connection, payload, target_version)
    return total


def _stale_job_ids(session: Session, batch_size: int, version: str) -> list[int]:
    """Select up to ``batch_size`` stale/uncomputed job IDs via ``ix_jobs_location_scope_version``."""
    from hiring_scraper.app.models import Job

    ids: list[int] = []
    for clause, order_by in (
        (Job.location_scope_version.is_(None), (Job.id.asc(),)),
        (Job.location_scope_version < version, (Job.location_scope_version.asc(), Job.id.asc())),
        (Job.location_scope_version > version, (Job.location_scope_version.asc(), Job.id.asc())),
    ):
        remaining = batch_size - len(ids)
        if remaining <= 0:
            break
        ids.extend(
            int(job_id)
            for job_id in session.scalars(
                select(Job.id).where(clause).order_by(*order_by).limit(remaining)
            ).all()
        )
    return ids


def backfill_job_location_scopes_once(batch_size: int = 500, *, session_factory=None) -> int:
    """Recompute stored location scopes for one bounded batch of NULL/stale jobs."""
    from hiring_scraper.app import database
    from hiring_scraper.app.models import Job, JobLocation

    factory = session_factory or database.SessionLocal
    version = LOCATION_SCOPE_VERSION
    with factory.begin() as session:
        candidate_ids = _stale_job_ids(session, batch_size, version)
        if not candidate_ids:
            return 0
        locked_rows = session.execute(
            select(Job.id, Job.location_text)
            .where(
                Job.id.in_(candidate_ids),
                or_(
                    Job.location_scope_version.is_(None),
                    Job.location_scope_version < version,
                    Job.location_scope_version > version,
                ),
            )
            .order_by(Job.id.asc())
            .with_for_update(key_share=True, skip_locked=True)
        ).all()
        if not locked_rows:
            return 0
        locked_ids = [int(row.id) for row in locked_rows]
        location_rows = session.execute(
            select(
                JobLocation.job_id,
                JobLocation.label,
                JobLocation.country_code,
                JobLocation.latitude,
                JobLocation.longitude,
            ).where(JobLocation.job_id.in_(locked_ids))
        ).all()
        by_job: dict[int, list[tuple[str | None, str | None, float | None, float | None]]] = {}
        for job_id, label, country_code, latitude, longitude in location_rows:
            by_job.setdefault(int(job_id), []).append((label, country_code, latitude, longitude))
        payload = []
        for job_id, location_text in locked_rows:
            germany_candidate, location_states = compute_job_location_scope(
                location_text, by_job.get(int(job_id), ()),
            )
            payload.append({
                'id': int(job_id),
                'germany_candidate': germany_candidate,
                'location_states': location_states,
            })
        return apply_scope_updates(session.connection(), payload, version)
