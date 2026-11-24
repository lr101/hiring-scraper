"""Precomputed Germany and German-state candidate flags for job search scopes.

Search snapshots and semantic search narrow candidate jobs in SQL before Python
runs exact workplace and remote checks. Evaluating hundreds of ``ILIKE``
patterns against ``jobs.location_text`` and ``job_locations.label`` on every
snapshot forces full inventory scans. This module evaluates the exact same
PostgreSQL ``ILIKE`` pattern, ``country_code``, and bounding-box rules in SQL
for bounded batches of uncomputed or invalidated jobs, stores the result on
``jobs``, and stamps a deterministic version hash derived from the rules so any
place-name or boundary update triggers a safe fallback and bounded backfill.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import re
import unicodedata
from typing import Any

from sqlalchemy import Float, Integer, String, Text, bindparam, delete, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
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


def _compute_location_scope_version() -> str:
    payload = {
        'schema': 2,
        'germany_patterns': sorted(germany_text_patterns()),
        'country_code_patterns': sorted(GERMANY_COUNTRY_CODE_PATTERNS),
        'state_patterns': {
            state: sorted(state_text_patterns(state))
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
    return f'loc-scope-v2-{digest}'


LOCATION_SCOPE_VERSION = _compute_location_scope_version()


@lru_cache(maxsize=1)
def _sql_scope_parameters() -> dict[str, Any]:
    germany_patterns = sorted(germany_text_patterns() | set(GERMANY_COUNTRY_CODE_PATTERNS))
    bounds = germany_bounds()
    de_west, de_south, de_east, de_north = bounds if bounds is not None else (None, None, None, None)
    bounds_by_state = german_state_bounds()
    state_rules = []
    for state in sorted(GERMAN_STATES):
        state_bounds = bounds_by_state.get(state)
        west, south, east, north = state_bounds if state_bounds is not None else (None, None, None, None)
        state_rules.append({
            'state': state,
            'patterns': sorted(state_text_patterns(state)),
            'west': west,
            'south': south,
            'east': east,
            'north': north,
        })
    return {
        'germany_patterns': germany_patterns,
        'de_west': de_west,
        'de_south': de_south,
        'de_east': de_east,
        'de_north': de_north,
        'state_rules': json.dumps(state_rules, ensure_ascii=False, separators=(',', ':')),
    }


_RECOMPUTE_JOB_LOCATION_SCOPES_SQL = text("""
    WITH state_rules(state, patterns, west, south, east, north) AS (
        SELECT state, patterns, west, south, east, north
          FROM jsonb_to_recordset(CAST(:state_rules AS jsonb))
            AS r(
                state text,
                patterns text[],
                west double precision,
                south double precision,
                east double precision,
                north double precision
            )
    ),
    cleared AS (
        DELETE FROM job_location_scope_dirty WHERE job_id = ANY(:job_ids)
    )
    UPDATE jobs AS j
       SET germany_candidate = (
               COALESCE(j.location_text ILIKE ANY(:germany_patterns), FALSE)
               OR EXISTS (
                   SELECT 1
                     FROM job_locations AS jl
                    WHERE jl.job_id = j.id
                      AND (
                          lower(jl.country_code) = 'de'
                          OR jl.label ILIKE ANY(:germany_patterns)
                          OR (
                              jl.latitude BETWEEN :de_south AND :de_north
                              AND jl.longitude BETWEEN :de_west AND :de_east
                          )
                      )
               )
           ),
           location_states = ARRAY(
                   SELECT sr.state
                     FROM state_rules AS sr
                    WHERE COALESCE(j.location_text ILIKE ANY(sr.patterns), FALSE)
                       OR EXISTS (
                           SELECT 1
                             FROM job_locations AS jl
                            WHERE jl.job_id = j.id
                              AND (
                                  jl.label ILIKE ANY(sr.patterns)
                                  OR (
                                      jl.latitude BETWEEN sr.south AND sr.north
                                      AND jl.longitude BETWEEN sr.west AND sr.east
                                  )
                              )
                       )
                    ORDER BY sr.state
           ),
           location_scope_version = :version
     WHERE j.id = ANY(:job_ids)
""").bindparams(
    bindparam('germany_patterns', type_=ARRAY(Text)),
    bindparam('de_west', type_=Float),
    bindparam('de_south', type_=Float),
    bindparam('de_east', type_=Float),
    bindparam('de_north', type_=Float),
    bindparam('state_rules', type_=Text),
    bindparam('version', type_=String),
    bindparam('job_ids', type_=ARRAY(Integer)),
)


def _stale_job_ids(session: Session, batch_size: int, version: str, *, exclude: list[int]) -> list[int]:
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
        statement = select(Job.id).where(clause).order_by(*order_by).limit(remaining)
        if exclude:
            statement = statement.where(Job.id.not_in(exclude))
        ids.extend(session.scalars(statement))
    return ids


def dirty_job_location_scope_exists():
    from hiring_scraper.app.models import Job, JobLocationScopeDirty

    return select(1).where(JobLocationScopeDirty.job_id == Job.id).exists()


def backfill_job_location_scopes_once(batch_size: int = 500, *, session_factory=None) -> int:
    """Atomically lock and recompute stored location scopes in SQL for one bounded batch."""
    from hiring_scraper.app import database
    from hiring_scraper.app.models import Job, JobLocationScopeDirty

    factory = session_factory or database.SessionLocal
    version = LOCATION_SCOPE_VERSION
    with factory.begin() as session:
        dirty_ids = list(session.scalars(
            select(JobLocationScopeDirty.job_id).distinct().order_by(JobLocationScopeDirty.job_id).limit(batch_size)
        ))
        candidate_ids = dirty_ids + _stale_job_ids(session, batch_size - len(dirty_ids), version, exclude=dirty_ids)
        if not candidate_ids:
            return 0
        locked_ids = list(session.scalars(
            select(Job.id)
            .where(
                Job.id.in_(candidate_ids),
                or_(Job.location_scope_version.is_distinct_from(version), dirty_job_location_scope_exists()),
            )
            .order_by(Job.id.asc())
            .with_for_update(key_share=True, skip_locked=True)
        ))
        if dirty_ids:
            # Cascaded location deletes can mark jobs that no longer exist.
            session.execute(delete(JobLocationScopeDirty).where(
                JobLocationScopeDirty.job_id.in_(dirty_ids),
                ~select(1).where(Job.id == JobLocationScopeDirty.job_id).exists(),
            ))
        if not locked_ids:
            return 0
        # The UPDATE and its DELETE CTE share one PostgreSQL statement snapshot.
        # A child edit invisible to the recompute also keeps its independent mark.
        result = session.execute(_RECOMPUTE_JOB_LOCATION_SCOPES_SQL, {
            **_sql_scope_parameters(),
            'version': version,
            'job_ids': locked_ids,
        })
        return result.rowcount
