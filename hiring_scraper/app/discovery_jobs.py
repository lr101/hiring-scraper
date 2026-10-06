"""Location-scoped crawl campaigns and their API representations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from hiring_scraper.app.database import IS_SQLITE
from hiring_scraper.app.models import (
    Company, ConfiguredLocation, DiscoveryJob, DiscoveryJobCompany, utcnow,
)
from hiring_scraper.geography import haversine_m


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def company_ids_in_radius(session: Session, latitude: float, longitude: float,
                          radius_km: float) -> list[int]:
    radius_m = radius_km * 1000
    if not IS_SQLITE:
        rows = session.execute(text(
            "SELECT id FROM companies WHERE website_url IS NOT NULL AND website_url <> '' "
            "AND ST_DWithin(location_geog, "
            "ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, :radius_m, false) "
            "ORDER BY id"
        ).params(latitude=latitude, longitude=longitude, radius_m=radius_m)).all()
        return [int(row[0]) for row in rows]

    companies = session.scalars(select(Company).where(
        Company.website_url.is_not(None), Company.website_url != "",
        Company.latitude.is_not(None), Company.longitude.is_not(None),
    )).all()
    return [company.id for company in companies
            if haversine_m(latitude, longitude, company.latitude, company.longitude) <= radius_m]


def create_discovery_job(session: Session, *, label: str, latitude: float, longitude: float,
                         radius_km: float, city: str | None = None,
                         state: str | None = None, postcode: str | None = None,
                         configured_location_id: int | None = None, kind: str = "manual",
                         scheduled_for: datetime | None = None,
                         now: datetime | None = None,
                         stage: str = "company_homepage_discovery") -> DiscoveryJob:
    now = now or utcnow()
    if scheduled_for is not None and scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    status = "scheduled" if scheduled_for is not None and scheduled_for > now else "queued"
    job = DiscoveryJob(
        configured_location_id=configured_location_id, kind=kind, label=label.strip(),
        city=city, state=state, postcode=postcode, latitude=latitude, longitude=longitude,
        radius_km=radius_km, status=status, stage=stage, scheduled_for=scheduled_for,
        created_at=now, candidate_total=0,
    )
    session.add(job)
    return job


def configured_location_json(location: ConfiguredLocation) -> dict[str, Any]:
    return {
        "id": location.id, "label": location.label, "city": location.city,
        "state": location.state, "postcode": location.postcode,
        "latitude": location.latitude, "longitude": location.longitude,
        "radius_km": location.radius_km, "interval_days": location.interval_days,
        "enabled": location.enabled, "next_run_at": _iso(location.next_run_at),
        "created_at": _iso(location.created_at), "updated_at": _iso(location.updated_at),
    }


def discovery_job_json(job: DiscoveryJob) -> dict[str, Any]:
    terminal = job.status in {"completed", "partial", "failed"}
    if job.candidate_total:
        progress = min(100, round(100 * job.processed_count / job.candidate_total))
    else:
        progress = 100 if job.status in {"completed", "partial"} else 0
    return {
        "id": job.id, "configured_location_id": job.configured_location_id,
        "kind": job.kind, "stage": job.stage, "label": job.label, "city": job.city, "state": job.state,
        "postcode": job.postcode, "latitude": job.latitude, "longitude": job.longitude,
        "radius_km": job.radius_km, "status": job.status,
        "scheduled_for": _iso(job.scheduled_for), "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at), "finished_at": _iso(job.finished_at),
        "candidate_total": job.candidate_total, "processed_count": job.processed_count,
        "succeeded_count": job.succeeded_count, "failed_count": job.failed_count,
        "companies_found": job.companies_found, "homepages_found": job.homepages_found,
        "jobs_found": job.jobs_found, "progress_percent": progress,
        "progress_message": job.progress_message,
        "progress_updated_at": _iso(job.progress_updated_at), "error": job.error,
    }


def advance_location_schedule(location: ConfiguredLocation, now: datetime) -> None:
    location.next_run_at = now + timedelta(days=location.interval_days)
    location.updated_at = now
