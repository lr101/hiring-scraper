"""Scheduled, single-feed-at-a-time refresh worker."""
from __future__ import annotations

import logging
import json
import os
import random
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from sqlalchemy import and_, case, select, or_, update
from sqlalchemy.exc import DBAPIError, OperationalError, TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import selectinload

from hiring_scraper.app.enrichment import refresh_enrichment, preserve_verified_detail
from hiring_scraper.app.database import SessionLocal, engine, is_transient_database_error
from hiring_scraper.app.crawl_ranking import refresh_company_rank, next_company_due, next_feed_due
from hiring_scraper.app.models import (Company, ConfiguredLocation, Job, JobFeed, JobLocation,
                                      ScanRun, utcnow)
from hiring_scraper.app.job_search import (
    job_search_signature, refresh_application_matches_for_jobs, replace_job_locations,
)
from hiring_scraper.app.feed_schedules import (
    is_synthetic_germany_scope, next_scope_due, scope_due_at,
)
from hiring_scraper.geography import haversine_m
from hiring_scraper.app.employers import EmployerUpsertDeferred, upsert_job_employer
from hiring_scraper.app.default_job_feeds import ensure_default_job_feeds
from hiring_scraper.ats import fetch_feed, parse_feed
from hiring_scraper.browser import BROWSER_CLIENT_REQUEST_BUDGET
from hiring_scraper.http import Client
from hiring_scraper.crawl_policy import ORIGIN_PACER


LOG = logging.getLogger("hiring_scraper.worker")
POLL_SECONDS = max(3, int(os.getenv("SCAN_POLL_SECONDS", "3")))
SCAN_INTERVAL_HOURS = max(1, int(os.getenv("FEED_SCAN_INTERVAL_HOURS", "6")))
NEW_HTML_FEED_PRIORITY_SECONDS = 300
OVERDUE_FEED_PRIORITY_SECONDS = 900
REQUEST_DELAY_SECONDS = max(1.0, float(os.getenv("CRAWL_DELAY_SECONDS", "1")))
LEASE_MINUTES = max(2, int(os.getenv("FEED_SCAN_LEASE_MINUTES", "5")))
CAPTURE_DIR = Path(os.getenv("SCAN_CAPTURE_DIR", "/tmp/hiring-scraper-feed-captures"))
USER_AGENT = os.getenv("HIRING_USER_AGENT", "HiringScraper/0.2 (public job-feed refresh)")
_ORIGIN_LOCK = Lock()
_LAST_ORIGIN_REQUEST: dict[str, float] = {}
SQLITE_BUSY_RETRIES = 4


def _is_sqlite_busy(error: Exception) -> bool:
    if engine.dialect.name != "sqlite":
        return False
    driver_error = getattr(error, "orig", error)
    code = getattr(driver_error, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(driver_error).casefold()
    return "database is locked" in message or "database table is locked" in message


def _retry_sqlite_busy(operation, *, action: str):
    for attempt in range(SQLITE_BUSY_RETRIES):
        try:
            return operation()
        except (OperationalError, sqlite3.OperationalError) as error:
            if not _is_sqlite_busy(error) or attempt + 1 == SQLITE_BUSY_RETRIES:
                raise
            delay = min(2.0, 0.25 * (2 ** attempt)) + random.uniform(0, 0.15)
            LOG.warning("SQLite is busy while %s; retrying in %.2fs (%s/%s)",
                        action, delay, attempt + 1, SQLITE_BUSY_RETRIES)
            time.sleep(delay)


def _claim_due_feed() -> tuple[int, int, int, dict | None] | None:
    now = utcnow()
    recently_queued_html = and_(
        JobFeed.provider.in_({"html_jobs", "browser_html"}),
        JobFeed.last_checked_at.is_(None),
        JobFeed.next_scan_at >= now - timedelta(seconds=NEW_HTML_FEED_PRIORITY_SECONDS),
    )
    overdue_feed = JobFeed.next_scan_at <= now - timedelta(seconds=OVERDUE_FEED_PRIORITY_SECONDS)
    with SessionLocal.begin() as session:
        statement = (select(JobFeed).join(Company)
                     .where(Company.crawl_paused.is_(False), JobFeed.enabled.is_(True),
                            JobFeed.next_scan_at.is_not(None), JobFeed.next_scan_at <= now,
                            or_(JobFeed.lease_until.is_(None), JobFeed.lease_until < now))
                     .order_by(case((overdue_feed, 0), else_=1),
                               case((recently_queued_html, 0), else_=1),
                               JobFeed.next_scan_at,
                               case((Company.source == "default_job_feed", 0), else_=1),
                               Company.crawl_priority.desc(), JobFeed.id).limit(1))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True, of=JobFeed)
        feed = session.scalars(statement).first()
        if feed is None:
            return None
        for expired in session.scalars(select(ScanRun).where(
                ScanRun.feed_id == feed.id, ScanRun.status == 'running')):
            _requeue_search_scope(feed, expired, now)
            expired.status = 'failed'
            expired.finished_at = now
            expired.error = 'Scan lease expired; a newer attempt owns this feed.'
        search_scope = None
        if feed.provider == "arbeitsagentur":
            pending = list(feed.pending_search_scopes) if isinstance(feed.pending_search_scopes, list) else []
            pending = [item for item in pending if isinstance(item, dict)]
            due_indexes = [index for index, item in enumerate(pending)
                           if scope_due_at(item, now) <= now]
            if due_indexes:
                selected_index = min(due_indexes, key=lambda index: scope_due_at(pending[index], now))
                search_scope = pending.pop(selected_index)
            else:
                # Managed AfA searches only run with a saved geography. A
                # regular feed cadence must never fall back to an unfiltered
                # national query when no scheduled scope is due.
                feed.pending_search_scopes = pending
                feed.next_scan_at = next_scope_due(pending, now)
                return None
            feed.pending_search_scopes = pending
        feed.lease_until = now + timedelta(minutes=LEASE_MINUTES)
        feed.last_attempt_at = now
        feed.attempt_count += 1
        scan = ScanRun(feed_id=feed.id, started_at=now, status="running", search_scope=search_scope)
        session.add(scan)
        session.flush()
        return feed.id, scan.id, feed.source_revision, search_scope


