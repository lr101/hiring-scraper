"""Profile-specific application states, separate from the source's job lifecycle."""
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, update

from hiring_scraper.app.models import JobApplication

ApplicationStatus = Literal['new', 'open', 'in_gots_hand', 'not_interested', 'waiting_for_reply',
                            'automated_application', 'interview', 'rejected', 'accepted', 'closed']
APPLICATION_STATUSES = get_args(ApplicationStatus)
APPLIED_STATUSES = ('waiting_for_reply', 'automated_application')


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
