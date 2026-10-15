"""Resolve hiring organizations found in job feeds to known companies."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from hiring_scraper.app.models import Company, JobFeed, utcnow

_THIRD_PARTY_COMPANY_HOSTS = {
    "facebook.com", "glassdoor.com", "indeed.com", "jobware.de", "kununu.com",
    "linkedin.com", "meinestadt.de", "monster.de", "stepstone.de", "xing.com",
}


class EmployerUpsertDeferred(RuntimeError):
    """Retry the enclosing job refresh when an employer is being initialized."""


def hiring_organization(job_data: dict) -> dict[str, str]:
    raw = job_data.get("raw_metadata")
    raw = raw if isinstance(raw, dict) else {}
    value = raw.get("hiring_organization") or raw.get("employer") or raw.get("organization")
    if isinstance(value, str):
        name, url = value, None
        careers_url = None
    elif isinstance(value, dict):
        name = value.get("name") or value.get("legalName")
        url = value.get("url") or value.get("website") or value.get("sameAs")
        careers_url = value.get("careers_url") or value.get("career_url")
    else:
        name = raw.get("company_name") or raw.get("employer_name") or job_data.get("company_name")
        url = raw.get("company_url") or raw.get("employer_url") or job_data.get("company_url")
        careers_url = raw.get("careers_url") or raw.get("career_url")
    result = {}
    for key, candidate in (("name", name), ("url", url), ("careers_url", careers_url)):
        if isinstance(candidate, str) and candidate.strip():
            result[key] = candidate.strip()[:2000]
    return result


def _valid_http_url(value: str | None) -> str | None:
    if not value or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return None
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    return value


def _is_third_party_company_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").casefold().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain)
               for domain in _THIRD_PARTY_COMPANY_HOSTS)


def _company_identity(name: str, domain: str | None) -> str:
    key = "domain:" + domain if domain else "name:" + " ".join(name.casefold().split())
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _insert_for(session: Session, model):
    if session.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    return insert(model)


def _try_employer_upsert_lock(session: Session, identity: str) -> bool:
    """Serialize this employer's secondary writes without waiting or deadlocking."""
    if session.get_bind().dialect.name != "postgresql":
        return True
    transaction = session.get_transaction()
    lock_state = session.info.get("employer_upsert_locks")
    if lock_state is not None and lock_state[0] is transaction and identity in lock_state[1]:
        return True
    lock_key = int.from_bytes(
        hashlib.sha256(f"job-feed-employer-upsert:{identity}".encode("utf-8")).digest()[:8],
        "big", signed=True,
    )
    acquired = bool(session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}))
    if acquired:
        transaction = session.get_transaction()
        if lock_state is None or lock_state[0] is not transaction:
            lock_state = (transaction, set())
            session.info["employer_upsert_locks"] = lock_state
        lock_state[1].add(identity)
    return acquired


def _find_company(session: Session, name: str, domain: str | None) -> Company | None:
    usable = (Company.category.is_(None) | (Company.category != "job_board_source"))
    if domain:
        match = session.scalars(select(Company).where(
            func.lower(Company.domain).in_({domain, "www." + domain}), usable
        ).order_by(Company.id)).first()
        if match:
            return match
    return session.scalars(select(Company).where(
        func.lower(func.trim(Company.name)) == name.casefold(), usable
    ).order_by(Company.id)).first()


def _is_german_job(job_data: dict, feed: JobFeed) -> bool:
    if feed.company.source == "default_job_feed":
        return True
    raw = job_data.get("raw_metadata")
    raw = raw if isinstance(raw, dict) else {}
    country = str(raw.get("country_code") or raw.get("country") or "").strip().casefold()
    if country in {"de", "deu", "germany", "deutschland"}:
        return True
    locations = job_data.get("locations")
    return isinstance(locations, list) and any(
        isinstance(location, dict) and str(location.get("country_code") or "").casefold() == "de"
        for location in locations
    )


def _register_direct_feed(session: Session, company: Company, url: str, now: datetime) -> bool:
    """Attach a recognized public ATS board without mistaking its host for the employer."""
    from hiring_scraper.ats import identify

    identified = identify(url)
    if not identified or not identified.get("feed_url"):
        return False
    provider, feed_url = identified["provider"], identified["feed_url"]
    board_url = identified.get("board_url") or url
    if provider == "personio":
        feed_parts = urlsplit(feed_url)
        board_url = f"{feed_parts.scheme}://{feed_parts.netloc}"
    feed = session.scalar(select(JobFeed).where(
        JobFeed.company_id == company.id, JobFeed.provider == provider,
        JobFeed.feed_url == feed_url))
    if not company.career_url_manual:
        company.career_url = board_url
        company.career_status = "jobs_feed_found"
    if feed is None:
        statement = _insert_for(session, JobFeed).values(
            company_id=company.id, provider=provider, tenant=identified.get("tenant"),
            board_url=board_url, feed_url=feed_url,
            status="pending", job_count=0, next_scan_at=now,
        ).on_conflict_do_nothing(index_elements=[
            JobFeed.company_id, JobFeed.provider, JobFeed.feed_url,
        ])
        feed_id = session.scalar(statement.returning(JobFeed.id))
        if feed_id is not None:
            feed = session.get(JobFeed, feed_id)
        else:
            feed = session.scalar(select(JobFeed).where(
                JobFeed.company_id == company.id, JobFeed.provider == provider,
                JobFeed.feed_url == feed_url))
        if feed is None:
            raise RuntimeError("Job feed insert conflicted but the feed row was not found")
        feed.board_url = board_url
        if feed.last_attempt_at is None and feed.lease_until is None:
            feed.next_scan_at = now
    else:
        feed.board_url = board_url
        if feed.last_attempt_at is None and feed.lease_until is None:
            feed.next_scan_at = now
    return True


