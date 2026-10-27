"""Consolidate stored Journey rows without deleting source vacancies or events."""
import hashlib
import json

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.orm import joinedload, selectinload

from hiring_scraper.app.applications import canonical_application_states
from hiring_scraper.app.board_scope import vacancy_keys
from hiring_scraper.app.models import (
    AppDataMigration, Job, JobApplication, JobApplicationDeduplicationArchive,
    JobFeed, JobVacancyIdentity, UserProfile, utcnow,
)


_STATE_FIELDS = ('profile_id', 'job_id', 'status', 'previous_status', 'auto_created', 'created_at', 'updated_at')
MIGRATION_KEY = '0032_application_deduplication'


def migrate_existing_application_duplicates(session):
    """Record completion in the same transaction as the historical cleanup."""
    if session.get(AppDataMigration, MIGRATION_KEY) is not None:
        return
    # Acquire every profile before the shared inventory lock, matching normal
    # publisher lock order even when the migration spans several batches.
    session.execute(select(UserProfile.id).order_by(UserProfile.id).with_for_update()).all()
    job_ids = session.scalars(select(JobApplication.job_id).distinct()).all()
    index_vacancy_identities(session, job_ids)
    consolidate_application_duplicates(session, archive=True)
    session.execute(insert(AppDataMigration).values(key=MIGRATION_KEY, applied_at=utcnow()))


def index_vacancy_identities(session, job_ids):
    """Union retained vacancy keys for changed jobs; return affected group IDs."""
    ids = sorted(set(job_ids))
    if not ids:
        return set()
    if session.get_bind().dialect.name == 'postgresql':
        session.execute(text('SELECT pg_advisory_xact_lock(85793011, 2)'))
    for start in range(0, len(ids), 200):
        jobs = session.scalars(select(Job).where(Job.id.in_(ids[start:start + 200])).options(
            joinedload(Job.feed).joinedload(JobFeed.company), selectinload(Job.locations),
        ).execution_options(populate_existing=True)).all()
        for job in jobs:
            hashes = {hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                      for key in vacancy_keys(job) if key[0] != 'retained-vacancy'}
            existing = session.execute(select(JobVacancyIdentity.key_hash, JobVacancyIdentity.vacancy_group_id)
                                       .where(JobVacancyIdentity.key_hash.in_(hashes))).all()
            groups = {group_id for _, group_id in existing}
            if job.vacancy_group_id is not None:
                groups.add(job.vacancy_group_id)
            group_id = min({job.id, *groups})
            merged = groups - {group_id}
            if merged:
                session.execute(update(Job).where(Job.vacancy_group_id.in_(merged)).values(vacancy_group_id=group_id))
                session.execute(update(JobVacancyIdentity).where(JobVacancyIdentity.vacancy_group_id.in_(merged))
                                .values(vacancy_group_id=group_id))
            session.execute(update(Job).where(Job.id == job.id).values(vacancy_group_id=group_id))
            missing = hashes - {key_hash for key_hash, _ in existing}
            if missing:
                session.execute(insert(JobVacancyIdentity), [
                    {'key_hash': key_hash, 'vacancy_group_id': group_id} for key_hash in missing])
    affected = set()
    for start in range(0, len(ids), 200):
        affected.update(session.scalars(select(Job.vacancy_group_id).where(Job.id.in_(ids[start:start + 200]))))
    return affected - {None}


def consolidate_application_duplicates(session, profile_ids=None, *, archive=False, vacancy_group_ids=None):
    """Archive every historical loser, or retract only redundant automatic New.

    Match writers hold profile locks through publication. Migration callers take
    the same locks here; keep both reads and writes bounded by profile/batch.
    """
    if profile_ids is None:
        profile_ids = session.scalars(select(UserProfile.id).order_by(UserProfile.id)).all()
    ids = sorted(set(profile_ids))
    removed = 0
    for start in range(0, len(ids), 100):
        batch_ids = ids[start:start + 100]
        session.execute(select(UserProfile.id).where(UserProfile.id.in_(batch_ids))
                        .order_by(UserProfile.id).with_for_update()).all()
        group_ids = sorted(set(vacancy_group_ids)) if vacancy_group_ids is not None else None
        batches = [None] if group_ids is None else [group_ids[i:i + 200] for i in range(0, len(group_ids), 200)]
        for group_batch in batches:
            winners = {row.id for row in canonical_application_states(session, batch_ids, vacancy_group_ids=group_batch)}
            cursor = 0
            while True:
                statement = select(JobApplication).where(
                    JobApplication.profile_id.in_(batch_ids), JobApplication.id > cursor)
                if group_batch is not None:
                    statement = statement.where(JobApplication.job.has(Job.vacancy_group_id.in_(group_batch)))
                rows = session.scalars(statement.order_by(JobApplication.id).limit(200)).all()
                if not rows:
                    break
                cursor = rows[-1].id
                losers = [row for row in rows if row.id not in winners and
                          (archive or row.auto_created and row.status == 'new')]
                if archive and losers:
                    session.execute(insert(JobApplicationDeduplicationArchive), [{
                        **{name: getattr(row, name) for name in _STATE_FIELDS},
                        'original_application_id': row.id, 'archived_at': utcnow(),
                    } for row in losers])
                if losers:
                    session.execute(delete(JobApplication).where(JobApplication.id.in_([row.id for row in losers]))
                                    .execution_options(synchronize_session=False))
                    removed += len(losers)
    return removed


def restore_archived_applications(session):
    """Restore missing profile/source states on downgrade; retain later edits."""
    existing = set(session.execute(select(JobApplication.profile_id, JobApplication.job_id)).all())
    cursor = 0
    while True:
        rows = session.scalars(select(JobApplicationDeduplicationArchive).where(
            JobApplicationDeduplicationArchive.id > cursor,
        ).order_by(JobApplicationDeduplicationArchive.id).limit(200)).all()
        if not rows:
            break
        cursor = rows[-1].id
        restored = []
        for row in rows:
            identity = (row.profile_id, row.job_id)
            if identity not in existing:
                restored.append({name: getattr(row, name) for name in _STATE_FIELDS})
                existing.add(identity)
        if restored:
            # Allocate fresh surrogate IDs; old IDs may have been reused.
            session.execute(insert(JobApplication), restored)
