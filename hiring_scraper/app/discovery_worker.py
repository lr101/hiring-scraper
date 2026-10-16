"""Scheduled, bounded discovery of employer career pages and public job feeds."""
from __future__ import annotations

import argparse
import logging
import hashlib
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import DBAPIError, TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import aliased, selectinload

from hiring_scraper.app.enrichment import refresh_enrichment, preserve_verified_detail
from hiring_scraper.app.database import SessionLocal, engine, is_transient_database_error
from hiring_scraper.app.crawl_ranking import refresh_company_rank, next_company_due, next_feed_due
from hiring_scraper.app.national_discovery import advance_map_cursor
from hiring_scraper.app.discovery_jobs import (
    advance_location_schedule, canonical_company_ids, canonical_company_map,
    company_ids_for_search, create_discovery_job,
)
from hiring_scraper.app.feed_schedules import queue_arbeitsagentur_scope
from hiring_scraper.app.models import (
    Company, ConfiguredLocation, DiscoveryJob, DiscoveryJobCompany, DiscoveryRun,
    Job, JobFeed, JobLocation, utcnow,
)
from hiring_scraper.app.job_search import (
    job_search_signature, refresh_application_matches_for_jobs, replace_job_locations,
)
from hiring_scraper.app.employers import upsert_job_employer
from hiring_scraper.discovery import discover, trusted_html_jobs, trusted_html_source
from hiring_scraper.http import Client
from hiring_scraper.crawl_policy import ORIGIN_PACER
from hiring_scraper.location_discovery import (
    LocationSearchCancelled,
    fetch_location_companies, is_retryable_overpass_error, resolve_missing_company_websites,
)
from hiring_scraper.website_discovery import configured_website_search


LOG = logging.getLogger("hiring_scraper.discovery_worker")
POLL_SECONDS = max(3, int(os.getenv("CAREER_DISCOVERY_POLL_SECONDS", "3")))
INTERVAL_DAYS = max(1, int(os.getenv("CAREER_DISCOVERY_INTERVAL_DAYS", "30")))
LEASE_MINUTES = max(5, int(os.getenv("CAREER_DISCOVERY_LEASE_MINUTES", "30")))
HOMEPAGE_RESOLUTION_LEASE_MINUTES = max(5, int(os.getenv("HOMEPAGE_RESOLUTION_LEASE_MINUTES", "15")))
HOMEPAGE_RESOLUTION_MAX_ATTEMPTS = max(1, int(os.getenv("HOMEPAGE_RESOLUTION_MAX_ATTEMPTS", "3")))
LOCATION_SCAN_MAX_ATTEMPTS = 3
NATIONAL_MAP_INTERVAL_SECONDS = max(60, int(os.getenv('NATIONAL_MAP_INTERVAL_SECONDS','300')))
NATIONAL_FIRST_CRAWL_SPREAD_SECONDS = 300
CRAWL_BATCH_DELAY_SECONDS = max(1, float(os.getenv('CAREER_DISCOVERY_BATCH_DELAY_SECONDS','1')))
REQUEST_DELAY_SECONDS = max(1.0, float(os.getenv("CAREER_DISCOVERY_DELAY_SECONDS", "1")))
MAX_PAGES = min(24, max(1, int(os.getenv("CAREER_DISCOVERY_MAX_PAGES", "24"))))
MAX_DEPTH = min(6, max(1, int(os.getenv("CAREER_DISCOVERY_MAX_DEPTH", "5"))))
MAX_REQUESTS = min(64, max(1, int(os.getenv("CAREER_DISCOVERY_MAX_REQUESTS", "48"))))
MAX_CRAWL_WORKER_LIMIT = 32
MAX_CRAWL_WORKERS = min(MAX_CRAWL_WORKER_LIMIT,
                        max(1, int(os.getenv("CAREER_DISCOVERY_WORKERS", "8"))))
TIMEOUT_SECONDS = max(5, float(os.getenv("CAREER_DISCOVERY_TIMEOUT_SECONDS", "15")))
CAPTURE_DIR = Path(os.getenv("CAREER_DISCOVERY_CAPTURE_DIR", "/tmp/hiring-scraper-discovery-captures"))
USER_AGENT = os.getenv("HIRING_USER_AGENT", "HiringScraper/0.2 (public career discovery)")
FEED_INTERVAL_HOURS = max(1, int(os.getenv("FEED_SCAN_INTERVAL_HOURS", "6")))
_ORIGIN_PACER = ORIGIN_PACER
_SQLITE_FINALIZE_LOCK = Lock()

_CAREER_STATUS = {
    "career_content_found": "career_page_found",
    "jobs_feed_found": "jobs_feed_found",
    "jobs_extracted": "jobs_extracted",
    "ats_identified": "provider_detected",
    "unresolved": "unresolved",
}
_POSITIVE_CAREER_STATUSES = {"career_page_found", "jobs_feed_found", "jobs_extracted", "provider_detected"}


def _claim_due_company() -> tuple[int, int] | None:
    now = utcnow()
    with SessionLocal.begin() as session:
        earlier_domain_company = aliased(Company)
        company_domain = case(
            (func.lower(Company.domain).like("www.%"), func.substr(func.lower(Company.domain), 5)),
            else_=func.lower(Company.domain))
        earlier_domain = case(
            (func.lower(earlier_domain_company.domain).like("www.%"),
             func.substr(func.lower(earlier_domain_company.domain), 5)),
            else_=func.lower(earlier_domain_company.domain))
        duplicate_domain = select(earlier_domain_company.id).where(
            Company.domain.is_not(None), earlier_domain_company.domain.is_not(None),
            earlier_domain_company.id < Company.id,
            earlier_domain_company.website_url.is_not(None), earlier_domain_company.website_url != "",
            earlier_domain == company_domain,
        ).correlate(Company).exists()
        statement = (select(Company)
                     .where(Company.website_url.is_not(None), Company.website_url != "", Company.crawl_paused.is_(False),
                            ~duplicate_domain,
                            ~Company.id.in_(select(DiscoveryJobCompany.company_id).join(DiscoveryJob).where(
                                DiscoveryJobCompany.status.in_(("queued", "running")),
                                DiscoveryJob.status.in_(("scheduled", "queued", "running")))),
                            or_(Company.next_discovery_at.is_(None), Company.next_discovery_at <= now),
                            or_(Company.discovery_lease_until.is_(None), Company.discovery_lease_until < now))
                     .order_by(Company.next_discovery_at.asc().nullsfirst(), Company.crawl_priority.desc(), Company.id).limit(1))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        company = session.scalars(statement).first()
        if company is None:
            return None
        stale_runs = session.scalars(select(DiscoveryRun).where(
            DiscoveryRun.company_id == company.id, DiscoveryRun.status == "running"
        ).with_for_update()).all()
        for stale_run in stale_runs:
            stale_run.status = "failed"
            stale_run.error = "Worker lease expired; discovery was requeued."
            stale_run.finished_at = now
        company.discovery_lease_until = now + timedelta(minutes=LEASE_MINUTES)
        company.discovery_attempt_count += 1
        run = DiscoveryRun(company_id=company.id, started_at=now, status="running")
        session.add(run)
        session.flush()
        return company.id, run.id


def _retry_time(attempt: int) -> datetime:
    minutes = min(7 * 24 * 60, 5 * 2 ** min(max(attempt - 1, 0), 10))
    return utcnow() + timedelta(minutes=minutes, seconds=random.randint(0, 59))


def _homepage_source_tags(tags: dict | None) -> dict:
    allowed = ("addr:city", "addr:street", "addr:postcode", "email", "contact:email",
               "phone", "contact:phone")
    return {key: value.strip()[:500] for key in allowed
            if isinstance((value := (tags or {}).get(key)), str) and value.strip()}


