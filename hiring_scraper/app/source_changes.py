"""Explicit bookkeeping after writing source rows.

Source rows (jobs and their locations, feeds, companies, profiles, CVs) are the
truth. Matches, vectors and search snapshots are derived from them. Every write
path declares what it changed before committing::

    reserve_job_source_rows(session, existing_ids)   # lock first, in id order
    ... write jobs ...
    source_changes.jobs_changed(session, written_ids)
    session.commit()

``refresh_application_matches_for_jobs`` declares its jobs, so paths that queue
rematching already comply. A transaction that declared changes publishes one new
search generation as its last step before commit (:func:`finish`); call it
explicitly only to publish earlier in a longer transaction.

Declarations are idempotent and cheap to repeat. Invalidation compares stored
hashes with values recomputed from the database, so it does not depend on ORM
attribute history and works after any number of flushes.

A detector (:func:`_detect`) watches flushes without acting on them. When a
transaction commits tracked changes it did not declare, it raises in strict mode
(``RADIUS_STRICT_SOURCE_CHANGES=1``, set by the test harness) and logs an error
otherwise, naming the undeclared rows. It never repairs them implicitly.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from uuid import uuid4

from sqlalchemy import delete, event, exists, func, inspect, insert, literal, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from hiring_scraper.app.models import (
    Company, Job, JobAnalysis, JobAnalysisQueue, JobFeed, JobLocation, JobSearchState, MatchDirtyJob, MatchEvidence,
    MatchEvidenceQueue,
    ProfileCV, UserProfile, utcnow,
)

LOG = logging.getLogger(__name__)
_DECLARED = 'source_changes_declared'
_DETECTED = 'source_changes_detected'
_DIRTY = 'source_changes_search_dirty'

# Fields whose changes affect search or matching.
TRACKED_FIELDS = {
    Company: {"name", "domain", "state", "country_code", "category", "source"},
    JobFeed: {"company_id", "provider", "tenant", "source_revision"},
    Job: {"feed_id", "external_id", "employer_company_id", "title", "url", "description",
          "location_text", "is_remote", "work_arrangement", "employment_type", "schedule",
          "department", "seniority", "date_posted", "salary", "raw_metadata", "enrichment",
          "first_seen_at", "is_active", "closed_at"},
    JobLocation: {"job_id", "label", "latitude", "longitude", "country_code"},
    UserProfile: {"preferences"},
    ProfileCV: {"extracted_text", "variants"},
    JobAnalysis: {"source_hash", "version", "status", "payload"},
    MatchEvidence: {"version", "analysis_hash", "units_hash", "payload"},
}


def tracked_values(item) -> tuple:
    """The search-relevant fields of a row; compare before and after an edit."""
    return tuple(getattr(item, name) for name in sorted(TRACKED_FIELDS[type(item)]))


def strict() -> bool:
    return os.environ.get('RADIUS_STRICT_SOURCE_CHANGES') == '1'


def _ids(values: Iterable) -> list[int]:
    return sorted({int(value) for value in values if value is not None})


def _declare(session: Session, kind: str, ids: Iterable[int]) -> None:
    session.info.setdefault(_DECLARED, set()).update((kind, value) for value in ids)


def _queue_rematch(session: Session, job_ids) -> None:
    """Queue jobs for the match worker; ``job_ids`` may be a list or a select of ids."""
    statement = pg_insert(MatchDirtyJob).from_select(
        ['job_id', 'revision', 'queued_at'],
        select(Job.id, literal(uuid4().hex), literal(utcnow())).where(Job.id.in_(job_ids)))
    session.execute(statement.on_conflict_do_update(
        index_elements=['job_id'],
        # Keep first-dirty order so frequently updated jobs cannot starve.
        set_={'revision': statement.excluded.revision}))


def queue_stale_rematch(session: Session, job_ids: Iterable[int]) -> None:
    """Queue stale/legacy jobs for rematching without contending on already-dirty rows."""
    ids = _ids(job_ids)
    if not ids:
        return
    statement = pg_insert(MatchDirtyJob).from_select(
        ['job_id', 'revision', 'queued_at'],
        select(Job.id, literal(uuid4().hex), literal(utcnow())).where(Job.id.in_(ids)),
    )
    session.execute(statement.on_conflict_do_nothing(index_elements=['job_id']))


def _mark_stale_analyses(session: Session, jobs: list[Job]) -> None:
    """Flag analyses whose posting text changed. They stay in use until re-analysed."""
    from sqlalchemy import or_

    from hiring_scraper.app.analysis_gate import fresh_job_clause, user_intent_job_exists
    from hiring_scraper.job_analysis import job_lines, source_hash
    stored = {row.job_id: (row.source_hash, row.status) for row in session.execute(select(
        JobAnalysis.job_id, JobAnalysis.source_hash, JobAnalysis.status).where(
        JobAnalysis.job_id.in_([job.id for job in jobs])))}
    stale = [job.id for job in jobs if job.id in stored and stored[job.id][1] == 'ok' and stored[job.id][0] != source_hash(
        job_lines({'title': job.title, 'description': job.description, 'raw_metadata': job.raw_metadata}))]
    if stale:
        session.execute(update(JobAnalysis).where(JobAnalysis.job_id.in_(stale)).values(status='stale'))
    # New, edited or never successfully analysed postings enter the queue only when
    # fresh, user-intent, or already marked priority.
    needed = [job.id for job in jobs if job.is_active and (job.id not in stored or job.id in stale
                                                           or stored[job.id][1] != 'ok')]
    if needed:
        intent = user_intent_job_exists(Job.id)
        already_priority = exists(select(1).where(
            JobAnalysisQueue.job_id == Job.id,
            JobAnalysisQueue.priority.is_(True),
        ))
        eligible = select(Job.id, literal(utcnow()), intent).where(
            Job.id.in_(needed),
            or_(intent, already_priority, fresh_job_clause()),
        )
        statement = pg_insert(JobAnalysisQueue).from_select(['job_id', 'queued_at', 'priority'], eligible)
        # A newer queued_at tells an in-flight analysis of the old text not to dequeue.
        # Reset pre-rank similarity so edited text is re-scored, while preserving priority.
        session.execute(statement.on_conflict_do_update(
            index_elements=['job_id'],
            set_={
                'retry_at': None,
                'queued_at': statement.excluded.queued_at,
                'priority': or_(JobAnalysisQueue.priority, statement.excluded.priority),
                'similarity': None,
                'raw_similarity': None,
                'profile_set_hash': None,
            },
        ))
    # Reopened postings keep their analysis, but their pair evidence may have been
    # dropped while closed: queue pairs without evidence for the current output.
    analysed = [job.id for job in jobs if job.is_active and job.id in stored and job.id not in stale
                and stored[job.id][1] in ('ok', 'stale')]
    if analysed:
        from hiring_scraper.matching_v3.evidence import VERSION as EVIDENCE_VERSION
        matching = exists(select(1).where(
            MatchEvidence.profile_id == UserProfile.id, MatchEvidence.job_id == JobAnalysis.job_id,
            MatchEvidence.version == EVIDENCE_VERSION, MatchEvidence.analysis_hash == JobAnalysis.result_hash))
        missing = (select(UserProfile.id, JobAnalysis.job_id, func.clock_timestamp())
                   .select_from(JobAnalysis).join(UserProfile, true())
                   .where(JobAnalysis.job_id.in_(analysed), ~matching))
        session.execute(pg_insert(MatchEvidenceQueue).from_select(
            ['profile_id', 'job_id', 'queued_at'], missing).on_conflict_do_nothing())


def _bump_profiles(session: Session, profile_ids) -> None:
    """Invalidate in-flight publications for these profiles (see models.hold_profiles)."""
    session.execute(update(UserProfile).where(UserProfile.id.in_(sorted(profile_ids)))
                    .values(source_version=UserProfile.source_version + 1))


def jobs_changed(session: Session, job_ids: Iterable[int]) -> None:
    """Declare created, edited, closed or deleted jobs (including their locations).

    Flags job analyses whose inputs changed, queues the jobs for rematching and marks
    search snapshots stale. Lock the rows first with ``reserve_job_source_rows``
    when writing more than one job.
    """
    ids = _ids(job_ids)
    if not ids:
        return
    session.flush()
    _declare(session, 'job', ids)
    jobs = session.scalars(
        select(Job).where(Job.id.in_(ids)).execution_options(populate_existing=True)
    ).all()
    if jobs:
        _mark_stale_analyses(session, jobs)
        _queue_rematch(session, [job.id for job in jobs])
    session.info[_DIRTY] = True


def feeds_changed(session: Session, feed_ids: Iterable[int]) -> None:
    """Declare feed edits (owner, provider, tenant or source revision)."""
    ids = _ids(feed_ids)
    if not ids:
        return
    session.flush()
    _declare(session, 'feed', ids)
    _queue_rematch(session, select(Job.id).where(Job.feed_id.in_(ids)))
    session.info[_DIRTY] = True


def companies_changed(session: Session, company_ids: Iterable[int]) -> None:
    """Declare company edits that change search facts (name, domain, state, ...)."""
    ids = _ids(company_ids)
    if not ids:
        return
    session.flush()
    _declare(session, 'company', ids)
    feeds = select(JobFeed.id).where(JobFeed.company_id.in_(ids))
    _queue_rematch(session, select(Job.id).where(
        Job.feed_id.in_(feeds) | Job.employer_company_id.in_(ids)))
    session.info[_DIRTY] = True


def profile_changed(
    session: Session,
    profile_id: int,
    *,
    career_facts_changed: bool = False,
    presentation_only: bool = False,
) -> None:
    """Declare edits to a profile's preferences or CV.

    Marks its Journey index stale when ``career_facts_changed``; the job analysis
    worker notices changed CV lines by hash and refreshes the pair evidence. When
    ``presentation_only`` is true (only ``min_match_score`` / ``include_unknown``
    or non-scoring fields changed), bumps ``source_version`` to serialize against
    in-flight workers without touching ``match_index_version``.
    """
    session.flush()
    _declare(session, 'profile', [profile_id])
    # The version bump locks the row until commit; publishers verify the version.
    _bump_profiles(session, [profile_id])
    if presentation_only:
        session.info[_DIRTY] = True
        return
    profile = session.scalar(select(UserProfile).where(UserProfile.id == profile_id)
                             .execution_options(populate_existing=True))
    if profile is None:
        return
    if career_facts_changed:
        profile.match_index_version = None
    session.info[_DIRTY] = True


def analysis_changed(session: Session, job_ids: Iterable[int]) -> None:
    """Declare published job analyses or pair evidence: rematch those jobs."""
    jobs = _ids(job_ids)
    if not jobs:
        return
    session.flush()
    _declare(session, 'job', jobs)
    _queue_rematch(session, jobs)
    session.info[_DIRTY] = True


def finish(session: Session) -> None:
    """Publish a new search generation if anything search-relevant changed.

    Call last, right before commit: it takes the shared generation lock, which
    must not be held while writing source rows.
    """
    session.flush()
    if not session.info.pop(_DIRTY, False):
        return
    connection = session.connection()
    # Serialize sequence allocation with commit, including an empty table.
    # Otherwise a lower sequence ID committing later can miss invalidation.
    connection.execute(text("SELECT pg_advisory_xact_lock(85793011, 2)"))
    connection.execute(insert(JobSearchState).values())
    generation = connection.execute(select(JobSearchState.id).order_by(JobSearchState.id.desc()).limit(1)).scalar_one()
    connection.execute(delete(JobSearchState).where(JobSearchState.id < generation))
    session.info['source_changes_finished'] = True


# --- Detection only: find write paths that forgot to declare -----------------

def _changed(session: Session, item) -> bool:
    fields = TRACKED_FIELDS[type(item)]
    if item in session.new or item in session.deleted:
        return True
    state = inspect(item)
    if type(item) is Job and state.attrs.locations.history.has_changes():
        return True
    return any(state.attrs[name].history.has_changes() for name in fields)


def _from_test_code() -> bool:
    """Whether the innermost non-library caller is test code (fixtures seed rows directly)."""
    import sys
    frame = sys._getframe(2)
    while frame is not None:
        path = frame.f_code.co_filename.replace('\\', '/')
        if '/hiring_scraper/' in path and not path.endswith(('/source_changes.py', '/models.py')):
            return False
        if '/tests/' in path:
            return True
        frame = frame.f_back
    return False


def _detect(session: Session, _flush_context) -> None:
    # Strict mode checks application write paths; test fixtures that insert
    # rows directly simulate existing data and are not write paths.
    if strict() and _from_test_code():
        return
    detected = session.info.setdefault(_DETECTED, set())
    deleted_profiles = {item.id for item in session.deleted if isinstance(item, UserProfile)}
    for item in (*session.new, *session.dirty, *session.deleted):
        if type(item) not in TRACKED_FIELDS or not _changed(session, item):
            continue
        # Evidence deleted with its profile changes no job's inputs.
        if (item in session.deleted and isinstance(item, MatchEvidence)
                and item.profile_id in deleted_profiles):
            continue
        if isinstance(item, Job):
            detected.add(('job', item.id))
        elif isinstance(item, JobLocation):
            detected.add(('job', item.job_id or (item.job.id if item.job is not None else None)))
        elif isinstance(item, JobFeed):
            detected.add(('feed', item.id))
        elif isinstance(item, Company):
            detected.add(('company', item.id))
        elif isinstance(item, UserProfile):
            if item not in session.new:  # a new profile has nothing derived yet
                detected.add(('profile', item.id))
        elif isinstance(item, ProfileCV):
            detected.add(('profile', item.profile_id or (item.profile.id if item.profile is not None else None)))
        elif isinstance(item, (JobAnalysis, MatchEvidence)):
            detected.add(('job', item.job_id))


def _verify(session: Session) -> None:
    detected = {pair for pair in session.info.get(_DETECTED, set()) if pair[1] is not None}
    declared = session.info.get(_DECLARED, set())
    missing = sorted(detected - declared)
    problems = []
    if missing:
        problems.append('undeclared source changes ' + ', '.join(f'{kind}:{key}' for kind, key in missing[:20]))
    if not problems:
        return
    message = 'Derived data may be stale: ' + '; '.join(problems)
    if strict():
        raise RuntimeError(message)
    LOG.error(message)


def _reset(session: Session) -> None:
    for key in (_DECLARED, _DETECTED, _DIRTY, 'source_changes_finished'):
        session.info.pop(key, None)


def _before_commit(session: Session) -> None:
    # SQLAlchemy also fires this when a savepoint is released: verify and
    # publish only at the outer commit.
    if session.in_nested_transaction():
        return
    session.flush()
    _verify(session)
    finish(session)  # only acts when this transaction declared changes


def _transaction_end(session: Session, transaction) -> None:
    # Savepoint rollbacks/releases keep the outer transaction's bookkeeping.
    if transaction.parent is None:
        _reset(session)


event.listen(Session, "after_flush", _detect)
event.listen(Session, "before_commit", _before_commit)
event.listen(Session, "after_transaction_end", _transaction_end)
