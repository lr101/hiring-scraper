"""HTTP API for nearby companies, job discovery, and profile matching."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from hiring_scraper.app.companion import router as companion_router
from hiring_scraper.app.application_documents import router as application_documents_router
from hiring_scraper.app.application_generation import router as application_generation_router
from hiring_scraper.app.document_revision import router as document_revision_router
from hiring_scraper.app.application_deduplication import index_vacancy_identities
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.orm import Session, aliased, joinedload, selectinload
from hiring_scraper.app.database import IS_SQLITE, dialect_insert, engine, get_session, initialize_sqlite_schema
from hiring_scraper.app.models import (
    ApplicationMomentumEvent, Company, ConfiguredLocation, DiscoveryJob, DiscoveryJobCompany,
    DiscoveryRun, Job, JobApplication, JobFeed, JobSearchResult, JobSearchSnapshot, JobVacancyGroup,
    LocationCache, ScanRun, SemanticSearchDocument, SemanticSearchIndexState, UserProfile, utcnow,
)
from hiring_scraper.app.discovery_jobs import (
    configured_location_json, create_discovery_job as make_discovery_job, discovery_job_json, german_company_filter,
)
from hiring_scraper.election_data import municipality_vote_result
from hiring_scraper.app.feed_schedules import (
    configured_location_scope_payload, germany_search_scope_payload,
    is_synthetic_germany_scope, next_scope_due, queue_arbeitsagentur_scope,
)
from hiring_scraper.app.default_job_feeds import ensure_default_job_feeds
from hiring_scraper.app.company_identity import company_domain, find_company_by_website, is_hosted_job_site
from hiring_scraper.geography import GERMAN_STATES, haversine_m, normalize_german_state
from hiring_scraper.app.profiles import router as profiles_router, require_profile
from hiring_scraper.app.applications import (
    APPLIED_STATUSES, APPLICATION_STATUSES, ApplicationRequest, ApplicationStatus, application_json,
    canonical_application_states, canonical_application_states_for_jobs, close_job_applications,
)
from hiring_scraper.app.job_search import (
    ProfileIndexChanged, ensure_profile_match_indexes, profile_match_index_stale, refresh_application_matches_for_jobs,
)
from hiring_scraper.matching import VERSION as MATCHING_VERSION
from hiring_scraper.semantic_matching import MODEL_KEY as SEMANTIC_MODEL_KEY
from hiring_scraper.app import search_snapshots
from hiring_scraper.app.board_scope import deduplicate_jobs


KARLSRUHE = {"label": "Karlsruhe, Baden-Württemberg, Deutschland", "city": "Karlsruhe",
             "state": "Baden-Württemberg", "latitude": 49.0068705, "longitude": 8.4034195,
             "precision": "city_centroid", "country_code": "de", "type": "city"}
ATS_FEED_PROVIDERS = {"greenhouse", "lever", "personio", "ashby", "recruitee", "smartrecruiters"}
MANAGED_FEED_PROVIDERS = {"external_html", "browser_html", "rss", "arbeitsagentur", *ATS_FEED_PROVIDERS}
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_REQUEST = 0.0
_SEMANTIC_EMBEDDING_URL = os.getenv("SEMANTIC_EMBEDDING_URL", "http://127.0.0.1:8001/embed")
APPLICATION_WEEKLY_GOAL = max(1, int(os.getenv("APPLICATION_WEEKLY_GOAL", "10")))
_QUERY_EMBEDDINGS: dict[str, list[float]] = {}
_QUERY_EMBEDDING_LOCK = threading.Lock()


def _query_embedding(query: str) -> list[float] | None:
    """Request a normalized query vector from the resident local ONNX worker."""
    normalized = " ".join(query.split())
    with _QUERY_EMBEDDING_LOCK:
        cached = _QUERY_EMBEDDINGS.get(normalized.casefold())
    if cached is not None:
        return cached
    request = Request(_SEMANTIC_EMBEDDING_URL,
                      data=json.dumps({"text": normalized}).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read())
        vector = payload.get("embedding") if isinstance(payload, dict) else None
        if not isinstance(vector, list) or len(vector) != 384:
            return None
        vector = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in vector):
            return None
    except (OSError, ValueError, TypeError, HTTPError, URLError, json.JSONDecodeError):
        return None
    with _QUERY_EMBEDDING_LOCK:
        if len(_QUERY_EMBEDDINGS) >= 256:
            _QUERY_EMBEDDINGS.pop(next(iter(_QUERY_EMBEDDINGS)))
        _QUERY_EMBEDDINGS[normalized.casefold()] = vector
    return vector


def _semantic_index_ready(session: Session, entity_type: str) -> bool:
    return bool(session.scalar(select(SemanticSearchIndexState.backfill_complete).where(
        SemanticSearchIndexState.entity_type == entity_type,
        SemanticSearchIndexState.model_revision == SEMANTIC_MODEL_KEY,
    )))


def _search_query_embedding(session: Session, query: str | None, entity_type: str) -> list[float] | None:
    if not query or IS_SQLITE or not _semantic_index_ready(session, entity_type):
        return None
    # These read endpoints must return their connection before local HTTP I/O.
    # A burst of embedding requests otherwise exhausts the database pool.
    session.rollback()
    return _query_embedding(query)


class LocationQuery(BaseModel):
    search_scope: Literal["radius", "state", "country"] | None = None
    query: str = Field(min_length=2, max_length=200)


class ConfiguredLocationCreate(BaseModel):
    search_scope: Literal["radius", "state", "country"] = "radius"
    label: str | None = Field(None, min_length=2, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postcode: str | None = Field(default=None, max_length=20)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    radius_km: float = Field(default=15, gt=0, le=200)
    interval_days: int = Field(default=7, ge=1, le=365)


class ConfiguredLocationUpdate(BaseModel):
    radius_km: float | None = Field(default=None, gt=0, le=200)
    interval_days: int | None = Field(default=None, ge=1, le=365)
    enabled: bool | None = None


class DiscoveryJobCreate(BaseModel):
    search_scope: Literal["radius", "state", "country"] = "radius"
    location_id: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, min_length=2, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postcode: str | None = Field(default=None, max_length=20)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=200)
    scheduled_for: datetime | None = None


class ManagedFeedCreate(BaseModel):
    company_id: int | None = Field(default=None, ge=1)
    company_name: str | None = Field(default=None, max_length=500)
    website_url: str | None = Field(default=None, max_length=2000)
    feed_url: str = Field(min_length=8, max_length=2000)
    provider: Literal["auto", "external_html", "browser_html", "rss", "ats", "arbeitsagentur"] = "external_html"
    track_company: bool = False
    location_query: str | None = Field(default=None, max_length=200)
    radius_km: float | None = Field(default=None, gt=0, le=200)
    terms: list[str] | None = Field(default=None, max_length=50)


class ManagedFeedUpdate(BaseModel):
    feed_url: str | None = Field(default=None, min_length=8, max_length=2000)
    provider: Literal["external_html", "browser_html", "rss", "ats", "arbeitsagentur"] | None = None
    enabled: bool | None = None
    career_page_url: str | None = Field(default=None, max_length=2000)
    location_query: str | None = Field(default=None, max_length=200)
    radius_km: float | None = Field(default=None, gt=0, le=200)
    terms: list[str] | None = Field(default=None, max_length=50)


class CompanyUpdate(BaseModel):
    domain: str | None = Field(default=None, max_length=255)


def _model_value(request: Any, key: str, default: Any = None) -> Any:
    if isinstance(request, dict):
        return request.get(key, default)
    return getattr(request, key, default)


def _key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _managed_page_url(value: str | None, label: str = "Job page") -> str:
    if not isinstance(value, str) or not value or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        raise HTTPException(status_code=422, detail=f"{label} must be a valid public HTTPS URL")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if (parts.scheme != "https" or not host or parts.username or parts.password or
                port not in (None, 443) or parts.fragment):
            raise ValueError("Expected a public HTTPS URL without credentials or a fragment")
        host = host.rstrip(".").casefold()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("Local hostnames are not public job pages")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("Private and reserved addresses are not accepted")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{label} must be a valid public HTTPS URL: {error}") from error
    return value


def _managed_company_domain(value: str | None) -> str | None:
    """Normalize and validate a manually entered public company hostname."""
    if value is None or not value.strip():
        return None
    candidate = value.strip().rstrip(".")
    if re.search(r"[\s\x00-\x1f\x7f\\]", candidate):
        raise HTTPException(status_code=422, detail="Company domain must be a valid public hostname")
    try:
        parts = urlsplit(f"//{candidate}")
        host = parts.hostname
        if (not host or parts.username or parts.password or parts.port is not None or
                parts.path or parts.query or parts.fragment):
            raise ValueError("Enter a domain only, without a path or port")
        ascii_host = host.encode("idna").decode("ascii").casefold().rstrip(".").removeprefix("www.")
        try:
            address = ipaddress.ip_address(ascii_host)
        except ValueError:
            address = None
        if address is None and (len(ascii_host) > 253 or not re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+",
                ascii_host)):
            raise ValueError("Enter a fully qualified hostname")
        if address is not None and not address.is_global:
            raise ValueError("Private and reserved addresses are not accepted")
        if address is None:
            # Reuse the public host checks applied to manually managed source URLs.
            _managed_page_url(f"https://{ascii_host}", "Company domain")
        return ascii_host
    except (UnicodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"Company domain must be a valid public hostname: {error}") from error


def _managed_feed_source(url: str, provider: str,
                         source_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a manually entered career page to its bounded provider feed."""
    source_config = source_config or {}
    if provider == "auto":
        from hiring_scraper.ats import identify
        identified = identify(url)
        if identified and identified.get("provider") in ATS_FEED_PROVIDERS and identified.get("feed_url"):
            provider = "ats"
        else:
            provider = "rss" if re.search(r'\.(?:rss|xml|atom)$', urlsplit(url).path, re.I) else "external_html"
    if provider == "arbeitsagentur":
        parts = urlsplit(url)
        if (parts.hostname or "").casefold().removeprefix("www.") != "arbeitsagentur.de" or not re.match(
                r"^/jobsuche(?:/|$)", parts.path, re.IGNORECASE):
            raise HTTPException(status_code=422, detail="Use the public Agentur für Arbeit Jobsuche page")
        identity = hashlib.sha256(json.dumps(source_config, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"provider": "arbeitsagentur",
                "feed_url": f"https://www.arbeitsagentur.de/jobsuche/?adapter_query={identity}",
                "board_url": "https://www.arbeitsagentur.de/jobsuche/", "tenant": None}
    if provider != "ats":
        return {"provider": provider, "feed_url": url, "board_url": url, "tenant": None}
    from hiring_scraper.ats import identify

    identified = identify(url)
    if not identified or identified.get("provider") not in ATS_FEED_PROVIDERS or not identified.get("feed_url"):
        raise HTTPException(status_code=422, detail="This page does not expose a supported public ATS feed")
    return {
        "provider": identified["provider"],
        "feed_url": identified["feed_url"],
        "board_url": identified.get("board_url") or url,
        "tenant": identified.get("tenant"),
    }


def _schedule_managed_arbeitsagentur_feed(session: Session, feed: JobFeed,
                                          now: datetime) -> None:
    """Attach active and saved search geographies to a managed BA adapter."""
    if not feed.enabled:
        feed.next_scan_at = None
        return
    active_jobs = session.scalars(select(DiscoveryJob).where(
        DiscoveryJob.status.in_(("scheduled", "queued", "running"))
    ).order_by(DiscoveryJob.id)).all()
    for job in active_jobs:
        queue_arbeitsagentur_scope(session, job, job.scheduled_for or now, feed_id=feed.id)

    pending = list(feed.pending_search_scopes) if isinstance(feed.pending_search_scopes, list) else []
    country_location = session.scalars(select(ConfiguredLocation).where(
        ConfiguredLocation.enabled.is_(True),
        ConfiguredLocation.search_scope == "country").order_by(ConfiguredLocation.id).limit(1)).first()
    if (country_location is not None and
            not any(isinstance(item, dict) and item.get("search_scope") == "country" for item in pending)):
        pending.append(configured_location_scope_payload(country_location, now))
    has_regional_schedule = session.scalar(select(ConfiguredLocation.id).where(
        ConfiguredLocation.enabled.is_(True),
        ConfiguredLocation.search_scope != "country").limit(1))
    if has_regional_schedule is not None:
        pending = [item for item in pending if not (
            isinstance(item, dict) and is_synthetic_germany_scope(item))]
    if not pending and has_regional_schedule is None:
        pending = [germany_search_scope_payload(now)]
    feed.pending_search_scopes = pending
    feed.next_scan_at = next_scope_due(pending, now)


def _managed_feed_config(session: Session, request: Any, provider: str,
                         previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve an optional local filter for page feeds and BA role terms."""
    previous = previous if isinstance(previous, dict) else {}
    supplied = getattr(request, "model_fields_set", set())
    config = dict(previous)
    if provider == "arbeitsagentur":
        # The BA adapter inherits each scheduled discovery job's area. Its
        # former free-text filter is intentionally discarded on edits.
        config.pop("location_filter", None)
        config["managed_adapter"] = True
    else:
        previous_filter = previous.get("location_filter")
        previous_filter = previous_filter if isinstance(previous_filter, dict) else {}
        location_query = (getattr(request, "location_query", None) if "location_query" in supplied
                          else previous_filter.get("query"))
        location_query = " ".join(location_query.split()) if isinstance(location_query, str) else ""
        radius_value = (getattr(request, "radius_km", None) if "radius_km" in supplied
                        else previous_filter.get("radius_km"))
        if radius_value is None and location_query:
            radius_value = 35
        if location_query and len(location_query) < 2:
            raise HTTPException(status_code=422, detail="Location must have at least two characters")
        if radius_value is not None and not location_query:
            raise HTTPException(status_code=422, detail="Enter a location when setting a search radius")
        if location_query:
            if str(previous_filter.get("query") or "").casefold() == location_query.casefold() and previous_filter.get("latitude") is not None:
                resolved = previous_filter
            else:
                normalized = _key(location_query)
                if normalized in {"karlsruhe", "karlsruhe baden württemberg", "karlsruhe baden-württemberg"}:
                    results = [KARLSRUHE]
                else:
                    cached = session.scalar(select(LocationCache).where(LocationCache.query_key == normalized))
                    if cached:
                        results = cached.results
                    else:
                        results = _nominatim_results(location_query)
                        session.add(LocationCache(query_key=normalized, results=results))
                        session.flush()
                if not results:
                    raise HTTPException(status_code=422, detail="Could not resolve this location in Germany")
                resolved = results[0]
            city = resolved.get("city") or location_query
            config["location_filter"] = {
                "query": location_query, "city": city, "label": resolved.get("label") or location_query,
                "latitude": resolved.get("latitude"), "longitude": resolved.get("longitude"),
                "precision": resolved.get("precision"), "country_code": "DE",
                "radius_km": float(radius_value if radius_value is not None else 35),
            }
        else:
            config.pop("location_filter", None)

    if provider == "arbeitsagentur":
        terms = getattr(request, "terms", None) if "terms" in supplied else previous.get("terms")
        if terms is None:
            terms = []
        cleaned, seen = [], set()
        for value in terms:
            term = " ".join(value.split()) if isinstance(value, str) else ""
            if not term:
                continue
            if len(term) > 120:
                raise HTTPException(status_code=422, detail="Each job term must be at most 120 characters")
            normalized = _key(term)
            if normalized not in seen:
                cleaned.append(term)
                seen.add(normalized)
        from hiring_scraper.regional import DEFAULT_TERMS
        uses_default_terms = (not bool(cleaned) if "terms" in supplied
                              else bool(previous.get("uses_default_terms", not bool(cleaned))))
        config["uses_default_terms"] = uses_default_terms
        config["terms"] = cleaned or list(DEFAULT_TERMS)
    else:
        config.pop("terms", None)
        config.pop("uses_default_terms", None)
        config.pop("managed_adapter", None)
    return config


def _is_managed_arbeitsagentur_feed(feed: JobFeed) -> bool:
    config = feed.source_config if isinstance(feed.source_config, dict) else {}
    return feed.provider == "arbeitsagentur" and (
        config.get("managed_adapter") is True or bool(config.get("location_filter")) or
        feed.company.source == "managed_feed")


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
        city_label = ", ".join(value for value in (city, state) if value)
        results.append({"label": item.get("display_name", query), "city": city,
                        "postcode": address.get("postcode"), "state": state,
                        "latitude": latitude, "longitude": longitude,
                        "precision": "city_centroid" if item.get("addresstype") in {"city", "town", "village", "municipality"} else "place_point",
                        "country_code": "de", "type": item.get("type") or item.get("addresstype") or "place",
                        "city_election_result": municipality_vote_result(city_label, "de")})
    return results


def _with_city_election_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for item in results:
        city_label = ", ".join(value for value in (item.get("city"), item.get("state")) if value)
        enriched.append({**item, "city_election_result": municipality_vote_result(
            city_label or item.get("label"), item.get("country_code"))})
    return enriched


def _distance(company: Company, latitude: float, longitude: float) -> float:
    if company.latitude is None or company.longitude is None:
        return float("inf")
    return haversine_m(latitude, longitude, company.latitude, company.longitude)


def _sqlite_company_bbox(latitude: float, longitude: float, radius_km: float):
    """Use indexed coordinate bounds before the exact Python haversine check."""
    return search_snapshots.coordinate_bbox_filters(latitude, longitude, radius_km,
                                    Company.latitude, Company.longitude)


def _company_json(company: Company, distance_m: float, active_job_count: int | None = None) -> dict[str, Any]:
    next_check = None
    if not company.crawl_paused:
        scheduled = [value for value in [company.next_discovery_at, *(feed.next_scan_at for feed in company.feeds)] if value]
        if scheduled:
            next_check = min(scheduled, key=lambda value: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value)
    return {"country_code": company.country_code, "employee_count": company.employee_count,
            "crawl_priority": company.crawl_priority, "crawl_interval_days": company.crawl_interval_days,
            "crawl_reason": company.crawl_reason, "crawl_paused": company.crawl_paused,
            "ranking_updated_at": company.ranking_updated_at.isoformat() if company.ranking_updated_at else None,
            "next_check_at": next_check.isoformat() if next_check else None,
            "id": company.id, "name": company.name, "domain": company.domain,
            "website_url": company.website_url, "category": company.category,
            "domain_match_method": company.domain_match_method,
            "domain_evidence_url": company.domain_evidence_url,
            "source": company.source, "source_url": company.source_url,
            "state": company.state, "latitude": company.latitude, "longitude": company.longitude,
            "location_precision": company.location_precision, "location_label": company.location_label,
            "career_url": company.career_url, "career_status": company.career_status,
            "distance_m": round(distance_m, 1) if distance_m != float("inf") else None,
            "feed_count": len(company.feeds),
            "active_job_count": active_job_count if active_job_count is not None else
            sum(feed.job_count or 0 for feed in company.feeds)}


def _unique_active_job_counts(session: Session, company_ids: list[int]) -> dict[int, int]:
    if not company_ids:
        return {}
    jobs = session.scalars(select(Job).join(Job.feed).where(
        Job.is_active.is_(True), or_(JobFeed.company_id.in_(company_ids),
                                     Job.employer_company_id.in_(company_ids))).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations))).unique().all()
    grouped: dict[int, list[Job]] = {company_id: [] for company_id in company_ids}
    for job in jobs:
        company_id = job.employer_company_id or job.feed.company_id
        if company_id in grouped:
            grouped[company_id].append(job)
    return {company_id: len(deduplicate_jobs(rows)) for company_id, rows in grouped.items()}


def _feed_json(feed: JobFeed) -> dict[str, Any]:
    return {"id": feed.id, "provider": feed.provider, "tenant": feed.tenant,
            "board_url": feed.board_url, "feed_url": feed.feed_url,
            "source_config": feed.source_config or {},
            "enabled": feed.enabled,
            "status": feed.status, "job_count": feed.job_count,
            "last_checked_at": feed.last_checked_at.isoformat() if feed.last_checked_at else None,
            "next_scan_at": feed.next_scan_at.isoformat() if feed.next_scan_at else None,
            "last_error": feed.last_error}


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
    # Feed scans and stale search snapshot refreshes run in separate workers.


app.include_router(profiles_router)
app.include_router(companion_router)
app.include_router(application_documents_router)
app.include_router(application_generation_router)
app.include_router(document_revision_router)


@app.get("/health/ready")
def readiness(session: Session = Depends(get_session)) -> dict[str, str]:
    try:
        session.execute(select(1))
    except Exception as error:
        raise HTTPException(status_code=503, detail="Database is not ready") from error
    return {"status": "ready"}


def _canonical_country(country: str | None) -> str | None:
    if country is None:
        return None
    if country.strip().casefold() not in {"de", "germany", "deutschland"}:
        raise HTTPException(status_code=422, detail="Only Germany (DE) is supported for country searches")
    return "DE"


def _lock_germany_inventory(session: Session) -> None:
    """Serialize Germany inventory create requests before checking for a live job."""
    if engine.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(1547632348, 1)"))


def _active_germany_inventory(session: Session) -> DiscoveryJob | None:
    return session.scalar(select(DiscoveryJob).where(
        DiscoveryJob.search_scope == "country",
        DiscoveryJob.stage.in_(("company_homepage_discovery", "career_page_discovery")),
        DiscoveryJob.status.in_(("scheduled", "queued", "running")),
    ).order_by(DiscoveryJob.id).limit(1))


def _country_location() -> dict[str, Any]:
    return {"label": "Germany", "country": "DE", "state": None, "city": None, "postcode": None,
            "search_scope": "country", "latitude": 0.0, "longitude": 0.0,
            "precision": "country_boundary", "type": "country", "country_code": "de"}


def _state_location(state: str) -> dict[str, Any]:
    return {"label": state, "state": state, "city": None, "postcode": None,
            "search_scope": "state", "latitude": 0.0, "longitude": 0.0,
            "precision": "state_boundary", "type": "state", "country_code": "de"}


@app.get("/api/v1/states")
def list_states() -> dict[str, Any]:
    return {"items": list(GERMAN_STATES)}


@app.post("/api/v1/locations/resolve")
def resolve_location(request: LocationQuery, session: Session = Depends(get_session)) -> dict[str, Any]:
    if request.search_scope == "country" or request.search_scope != "radius" and request.query.strip().casefold() in {"de", "germany", "deutschland"}:
        _canonical_country(request.query)
        return {"query": request.query, "results": [_country_location()], "cached": True}
    try:
        state = normalize_german_state(request.query) if request.search_scope != "radius" else None
    except ValueError:
        state = None
    if state:
        return {"query": request.query, "results": [_state_location(state)], "cached": True}
    normalized = _key(request.query)
    if normalized in {"karlsruhe", "karlsruhe baden württemberg", "karlsruhe baden-württemberg"}:
        return {"query": request.query, "results": _with_city_election_results([KARLSRUHE]), "cached": True}
    cached = session.scalar(select(LocationCache).where(LocationCache.query_key == normalized))
    if cached:
        return {"query": request.query, "results": _with_city_election_results(cached.results), "cached": True}
    results = _nominatim_results(request.query)
    entry = LocationCache(query_key=normalized, results=results)
    session.add(entry)
    session.commit()
    return {"query": request.query, "results": _with_city_election_results(results), "cached": False}


@app.get("/api/v1/locations")
def list_configured_locations(session: Session = Depends(get_session)) -> dict[str, Any]:
    locations = session.scalars(select(ConfiguredLocation).order_by(
        ConfiguredLocation.enabled.desc(), ConfiguredLocation.next_run_at.asc(), ConfiguredLocation.id.asc()
    )).all()
    return {"items": [configured_location_json(location) for location in locations]}


@app.post("/api/v1/locations", status_code=201)
def create_configured_location(request: ConfiguredLocationCreate,
                               session: Session = Depends(get_session)) -> dict[str, Any]:
    search_scope = _model_value(request, "search_scope", "radius")
    active_country_job = None
    if search_scope == "country":
        _canonical_country(_model_value(request, "country", "DE"))
        _lock_germany_inventory(session)
        active_country_job = _active_germany_inventory(session)
        existing = session.scalar(select(ConfiguredLocation).where(
            ConfiguredLocation.search_scope == "country").order_by(ConfiguredLocation.id).limit(1))
        if existing is not None:
            latest = active_country_job or session.scalar(select(DiscoveryJob).where(
                DiscoveryJob.search_scope == "country").order_by(DiscoveryJob.id.desc()).limit(1))
            result = {**configured_location_json(existing), "already_saved": True,
                      "first_discovery_job": discovery_job_json(latest) if latest else None}
            session.commit()
            return result
        values = _country_location()
    elif search_scope == "state":
        state = search_snapshots.canonical_state(_model_value(request, "state"))
        if not state:
            raise HTTPException(status_code=422, detail="Choose a German state")
        values = _state_location(state)
    else:
        values = {key: _model_value(request, key) for key in
                  ("label", "city", "state", "postcode", "latitude", "longitude")}
        if values["latitude"] is None or values["longitude"] is None:
            raise HTTPException(status_code=422, detail="Provide coordinates for a radius search")
    label = str(values["label"] or "").strip()
    if len(label) < 2:
        raise HTTPException(status_code=422, detail="Location label must contain at least two characters")
    now = utcnow()
    interval_days = int(_model_value(request, "interval_days", 7))
    location = ConfiguredLocation(
        label=label,
        search_scope=search_scope, city=values["city"], state=values["state"],
        postcode=values["postcode"],
        latitude=float(values["latitude"]), longitude=float(values["longitude"]),
        radius_km=float(_model_value(request, "radius_km", 15)),
        interval_days=interval_days, enabled=True,
        next_run_at=(now + timedelta(days=interval_days) if search_scope != "country" else None),
        created_at=now, updated_at=now,
    )
    session.add(location)
    session.flush()
    if search_scope == "country" and active_country_job is not None:
        # The saved location may have been removed while its inventory kept
        # running, or a manual country scan may have started first. Reuse that
        # global inventory and attach its status to the newly saved entry.
        active_country_job.configured_location_id = location.id
        first_job = active_country_job
    else:
        first_job = make_discovery_job(
            session, label=location.label, search_scope=location.search_scope, city=location.city, state=location.state,
            postcode=location.postcode, latitude=location.latitude,
            longitude=location.longitude, radius_km=location.radius_km,
            configured_location_id=location.id, kind="initial", now=now,
        )
    queue_arbeitsagentur_scope(session, first_job, first_job.scheduled_for or now)
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
        location.next_run_at = (now + timedelta(days=location.interval_days)
                                if location.search_scope != "country" else None)
    if enabled is not None:
        location.enabled = bool(enabled)
        if (location.enabled and location.search_scope != "country" and
                (location.next_run_at is None or location.next_run_at <= now)):
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
        search_scope = location.search_scope
        label, city, state, postcode = location.label, location.city, location.state, location.postcode
        latitude, longitude = location.latitude, location.longitude
        radius_km, configured_location_id = location.radius_km, location.id
    elif _model_value(request, "search_scope", "radius") == "country":
        _canonical_country(_model_value(request, "country", "DE"))
        search_scope, label, city, state, postcode = "country", "Germany", None, None, None
        latitude, longitude, radius_km, configured_location_id = 0.0, 0.0, 15, None
    elif _model_value(request, "search_scope", "radius") == "state":
        search_scope = "state"
        state = search_snapshots.canonical_state(_model_value(request, "state"))
        if not state:
            raise HTTPException(status_code=422, detail="Choose a German state")
        label, city, postcode = state, None, None
        latitude, longitude, radius_km, configured_location_id = 0.0, 0.0, 15, None
    else:
        search_scope = "radius"
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
    if search_scope == "country":
        _lock_germany_inventory(session)
        active = _active_germany_inventory(session)
        if active is not None:
            result = discovery_job_json(active)
            session.commit()
            return result
    job = make_discovery_job(
        session, label=label, search_scope=search_scope, city=city, state=state, postcode=postcode,
        latitude=float(latitude), longitude=float(longitude), radius_km=float(radius_km),
        configured_location_id=configured_location_id, scheduled_for=scheduled_for,
    )
    queue_arbeitsagentur_scope(session, job, job.scheduled_for or utcnow())
    if search_scope == "country":
        ensure_default_job_feeds(session)
        now = utcnow()
        refresh_at = job.scheduled_for or now
        protected = {"blocked", "bot_challenge", "throttled", "failed", "incomplete",
                     "schema_error", "unsupported", "paused"}
        feeds = session.scalars(select(JobFeed).join(Company).where(
            Company.source == "default_job_feed", JobFeed.enabled.is_(True))).all()
        for feed in feeds:
            lease = feed.lease_until
            if lease is not None and lease.tzinfo is None:
                lease = lease.replace(tzinfo=timezone.utc)
            if lease is not None and lease > now:
                continue
            if feed.status not in protected:
                feed.next_scan_at = refresh_at
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


def _ranked_company_health(session: Session, now: datetime) -> dict[str, int]:
    company_domain = func.lower(Company.domain)
    company_domain = case((company_domain.like("www.%"), func.substr(company_domain, 5)), else_=company_domain)
    german_companies = (select(Company.id.label("company_id"), company_domain.label("domain_key"),
                               Company.crawl_paused.label("crawl_paused"))
                        .where(german_company_filter(), Company.website_url.is_not(None), Company.website_url != "",
                               or_(Company.category.is_(None), Company.category != "job_board_source"))
                        .subquery())
    german_domain_roots = (select(
        german_companies.c.domain_key,
        func.coalesce(
            func.min(case((german_companies.c.crawl_paused.is_(False), german_companies.c.company_id), else_=None)),
            func.min(german_companies.c.company_id),
        ).label("company_id"),
    ).where(german_companies.c.domain_key.is_not(None), german_companies.c.domain_key != "")
        .group_by(german_companies.c.domain_key).subquery())
    german_company_roots = (select(func.coalesce(german_domain_roots.c.company_id,
                                                 german_companies.c.company_id).label("company_id"))
                            .select_from(german_companies)
                            .outerjoin(german_domain_roots,
                                       german_domain_roots.c.domain_key == german_companies.c.domain_key)
                            .distinct().subquery())
    interval = func.coalesce(Company.crawl_interval_days, 7)
    health_counts = session.execute(select(
        func.count(Company.id).label("total"),
        func.sum(case((interval <= 1, 1), else_=0)).label("daily"),
        func.sum(case((interval == 7, 1), else_=0)).label("weekly"),
        func.sum(case((interval == 30, 1), else_=0)).label("monthly"),
        func.sum(case((and_(interval > 1, interval != 7, interval != 30), 1), else_=0)).label("other"),
        func.sum(case((and_(Company.next_discovery_at.is_not(None), Company.next_discovery_at <= now), 1),
                      else_=0)).label("due"),
        func.sum(case((and_(Company.next_discovery_at.is_not(None),
                            Company.next_discovery_at <= now - timedelta(hours=24)), 1), else_=0)).label("overdue"),
        func.sum(case((Company.next_discovery_at.is_(None), 1), else_=0)).label("unscheduled"),
    ).join(german_company_roots, Company.id == german_company_roots.c.company_id)
      .where(Company.website_url.is_not(None), Company.website_url != "", Company.crawl_paused.is_(False))
      ).mappings().one()
    return {key: int(health_counts[key] or 0) for key in
            ("total", "daily", "weekly", "monthly", "other", "due", "overdue", "unscheduled")}


@app.get("/api/v1/ranked-company-health")
def list_ranked_company_health(session: Session = Depends(get_session)) -> dict[str, int]:
    """Return Germany's ranked company check schedule and backlog counts."""
    return _ranked_company_health(session, utcnow())


@app.get("/api/v1/active-workers")
def list_active_workers(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return work with a live lease, without creating a persistent activity history."""
    now = utcnow()
    items: list[dict[str, Any]] = []
    germany_job = session.scalar(select(DiscoveryJob).where(
        DiscoveryJob.search_scope == "country",
    ).order_by(DiscoveryJob.created_at.desc(), DiscoveryJob.id.desc()).limit(1))
    germany_map_progress = None
    if germany_job is not None:
        cursor = germany_job.map_cursor if isinstance(germany_job.map_cursor, dict) else {}
        map_complete = bool(cursor.get("done"))
        state_index = max(0, int(cursor.get("state_index", 0)))
        germany_map_progress = {
            "job_id": germany_job.id,
            "status": germany_job.status,
            "stage": germany_job.stage,
            "complete": map_complete,
            "resumable": (not map_complete and germany_job.status == "failed" and
                          germany_job.stage == "company_homepage_discovery"),
            "states_completed": len(GERMAN_STATES) if map_complete else min(len(GERMAN_STATES), state_index),
            "current_state_number": None if map_complete else min(len(GERMAN_STATES), state_index + 1),
            "states_total": len(GERMAN_STATES),
            "sections_saved": max(0, int(cursor.get("completed_sections", 0))),
            "companies_found": germany_job.companies_found,
            "websites_found": germany_job.homepages_found,
            "scheduled_for": germany_job.scheduled_for.isoformat() if germany_job.scheduled_for else None,
            "error": germany_job.error,
        }

    location_jobs = session.scalars(select(DiscoveryJob).where(
        DiscoveryJob.status == "running",
        DiscoveryJob.location_scan_lease_until.is_not(None),
        DiscoveryJob.location_scan_lease_until > now,
    ).order_by(DiscoveryJob.progress_updated_at.desc().nullslast(), DiscoveryJob.id)).all()
    for job in location_jobs:
        if job.search_scope == "country":
            cursor = job.map_cursor if isinstance(job.map_cursor, dict) else {}
            state_number = min(len(GERMAN_STATES), max(0, int(cursor.get("state_index", 0))) + 1)
            sections_saved = max(0, int(cursor.get("completed_sections", 0)))
            activity = f"Mapping Germany · state {state_number} of {len(GERMAN_STATES)}"
            context = f"{sections_saved} map sections saved"
        elif job.search_scope == "state":
            activity = f"Searching company listings in {job.state or job.label}"
            context = "Company and website discovery"
        else:
            activity = f"Searching company listings within {job.radius_km:g} km"
            context = "Company and website discovery"
        items.append({
            "id": f"location:{job.id}",
            "kind": "location_search",
            "title": job.label,
            "activity": activity,
            "context": context,
            "started_at": job.started_at.isoformat() if job.started_at else None,
        })

    latest_discovery_run_id = (select(func.max(DiscoveryRun.id))
                               .where(DiscoveryRun.company_id == Company.id)
                               .correlate(Company).scalar_subquery())
    discovery_rows = session.execute(
        select(DiscoveryRun, Company, DiscoveryJob)
        .join(Company, Company.id == DiscoveryRun.company_id)
        .outerjoin(DiscoveryJobCompany, DiscoveryJobCompany.discovery_run_id == DiscoveryRun.id)
        .outerjoin(DiscoveryJob, DiscoveryJob.id == DiscoveryJobCompany.discovery_job_id)
        .where(DiscoveryRun.id == latest_discovery_run_id,
               DiscoveryRun.status == "running",
               Company.discovery_lease_until.is_not(None), Company.discovery_lease_until > now)
        .order_by(DiscoveryRun.started_at.desc(), DiscoveryRun.id)
    ).all()
    for run, company, job in discovery_rows:
        items.append({
            "id": f"company:{run.id}",
            "kind": "company_crawl",
            "title": company.name,
            "activity": "Checking the website for job openings",
            "context": job.label if job is not None else company.domain or "Ranked company check",
            "started_at": run.started_at.isoformat(),
        })

    homepage_companies = session.scalars(select(Company).where(
        Company.website_url.is_(None),
        Company.homepage_resolution_lease_until.is_not(None),
        Company.homepage_resolution_lease_until > now,
    ).order_by(Company.homepage_resolution_lease_until, Company.id)).all()
    for company in homepage_companies:
        items.append({
            "id": f"homepage:{company.id}",
            "kind": "homepage_lookup",
            "title": company.name,
            "activity": "Looking for a verified website",
            "context": f"Attempt {company.homepage_resolution_attempts}",
            "started_at": None,
        })

    latest_feed_scan_id = (select(func.max(ScanRun.id))
                           .where(ScanRun.feed_id == JobFeed.id)
                           .correlate(JobFeed).scalar_subquery())
    feed_rows = session.execute(
        select(ScanRun, JobFeed, Company)
        .join(JobFeed, JobFeed.id == ScanRun.feed_id)
        .join(Company, Company.id == JobFeed.company_id)
        .where(ScanRun.id == latest_feed_scan_id,
               ScanRun.status == "running",
               JobFeed.lease_until.is_not(None), JobFeed.lease_until > now)
        .order_by(ScanRun.started_at.desc(), ScanRun.id)
    ).all()
    for run, feed, company in feed_rows:
        items.append({
            "id": f"feed:{run.id}",
            "kind": "job_feed_scan",
            "feed_id": feed.id,
            "title": company.name,
            "activity": f"Refreshing {feed.provider} job listings",
            "context": feed.tenant or feed.provider,
            "started_at": run.started_at.isoformat(),
        })

    items.sort(key=lambda item: item["started_at"] or "", reverse=True)
    return {"items": items, "active_count": len(items), "germany_map_progress": germany_map_progress}


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


@app.post("/api/v1/discovery-jobs/{discovery_job_id}/resume", status_code=202)
def resume_discovery_job(discovery_job_id: int,
                         session: Session = Depends(get_session)) -> dict[str, Any]:
    """Continue an incomplete Germany map from its last persisted section."""
    _lock_germany_inventory(session)
    job = session.scalar(select(DiscoveryJob).where(
        DiscoveryJob.id == discovery_job_id).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Search not found")

    cursor = job.map_cursor if isinstance(job.map_cursor, dict) else {}
    if (job.search_scope != "country" or job.stage != "company_homepage_discovery" or
            cursor.get("done")):
        raise HTTPException(status_code=409, detail="Only an incomplete Germany map search can be resumed")

    if job.status in {"scheduled", "queued", "running"}:
        session.commit()
        return discovery_job_json(job)
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="Only a failed Germany map search can be resumed")
    active = _active_germany_inventory(session)
    if active is not None:
        raise HTTPException(status_code=409, detail="Another Germany map search is already active")

    now = utcnow()
    state_number = min(len(GERMAN_STATES), max(0, int(cursor.get("state_index", 0))) + 1)
    job.status = "queued"
    job.scheduled_for = None
    job.finished_at = None
    job.location_scan_lease_until = None
    job.location_scan_token = None
    job.location_scan_attempt_count = 0
    job.error = None
    job.progress_message = (f"Resuming Germany map from saved progress · state {state_number} of "
                            f"{len(GERMAN_STATES)}")
    job.progress_updated_at = now
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
                   sort: Literal['distance','name'] = 'distance', state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = search_snapshots.canonical_state(state)
    employer_country = _canonical_country(country)
    base = select(Company).options(selectinload(Company.feeds))
    filters = []
    normalized_query = " ".join(query.split()) if query else None
    vector = _search_query_embedding(session, normalized_query, 'company')
    if normalized_query and vector is None:
        # Stay responsive while the local model is loading or unavailable.
        filters.append(Company.name.ilike(f"%{normalized_query}%"))
    if discovery == 'domain':
        filters.append(Company.domain.is_not(None))
    elif discovery == 'career':
        filters.append(or_(Company.career_url.is_not(None),
                           Company.career_status.in_(['career_page_found','jobs_feed_found','jobs_extracted','provider_detected'])))
    elif discovery == 'feed':
        filters.append(Company.feeds.any(JobFeed.provider != 'html_jobs'))
    elif discovery == 'jobs':
        filters.append(or_(Company.feeds.any(JobFeed.jobs.any(Job.is_active.is_(True))),
                           Company.employer_jobs.any(Job.is_active.is_(True))))
    if vector is not None:
        session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        session.execute(text("SET LOCAL hnsw.ef_search = 100"))
        vector_filter = and_(SemanticSearchDocument.entity_type == "company",
                             SemanticSearchDocument.model_revision == SEMANTIC_MODEL_KEY)
        vector_distance = SemanticSearchDocument.embedding.cosine_distance(vector)
    if state or employer_country:
        filters.append(german_company_filter() if employer_country else Company.state == state)
        if vector is not None:
            total = session.scalar(select(func.count()).select_from(Company).join(
                SemanticSearchDocument, and_(SemanticSearchDocument.entity_id == Company.id, vector_filter)
            ).where(*filters)) or 0
            if total:
                rows = session.execute(select(Company, vector_distance.label("distance")).join(
                    SemanticSearchDocument, and_(SemanticSearchDocument.entity_id == Company.id, vector_filter)
                ).options(selectinload(Company.feeds)).where(*filters).order_by(
                    vector_distance, Company.id).offset(offset).limit(limit)).all()
                counts = _unique_active_job_counts(session, [company.id for company, _ in rows])
                items = []
                for company, distance in rows:
                    item = _company_json(company, float("inf"), counts.get(company.id, 0))
                    item["semantic_score"] = round(1 - float(distance), 4)
                    items.append(item)
                return {"items": items, "total": total, "offset": offset, "limit": limit,
                        "search_mode": "semantic",
                        "location": {"state": state, "country": employer_country,
                                     "search_scope": "country" if employer_country else "state"}}
            vector = None
            filters.append(Company.name.ilike(f"%{normalized_query}%"))
        total = session.scalar(select(func.count()).select_from(Company).where(*filters)) or 0
        rows = session.scalars(base.where(*filters).order_by(Company.name, Company.id)
                               .offset(offset).limit(limit)).all()
        counts = _unique_active_job_counts(session, [company.id for company in rows])
        items = [_company_json(company, float("inf"), counts.get(company.id, 0)) for company in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "search_mode": "keyword" if normalized_query else "browse",
                "location": {"state": state, "country": employer_country,
                             "search_scope": "country" if employer_country else "state"}}
    radius_m = radius_km * 1000
    if not IS_SQLITE:
        spatial_clause = text("ST_DWithin(companies.location_geog, ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, :radius_m, false)")
        filters.append(spatial_clause)
        base = base.where(*filters)
        if vector is not None:
            count = session.scalar(select(func.count()).select_from(Company).join(
                SemanticSearchDocument, and_(SemanticSearchDocument.entity_id == Company.id, vector_filter)
            ).where(*filters).params(longitude=longitude, latitude=latitude, radius_m=radius_m)) or 0
            if count:
                distance_m = text("ST_Distance(companies.location_geog, "
                                  "ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, false)")
                rows = session.execute(select(Company, vector_distance.label("semantic_distance"),
                                              distance_m.label("distance_m")).join(
                    SemanticSearchDocument, and_(SemanticSearchDocument.entity_id == Company.id, vector_filter)
                ).options(selectinload(Company.feeds)).where(*filters).order_by(
                    vector_distance, Company.id).offset(offset).limit(limit).params(
                        longitude=longitude, latitude=latitude, radius_m=radius_m)).all()
                counts = _unique_active_job_counts(session, [company.id for company, _, _ in rows])
                items = []
                for company, distance, meters in rows:
                    item = _company_json(company, float(meters), counts.get(company.id, 0))
                    item["semantic_score"] = round(1 - float(distance), 4)
                    items.append(item)
                return {"items": items, "total": count, "offset": offset, "limit": limit,
                        "search_mode": "semantic",
                        "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km}}
            vector = None
            filters.append(Company.name.ilike(f"%{normalized_query}%"))
            base = select(Company).options(selectinload(Company.feeds)).where(*filters)

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
                "search_mode": "keyword" if normalized_query else "browse",
                "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km}}

    candidates = list(session.scalars(base.where(*filters, *_sqlite_company_bbox(latitude, longitude, radius_km))))
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
            "search_mode": "keyword" if normalized_query else "browse",
            "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km}}


def _resolved_search_area(profile, latitude, longitude, radius_km, place, country_code):
    area = (profile.preferences.get('search_area') or {}) if profile else {}
    explicit = any(isinstance(value, (int, float)) for value in (latitude, longitude, radius_km)) or isinstance(place, str) or isinstance(country_code, str)
    latitude = latitude if isinstance(latitude, (int, float)) else area.get('latitude')
    longitude = longitude if isinstance(longitude, (int, float)) else area.get('longitude')
    radius_km = radius_km if isinstance(radius_km, (int, float)) else area.get('radius_km')
    return {'latitude': latitude if latitude is not None else 49.0068705,
            'longitude': longitude if longitude is not None else 8.4034195,
            'radius_km': radius_km if radius_km is not None else 15,
            'place': place if isinstance(place, str) else area.get('city'),
            'country': country_code.upper() if isinstance(country_code, str) else area.get('country_code') or 'DE',
            'profile_area': bool(area), 'explicit': explicit}


@app.get("/api/v1/jobs")
def list_jobs(latitude: float | None = Query(None, ge=-90, le=90),
              longitude: float | None = Query(None, ge=-180, le=180),
              radius_km: float | None = Query(None, gt=0, le=200),
              company_id: int | None = Query(None, ge=1),
              query: str | None = Query(None, max_length=160),
              place: str | None = Query(None, max_length=120),
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
              location_scope: Literal['area', 'remote', 'area_remote'] | None = None,
              work_style: Literal['all','remote','hybrid','onsite'] = 'all',
              sort: Literal['relevance','match','newest','posted','discovered','title','company'] = 'relevance',
              session: Session = Depends(get_session),
              profile_id: Annotated[int | None, Query(ge=1)] = None,
              profile_ids: Annotated[str | None, Query(max_length=1200)] = None,
              snapshot_id: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
              min_match_score: Annotated[int | None, Query(ge=0, le=100)] = None,
              include_unknown: bool | None = None,
              job_status: ApplicationStatus | None = None,
              posting_status: Literal['open', 'closed', 'all'] = 'open',
              country_code: Annotated[str | None, Query(pattern=r'^[A-Za-z]{2}$')] = None,
              state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = search_snapshots.canonical_state(state)
    employer_country = _canonical_country(country)
    selected_ids: list[int] = []
    if profile_ids is not None:
        try:
            selected_ids = [int(value) for value in profile_ids.split(',') if value]
        except ValueError as error:
            raise HTTPException(status_code=422, detail='profile_ids must be a comma-separated list of IDs') from error
    elif profile_id is not None:
        selected_ids = [profile_id]
    selected_ids = list(dict.fromkeys(selected_ids))
    if len(selected_ids) > 100 or any(value < 1 for value in selected_ids):
        raise HTTPException(status_code=422, detail='Select between 1 and 100 valid profiles')
    if profile_id is None and selected_ids:
        profile_id = selected_ids[0]
    tracking_profile = require_profile(profile_id, session) if profile_id is not None else None
    # Search context uses one profile only for its saved location and application
    # tracking. Browse retrieval and ranking never need the profile match index.
    profiles: list[UserProfile] = []
    if job_status is not None and tracking_profile is None:
        raise HTTPException(status_code=422, detail='Select a profile before filtering by application status')
    profile = tracking_profile
    # A state/country selector is its own saved search area. Keep a selected
    # profile for application tracking, but do not let its radius replace that
    # configured scope (or prevent its profileless snapshot from being reused).
    area_profile = None if state or employer_country else profile
    resolved = _resolved_search_area(area_profile, latitude, longitude, radius_km, place, country_code)
    latitude, longitude, radius_km = (resolved[key] for key in ('latitude', 'longitude', 'radius_km'))
    place, country = resolved['place'], resolved['country']
    location_scope = location_scope or ('remote' if work_style == 'remote' else 'area_remote')
    scope_country = country if resolved['profile_area'] or isinstance(country_code, str) else None
    search_sort = {'relevance': 'match', 'newest': 'posted'}.get(sort, sort)
    thresholds: dict[int | None, tuple[int, bool]] = {}
    thresholds[None] = (0, True)
    normalized_query = ' '.join(query.split()) if query else None
    vector = _search_query_embedding(session, normalized_query, 'job')
    if vector is not None:
        semantic_page = search_snapshots.semantic_jobs_page(
            session, vector,
            latitude=latitude, longitude=longitude, radius_km=radius_km,
            place=place, country=scope_country, location_scope=location_scope,
            work_style=work_style, company_id=company_id,
            state=state, employer_country=employer_country, profile=tracking_profile,
            offset=offset, limit=limit, job_status=job_status, posting_status=posting_status,
        )
        if semantic_page["semantic_candidates"]:
            semantic_page.pop("semantic_candidates", None)
            return semantic_page
    # Browse snapshots are profileless; profile matching is handled separately
    # and cannot delay or filter ordinary search.
    generation = search_snapshots.search_generation(session)
    criteria = {
        'matching_version': MATCHING_VERSION,
        'geography_version': search_snapshots.WORK_LOCATION_GEOGRAPHY_VERSION,
        # Expiry is time based, so rotate cache keys every 15 minutes even if
        # no crawler has changed the underlying row generation.
        'freshness_bucket': int(utcnow().timestamp() // search_snapshots.SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS),
        'company_id': company_id,
        'query': normalized_query,
        'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
        'place': place, 'country': scope_country, 'location_scope': location_scope,
        'work_style': work_style, 'sort': search_sort, 'state': state,
        'employer_country': employer_country,
        'profiles': [],
    }
    # Ordering is applied from persisted sort columns at read time, so changing
    # sort does not require rebuilding the scoped candidate snapshot.
    if posting_status != 'open':
        criteria['posting_status'] = posting_status
    snapshot_criteria = {name: value for name, value in criteria.items() if name != 'sort'}
    snapshot = None
    snapshot_stale = False
    if snapshot_id is not None:
        snapshot = session.get(JobSearchSnapshot, snapshot_id)
        if snapshot is None or not search_snapshots.search_criteria_match(
                (snapshot.payload or {}).get('criteria'), snapshot_criteria):
            raise HTTPException(status_code=409, detail='Search snapshot expired or criteria changed; restart from page one')
        current_key = search_snapshots.search_key(generation, snapshot_criteria)
        if snapshot.generation != generation or snapshot.key != current_key:
            current_snapshot = session.get(JobSearchSnapshot, current_key)
            if current_snapshot is not None:
                snapshot = current_snapshot
            else:
                latest_snapshot = search_snapshots.latest_stale_snapshot(session, generation, snapshot_criteria)
                if latest_snapshot is None:
                    raise HTTPException(status_code=409, detail='Search snapshot expired or criteria changed; restart from page one')
                snapshot = latest_snapshot
                snapshot_stale = True
                search_snapshots.enqueue_search_snapshot_refresh(session, snapshot_criteria)
        key = snapshot.key
        generation = snapshot.generation
    else:
        key = search_snapshots.search_key(generation, snapshot_criteria)
        snapshot = session.get(JobSearchSnapshot, key)
    if snapshot is None:
        latest_snapshot = search_snapshots.latest_stale_snapshot(session, generation, snapshot_criteria)
        if latest_snapshot is not None:
            snapshot = latest_snapshot
            key = snapshot.key
            generation = snapshot.generation
            snapshot_stale = True
            search_snapshots.enqueue_search_snapshot_refresh(session, snapshot_criteria)
        else:
            snapshot = search_snapshots.materialize_search_snapshot(
                session, profiles, key, generation, snapshot_criteria, thresholds,
            ) if IS_SQLITE else None
            if snapshot is None:
                search_snapshots.enqueue_search_snapshot_refresh(session, snapshot_criteria)
                return {
                    "items": [], "total": 0, "offset": offset, "limit": limit,
                    "profile_id": tracking_profile.id if tracking_profile else None,
                    "profile_ids": [tracking_profile.id] if tracking_profile else [],
                    "application_counts": dict.fromkeys(APPLICATION_STATUSES, 0),
                    "location": {"latitude": latitude, "longitude": longitude,
                                 "radius_km": radius_km, "city": place,
                                 "country_code": scope_country},
                    "location_scope": location_scope, "state": state,
                    "country": employer_country, "search_mode": "warming",
                    "snapshot_stale": True,
                }
    result_count = select(func.count()).select_from(JobSearchResult).join(
        Job, Job.id == JobSearchResult.job_id).where(
        JobSearchResult.snapshot_key == key, search_snapshots.posting_status_filter(posting_status))
    result_rows = select(JobSearchResult, Job).join(Job, Job.id == JobSearchResult.job_id).where(
        JobSearchResult.snapshot_key == key, search_snapshots.posting_status_filter(posting_status))
    total = session.scalar(result_count) or 0
    result_order = {
        'posted': (JobSearchResult.posted_rank.desc(), JobSearchResult.discovered_rank.desc(),
                   JobSearchResult.title_sort, JobSearchResult.job_id),
        'discovered': (JobSearchResult.discovered_rank.desc(), JobSearchResult.posted_rank.desc(),
                       JobSearchResult.title_sort, JobSearchResult.job_id),
        'title': (JobSearchResult.title_sort, JobSearchResult.company_sort, JobSearchResult.job_id),
        'company': (JobSearchResult.company_sort, JobSearchResult.title_sort, JobSearchResult.job_id),
        'remote': (JobSearchResult.remote_sort, JobSearchResult.title_sort, JobSearchResult.job_id),
    }.get(search_sort, (JobSearchResult.discovered_rank.desc(),
                        JobSearchResult.posted_rank.desc(), JobSearchResult.title_sort,
                        JobSearchResult.job_id))
    result_options = result_rows.options(
        joinedload(Job.feed).joinedload(JobFeed.company),
        joinedload(Job.employer_company), selectinload(Job.locations),
    ).order_by(*result_order)
    application_counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    applications_by_job = {}
    if tracking_profile is not None and job_status is not None:
        # Status filtering needs identity-aware state for unindexed legacy
        # copies. Scan the cached result set in bounded pages before slicing.
        selected_results = []
        seen_groups = set()
        seen_matching_groups = set()
        matched_count = 0
        cursor = 0
        batch_size = 400
        while cursor < total:
            rows = session.execute(result_options.offset(cursor).limit(batch_size)).unique().all()
            if not rows:
                break
            batch_jobs = [row[1] for row in rows]
            states_by_job = canonical_application_states_for_jobs(
                session, tracking_profile.id, batch_jobs)
            for result_row, job in rows:
                application = states_by_job.get(job.id)
                if application is None:
                    continue
                group_key = application.vacancy_group_id or application.job_id
                if group_key not in seen_groups:
                    seen_groups.add(group_key)
                    application_counts[application.status] += 1
                if application.status != job_status or group_key in seen_matching_groups:
                    continue
                seen_matching_groups.add(group_key)
                if matched_count >= offset and len(selected_results) < limit:
                    selected_results.append((result_row, job, None))
                    applications_by_job[job.id] = application
                matched_count += 1
            cursor += len(rows)
        total = matched_count
    else:
        selected_rows = session.execute(result_options.offset(offset).limit(limit)).unique().all()
        selected_results = [(row[0], row[1], None) for row in selected_rows]
        applications_by_job = (canonical_application_states_for_jobs(
            session, tracking_profile.id, (job for _, job, _ in selected_results))
            if tracking_profile else {})
    items = []
    for result_row, job, match_row in selected_results:
        item = search_snapshots.job_json(job)
        item['match_kind'] = result_row.scope.get('match_kind')
        item['geography'] = result_row.scope
        if tracking_profile is not None:
            item['profile_id'] = tracking_profile.id
            item['profile_name'] = tracking_profile.name
            application = applications_by_job.get(job.id)
            item['application'] = application_json(application) if application else None
        items.append(item)
    if tracking_profile and job_status is None:
        application_job = aliased(Job)
        application_group = aliased(JobVacancyGroup)
        candidate_group = aliased(JobVacancyGroup)
        same_application_group = select(application_group.job_id).where(
            application_group.vacancy_group_id == candidate_group.vacancy_group_id)
        application_match = and_(
            JobApplication.profile_id == tracking_profile.id,
            JobApplication.job_id.in_(select(application_job.id).where(
                or_(application_job.id == JobSearchResult.job_id,
                    and_(candidate_group.vacancy_group_id.is_not(None),
                         application_job.id.in_(same_application_group))))),
        )
        count_statement = select(JobApplication.status, func.count()).select_from(
            JobSearchResult).join(Job, Job.id == JobSearchResult.job_id).outerjoin(
                candidate_group, candidate_group.job_id == Job.id).join(
                JobApplication, application_match).where(
                    JobSearchResult.snapshot_key == key, search_snapshots.posting_status_filter(posting_status))
        counts = session.execute(count_statement.group_by(JobApplication.status)).all()
        application_counts.update(dict(counts))
    payload = snapshot.payload or {}
    return {**payload, 'items': items, 'total': total, 'offset': offset, 'limit': limit,
            'profile_id': tracking_profile.id if tracking_profile else None,
            'profile_ids': [tracking_profile.id] if tracking_profile else [],
            'application_counts': application_counts,
            'search_mode': 'keyword' if normalized_query else 'browse',
            'snapshot_id': key, 'snapshot_stale': snapshot_stale}


def _application_momentum_json(session: Session) -> dict[str, int]:
    now = utcnow()
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    weekly_jobs: dict[datetime, set[int]] = {}
    lifetime_jobs: set[int] = set()
    logical_job_id = func.coalesce(JobVacancyGroup.vacancy_group_id, Job.id)
    applied_events = session.execute(select(
        logical_job_id,
        ApplicationMomentumEvent.occurred_at,
        ApplicationMomentumEvent.is_legacy,
    ).select_from(ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id)
        .outerjoin(JobVacancyGroup, JobVacancyGroup.job_id == Job.id)
        .where(ApplicationMomentumEvent.event_type == 'applied')
        .order_by(ApplicationMomentumEvent.occurred_at)).all()
    counted_weekly_jobs: set[int] = set()
    for job_id, occurred_at, is_legacy in applied_events:
        lifetime_jobs.add(job_id)
        if is_legacy or job_id in counted_weekly_jobs:
            continue
        counted_weekly_jobs.add(job_id)
        occurred_at = occurred_at.replace(tzinfo=timezone.utc) if occurred_at.tzinfo is None else occurred_at.astimezone(timezone.utc)
        event_week = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=occurred_at.weekday())
        weekly_jobs.setdefault(event_week, set()).add(job_id)

    weekly_goal = APPLICATION_WEEKLY_GOAL
    applied_this_week = len(weekly_jobs.get(week_start, set()))
    successful_weeks = 0
    streak_week = week_start
    if len(weekly_jobs.get(streak_week, set())) < weekly_goal:
        streak_week -= timedelta(days=7)
    while len(weekly_jobs.get(streak_week, set())) >= weekly_goal:
        successful_weeks += 1
        streak_week -= timedelta(days=7)

    interview_count = session.scalar(select(func.count(func.distinct(logical_job_id))).select_from(
        ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id).outerjoin(
            JobVacancyGroup, JobVacancyGroup.job_id == Job.id).where(
            ApplicationMomentumEvent.event_type == 'interview',
        )) or 0
    jobs_won = session.scalar(select(func.count(func.distinct(logical_job_id))).select_from(
        ApplicationMomentumEvent).join(Job, Job.id == ApplicationMomentumEvent.job_id).outerjoin(
            JobVacancyGroup, JobVacancyGroup.job_id == Job.id).where(
            ApplicationMomentumEvent.event_type == 'accepted',
        )) or 0
    return {
        'weekly_goal': weekly_goal,
        'applied_this_week': applied_this_week,
        'lifetime_applied': len(lifetime_jobs),
        'successful_weeks': successful_weeks,
        'interview_count': interview_count,
        'jobs_won': jobs_won,
    }


@app.get('/api/v1/application-momentum')
def get_application_momentum(session: Session = Depends(get_session)) -> dict[str, int]:
    return _application_momentum_json(session)


@app.put('/api/v1/profiles/{profile_id}/applications/{job_id}')
def set_job_application(profile_id: int, job_id: int, request: ApplicationRequest,
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    require_profile(profile_id, session)
    if session.get_bind().dialect.name == 'sqlite':
        # End the read transaction before SQLite's no-op inventory write lock.
        session.commit()
    # Index in its own transaction before taking row locks, then release the
    # identity union guard before taking the profile lock used by publishers.
    index_vacancy_identities(session, [job_id])
    session.commit()
    # Serialize tracking with source closure before reading the posting state.
    # SQLite needs a write to acquire the same protection as FOR UPDATE.
    if session.get_bind().dialect.name == 'sqlite':
        session.execute(update(Job).where(Job.id == job_id).values(
            is_active=Job.is_active).execution_options(synchronize_session=False))
    # Only application state changes; NO KEY UPDATE serializes source closure
    # and allows a concurrent match-index foreign-key check to finish.
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update(key_share=True)
                         .execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(status_code=404, detail='Job not found')
    locked_profile_id = session.scalar(select(UserProfile.id).where(
        UserProfile.id == profile_id).with_for_update())
    if locked_profile_id is None:
        raise HTTPException(status_code=404, detail='Profile not found')
    canonical_application = canonical_application_states_for_jobs(session, profile_id, [job]).get(job.id)
    previous_status = canonical_application.status if canonical_application else None
    requested_status = request.status
    # Opening a retained source copy can submit its default New state. Keep an
    # existing decision unless the request explicitly undoes it.
    if (canonical_application is not None and canonical_application.job_id != job.id
            and request.status == 'new' and request.action != 'undo'
            and previous_status != 'new'):
        session.commit()
        return application_json(canonical_application)
    target_job_id = canonical_application.job_id if canonical_application else job.id
    if not job.is_active:
        if request.status == 'new' and previous_status in ('accepted', 'rejected', 'closed'):
            target_status = previous_status
        elif request.status in ('accepted', 'rejected'):
            target_status = request.status
        else:
            target_status = 'rejected' if request.status in APPLIED_STATUSES else 'closed'
        request = request.model_copy(update={'status': target_status})
    # Atomic upsert makes repeat saves and simultaneous requests safe on both stores.
    insert = dialect_insert(session)
    now = utcnow()
    statement = insert(JobApplication).values(profile_id=profile_id, job_id=target_job_id,
        status=request.status, previous_status=None, auto_created=False,
        created_at=now, updated_at=now).on_conflict_do_update(
            index_elements=['profile_id', 'job_id'], set_={
                'status': request.status,
                'auto_created': False,
                'previous_status': case((JobApplication.status != request.status, JobApplication.status),
                                        else_=JobApplication.previous_status),
                'updated_at': now,
            })
    session.execute(statement)
    momentum_event_type = {
        'waiting_for_reply': 'applied',
        'automated_application': 'applied',
        'interview': 'interview',
        'accepted': 'accepted',
    }.get(requested_status)
    if request.action == 'stage' and momentum_event_type and previous_status != requested_status:
        already_counted = session.scalar(select(ApplicationMomentumEvent.id).where(
            ApplicationMomentumEvent.profile_id == profile_id,
            or_(ApplicationMomentumEvent.job_id == job.id,
                and_(job.vacancy_group_id is not None, ApplicationMomentumEvent.job_id.in_(
                    select(JobVacancyGroup.job_id).where(
                        JobVacancyGroup.vacancy_group_id == job.vacancy_group_id)))),
            ApplicationMomentumEvent.event_type == momentum_event_type,
        ).limit(1)) is not None
        if not already_counted:
            session.add(ApplicationMomentumEvent(
                profile_id=profile_id,
                job_id=target_job_id,
                event_type=momentum_event_type,
                occurred_at=now,
            ))
    session.commit()
    application = session.scalar(select(JobApplication).where(
        JobApplication.profile_id == profile_id, JobApplication.job_id == target_job_id)
        .execution_options(populate_existing=True))
    return application_json(application)


@app.get('/api/v1/profiles/{profile_id}/applications')
def list_job_applications(profile_id: int,
                          status: ApplicationStatus | None = None,
                          offset: Annotated[int, Query(ge=0)] = 0,
                          limit: Annotated[int, Query(ge=1, le=100)] = 40,
                          session: Session = Depends(get_session)) -> dict[str, Any]:
    require_profile(profile_id, session)
    canonical_states = canonical_application_states(session, [profile_id])
    counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    for application in canonical_states:
        counts[application.status] += 1
    applications = sorted((application for application in canonical_states
                           if status is None or application.status == status),
                          key=lambda row: (row.updated_rank, row.id), reverse=True)[offset:offset + limit]
    jobs = session.scalars(select(Job).where(Job.id.in_({row.job_id for row in applications})).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations))).unique().all()
    jobs_by_id = {job.id: job for job in jobs}
    items = []
    for application in applications:
        item = search_snapshots.job_json(jobs_by_id[application.job_id])
        item['application'] = application_json(application)
        items.append(item)
    return {'profile_id': profile_id, 'items': items, 'counts': counts,
            'total': counts[status] if status is not None else sum(counts.values()),
            'offset': offset, 'limit': limit}


@app.get('/api/v1/applications')
def list_applications(profile_ids: str = Query(..., max_length=1200),
                      statuses: str | None = Query(None, max_length=160),
                      sort: Literal['match', 'posted', 'discovered'] = 'match',
                      offset: Annotated[int, Query(ge=0)] = 0,
                      limit: Annotated[int, Query(ge=1, le=100)] = 40,
                      session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        selected_ids = list(dict.fromkeys(int(value) for value in profile_ids.split(',') if value))
    except ValueError as error:
        raise HTTPException(status_code=422, detail='profile_ids must be a comma-separated list of IDs') from error
    if not selected_ids or len(selected_ids) > 100 or any(value < 1 for value in selected_ids):
        raise HTTPException(status_code=422, detail='Select between 1 and 100 valid profiles')
    profiles = [require_profile(value, session) for value in selected_ids]
    selected_statuses: list[str] = []
    if statuses and statuses != 'all':
        selected_statuses = list(dict.fromkeys(value for value in statuses.split(',') if value))
        if not selected_statuses or any(value not in APPLICATION_STATUSES for value in selected_statuses):
            raise HTTPException(status_code=422, detail='statuses contains an unknown application state')

    # Production reads must not scan the entire job inventory. A dedicated
    # worker backfills stale profiles while saved application stages stay usable.
    # Keep the small SQLite development flow self-contained, like browse search.
    rebuild_interrupted = False
    if IS_SQLITE:
        try:
            if ensure_profile_match_indexes(session, profiles):
                session.commit()
        except ProfileIndexChanged:
            # Discard the interrupted snapshot and keep saved stages usable.
            # The client's pending-state refresh will try the backfill again.
            session.rollback()
            profiles = [require_profile(value, session) for value in selected_ids]
            rebuild_interrupted = True
    from hiring_scraper.app.models import MatchDirtyJob
    matching_pending = (rebuild_interrupted or any(profile_match_index_stale(profile) for profile in profiles)
                        or (not IS_SQLITE and session.scalar(select(MatchDirtyJob.job_id).limit(1)) is not None))

    canonical_states = canonical_application_states(session, selected_ids)
    counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    for application in canonical_states:
        counts[application.status] += 1
    applications = [application for application in canonical_states
                    if not selected_statuses or application.status in selected_statuses]
    total = len(applications)
    # Stored ranks remain usable while bounded worker pages rebuild them.
    # Matching card details below is limited to the requested page.
    order = {
        'posted': lambda row: (row.posted_rank, row.match_rank, row.discovered_rank, row.id),
        'discovered': lambda row: (row.discovered_rank, row.match_rank, row.posted_rank, row.id),
        'match': lambda row: (row.match_rank, row.discovered_rank, row.posted_rank, row.id),
    }[sort]
    applications = sorted(applications, key=order, reverse=True)[offset:offset + limit]
    # Bind only this page's stable job IDs, regardless of the full Journey size.
    jobs = session.scalars(select(Job).where(Job.id.in_({row.job_id for row in applications})).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations))).unique().all()
    jobs_by_id = {job.id: job for job in jobs}
    profiles_by_id = {profile.id: profile for profile in profiles}
    from hiring_scraper.app.profile_llm import load_matching_documents as load_semantic_documents
    semantic_documents = load_semantic_documents(session, jobs, profiles)
    items = []
    for application in applications:
        job = jobs_by_id[application.job_id]
        item = search_snapshots.job_json(job)
        item['application'] = application_json(application)
        item['profile_id'] = application.profile_id
        item['profile_name'] = profiles_by_id[application.profile_id].name
        item['profile_match'] = search_snapshots.profile_match(job, profiles_by_id[application.profile_id], None, semantic_documents)
        items.append(item)
    return {'profile_ids': selected_ids, 'items': items, 'counts': counts,
            'total': total,
            'offset': offset, 'limit': limit, 'sort': sort, 'matching_pending': matching_pending}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session),
            profile_id: Annotated[int | None, Query(ge=1)] = None,
            latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
            longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
            radius_km: Annotated[float | None, Query(gt=0, le=200)] = None,
            place: Annotated[str | None, Query(max_length=120)] = None,
            country_code: Annotated[str | None, Query(pattern=r'^[A-Za-z]{2}$')] = None,
            state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = search_snapshots.canonical_state(state)
    employer_country = _canonical_country(country)
    statement = select(Job).options(joinedload(Job.feed).joinedload(JobFeed.company),
                                    joinedload(Job.employer_company),
                                    selectinload(Job.locations)).where(Job.id == job_id)
    job = session.scalars(statement).unique().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = search_snapshots.job_json(job, include_description=True)
    if employer_country and profile_id is None:
        data['geography'] = search_snapshots.result_scope(job, 0, 0, 15, None, None, state, employer_country)
    if profile_id is not None:
        profile = require_profile(profile_id, session)
        resolved = _resolved_search_area(profile, latitude, longitude, radius_km, place, country_code)
        scope = search_snapshots.result_scope(job, resolved['latitude'], resolved['longitude'],
                                 resolved['radius_km'], resolved['place'],
                                 resolved['country'] if resolved['profile_area'] or isinstance(country_code, str) else None, state, employer_country) if state or employer_country or resolved['profile_area'] or resolved['explicit'] else None
        data['geography'] = scope
        data['profile_match'] = search_snapshots.profile_match(job, profile, scope)
        application = canonical_application_states_for_jobs(session, profile.id, [job]).get(job.id)
        data['application'] = application_json(application) if application else None
    return data


@app.get("/api/v1/companies/{company_id}")
def get_company(company_id: int,
                session: Session = Depends(get_session),
                profile_id: Annotated[int | None, Query(ge=1)] = None) -> dict[str, Any]:
    profile = require_profile(profile_id, session) if profile_id is not None else None
    company = session.scalars(select(Company).options(selectinload(Company.feeds))
                              .where(Company.id == company_id)).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    source_jobs = session.scalars(select(Job).join(Job.feed).where(or_(
        JobFeed.company_id == company_id, Job.employer_company_id == company_id)).options(
            joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
            selectinload(Job.locations))).unique().all()
    all_jobs = deduplicate_jobs(source_jobs)
    active_jobs = deduplicate_jobs([job for job in all_jobs if job.is_active])
    data = _company_json(company, float("inf"), len(active_jobs))
    data["feeds"] = [_feed_json(feed) for feed in company.feeds]
    job_data = [search_snapshots.job_json(job) for job in all_jobs]
    if profile is not None and job_data:
        applications = {job_id: application_json(state) for job_id, state in
                        canonical_application_states_for_jobs(session, profile.id, all_jobs).items()
                        if state is not None}
        for item in job_data:
            item["application"] = applications.get(item["id"])
    data["jobs"] = job_data
    return data


@app.patch("/api/v1/companies/{company_id}")
def update_company(company_id: int, request: CompanyUpdate,
                   session: Session = Depends(get_session)) -> dict[str, Any]:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if "domain" not in request.model_fields_set:
        raise HTTPException(status_code=422, detail="Provide a company domain to save")
    domain = _managed_company_domain(request.domain)
    company.domain = domain
    company.domain_match_method = "manual" if domain else None
    company.domain_evidence_url = None
    session.commit()
    return {"id": company.id, "domain": company.domain,
            "domain_match_method": company.domain_match_method}


def _managed_feed_json(feed: JobFeed) -> dict[str, Any]:
    return {**_feed_json(feed), "company_id": feed.company_id,
            "company_name": feed.company.name, "company_website": feed.company.website_url,
            "company_career_url": feed.company.career_url, "company_source": feed.company.source}


@app.get("/api/v1/feeds")
def list_feeds(session: Session = Depends(get_session)) -> dict[str, Any]:
    feeds = session.scalars(select(JobFeed).options(joinedload(JobFeed.company)).where(
        JobFeed.provider != "manual_job").order_by(
            Company.name.asc(), JobFeed.id.asc()).join(JobFeed.company)).all()
    return {"items": [_managed_feed_json(feed) for feed in feeds], "total": len(feeds)}


@app.get("/api/v1/feeds/special")
def list_special_feeds(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return enabled managed Agentur für Arbeit feeds for the Locations view."""
    managed_marker = or_(
        Company.source == "managed_feed",
        JobFeed.source_config["managed_adapter"].as_boolean().is_(True),
        JobFeed.source_config["location_filter"].as_string().is_not(None),
    )
    feeds = session.scalars(select(JobFeed).options(joinedload(JobFeed.company)).where(
        JobFeed.provider == "arbeitsagentur", JobFeed.enabled.is_(True), managed_marker).order_by(
            Company.name.asc(), JobFeed.id.asc()).join(JobFeed.company)).all()
    items = [_managed_feed_json(feed) for feed in feeds if _is_managed_arbeitsagentur_feed(feed)]
    return {"items": items, "total": len(items)}


@app.post("/api/v1/feeds", status_code=201)
def create_managed_feed(request: ManagedFeedCreate,
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    insert = dialect_insert(session)
    source_url = _managed_page_url(request.feed_url)
    source_config = _managed_feed_config(session, request, request.provider)
    source = _managed_feed_source(source_url, request.provider, source_config)
    website_url = _managed_page_url(request.website_url, "Company website") if request.website_url else None
    track_company = getattr(request, "track_company", False)
    if website_url and is_hosted_job_site(website_url):
        raise HTTPException(status_code=422, detail="Enter the employer's own website, rather than its hosted job board")
    if not website_url and not is_hosted_job_site(source_url):
        parts = urlsplit(source_url)
        website_url = f"{parts.scheme}://{parts.netloc}/"
    company = session.get(Company, request.company_id) if request.company_id else None
    if request.company_id and company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if company is None and website_url:
        company = find_company_by_website(session, website_url)
    if company is None and not request.company_id:
        # The canonical ATS feed URL includes the tenant; its shared host alone
        # must never combine different employers.
        source_company = session.scalar(select(Company).join(JobFeed).where(
            JobFeed.feed_url == source["feed_url"]).order_by(Company.id).limit(1))
        if source_company is not None and (not request.website_url or not source_company.domain):
            company = source_company
    company_created = company is None
    if company is None:
        company_name = (request.company_name or "").strip()
        if len(company_name) < 2:
            if request.provider == "arbeitsagentur":
                company_name = "Agentur für Arbeit"
            else:
                company_name = source.get("tenant") or company_domain(website_url or source["board_url"])
        domain = company_domain(website_url) if website_url else None
        identity = f"domain:{domain}" if domain else f"feed:{source['feed_url']}"
        source_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        company = session.scalar(select(Company).where(
            Company.source == "managed_feed", Company.source_id == source_id))
        company_created = company is None
        if company is None:
            company_values = dict(source="managed_feed", source_id=source_id,
                              name=company_name, website_url=website_url,
                              domain=domain, category="manual_company" if track_company else "job_board_source",
                              source_url=source["board_url"], career_url=source["board_url"],
                              career_status="jobs_feed_found",
                              next_discovery_at=utcnow() if track_company and website_url else None)
            company_id = session.scalar(insert(Company).values(**company_values).on_conflict_do_nothing(
                index_elements=['source', 'source_id']).returning(Company.id))
            company_created = company_id is not None
            company = (session.get(Company, company_id) if company_created else
                       session.scalar(select(Company).where(
                           Company.source == 'managed_feed', Company.source_id == source_id)))
    if website_url:
        if not company.website_url:
            company.website_url = website_url
        if not company.domain and company.domain_match_method != "manual":
            company.domain = company_domain(website_url)
    if track_company:
        if company.source == "extension_job" and company.category == "job_posting":
            company.crawl_paused = False
            company.category = "manual_company"
        if company.category == "job_board_source" and website_url:
            company.category = "manual_company"
        if not company.career_url_manual:
            company.career_url = source["board_url"]
            company.career_status = "jobs_feed_found"
        if company.website_url and not company.crawl_paused:
            company.next_discovery_at = utcnow()
    duplicate = session.scalar(select(JobFeed).where(
        JobFeed.company_id == company.id, JobFeed.provider == source["provider"],
        JobFeed.feed_url == source["feed_url"]))
    if duplicate:
        if track_company:
            session.commit()
            return {**_managed_feed_json(duplicate), "company_created": False, "already_tracked": True}
        raise HTTPException(status_code=409, detail="This page is already managed for this company")
    now = utcnow()
    feed_values = dict(company_id=company.id, provider=source["provider"], tenant=source["tenant"],
                   source_config=source_config, board_url=source["board_url"], feed_url=source["feed_url"], status="pending",
                   enabled=True, job_count=0,
                   next_scan_at=now if source["provider"] != "arbeitsagentur" else None)
    feed_id = session.scalar(insert(JobFeed).values(**feed_values).on_conflict_do_nothing(
        index_elements=['company_id', 'provider', 'feed_url']).returning(JobFeed.id))
    already_tracked = feed_id is None
    if already_tracked and not track_company:
        raise HTTPException(status_code=409, detail="This page is already managed for this company")
    feed = (session.get(JobFeed, feed_id) if feed_id is not None else
            session.scalar(select(JobFeed).where(JobFeed.company_id == company.id,
                JobFeed.provider == source['provider'], JobFeed.feed_url == source['feed_url'])))
    if source["provider"] == "arbeitsagentur" and not already_tracked:
        _schedule_managed_arbeitsagentur_feed(session, feed, now)
    session.commit()
    session.refresh(feed)
    return {**_managed_feed_json(feed), "company_created": company_created, "already_tracked": already_tracked}


@app.patch("/api/v1/feeds/{feed_id}")
def update_managed_feed(feed_id: int, request: ManagedFeedUpdate,
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    # Prepare URLs and geocoding before taking source row locks. Recheck the
    # revision after locking so an edit cannot overwrite a concurrent edit.
    feed = session.scalars(select(JobFeed).options(joinedload(JobFeed.company)).where(
        JobFeed.id == feed_id)).first()
    if feed is None:
        raise HTTPException(status_code=404, detail="Job feed not found")
    if (request.enabled is True and feed.provider == "arbeitsagentur" and
            not _is_managed_arbeitsagentur_feed(feed)):
        raise HTTPException(status_code=409, detail="This is an imported snapshot. Add a regional adapter from Job feeds to schedule searches.")
    changed_fields = getattr(request, "model_fields_set", None)
    if changed_fields is None:
        changed_fields = (set(request) if isinstance(request, dict) else {
            name for name in ManagedFeedUpdate.model_fields
            if getattr(request, name, None) is not None
        })
    career_page_supplied = "career_page_url" in changed_fields
    new_career_page_url = (_managed_page_url(request.career_page_url, "Career page")
                           if career_page_supplied and request.career_page_url else None)
    if (request.feed_url is not None or request.provider is not None or
            {"location_query", "radius_km", "terms"} & changed_fields) and feed.provider not in MANAGED_FEED_PROVIDERS:
        raise HTTPException(status_code=409, detail="Only manually managed public feeds can change their page or scraper type")
    now = utcnow()
    source_edit = (request.feed_url is not None or request.provider is not None or
                   bool({"location_query", "radius_km", "terms"} & changed_fields))
    current_selector = "ats" if feed.provider in ATS_FEED_PROVIDERS else feed.provider
    target_provider = request.provider or current_selector
    if source_edit:
        source_config = _managed_feed_config(session, request, target_provider, feed.source_config or {})
        source_url = request.feed_url or (feed.board_url if current_selector in {"ats", "arbeitsagentur"} else feed.feed_url)
        source_url = _managed_page_url(source_url)
        source = _managed_feed_source(source_url, target_provider, source_config)
    else:
        source = {"provider": feed.provider, "feed_url": feed.feed_url,
                  "board_url": feed.board_url or feed.feed_url, "tenant": feed.tenant}
        source_config = feed.source_config or {}
    prepared_revision = feed.source_revision
    company_id = feed.company_id
    # Persist any location cache entry and release the preparation connection.
    session.commit()
    if session.get_bind().dialect.name == 'postgresql':
        session.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
    locked = session.execute(update(JobFeed).where(JobFeed.id == feed_id).values(
        source_revision=JobFeed.source_revision).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise HTTPException(status_code=404, detail="Job feed not found")
    feed = session.scalar(select(JobFeed).options(joinedload(JobFeed.company)).where(
        JobFeed.id == feed_id).execution_options(populate_existing=True))
    if feed.source_revision != prepared_revision or feed.company_id != company_id:
        raise HTTPException(status_code=409, detail="Job feed changed during this edit; reload and try again")
    new_url = source["feed_url"]
    new_provider = source["provider"]
    new_board_url = source["board_url"]
    new_tenant = source["tenant"]
    new_source_config = source_config
    if (new_url != feed.feed_url or new_provider != feed.provider or
            new_board_url != feed.board_url or new_tenant != feed.tenant or
            new_source_config != (feed.source_config or {})):
        duplicate = session.scalar(select(JobFeed).where(
            JobFeed.company_id == feed.company_id, JobFeed.provider == new_provider,
            JobFeed.feed_url == new_url, JobFeed.id != feed.id))
        if duplicate:
            raise HTTPException(status_code=409, detail="This page is already managed for this company")
    changed_source = (new_url != feed.feed_url or new_provider != feed.provider or
                      new_board_url != feed.board_url or new_tenant != feed.tenant or
                      new_source_config != (feed.source_config or {}))
    changed_enabled = request.enabled is not None and request.enabled != feed.enabled
    update_career_page = (career_page_supplied and
                          (new_career_page_url != feed.company.career_url or
                           feed.company.career_url_manual != bool(new_career_page_url)))
    if update_career_page:
        feed.company.career_url = new_career_page_url
        feed.company.career_url_manual = bool(new_career_page_url)
        feed.company.career_status = "career_page_found" if new_career_page_url else "unresolved"
        if not feed.company.crawl_paused:
            feed.company.next_discovery_at = now
    if changed_source:
        # A source is an identity boundary: keep its URL/provider and job
        # history together, and start a fresh feed row for the replacement.
        replacement_enabled = feed.enabled if request.enabled is None else request.enabled
        feed.source_revision = (feed.source_revision or 1) + 1
        feed.enabled = False
        feed.status = "source_replaced"
        feed.next_scan_at = None
        feed.lease_until = None
        # These updates do not change job keys. NO KEY UPDATE remains compatible
        # with the KEY SHARE locks a concurrent match-index insert takes for its
        # job foreign key, avoiding a job/profile lock cycle during retirement.
        retiring_jobs = session.scalars(select(Job).where(
            Job.feed_id == feed.id, Job.is_active.is_(True)).order_by(Job.id)
            .with_for_update(key_share=True).execution_options(populate_existing=True)).all()
        for job in retiring_jobs:
            job.is_active = False
            job.closed_at = now
        session.flush()
        if retiring_jobs:
            retiring_job_ids = [job.id for job in retiring_jobs]
            refresh_application_matches_for_jobs(
                session, retiring_job_ids,
                before_publish=lambda publish_session: close_job_applications(
                    publish_session, retiring_job_ids, now),
            )
        if (feed.company.source == "managed_feed" and not career_page_supplied and
                not feed.company.career_url_manual):
            feed.company.career_url = new_board_url
            feed.company.source_url = new_board_url
        replacement_search_scopes = []
        if new_provider == "arbeitsagentur":
            pending_scopes = feed.pending_search_scopes if isinstance(feed.pending_search_scopes, list) else []
            for scope in pending_scopes:
                if isinstance(scope, dict) and scope not in replacement_search_scopes:
                    replacement_search_scopes.append(scope)
            running_scopes = session.scalars(select(ScanRun.search_scope).where(
                ScanRun.feed_id == feed.id, ScanRun.status == "running",
                ScanRun.search_scope.is_not(None))).all()
            for scope in running_scopes:
                if not isinstance(scope, dict):
                    continue
                discovery_job_id = scope.get("discovery_job_id")
                already_queued = any(
                    existing == scope or (discovery_job_id is not None and
                                          existing.get("discovery_job_id") == discovery_job_id)
                    for existing in replacement_search_scopes)
                if not already_queued:
                    replacement_search_scopes.append(scope)
        replacement = JobFeed(
            company_id=feed.company_id, provider=new_provider, tenant=new_tenant,
            source_config=new_source_config, board_url=new_board_url, feed_url=new_url,
            status="pending" if replacement_enabled else "paused", enabled=replacement_enabled,
            source_revision=1, job_count=0,
            next_scan_at=now if replacement_enabled and new_provider != "arbeitsagentur" else None,
            pending_search_scopes=replacement_search_scopes,
        )
        session.add(replacement)
        session.flush()
        if new_provider == "arbeitsagentur" and replacement_enabled:
            _schedule_managed_arbeitsagentur_feed(session, replacement, now)
        session.commit()
        session.refresh(replacement)
        return _managed_feed_json(replacement)

    if request.enabled is not None:
        if changed_enabled:
            feed.source_revision = (feed.source_revision or 1) + 1
            if feed.provider == "arbeitsagentur":
                pending_scopes = list(feed.pending_search_scopes) if isinstance(
                    feed.pending_search_scopes, list) else []
                running_scopes = session.scalars(select(ScanRun.search_scope).where(
                    ScanRun.feed_id == feed.id, ScanRun.status == "running",
                    ScanRun.search_scope.is_not(None))).all()
                for scope in running_scopes:
                    if isinstance(scope, dict) and scope not in pending_scopes:
                        pending_scopes.append(scope)
                feed.pending_search_scopes = pending_scopes
        feed.enabled = request.enabled
    if not feed.enabled:
        feed.next_scan_at = None
        feed.status = "paused"
    elif request.enabled is True:
        feed.status = "pending"
        if feed.provider == "arbeitsagentur":
            _schedule_managed_arbeitsagentur_feed(session, feed, now)
        else:
            feed.next_scan_at = now
    session.commit()
    session.refresh(feed)
    return _managed_feed_json(feed)


@app.post("/api/v1/feeds/{feed_id}/scan", status_code=202)
def rescan_managed_feed(feed_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    feed = session.scalars(select(JobFeed).options(joinedload(JobFeed.company)).where(
        JobFeed.id == feed_id)).first()
    if feed is None:
        raise HTTPException(status_code=404, detail="Job feed not found")
    if not feed.enabled:
        raise HTTPException(status_code=409, detail="Resume this feed before scanning it")
    if feed.provider == "arbeitsagentur" and not _is_managed_arbeitsagentur_feed(feed):
        raise HTTPException(status_code=409, detail="This is an imported snapshot, not a scheduled BA adapter.")
    now = utcnow()
    if feed.provider == "arbeitsagentur":
        current_pending = feed.pending_search_scopes if isinstance(feed.pending_search_scopes, list) else []
        locations = session.scalars(select(ConfiguredLocation).where(
            ConfiguredLocation.enabled.is_(True)).order_by(ConfiguredLocation.id)).all()
        pending = []
        pending_keys = set()
        queued_locations = set()
        for scope in current_pending:
            if not isinstance(scope, dict):
                continue
            if (any(location.search_scope != "country" for location in locations)
                    and is_synthetic_germany_scope(scope)):
                continue
            forced = {**scope, "scheduled_for": now.isoformat()}
            job_id = forced.get("discovery_job_id")
            location_id = forced.get("configured_location_id")
            key = (("job", job_id) if job_id is not None else
                   ("location", location_id) if location_id is not None else
                   ("scope", json.dumps(forced, sort_keys=True)))
            if key in pending_keys:
                continue
            pending_keys.add(key)
            pending.append(forced)
            if location_id is not None:
                queued_locations.add(location_id)
        pending.extend(configured_location_scope_payload(location, now) for location in locations
                       if location.id not in queued_locations)
        if not pending:
            pending = [germany_search_scope_payload(now)]
        feed.pending_search_scopes = pending
    feed.next_scan_at = now
    feed.status = "pending"
    session.commit()
    session.refresh(feed)
    return _managed_feed_json(feed)


@app.get("/api/v1/summary")
def summary(latitude: float = Query(49.0068705, ge=-90, le=90),
            longitude: float = Query(8.4034195, ge=-180, le=180),
            radius_km: float = Query(15, gt=0, le=200),
            place: str | None = Query(None, max_length=120),
            session: Session = Depends(get_session), state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = search_snapshots.canonical_state(state)
    employer_country = _canonical_country(country)
    company_page = list_companies(latitude, longitude, radius_km, None, 0, 1, session, state=state, country=employer_country)
    job_page = list_jobs(latitude=latitude, longitude=longitude, radius_km=radius_km,
                         company_id=None, query=None, place=place, offset=0, limit=1,
                         location_scope='area_remote' if state or employer_country else 'area', session=session, state=state, country=employer_country)
    remote_job_page = list_jobs(latitude=latitude, longitude=longitude, radius_km=radius_km,
                                company_id=None, query=None, place=place, offset=0, limit=1,
                                location_scope='remote', work_style="remote", session=session, state=state, country=employer_country)
    if state or employer_country:
        domain_count = session.scalar(select(func.count(Company.id)).where(
            german_company_filter() if employer_country else Company.state == state, Company.domain.is_not(None))) or 0
    elif IS_SQLITE:
        domain_candidates = session.scalars(select(Company).where(
            Company.domain.is_not(None), *_sqlite_company_bbox(latitude, longitude, radius_km))).all()
        domain_count = sum(_distance(company, latitude, longitude) <= radius_km * 1000 for company in domain_candidates)
    else:
        domain_count = session.scalar(text("SELECT count(*) FROM companies WHERE domain IS NOT NULL AND ST_DWithin(location_geog, ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)::geography,:radius_m,false)")
                                      .params(longitude=longitude, latitude=latitude, radius_m=radius_km * 1000)) or 0
    return {"companies_in_radius": company_page["total"], "companies_with_domain": domain_count,
            "jobs_for_location": job_page["total"],
            "remote_jobs_in_result": remote_job_page["total"],
            "as_of": datetime.now(timezone.utc).isoformat()}


@app.post('/api/v1/profile-matching/rematch')
def rematch_all_jobs():
    from hiring_scraper.app.profile_llm import requeue_all_assessments
    return requeue_all_assessments()


class MatchingModelSetting(BaseModel):
    model: str = Field(min_length=1, max_length=100)


@app.put('/api/v1/profile-matching/model')
def set_profile_matching_model(request: MatchingModelSetting):
    from hiring_scraper.app.profile_llm import set_matching_model
    try:
        return set_matching_model(request.model)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get('/api/v1/profile-matching/status')
def profile_matching_status():
    from hiring_scraper.app.profile_llm import matching_status
    return matching_status()


FRONTEND_DIR = Path(os.getenv(
    "HIRING_FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"
))
FRONTEND_INDEX = FRONTEND_DIR / "index.html"
FRONTEND_ASSETS = FRONTEND_DIR / "assets"
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")


@app.get("/", include_in_schema=False)
def frontend_index() -> FileResponse:
    if not FRONTEND_INDEX.is_file():
        raise HTTPException(status_code=404, detail="Frontend is not built")
    return FileResponse(FRONTEND_INDEX)


@app.get("/{frontend_path:path}", include_in_schema=False)
def frontend_route(frontend_path: str) -> FileResponse:
    if frontend_path in {"api", "health"} or frontend_path.startswith(("api/", "health/")):
        raise HTTPException(status_code=404, detail="Not found")
    if not FRONTEND_INDEX.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    frontend_root = FRONTEND_DIR.resolve()
    requested_file = (frontend_root / frontend_path).resolve()
    try:
        requested_file.relative_to(frontend_root)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Not found") from error
    if requested_file.is_file():
        return FileResponse(requested_file)
    return FileResponse(FRONTEND_INDEX)