def _homepage_resolution_due(company: Company, candidate: dict, now: datetime) -> datetime | None:
    tags = _homepage_source_tags(candidate.get("tags"))
    has_email = bool(tags.get("email") or tags.get("contact:email"))
    if not has_email and not configured_website_search():
        return None
    days = 1 if (company.employee_count or 0) >= 250 else 3 if has_email else 7
    source_id = str(candidate.get("source_id") or company.source_id)
    offset = int.from_bytes(hashlib.sha256(f"homepage:{source_id}".encode()).digest()[:8], "big")
    return now + timedelta(seconds=offset % (days * 86400))


def _national_first_crawl_due(job_id: int, company_id: int, now: datetime) -> datetime:
    offset = int.from_bytes(
        hashlib.sha256(f"{job_id}:{company_id}".encode()).digest()[:4], "big"
    ) % NATIONAL_FIRST_CRAWL_SPREAD_SECONDS
    return now + timedelta(seconds=offset)


def _claim_due_homepage_resolution(now=None) -> tuple[int, int, str] | None:
    now = now or utcnow()
    with SessionLocal.begin() as session:
        statement = (select(Company)
                     .where(Company.website_url.is_(None), Company.homepage_source_tags.is_not(None),
                            Company.homepage_resolution_next_at.is_not(None),
                            Company.homepage_resolution_next_at <= now,
                            or_(Company.homepage_resolution_lease_until.is_(None),
                                Company.homepage_resolution_lease_until < now))
                     .order_by(Company.crawl_priority.desc(), Company.employee_count.desc().nullslast(),
                               Company.homepage_resolution_next_at, Company.id).limit(1))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        company = session.scalars(statement).first()
        if company is None:
            return None
        company.homepage_resolution_attempts += 1
        company.homepage_resolution_token = uuid4().hex
        company.homepage_resolution_lease_until = now + timedelta(minutes=HOMEPAGE_RESOLUTION_LEASE_MINUTES)
        return company.id, company.homepage_resolution_attempts, company.homepage_resolution_token


def _process_homepage_resolution(claim: tuple[int, int, str]) -> bool:
    company_id, attempt, token = claim
    with SessionLocal() as session:
        company = session.get(Company, company_id)
        if (company is None or company.website_url or not company.homepage_source_tags or
                company.homepage_resolution_attempts != attempt or
                company.homepage_resolution_token != token):
            return True
        candidate = {
            "source_id": company.source_id, "name": company.name, "category": company.category,
            "latitude": company.latitude, "longitude": company.longitude,
            "tags": dict(company.homepage_source_tags),
        }
        search_area_hint = company.state
    error = None
    try:
        resolve_missing_company_websites([candidate], search_area_hint=search_area_hint,
                                         max_companies=1, max_searches=3, max_requests=8)
    except Exception as failure:
        error = failure
        LOG.warning("missing homepage resolution failed company_id=%s attempt=%s: %s",
                    company_id, attempt, failure)
    now = utcnow()
    with SessionLocal.begin() as session:
        company = session.scalar(select(Company).where(Company.id == company_id).with_for_update())
        if company is None:
            return True
        lease = company.homepage_resolution_lease_until
        if lease is not None and lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if (company.homepage_resolution_attempts != attempt or
                company.homepage_resolution_token != token or lease is None or lease <= now):
            return True
        company.homepage_resolution_lease_until = None
        company.homepage_resolution_token = None
        website = candidate.get("website_url") if error is None else None
        if website:
            company.website_url = website
            if company.domain_match_method != "manual":
                company.domain = candidate.get("domain")
                company.domain_match_method = candidate.get("domain_match_method")
                company.domain_evidence_url = candidate.get("domain_evidence_url")
            company.homepage_source_tags = None
            company.homepage_resolution_next_at = None
            company.homepage_resolution_attempts = 0
            refresh_company_rank(session, company, now)
            company.next_discovery_at = now
        elif attempt < HOMEPAGE_RESOLUTION_MAX_ATTEMPTS:
            # A verified lead may appear later; keep retries rare after the first,
            # and spread them according to company size.
            delay_days = 30 if (company.employee_count or 0) >= 250 else 90
            jitter = int.from_bytes(hashlib.sha256(
                f"homepage-retry:{company.source}:{company.source_id}:{attempt}".encode()).digest()[:4], "big") % 86400
            company.homepage_resolution_next_at = now + timedelta(days=delay_days, seconds=jitter)
        else:
            company.homepage_resolution_next_at = None
    return True


def _job_locations(job_data: dict, old_locations: list[JobLocation]) -> list[dict]:
    source_locations = job_data.get("locations")
    if not source_locations:
        label = (job_data.get("location") or "").strip()
        source_locations = [{"label": label}] if label else []
    old_by_label = {row.label.casefold(): row for row in old_locations}
    output = []
    for source in source_locations:
        source = {"label": source} if isinstance(source, str) else source
        if not isinstance(source, dict) or not source.get("label"):
            continue
        label = str(source["label"]).strip()
        old = old_by_label.get(label.casefold())
        latitude, longitude = source.get("latitude"), source.get("longitude")
        precision, country = source.get("precision"), source.get("country_code")
        if latitude is None and old:
            latitude, longitude = old.latitude, old.longitude
            precision, country = old.precision, old.country_code
        key = label.casefold().strip()
        locality = key.split(" - ", 1)[0].split(",", 1)[0].strip()
        if locality == "karlsruhe" and latitude is None:
            latitude, longitude, precision, country = 49.0068705, 8.4034195, "city_centroid", "de"
        elif locality == "berlin" and latitude is None:
            latitude, longitude, precision, country = 52.5200, 13.4050, "city_centroid", "de"
        output.append({"label": label, "latitude": latitude, "longitude": longitude,
                       "precision": precision, "country_code": country})
    return output


def _lock_current_discovery_run(session, company_id: int, run_id: int) -> DiscoveryRun | None:
    """Return a run only while it remains the company's latest active attempt."""
    run = session.scalars(select(DiscoveryRun).where(
        DiscoveryRun.id == run_id, DiscoveryRun.company_id == company_id
    ).with_for_update()).first()
    if run is None or run.status != "running":
        return None
    latest_id = session.scalar(select(DiscoveryRun.id).where(
        DiscoveryRun.company_id == company_id
    ).order_by(DiscoveryRun.id.desc()).limit(1))
    return run if latest_id == run_id else None


def _upsert_jobs(session, feed: JobFeed, rows: list[dict], complete: bool, now: datetime) -> tuple[set[int], set[int]]:
    existing = {job.external_id: job for job in session.scalars(
        select(Job).where(Job.feed_id == feed.id).options(selectinload(Job.locations))).all()}
    seen: set[str] = set()
    seen_job_ids: set[int] = set()
    touched_job_ids: set[int] = set()
    for data in rows:
        external_id, title, url = data.get("id"), data.get("title"), data.get("url")
        if external_id in (None, "") or not title or not url:
            continue
        external_id = str(external_id)
        seen.add(external_id)
        job = existing.get(external_id)
        created = job is None
        if created:
            job = Job(feed_id=feed.id, external_id=external_id, title=title, url=url,
                      first_seen_at=now, last_seen_at=now)
            session.add(job)
            session.flush()
            existing[external_id] = job
            old_locations = []
        else:
            old_locations = list(job.locations)
        before_signature = None if created else job_search_signature(job)
        seen_job_ids.add(job.id)
        data = preserve_verified_detail(job, data)
        job.title, job.url = title, url
        job.description = data.get("description")
        job.location_text = data.get("location") or None
        job.is_remote = bool(data.get("is_remote")) or (job.location_text or "").casefold().strip() == "remote"
        job.work_arrangement = data.get("work_arrangement") or ("remote" if job.is_remote else None)
        for field in ("employment_type", "schedule", "department", "seniority", "date_posted", "salary"):
            setattr(job, field, data.get(field))
        job.raw_metadata = data.get("raw_metadata") or {}
        employer = upsert_job_employer(session, feed, data, now)
        if employer is not None:
            job.employer_company_id = employer.id
        refresh_enrichment(job)
        job.last_seen_at = now
        job.is_active = True
        job.missing_complete_scans = 0
        job.closed_at = None
        replace_job_locations(job, _job_locations(data, old_locations))
        if created or job_search_signature(job) != before_signature:
            touched_job_ids.add(job.id)

    if complete:
        for external_id, job in existing.items():
            if external_id in seen:
                continue
            job.missing_complete_scans += 1
            last_seen = job.last_seen_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if job.missing_complete_scans >= 2 and now - last_seen >= timedelta(days=7):
                job.is_active = False
                job.closed_at = now
                touched_job_ids.add(job.id)

    feed.job_count = len(seen)
    feed.status = "parsed" if complete and seen else "complete_empty" if complete else "incomplete"
    feed.last_checked_at = now
    feed.last_error = None if complete else "Discovery returned a partial job list; no jobs were closed."
    feed.next_scan_at = now + timedelta(hours=FEED_INTERVAL_HOURS)
    feed.attempt_count = 0
    return seen_job_ids, touched_job_ids