def _pace_origin(url: str) -> None:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc.lower()}"
    with _ORIGIN_LOCK:
        now = time.monotonic()
        wait = REQUEST_DELAY_SECONDS - (now - _LAST_ORIGIN_REQUEST.get(origin, 0.0))
        if wait > 0:
            time.sleep(wait)
        _LAST_ORIGIN_REQUEST[origin] = time.monotonic()


def _retry_after(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        delay_seconds = max(0, int(value.strip()))
        return now + timedelta(seconds=min(delay_seconds, 86_400))
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return max(now, moment.astimezone(timezone.utc))
        except (TypeError, ValueError, OverflowError):
            return None


def _backoff(attempt: int, *, incomplete: bool = False) -> datetime:
    now = utcnow()
    if incomplete:
        return now + timedelta(hours=1, minutes=random.randint(0, 15))
    minutes = min(24 * 60, 5 * (2 ** min(max(attempt - 1, 0), 8)))
    return now + timedelta(minutes=minutes, seconds=random.randint(0, 59))


def _request(feed: JobFeed, search_scope: dict | None = None,
             *, run_id: int | None = None) -> tuple[dict, bytes]:
    if feed.provider == "arbeitsagentur":
        return _request_arbeitsagentur(feed, search_scope, run_id=run_id)
    max_requests = (BROWSER_CLIENT_REQUEST_BUDGET if feed.provider == "browser_html" else
                    10 if feed.provider == "lever" else 8)
    client = Client(CAPTURE_DIR, timeout=20, delay=REQUEST_DELAY_SECONDS, origin_pacer=ORIGIN_PACER,
                    max_requests=max_requests, user_agent=USER_AGENT)
    headers = {}
    # A validator for offset zero cannot establish that later offsets are stable.
    # Lever and SmartRecruiters scans therefore fetch all pages after a prior scan.
    if feed.etag and feed.provider not in {"lever", "smartrecruiters"}:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified and feed.provider not in {"lever", "smartrecruiters"}:
        headers["If-Modified-Since"] = feed.last_modified
    metadata, body = fetch_feed(client, feed.provider, feed.feed_url, feed.board_url or feed.feed_url,
                                conditional_headers=headers, pace=_pace_origin)
    if (feed.provider == "rss" and feed.company.source == "default_job_feed" and
            metadata.get("state") == "ok" and metadata.get("status") != 304):
        try:
            parsed = parse_feed("rss", body, metadata.get("final_url") or feed.feed_url)
        except (ValueError, TypeError):
            return metadata, body
        _enrich_default_rss_jobs(client, feed, parsed.get("jobs", []))
        metadata = {**metadata, "parsed_feed": parsed}
    return metadata, body


def _heartbeat_adapter_feed(feed: JobFeed, run_id: int | None) -> None:
    """Renew the ordinary feed lease while a bounded provider adapter runs."""
    now = utcnow()
    with SessionLocal.begin() as session:
        session.execute(update(JobFeed).where(JobFeed.id == feed.id).values(
            source_revision=JobFeed.source_revision).execution_options(synchronize_session=False))
        current = session.get(JobFeed, feed.id, populate_existing=True)
        if (current is None or not current.enabled or current.source_revision != feed.source_revision
                or run_id is None or _current_feed_run(session, feed.id, run_id) is None):
            raise RuntimeError("The job-feed scan lease was lost while the adapter was running.")
        current.lease_until = now + timedelta(minutes=LEASE_MINUTES)


def _request_arbeitsagentur(feed: JobFeed, search_scope: dict | None = None,
                           *, run_id: int | None = None) -> tuple[dict, bytes]:
    """Capture a bounded BA Jobsuche query as an ordinary scheduled feed scan."""
    from hiring_scraper.regional import DEFAULT_TERMS, acquire

    source_config = feed.source_config if isinstance(feed.source_config, dict) else {}
    scope = search_scope if isinstance(search_scope, dict) else {}
    scope_type = scope.get("search_scope")
    city = None
    radius_km = None
    if scope_type == "radius":
        city = str(scope.get("city") or scope.get("label") or "").strip() or None
        radius_km = float(scope.get("radius_km", 35)) if city else None
    elif scope_type == "state":
        city = str(scope.get("state") or scope.get("label") or "").strip() or None
    with tempfile.TemporaryDirectory(prefix="hiring-ba-feed-") as directory:
        capture_dir = Path(directory) / "capture"
        manifest = acquire(
            capture_dir,
            city=city,
            radius_km=radius_km,
            terms=source_config.get("terms") or DEFAULT_TERMS,
            max_pages=2,
            page_size=100,
            max_details=50,
            max_requests=120,
            delay=REQUEST_DELAY_SECONDS,
            progress=lambda: _heartbeat_adapter_feed(feed, run_id),
        )
        snapshot_bytes = (capture_dir / "snapshot.json").read_bytes()
        snapshot = json.loads(snapshot_bytes)
    raw_jobs = snapshot.get("jobs", []) if isinstance(snapshot, dict) else []
    jobs = []
    for row in raw_jobs:
        if not isinstance(row, dict):
            continue
        raw = row.get("raw_metadata")
        raw = dict(raw) if isinstance(raw, dict) else {}
        raw.update({
            "country_code": "DE",
            "hiring_organization": {"name": row.get("company_name")},
            "adapter_scan": {
                "state": manifest.get("state"),
                "search_scope": scope_type or "country",
                "location": city,
                "radius_km": radius_km,
            },
        })
        jobs.append({
            "id": row.get("external_id"), "title": row.get("title"), "url": row.get("url"),
            "description": row.get("description"), "location": row.get("location_text"),
            "locations": row.get("locations", []), "is_remote": row.get("is_remote", False),
            "work_arrangement": row.get("work_arrangement"),
            "employment_type": row.get("employment_type"), "date_posted": row.get("date_posted"),
            "salary": row.get("salary"), "company_name": row.get("company_name"),
            "raw_metadata": raw,
        })
    if manifest.get("stop_reason") == "bot_challenge" or manifest.get("state") == "bot_challenge":
        return ({"state": "bot_challenge", "status": None,
                 "error": "The source returned a bot or CAPTCHA challenge."}, snapshot_bytes)
    capture_state = manifest.get("state")
    complete_capture = capture_state == "complete_selected_search"
    # A bounded or truncated capture still contains useful observations. Keep
    # it as a rolling window so the scope queue can advance, while the false
    # `complete` flag below prevents missing jobs from being closed.
    windowed_capture = complete_capture or (capture_state == "partial" and bool(jobs))
    return ({"state": "ok", "status": 200,
             "parsed_feed": {"jobs": jobs, "complete": False, "windowed": windowed_capture},
             "pagination": {"complete": complete_capture, "state": capture_state,
                            "search_scope": scope_type or "country"}}, snapshot_bytes)


def _matches_location_filter(job_data: dict, location_filter: dict) -> bool:
    """Apply an optional saved radius filter to locations in an ordinary feed."""
    if not location_filter:
        return True
    raw = job_data.get("raw_metadata") if isinstance(job_data.get("raw_metadata"), dict) else {}
    if job_data.get("is_remote") or job_data.get("work_arrangement") == "remote":
        remote_codes = raw.get("remote_country_codes")
        return not isinstance(remote_codes, list) or not remote_codes or any(
            str(code).casefold() == "de" for code in remote_codes)
    center_lat = location_filter.get("latitude")
    center_lon = location_filter.get("longitude")
    try:
        center_lat, center_lon = float(center_lat), float(center_lon)
        radius_km = float(location_filter.get("radius_km", 35))
    except (TypeError, ValueError):
        return False
    city = str(location_filter.get("city") or location_filter.get("query") or "").strip().casefold()
    source_locations = job_data.get("locations")
    if not isinstance(source_locations, list) or not source_locations:
        label = job_data.get("location")
        source_locations = [{"label": label}] if isinstance(label, str) and label.strip() else []
    for location in source_locations:
        if isinstance(location, str):
            location = {"label": location}
        if not isinstance(location, dict):
            continue
        country = str(location.get("country_code") or "").casefold()
        if country and country not in {"de", "deu", "germany", "deutschland"}:
            continue
        latitude, longitude = location.get("latitude"), location.get("longitude")
        if latitude is not None and longitude is not None:
            try:
                if haversine_m(center_lat, center_lon, float(latitude), float(longitude)) <= radius_km * 1000:
                    return True
            except (TypeError, ValueError):
                continue
        elif city and str(location.get("label") or "").split(",", 1)[0].strip().casefold() == city:
            return True
    return False


def _title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _enrich_default_rss_jobs(client: Client, feed: JobFeed, jobs: list[dict]) -> None:
    """Read a few linked posting pages for employer and structured job evidence."""
    from hiring_scraper.app.employers import hiring_organization
    from hiring_scraper.app.board_scope import canonical_vacancy_url
    from hiring_scraper.html_jobs import extract_html_jobs

    previous = {job.external_id: job for job in feed.jobs}
    crawled = 0
    for data in jobs:
        raw = data.get("raw_metadata")
        raw = raw if isinstance(raw, dict) else {}
        if (feed.company.category != "job_board_source" and
                not hiring_organization(data).get("name")):
            raw["hiring_organization"] = {
                "name": feed.company.name,
                **({"url": feed.company.website_url} if feed.company.website_url else {}),
            }
            data["raw_metadata"] = raw
        old = previous.get(str(data.get("id") or ""))
        old_raw = old.raw_metadata if old and isinstance(old.raw_metadata, dict) else {}
        old_crawl = old_raw.get("employer_detail_crawl")
        if old and old.title == data.get("title") and old.url == data.get("url"):
            old_organization = old_raw.get("hiring_organization")
            new_organization = raw.get("hiring_organization")
            if isinstance(old_organization, dict):
                raw["hiring_organization"] = {
                    **old_organization,
                    **(new_organization if isinstance(new_organization, dict) else {}),
                }
            if isinstance(old_crawl, dict):
                raw["employer_detail_crawl"] = old_crawl
                for key, value in (("description", old.description), ("location", old.location_text),
                                   ("locations", [{"label": item.label, "latitude": item.latitude,
                                                   "longitude": item.longitude, "precision": item.precision,
                                                   "country_code": item.country_code}
                                                  for item in old.locations])):
                    if data.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                        data[key] = value
                data["raw_metadata"] = raw
                continue
        if crawled >= 5 or (hiring_organization(data).get("name") and
                            hiring_organization(data).get("url")):
            continue
        job_url = data.get("url")
        if not isinstance(job_url, str):
            continue
        try:
            response, page_body = client.get(job_url)
        except (OSError, ValueError, TypeError):
            continue
        crawled += 1
        raw = data.setdefault("raw_metadata", {})
        if response.get("state") == "bot_challenge":
            raw["employer_detail_crawl"] = {
                "state": "bot_challenge", "reason": "bot_challenge",
            }
            continue
        if response.get("state") != "ok" or response.get("status") != 200:
            continue
        content_type = str(response.get("content_type") or "").casefold()
        if content_type and not any(kind in content_type for kind in ("html", "xhtml")):
            continue
        final_url = response.get("final_url") or job_url
        try:
            extracted = extract_html_jobs(page_body, final_url).get("jobs", [])
        except (ValueError, TypeError):
            continue
        requested_key = canonical_vacancy_url(final_url) or canonical_vacancy_url(job_url)
        matching = [row for row in extracted
                    if requested_key and
                    _title_key(str(row.get("title") or "")) == _title_key(str(data.get("title") or ""))
                    and canonical_vacancy_url(str(row.get("url") or "")) == requested_key]
        if len(matching) != 1:
            continue
        detail = matching[0]
        detail_raw = detail.get("raw_metadata")
        if isinstance(detail_raw, dict):
            raw.update({key: value for key, value in detail_raw.items()
                        if key not in {"source", "source_feed_url", "source_item_id"}})
        raw["employer_detail_crawl"] = {
            "state": "parsed", "url": final_url,
            **({"capture": response["capture"]} if response.get("capture") else {}),
        }
        for key in ("description", "location", "locations", "employment_type", "date_posted",
                    "work_arrangement", "is_remote", "salary"):
            if data.get(key) in (None, "", [], {}) and detail.get(key) not in (None, "", [], {}):
                data[key] = detail[key]


def _location_rows(job_data: dict, existing: list[JobLocation]) -> list[dict]:
    source_locations = job_data.get("locations")
    if not source_locations:
        label = (job_data.get("location") or "").strip()
        source_locations = [{"label": label}] if label else []
    old_by_label = {item.label.casefold(): item for item in existing}
    rows = []
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
        normalized = label.casefold().strip()
        if normalized in {"karlsruhe", "karlsruhe, deutschland", "karlsruhe, germany"} and latitude is None:
            latitude, longitude, precision, country = 49.0068705, 8.4034195, "city_centroid", "de"
        elif normalized in {"berlin", "berlin, deutschland", "berlin, germany"} and latitude is None:
            latitude, longitude, precision, country = 52.5200, 13.4050, "city_centroid", "de"
        rows.append({"label": label, "latitude": latitude, "longitude": longitude,
                     "precision": precision, "country_code": country})
    return rows


def _set_failure(feed: JobFeed, run: ScanRun, status: str, metadata: dict, error: str) -> None:
    now = utcnow()
    feed.status = status if feed.enabled else "paused"
    feed.last_error = error[:2000]
    retry_at = _retry_after(metadata.get("retry_after"), now)
    if retry_at is None:
        retry_at = (now + timedelta(hours=24) if status in {"blocked", "bot_challenge"}
                    else _backoff(feed.attempt_count))
    feed.next_scan_at = retry_at if feed.enabled else None
    feed.lease_until = None
    run.finished_at = now
    run.status = status
    run.http_status = metadata.get("status")
    run.error = error[:2000]


def _schedule_career_revisit(company: Company, now: datetime) -> None:
    due = next_company_due(company, now)
    previous = company.next_discovery_at
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    # Repeated feed success must not push the independent homepage revisit back.
    if due is None or previous is None or due < previous:
        company.next_discovery_at = due


def _next_scan_after_success(session, feed: JobFeed, due: datetime, now: datetime,
                             completed_scope: dict | None = None) -> datetime | None:
    pending = list(feed.pending_search_scopes) if isinstance(feed.pending_search_scopes, list) else []
    if feed.provider == "arbeitsagentur":
        pending_country = any(isinstance(item, dict) and item.get("search_scope") == "country"
                              for item in pending)
        pending_regional = any(isinstance(item, dict) and item.get("search_scope") != "country"
                               for item in pending)
        has_regional_schedule = session.scalar(select(ConfiguredLocation.id).where(
            ConfiguredLocation.enabled.is_(True),
            ConfiguredLocation.search_scope != "country").limit(1)) is not None
        fallback_without_regional_schedule = (
            isinstance(completed_scope, dict) and is_synthetic_germany_scope(completed_scope) and
            not pending_regional and not has_regional_schedule)
        explicit_country_scope = (
            isinstance(completed_scope, dict) and completed_scope.get("search_scope") == "country" and
            not is_synthetic_germany_scope(completed_scope))
        if (feed.enabled and not pending_country and
                (explicit_country_scope or fallback_without_regional_schedule)):
            repeat_scope = {**completed_scope, "discovery_job_id": None,
                            "scheduled_for": due.isoformat()}
            pending.append(repeat_scope)
            feed.pending_search_scopes = pending
        return next_scope_due(pending, now) if feed.enabled else None
    if feed.enabled and any(isinstance(item, dict) for item in pending):
        return now
    return due if feed.enabled else None


def _requeue_search_scope(feed: JobFeed, run: ScanRun,
                          retry_at: datetime | None = None) -> None:
    if (feed.provider != "arbeitsagentur" or feed.status == "source_replaced" or
            not isinstance(run.search_scope, dict)):
        return
    pending = list(feed.pending_search_scopes) if isinstance(feed.pending_search_scopes, list) else []
    retry_scope = dict(run.search_scope)
    if retry_at is not None:
        retry_scope["scheduled_for"] = retry_at.isoformat()
    job_id = retry_scope.get("discovery_job_id")
    for index, item in enumerate(pending):
        same_scope = (isinstance(item, dict) and (
            item.get("discovery_job_id") == job_id if job_id is not None else item == run.search_scope
        ))
        if not same_scope:
            continue
        if retry_at is not None and scope_due_at(item, utcnow()) <= retry_at:
            pending[index] = {**item, "scheduled_for": retry_at.isoformat()}
            feed.pending_search_scopes = pending
        return
    feed.pending_search_scopes = [retry_scope, *pending]


def _lock_feed_result(session, feed_id: int) -> JobFeed | None:
    # Global order for source finalizers: Company -> JobFeed -> ScanRun.
    # Discovery already locks Company first; Feed -> Company deadlocked with it.
    company_id = session.scalar(select(JobFeed.company_id).where(JobFeed.id == feed_id))
    if company_id is None:
        return None
    if session.get_bind().dialect.name == 'postgresql':
        session.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
    # SQLite also needs a real write before the ownership/revision read.
    session.execute(update(JobFeed).where(JobFeed.id == feed_id).values(
        source_revision=JobFeed.source_revision).execution_options(synchronize_session=False))
    return session.get(JobFeed, feed_id, populate_existing=True)


def _current_feed_run(session, feed_id: int, run_id: int) -> ScanRun | None:
    run = session.scalar(select(ScanRun).where(
        ScanRun.id == run_id, ScanRun.feed_id == feed_id).with_for_update())
    if run is None or run.status != 'running':
        return None
    latest_id = session.scalar(select(ScanRun.id).where(
        ScanRun.feed_id == feed_id).order_by(ScanRun.id.desc()).limit(1))
    return run if latest_id == run_id else None


def _persist_result(feed_id: int, run_id: int, metadata: dict, body: bytes,
                    source_revision: int | None = None) -> None:
    now = utcnow()
    with SessionLocal.begin() as session:
        feed = _lock_feed_result(session, feed_id)
        run = _current_feed_run(session, feed_id, run_id)
        if feed is None or run is None:
            return
        if source_revision is not None and feed.source_revision != source_revision:
            # The lease and search queue now belong to a newer revision. A
            # stale worker may only finish its own run row while a newer scan
            # is active. If not, release this orphaned lease so the current
            # revision can be claimed immediately.
            newer_run = session.scalar(select(ScanRun.id).where(
                ScanRun.feed_id == feed.id, ScanRun.id != run.id,
                ScanRun.status == "running").limit(1))
            if newer_run is None:
                feed.lease_until = None
            run.finished_at = now
            run.status = "stale_source"
            run.http_status = metadata.get("status")
            run.error = "Feed source changed during this scan; the old response was discarded."
            return
        state = metadata.get("state")
        status = metadata.get("status")
        feed.lease_until = None
        if metadata.get("etag"):
            feed.etag = metadata["etag"]
        if metadata.get("last_modified"):
            feed.last_modified = metadata["last_modified"]

        if state == "not_modified" or status == 304:
            feed.last_checked_at = now
            if feed.status != "incomplete":
                feed.last_error = None
            refresh_company_rank(session, feed.company, now,
                                 {'complete': True, 'status': 'complete_empty'}
                                 if feed.status == 'complete_empty' else None)
            due = (_backoff(1, incomplete=True) if feed.status == "incomplete" else
                   next_feed_due(feed.company, feed, now))
            feed.next_scan_at = _next_scan_after_success(session, feed, due, now, run.search_scope)
            _schedule_career_revisit(feed.company, now)
            if not feed.enabled:
                feed.status = "paused"
            run.finished_at = now
            run.status = "not_modified"
            run.http_status = 304
            run.item_count = feed.job_count or 0
            return

        if state != "ok":
            if state == "bot_challenge":
                failure_status = "bot_challenge"
            elif status in {401, 403}:
                failure_status = "blocked"
            elif status == 429:
                failure_status = "throttled"
            elif state in {"unsupported_api", "browser_unavailable"}:
                failure_status = "unsupported"
            else:
                failure_status = "failed"
            detail = metadata.get("error") or state or f"HTTP {status}"
            _requeue_search_scope(feed, run, _backoff(1, incomplete=True))
            _set_failure(feed, run, failure_status, metadata, detail)
            return

        try:
            if metadata.get("schema_error"):
                raise ValueError(metadata["schema_error"])
            parsed = metadata.get("parsed_feed")
            if parsed is None:
                board_url = feed.board_url or feed.feed_url
                parts = urlsplit(board_url)
                scope = re.fullmatch(r'/job/(\d+)/?', parts.path) if feed.provider == 'personio' else None
                if scope:
                    # Several explicitly linked postings can share one XML feed.
                    # Preserve every authorized scope without admitting group jobs.
                    authorized = {scope[1]}
                    for existing in session.scalars(select(Job).where(Job.feed_id == feed.id)).all():
                        marker = (existing.raw_metadata or {}).get('personio_posting_scope')
                        if marker == existing.external_id and re.fullmatch(r'\d+', marker):
                            authorized.add(marker)
                    parsed = parse_feed('personio', body, f'{parts.scheme}://{parts.netloc}')
                    parsed = {'jobs':[job for job in parsed['jobs'] if job['id'] in authorized], 'complete':False}
                    for job in parsed['jobs']:
                        job.setdefault('raw_metadata', {})['personio_posting_scope'] = job['id']
                else:
                    parse_url = (metadata.get("final_url") or feed.feed_url
                                 if feed.provider == "rss" else board_url)
                    parsed = parse_feed(feed.provider, body, parse_url)
        except (ValueError, TypeError) as error:
            _set_failure(feed, run, "schema_error", metadata, str(error))
            return

        incoming = parsed.get("jobs", [])
        if feed.provider == 'telekom':
            from hiring_scraper.discovery import _employer_matches
            seed = {'name':feed.company.name, 'website':feed.company.website_url}
            incoming = [data for data in incoming if _employer_matches(seed, data)]
        complete = bool(parsed.get("complete"))
        windowed = bool(parsed.get("windowed"))
        location_filter = (feed.source_config or {}).get("location_filter")
        if feed.provider != "arbeitsagentur" and isinstance(location_filter, dict):
            incoming = [data for data in incoming if _matches_location_filter(data, location_filter)]
            # A filtered view of a complete feed cannot establish that omitted
            # postings have closed, so retain them as a rolling search window.
            complete = False
            windowed = True
        successful = complete or windowed
        if not successful:
            _requeue_search_scope(feed, run, _backoff(1, incomplete=True))
        current = {job.external_id: job for job in session.scalars(
            select(Job).where(Job.feed_id == feed.id).options(selectinload(Job.locations))).all()}
        seen = set()
        touched_job_ids: set[int] = set()
        for data in incoming:
            if (feed.provider == "rss" and not data.get("locations")
                    and feed.company.location_label):
                source_location = {
                    "label": feed.company.location_label,
                    "latitude": feed.company.latitude,
                    "longitude": feed.company.longitude,
                    "precision": feed.company.location_precision,
                    "country_code": "DE",
                }
                data["location"] = feed.company.location_label
                data["locations"] = [source_location]
                data["raw_metadata"] = {
                    **(data.get("raw_metadata") or {}),
                    "location_source": "configured_feed_source",
                }
            external_id = str(data["id"])
            seen.add(external_id)
            job = current.get(external_id)
            created = job is None
            if created:
                job = Job(feed_id=feed.id, external_id=external_id, title=data["title"], url=data["url"],
                          first_seen_at=now, last_seen_at=now)
                session.add(job)
                session.flush()
                current[external_id] = job
                old_locations = []
            else:
                old_locations = list(job.locations)
            before_signature = None if created else job_search_signature(job)
            data = preserve_verified_detail(job, data)
            job.title = data["title"]
            job.url = data["url"]
            job.description = data.get("description")
            job.location_text = data.get("location") or None
            job.is_remote = bool(data.get("is_remote")) or (job.location_text or "").casefold().strip() == "remote"
            job.work_arrangement = data.get("work_arrangement") or ("remote" if job.is_remote else None)
            job.employment_type = data.get("employment_type")
            job.schedule = data.get("schedule")
            job.department = data.get("department")
            job.seniority = data.get("seniority")
            job.date_posted = data.get("date_posted")
            job.salary = data.get("salary")
            job.raw_metadata = data.get("raw_metadata") or {}
            employer = upsert_job_employer(session, feed, data, now)
            if employer is not None:
                job.employer_company_id = employer.id
            refresh_enrichment(job)
            job.last_seen_at = now
            job.is_active = True
            job.missing_complete_scans = 0
            job.closed_at = None
            replace_job_locations(job, _location_rows(data, old_locations))
            if created or job_search_signature(job) != before_signature:
                touched_job_ids.add(job.id)

        if complete:
            for external_id, job in current.items():
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

        feed.job_count = len(incoming)
        feed.status = (("parsed" if incoming else "complete_empty") if complete else
                       ("windowed" if incoming else "windowed_empty") if windowed else "incomplete") if feed.enabled else "paused"
        feed.last_checked_at = now
        feed.last_error = None if successful else (
            (metadata.get("pagination") or {}).get("error") or
            "Feed returned a partial page. No jobs were closed.")
        feed.attempt_count = 0
        session.flush()
        refresh_company_rank(session, feed.company, now,
                             {'complete': True, 'status': 'complete_empty'} if complete and not incoming else None)
        due = (next_feed_due(feed.company, feed, now) if successful else
               _backoff(1, incomplete=True))
        if successful:
            feed.next_scan_at = _next_scan_after_success(session, feed, due, now, run.search_scope)
        elif feed.provider == "arbeitsagentur":
            # Keep other due locations moving while this failed scope waits
            # for its retry window.
            feed.next_scan_at = next_scope_due(feed.pending_search_scopes, now) if feed.enabled else None
        else:
            feed.next_scan_at = due if feed.enabled else None
        if successful:
            _schedule_career_revisit(feed.company, now)
        run.finished_at = now
        run.status = feed.status
        run.http_status = status
        run.item_count = len(incoming)
        run.error = feed.last_error
        if touched_job_ids:
            session.flush()
            refresh_application_matches_for_jobs(session, touched_job_ids)


def _record_scan_failure(feed_id: int, run_id: int, source_revision: int,
                         error: Exception) -> None:
    with SessionLocal.begin() as failed_session:
        failed_feed = _lock_feed_result(failed_session, feed_id)
        failed_run = _current_feed_run(failed_session, feed_id, run_id)
        if failed_feed and failed_run:
            if failed_feed.source_revision != source_revision:
                newer_run = failed_session.scalar(select(ScanRun.id).where(
                    ScanRun.feed_id == failed_feed.id, ScanRun.id != failed_run.id,
                    ScanRun.status == "running").limit(1))
                if newer_run is None:
                    failed_feed.lease_until = None
                failed_run.finished_at = utcnow()
                failed_run.status = "stale_source"
                failed_run.error = "Feed source changed during this scan; the old response was discarded."
            else:
                retry_at = None
                if isinstance(error, EmployerUpsertDeferred):
                    retry_at = utcnow() + timedelta(seconds=random.randint(15, 45))
                    # This was contention, not a failed remote scan. Do not
                    # increase the exponential retry delay for a healthy feed.
                    failed_feed.attempt_count = max(0, failed_feed.attempt_count - 1)
                _requeue_search_scope(
                    failed_feed, failed_run,
                    retry_at if retry_at is not None else _backoff(1, incomplete=True),
                )
                _set_failure(failed_feed, failed_run, "failed", {}, str(error))
                if retry_at is not None:
                    failed_feed.next_scan_at = retry_at if failed_feed.enabled else None


def scan_once() -> bool:
    claimed = _retry_sqlite_busy(_claim_due_feed, action="claiming a due feed")
    if claimed is None:
        return False
    feed_id, run_id, source_revision, search_scope = claimed
    with SessionLocal() as session:
        feed = session.scalars(select(JobFeed).options(
            selectinload(JobFeed.company),
            selectinload(JobFeed.jobs).selectinload(Job.locations),
        ).where(JobFeed.id == feed_id)).first()
    if feed is None:
        return True
    if feed.source_revision != source_revision:
        _retry_sqlite_busy(
            lambda: _persist_result(feed_id, run_id, {"state": "stale_source"}, b"", source_revision),
            action=f"discarding stale scan result for feed {feed_id}",
        )
        return True
    try:
        metadata, body = _request(feed, search_scope, run_id=run_id)
        _retry_sqlite_busy(
            lambda: _persist_result(feed_id, run_id, metadata, body, source_revision),
            action=f"saving scan result for feed {feed_id}",
        )
        LOG.info("feed scan id=%s provider=%s state=%s jobs=%s", feed_id, feed.provider,
                 metadata.get("state"), metadata.get("status"))
    except Exception as error:
        if is_transient_database_error(error):
            # The rolled-back persistence transaction must not label a healthy
            # source failed. The outer loop backs off; the lease allows recovery.
            raise
        LOG.exception("feed scan failed id=%s", feed_id)
        try:
            _retry_sqlite_busy(
                lambda: _record_scan_failure(feed_id, run_id, source_revision, error),
                action=f"recording scan failure for feed {feed_id}",
            )
        except Exception:
            # The lease expires if SQLite remains unavailable. Keep the worker
            # alive so this transient lock cannot take down the preview stack.
            LOG.exception("Could not record scan failure for feed %s; its lease will expire", feed_id)
    return True


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    initialized = False
    while True:
        try:
            if not initialized:
                added_defaults = ensure_default_job_feeds()
                LOG.info("loaded default public job feeds added=%s", added_defaults)
                LOG.info("feed worker started poll_seconds=%s interval_hours=%s", POLL_SECONDS, SCAN_INTERVAL_HOURS)
                initialized = True
            worked = scan_once()
        except (DBAPIError, DatabasePoolTimeout) as error:
            if not is_transient_database_error(error):
                raise
            # Session contexts rolled back and released row locks; work remains
            # recoverable through its expiring lease if failure recording failed.
            LOG.warning('Feed database cycle failed; backing off before retry', exc_info=True)
            worked = False
        if not worked:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
