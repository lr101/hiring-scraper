"""Small job catalog and explicit save actions for the browser companion."""
import hashlib
import ipaddress
import re
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload, load_only

from hiring_scraper.app.database import get_session
from hiring_scraper.app.company_identity import company_domain, find_company_by_website, is_hosted_job_site
from hiring_scraper.app.models import Company, Job, JobApplication, JobFeed, UserProfile, utcnow
from hiring_scraper.app.board_scope import expiry

router = APIRouter(prefix='/api/v1/companion', tags=['Application companion'])
_PUBLIC_HOST_PATTERN = re.compile(
    r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)'
    r'(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+'
)
_SENSITIVE_URL_PARAMETER = re.compile(
    r'^(?:token|access_token|id_token|code|session|sessionid|password|state|samlresponse)$', re.I
)
_TRANSIENT_URL_PATH = re.compile(
    r'(?:^|/)(?:log-?in|sign-?in|auth|oauth|sso|logout|consent)(?:/|$)', re.I
)
_TRACKING_URL_PARAMETER = re.compile(r'^utm_', re.I)


class SaveJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: int = Field(ge=1)
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(min_length=1, max_length=1000)
    company_name: str | None = Field(default=None, max_length=500)
    website_url: str | None = Field(default=None, max_length=2048)


def _job_page_url(value: str) -> tuple[str, str, str]:
    """Accept a public posting URL and remove tracking-only URL state."""
    if re.search(r'[\s\x00-\x1f\x7f\\]', value):
        raise HTTPException(status_code=422, detail='Job URL must be a public HTTPS page')
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if (parts.scheme != 'https' or not host or parts.username or parts.password or
                port not in (None, 443) or parts.fragment):
            raise ValueError('Use an HTTPS job page without credentials, a port, or a fragment')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            host = host.encode('idna').decode('ascii').casefold().rstrip('.')
            if (host in {'localhost', 'localhost.localdomain'} or
                    host.endswith(('.localhost', '.local', '.internal')) or
                    not _PUBLIC_HOST_PATTERN.fullmatch(host)):
                raise ValueError('Use a public, fully qualified hostname')
        if address is not None and not address.is_global:
            raise ValueError('Private and reserved addresses are not accepted')
        if _TRANSIENT_URL_PATH.search(parts.path):
            raise ValueError('Open the public job page, not a login or session page')
        query = parse_qsl(parts.query, keep_blank_values=True)
        if any(_SENSITIVE_URL_PARAMETER.fullmatch(key) for key, _ in query):
            raise ValueError('This page URL contains temporary sign-in details')
        query = [(key, item) for key, item in query if not _TRACKING_URL_PARAMETER.match(key) and
                 key.casefold() not in {'gclid', 'fbclid', 'msclkid', 'mc_cid', 'mc_eid'}]
        host_header = f'[{host}]' if address is not None and address.version == 6 else host
        canonical = urlunsplit(('https', host_header, parts.path or '/', urlencode(sorted(query)), ''))
        if len(canonical) > 2048:
            raise ValueError('Job URL is too long')
        return canonical, host, urlunsplit(('https', host_header, '/', '', ''))
    except (UnicodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f'Job URL must be a public HTTPS page: {error}') from error


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


@router.post('/jobs', status_code=201)
def save_companion_job(request: SaveJobRequest,
                       session: Session = Depends(get_session)) -> dict:
    """Save one posting to one profile; it does not subscribe that profile to a feed."""
    if session.get_bind().dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    profile = session.get(UserProfile, request.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail='Profile not found')
    title = ' '.join(request.title.split())
    if not title:
        raise HTTPException(status_code=422, detail='Enter a job title')
    url, host, website_url = _job_page_url(request.url)
    if request.website_url:
        _, _, website_url = _job_page_url(request.website_url)
        if is_hosted_job_site(website_url):
            raise HTTPException(status_code=422, detail="Enter the employer's own website, rather than its hosted job board")
    elif is_hosted_job_site(url):
        website_url = None
    job = session.scalar(select(Job).where(Job.url == url).order_by(Job.id).limit(1))
    created = False
    if job is None:
        company_name = ' '.join((request.company_name or '').split()) or host.removeprefix('www.')
        domain = company_domain(website_url) if website_url else None
        identity = f'domain:{domain}' if domain else f'posting:{url}'
        source_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        company = find_company_by_website(session, website_url) if website_url else None
        if company is None:
            company = session.scalar(select(Company).where(
                Company.source == 'extension_job', Company.source_id == source_id))
        if company is None:
            company_values = dict(
                source='extension_job', source_id=source_id, name=company_name,
                website_url=website_url, domain=domain, category='job_posting',
                source_url=website_url or url, crawl_paused=True,
            )
            company_id = session.scalar(insert(Company).values(**company_values)
                .on_conflict_do_nothing(index_elements=['source', 'source_id']).returning(Company.id))
            company = (session.get(Company, company_id) if company_id is not None else
                       session.scalar(select(Company).where(
                           Company.source == 'extension_job', Company.source_id == source_id)))
        feed = session.scalar(select(JobFeed).where(
            JobFeed.company_id == company.id, JobFeed.provider == 'manual_job'))
        if feed is None:
            feed_values = dict(
                company_id=company.id, provider='manual_job', board_url=website_url,
                feed_url=website_url or url, source_config={'manual_entry': True},
                status='manual', enabled=False, job_count=0,
            )
            feed_id = session.scalar(insert(JobFeed).values(**feed_values).on_conflict_do_nothing(
                index_elements=['company_id', 'provider', 'feed_url']).returning(JobFeed.id))
            feed = (session.get(JobFeed, feed_id) if feed_id is not None else
                    session.scalar(select(JobFeed).where(
                        JobFeed.company_id == company.id, JobFeed.provider == 'manual_job',
                        JobFeed.feed_url == (website_url or url))))
        external_id = hashlib.sha256(url.encode('utf-8')).hexdigest()
        job_id = session.scalar(insert(Job).values(
            feed_id=feed.id, external_id=external_id, title=title, url=url,
            raw_metadata={'source': 'browser_extension', 'manually_added': True},
        ).on_conflict_do_nothing(index_elements=['feed_id', 'external_id']).returning(Job.id))
        created = job_id is not None
        job = (session.get(Job, job_id) if created else session.scalar(select(Job).where(
            Job.feed_id == feed.id, Job.external_id == external_id)))
        if created:
            session.execute(update(JobFeed).where(JobFeed.id == feed.id).values(
                job_count=func.coalesce(JobFeed.job_count, 0) + 1))

    now = utcnow()
    application_id = session.scalar(insert(JobApplication).values(
        profile_id=profile.id, job_id=job.id, status='new', auto_created=False,
        created_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=['profile_id', 'job_id']).returning(JobApplication.id))
    application = session.scalar(select(JobApplication).where(
        JobApplication.profile_id == profile.id, JobApplication.job_id == job.id))
    already_added = application_id is None
    session.commit()
    company = job.employer_company or job.feed.company
    return {
        'job': {'id': job.id, 'title': job.title, 'company_name': company.name,
                'url': job.url, 'is_active': job.is_active and not expiry(job)['expired']},
        'profile_id': profile.id, 'profile_name': profile.name,
        'status': application.status, 'created': created, 'already_added': already_added,
    }