def _campaign_attempt(session, item_id: int, run_id: int):
    """Load the parent job under lock and verify this campaign still owns its task."""
    item = session.get(DiscoveryJobCompany, item_id)
    if item is None:
        return None
    job = session.scalars(select(DiscoveryJob).where(
        DiscoveryJob.id == item.discovery_job_id).with_for_update()).first()
    if (job is None or (job.stage != "career_page_discovery" and not (job.search_scope == "country" and job.stage == "company_homepage_discovery")) or
            job.status not in {"running", "scheduled", "queued"} or
            item.status != "running" or item.discovery_run_id != run_id):
        return (job, item) if job is not None and job.status == "cancelled" and \
            item.status == "running" and item.discovery_run_id == run_id else None
    return job, item


def _campaign_attempt_is_live(item_id: int, run_id: int) -> bool:
    with SessionLocal() as session:
        attempt = _campaign_attempt(session, item_id, run_id)
        return attempt is not None and attempt[0] is not None and attempt[0].status != "cancelled"


def _discard_cancelled_campaign_attempt(company: Company, run: DiscoveryRun,
                                        item: DiscoveryJobCompany, now) -> None:
    company.discovery_lease_until = None
    run.status = "cancelled"
    run.finished_at = now
    item.status = "cancelled"
    item.finished_at = now
    item.jobs_found = 0
    item.error = None


def _persist_discovery(company_id: int, run_id: int, result: dict,
                       *, campaign_item_id: int | None = None) -> int | None:
    now = utcnow()
    with SessionLocal.begin() as session:
        company = session.scalars(select(Company).where(
            Company.id == company_id).with_for_update()).first()
        run = _lock_current_discovery_run(session, company_id, run_id)
        if company is None or run is None:
            return 0
        if campaign_item_id is not None:
            attempt = _campaign_attempt(session, campaign_item_id, run_id)
            if attempt is None:
                return None
            job, item = attempt
            if job.status == "cancelled":
                _discard_cancelled_campaign_attempt(company, run, item, now)
                return None
        company.discovery_lease_until = None
        company.discovery_error = None
        company.discovery_attempt_count = 0
        company.last_checked_at = now
        fresh_status = _CAREER_STATUS.get(result.get("status"), "unresolved")
        if fresh_status != "unresolved" or company.career_status not in _POSITIVE_CAREER_STATUSES:
            company.career_status = fresh_status

        seed = {"name": company.name, "website": company.website_url}
        page_by_url = {page.get("url"): page for page in result.get("pages", [])}
        career_pages = [page for page in result.get("pages", [])
                        if page.get("classification") in {"career_content", "jobposting"} and
                        page.get("html_extraction_trust") != "unverified_external_source"]
        career_url = next((page.get("url") for page in career_pages if page.get("url") and
                           (page.get("html_extraction_trust") in
                            {"first_party", "branded_external", "linked_external_verified"} or
                            trusted_html_source(seed, page, result.get("pages", [])))), None)
        parsed_boards = [board for board in result.get("boards", [])
                         if board.get("feed_state") == "parsed" and board.get("feed_url")]
        if career_url is None and parsed_boards:
            career_url = parsed_boards[0].get("board_url") or parsed_boards[0].get("feed_url")
        if career_url and not company.career_url_manual:
            company.career_url = career_url

        feed_by_key = {(feed.provider, feed.feed_url): feed for feed in session.scalars(
            select(JobFeed).where(JobFeed.company_id == company_id)).all()}
        # Career listings with no current postings still need direct future checks.
        # Do not turn individual vacancy pages or untrusted pages into listings.
        if not parsed_boards:
            for page in career_pages:
                url = page.get("url")
                key = ("html_jobs", url)
                if (page.get("classification") != "career_content" or not url or key in feed_by_key or
                        not trusted_html_source(seed, page, result.get("pages", []))):
                    continue
                feed = JobFeed(company_id=company_id, provider="html_jobs", board_url=url,
                               feed_url=url, status="pending", job_count=0, next_scan_at=now)
                session.add(feed)
                feed_by_key[key] = feed
        observed_job_ids: set[int] = set()
        touched_job_ids: set[int] = set()
        updated_feed_ids = set()
        for board in parsed_boards:
            jobs = board.get("jobs", [])
            if board.get("provider") in {"html_jobs", "browser_html"}:
                source_page = page_by_url.get(board.get("discovered_on"), {})
                jobs, trust = trusted_html_jobs(seed, source_page, result.get("pages", []), jobs)
                source_page["html_extraction_trust"] = trust
                if not jobs:
                    continue
            key = (board["provider"], board["feed_url"])
            feed = feed_by_key.get(key)
            if feed is None and board["provider"] in {"html_jobs", "browser_html"}:
                legacy_key = next((existing_key for existing_key in feed_by_key
                                   if existing_key[1] == board["feed_url"] and
                                   existing_key[0] in {"html_jobs", "browser_html"}), None)
                if legacy_key is not None:
                    feed = feed_by_key.pop(legacy_key)
                    if feed.lease_until is None and feed.provider != board["provider"]:
                        feed.provider = board["provider"]
                        feed.source_revision = (feed.source_revision or 0) + 1
                    feed_by_key[key] = feed
            if feed is None:
                if (board["provider"] == "recruitee" and session.scalar(
                        select(Job.id).join(JobFeed).where(
                            JobFeed.company_id == company_id,
                            JobFeed.provider.in_({"html_jobs", "browser_html"}),
                            Job.is_active.is_(True)).limit(1)) is not None):
                    # HTML and XML may describe the same jobs with different
                    # public URLs. Preserve the existing rows until aliases are
                    # verified; existing supported feeds still refresh normally.
                    LOG.info("Deferred new Recruitee feed for company %s: active HTML jobs lack verified aliases (%s)",
                             company_id, board["feed_url"])
                    continue
                feed = JobFeed(company_id=company_id, provider=board["provider"],
                               tenant=board.get("tenant"), board_url=board.get("board_url"),
                               feed_url=board["feed_url"], status="parsed", job_count=0,
                               last_checked_at=now, next_scan_at=now + timedelta(hours=FEED_INTERVAL_HOURS))
                session.add(feed)
                session.flush()
                feed_by_key[key] = feed
            else:
                feed.tenant = board.get("tenant") or feed.tenant
                feed.board_url = board.get("board_url") or feed.board_url
            observed, touched = _upsert_jobs(session, feed, jobs, bool(board.get("complete")), now)
            observed_job_ids.update(observed)
            touched_job_ids.update(touched)
            updated_feed_ids.add(feed.id)

        if updated_feed_ids:
            # Multiple scoped Personio links can upsert separate subsets of
            # the same XML source. Store the feed's aggregate active count,
            # rather than the last subset's count from _upsert_jobs.
            session.flush()
            for feed in session.scalars(select(JobFeed).where(
                    JobFeed.id.in_(updated_feed_ids))).all():
                feed.job_count = session.scalar(select(func.count(Job.id)).where(
                    Job.feed_id == feed.id, Job.is_active.is_(True)))

        if touched_job_ids:
            refresh_application_matches_for_jobs(session, touched_job_ids)

        run.finished_at = now
        run.status = result.get("status", "unresolved")
        run.pages_checked = len(result.get("pages", []))
        # Scoped links can overlap on the same feed; count each observed row once.
        total_jobs = len(observed_job_ids)
        run.jobs_found = total_jobs
        run.evidence = {
            "website_url": company.website_url,
            "seed_resolution": result.get("seed_resolution"),
            "career_pages": [{key: page.get(key) for key in
                               ("requested_url", "url", "parent", "method", "depth", "classification",
                                "html_extraction_trust", "fetch_state", "http_status", "redirect_chain",
                                "browser_render_state", "browser_render_error", "browser_requests",
                                "browser_rendered")}
                              for page in result.get("pages", [])
                              if page.get("classification") in {"career_content", "jobposting"} or
                              page.get("html_extraction_trust") == "unverified_external_source" or
                              page.get("html_extracted_job_count") or page.get("browser_render_state")],
            "boards": [{key: board.get(key) for key in
                        ("provider", "tenant", "board_url", "feed_url", "evidence_url", "evidence_kind",
                         "discovered_on", "feed_state", "feed_http_status", "job_count", "complete",
                         "personio_posting_scopes", "personio_full_board_authorized", "rendered")}
                       for board in result.get("boards", [])],
            "unverified_external_html_pages": result.get("unverified_external_html_pages", []),
            "unverified_external_ats": result.get("unverified_external_ats", []),
            "limits": result.get("limits", {}),
        }
        session.flush()
        refresh_company_rank(session, company, now, result)
        company.next_discovery_at = next_company_due(company, now)
        for feed in session.scalars(select(JobFeed).where(JobFeed.company_id == company.id)).all():
            if feed.attempt_count or feed.lease_until is not None:
                continue
            due = next_feed_due(company, feed, now)
            old_due = feed.next_scan_at
            if old_due is not None and old_due.tzinfo is None:
                old_due = old_due.replace(tzinfo=timezone.utc)
            if due is None or old_due is None or due < old_due:
                feed.next_scan_at = due
        return total_jobs


