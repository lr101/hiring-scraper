"""Profile-specific application states, separate from the source's job lifecycle."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.orm import joinedload

from hiring_scraper.app.board_scope import expiry, vacancy_keys
from hiring_scraper.app.models import (
    Job, JobApplication, JobFeed, JobVacancyGroup, JobVacancyIdentity, ProfileJobMatch,
)

ApplicationStatus = Literal['new', 'open', 'in_gots_hand', 'not_interested', 'waiting_for_reply',
                            'automated_application', 'interview', 'rejected', 'accepted', 'closed']
APPLICATION_STATUSES = get_args(ApplicationStatus)
APPLIED_STATUSES = ('waiting_for_reply', 'automated_application')


@dataclass(frozen=True)
class CanonicalApplicationState:
    id: int
    profile_id: int
    job_id: int
    vacancy_group_id: int | None
    status: str
    previous_status: str | None
    created_at: datetime
    updated_at: datetime
    updated_rank: float
    match_rank: int
    discovered_rank: float
    posted_rank: str


def canonical_application_states(session, profile_ids, *, vacancy_group_ids=None, job_ids=None):
    """Choose one saved application per profile/vacancy without changing history.

    Only tracked jobs are scanned, in bounded batches. Keep compact identities
    and application state so descriptions are not retained for the whole Journey.
    Capturing state also survives workers replacing automatic application IDs
    between this scan and the bounded page fetch.
    """
    statement = select(JobApplication, ProfileJobMatch.match_score,
                       JobVacancyGroup.vacancy_group_id).outerjoin(ProfileJobMatch, and_(
        ProfileJobMatch.profile_id == JobApplication.profile_id,
        ProfileJobMatch.job_id == JobApplication.job_id,
    )).outerjoin(JobVacancyGroup, JobVacancyGroup.job_id == JobApplication.job_id).where(
        JobApplication.profile_id.in_(profile_ids)).options(
        joinedload(JobApplication.job).joinedload(Job.feed).joinedload(JobFeed.company),
        joinedload(JobApplication.job).selectinload(Job.locations),
    ).order_by(JobApplication.id).execution_options(yield_per=200)
    if vacancy_group_ids is not None or job_ids is not None:
        targets = []
        if vacancy_group_ids:
            targets.append(JobApplication.job_id.in_(select(JobVacancyGroup.job_id).where(
                JobVacancyGroup.vacancy_group_id.in_(vacancy_group_ids))))
        if job_ids:
            targets.append(JobApplication.job_id.in_(job_ids))
        statement = statement.where(or_(*targets) if targets else False)
    groups, seen = {}, {}
    for application, match_score, vacancy_group_id in session.execute(statement):
        job = application.job
        keys = {(application.profile_id, *key) for key in vacancy_keys(job)}
        existing = {seen[key] for key in keys if key in seen}
        group_id = min(existing) if existing else application.id
        updated = application.updated_at
        updated = updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated
        # Explicit stages survive richer or newer automatic matches. Merely
        # viewing another copy (New) must not replace an existing decision;
        # an explicit undo back to New still counts as a saved decision.
        decided = not application.auto_created and (
            application.status != 'new' or application.previous_status is not None)
        quality = (not application.auto_created, decided,
                   updated.timestamp() if not application.auto_created else 0,
                   job.is_active and not expiry(job)['expired'],
                   len(job.description or ''), len(job.locations), -job.id)
        discovered = job.first_seen_at
        discovered = discovered.replace(tzinfo=timezone.utc) if discovered.tzinfo is None else discovered
        state = CanonicalApplicationState(
            id=application.id, profile_id=application.profile_id, job_id=application.job_id,
            vacancy_group_id=vacancy_group_id,
            status=application.status, previous_status=application.previous_status,
            created_at=application.created_at, updated_at=application.updated_at,
            updated_rank=updated.timestamp(), match_rank=match_score if match_score is not None else -1,
            discovered_rank=discovered.timestamp(), posted_rank=job.date_posted or '',
        )
        winner = (state, quality)
        for old_group in existing:
            previous = groups.pop(old_group)
            keys.update(previous['keys'])
            if previous['winner'][1] > winner[1]:
                winner = previous['winner']
        groups[group_id] = {'keys': keys, 'winner': winner}
        for key in keys:
            seen[key] = group_id
    return [group['winner'][0] for group in groups.values()]


def canonical_application_states_for_jobs(session, profile_id, jobs):
    """Map source cards to their saved state, including retained vacancy copies."""
    jobs = list(jobs)
    if not jobs:
        return {}
    if len(jobs) > 200:
        mapped = {}
        for start in range(0, len(jobs), 200):
            mapped.update(canonical_application_states_for_jobs(session, profile_id, jobs[start:start + 200]))
        return mapped
    job_ids = {job.id for job in jobs}
    group_rows = session.execute(select(JobVacancyGroup.job_id, JobVacancyGroup.vacancy_group_id).where(
        JobVacancyGroup.job_id.in_(job_ids))).all()
    groups_by_job = {job_id: {group_id} for job_id, group_id in group_rows}
    groups_by_job.update({job_id: set() for job_id in job_ids if job_id not in groups_by_job})
    group_ids = {group_id for _, group_id in group_rows}
    identity_hashes = {}
    for job in jobs:
        hashes = {hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                  for key in vacancy_keys(job) if key[0] != 'retained-vacancy'}
        identity_hashes[job.id] = hashes
    all_hashes = set().union(*identity_hashes.values()) if identity_hashes else set()
    if all_hashes:
        jobs_by_hash = {}
        for job_id, hashes in identity_hashes.items():
            for key_hash in hashes:
                jobs_by_hash.setdefault(key_hash, []).append(job_id)
        ordered_hashes = sorted(all_hashes)
        for start in range(0, len(ordered_hashes), 500):
            for key_hash, group_id in session.execute(select(
                JobVacancyIdentity.key_hash, JobVacancyIdentity.vacancy_group_id,
            ).where(JobVacancyIdentity.key_hash.in_(ordered_hashes[start:start + 500]))):
                for job_id in jobs_by_hash[key_hash]:
                    groups_by_job[job_id].add(group_id)
                    group_ids.add(group_id)
    states = canonical_application_states(session, [profile_id],
                                          vacancy_group_ids=group_ids, job_ids=job_ids)
    by_job = {state.job_id: state for state in states}
    by_group = {state.vacancy_group_id: state for state in states
                if state.vacancy_group_id is not None}
    return {job.id: (by_job.get(job.id) or next((by_group[group_id] for group_id in groups_by_job[job.id]
                                                if group_id in by_group), None))
            for job in jobs}


def vacancy_group_ids_for_jobs(session, job_ids):
    """Return persisted canonical groups for source job IDs in bounded queries."""
    ids = sorted(set(job_ids))
    groups = {}
    for start in range(0, len(ids), 500):
        groups.update(session.execute(select(
            JobVacancyGroup.job_id, JobVacancyGroup.vacancy_group_id,
        ).where(JobVacancyGroup.job_id.in_(ids[start:start + 500]))).all())
    return groups


def close_job_applications(session, job_ids, now):
    """Apply journey outcomes once, when the source transitions a job to closed."""
    ids = sorted(set(job_ids))
    for start in range(0, len(ids), 500):
        session.execute(update(JobApplication).where(
            JobApplication.job_id.in_(ids[start:start + 500]),
            JobApplication.status.not_in(('accepted', 'rejected', 'closed')),
        ).values(
            previous_status=JobApplication.status,
            status=case((JobApplication.status.in_(APPLIED_STATUSES), 'rejected'), else_='closed'),
            updated_at=now,
        ).execution_options(synchronize_session=False))


class ApplicationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: ApplicationStatus
    action: Literal['stage', 'undo'] = 'stage'


def application_json(application):
    return {'profile_id': application.profile_id, 'job_id': application.job_id,
            'status': application.status, 'previous_status': application.previous_status,
            'created_at': application.created_at.isoformat(),
            'updated_at': application.updated_at.isoformat()}