def upsert_job_employer(session: Session, feed: JobFeed, job_data: dict,
                        now: datetime | None = None) -> Company | None:
    """Associate a posting with its employer and queue any next discovery step.

    Employer identity comes from job structured data or an explicit RSS company
    field. A job-board source itself is never treated as the hiring company.
    """
    organization = hiring_organization(job_data)
    name = organization.get("name", "").strip()
    if not name or len(name) < 2:
        return None
    now = now or utcnow()
    organization_url = _valid_http_url(organization.get("url"))
    career_url = _valid_http_url(organization.get("careers_url"))

    domain = None
    company_website = None
    if organization_url and not _is_third_party_company_url(organization_url):
        from hiring_scraper.ats import identify
        ats_source = identify(organization_url)
        if ats_source:
            domain = None
        else:
            parts = urlsplit(organization_url)
            domain = (parts.hostname or "").casefold().removeprefix("www.").rstrip(".")
            company_website = f"{parts.scheme}://{parts.netloc}/"

    source = "job_feed_employer"
    source_id = _company_identity(name, domain)
    normalized_name = " ".join(name.casefold().split())
    # The employer lookup matches either normalized name or domain. Lock both
    # keys before lookup so separate first-time identities cannot create two
    # rows while neither transaction can see the other's uncommitted insert.
    lock_identities = sorted({source_id, f"name:{normalized_name}"})
    if any(not _try_employer_upsert_lock(session, identity) for identity in lock_identities):
        # Roll back the containing job refresh so the retry can persist both
        # the job association and any employer/feed details from this posting.
        raise EmployerUpsertDeferred("Another worker is updating this employer; retry the job refresh")
    company = _find_company(session, name, domain)
    if company is not None and not _try_employer_upsert_lock(
            session, f"company:{company.id}"):
        # Different source identities can still resolve to this same canonical
        # company (for example, a corporate URL and an ATS-hosted URL).
        raise EmployerUpsertDeferred("Another worker is updating this employer; retry the job refresh")
    is_new = False
    website_added = False
    if company is None:
        statement = _insert_for(session, Company).values(
            source=source, source_id=source_id, name=name[:500],
            website_url=company_website, domain=domain,
            category="employer_from_job_feed", source_url=organization_url or feed.board_url,
            country_code="DE" if _is_german_job(job_data, feed) else None,
            career_status="not_checked",
        ).on_conflict_do_nothing(index_elements=[Company.source, Company.source_id])
        company_id = session.scalar(statement.returning(Company.id))
        if company_id is not None:
            company = session.get(Company, company_id)
            is_new = True
        else:
            # Another worker may have inserted this employer after _find_company
            # ran. Reuse the winner rather than aborting the whole feed refresh.
            company = session.scalar(select(Company).where(
                Company.source == source, Company.source_id == source_id))
            if company is None:
                raise RuntimeError("Employer insert conflicted but the company row was not found")
    if company is None:
        raise RuntimeError("Could not resolve the hiring company")
    if not is_new:
        if not company.website_url and company_website:
            company.website_url = company_website
            if company.domain_match_method != "manual":
                company.domain = domain
            website_added = True
        if company.country_code is None and _is_german_job(job_data, feed):
            company.country_code = "DE"

    registered = False
    for url in (career_url, organization_url):
        if url and _register_direct_feed(session, company, url, now):
            registered = True
    if (career_url and not registered and not _is_third_party_company_url(career_url) and
            not company.career_url_manual):
        company.career_url = career_url
        company.career_status = "career_page_found"
        page_feed = session.scalar(select(JobFeed).where(
            JobFeed.company_id == company.id, JobFeed.provider == "html_jobs",
            JobFeed.feed_url == career_url))
        if page_feed is None:
            statement = _insert_for(session, JobFeed).values(
                company_id=company.id, provider="html_jobs", board_url=career_url,
                feed_url=career_url, status="pending", job_count=0, next_scan_at=now,
            ).on_conflict_do_nothing(index_elements=[
                JobFeed.company_id, JobFeed.provider, JobFeed.feed_url,
            ])
            session.execute(statement)
    if company.website_url and (is_new or website_added):
        due = company.next_discovery_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if is_new or due is None or due > now:
            company.next_discovery_at = now
    return company
