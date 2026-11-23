"""Today's focus: a small, achievable set of next applications.

A long Journey list invites procrastination. Focus picks at most three roles to
act on now: ones with documents already prepared come first (the fastest send),
then roles the user marked Interested, then the strongest matches. Sending three
applications completes the day; nothing here nags, penalizes or submits anything.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from hiring_scraper.app import application_generation_jobs, application_preparation_queue, codex_runtime
from hiring_scraper.app.applications import canonical_application_states
from hiring_scraper.app.database import get_session
from hiring_scraper.app.models import ApplicationMomentumEvent, Job, JobFeed, JobVacancyGroup, utcnow
from hiring_scraper.app.profiles import require_profile
from hiring_scraper.app.board_scope import expiry

router = APIRouter(prefix='/api/v1')
DAILY_TARGET = 3
ACTIONABLE = ('new', 'open')


def _documents_ready(session: Session, profile_id: int, job_id: int) -> bool:
    """Whether a reviewed-ready draft or completed preparation already exists."""
    try:
        record = application_preparation_queue.status(session, profile_id, job_id)
        if record and record.get('state') == 'completed':
            return True
        root = codex_runtime._root()
        return root.exists() and bool(application_generation_jobs.load_draft(root, profile_id, job_id))
    except (OSError, ValueError):
        return False


def _sent_today(session: Session) -> int:
    """Distinct vacancies first marked applied since midnight UTC (matches weekly momentum)."""
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    logical_job_id = func.coalesce(JobVacancyGroup.vacancy_group_id, Job.id)
    first_applied = select(logical_job_id.label('job'), func.min(ApplicationMomentumEvent.occurred_at).label('first')).select_from(
        ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id).outerjoin(
        JobVacancyGroup, JobVacancyGroup.job_id == Job.id).where(
        ApplicationMomentumEvent.event_type == 'applied',
        ApplicationMomentumEvent.is_legacy.is_(False),
    ).group_by(logical_job_id).subquery()
    return int(session.scalar(select(func.count()).select_from(first_applied).where(
        first_applied.c.first >= start, first_applied.c.first < start + timedelta(days=1))) or 0)


def _reason(status: str, ready: bool, score: int | None) -> str:
    if ready:
        return 'Documents are ready to review and send'
    if status == 'open':
        return 'You marked this role as Interested'
    return f'Strong match ({score})' if score is not None and score >= 0 else 'New in your Journey'


def todays_focus(session: Session, profile_id: int) -> dict:
    states = [state for state in canonical_application_states(session, [profile_id])
              if state.status in ACTIONABLE]
    # Interested before New, then match, then freshest; readiness is checked lazily below.
    states.sort(key=lambda state: (state.status == 'open', state.match_rank, state.discovered_rank), reverse=True)
    candidates = []
    for start in range(0, len(states), 50):
        batch = states[start:start + 50]
        jobs = {job.id: job for job in session.scalars(select(Job).where(Job.id.in_([s.job_id for s in batch]))
                                                         .options(joinedload(Job.feed).joinedload(JobFeed.company)))}
        for state in batch:
            job = jobs.get(state.job_id)
            if job is None or not job.is_active or expiry(job)['expired']:
                continue
            candidates.append((state, job, _documents_ready(session, profile_id, job.id)))
        # Ready roles jump the queue, so look a little beyond the first three.
        if len(candidates) >= DAILY_TARGET * 4:
            break
    candidates.sort(key=lambda item: (item[2], item[0].status == 'open', item[0].match_rank,
                                      item[0].discovered_rank), reverse=True)
    items = []
    for state, job, ready in candidates[:DAILY_TARGET]:
        score = state.match_rank if state.match_rank >= 0 else None
        company = job.feed.company.name if job.feed and job.feed.company else None
        items.append({
            'job_id': job.id, 'title': job.title, 'company': company, 'status': state.status,
            'match_score': score, 'documents_ready': ready, 'reason': _reason(state.status, ready, score),
        })
    sent = _sent_today(session)
    return {'items': items, 'sent_today': sent, 'daily_target': DAILY_TARGET,
            'completed': sent >= DAILY_TARGET}


@router.get('/profiles/{profile_id}/focus')
def get_todays_focus(profile_id: int, session: Session = Depends(get_session)) -> dict:
    require_profile(profile_id, session)
    return todays_focus(session, profile_id)
