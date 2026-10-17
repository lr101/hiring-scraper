"""Minimal read-only job catalog for the browser companion; no search side effects."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, load_only

from hiring_scraper.app.database import get_session
from hiring_scraper.app.models import Company, Job, JobFeed
from hiring_scraper.app.board_scope import expiry

router = APIRouter(prefix='/api/v1/companion', tags=['Application companion'])


@router.get('/jobs')
def list_companion_jobs(
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    session: Session = Depends(get_session),
) -> dict:
    rows = session.scalars(select(Job).where(Job.id > after_id).options(
        load_only(Job.id, Job.title, Job.url, Job.is_active, Job.raw_metadata,
                  Job.feed_id, Job.employer_company_id),
        joinedload(Job.feed).load_only(JobFeed.company_id).joinedload(JobFeed.company).load_only(Company.name),
        joinedload(Job.employer_company).load_only(Company.name),
    ).order_by(Job.id).limit(limit + 1)).all()
    items = []
    for job in rows[:limit]:
        if len(job.url) > 2048:
            continue
        metadata = job.raw_metadata if isinstance(job.raw_metadata, dict) else {}
        organization = metadata.get('hiring_organization')
        name = organization.get('name') if isinstance(organization, dict) else None
        company_name = (job.employer_company.name if job.employer_company else
                        name.strip() if isinstance(name, str) and name.strip() else job.feed.company.name)
        items.append({'id': job.id, 'title': job.title[:300], 'company_name': company_name[:300],
                      'url': job.url, 'is_active': job.is_active and not expiry(job)['expired']})
    return {'items': items, 'next_after_id': rows[limit - 1].id if len(rows) > limit else None}
