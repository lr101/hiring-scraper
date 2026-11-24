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
import math
import os
import time
from datetime import timedelta
from urllib.request import Request, urlopen

from sqlalchemy import and_, delete, exists, func, or_, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from hiring_scraper.app import source_changes
from hiring_scraper.app.board_scope import geographic_scope
from hiring_scraper.app.database import SessionLocal, log_worker_failure
from hiring_scraper.app.local_llm import analyse_lines, rerank
from hiring_scraper.app.match_documents import USABLE_ANALYSIS_STATUSES
from hiring_scraper.app.models import (
    Job, JobAnalysis, JobAnalysisQueue, JobLocation, MatchEvidence, MatchEvidenceQueue, ProfileEvidenceUnits,
    UserProfile, utcnow,
)
from hiring_scraper.job_analysis import VERSION as ANALYSIS_VERSION, analysis_payload, job_lines, source_hash
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
CANDIDATE_WINDOW = 400
MAX_RETRY_HOURS = 24
_DEFAULT_AREA = (49.0068705, 8.4034195, 15.0)


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
    except (OSError, ValueError) as error:
        raise OSError(f'Local embedding service unavailable: {error}') from error
    if payload.get('model') != EMBEDDING_MODEL_KEY or len(payload.get('embeddings') or []) != len(texts):
        raise OSError('Local embedding service returned an incompatible model or batch')
    return payload['embeddings']


# --- scope and priority -------------------------------------------------------------

def _areas(profiles) -> list[tuple[float, float, float, str | None, str]]:
    areas = []
    for profile in profiles:
        area = (profile.matching_preferences or {}).get('search_area') or {}
        try:
            latitude = float(area.get('latitude', _DEFAULT_AREA[0]))
            longitude = float(area.get('longitude', _DEFAULT_AREA[1]))
            radius = float(area.get('radius_km', _DEFAULT_AREA[2]))
        except (TypeError, ValueError):
            latitude, longitude, radius = _DEFAULT_AREA
        areas.append((latitude, longitude, radius, area.get('city'), area.get('country_code') or 'DE'))
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
    """Queued jobs, in-area first (newest first), skipping ones waiting for a retry."""
    now = utcnow()
    areas = _areas(profiles)
    base = (select(Job).join(JobAnalysisQueue, JobAnalysisQueue.job_id == Job.id)
            .where(or_(JobAnalysisQueue.retry_at.is_(None), JobAnalysisQueue.retry_at <= now))
            .options(selectinload(Job.locations)).order_by(Job.id.desc()))
    found: list[Job] = []
    if areas:
        for job in session.scalars(base.where(_nearby_condition(areas)).limit(CANDIDATE_WINDOW)):
            if _in_scope(job, areas):
                found.append(job)
                if len(found) >= limit:
                    return found
    seen = {job.id for job in found}
    for job in session.scalars(base.limit(limit + len(seen))):
        if job.id not in seen:
            found.append(job)
            if len(found) >= limit:
                break
    return found


def queue_evidence(session, *, job_ids=None, profile_id: int | None = None) -> None:
    """Queue pair evidence for analysed active jobs (all profiles, or one profile)."""
    pairs = (select(UserProfile.id, JobAnalysis.job_id, func.now())
             .select_from(JobAnalysis).join(Job, Job.id == JobAnalysis.job_id).join(UserProfile, true())
             .where(JobAnalysis.status.in_(USABLE_ANALYSIS_STATUSES), Job.is_active.is_(True)))
    if job_ids is not None:
        pairs = pairs.where(JobAnalysis.job_id.in_(list(job_ids)))
    if profile_id is not None:
        pairs = pairs.where(UserProfile.id == profile_id)
    session.execute(pg_insert(MatchEvidenceQueue).from_select(['profile_id', 'job_id', 'queued_at'], pairs)
                    .on_conflict_do_nothing())


def reconcile_queues() -> None:
    """Once per start: queue work left behind by model or prompt version changes.

    A one-time pass at startup, not a poll: normal operation only touches queues.
    """
    with SessionLocal() as session:
        current = exists(select(1).where(JobAnalysis.job_id == Job.id, JobAnalysis.version == ANALYSIS_VERSION,
                                         JobAnalysis.status.in_(USABLE_ANALYSIS_STATUSES)))
        session.execute(pg_insert(JobAnalysisQueue).from_select(
            ['job_id', 'queued_at'], select(Job.id, func.now()).where(Job.is_active.is_(True), ~current))
            .on_conflict_do_nothing())
        stale_evidence = (select(MatchEvidence.profile_id, MatchEvidence.job_id, func.now())
                          .where(MatchEvidence.version != EVIDENCE_VERSION))
        session.execute(pg_insert(MatchEvidenceQueue).from_select(
            ['profile_id', 'job_id', 'queued_at'], stale_evidence).on_conflict_do_nothing())
        session.commit()


