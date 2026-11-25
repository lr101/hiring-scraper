"""Background local-model analysis: job duties/requirements, CV units and pair evidence.

Runs next to the ``llm`` and ``llm-reranker`` containers. Every stage is small
and restartable: results are written per job or pair, so a crash or a model
outage loses at most the item in flight. Jobs inside a profile's search area
(and remote jobs) are analysed first, newest first; the rest follow. Model
calls run outside database transactions, and each result is published only if
its inputs are unchanged.
"""
from __future__ import annotations

import json
import logging
from http.client import HTTPException
import math
import os
import time
from datetime import timedelta
from urllib.request import Request, urlopen

from sqlalchemy import and_, delete, exists, func, not_, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import lazyload, selectinload

from hiring_scraper.app import source_changes
from hiring_scraper.app.analysis_gate import (
    current_profile_set_hash,
    fresh_job_clause,
    min_similarity,
    profile_embedding_hash,
    profile_embedding_text,
    user_intent_job_exists,
)
from hiring_scraper.app.board_scope import geographic_scope
from hiring_scraper.app.database import SessionLocal, engine, log_worker_failure
from hiring_scraper.app.local_llm import ModelRequestRejected, analyse_lines, rerank
from hiring_scraper.app.match_documents import USABLE_ANALYSIS_STATUSES
from hiring_scraper.app.models import (
    Job, JobAnalysis, JobAnalysisQueue, JobApplication, JobLocation, MatchEvidence, MatchEvidenceQueue,
    ProfileEvidenceUnits, SemanticSearchDocument, UserProfile, utcnow,
)
from hiring_scraper.job_analysis import (
    VERSION as ANALYSIS_VERSION, analysis_payload, job_line_entries, job_lines, result_hash, source_hash,
)
from hiring_scraper.matching_v3.evidence import (
    UNITS_VERSION,
    VERSION as EVIDENCE_VERSION,
    compute_evidence,
    embed_units,
    units_hash,
    units_source,
)
from hiring_scraper.semantic_matching import MODEL_KEY as EMBEDDING_MODEL_KEY

LOG = logging.getLogger(__name__)
POLL_SECONDS = max(1.0, float(os.getenv('JOB_ANALYSIS_POLL_SECONDS', '5')))
EVIDENCE_PAIRS_PER_CYCLE = max(1, int(os.getenv('JOB_ANALYSIS_EVIDENCE_PAIRS', '6')))
PRERANK_BATCH_SIZE = max(50, int(os.getenv('JOB_ANALYSIS_PRERANK_BATCH', '500')))
CANDIDATE_WINDOW = 400
MAX_RETRY_HOURS = 24
_DEFAULT_AREA = (49.0068705, 8.4034195, 15.0)
_COUNTRY_LABELS = {'de', 'germany', 'deutschland'}


# --- model access ----------------------------------------------------------------