def _record_failure(company_id: int, run_id: int, error: Exception,
                    *, campaign_item_id: int | None = None) -> None:
    now = utcnow()
    with SessionLocal.begin() as session:
        company = session.scalars(select(Company).where(
            Company.id == company_id).with_for_update()).first()
        run = _lock_current_discovery_run(session, company_id, run_id)
        if company is None or run is None:
            return
        if campaign_item_id is not None:
            attempt = _campaign_attempt(session, campaign_item_id, run_id)
            if attempt is None:
                return
            job, item = attempt
            if job.status == "cancelled":
                _discard_cancelled_campaign_attempt(company, run, item, now)
                return
        detail = str(error)[:2000]
        company.discovery_lease_until = None
        company.discovery_error = detail
        company.next_discovery_at = _retry_time(company.discovery_attempt_count)
        run.finished_at = now
        run.status = "failed"
        run.error = detail


def _claim_location_company_search(now=None) -> tuple[int, str] | None:
    """Lease one queued location-level company and homepage search."""
    now = now or utcnow()
    with SessionLocal.begin() as session:
        statement = (select(DiscoveryJob)
                     .where(DiscoveryJob.stage == "company_homepage_discovery",
                            DiscoveryJob.kind != "recurring",
                            or_(DiscoveryJob.status == "queued",
                                ((DiscoveryJob.status == "scheduled") &
                                 or_(DiscoveryJob.scheduled_for.is_(None),
                                     DiscoveryJob.scheduled_for <= now)),
                                ((DiscoveryJob.status == "running") &
                                 or_(DiscoveryJob.location_scan_lease_until.is_(None),
                                     DiscoveryJob.location_scan_lease_until <= now))))
                     .order_by(DiscoveryJob.created_at, DiscoveryJob.id).limit(1))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        job = session.scalars(statement).first()
        if job is None:
            return None
        job.status = "running"
        job.started_at = job.started_at or now
        job.location_scan_attempt_count += 1
        job.progress_message = "Searching nearby map listings"
        job.progress_updated_at = now
        job.location_scan_lease_until = now + timedelta(minutes=LEASE_MINUTES)
        job.location_scan_token = uuid4().hex
        return job.id, job.location_scan_token


def _lock_location_scan_attempt(session, job_id: int, attempt_token: str, now):
    statement = select(DiscoveryJob).where(DiscoveryJob.id == job_id)
    if engine.dialect.name == "postgresql":
        statement = statement.with_for_update()
    job = session.scalars(statement).first()
    if (job is None or job.stage != "company_homepage_discovery" or job.status != "running" or
            not attempt_token or job.location_scan_token != attempt_token):
        return None
    lease = job.location_scan_lease_until
    if lease is None:
        return None
    if lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    return job if lease > now else None


def _update_location_search_progress(job_id: int, attempt_token: str,
                                     message: str, *, companies_found: int | None = None,
                                     homepages_found: int | None = None) -> bool:
    """Persist a source-search phase while its worker still owns the location lease."""
    now = utcnow()
    with SessionLocal.begin() as session:
        job = _lock_location_scan_attempt(session, job_id, attempt_token, now)
        if job is None:
            return False
        job.location_scan_lease_until = now + timedelta(minutes=LEASE_MINUTES)
        job.progress_message = message[:500]
        job.progress_updated_at = now
        if companies_found is not None:
            job.companies_found = max(0, int(companies_found))
        if homepages_found is not None:
            job.homepages_found = max(0, int(homepages_found))
        return True