# --- stage 1: profile evidence units ----------------------------------------------

def refresh_profile_units_once(*, embed_fn=embed) -> bool:
    """Embed one profile's changed CV lines, then queue its evidence for every analysed job."""
    with SessionLocal() as session:
        profiles = session.scalars(select(UserProfile).order_by(UserProfile.id)).all()
        stored = {row.profile_id: (row.version, row.source_hash) for row in session.execute(
            select(ProfileEvidenceUnits.profile_id, ProfileEvidenceUnits.version, ProfileEvidenceUnits.source_hash))}
        work = None
        for profile in profiles:
            source = units_source(profile.matching_preferences)
            digest = units_hash(source)
            if stored.get(profile.id) != (UNITS_VERSION, digest):
                work = (profile.id, source, digest)
                break
    if work is None:
        return False
    profile_id, source, digest = work
    vectors = embed_units(source, embed_fn)
    with SessionLocal() as session:
        profile = session.get(UserProfile, profile_id)
        if profile is None or units_hash(units_source(profile.matching_preferences)) != digest:
            return True  # Edited meanwhile; the next cycle embeds the new version.
        row = session.get(ProfileEvidenceUnits, profile_id) or ProfileEvidenceUnits(profile_id=profile_id)
        row.version, row.source_hash, row.payload, row.vectors = UNITS_VERSION, digest, source, vectors
        row.updated_at = utcnow()
        session.merge(row)
        queue_evidence(session, profile_id=profile_id)
        session.commit()
    LOG.info('Embedded %d CV evidence units for profile %d', len(source['units']), profile_id)
    return True


# --- stage 2: job analysis ---------------------------------------------------------

def _job_source(job) -> dict:
    return {'title': job.title, 'description': job.description, 'raw_metadata': job.raw_metadata}


def analyse_next_job(*, analyse=analyse_lines) -> int | None:
    """Analyse the next queued job. Returns its id, or None when the queue is idle."""
    with SessionLocal() as session:
        profiles = session.scalars(select(UserProfile)).all()
        jobs = next_jobs_to_analyse(session, profiles)
        if not jobs:
            return None
        job = jobs[0]
        job_id = job.id
        if not job.is_active:
            session.execute(delete(JobAnalysisQueue).where(JobAnalysisQueue.job_id == job_id))
            session.commit()
            return job_id
        lines = job_lines(_job_source(job))
        queued_at = session.scalar(select(JobAnalysisQueue.queued_at).where(JobAnalysisQueue.job_id == job_id))
        previous = session.get(JobAnalysis, job_id)
        attempts = previous.attempts if previous is not None and previous.version == ANALYSIS_VERSION else 0
    digest = source_hash(lines)
    started = time.monotonic()
    try:
        payload = analysis_payload(lines, analyse(lines)) if len(lines) > 1 else analysis_payload(lines, '')
        error = None
    except OSError:
        raise  # Model container down: the caller backs off, the job stays queued.
    except Exception as failure:  # A malformed answer must not stop the queue.
        payload, error = None, f'{type(failure).__name__}: {failure}'[:300]
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return job_id
        if source_hash(job_lines(_job_source(job))) != digest:
            return job_id  # Edited during inference; it stays queued for the new text.
        row = session.get(JobAnalysis, job_id)
        if row is None:
            row = JobAnalysis(job_id=job_id, payload={}, attempts=0)
            session.add(row)
        queued = session.get(JobAnalysisQueue, job_id)
        if payload is not None:
            row.version, row.source_hash, row.status, row.payload = ANALYSIS_VERSION, digest, 'ok', payload
            row.attempts, row.error, row.retry_at = 0, None, None
            if queued is not None and queued.queued_at == queued_at:
                session.delete(queued)  # A re-queue during inference keeps its entry.
            session.flush()
            queue_evidence(session, job_ids=[job_id])
        else:
            row.attempts = attempts + 1
            row.error = error
            row.retry_at = utcnow() + timedelta(hours=min(MAX_RETRY_HOURS, 2 ** min(row.attempts, 5)))
            if queued is not None:
                queued.retry_at = row.retry_at
            if row.status != 'stale' or not row.payload:
                row.version, row.source_hash, row.status, row.payload = ANALYSIS_VERSION, digest, 'failed', {}
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
    profiles = session.scalars(select(UserProfile).order_by(UserProfile.id)).all()
    units = dict(session.execute(select(ProfileEvidenceUnits.profile_id, ProfileEvidenceUnits.source_hash).where(
        ProfileEvidenceUnits.version == UNITS_VERSION)).all())
    pairs: list[tuple[int, int, object]] = []
    for profile in profiles:
        if units.get(profile.id) != units_hash(units_source(profile.matching_preferences)):
            continue  # Units are refreshed first; evidence waits for them.
        query = (select(Job, MatchEvidenceQueue.queued_at)
                 .join(MatchEvidenceQueue, MatchEvidenceQueue.job_id == Job.id)
                 .where(MatchEvidenceQueue.profile_id == profile.id)
                 .options(selectinload(Job.locations)).order_by(Job.id.desc()))
        if job_ids is not None:
            query = query.where(Job.id.in_(job_ids))
        areas = _areas([profile])
        rows = [(job, queued_at) for job, queued_at in session.execute(
            query.where(_nearby_condition(areas)).limit(CANDIDATE_WINDOW)) if _in_scope(job, areas)]
        if len(rows) < limit:
            seen = {job.id for job, _ in rows}
            rows += [(job, queued_at) for job, queued_at in session.execute(query.limit(limit + len(seen)))
                     if job.id not in seen]
        pairs.extend((profile.id, job.id, queued_at) for job, queued_at in rows[:limit])
        if len(pairs) >= limit:
            break
    return pairs[:limit]


