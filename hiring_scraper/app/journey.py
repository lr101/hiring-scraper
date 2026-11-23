"""Journey: per-profile application stages, the application list and momentum.

Stage changes record momentum events once per vacancy, so weekly goals,
streaks and rewards never double count an application.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from hiring_scraper.app import search_snapshots
from hiring_scraper.app.application_deduplication import index_vacancy_identities
from hiring_scraper.app.applications import (
    APPLIED_STATUSES, APPLICATION_STATUSES, ApplicationRequest, ApplicationStatus, application_json,
    canonical_application_states, canonical_application_states_for_jobs,
)
from hiring_scraper.app.database import dialect_insert, get_session
from hiring_scraper.app.job_search import (
    profile_match_index_stale,
)
from hiring_scraper.app.models import (
    ApplicationMomentumEvent, Job, JobApplication, JobFeed, JobVacancyGroup, UserProfile, hold_profiles, utcnow,
)
from hiring_scraper.app.profiles import require_profile

router = APIRouter()
APPLICATION_WEEKLY_GOAL = max(1, int(os.getenv("APPLICATION_WEEKLY_GOAL", "10")))


def _application_momentum_json(session: Session) -> dict[str, int]:
    now = utcnow()
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    weekly_jobs: dict[datetime, set[int]] = {}
    lifetime_jobs: set[int] = set()
    logical_job_id = func.coalesce(JobVacancyGroup.vacancy_group_id, Job.id)
    applied_events = session.execute(select(
        logical_job_id,
        ApplicationMomentumEvent.occurred_at,
        ApplicationMomentumEvent.is_legacy,
    ).select_from(ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id)
        .outerjoin(JobVacancyGroup, JobVacancyGroup.job_id == Job.id)
        .where(ApplicationMomentumEvent.event_type == 'applied')
        .order_by(ApplicationMomentumEvent.occurred_at)).all()
    counted_weekly_jobs: set[int] = set()
    for job_id, occurred_at, is_legacy in applied_events:
        lifetime_jobs.add(job_id)
        if is_legacy or job_id in counted_weekly_jobs:
            continue
        counted_weekly_jobs.add(job_id)
        occurred_at = occurred_at.replace(tzinfo=timezone.utc) if occurred_at.tzinfo is None else occurred_at.astimezone(timezone.utc)
        event_week = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=occurred_at.weekday())
        weekly_jobs.setdefault(event_week, set()).add(job_id)

    weekly_goal = APPLICATION_WEEKLY_GOAL
    applied_this_week = len(weekly_jobs.get(week_start, set()))
    successful_weeks = 0
    streak_week = week_start
    if len(weekly_jobs.get(streak_week, set())) < weekly_goal:
        streak_week -= timedelta(days=7)
    while len(weekly_jobs.get(streak_week, set())) >= weekly_goal:
        successful_weeks += 1
        streak_week -= timedelta(days=7)

    interview_count = session.scalar(select(func.count(func.distinct(logical_job_id))).select_from(
        ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id).outerjoin(
            JobVacancyGroup, JobVacancyGroup.job_id == Job.id).where(
            ApplicationMomentumEvent.event_type == 'interview',
        )) or 0
    jobs_won = session.scalar(select(func.count(func.distinct(logical_job_id))).select_from(
        ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id).outerjoin(
            JobVacancyGroup, JobVacancyGroup.job_id == Job.id).where(
            ApplicationMomentumEvent.event_type == 'accepted',
        )) or 0
    return {
        'weekly_goal': weekly_goal,
        'applied_this_week': applied_this_week,
        'lifetime_applied': len(lifetime_jobs),
        'successful_weeks': successful_weeks,
        'interview_count': interview_count,
        'jobs_won': jobs_won,
    }


@router.get('/api/v1/application-momentum')
def get_application_momentum(session: Session = Depends(get_session)) -> dict[str, int]:
    return _application_momentum_json(session)


@router.put('/api/v1/profiles/{profile_id}/applications/{job_id}')
def set_job_application(profile_id: int, job_id: int, request: ApplicationRequest,
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    require_profile(profile_id, session)
    # Index in its own transaction before taking row locks, then release the
    # identity union guard before holding the profile like a publisher does.
    index_vacancy_identities(session, [job_id])
    session.commit()
    # Serialize tracking with source closure before reading the posting state.
    # Only application state changes; NO KEY UPDATE serializes source closure
    # and allows a concurrent match-index foreign-key check to finish.
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update(key_share=True)
                         .execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(status_code=404, detail='Job not found')
    if not hold_profiles(session, [profile_id]):
        raise HTTPException(status_code=404, detail='Profile not found')
    canonical_application = canonical_application_states_for_jobs(session, profile_id, [job]).get(job.id)
    previous_status = canonical_application.status if canonical_application else None
    requested_status = request.status
    # Opening a retained source copy can submit its default New state. Keep an
    # existing decision unless the request explicitly undoes it.
    if (canonical_application is not None and canonical_application.job_id != job.id
            and request.status == 'new' and request.action != 'undo'
            and previous_status != 'new'):
        session.commit()
        return application_json(canonical_application)
    target_job_id = canonical_application.job_id if canonical_application else job.id
    if not job.is_active:
        if request.status == 'new' and previous_status in ('accepted', 'rejected', 'closed'):
            target_status = previous_status
        elif request.status in ('accepted', 'rejected'):
            target_status = request.status
        else:
            target_status = 'rejected' if request.status in APPLIED_STATUSES else 'closed'
        request = request.model_copy(update={'status': target_status})
    # Atomic upsert makes repeat saves and simultaneous requests safe on both stores.
    insert = dialect_insert(session)
    now = utcnow()
    statement = insert(JobApplication).values(profile_id=profile_id, job_id=target_job_id,
        status=request.status, previous_status=None, auto_created=False,
        created_at=now, updated_at=now).on_conflict_do_update(
            index_elements=['profile_id', 'job_id'], set_={
                'status': request.status,
                'auto_created': False,
                'previous_status': case((JobApplication.status != request.status, JobApplication.status),
                                        else_=JobApplication.previous_status),
                'updated_at': now,
            })
    session.execute(statement)
    momentum_event_type = {
        'waiting_for_reply': 'applied',
        'automated_application': 'applied',
        'interview': 'interview',
        'accepted': 'accepted',
    }.get(requested_status)
    if request.action == 'stage' and momentum_event_type and previous_status != requested_status:
        already_counted = session.scalar(select(ApplicationMomentumEvent.id).where(
            ApplicationMomentumEvent.profile_id == profile_id,
            or_(ApplicationMomentumEvent.job_id == job.id,
                and_(job.vacancy_group_id is not None, ApplicationMomentumEvent.job_id.in_(
                    select(JobVacancyGroup.job_id).where(
                        JobVacancyGroup.vacancy_group_id == job.vacancy_group_id)))),
            ApplicationMomentumEvent.event_type == momentum_event_type,
        ).limit(1)) is not None
        if not already_counted:
            session.add(ApplicationMomentumEvent(
                profile_id=profile_id,
                job_id=target_job_id,
                event_type=momentum_event_type,
                occurred_at=now,
            ))
    session.commit()
    application = session.scalar(select(JobApplication).where(
        JobApplication.profile_id == profile_id, JobApplication.job_id == target_job_id)
        .execution_options(populate_existing=True))
    return application_json(application)


@router.get('/api/v1/profiles/{profile_id}/applications')
def list_job_applications(profile_id: int,
                          status: ApplicationStatus | None = None,
                          offset: Annotated[int, Query(ge=0)] = 0,
                          limit: Annotated[int, Query(ge=1, le=100)] = 40,
                          session: Session = Depends(get_session)) -> dict[str, Any]:
    require_profile(profile_id, session)
    canonical_states = canonical_application_states(session, [profile_id])
    counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    for application in canonical_states:
        counts[application.status] += 1
    applications = sorted((application for application in canonical_states
                           if status is None or application.status == status),
                          key=lambda row: (row.updated_rank, row.id), reverse=True)[offset:offset + limit]
    jobs = session.scalars(select(Job).where(Job.id.in_({row.job_id for row in applications})).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations))).unique().all()
    jobs_by_id = {job.id: job for job in jobs}
    items = []
    for application in applications:
        item = search_snapshots.job_json(jobs_by_id[application.job_id])
        item['application'] = application_json(application)
        items.append(item)
    return {'profile_id': profile_id, 'items': items, 'counts': counts,
            'total': counts[status] if status is not None else sum(counts.values()),
            'offset': offset, 'limit': limit}


@router.get('/api/v1/applications')
def list_applications(profile_ids: str = Query(..., max_length=1200),
                      statuses: str | None = Query(None, max_length=160),
                      sort: Literal['match', 'posted', 'discovered'] = 'match',
                      offset: Annotated[int, Query(ge=0)] = 0,
                      limit: Annotated[int, Query(ge=1, le=100)] = 40,
                      session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        selected_ids = list(dict.fromkeys(int(value) for value in profile_ids.split(',') if value))
    except ValueError as error:
        raise HTTPException(status_code=422, detail='profile_ids must be a comma-separated list of IDs') from error
    if not selected_ids or len(selected_ids) > 100 or any(value < 1 for value in selected_ids):
        raise HTTPException(status_code=422, detail='Select between 1 and 100 valid profiles')
    profiles = [require_profile(value, session) for value in selected_ids]
    selected_statuses: list[str] = []
    if statuses and statuses != 'all':
        selected_statuses = list(dict.fromkeys(value for value in statuses.split(',') if value))
        if not selected_statuses or any(value not in APPLICATION_STATUSES for value in selected_statuses):
            raise HTTPException(status_code=422, detail='statuses contains an unknown application state')

    # Reads never scan the inventory or match synchronously: the profile match
    # worker backfills stale profiles and drains changed jobs, while saved
    # application stages stay usable and the page reports pending work.
    from hiring_scraper.app.models import MatchDirtyJob
    matching_pending = (any(profile_match_index_stale(profile) for profile in profiles)
                        or session.scalar(select(MatchDirtyJob.job_id).limit(1)) is not None)

    canonical_states = canonical_application_states(session, selected_ids)
    counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    for application in canonical_states:
        counts[application.status] += 1
    applications = [application for application in canonical_states
                    if not selected_statuses or application.status in selected_statuses]
    total = len(applications)
    # Stored ranks remain usable while bounded worker pages rebuild them.
    # Matching card details below is limited to the requested page.
    order = {
        'posted': lambda row: (row.posted_rank, row.match_rank, row.discovered_rank, row.id),
        'discovered': lambda row: (row.discovered_rank, row.match_rank, row.posted_rank, row.id),
        'match': lambda row: (row.match_rank, row.discovered_rank, row.posted_rank, row.id),
    }[sort]
    applications = sorted(applications, key=order, reverse=True)[offset:offset + limit]
    # Bind only this page's stable job IDs, regardless of the full Journey size.
    jobs = session.scalars(select(Job).where(Job.id.in_({row.job_id for row in applications})).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations))).unique().all()
    jobs_by_id = {job.id: job for job in jobs}
    profiles_by_id = {profile.id: profile for profile in profiles}
    from hiring_scraper.app.profile_llm import load_matching_documents as load_semantic_documents
    semantic_documents = load_semantic_documents(session, jobs, profiles)
    items = []
    for application in applications:
        job = jobs_by_id[application.job_id]
        item = search_snapshots.job_json(job)
        item['application'] = application_json(application)
        item['profile_id'] = application.profile_id
        item['profile_name'] = profiles_by_id[application.profile_id].name
        item['profile_match'] = search_snapshots.profile_match(job, profiles_by_id[application.profile_id], None, semantic_documents)
        items.append(item)
    return {'profile_ids': selected_ids, 'items': items, 'counts': counts,
            'total': total,
            'offset': offset, 'limit': limit, 'sort': sort, 'matching_pending': matching_pending}
