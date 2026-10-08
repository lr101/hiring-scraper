"""Profile-specific application states, separate from the source's job lifecycle."""
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

ApplicationStatus = Literal['new', 'open', 'not_interested', 'waiting_for_reply',
                            'interview', 'rejected', 'accepted']
APPLICATION_STATUSES = get_args(ApplicationStatus)


class ApplicationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: ApplicationStatus


def application_json(application):
    return {'profile_id': application.profile_id, 'job_id': application.job_id,
            'status': application.status, 'created_at': application.created_at.isoformat(),
            'updated_at': application.updated_at.isoformat()}