def _persist_location_companies(job_id: int, attempt_token: str, candidates: list[dict], *,
                                map_cursor=None, country_state=None) -> None:
    """Save nearby companies and homepage evidence, then queue homepages for career checks."""
    now = utcnow()
    with SessionLocal.begin() as session:
        job = _lock_location_scan_attempt(session, job_id, attempt_token, now)
        if job is None:
            return

        imported = {}
        for candidate in candidates:
            source_id = str(candidate.get("source_id") or "").strip()
            name = str(candidate.get("name") or "").strip()
            if not source_id or not name:
                continue
            company = session.scalar(select(Company).where(
                Company.source == "openstreetmap", Company.source_id == source_id))
            new_company = company is None
            website_url = candidate.get("website_url")
            if company is None:
                company = Company(
                    source="openstreetmap", source_id=source_id, name=name,
                    website_url=website_url, domain=candidate.get("domain") if website_url else None,
                    domain_match_method=candidate.get("domain_match_method") if website_url else None,
                    domain_evidence_url=candidate.get("domain_evidence_url") if website_url else None,
                    category=candidate.get("category"), source_url=candidate.get("source_url"),
                    latitude=candidate.get("latitude"), longitude=candidate.get("longitude"),
                    location_precision=candidate.get("location_precision"),
                    location_label=candidate.get("location_label"), career_status="not_checked",
                    next_discovery_at=now + timedelta(days=INTERVAL_DAYS) if website_url else None,
                )
                session.add(company)
            else:
                company.name = name
                company.category = candidate.get("category") or company.category
                company.source_url = candidate.get("source_url") or company.source_url
                if candidate.get("latitude") is not None:
                    company.latitude = candidate["latitude"]
                if candidate.get("longitude") is not None:
                    company.longitude = candidate["longitude"]
                company.location_precision = candidate.get("location_precision") or company.location_precision
                company.location_label = candidate.get("location_label") or company.location_label
                if not company.website_url and website_url:
                    company.website_url = website_url
                    if company.domain_match_method != "manual":
                        company.domain = candidate.get("domain")
                        company.domain_match_method = candidate.get("domain_match_method")
                        company.domain_evidence_url = candidate.get("domain_evidence_url")
            if job.search_scope == "state":
                company.state = job.state
                company.country_code = 'DE'
            elif country_state:
                company.state = country_state
                company.country_code = 'DE'
            fresh_size_evidence = False
            if candidate.get('employee_count'):
                incoming_size = candidate.get('size_evidence', {})
                fresh_size_evidence = (company.employee_count != candidate['employee_count'] or
                                       any((company.size_evidence or {}).get(key) != value
                                           for key, value in incoming_size.items()))
                company.employee_count = candidate['employee_count']
                company.size_evidence = {**(company.size_evidence or {}), **incoming_size}
            session.flush()
            refresh_company_rank(session, company, now,
                                 fresh_employer_evidence=fresh_size_evidence)
            crawl_company = company
            is_domain_alias = False
            if map_cursor is not None and company.domain and company.website_url:
                canonical_id = canonical_company_ids(session, [company.id])[0]
                if canonical_id != company.id:
                    crawl_company = session.get(Company, canonical_id)
                    is_domain_alias = crawl_company is not None
                    if crawl_company is not None:
                        crawl_company.country_code = 'DE'
                        root_fresh_size_evidence = False
                        if (company.employee_count or 0) > (crawl_company.employee_count or 0):
                            root_fresh_size_evidence = True
                            crawl_company.employee_count = company.employee_count
                            crawl_company.size_evidence = {**(crawl_company.size_evidence or {}),
                                                           **(company.size_evidence or {})}
                        elif company.employee_count and any(
                                (crawl_company.size_evidence or {}).get(key) != value
                                for key, value in (company.size_evidence or {}).items()):
                            root_fresh_size_evidence = True
                            crawl_company.size_evidence = {**(crawl_company.size_evidence or {}),
                                                           **(company.size_evidence or {})}
                        if not crawl_company.website_url:
                            crawl_company.website_url = company.website_url
                            if crawl_company.domain_match_method != "manual":
                                crawl_company.domain = company.domain
                                crawl_company.domain_match_method = company.domain_match_method
                                crawl_company.domain_evidence_url = company.domain_evidence_url
                        refresh_company_rank(session, crawl_company, now,
                                             fresh_employer_evidence=root_fresh_size_evidence)
                        # Office rows remain searchable, but share the canonical
                        # website's rank and next page-check time.
                        company.crawl_priority = crawl_company.crawl_priority
                        company.crawl_interval_days = crawl_company.crawl_interval_days
                        company.crawl_reason = crawl_company.crawl_reason
                        company.crawl_paused = crawl_company.crawl_paused
                        company.next_discovery_at = crawl_company.next_discovery_at
            if map_cursor is not None and not company.website_url:
                tags = _homepage_source_tags(candidate.get('tags'))
                company.homepage_source_tags = tags or None
                if (company.homepage_resolution_next_at is None and
                        company.homepage_resolution_attempts < HOMEPAGE_RESOLUTION_MAX_ATTEMPTS):
                    company.homepage_resolution_next_at = _homepage_resolution_due(company, candidate, now)
            elif company.website_url:
                company.homepage_source_tags = None
                company.homepage_resolution_next_at = None
                company.homepage_resolution_lease_until = None
            if map_cursor is not None:
                # Record every distinct mapped office, but queue at most one page
                # crawl for an employer domain in the national inventory.
                recorded_item = session.scalar(select(DiscoveryJobCompany).where(
                    DiscoveryJobCompany.discovery_job_id == job.id, DiscoveryJobCompany.company_id == company.id))
                if is_domain_alias:
                    if recorded_item is None:
                        job.companies_found += 1
                        session.add(DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                                                        status='skipped'))
                existing_item = session.scalar(select(DiscoveryJobCompany).where(
                    DiscoveryJobCompany.discovery_job_id == job.id,
                    DiscoveryJobCompany.company_id == crawl_company.id))
                if existing_item is None:
                    if not is_domain_alias:
                        job.companies_found += 1
                    if crawl_company.website_url and not crawl_company.crawl_paused:
                        # Spread first checks over five minutes to smooth map-search
                        # bursts, rather than holding them for up to a day. Keep an
                        # existing company's rank schedule unless this is a new
                        # company or it has no prior crawl date.
                        if new_company or crawl_company.next_discovery_at is None:
                            crawl_company.next_discovery_at = _national_first_crawl_due(
                                job.id, crawl_company.id, now)
                        status = 'queued'
                        job.candidate_total += 1
                        job.homepages_found += 1
                    else:
                        status = 'skipped'
                    session.add(DiscoveryJobCompany(discovery_job_id=job.id,
                                                    company_id=crawl_company.id, status=status))
                elif crawl_company.website_url and not crawl_company.crawl_paused and existing_item.status == 'skipped':
                    existing_item.status = 'queued'
                    existing_item.finished_at = None
                    job.candidate_total += 1
                    job.homepages_found += 1
                    if new_company or crawl_company.next_discovery_at is None:
                        crawl_company.next_discovery_at = _national_first_crawl_due(
                            job.id, crawl_company.id, now)
                session.flush()
            imported[source_id] = bool(website_url)

        session.flush()
        if map_cursor is not None:
            job.map_cursor = map_cursor
            job.location_scan_lease_until = None
            job.location_scan_token = None
            job.location_scan_attempt_count = 0
            job.error = None
            job.progress_updated_at = now
            if map_cursor.get('done'):
                job.stage = 'career_page_discovery'
                job.status = 'queued'
                job.scheduled_for = None
                if job.processed_count >= job.candidate_total:
                    job.stage = 'complete'
                    job.status = 'partial' if job.failed_count else 'completed'
                    job.finished_at = now
                job.progress_message = 'Germany map inventory complete; ranked company checks continue'
            else:
                job.status = 'scheduled'
                job.scheduled_for = now + timedelta(seconds=NATIONAL_MAP_INTERVAL_SECONDS)
                job.progress_message = (f"Germany: {map_cursor.get('completed_sections',0)} map sections saved; "
                                        f"state {min(16,map_cursor.get('state_index',0)+1)} of 16; ranked checks continue")
            return
        company_ids = company_ids_for_search(session, job)
        job.candidate_total = len(company_ids)
        job.processed_count = 0
        job.succeeded_count = 0
        job.failed_count = 0
        job.jobs_found = 0
        job.companies_found = len(imported)
        job.homepages_found = sum(imported.values())
        job.location_scan_lease_until = None
        job.location_scan_token = None
        job.error = None
        job.stage = "career_page_discovery"
        job.progress_message = "Company search complete; checking company websites for jobs"
        job.progress_updated_at = now
        if company_ids:
            session.add_all(DiscoveryJobCompany(discovery_job_id=job.id, company_id=company_id,
                                                status="queued") for company_id in company_ids)
            job.status = "queued"
        else:
            job.status = "completed"
            job.stage = "complete"
            job.finished_at = now
            job.progress_message = "Search complete"