def embed(texts: list[str], kind: str) -> list[list[float]]:
    """E5 vectors from the semantic search worker (prefixes are added there)."""
    base = os.getenv('SEMANTIC_EMBEDDING_URL', 'http://127.0.0.1:8001/embed').rstrip('/')
    url = base.removesuffix('/embed') + '/embed/batch'
    texts = [text.strip()[:900] or '-' for text in texts]
    request = Request(url, data=json.dumps({'texts': texts, 'kind': kind}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read(4_000_000))
    except (OSError, ValueError, HTTPException) as error:
        raise OSError(f'Local embedding service unavailable: {error}') from error
    if payload.get('model') != EMBEDDING_MODEL_KEY or len(payload.get('embeddings') or []) != len(texts):
        raise OSError('Local embedding service returned an incompatible model or batch')
    return payload['embeddings']


# --- profile inputs ---------------------------------------------------------------

_PROFILE_CACHE: dict[int, tuple[tuple, dict]] = {}


def _profiles(session) -> list[UserProfile]:
    # CV rows load only on a cache miss (parsing a LaTeX CV is the expensive part).
    return session.scalars(select(UserProfile).options(lazyload(UserProfile.cv)).order_by(UserProfile.id)).all()


def _profile_inputs(profile) -> dict:
    """Matching preferences, CV evidence units, and query text, cached per profile revision.

    Every profile or CV edit bumps ``source_version`` (source_changes.profile_changed),
    so idle polls do not re-parse CVs every few seconds.
    """
    stamp = (profile.source_version, str(profile.created_at), str(profile.updated_at),
             json.dumps(profile.preferences or {}, sort_keys=True, default=str))
    cached = _PROFILE_CACHE.get(profile.id)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    preferences = profile.matching_preferences or {}
    source = units_source(preferences)
    embed_text = profile_embedding_text(preferences)
    embed_hash = profile_embedding_hash(embed_text) if embed_text else None
    value = {
        'preferences': preferences,
        'source': source,
        'digest': units_hash(source),
        'embed_text': embed_text,
        'embed_hash': embed_hash,
    }
    _PROFILE_CACHE[profile.id] = (stamp, value)
    return value


# --- scope and priority -------------------------------------------------------------

def _areas(profiles) -> list[tuple[float, float, float, str | None, str]]:
    """Real (non-country-wide) profile search areas used for in-area preference."""
    areas = []
    for profile in profiles:
        area = _profile_inputs(profile)['preferences'].get('search_area')
        if not isinstance(area, dict) or not area:
            continue
        if area.get('search_scope') in ('country', 'state') or area.get('type') in ('country', 'state'):
            continue
        label = (area.get('label') or '').strip().casefold()
        city = area.get('city')
        if not city and label in _COUNTRY_LABELS:
            continue
        if area.get('latitude') is None or area.get('longitude') is None:
            continue
        try:
            latitude = float(area['latitude'])
            longitude = float(area['longitude'])
            radius = float(area.get('radius_km', _DEFAULT_AREA[2]))
        except (TypeError, ValueError):
            continue
        if (latitude == 0.0 and longitude == 0.0) or not (0.0 < radius <= 250.0):
            continue
        areas.append((latitude, longitude, radius, city, area.get('country_code') or 'DE'))
    return areas


def _nearby_condition(areas):
    """Cheap SQL prefilter: remote jobs or a location inside any profile's bounding box."""
    boxes = []
    for latitude, longitude, radius, _city, _country in areas:
        dlat = radius / 111.0
        dlon = radius / max(1.0, 111.0 * math.cos(math.radians(latitude)))
        boxes.append(and_(JobLocation.latitude.between(latitude - dlat, latitude + dlat),
                          JobLocation.longitude.between(longitude - dlon, longitude + dlon)))
    located = exists(select(1).where(JobLocation.job_id == Job.id, or_(*boxes))) if boxes else None
    remote = or_(Job.is_remote.is_(True), Job.work_arrangement == 'remote')
    return or_(remote, located) if located is not None else remote


def _in_scope(job, areas) -> bool:
    return any(geographic_scope(job, lat, lon, radius, city, country)['eligible']
               for lat, lon, radius, city, country in areas)


def next_jobs_to_analyse(session, profiles, limit: int = 1) -> list[Job]:
    """Next queued jobs for the LLM: priority/user-intent first, then gated fresh jobs
    ordered by descending embedding similarity (preferring real local areas when set)."""
    now = utcnow()
    ready = or_(JobAnalysisQueue.retry_at.is_(None), JobAnalysisQueue.retry_at <= now)
    order_priority = (JobAnalysisQueue.similarity.desc().nulls_last(), Job.id.desc())

    # 1. Explicit priority rows (saved, moved stage, or opened on job page) bypass age and similarity.
    found: list[Job] = list(session.scalars(
        select(Job).join(JobAnalysisQueue, JobAnalysisQueue.job_id == Job.id)
        .where(JobAnalysisQueue.priority.is_(True), ready)
        .options(selectinload(Job.locations))
        .order_by(*order_priority)
        .limit(limit)
    ).all())
    if len(found) >= limit:
        return found[:limit]

    # Catch user-intent applications that were queued before their priority flag was promoted.
    seen = {job.id for job in found}
    intent_ids = select(JobApplication.job_id).where(
        not_(and_(JobApplication.auto_created.is_(True), JobApplication.status == 'new'))
    )
    for job in session.scalars(
        select(Job).join(JobAnalysisQueue, JobAnalysisQueue.job_id == Job.id)
        .where(JobAnalysisQueue.priority.is_(False), Job.id.in_(intent_ids), ready)
        .options(selectinload(Job.locations))
        .order_by(*order_priority)
        .limit(limit + len(seen))
    ):
        if job.id not in seen:
            found.append(job)
            seen.add(job.id)
            if len(found) >= limit:
                return found[:limit]

    # 2. Fresh postings scored against current profile embeddings above the similarity threshold.
    profile_hash = current_profile_set_hash(session)
    threshold = min_similarity()
    if profile_hash is None:
        if threshold >= 0.0:
            return found
        scored_base = (
            select(Job).join(JobAnalysisQueue, JobAnalysisQueue.job_id == Job.id)
            .where(ready, fresh_job_clause())
            .options(selectinload(Job.locations))
            .order_by(Job.id.desc())
        )
    else:
        scored_base = (
            select(Job).join(JobAnalysisQueue, JobAnalysisQueue.job_id == Job.id)
            .where(
                JobAnalysisQueue.profile_set_hash == profile_hash,
                JobAnalysisQueue.similarity.is_not(None),
                JobAnalysisQueue.similarity >= threshold,
                ready,
                fresh_job_clause(),
            )
            .options(selectinload(Job.locations))
            .order_by(JobAnalysisQueue.similarity.desc(), Job.id.desc())
        )
    areas = _areas(profiles)
    if areas:
        for job in session.scalars(scored_base.where(_nearby_condition(areas)).limit(CANDIDATE_WINDOW)):
            if job.id not in seen and _in_scope(job, areas):
                found.append(job)
                seen.add(job.id)
                if len(found) >= limit:
                    return found[:limit]
    for job in session.scalars(scored_base.limit(limit + len(seen))):
        if job.id not in seen:
            found.append(job)
            seen.add(job.id)
            if len(found) >= limit:
                break
    return found[:limit]


def _requeue_evidence(statement):
    """Insert pair requests; an existing request gets a new revision, so a worker that is
    still computing the old inputs keeps it queued instead of acknowledging it."""
    return statement.on_conflict_do_update(
        index_elements=['profile_id', 'job_id'],
        set_={'queued_at': func.clock_timestamp(), 'retry_at': None, 'attempts': 0})


def queue_evidence(session, *, job_ids=None, profile_id: int | None = None) -> None:
    """Queue pair evidence for analysed active jobs (all profiles, or one profile)."""
    pairs = (select(UserProfile.id, JobAnalysis.job_id, func.clock_timestamp())
             .select_from(JobAnalysis).join(Job, Job.id == JobAnalysis.job_id).join(UserProfile, true())
             .where(JobAnalysis.status.in_(USABLE_ANALYSIS_STATUSES), Job.is_active.is_(True))
             .order_by(UserProfile.id, JobAnalysis.job_id))
    if job_ids is not None:
        pairs = pairs.where(JobAnalysis.job_id.in_(list(job_ids)))
    if profile_id is not None:
        pairs = pairs.where(UserProfile.id == profile_id)
    session.execute(_requeue_evidence(
        pg_insert(MatchEvidenceQueue).from_select(['profile_id', 'job_id', 'queued_at'], pairs)))


def prune_expired_queue_rows(*, batch_size: int = 5000) -> int:
    """Remove inactive, already-analysed, or age-expired non-priority rows from the queue."""
    with SessionLocal() as session:
        current = exists(select(1).where(
            JobAnalysis.job_id == Job.id,
            JobAnalysis.version == ANALYSIS_VERSION,
            JobAnalysis.status == 'ok',
        ))
        stale_row = or_(
            Job.is_active.is_(False),
            current,
            and_(
                JobAnalysisQueue.priority.is_(False),
                ~user_intent_job_exists(Job.id),
                ~fresh_job_clause(),
            ),
        )
        victims = (
            select(JobAnalysisQueue.job_id)
            .join(Job, Job.id == JobAnalysisQueue.job_id)
            .where(stale_row)
            .limit(batch_size)
        )
        result = session.execute(delete(JobAnalysisQueue).where(JobAnalysisQueue.job_id.in_(victims)))
        session.commit()
        return int(result.rowcount or 0)


def reconcile_queues() -> None:
    """Once per start: prune gated-out queue rows, queue fresh or user-intent active postings
    missing a current analysis, and queue missing pair evidence for analysed postings.

    A one-time pass at startup, not a poll: normal operation only touches queues.
    """
    with SessionLocal() as session:
        # Promote queued postings with user intent so they bypass age/similarity gates.
        session.execute(
            update(JobAnalysisQueue)
            .where(JobAnalysisQueue.priority.is_(False), user_intent_job_exists(JobAnalysisQueue.job_id))
            .values(priority=True)
        )
        session.commit()

    while prune_expired_queue_rows() > 0:
        pass

    with SessionLocal() as session:
        current = exists(select(1).where(JobAnalysis.job_id == Job.id, JobAnalysis.version == ANALYSIS_VERSION,
                                         JobAnalysis.status == 'ok'))
        intent = user_intent_job_exists(Job.id)
        eligible_source = or_(intent, fresh_job_clause())
        session.execute(pg_insert(JobAnalysisQueue).from_select(
            ['job_id', 'queued_at', 'priority'],
            select(Job.id, func.clock_timestamp(), intent).where(
                Job.is_active.is_(True), ~current, eligible_source,
            ),
        ).on_conflict_do_nothing())
        matching = exists(select(1).where(
            MatchEvidence.profile_id == UserProfile.id, MatchEvidence.job_id == JobAnalysis.job_id,
            MatchEvidence.version == EVIDENCE_VERSION, MatchEvidence.analysis_hash == JobAnalysis.result_hash))
        missing = (select(UserProfile.id, JobAnalysis.job_id, func.clock_timestamp())
                   .select_from(JobAnalysis).join(Job, Job.id == JobAnalysis.job_id).join(UserProfile, true())
                   .where(JobAnalysis.status.in_(USABLE_ANALYSIS_STATUSES), Job.is_active.is_(True), ~matching))
        session.execute(pg_insert(MatchEvidenceQueue).from_select(
            ['profile_id', 'job_id', 'queued_at'], missing).on_conflict_do_nothing())
        session.commit()


# --- stage 1: profile evidence units & profile query vectors ----------------------

def refresh_profile_units_once(*, embed_fn=embed) -> bool:
    """Embed one profile's changed CV lines and profile query vector, then queue its evidence."""
    with SessionLocal() as session:
        profiles = _profiles(session)
        stored_units = {row.profile_id: (row.version, row.source_hash) for row in session.execute(
            select(ProfileEvidenceUnits.profile_id, ProfileEvidenceUnits.version, ProfileEvidenceUnits.source_hash))}
        stored_docs = {row.entity_id: row.content_hash for row in session.execute(
            select(SemanticSearchDocument.entity_id, SemanticSearchDocument.content_hash).where(
                SemanticSearchDocument.entity_type == 'profile',
                SemanticSearchDocument.model_revision == EMBEDDING_MODEL_KEY,
            ))}
        work = None
        for profile in profiles:
            inputs = _profile_inputs(profile)
            source, digest = inputs['source'], inputs['digest']
            embed_text, embed_hash = inputs['embed_text'], inputs['embed_hash']
            needs_units = stored_units.get(profile.id) != (UNITS_VERSION, digest)
            needs_doc = stored_docs.get(profile.id) != embed_hash
            if needs_units or needs_doc:
                work = (profile.id, source, digest, embed_text, embed_hash, needs_units, needs_doc)
                break
    if work is None:
        return False
    profile_id, source, digest, embed_text, embed_hash, needs_units, needs_doc = work
    vectors = embed_units(source, embed_fn) if needs_units else None
    profile_vector = embed_fn([embed_text], 'query')[0] if (needs_doc and embed_text) else None
    with SessionLocal() as session:
        profile = session.get(UserProfile, profile_id)
        if profile is None:
            return True
        current_inputs = _profile_inputs(profile)
        if current_inputs['digest'] != digest or current_inputs['embed_hash'] != embed_hash:
            return True  # Edited meanwhile; the next cycle embeds the new version.
        if needs_units and vectors is not None:
            row = session.get(ProfileEvidenceUnits, profile_id) or ProfileEvidenceUnits(profile_id=profile_id)
            row.version, row.source_hash, row.payload, row.vectors = UNITS_VERSION, digest, source, vectors
            row.updated_at = utcnow()
            session.merge(row)
            queue_evidence(session, profile_id=profile_id)
        if needs_doc:
            if profile_vector is not None and embed_hash is not None:
                now = utcnow()
                session.execute(pg_insert(SemanticSearchDocument).values(
                    entity_type='profile',
                    entity_id=profile_id,
                    content_hash=embed_hash,
                    model_revision=EMBEDDING_MODEL_KEY,
                    embedding=profile_vector,
                    indexed_at=now,
                ).on_conflict_do_update(
                    index_elements=['entity_type', 'entity_id'],
                    set_={
                        'content_hash': embed_hash,
                        'model_revision': EMBEDDING_MODEL_KEY,
                        'embedding': profile_vector,
                        'indexed_at': now,
                    },
                ))
            else:
                session.execute(delete(SemanticSearchDocument).where(
                    SemanticSearchDocument.entity_type == 'profile',
                    SemanticSearchDocument.entity_id == profile_id,
                ))
        session.commit()
    if needs_units:
        LOG.info('Embedded %d CV evidence units for profile %d', len(source['units']), profile_id)
    if needs_doc:
        LOG.info('Updated pre-rank embedding for profile %d', profile_id)
    return True


def refresh_prerank_scores_once(*, batch_size: int = PRERANK_BATCH_SIZE) -> int:
    """Compute max profile cosine similarity for a bounded batch of queued postings.

    Postings without a job embedding yet stay unscored until ``semantic_search_worker``
    indexes them; they neither block scored rows nor get dropped.
    """
    with SessionLocal() as session:
        profile_hash = current_profile_set_hash(session)
        if profile_hash is None:
            return 0
        result = session.execute(
            text("""
                WITH to_score AS (
                    SELECT q.job_id,
                           (
                               SELECT MAX(COALESCE(
                                   NULLIF(1.0 - (jd.embedding <=> pd.embedding), 'NaN'::double precision),
                                   0.0
                               ))
                                 FROM semantic_search_documents AS pd
                                WHERE pd.entity_type = 'profile'
                                  AND pd.model_revision = :model
                           ) AS max_sim
                      FROM job_analysis_queue AS q
                      JOIN semantic_search_documents AS jd
                        ON jd.entity_type = 'job'
                       AND jd.entity_id = q.job_id
                       AND jd.model_revision = :model
                     WHERE q.profile_set_hash IS NULL
                        OR q.profile_set_hash < :profile_hash
                        OR q.profile_set_hash > :profile_hash
                     LIMIT :batch_size
                )
                UPDATE job_analysis_queue AS q
                   SET similarity = to_score.max_sim,
                       profile_set_hash = :profile_hash
                  FROM to_score
                 WHERE q.job_id = to_score.job_id
                   AND to_score.max_sim IS NOT NULL
            """),
            {'model': EMBEDDING_MODEL_KEY, 'profile_hash': profile_hash, 'batch_size': batch_size},
        )
        session.commit()
        return int(result.rowcount or 0)


# --- stage 2: job analysis ---------------------------------------------------------

MIN_TRIMMED_LINES = 12


def _job_source(job) -> dict:
    return {'title': job.title, 'description': job.description, 'raw_metadata': job.raw_metadata}


def _analyse_fitting(lines: list[str], scopes: list[str], analyse) -> dict:
    """Analyse, shortening the prompt while the model rejects it as too long."""
    keep = len(lines) - 1
    while True:
        try:
            output = analyse(lines[:keep + 1]) if keep > 0 else ''
        except ModelRequestRejected:
            if keep <= MIN_TRIMMED_LINES:
                raise
            keep = max(MIN_TRIMMED_LINES, keep // 2)
            LOG.info('Posting too long for the model; retrying with %d lines', keep)
            continue
        # Line numbers stay valid: only the tail was cut, and the full lines are stored.
        return analysis_payload(lines, output, scopes)


def _luna_analysis(lines: list[str], scopes: list[str]) -> dict | None:
    """Luna first while the subscription has headroom; None means use the local LLM."""
    from hiring_scraper.app import remote_matching
    if not remote_matching.luna_allowed():
        return None
    try:
        payload = analysis_payload(lines, remote_matching.analyse_lines(lines), scopes)
    except Exception as error:  # Any Luna failure falls back to the local model for this job.
        LOG.info('Luna analysis unavailable, using the local model: %s', str(error)[:200])
        return None
    payload['provider'] = 'luna'
    return payload


def _evidence_with_backup(payload_in, title, source, vectors, embed_fn, rerank_fn) -> dict:
    """Luna first while allowed (not when a test injects the reranker), local reranker as backup."""
    if rerank_fn is rerank:
        from hiring_scraper.app import remote_matching
        if remote_matching.luna_allowed():
            try:
                return remote_matching.compute_evidence(payload_in, title, source)
            except Exception as error:
                LOG.info('Luna evidence unavailable, using the local reranker: %s', str(error)[:200])
    return compute_evidence(payload_in, title, source, vectors, embed=embed_fn, rerank=rerank_fn)


def analyse_next_job(*, analyse=analyse_lines) -> int | None:
    """Analyse the next queued job. Returns its id, or None when the queue is idle."""
    with SessionLocal() as session:
        profiles = _profiles(session)
        jobs = next_jobs_to_analyse(session, profiles)
        if not jobs:
            return None
        job = jobs[0]
        job_id = job.id
        if not job.is_active:
            session.execute(delete(JobAnalysisQueue).where(JobAnalysisQueue.job_id == job_id))
            session.commit()
            return job_id
        entries = job_line_entries(_job_source(job))
        queued_at = session.scalar(select(JobAnalysisQueue.queued_at).where(JobAnalysisQueue.job_id == job_id))
        previous = session.get(JobAnalysis, job_id)
        attempts = previous.attempts if previous is not None and previous.version == ANALYSIS_VERSION else 0
    lines, scopes = [line for line, _ in entries], [scope for _, scope in entries]
    digest = source_hash(lines)
    started = time.monotonic()
    try:
        payload = _luna_analysis(lines, scopes) if analyse is analyse_lines else None
        if payload is None:
            payload = _analyse_fitting(lines, scopes, analyse)
        error = None
    except OSError:
        raise  # Model container down: the caller pauses this stage, the job stays queued.
    except Exception as failure:  # A malformed answer or rejected input must not stop the queue.
        payload, error = None, f'{type(failure).__name__}: {failure}'[:300]
    with SessionLocal() as session:
        # Lock order (job, then its request) matches the crawler, which updates the job
        # row and then re-queues it: an edit either lands before this check or waits
        # until the result is published and then queues the new text.
        job = session.execute(select(Job).where(Job.id == job_id).with_for_update(read=True)).scalar_one_or_none()
        queued = session.execute(select(JobAnalysisQueue).where(JobAnalysisQueue.job_id == job_id)
                                 .with_for_update()).scalar_one_or_none()
        if job is None or queued is None:
            return job_id  # Deleted, or another worker already handled this request.
        if source_hash(job_lines(_job_source(job))) != digest:
            return job_id  # Edited during inference; it stays queued for the new text.
        current_request = queued.queued_at == queued_at
        row = session.get(JobAnalysis, job_id)
        if payload is not None:
            if row is None:
                row = JobAnalysis(job_id=job_id, payload={}, attempts=0)
                session.add(row)
            row.version, row.source_hash, row.status, row.payload = ANALYSIS_VERSION, digest, 'ok', payload
            row.result_hash = result_hash(ANALYSIS_VERSION, payload)
            row.attempts, row.error, row.retry_at = 0, None, None
            if current_request:
                session.delete(queued)  # A newer request (re-queue during inference) stays.
            session.flush()
            queue_evidence(session, job_ids=[job_id])
        else:
            if not current_request:
                return job_id  # A newer request supersedes this failed attempt.
            if row is None:
                row = JobAnalysis(job_id=job_id, payload={}, attempts=0)
                session.add(row)
            row.attempts = attempts + 1
            row.error = error
            row.retry_at = utcnow() + timedelta(hours=min(MAX_RETRY_HOURS, 2 ** min(row.attempts, 5)))
            queued.retry_at = row.retry_at
            # A usable earlier analysis (ok or stale) stays in use until a retry succeeds.
            if row.status not in USABLE_ANALYSIS_STATUSES or not row.payload:
                row.version, row.source_hash, row.status, row.payload = ANALYSIS_VERSION, digest, 'failed', {}
                row.result_hash = ''
        row.analysed_at = utcnow()
        source_changes.analysis_changed(session, [job_id])
        session.commit()
    if payload is not None:
        LOG.info('Analysed job %d in %.0fs: %d duties, %d requirements', job_id, time.monotonic() - started,
                 len(payload['duties']), len(payload['requirements']))
    else:
        LOG.warning('Job analysis for %d failed (%s); retrying later', job_id, error)
    return job_id


# --- stage 3: pair evidence --------------------------------------------------------

def _queued_pairs(session, limit: int, job_ids: list[int] | None = None) -> list[tuple[int, int, object]]:
    """Queued (profile, job, queued_at) pairs whose profile units are current, in-area jobs first."""
    profiles = _profiles(session)
    units = dict(session.execute(select(ProfileEvidenceUnits.profile_id, ProfileEvidenceUnits.source_hash).where(
        ProfileEvidenceUnits.version == UNITS_VERSION)).all())
    now = utcnow()
    pairs: list[tuple[int, int, object]] = []
    for profile in profiles:
        if units.get(profile.id) != _profile_inputs(profile)['digest']:
            continue  # Units are refreshed first; evidence waits for them.
        query = (select(Job, MatchEvidenceQueue.queued_at)
                 .join(MatchEvidenceQueue, MatchEvidenceQueue.job_id == Job.id)
                 .where(MatchEvidenceQueue.profile_id == profile.id,
                        or_(MatchEvidenceQueue.retry_at.is_(None), MatchEvidenceQueue.retry_at <= now))
                 .options(selectinload(Job.locations)).order_by(Job.id.desc()))
        if job_ids is not None:
            query = query.where(Job.id.in_(job_ids))
        areas = _areas([profile])
        rows = ([(job, queued_at) for job, queued_at in session.execute(
            query.where(_nearby_condition(areas)).limit(CANDIDATE_WINDOW)) if _in_scope(job, areas)]
            if areas else [])
        if len(rows) < limit:
            seen = {job.id for job, _ in rows}
            rows += [(job, queued_at) for job, queued_at in session.execute(query.limit(limit + len(seen)))
                     if job.id not in seen]
        pairs.extend((profile.id, job.id, queued_at) for job, queued_at in rows[:limit])
        if len(pairs) >= limit:
            break
    return pairs[:limit]


def _locked_request(session, profile_id: int, job_id: int, queued_at):
    """The pair request, locked, if it is still the revision this worker started from."""
    queued = session.execute(select(MatchEvidenceQueue).where(
        MatchEvidenceQueue.profile_id == profile_id, MatchEvidenceQueue.job_id == job_id).with_for_update()
    ).scalar_one_or_none()
    return queued if queued is not None and queued.queued_at == queued_at else None


def refresh_evidence_once(*, limit: int = EVIDENCE_PAIRS_PER_CYCLE, job_ids: list[int] | None = None,
                          embed_fn=embed, rerank_fn=rerank) -> int:
    """Compute queued pair evidence. Returns the number of pairs handled."""
    with SessionLocal() as session:
        pairs = _queued_pairs(session, limit, job_ids)
    done = 0
    for profile_id, job_id, queued_at in pairs:
        with SessionLocal() as session:
            analysis = session.get(JobAnalysis, job_id)
            unit_row = session.get(ProfileEvidenceUnits, profile_id)
            job = session.get(Job, job_id)
            existing = session.get(MatchEvidence, (profile_id, job_id))
            usable = (analysis is not None and analysis.status in USABLE_ANALYSIS_STATUSES and unit_row is not None
                      and job is not None and job.is_active)
            current = (existing is not None and usable and existing.version == EVIDENCE_VERSION
                       and (existing.analysis_hash, existing.units_hash) == (analysis.result_hash, unit_row.source_hash))
            if not usable or current:
                # Closed postings are re-queued on reopen (source_changes), so dropping is safe.
                queued = _locked_request(session, profile_id, job_id, queued_at)
                if queued is not None:
                    session.delete(queued)
                session.commit()
                done += 1
                continue
            inputs = (analysis.result_hash, unit_row.source_hash)
            payload_in, title = dict(analysis.payload), job.title or ''
            source, vectors = dict(unit_row.payload), bytes(unit_row.vectors)
        try:
            evidence = _evidence_with_backup(payload_in, title, source, vectors, embed_fn, rerank_fn)
        except OSError:
            raise
        except Exception as error:  # A poison pair must not block the queue; retry it later.
            log_worker_failure(LOG, f'Evidence for profile {profile_id} and job {job_id} failed; retrying later', error)
            with SessionLocal() as session:
                queued = _locked_request(session, profile_id, job_id, queued_at)
                if queued is not None:
                    queued.attempts += 1
                    queued.retry_at = utcnow() + timedelta(hours=min(MAX_RETRY_HOURS, 2 ** min(queued.attempts, 5)))
                session.commit()
            continue
        with SessionLocal() as session:
            # A CV or posting change bumps the request revision; publishing then waits for it.
            queued = _locked_request(session, profile_id, job_id, queued_at)
            analysis = session.get(JobAnalysis, job_id)
            unit_row = session.get(ProfileEvidenceUnits, profile_id)
            if (queued is None or analysis is None or unit_row is None
                    or (analysis.result_hash, unit_row.source_hash) != inputs):
                continue
            row = session.get(MatchEvidence, (profile_id, job_id)) or MatchEvidence(profile_id=profile_id, job_id=job_id)
            row.version, row.analysis_hash, row.units_hash = EVIDENCE_VERSION, inputs[0], inputs[1]
            row.payload, row.computed_at = evidence, utcnow()
            session.merge(row)
            session.delete(queued)
            source_changes.analysis_changed(session, [job_id])
            session.commit()
        done += 1
    return done


class _Stage:
    """Independent backoff: an unavailable reranker must not stop job analysis, and the
    other way round."""

    def __init__(self, name: str) -> None:
        self.name, self.paused_until, self.backoff = name, 0.0, POLL_SECONDS

    def run(self, step) -> bool:
        if time.monotonic() < self.paused_until:
            return False
        try:
            worked = bool(step())
        except OSError as error:
            LOG.warning('%s: local model unavailable (%s); pausing %.0fs', self.name, error, self.backoff)
            self.paused_until = time.monotonic() + self.backoff
            self.backoff = min(300.0, self.backoff * 2)
            return False
        self.backoff = POLL_SECONDS
        return worked


_STAGES = {name: _Stage(name) for name in ('profile units', 'evidence', 'job analysis')}
PRUNE_INTERVAL_SECONDS = 3600.0


def run_cycle() -> bool:
    """One bounded pass; returns True when any work was done."""
    worked = _STAGES['profile units'].run(refresh_profile_units_once)
    worked = bool(refresh_prerank_scores_once()) or worked
    worked = _STAGES['evidence'].run(refresh_evidence_once) or worked
    analysed: list[int] = []

    def analyse_step() -> bool:
        job_id = analyse_next_job()
        if job_id is not None:
            analysed.append(job_id)
        return job_id is not None

    if _STAGES['job analysis'].run(analyse_step):
        worked = True
        # New postings become visible fastest when their evidence follows directly.
        _STAGES['evidence'].run(lambda: refresh_evidence_once(limit=50, job_ids=analysed))
    return worked


def _hold_single_worker_lock():
    """One active analysis worker: overlapping instances (a rolling deploy) would repeat
    the same minutes of inference. The session-level lock lives as long as this connection."""
    while True:
        try:
            connection = engine.connect()
            if connection.scalar(text('SELECT pg_try_advisory_lock(85793011, 9)')):
                connection.commit()
                return connection
            connection.close()
            LOG.info('Another job analysis worker is active; waiting')
        except Exception as error:
            log_worker_failure(LOG, 'Could not take the job analysis worker lock; retrying', error)
        time.sleep(30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    LOG.info('job analysis worker started (%s)', ANALYSIS_VERSION)
    lock_connection = _hold_single_worker_lock()  # noqa: F841 - held for the process lifetime
    while True:
        try:
            reconcile_queues()
            break
        except Exception as error:
            log_worker_failure(LOG, 'Could not reconcile job analysis queues; retrying', error)
            time.sleep(POLL_SECONDS)
    next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
    while True:
        try:
            worked = run_cycle()
            if time.monotonic() >= next_prune:
                prune_expired_queue_rows()
                next_prune = time.monotonic() + PRUNE_INTERVAL_SECONDS
        except Exception as error:
            log_worker_failure(LOG, 'Job analysis cycle failed; retrying', error)
            worked = False
        if not worked:
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
