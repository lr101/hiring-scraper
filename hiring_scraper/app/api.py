"""HTTP API for nearby companies, job discovery, and profile matching."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.orm import Session, joinedload, selectinload

from hiring_scraper.app.database import IS_SQLITE, engine, get_session, initialize_sqlite_schema
from hiring_scraper.app.models import (
    Base, Company, ConfiguredLocation, DiscoveryJob, DiscoveryJobCompany, Job, JobFeed, JobLocation,
    LocationCache, utcnow,
)
from hiring_scraper.app.discovery_jobs import (
    configured_location_json, create_discovery_job as make_discovery_job, discovery_job_json,
)
from hiring_scraper.geography import haversine_m, normalize_german_state
from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.profiles import router as profiles_router, require_profile
from hiring_scraper.matching import match_job
from hiring_scraper.app.board_scope import (
    deduplicate_jobs, expiry, geographic_scope, remote_countries, vacancy_keys,
)


KARLSRUHE = {"label": "Karlsruhe, Baden-Württemberg, Deutschland", "city": "Karlsruhe",
             "state": "Baden-Württemberg", "latitude": 49.0068705, "longitude": 8.4034195,
             "precision": "city_centroid", "country_code": "de", "type": "city"}
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_REQUEST = 0.0


class LocationQuery(BaseModel):
    query: str = Field(min_length=2, max_length=200)


class ConfiguredLocationCreate(BaseModel):
    label: str = Field(min_length=2, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postcode: str | None = Field(default=None, max_length=20)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=15, gt=0, le=200)
    interval_days: int = Field(default=7, ge=1, le=365)


class ConfiguredLocationUpdate(BaseModel):
    radius_km: float | None = Field(default=None, gt=0, le=200)
    interval_days: int | None = Field(default=None, ge=1, le=365)
    enabled: bool | None = None


class DiscoveryJobCreate(BaseModel):
    location_id: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, min_length=2, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postcode: str | None = Field(default=None, max_length=20)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=200)
    scheduled_for: datetime | None = None


def _model_value(request: Any, key: str, default: Any = None) -> Any:
    if isinstance(request, dict):
        return request.get(key, default)
    return getattr(request, key, default)


def _key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _nominatim_results(query: str) -> list[dict[str, Any]]:
    """Resolve one submitted location query; this is deliberately not autocomplete."""
    global _LAST_NOMINATIM_REQUEST
    params = urlencode({"q": query, "format": "jsonv2", "addressdetails": 1,
                        "limit": 5, "countrycodes": "de", "accept-language": "de"})
    request = Request("https://nominatim.openstreetmap.org/search?" + params,
                      headers={"User-Agent": os.getenv("NOMINATIM_USER_AGENT", "HiringScraper/0.2 (location search)"),
                               "Accept": "application/json"})
    with _NOMINATIM_LOCK:
        wait = 1.05 - (time.monotonic() - _LAST_NOMINATIM_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_NOMINATIM_REQUEST = time.monotonic()
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read(1_000_000))
        except HTTPError as error:
            raise HTTPException(status_code=502, detail=f"Location service returned HTTP {error.code}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=502, detail="Could not resolve this location right now") from error
    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Location service returned an invalid response")

    results = []
    for item in payload:
        address = item.get("address", {}) if isinstance(item, dict) else {}
        if address.get("country_code") != "de":
            continue
        city = next((address.get(k) for k in ("city", "town", "village", "municipality", "hamlet") if address.get(k)), None)
        state = address.get("state")
        if state:
            try:
                state = normalize_german_state(state)
            except ValueError:
                pass
        try:
            latitude, longitude = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        results.append({"label": item.get("display_name", query), "city": city,
                        "postcode": address.get("postcode"), "state": state,
                        "latitude": latitude, "longitude": longitude,
                        "precision": "city_centroid" if item.get("addresstype") in {"city", "town", "village", "municipality"} else "place_point",
                        "country_code": "de", "type": item.get("type") or item.get("addresstype") or "place"})
    return results


def _distance(company: Company, latitude: float, longitude: float) -> float:
    if company.latitude is None or company.longitude is None:
        return float("inf")
    return haversine_m(latitude, longitude, company.latitude, company.longitude)


def _company_json(company: Company, distance_m: float, active_job_count: int | None = None) -> dict[str, Any]:
    return {"id": company.id, "name": company.name, "domain": company.domain,
            "website_url": company.website_url, "category": company.category,
            "domain_match_method": company.domain_match_method,
            "domain_evidence_url": company.domain_evidence_url,
            "source": company.source, "source_url": company.source_url,
            "latitude": company.latitude, "longitude": company.longitude,
            "location_precision": company.location_precision, "location_label": company.location_label,
            "career_url": company.career_url, "career_status": company.career_status,
            "distance_m": round(distance_m, 1) if distance_m != float("inf") else None,
            "feed_count": len(company.feeds),
            "active_job_count": active_job_count if active_job_count is not None else
            sum(feed.job_count or 0 for feed in company.feeds)}


def _job_deduplication_key(job: Job) -> tuple[Any, ...]:
    return vacancy_keys(job)[-1]


def _deduplicate_jobs(jobs: list[Job]) -> list[Job]:
    return deduplicate_jobs(jobs)


def _sort_jobs(jobs: list[Job], sort: str) -> list[Job]:
    if sort == "newest":
        dated = [job for job in jobs if job.date_posted]
        undated = [job for job in jobs if not job.date_posted]
        dated.sort(key=lambda job: (job.date_posted or "", job.id), reverse=True)
        undated.sort(key=lambda job: (job.title.casefold(), job.id))
        return dated + undated
    if sort == "title":
        return sorted(jobs, key=lambda job: (job.title.casefold(), job.feed.company.name.casefold(), job.id))
    if sort == "company":
        return sorted(jobs, key=lambda job: (job.feed.company.name.casefold(), job.title.casefold(), job.id))
    return sorted(jobs, key=lambda job: (not job.is_remote, job.title.casefold(), job.id))


def _unique_active_job_counts(session: Session, company_ids: list[int]) -> dict[int, int]:
    if not company_ids:
        return {}
    jobs = session.scalars(select(Job).join(Job.feed).where(
        Job.is_active.is_(True), JobFeed.company_id.in_(company_ids)).options(
        joinedload(Job.feed), selectinload(Job.locations))).unique().all()
    return {company_id: len(_deduplicate_jobs([job for job in jobs
            if job.feed.company_id == company_id])) for company_id in company_ids}


def _feed_json(feed: JobFeed) -> dict[str, Any]:
    return {"id": feed.id, "provider": feed.provider, "tenant": feed.tenant,
            "board_url": feed.board_url, "feed_url": feed.feed_url,
            "status": feed.status, "job_count": feed.job_count,
            "last_checked_at": feed.last_checked_at.isoformat() if feed.last_checked_at else None}


def _job_json(job: Job, *, include_description: bool = False) -> dict[str, Any]:
    feed, company = job.feed, job.feed.company
    locations = [{"label": location.label, "latitude": location.latitude,
                  "longitude": location.longitude, "precision": location.precision,
                  "country_code": location.country_code} for location in job.locations]
    data = {"id": job.id, "external_id": job.external_id, "title": job.title,
            "url": job.url, "company_id": company.id, "company_name": company.name,
            "company_domain": company.domain, "company_website": company.website_url,
            "company_source_url": company.source_url, "provider": feed.provider,
            "board_url": feed.board_url, "feed_status": feed.status,
            "locations": locations, "location_text": job.location_text,
            "is_remote": job.is_remote, "work_arrangement": job.work_arrangement,
            "employment_type": job.employment_type,
            "schedule": job.schedule, "department": job.department,
            "seniority": job.seniority, "date_posted": job.date_posted,
            "salary": job.salary, "first_seen_at": job.first_seen_at.isoformat(),
            "last_seen_at": job.last_seen_at.isoformat(), "is_active": job.is_active,
            "raw_metadata": job.raw_metadata}
    data.update(expiry(job))
    data['remote_country_codes'] = sorted(remote_countries(job))
    data["enrichment"] = current_enrichment(job)
    if include_description:
        data["description"] = job.description
    else:
        data["description_preview"] = (job.description or "")[:280]
    return data


app = FastAPI(title="Hiring Discovery API", version="0.3.0",
              description="Company discovery, jobs and profile matching for German locations")
default_origins = "http://localhost:5173,http://127.0.0.1:5173"
cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", default_origins).split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["*"])


@app.on_event("startup")
def initialize_sqlite() -> None:
    if IS_SQLITE:
        initialize_sqlite_schema(engine)


app.include_router(profiles_router)


@app.get("/health/ready")
def readiness(session: Session = Depends(get_session)) -> dict[str, str]:
    try:
        session.execute(select(1))
    except Exception as error:
        raise HTTPException(status_code=503, detail="Database is not ready") from error
    return {"status": "ready"}


@app.post("/api/v1/locations/resolve")
def resolve_location(request: LocationQuery, session: Session = Depends(get_session)) -> dict[str, Any]:
    normalized = _key(request.query)
    if normalized in {"karlsruhe", "karlsruhe baden württemberg", "karlsruhe baden-württemberg"}:
        return {"query": request.query, "results": [KARLSRUHE], "cached": True}
    cached = session.scalar(select(LocationCache).where(LocationCache.query_key == normalized))
    if cached:
        return {"query": request.query, "results": cached.results, "cached": True}
    results = _nominatim_results(request.query)
    entry = LocationCache(query_key=normalized, results=results)
    session.add(entry)
    session.commit()
    return {"query": request.query, "results": results, "cached": False}


@app.get("/api/v1/locations")
def list_configured_locations(session: Session = Depends(get_session)) -> dict[str, Any]:
    locations = session.scalars(select(ConfiguredLocation).order_by(
        ConfiguredLocation.enabled.desc(), ConfiguredLocation.next_run_at.asc(), ConfiguredLocation.id.asc()
    )).all()
    return {"items": [configured_location_json(location) for location in locations]}


@app.post("/api/v1/locations", status_code=201)
def create_configured_location(request: ConfiguredLocationCreate,
                               session: Session = Depends(get_session)) -> dict[str, Any]:
    label = str(_model_value(request, "label", "")).strip()
    if len(label) < 2:
        raise HTTPException(status_code=422, detail="Location label must contain at least two characters")
    now = utcnow()
    interval_days = int(_model_value(request, "interval_days", 7))
    location = ConfiguredLocation(
        label=label,
        city=_model_value(request, "city"), state=_model_value(request, "state"),
        postcode=_model_value(request, "postcode"),
        latitude=float(_model_value(request, "latitude")),
        longitude=float(_model_value(request, "longitude")),
        radius_km=float(_model_value(request, "radius_km", 15)),
        interval_days=interval_days, enabled=True,
        next_run_at=now + timedelta(days=interval_days), created_at=now, updated_at=now,
    )
    session.add(location)
    session.flush()
    first_job = make_discovery_job(
        session, label=location.label, city=location.city, state=location.state,
        postcode=location.postcode, latitude=location.latitude,
        longitude=location.longitude, radius_km=location.radius_km,
        configured_location_id=location.id, kind="initial", now=now,
    )
    session.commit()
    session.refresh(location)
    session.refresh(first_job)
    return {**configured_location_json(location), "first_discovery_job": discovery_job_json(first_job)}


@app.patch("/api/v1/locations/{location_id}")
def update_configured_location(location_id: int, request: ConfiguredLocationUpdate,
                               session: Session = Depends(get_session)) -> dict[str, Any]:
    location = session.get(ConfiguredLocation, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Configured location not found")
    now = utcnow()
    radius_km = _model_value(request, "radius_km")
    interval_days = _model_value(request, "interval_days")
    enabled = _model_value(request, "enabled")
    if radius_km is not None:
        location.radius_km = float(radius_km)
    if interval_days is not None:
        location.interval_days = int(interval_days)
        location.next_run_at = now + timedelta(days=location.interval_days)
    if enabled is not None:
        location.enabled = bool(enabled)
        if location.enabled and (location.next_run_at is None or location.next_run_at <= now):
            location.next_run_at = now + timedelta(days=location.interval_days)
    location.updated_at = now
    session.commit()
    session.refresh(location)
    return configured_location_json(location)


@app.delete("/api/v1/locations/{location_id}")
def delete_configured_location(location_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    location = session.get(ConfiguredLocation, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Configured location not found")
    session.delete(location)
    session.commit()
    return {"deleted": True}


@app.post("/api/v1/discovery-jobs", status_code=202)
def create_discovery_job(request: DiscoveryJobCreate,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
    location_id = _model_value(request, "location_id")
    location = session.get(ConfiguredLocation, int(location_id)) if location_id is not None else None
    if location_id is not None and location is None:
        raise HTTPException(status_code=404, detail="Configured location not found")
    if location is not None:
        label, city, state, postcode = location.label, location.city, location.state, location.postcode
        latitude, longitude = location.latitude, location.longitude
        radius_km, configured_location_id = location.radius_km, location.id
    else:
        label = str(_model_value(request, "label") or "").strip()
        latitude = _model_value(request, "latitude")
        longitude = _model_value(request, "longitude")
        radius_km = _model_value(request, "radius_km")
        if len(label) < 2 or latitude is None or longitude is None or radius_km is None:
            raise HTTPException(status_code=422, detail="Provide a configured location or a label, coordinates, and radius")
        city, state, postcode = (_model_value(request, key) for key in ("city", "state", "postcode"))
        configured_location_id = None
    scheduled_for = _model_value(request, "scheduled_for")
    if isinstance(scheduled_for, str):
        try:
            scheduled_for = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="scheduled_for must be an ISO datetime") from error
    job = make_discovery_job(
        session, label=label, city=city, state=state, postcode=postcode,
        latitude=float(latitude), longitude=float(longitude), radius_km=float(radius_km),
        configured_location_id=configured_location_id, scheduled_for=scheduled_for,
    )
    session.commit()
    session.refresh(job)
    return discovery_job_json(job)


@app.get("/api/v1/discovery-jobs")
def list_discovery_jobs(status: str | None = Query(None),
                        limit: int = Query(50, ge=1, le=100),
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    valid_statuses = {"scheduled", "queued", "running", "completed", "partial", "failed", "cancelled"}
    if status is not None and status not in valid_statuses:
        raise HTTPException(status_code=422, detail="Unknown discovery job status")
    statement = select(DiscoveryJob)
    if status is not None:
        statement = statement.where(DiscoveryJob.status == status)
    jobs = session.scalars(statement.order_by(DiscoveryJob.created_at.desc(), DiscoveryJob.id.desc())
                           .limit(limit)).all()
    return {"items": [discovery_job_json(job) for job in jobs]}


@app.get("/api/v1/discovery-jobs/{discovery_job_id}")
def get_discovery_job(discovery_job_id: int,
                      session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(DiscoveryJob, discovery_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Discovery job not found")
    return discovery_job_json(job)


@app.post("/api/v1/discovery-jobs/{discovery_job_id}/cancel")
def cancel_discovery_job(discovery_job_id: int,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.scalar(select(DiscoveryJob).where(
        DiscoveryJob.id == discovery_job_id).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Search not found")
    already_cancelled = job.status == "cancelled"
    if not already_cancelled and job.status not in {"scheduled", "queued", "running"}:
        raise HTTPException(status_code=409, detail="This search has already finished")

    now = utcnow()
    if not already_cancelled:
        job.status = "cancelled"
        job.finished_at = now
        job.location_scan_lease_until = None
        job.location_scan_token = None
        job.error = None
        job.progress_message = "Search cancelled"
        job.progress_updated_at = now
    session.commit()
    # Release the parent lock before touching task rows. Workers claim a task row
    # before locking its parent job, so this order avoids a lock cycle.
    session.execute(update(DiscoveryJobCompany).where(
        DiscoveryJobCompany.discovery_job_id == job.id,
        DiscoveryJobCompany.status == "queued",
    ).values(status="cancelled", finished_at=now), execution_options={"synchronize_session": False})
    session.commit()
    session.refresh(job)
    return discovery_job_json(job)


@app.get("/api/v1/companies")
def list_companies(latitude: float = Query(49.0068705, ge=-90, le=90),
                   longitude: float = Query(8.4034195, ge=-180, le=180),
                   radius_km: float = Query(15, gt=0, le=200),
                   query: str | None = Query(None, max_length=160),
                   offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                   session: Session = Depends(get_session),
                   discovery: Literal['all','domain','career','feed','jobs'] = 'all',
                   sort: Literal['distance','name'] = 'distance') -> dict[str, Any]:
    base = select(Company).options(selectinload(Company.feeds))
    filters = []
    if query:
        filters.append(Company.name.ilike(f"%{query.strip()}%"))
    if discovery == 'domain':
        filters.append(Company.domain.is_not(None))
    elif discovery == 'career':
        filters.append(or_(Company.career_url.is_not(None),
                           Company.career_status.in_(['career_page_found','jobs_feed_found','jobs_extracted','provider_detected'])))
    elif discovery == 'feed':
        filters.append(Company.feeds.any(JobFeed.provider != 'html_jobs'))
    elif discovery == 'jobs':
        filters.append(Company.feeds.any(JobFeed.jobs.any(Job.is_active.is_(True))))
    radius_m = radius_km * 1000
    if not IS_SQLITE:
        spatial_clause = text("ST_DWithin(companies.location_geog, ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, :radius_m, false)")
        filters.append(spatial_clause)
        base = base.where(*filters)
        count = session.scalar(select(func.count()).select_from(Company).where(*filters).params(
            longitude=longitude, latitude=latitude, radius_m=radius_m)) or 0
        distance = text("ST_Distance(companies.location_geog, ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, false) AS distance_m")
        ordered_query = base.add_columns(distance)
        if sort == 'name':
            ordered_query = ordered_query.order_by(Company.name.asc(), Company.id.asc())
        else:
            ordered_query = ordered_query.order_by(text("distance_m ASC"), Company.name.asc(), Company.id.asc())
        rows = session.execute(ordered_query.offset(offset).limit(limit).params(
            longitude=longitude, latitude=latitude, radius_m=radius_m)).all()
        counts = _unique_active_job_counts(session, [company.id for company, _ in rows])
        items = [_company_json(company, float(meters), counts.get(company.id, 0)) for company, meters in rows]
        return {"items": items, "total": count, "offset": offset, "limit": limit,
                "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km}}

    candidates = list(session.scalars(base.where(*filters)))
    candidates = [(company, _distance(company, latitude, longitude)) for company in candidates]
    candidates = [(company, distance) for company, distance in candidates if distance <= radius_m]
    if sort == 'name':
        candidates.sort(key=lambda pair: (pair[0].name.casefold(), pair[1], pair[0].id))
    else:
        candidates.sort(key=lambda pair: (pair[1], pair[0].name.casefold(), pair[0].id))
    total = len(candidates)
    page = candidates[offset:offset + limit]
    counts = _unique_active_job_counts(session, [company.id for company, _ in page])
    items = [_company_json(company, distance, counts.get(company.id, 0)) for company, distance in page]
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km}}


def _profile_match(job, profile, scope):
    match = match_job(job_input(job), profile.preferences, current_enrichment(job))
    if scope is not None:
        match['unknowns'] = list(dict.fromkeys(match['unknowns'] + scope['unknowns']))
        if scope['unknowns']:
            match['uncertain'] = True
            if match.get('fit_tier') == 'recommended':
                match['fit_tier'] = 'possible'
        if not scope['eligible']:
            match['eligible'] = False
            match['fit_tier'] = 'unlikely'
            match['conflicts'] = match['conflicts'] + ['Search scope: ' + scope['reason'].replace('_', ' ')]
    return match


def _jobs_page(jobs, sort, offset, limit, latitude, longitude, radius_km,
               profile, min_match_score, include_unknown, place=None, country=None):
    filtered = {'expired': 0, 'remote_country': 0, 'outside_area': 0,
                'profile_conflict': 0, 'below_score': 0, 'uncertain': 0}
    scoped, scopes = [], {}
    for job in jobs:
        scope = geographic_scope(job, latitude, longitude, radius_km, place, country)
        if scope['eligible']:
            scoped.append(job)
            scopes[job.id] = scope
        else:
            filtered[scope['reason']] += 1
    unique = _deduplicate_jobs(scoped)
    matching = _sort_jobs(unique, sort)
    matches = {}
    if profile is not None:
        selected = []
        for job in matching:
            match = _profile_match(job, profile, scopes[job.id])
            matches[job.id] = match
            if not match['eligible']:
                filtered['profile_conflict'] += 1
            elif match['score'] < min_match_score:
                filtered['below_score'] += 1
            elif not include_unknown and match['uncertain']:
                filtered['uncertain'] += 1
            else:
                selected.append(job)
        matching = selected
        if sort == 'relevance':
            matching.sort(key=lambda job: (-matches[job.id]['score'], matches[job.id]['uncertain'],
                                          job.title.casefold(), job.id))
    items = [_job_json(job) for job in matching[offset:offset + limit]]
    for item in items:
        item['match_kind'] = scopes[item['id']]['match_kind']
        item['geography'] = scopes[item['id']]
        if profile is not None:
            item['profile_match'] = matches[item['id']]
    tiers = [matches[job.id].get('fit_tier', 'possible') for job in matching] if profile else []
    return {'items': items, 'total': len(matching), 'offset': offset, 'limit': limit,
            'location': {'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
                         'city': place, 'country_code': country},
            'profile_id': profile.id if profile is not None else None,
            'counts': {'source': len(jobs), 'scoped': len(unique), 'filtered': filtered,
                       'duplicates_removed': len(scoped) - len(unique),
                       'recommended': tiers.count('recommended'), 'possible': tiers.count('possible')},
            'coverage': {'source_count': len({job.feed_id for job in jobs}),
                         'note': 'Observed active source records; selected sources are not exhaustive market coverage.'},
            'rule': 'work location within radius OR explicitly remote within country scope'}


@app.get("/api/v1/jobs")
def list_jobs(latitude: float | None = Query(None, ge=-90, le=90),
              longitude: float | None = Query(None, ge=-180, le=180),
              radius_km: float | None = Query(None, gt=0, le=200),
              company_id: int | None = Query(None, ge=1),
              query: str | None = Query(None, max_length=160),
              place: str | None = Query(None, max_length=120),
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
              work_style: Literal['all','remote','hybrid','onsite'] = 'all',
              sort: Literal['relevance','newest','title','company'] = 'relevance',
              session: Session = Depends(get_session),
              profile_id: Annotated[int | None, Query(ge=1)] = None,
              min_match_score: Annotated[int | None, Query(ge=0, le=100)] = None,
              include_unknown: bool | None = None) -> dict[str, Any]:
    profile = require_profile(profile_id, session) if profile_id is not None else None
    area = (profile.preferences.get('search_area') or {}) if profile else {}
    defaults = (profile.preferences.get('matching_defaults') or {}) if profile else {}
    # Direct internal calls retain support for the historic Query defaults.
    latitude = latitude if isinstance(latitude, (int, float)) else area.get('latitude')
    longitude = longitude if isinstance(longitude, (int, float)) else area.get('longitude')
    radius_km = radius_km if isinstance(radius_km, (int, float)) else area.get('radius_km')
    latitude = latitude if latitude is not None else 49.0068705
    longitude = longitude if longitude is not None else 8.4034195
    radius_km = radius_km if radius_km is not None else 15
    place = place if isinstance(place, str) else area.get('city')
    country = area.get('country_code') or 'DE'
    min_match_score = defaults.get('min_match_score', 0) if min_match_score is None else min_match_score
    include_unknown = defaults.get('include_unknown', True) if include_unknown is None else include_unknown
    if profile is None and min_match_score:
        raise HTTPException(status_code=422, detail="Select a profile to filter by match score")
    statement = select(Job).join(Job.feed).join(JobFeed.company).options(
        joinedload(Job.feed).joinedload(JobFeed.company), selectinload(Job.locations))
    filters = [Job.is_active.is_(True)]
    if company_id is not None:
        filters.append(JobFeed.company_id == company_id)
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(or_(Job.title.ilike(pattern), Company.name.ilike(pattern), Company.domain.ilike(pattern)))
    if work_style == 'remote':
        remote_clause = or_(Job.work_arrangement == 'remote',
                            and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None)))
        filters.append(remote_clause)
    elif work_style in {'hybrid', 'onsite'}:
        filters.append(Job.work_arrangement == work_style)
    # One evidence policy for both database engines, applied before any pagination.
    rows = session.scalars(statement.where(*filters).order_by(Job.id.asc())).unique().all()
    return _jobs_page(rows, sort, offset, limit, latitude, longitude, radius_km,
                      profile, min_match_score, include_unknown, place, country if area else None)


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session),
            profile_id: Annotated[int | None, Query(ge=1)] = None) -> dict[str, Any]:
    statement = select(Job).options(joinedload(Job.feed).joinedload(JobFeed.company),
                                    selectinload(Job.locations)).where(Job.id == job_id)
    job = session.scalars(statement).unique().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = _job_json(job, include_description=True)
    if profile_id is not None:
        profile = require_profile(profile_id, session)
        area = profile.preferences.get('search_area') or {}
        scope = geographic_scope(job, area.get('latitude'), area.get('longitude'),
                                 area.get('radius_km', 35), area.get('city'),
                                 area.get('country_code') or 'DE') if area else None
        data['geography'] = scope
        data['profile_match'] = _profile_match(job, profile, scope)
    return data


@app.get("/api/v1/companies/{company_id}")
def get_company(company_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    company = session.scalars(select(Company).options(selectinload(Company.feeds).selectinload(JobFeed.jobs))
                              .where(Company.id == company_id)).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    all_jobs = _deduplicate_jobs([job for feed in company.feeds for job in feed.jobs])
    active_jobs = _deduplicate_jobs([job for job in all_jobs if job.is_active])
    data = _company_json(company, float("inf"), len(active_jobs))
    data["feeds"] = [_feed_json(feed) for feed in company.feeds]
    data["jobs"] = [_job_json(job) for job in all_jobs]
    return data


@app.get("/api/v1/summary")
def summary(latitude: float = Query(49.0068705, ge=-90, le=90),
            longitude: float = Query(8.4034195, ge=-180, le=180),
            radius_km: float = Query(15, gt=0, le=200),
            place: str | None = Query(None, max_length=120),
            session: Session = Depends(get_session)) -> dict[str, Any]:
    company_page = list_companies(latitude, longitude, radius_km, None, 0, 1, session)
    job_page = list_jobs(latitude=latitude, longitude=longitude, radius_km=radius_km,
                         company_id=None, query=None, place=place, offset=0, limit=1, session=session)
    remote_job_page = list_jobs(latitude=latitude, longitude=longitude, radius_km=radius_km,
                                company_id=None, query=None, place=place, offset=0, limit=1,
                                work_style="remote", session=session)
    if IS_SQLITE:
        domain_candidates = session.scalars(select(Company).where(Company.domain.is_not(None))).all()
        domain_count = sum(_distance(company, latitude, longitude) <= radius_km * 1000 for company in domain_candidates)
    else:
        domain_count = session.scalar(text("SELECT count(*) FROM companies WHERE domain IS NOT NULL AND ST_DWithin(location_geog, ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)::geography,:radius_m,false)")
                                      .params(longitude=longitude, latitude=latitude, radius_m=radius_km * 1000)) or 0
    return {"companies_in_radius": company_page["total"], "companies_with_domain": domain_count,
            "jobs_for_location": job_page["total"],
            "remote_jobs_in_result": remote_job_page["total"],
            "as_of": datetime.now(timezone.utc).isoformat()}