def _dequeue(session, profile_id: int, job_id: int, queued_at) -> None:
    # A newer request (same key, later queued_at) stays queued.
    session.execute(delete(MatchEvidenceQueue).where(
        MatchEvidenceQueue.profile_id == profile_id, MatchEvidenceQueue.job_id == job_id,
        MatchEvidenceQueue.queued_at == queued_at))


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
                       and (existing.analysis_hash, existing.units_hash) == (analysis.source_hash, unit_row.source_hash))
            if not usable or current:
                _dequeue(session, profile_id, job_id, queued_at)
                session.commit()
                done += 1
                continue
            inputs = (analysis.source_hash, unit_row.source_hash)
            payload_in, title = dict(analysis.payload), job.title or ''
            source, vectors = dict(unit_row.payload), bytes(unit_row.vectors)
        try:
            evidence = compute_evidence(payload_in, title, source, vectors, embed=embed_fn, rerank=rerank_fn)
        except OSError:
            raise
        except Exception as error:  # A poison pair must not block the queue.
            log_worker_failure(LOG, f'Evidence for profile {profile_id} and job {job_id} failed; skipped', error)
            with SessionLocal() as session:
                _dequeue(session, profile_id, job_id, queued_at)
                session.commit()
            continue
        with SessionLocal() as session:
            analysis = session.get(JobAnalysis, job_id)
            unit_row = session.get(ProfileEvidenceUnits, profile_id)
            if analysis is None or unit_row is None or (analysis.source_hash, unit_row.source_hash) != inputs:
                continue  # Inputs changed during inference; the change queued a fresh request.
            row = session.get(MatchEvidence, (profile_id, job_id)) or MatchEvidence(profile_id=profile_id, job_id=job_id)
            row.version, row.analysis_hash, row.units_hash = EVIDENCE_VERSION, inputs[0], inputs[1]
            row.payload, row.computed_at = evidence, utcnow()
            session.merge(row)
            _dequeue(session, profile_id, job_id, queued_at)
            source_changes.analysis_changed(session, [job_id])
            session.commit()
        done += 1
    return done


def run_cycle() -> bool:
    """One bounded pass; returns True when any work was done."""
    worked = refresh_profile_units_once()
    worked = bool(refresh_evidence_once()) or worked
    job_id = analyse_next_job()
    if job_id is not None:
        # New postings become visible fastest when their evidence follows directly.
        refresh_evidence_once(limit=50, job_ids=[job_id])
        worked = True
    return worked


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    LOG.info('job analysis worker started (%s)', ANALYSIS_VERSION)
    while True:
        try:
            reconcile_queues()
            break
        except Exception as error:
            log_worker_failure(LOG, 'Could not reconcile job analysis queues; retrying', error)
            time.sleep(POLL_SECONDS)
    backoff = POLL_SECONDS
    while True:
        try:
            worked = run_cycle()
            backoff = POLL_SECONDS
        except OSError as error:
            # Model or embedding containers are down or still loading: wait and retry.
            LOG.warning('Local models unavailable (%s); retrying in %.0fs', error, backoff)
            worked = False
            time.sleep(backoff)
            backoff = min(300.0, backoff * 2)
        except Exception as error:
            log_worker_failure(LOG, 'Job analysis cycle failed; retrying', error)
            worked = False
            time.sleep(POLL_SECONDS)
        if not worked:
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