def _fail_location_company_search(job_id: int, attempt_token: str,
                                  error: Exception) -> datetime | None:
    now = utcnow()
    with SessionLocal.begin() as session:
        job = _lock_location_scan_attempt(session, job_id, attempt_token, now)
        if job is None:
            return None
        detail = str(error)[:2000]
        if (is_retryable_overpass_error(error) and
                (job.search_scope == "country" or
                 job.location_scan_attempt_count < LOCATION_SCAN_MAX_ATTEMPTS)):
            retry_at = _retry_time(job.location_scan_attempt_count)
            job.status = "scheduled"
            job.scheduled_for = retry_at
            job.finished_at = None
            job.location_scan_lease_until = None
            job.location_scan_token = None
            job.error = detail
            job.progress_message = f"Temporary map service failure; retry scheduled for {retry_at.isoformat()}"
            job.progress_updated_at = now
            return retry_at
        job.status = "failed"
        job.finished_at = now
        job.location_scan_lease_until = None
        job.location_scan_token = None
        job.error = detail
        job.progress_message = "Company and website search could not be completed"
        job.progress_updated_at = now
        return None


def _register_known_career_pages(session, now, company_ids=None) -> None:
    """Upgrade saved career-only discoveries to directly refreshable sources."""
    statement = select(Company).where(
        Company.career_url.is_not(None), Company.career_url != "",
        Company.career_status.in_(_POSITIVE_CAREER_STATUSES), ~Company.feeds.any(),
    )
    if company_ids is not None:
        statement = statement.where(Company.id.in_(company_ids))
    for company in session.scalars(statement.with_for_update()).all():
        session.add(JobFeed(company_id=company.id, provider="html_jobs",
                            board_url=company.career_url, feed_url=company.career_url,
                            status="pending", job_count=0, next_scan_at=now))
    session.flush()


def _enqueue_known_sources(session, job, now) -> None:
    company_ids = company_ids_for_search(session, job)
    _register_known_career_pages(session, now, company_ids)
    feeds = session.scalars(select(JobFeed).where(JobFeed.company_id.in_(company_ids))).all()
    scheduled = 0
    for feed in feeds:
        # Preserve HTTP backoff and active leases; retries stay with the feed worker.
        if feed.attempt_count or feed.lease_until is not None:
            continue
        due = feed.next_scan_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due is None or due > now:
            feed.next_scan_at = now
        scheduled += 1
    queue_arbeitsagentur_scope(session, job, job.scheduled_for or now)
    job.status = "completed"
    job.stage = "complete"
    job.started_at = job.started_at or now
    job.finished_at = now
    job.progress_message = f"Scheduled {scheduled} known feeds and career pages for refresh"
    job.progress_updated_at = now


def _refresh_queued_recurring_jobs(now=None) -> None:
    """Convert legacy recurring campaigns without restarting expired crawls."""
    now = now or utcnow()
    with SessionLocal.begin() as session:
        jobs = session.scalars(select(DiscoveryJob).where(
            DiscoveryJob.kind == "recurring", DiscoveryJob.status.in_(("queued", "scheduled", "running")),
            or_(DiscoveryJob.scheduled_for.is_(None), DiscoveryJob.scheduled_for <= now),
        ).with_for_update()).all()
        for job in jobs:
            leases = [job.location_scan_lease_until]
            leases.extend(item.company.discovery_lease_until for item in job.companies
                          if item.status == "running")
            if job.status == "running" and any(
                    lease is not None and
                    (lease if lease.tzinfo else lease.replace(tzinfo=timezone.utc)) > now
                    for lease in leases):
                continue
            _enqueue_known_sources(session, job, now)
            job.location_scan_lease_until = None
            job.location_scan_token = None
            for item in job.companies:
                if item.status in {"queued", "running"}:
                    item.status = "cancelled"
                    item.finished_at = now
                    run = item.discovery_run
                    if run is not None and run.status == "running":
                        run.status = "cancelled"
                        run.finished_at = now
                        item.company.discovery_lease_until = None
        _register_known_career_pages(session, now)


def schedule_due_locations(now=None) -> int:
    """Materialize due recurring location schedules into durable discovery jobs."""
    now = now or utcnow()
    created = 0
    with SessionLocal.begin() as session:
        statement = (select(ConfiguredLocation)
                     .where(ConfiguredLocation.enabled.is_(True),
                            ConfiguredLocation.next_run_at.is_not(None),
                            ConfiguredLocation.next_run_at <= now)
                     .order_by(ConfiguredLocation.next_run_at, ConfiguredLocation.id)
                     .limit(25))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        statement = statement.where(ConfiguredLocation.search_scope != 'country')
        locations = session.scalars(statement).all()
        for location in locations:
            active = session.scalar(select(DiscoveryJob.id).where(
                DiscoveryJob.configured_location_id == location.id,
                DiscoveryJob.status.in_(("scheduled", "queued", "running")),
            ).limit(1))
            if active is None and location.search_scope != "country":
                job = create_discovery_job(
                    session, label=location.label, search_scope=location.search_scope, city=location.city, state=location.state,
                    postcode=location.postcode, latitude=location.latitude, longitude=location.longitude,
                    radius_km=location.radius_km, configured_location_id=location.id,
                    kind="recurring", scheduled_for=now, now=now, stage="feed_refresh",
                )
                _enqueue_known_sources(session, job, now)
                created += 1
            if location.search_scope != 'country':
                advance_location_schedule(location, now)
    return created


