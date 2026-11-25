"""Freshness, embedding pre-rank, and user-intent gate for local LLM job analysis.

Every enqueue path, startup reconciliation, worker selection, and progress counter
uses these helpers so gated-out postings neither consume LLM time nor inflate
the remaining-work ETA.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta

from sqlalchemy import Integer, and_, cast, exists, func, literal_column, not_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hiring_scraper.app.models import (
    Job, JobAnalysis, JobAnalysisQueue, JobApplication, SemanticSearchDocument, utcnow,
)
from hiring_scraper.job_analysis import VERSION as ANALYSIS_VERSION
from hiring_scraper.matching_v3.profile_text import profile_roles, profile_sources
from hiring_scraper.semantic_matching import MODEL_KEY as EMBEDDING_MODEL_KEY

DEFAULT_MAX_AGE_DAYS = 14
# intfloat/multilingual-e5-small cosine similarities are anisotropic (concentrated
# in ~0.75-0.93). On the public eval set, grade-3 fits score >= 0.831 and grade-2
# fits score >= 0.808, while unrelated German postings cluster around 0.76-0.79.
# 0.80 keeps full recall on relevant roles while dropping the unrelated tail;
# descending similarity order keeps the ~800/day LLM budget on the best candidates.
DEFAULT_MIN_SIMILARITY = 0.80
ISO_DATE_PREFIX_PG = r'^[0-9]{4}-[0-9]{2}-[0-9]{2}'


def max_age_days() -> int:
    raw = os.getenv('RADIUS_ANALYSIS_MAX_AGE_DAYS', str(DEFAULT_MAX_AGE_DAYS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_DAYS
    return max(0, value)


def min_similarity() -> float:
    raw = os.getenv('RADIUS_ANALYSIS_MIN_SIMILARITY', str(DEFAULT_MIN_SIMILARITY)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MIN_SIMILARITY
    return max(-1.0, min(1.0, value))


def fresh_job_clause(max_days: int | None = None):
    """SQL predicate for postings within the freshness window.

    Uses ``jobs.date_posted`` when it starts with ``YYYY-MM-DD`` and compares as
    TEXT against ``to_char(current_date - N, 'YYYY-MM-DD')`` without ever casting
    ``date_posted`` to ``date`` (production contains invalid dates like
    ``2026-98-12``). Falls back to ``jobs.first_seen_at`` otherwise.
    """
    days = max_age_days() if max_days is None else max(0, int(max_days))
    cutoff_ts = utcnow() - timedelta(days=days)
    has_iso = and_(Job.date_posted.is_not(None), Job.date_posted.op('~')(ISO_DATE_PREFIX_PG))
    no_iso = or_(Job.date_posted.is_(None), Job.date_posted.op('!~')(ISO_DATE_PREFIX_PG))
    cutoff_text = func.to_char(literal_column('current_date') - cast(days, Integer), 'YYYY-MM-DD')
    return or_(
        and_(has_iso, func.left(Job.date_posted, 10) >= cutoff_text),
        and_(no_iso, Job.first_seen_at >= cutoff_ts),
    )


def user_intent_job_exists(job_id_col=Job.id):
    """Whether any profile saved this posting or moved it past auto-created ``new``."""
    return exists(select(1).where(
        JobApplication.job_id == job_id_col,
        not_(and_(JobApplication.auto_created.is_(True), JobApplication.status == 'new')),
    ))


def queue_row_eligible_clause(
    *,
    max_days: int | None = None,
    min_sim: float | None = None,
    profile_set_hash: str | None = None,
    require_current_hash: bool = False,
):
    """Whether a ``job_analysis_queue`` row (joined to ``jobs``) passes the analysis gate."""
    threshold = min_similarity() if min_sim is None else float(min_sim)
    override = or_(JobAnalysisQueue.priority.is_(True), user_intent_job_exists(JobAnalysisQueue.job_id))
    scored = [
        fresh_job_clause(max_days=max_days),
        JobAnalysisQueue.similarity.is_not(None),
        JobAnalysisQueue.similarity >= threshold,
    ]
    if require_current_hash:
        if profile_set_hash is None:
            return override
        scored.append(JobAnalysisQueue.profile_set_hash == profile_set_hash)
    return or_(override, and_(*scored))


def enqueue_priority_analysis(session, job_id: int) -> None:
    """Queue a job with priority override so it bypasses age and similarity gates."""
    statement = pg_insert(JobAnalysisQueue).values(
        job_id=job_id,
        queued_at=utcnow(),
        priority=True,
    ).on_conflict_do_update(
        index_elements=['job_id'],
        set_={'priority': True},
        where=JobAnalysisQueue.priority.is_(False),
    )
    session.execute(statement)


def enqueue_priority_if_needed(session, job_id: int, *, is_active: bool) -> bool:
    """Priority-queue an active job unless it already has a current ``ok`` analysis."""
    if not is_active:
        return False
    has_current = session.scalar(select(JobAnalysis.job_id).where(
        JobAnalysis.job_id == job_id,
        JobAnalysis.version == ANALYSIS_VERSION,
        JobAnalysis.status == 'ok',
    )) is not None
    if has_current:
        return False
    enqueue_priority_analysis(session, job_id)
    return True


def profile_embedding_text(preferences: dict) -> str:
    """Compact career-fact query text for embedding a profile with the shared E5 model."""
    parts: list[str] = []
    roles = profile_roles(preferences)
    if roles:
        parts.append('Target roles: ' + ', '.join(roles[:10]))
    skills = [skill.strip() for skill in (preferences.get('skills') or [])
              if isinstance(skill, str) and skill.strip()]
    if skills:
        parts.append('Skills: ' + ', '.join(skills[:30]))
    summary = preferences.get('summary')
    if isinstance(summary, str) and summary.strip():
        parts.append('Summary: ' + ' '.join(summary.split()))
    for source_key, snippet in profile_sources(preferences):
        if source_key in ('summary', 'skills'):
            continue
        clean = ' '.join(snippet.split())
        if clean:
            parts.append(clean)
        if sum(len(part) for part in parts) >= 1000:
            break
    return '. '.join(parts).strip()[:900]


def profile_embedding_hash(text: str) -> str:
    """Content digest matching the E5 ``query:`` representation."""
    return hashlib.sha256(f'query: {text}'.encode('utf-8')).hexdigest()


def current_profile_set_hash(session) -> str | None:
    """Digest of all current profile embeddings, or None when no profile is embedded."""
    rows = session.execute(
        select(SemanticSearchDocument.entity_id, SemanticSearchDocument.content_hash)
        .where(
            SemanticSearchDocument.entity_type == 'profile',
            SemanticSearchDocument.model_revision == EMBEDDING_MODEL_KEY,
        )
        .order_by(SemanticSearchDocument.entity_id)
    ).all()
    if not rows:
        return None
    payload = json.dumps([[entity_id, content_hash] for entity_id, content_hash in rows], separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
