"""Queue managed public-source scans alongside scheduled discovery scopes."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from hiring_scraper.app.models import Company, DiscoveryJob, JobFeed


_PROTECTED_FEED_STATES = {"blocked", "bot_challenge", "throttled"}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def search_scope_payload(job: DiscoveryJob) -> dict:
    payload = {
        "discovery_job_id": job.id,
        "configured_location_id": job.configured_location_id,
        "search_scope": job.search_scope,
        "label": job.label,
        "city": job.city,
        "state": job.state,
        "scheduled_for": job.scheduled_for.isoformat() if job.scheduled_for else None,
    }
    if job.search_scope == "radius":
        payload["radius_km"] = job.radius_km
    return payload


def configured_location_scope_payload(location, scheduled_for: datetime | None = None) -> dict:
    """Build the next adapter scope from a saved location schedule."""
    scheduled_for = scheduled_for or location.next_run_at or datetime.now(timezone.utc)
    payload = {
        "discovery_job_id": None,
        "configured_location_id": location.id,
        "search_scope": location.search_scope,
        "label": location.label,
        "city": location.city,
        "state": location.state,
        "scheduled_for": scheduled_for.isoformat(),
    }
    if location.search_scope == "radius":
        payload["radius_km"] = location.radius_km
    return payload


def germany_search_scope_payload(scheduled_for: datetime | None = None) -> dict:
    """Represent an explicit unfiltered German search for the BA adapter."""
    scheduled_for = scheduled_for or datetime.now(timezone.utc)
    return {
        "discovery_job_id": None,
        "configured_location_id": None,
        "search_scope": "country",
        "label": "Germany",
        "city": None,
        "state": None,
        "scheduled_for": scheduled_for.isoformat(),
    }


def is_synthetic_germany_scope(scope: dict) -> bool:
    """Whether a Germany scope is only the adapter's no-schedule fallback."""
    return (scope.get("search_scope") == "country" and
            scope.get("configured_location_id") is None and
            scope.get("discovery_job_id") is None)


def scope_due_at(scope: dict, fallback: datetime | None = None) -> datetime:
    """Parse a queued scope's schedule time, treating absent/invalid values as due now."""
    fallback = fallback or datetime.now(timezone.utc)
    raw = scope.get("scheduled_for")
    if isinstance(raw, datetime):
        return _utc(raw) or fallback
    if isinstance(raw, str):
        try:
            return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00"))) or fallback
        except ValueError:
            pass
    return fallback


def next_scope_due(scopes: list, fallback: datetime | None = None) -> datetime | None:
    fallback = fallback or datetime.now(timezone.utc)
    due_times = [scope_due_at(scope, fallback) for scope in scopes if isinstance(scope, dict)]
    return min(due_times) if due_times else None


def queue_arbeitsagentur_scope(session: Session, job: DiscoveryJob,
                               due_at: datetime, feed_id: int | None = None) -> int:
    """Attach one durable scheduled search context to every managed BA adapter."""
    session.flush()
    scope = search_scope_payload(job)
    statement = select(JobFeed).join(Company).where(
        JobFeed.provider == "arbeitsagentur", JobFeed.enabled.is_(True),
    )
    if feed_id is not None:
        statement = statement.where(JobFeed.id == feed_id)
    feeds = session.scalars(statement.order_by(JobFeed.id)).all()
    queued = 0
    now = datetime.now(timezone.utc)
    for feed in feeds:
        config = feed.source_config if isinstance(feed.source_config, dict) else {}
        if not (config.get("managed_adapter") is True or config.get("location_filter") or
                feed.company.source == "managed_feed"):
            continue
        pending = list(feed.pending_search_scopes) if isinstance(feed.pending_search_scopes, list) else []
        if (scope.get("search_scope") != "country" or
                scope.get("configured_location_id") is not None or
                scope.get("discovery_job_id") is not None):
            # Once a real saved or active search exists, discard the synthetic
            # national fallback so it cannot keep running alongside it.
            pending = [item for item in pending if not (
                isinstance(item, dict) and is_synthetic_germany_scope(item))]
        location_id = job.configured_location_id
        existing_index = next((index for index, item in enumerate(pending)
                               if isinstance(item, dict) and (
                                   item.get("configured_location_id") == location_id
                                   if location_id is not None else
                                   item.get("discovery_job_id") == job.id)), None)
        if existing_index is not None:
            pending[existing_index] = scope
            feed.pending_search_scopes = pending
        else:
            feed.pending_search_scopes = [*pending, scope]
        queued += 1

        lease_until = _utc(feed.lease_until)
        if (feed.status not in _PROTECTED_FEED_STATES and not feed.attempt_count and
                (lease_until is None or lease_until <= now)):
            next_scan_at = _utc(feed.next_scan_at)
            if next_scan_at is None or _utc(due_at) < next_scan_at:
                feed.next_scan_at = due_at
    return queued