def _claim_campaign_company(now=None) -> tuple[int, int, int, int] | None:
    now = now or utcnow()
    stale_before = now - timedelta(minutes=LEASE_MINUTES)
    with SessionLocal.begin() as session:
        paused_statement = (select(DiscoveryJobCompany)
                            .join(Company, Company.id == DiscoveryJobCompany.company_id)
                            .join(DiscoveryJob, DiscoveryJob.id == DiscoveryJobCompany.discovery_job_id)
                            .where(DiscoveryJobCompany.status == "queued",
                                   DiscoveryJob.search_scope == "country",
                                   DiscoveryJob.stage.in_(("company_homepage_discovery", "career_page_discovery")),
                                   DiscoveryJob.status.in_(("scheduled", "queued", "running")),
                                   Company.crawl_paused.is_(True))
                            .order_by(DiscoveryJobCompany.id).limit(100))
        if engine.dialect.name == "postgresql":
            paused_statement = paused_statement.with_for_update(
                skip_locked=True, of=(DiscoveryJobCompany, Company))
        for item in session.scalars(paused_statement).all():
            item.status = "skipped"
            item.finished_at = now
            job = session.get(DiscoveryJob, item.discovery_job_id)
            if job is None:
                continue
            job.processed_count += 1
            job.progress_message = "Skipping companies parked by the relevance ranking"
            job.progress_updated_at = now
            if job.stage == "career_page_discovery" and job.processed_count >= job.candidate_total:
                job.finished_at = now
                job.status = "partial" if job.failed_count else "completed"
                job.stage = "complete"
                job.progress_message = "Search complete" if not job.failed_count else "Search finished with some issues"

        stale_statement = (select(DiscoveryJobCompany)
                           .join(Company, Company.id == DiscoveryJobCompany.company_id)
                           .join(DiscoveryJob, DiscoveryJob.id == DiscoveryJobCompany.discovery_job_id)
                           .where(DiscoveryJobCompany.status == "running",
                                  or_(DiscoveryJob.stage == "career_page_discovery",
                                      (DiscoveryJob.search_scope == "country") & (DiscoveryJob.stage == "company_homepage_discovery")),
                                  DiscoveryJob.kind != "recurring",
                                  DiscoveryJob.status.in_(("scheduled", "queued", "running")),
                                  DiscoveryJobCompany.started_at < stale_before)
                           .order_by(DiscoveryJobCompany.started_at, DiscoveryJobCompany.id)
                           .limit(100))
        if engine.dialect.name == "postgresql":
            stale_statement = stale_statement.with_for_update(
                skip_locked=True, of=(DiscoveryJobCompany, Company))
        stale_items = session.scalars(stale_statement).all()
        for item in stale_items:
            item.status = "queued"
            item.started_at = None
            if item.discovery_run_id:
                stale_run = session.get(DiscoveryRun, item.discovery_run_id)
                if stale_run is not None and stale_run.status == "running":
                    stale_run.status = "failed"
                    stale_run.error = "Worker lease expired; campaign task was requeued."
                    stale_run.finished_at = now
            item.discovery_run_id = None
            company = session.get(Company, item.company_id)
            if company is not None and company.discovery_lease_until is not None:
                lease = company.discovery_lease_until
                if lease.tzinfo is None:
                    lease = lease.replace(tzinfo=timezone.utc)
                if lease <= now:
                    company.discovery_lease_until = None

        # The session factory disables autoflush. Make the requeued status
        # visible to the queued-task query in this same transaction.
        if stale_items:
            session.flush()

        # Existing state/radius campaigns may predate domain-level scheduling.
        # Repoint their queued office aliases to one canonical website task.
        alias_statement = (select(DiscoveryJobCompany)
                           .join(Company, Company.id == DiscoveryJobCompany.company_id)
                           .join(DiscoveryJob, DiscoveryJob.id == DiscoveryJobCompany.discovery_job_id)
                           .where(DiscoveryJobCompany.status == "queued", Company.domain.is_not(None),
                                  DiscoveryJob.search_scope == "country",
                                  DiscoveryJob.status.in_(("scheduled", "queued", "running")))
                           .order_by(DiscoveryJobCompany.id).limit(100))
        if engine.dialect.name == "postgresql":
            alias_statement = alias_statement.with_for_update(skip_locked=True,
                                                               of=(DiscoveryJobCompany,))
        alias_items = session.scalars(alias_statement).all()
        roots = canonical_company_map(session, [item.company_id for item in alias_items])
        for alias_item in alias_items:
            canonical_id = roots.get(alias_item.company_id, alias_item.company_id)
            if canonical_id == alias_item.company_id:
                continue
            duplicate = session.scalar(select(DiscoveryJobCompany).where(
                DiscoveryJobCompany.discovery_job_id == alias_item.discovery_job_id,
                DiscoveryJobCompany.company_id == canonical_id))
            if duplicate is not None:
                alias_item.status = "skipped"
                alias_item.finished_at = now
                alias_item.error = "A company with this website is already checked in this search."
                job = session.get(DiscoveryJob, alias_item.discovery_job_id)
                if job is not None:
                    job.processed_count += 1
                    job.progress_updated_at = now
            else:
                alias_item.company_id = canonical_id
        if alias_items:
            session.flush()

        statement = (select(DiscoveryJobCompany)
                     .join(DiscoveryJob, DiscoveryJob.id == DiscoveryJobCompany.discovery_job_id)
                     .join(Company, Company.id == DiscoveryJobCompany.company_id)
                     .where(DiscoveryJobCompany.status == "queued",
                            or_(DiscoveryJob.stage == "career_page_discovery",
                                (DiscoveryJob.search_scope == "country") & (DiscoveryJob.stage == "company_homepage_discovery")),
                            DiscoveryJob.kind != "recurring",
                            DiscoveryJob.status.in_(("scheduled", "queued", "running")),
                            or_(DiscoveryJob.search_scope == "country", DiscoveryJob.scheduled_for.is_(None), DiscoveryJob.scheduled_for <= now),
                            or_(DiscoveryJob.search_scope != "country", Company.next_discovery_at.is_(None), Company.next_discovery_at <= now),
                            or_(Company.discovery_lease_until.is_(None), Company.discovery_lease_until < now))
                     .order_by(DiscoveryJob.scheduled_for.asc().nullsfirst(),
                               Company.crawl_priority.desc(),
                               Company.next_discovery_at.asc().nullsfirst(), DiscoveryJob.id.asc(),
                               DiscoveryJobCompany.id.asc())
                     .limit(1))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True, of=(DiscoveryJobCompany, Company))
        item = session.scalars(statement).first()
        if item is None:
            return None
        job = session.scalar(select(DiscoveryJob).where(
            DiscoveryJob.id == item.discovery_job_id).with_for_update())
        company = session.get(Company, item.company_id)
        if job is None or company is None:
            item.status = "failed"
            item.error = "Discovery job company or company record was deleted."
            item.finished_at = now
            if job is not None:
                job.processed_count += 1
                job.failed_count += 1
            return None
        if ((job.stage != "career_page_discovery" and not (job.search_scope == "country" and job.stage == "company_homepage_discovery")) or
                job.status not in {"scheduled", "queued", "running"}):
            return None

        company.discovery_lease_until = now + timedelta(minutes=LEASE_MINUTES)
        company.discovery_attempt_count += 1
        run = DiscoveryRun(company_id=company.id, started_at=now, status="running")
        session.add(run)
        session.flush()
        item.status = "running"
        item.started_at = now
        item.discovery_run_id = run.id
        if job.search_scope != "country" or job.stage != "company_homepage_discovery":
            job.status = "running"
        job.started_at = job.started_at or now
        job.progress_message = f"Checking {company.name}'s website for jobs"[:500]
        job.progress_updated_at = now
        return company.id, run.id, item.id, job.id


def _complete_due_empty_jobs(now=None) -> int:
    now = now or utcnow()
    with SessionLocal.begin() as session:
        jobs = session.scalars(select(DiscoveryJob).where(
            DiscoveryJob.status == "scheduled", DiscoveryJob.candidate_total == 0,
            DiscoveryJob.stage == "career_page_discovery",
            DiscoveryJob.scheduled_for.is_not(None), DiscoveryJob.scheduled_for <= now,
        )).all()
        for job in jobs:
            job.status = "completed"
            job.started_at = job.started_at or now
            job.finished_at = now
            job.progress_message = "Search complete"
            job.progress_updated_at = now
        return len(jobs)


def _finish_campaign_company(item_id: int, run_id: int, jobs_found: int = 0,
                             error: Exception | None = None) -> None:
    now = utcnow()
    with SessionLocal.begin() as session:
        item = session.scalar(select(DiscoveryJobCompany).where(
            DiscoveryJobCompany.id == item_id).with_for_update())
        if item is None or item.status != "running" or item.discovery_run_id != run_id:
            return
        job = session.scalar(select(DiscoveryJob).where(
            DiscoveryJob.id == item.discovery_job_id).with_for_update())
        if job is not None and job.status == "cancelled":
            company = session.get(Company, item.company_id)
            run = session.get(DiscoveryRun, run_id)
            if company is not None and run is not None:
                _discard_cancelled_campaign_attempt(company, run, item, now)
            else:
                item.status = "cancelled"
                item.finished_at = now
            return
        if job is None or job.status not in {"scheduled", "queued", "running"}:
            return
        item.finished_at = now
        item.jobs_found = max(0, int(jobs_found))
        item.error = str(error)[:2000] if error else None
        item.status = "failed" if error else "completed"
        job.processed_count += 1
        if error:
            job.failed_count += 1
            job.error = str(error)[:2000]
        else:
            job.succeeded_count += 1
            job.jobs_found += item.jobs_found
        if job.processed_count >= job.candidate_total and not (job.search_scope == "country" and job.stage == "company_homepage_discovery"):
            job.finished_at = now
            if job.failed_count == job.candidate_total and job.candidate_total:
                job.status = "failed"
            elif job.failed_count:
                job.status = "partial"
            else:
                job.status = "completed"
            job.stage = "complete"
            job.progress_message = "Search complete" if not job.failed_count else "Search finished with some issues"
        else:
            job.progress_message = f"Checked {job.processed_count} of {job.candidate_total} company websites"
        job.progress_updated_at = now


def _prepare_cycle() -> tuple[int, str] | None:
    try:
        schedule_due_locations()
        _refresh_queued_recurring_jobs()
        _complete_due_empty_jobs()
    except Exception:
        LOG.exception("could not materialize scheduled location discovery jobs")
    return _claim_location_company_search()


