"""Company domain identity for explicitly added job sources."""
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from hiring_scraper.app.models import Company

# Shared recruiting hosts identify a service, not the hiring company.
_SHARED_HOSTS = {
    'greenhouse.io', 'lever.co', 'personio.de', 'personio.com', 'ashbyhq.com',
    'recruitee.com', 'smartrecruiters.com', 'myworkdayjobs.com', 'softgarden.io',
    'softgarden.de', 'onlyfy.jobs', 'successfactors.eu', 'successfactors.com',
    'workable.com', 'teamtailor.com', 'breezy.hr', 'join.com', 'helixjobs.com',
    'linkedin.com', 'indeed.com', 'stepstone.de', 'xing.com', 'arbeitsagentur.de',
}
_CAREER_SUBDOMAINS = {'careers', 'career', 'jobs', 'job', 'karriere', 'recruiting', 'recruitment'}


def website_domain(url: str) -> str:
    host = (urlsplit(url).hostname or '').rstrip('.').casefold()
    return host.encode('idna').decode('ascii').removeprefix('www.')


def company_domain(url: str) -> str:
    domain = website_domain(url)
    while domain.split('.')[0] in _CAREER_SUBDOMAINS and domain.count('.') >= 2:
        domain = domain.split('.', 1)[1]
    return domain


def is_hosted_job_site(url: str) -> bool:
    domain = website_domain(url)
    return any(domain == shared or domain.endswith('.' + shared) for shared in _SHARED_HOSTS)


def find_company_by_website(session: Session, url: str) -> Company | None:
    """Prefer the exact hostname, then a company root behind a careers subdomain.

    Website URLs cover older imported companies whose domain has not been filled.
    Host boundaries prevent acme.example from matching another-acme.example.
    """
    if is_hosted_job_site(url):
        return None
    domain = website_domain(url)
    domains = [domain]
    while domain.split('.')[0] in _CAREER_SUBDOMAINS and domain.count('.') >= 2:
        domain = domain.split('.', 1)[1]
        domains.append(domain)
    for domain in domains:
        variants = {domain, 'www.' + domain}
        roots = [f'{scheme}://{host}' for scheme in ('http', 'https') for host in variants]
        match = session.scalars(select(Company).where(or_(
            func.lower(Company.domain).in_(variants),
            func.lower(Company.website_url).in_(roots),
            *(func.lower(Company.website_url).startswith(root + '/', autoescape=True) for root in roots),
        )).order_by(Company.id)).first()
        if match is not None:
            return match
    return None
