"""Scheduled, single-feed-at-a-time refresh worker."""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from sqlalchemy import select, or_

from hiring_scraper.app.database import SessionLocal, engine
from hiring_scraper.app.models import Job, JobFeed, JobLocation, ScanRun, utcnow
from hiring_scraper.ats import parse_feed
from hiring_scraper.http import Client


LOG = logging.getLogger("hiring_scraper.worker")
POLL_SECONDS = max(3, int(os.getenv("SCAN_POLL_SECONDS", "30")))
SCAN_INTERVAL_HOURS = max(1, int(os.getenv("FEED_SCAN_INTERVAL_HOURS", "6")))
REQUEST_DELAY_SECONDS = max(1.0, float(os.getenv("CRAWL_DELAY_SECONDS", "1")))
LEASE_MINUTES = max(2, int(os.getenv("FEED_SCAN_LEASE_MINUTES", "5")))
CAPTURE_DIR = Path(os.getenv("SCAN_CAPTURE_DIR", "/tmp/hiring-scraper-feed-captures"))
USER_AGENT = os.getenv("HIRING_USER_AGENT", "HiringScraper/0.2 (public job-feed refresh)")
_ORIGIN_LOCK = Lock()
_LAST_ORIGIN_REQUEST: dict[str, float] = {}


def _claim_due_feed() -> tuple[int, int] | None:
    now = utcnow()
    with SessionLocal.begin() as session:
        statement = (select(JobFeed)
                     .where(JobFeed.next_scan_at.is_not(None), JobFeed.next_scan_at <= now,
                            or_(JobFeed.lease_until.is_(None), JobFeed.lease_until < now))
                     .order_by(JobFeed.next_scan_at, JobFeed.id).limit(1))
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        feed = session.scalars(statement).first()
        if feed is None:
            return None
        feed.lease_until = now + timedelta(minutes=LEASE_MINUTES)
        feed.last_attempt_at = now
        feed.attempt_count += 1
        scan = ScanRun(feed_id=feed.id, started_at=now, status="running")
        session.add(scan)
        session.flush()
        return feed.id, scan.id


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


def _request(feed: JobFeed) -> tuple[dict, bytes]:
    _pace_origin(feed.feed_url)
    client = Client(CAPTURE_DIR, timeout=20, delay=0, max_requests=8, user_agent=USER_AGENT)
    if feed.provider in {"schema_org", "html_jobs"}:
        # Employer-hosted JSON and HTML pages use the robots-aware website path.
        return client.get(feed.feed_url)
    headers = {}
    if feed.etag:
        headers["If-None-Match"] = feed.etag
    if feed.last_modified:
        headers["If-Modified-Since"] = feed.last_modified
    return client.get_feed(feed.feed_url, headers)


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
    feed.status = status
    feed.last_error = error[:2000]
    feed.next_scan_at = _retry_after(metadata.get("retry_after"), now) or _backoff(feed.attempt_count)
    feed.lease_until = None
    run.finished_at = now
    run.status = status
    run.http_status = metadata.get("status")
    run.error = error[:2000]


def _persist_result(feed_id: int, run_id: int, metadata: dict, body: bytes) -> None:
    now = utcnow()
    with SessionLocal.begin() as session:
        feed = session.get(JobFeed, feed_id)
        run = session.get(ScanRun, run_id)
        if feed is None or run is None:
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
            feed.last_error = None
            feed.next_scan_at = now + timedelta(hours=SCAN_INTERVAL_HOURS, minutes=random.randint(0, 30))
            run.finished_at = now
            run.status = "not_modified"
            run.http_status = 304
            run.item_count = feed.job_count or 0
            return

        if state != "ok":
            if state in {"robots_disallowed", "robots_unavailable"} or status in {401, 403}:
                failure_status = "blocked"
            elif status == 429:
                failure_status = "throttled"
            elif state == "unsupported_api":
                failure_status = "unsupported"
            else:
                failure_status = "failed"
            detail = metadata.get("error") or state or f"HTTP {status}"
            _set_failure(feed, run, failure_status, metadata, detail)
            return

        try:
            parsed = parse_feed(feed.provider, body, feed.board_url or feed.feed_url)
        except (ValueError, TypeError) as error:
            _set_failure(feed, run, "schema_error", metadata, str(error))
            return

        incoming = parsed.get("jobs", [])
        complete = bool(parsed.get("complete"))
        current = {job.external_id: job for job in session.scalars(
            select(Job).where(Job.feed_id == feed.id).options(selectinload(Job.locations))).all()}
        seen = set()
        for data in incoming:
            external_id = str(data["id"])
            seen.add(external_id)
            job = current.get(external_id)
            if job is None:
                job = Job(feed_id=feed.id, external_id=external_id, title=data["title"], url=data["url"],
                          first_seen_at=now, last_seen_at=now)
                session.add(job)
                session.flush()
                current[external_id] = job
                old_locations = []
            else:
                old_locations = list(job.locations)
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
            job.last_seen_at = now
            job.is_active = True
            job.missing_complete_scans = 0
            job.closed_at = None
            for old in old_locations:
                session.delete(old)
            for location in _location_rows(data, old_locations):
                session.add(JobLocation(job_id=job.id, label=location["label"],
                                        latitude=location.get("latitude"), longitude=location.get("longitude"),
                                        precision=location.get("precision"), country_code=location.get("country_code")))

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

        feed.job_count = len(incoming)
        feed.status = "parsed" if complete and incoming else "complete_empty" if complete else "incomplete"
        feed.last_checked_at = now
        feed.last_error = None if complete else "Feed returned a partial page. No jobs were closed."
        feed.attempt_count = 0
        feed.next_scan_at = (now + timedelta(hours=SCAN_INTERVAL_HOURS, minutes=random.randint(0, 30))
                             if complete else _backoff(1, incomplete=True))
        run.finished_at = now
        run.status = feed.status
        run.http_status = status
        run.item_count = len(incoming)
        run.error = feed.last_error


def scan_once() -> bool:
    claimed = _claim_due_feed()
    if claimed is None:
        return False
    feed_id, run_id = claimed
    with SessionLocal() as session:
        feed = session.get(JobFeed, feed_id)
        if feed is None:
            return True
        try:
            metadata, body = _request(feed)
            _persist_result(feed_id, run_id, metadata, body)
            LOG.info("feed scan id=%s provider=%s state=%s jobs=%s", feed_id, feed.provider,
                     metadata.get("state"), metadata.get("status"))
        except Exception as error:
            LOG.exception("feed scan failed id=%s", feed_id)
            with SessionLocal.begin() as failed_session:
                failed_feed = failed_session.get(JobFeed, feed_id)
                failed_run = failed_session.get(ScanRun, run_id)
                if failed_feed and failed_run:
                    _set_failure(failed_feed, failed_run, "failed", {}, str(error))
    return True


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOG.info("feed worker started poll_seconds=%s interval_hours=%s", POLL_SECONDS, SCAN_INTERVAL_HOURS)
    while True:
        if not scan_once():
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