def _process_location_company_search(location_attempt: tuple[int, str]) -> None:
    location_job_id, attempt_token = location_attempt
    try:
        with SessionLocal() as session:
            job = session.get(DiscoveryJob, location_job_id)
            if job is None:
                return
            scope = (job.latitude, job.longitude, job.radius_km * 1000)
            state = job.state if job.search_scope == "state" else None
            country = job.search_scope == "country"
            cursor = job.map_cursor if country else None

        def report_progress(message: str, **counts) -> bool:
            return _update_location_search_progress(location_job_id, attempt_token, message, **counts)

        if country:
            report_progress(f"Germany: {cursor.get('completed_sections',0)} map sections saved; "
                            f"state {min(16,cursor.get('state_index',0)+1)} of 16; ranked checks continue")
            updated, candidates, country_state = advance_map_cursor(cursor, report_progress)
            _persist_location_companies(location_job_id, attempt_token, candidates,
                                        map_cursor=updated, country_state=country_state)
            return
        options = {"state": state} if state else {}
        candidates = fetch_location_companies(*scope, progress_callback=report_progress, **options)
        _persist_location_companies(location_job_id, attempt_token, candidates)
        LOG.info("location company search id=%s companies=%s homepages=%s",
                 location_job_id, len(candidates),
                 sum(bool(row.get("website_url")) for row in candidates))
    except LocationSearchCancelled:
        LOG.info('Stopped cancelled or superseded location search id=%s', location_job_id)
    except Exception as error:
        retry_at = _fail_location_company_search(location_job_id, attempt_token, error)
        if retry_at is not None:
            LOG.warning("location company and homepage search failed id=%s; retry scheduled at %s: %s",
                        location_job_id, retry_at.isoformat(), error)
        else:
            LOG.exception("location company and homepage search failed id=%s", location_job_id)


def _process_claimed_company(claim: tuple[int, int, int | None, int | None]) -> bool:
    company_id, run_id, item_id, _job_id = claim
    if item_id is not None and not _campaign_attempt_is_live(item_id, run_id):
        _finish_campaign_company(item_id, run_id)
        return True

    with SessionLocal() as session:
        company = session.get(Company, company_id)
        job = session.get(DiscoveryJob, _job_id) if _job_id is not None else None
        preferred_locations = ([value for value in (job.city, job.label) if value]
                               if job is not None else [])
        seed = ({"name": company.name, "website": company.website_url,
                 "category": company.category, "latitude": company.latitude,
                 "longitude": company.longitude, "source_id": company.source_id,
                 "preferred_locations": preferred_locations}
                if company is not None and company.website_url else None)
    if seed is None:
        error = ValueError("Company no longer has a website URL")

        def record_missing_website() -> None:
            _record_failure(company_id, run_id, error, campaign_item_id=item_id)
            if item_id is not None:
                _finish_campaign_company(item_id, run_id, error=error)

        if engine.dialect.name == "sqlite":
            with _SQLITE_FINALIZE_LOCK:
                record_missing_website()
        else:
            record_missing_website()
        return True
    try:
        capture_dir = CAPTURE_DIR / f"company-{company_id}"
        client = Client(capture_dir, timeout=TIMEOUT_SECONDS, delay=REQUEST_DELAY_SECONDS,
                        max_requests=MAX_REQUESTS, user_agent=USER_AGENT, origin_pacer=_ORIGIN_PACER)
        result = discover(seed, client, max_pages=MAX_PAGES, max_depth=MAX_DEPTH)

        def persist_result() -> int | None:
            jobs = _persist_discovery(company_id, run_id, result, campaign_item_id=item_id)
            if item_id is not None:
                _finish_campaign_company(item_id, run_id, jobs_found=jobs or 0)
            return jobs

        if engine.dialect.name == "sqlite":
            with _SQLITE_FINALIZE_LOCK:
                jobs = persist_result()
        else:
            jobs = persist_result()
        LOG.info("company discovery id=%s name=%r status=%s pages=%s jobs=%s",
                 company_id, seed["name"], result.get("status"), len(result.get("pages", [])), jobs)
    except Exception as error:
        LOG.exception("company discovery failed id=%s name=%r", company_id, seed["name"])

        def record_failure() -> None:
            _record_failure(company_id, run_id, error, campaign_item_id=item_id)
            if item_id is not None:
                _finish_campaign_company(item_id, run_id, error=error)

        if engine.dialect.name == "sqlite":
            with _SQLITE_FINALIZE_LOCK:
                record_failure()
        else:
            record_failure()
    return True


def process_once(*, location_jobs_only: bool = False) -> bool:
    location_attempt = _prepare_cycle()
    if location_attempt is not None:
        _process_location_company_search(location_attempt)
        return True
    campaign = _claim_campaign_company()
    if campaign is not None:
        return _process_claimed_company(campaign)
    if location_jobs_only:
        homepage = _claim_due_homepage_resolution()
        return _process_homepage_resolution(homepage) if homepage is not None else False
    claimed = _claim_due_company()
    if claimed is not None:
        return _process_claimed_company((*claimed, None, None))
    homepage = _claim_due_homepage_resolution()
    return _process_homepage_resolution(homepage) if homepage is not None else False


def process_batch(*, location_jobs_only: bool = False,
                  max_workers: int = MAX_CRAWL_WORKERS) -> bool:
    """Claim a bounded batch serially, then crawl distinct companies concurrently."""
    location_attempt = _prepare_cycle()
    if location_attempt is not None:
        _process_location_company_search(location_attempt)
        return True

    worker_count = min(MAX_CRAWL_WORKER_LIMIT, max(1, int(max_workers)))
    claims = []
    for _ in range(worker_count):
        try:
            claim = _claim_campaign_company()
            if claim is not None:
                claims.append(claim)
                continue
            if location_jobs_only:
                break
            due_company = _claim_due_company()
            if due_company is None:
                break
            claims.append((*due_company, None, None))
        except (DBAPIError, DatabasePoolTimeout) as error:
            if not is_transient_database_error(error):
                raise
            LOG.warning('Discovery claim failed; finishing already claimed work', exc_info=True)
            break

    # Keep reverse lookups behind due career-page checks and cap their throughput
    # to one company per batch, independently of the crawl worker count.
    homepage_claim = None
    if len(claims) < worker_count:
        try:
            homepage_claim = _claim_due_homepage_resolution()
        except (DBAPIError, DatabasePoolTimeout) as error:
            if not is_transient_database_error(error):
                raise
            LOG.warning('Homepage claim failed; finishing already claimed work', exc_info=True)

    if not claims:
        return _process_homepage_resolution(homepage_claim) if homepage_claim is not None else False
    if len(claims) == 1:
        _process_claimed_company(claims[0])
    else:
        with ThreadPoolExecutor(max_workers=len(claims), thread_name_prefix="career-discovery") as pool:
            futures = [pool.submit(_process_claimed_company, claim) for claim in claims]
            for future in futures:
                future.result()
    if homepage_claim is not None:
        _process_homepage_resolution(homepage_claim)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one due company and exit")
    parser.add_argument("--location-jobs-only", action="store_true",
                        help="Skip the site-wide company rescan backlog")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.once:
        process_once(location_jobs_only=args.location_jobs_only)
        return
    LOG.info("career discovery worker started; interval=%s days pages=%s depth=%s workers=%s location_jobs_only=%s",
             INTERVAL_DAYS, MAX_PAGES, MAX_DEPTH, MAX_CRAWL_WORKERS, args.location_jobs_only)
    while True:
        try:
            worked = process_batch(location_jobs_only=args.location_jobs_only)
        except (DBAPIError, DatabasePoolTimeout) as error:
            if not is_transient_database_error(error):
                raise
            LOG.warning('Discovery database cycle failed; backing off before retry', exc_info=True)
            worked = False
        time.sleep(CRAWL_BATCH_DELAY_SECONDS if worked else POLL_SECONDS)


if __name__ == "__main__":
    main()
