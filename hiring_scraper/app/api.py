"""HTTP API for nearby companies, job discovery, and profile matching."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
import json
import math
from math import cos, radians
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, delete, func, or_, select, text, update
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from hiring_scraper.app.database import IS_SQLITE, SessionLocal, engine, get_session, initialize_sqlite_schema
from hiring_scraper.app.models import (
    ApplicationMomentumEvent, Base, Company, ConfiguredLocation,
    DiscoveryJob, DiscoveryJobCompany, DiscoveryRun, Job, JobApplication,
    JobSearchRefresh, JobSearchResult, JobSearchSnapshot, JobSearchState, ProfileJobMatch, SemanticSearchDocument,
    SemanticSearchIndexState, UserProfile,
    JobFeed, JobLocation, LocationCache, ScanRun, utcnow,
)
from hiring_scraper.app.discovery_jobs import (
    configured_location_json, create_discovery_job as make_discovery_job, discovery_job_json, german_company_filter,
)
from hiring_scraper.app.feed_schedules import (
    configured_location_scope_payload, germany_search_scope_payload,
    is_synthetic_germany_scope, next_scope_due, queue_arbeitsagentur_scope,
)
from hiring_scraper.app.default_job_feeds import ensure_default_job_feeds
from hiring_scraper.geography import GERMAN_STATES, haversine_m, normalize_german_state
from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.profiles import router as profiles_router, require_profile
from hiring_scraper.app.applications import APPLICATION_STATUSES, ApplicationRequest, ApplicationStatus, application_json
from hiring_scraper.app.job_search import (
    ProfileIndexChanged, ensure_profile_match_indexes,
)
from hiring_scraper.matching import VERSION as MATCHING_VERSION, match_job
from hiring_scraper.app.board_scope import (
    deduplicate_jobs, expiry, fully_remote, geographic_scope, remote_countries,
    german_location_text_names, german_state_bounds, location_text_names,
    vacancy_keys, vacancy_text_scope,
)


KARLSRUHE = {"label": "Karlsruhe, Baden-Württemberg, Deutschland", "city": "Karlsruhe",
             "state": "Baden-Württemberg", "latitude": 49.0068705, "longitude": 8.4034195,
             "precision": "city_centroid", "country_code": "de", "type": "city"}
ATS_FEED_PROVIDERS = {"greenhouse", "lever", "personio", "ashby", "recruitee", "smartrecruiters"}
MANAGED_FEED_PROVIDERS = {"external_html", "browser_html", "rss", "arbeitsagentur", *ATS_FEED_PROVIDERS}
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_REQUEST = 0.0
_LOG = logging.getLogger(__name__)
_SEARCH_SNAPSHOT_REFRESH_DELAY_SECONDS = 2.0
_SEARCH_SNAPSHOT_REFRESH_LEASE_SECONDS = 15 * 60
_SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS = 15 * 60
_WORK_LOCATION_GEOGRAPHY_VERSION = 'work-location-text-v2'
_SEMANTIC_MODEL_KEY = "intfloat/multilingual-e5-small@ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3"
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
        SemanticSearchIndexState.model_revision == _SEMANTIC_MODEL_KEY,
    )))


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
    provider: Literal["external_html", "browser_html", "rss", "ats", "arbeitsagentur"] = "external_html"
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


def _coordinate_bbox_filters(latitude: float, longitude: float, radius_km: float,
                             latitude_column, longitude_column):
    latitude_delta = radius_km / 110.0
    filters = [
        latitude_column.is_not(None), longitude_column.is_not(None),
        latitude_column.between(max(-90.0, latitude - latitude_delta),
                                min(90.0, latitude + latitude_delta)),
    ]
    longitude_scale = abs(cos(radians(latitude)))
    if longitude_scale > 1e-6:
        longitude_delta = min(360.0, radius_km / (110.0 * longitude_scale))
        if longitude_delta < 180.0:
            west, east = longitude - longitude_delta, longitude + longitude_delta
            if west < -180:
                filters.append(or_(longitude_column >= west + 360, longitude_column <= east))
            elif east > 180:
                filters.append(or_(longitude_column >= west, longitude_column <= east - 360))
            else:
                filters.append(longitude_column.between(west, east))
    return filters


def _sqlite_company_bbox(latitude: float, longitude: float, radius_km: float):
    """Use indexed coordinate bounds before the exact Python haversine check."""
    return _coordinate_bbox_filters(latitude, longitude, radius_km,
                                    Company.latitude, Company.longitude)


def _location_text_patterns(value: str) -> set[str]:
    source = value.strip().casefold().replace('ß', 'ss')
    normalized = unicodedata.normalize('NFKD', source)
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    transliterated = source.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue'}))
    patterns = set()
    for raw in {value.strip().casefold(), value.strip().upper()}:
        raw_words = re.sub(r'[^A-Za-z0-9äöüÄÖÜ]+', '%', raw).strip('%')
        if raw_words:
            patterns.add(f'%{raw_words}%')
    for variant in {normalized, transliterated}:
        words = re.sub(r'[^a-z0-9]+', '%', variant).strip('%')
        if words:
            patterns.add(f'%{words}%')
    return patterns


def _job_location_text_candidate(value: str):
    patterns = {pattern for name in location_text_names(value)
                for pattern in _location_text_patterns(name)}
    return or_(*(Job.location_text.ilike(pattern) for pattern in patterns),
               *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns))


def _remote_job_candidate():
    return or_(Job.work_arrangement == 'remote',
               and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None)))


def _job_area_candidates(latitude: float, longitude: float, radius_km: float,
                         place: str | None, location_scope: str):
    """Broadly narrow jobs before Python performs exact workplace and remote checks."""
    expiry_clause = or_(Job.raw_metadata["validThrough"].as_string().is_not(None),
                        Job.raw_metadata["valid_through"].as_string().is_not(None))
    if location_scope == 'remote':
        return or_(_remote_job_candidate(), expiry_clause)
    if not IS_SQLITE:
        location_ids = select(JobLocation.job_id).where(text(
            "ST_DWithin(job_locations.location_geog, "
            "ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, :radius_m, false)"
        ).bindparams(longitude=longitude, latitude=latitude, radius_m=radius_km * 1000))
        spatial_clause = Job.id.in_(location_ids)
    else:
        bounds = _coordinate_bbox_filters(latitude, longitude, radius_km,
                                          JobLocation.latitude, JobLocation.longitude)
        location_ids = select(JobLocation.job_id).where(*bounds)
        spatial_clause = Job.id.in_(location_ids)
    clauses = [spatial_clause, expiry_clause, _remote_job_candidate()]
    if place:
        clauses.append(_job_location_text_candidate(place))
    return or_(*clauses)


def _job_state_text_candidates(state: str):
    patterns = {pattern for value in german_location_text_names(state)
                for pattern in _location_text_patterns(value)}
    candidates = [*(Job.location_text.ilike(pattern) for pattern in patterns),
                  *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns)]
    if bounds := german_state_bounds().get(state):
        west, south, east, north = bounds
        candidates.append(Job.locations.any(and_(
            JobLocation.latitude.between(south, north),
            JobLocation.longitude.between(west, east),
        )))
    return or_(*candidates)


def _job_germany_text_candidates():
    patterns = {pattern for value in ('Germany', 'Deutschland', *german_location_text_names())
                for pattern in _location_text_patterns(value)}
    country_code_patterns = (
        'DE', 'DE %', 'DE,%', 'DE/%', 'DE-%', 'DE)%',
        '%, DE', '%, DE %', '%, DE,%', '%,DE', '%,DE %',
        '% DE', '% DE %', '% DE,%', '%/DE', '%/DE %', '%/DE,%',
        '%(DE)%', '% (DE)%', '%(DE %', '% (DE %',
        '%;DE%', '%; DE%', '%|DE%', '%| DE%', '%\nDE%',
    )
    candidates = [*(Job.location_text.ilike(pattern) for pattern in patterns),
                  *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns),
                  *(Job.location_text.ilike(pattern) for pattern in country_code_patterns),
                  *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in country_code_patterns)]
    bounds = tuple(german_state_bounds().values())
    if bounds:
        west = min(bound[0] for bound in bounds)
        south = min(bound[1] for bound in bounds)
        east = max(bound[2] for bound in bounds)
        north = max(bound[3] for bound in bounds)
        candidates.append(Job.locations.any(and_(
            JobLocation.latitude.between(south, north),
            JobLocation.longitude.between(west, east),
        )))
    return or_(*candidates)


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
        return sorted(jobs, key=lambda job: (job.title.casefold(), _job_employer(job).casefold(), job.id))
    if sort == "company":
        return sorted(jobs, key=lambda job: (_job_employer(job).casefold(), job.title.casefold(), job.id))
    return sorted(jobs, key=lambda job: (not job.is_remote, job.title.casefold(), job.id))


def _job_date_rank(value: Any) -> float:
    if isinstance(value, datetime):
        parsed = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return parsed.timestamp()
    if not isinstance(value, str) or not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        return parsed.timestamp()
    except ValueError:
        return float("-inf")


def _job_employer(job: Job) -> str:
    if job.employer_company is not None:
        return job.employer_company.name
    metadata = job.raw_metadata if isinstance(job.raw_metadata, dict) else {}
    organization = metadata.get("hiring_organization")
    if isinstance(organization, dict) and isinstance(organization.get("name"), str) and organization["name"].strip():
        return organization["name"].strip()
    return job.feed.company.name


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
    return {company_id: len(_deduplicate_jobs(rows)) for company_id, rows in grouped.items()}


def _feed_json(feed: JobFeed) -> dict[str, Any]:
    return {"id": feed.id, "provider": feed.provider, "tenant": feed.tenant,
            "board_url": feed.board_url, "feed_url": feed.feed_url,
            "source_config": feed.source_config or {},
            "enabled": feed.enabled,
            "status": feed.status, "job_count": feed.job_count,
            "last_checked_at": feed.last_checked_at.isoformat() if feed.last_checked_at else None,
            "next_scan_at": feed.next_scan_at.isoformat() if feed.next_scan_at else None,
            "last_error": feed.last_error}


def _job_json(job: Job, *, include_description: bool = False) -> dict[str, Any]:
    feed, source_company = job.feed, job.feed.company
    company = job.employer_company or source_company
    raw_metadata = job.raw_metadata if isinstance(job.raw_metadata, dict) else {}
    organization = raw_metadata.get("hiring_organization")
    organization = organization if isinstance(organization, dict) else {}
    employer_name = organization.get("name") if isinstance(organization.get("name"), str) else None
    employer_url = organization.get("url") if isinstance(organization.get("url"), str) else None
    employer_host = urlsplit(employer_url).hostname if employer_url else None
    source_company_name = source_company.name
    resolved_employer_name = (company.name if job.employer_company is not None else
                              employer_name.strip() if employer_name and employer_name.strip() else source_company.name)
    locations = [{"label": location.label, "latitude": location.latitude,
                  "longitude": location.longitude, "precision": location.precision,
                  "country_code": location.country_code} for location in job.locations]
    data = {"id": job.id, "external_id": job.external_id, "title": job.title,
            "url": job.url, "company_id": company.id,
            "employer_company_id": job.employer_company_id,
            "company_name": resolved_employer_name,
            "company_name_is_employer": bool(job.employer_company is not None or
                                                (employer_name and employer_name.strip() and
                                                 employer_name.strip().casefold() != source_company_name.casefold())),
            "company_domain": company.domain or (employer_host if job.employer_company is None else None) or source_company.domain,
            "company_website": company.website_url or (employer_url if job.employer_company is None else None) or source_company.website_url,
            "company_source_url": company.source_url or source_company.source_url,
            "source_company_id": source_company.id, "source_company_name": source_company.name,
            "provider": feed.provider,
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
    # Feed scans and stale search snapshot refreshes run in separate workers.


app.include_router(profiles_router)


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


def _canonical_state(state: str | None) -> str | None:
    if state is None:
        return None
    try:
        return normalize_german_state(state)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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
        state = _canonical_state(_model_value(request, "state"))
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
        state = _canonical_state(_model_value(request, "state"))
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
            "status": germany_job.status,
            "stage": germany_job.stage,
            "complete": map_complete,
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


@app.get("/api/v1/companies")
def list_companies(latitude: float = Query(49.0068705, ge=-90, le=90),
                   longitude: float = Query(8.4034195, ge=-180, le=180),
                   radius_km: float = Query(15, gt=0, le=200),
                   query: str | None = Query(None, max_length=160),
                   offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                   session: Session = Depends(get_session),
                   discovery: Literal['all','domain','career','feed','jobs'] = 'all',
                   sort: Literal['distance','name'] = 'distance', state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = _canonical_state(state)
    employer_country = _canonical_country(country)
    base = select(Company).options(selectinload(Company.feeds))
    filters = []
    normalized_query = " ".join(query.split()) if query else None
    vector = (_query_embedding(normalized_query)
              if normalized_query and not IS_SQLITE and _semantic_index_ready(session, "company") else None)
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
                             SemanticSearchDocument.model_revision == _SEMANTIC_MODEL_KEY)
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


def _result_scope(job, latitude, longitude, radius_km, place, country, state=None, employer_country=None):
    if employer_country and not expiry(job)['expired']:
        if fully_remote(job):
            return geographic_scope(job, latitude, longitude, radius_km, place, employer_country)
        text_scope = vacancy_text_scope(job, country=employer_country)
        if text_scope is not None:
            return {'eligible': text_scope, 'reason': None if text_scope else 'outside_area',
                    'unknowns': [], 'match_kind': 'work_location' if text_scope else None}
        company = job.employer_company or job.feed.company
        source_company = job.feed.company
        has_german_workplace = any(
            str(location.country_code or "").casefold() == "de" for location in job.locations)
        eligible = (company.country_code == 'DE' or company.state in GERMAN_STATES or
                    source_company.source == "default_job_feed" or has_german_workplace)
        return {'eligible': eligible, 'reason': None if eligible else 'outside_area',
                'unknowns': [], 'match_kind': ('remote' if fully_remote(job) else 'employer_in_country') if eligible else None}
    if state and not expiry(job)['expired']:
        if fully_remote(job):
            return geographic_scope(job, latitude, longitude, radius_km, place, 'DE')
        text_scope = vacancy_text_scope(job, state=state)
        if text_scope is not None:
            return {'eligible': text_scope, 'reason': None if text_scope else 'outside_area',
                    'unknowns': [], 'match_kind': 'work_location' if text_scope else None}
        company = job.employer_company or job.feed.company
        eligible = company.state == state or job.feed.company.state == state
        return {'eligible': eligible, 'reason': None if eligible else 'outside_area',
                'unknowns': [], 'match_kind': ('remote' if fully_remote(job) else 'employer_in_state') if eligible else None}
    return geographic_scope(job, latitude, longitude, radius_km, place, country)


def _search_scope(job, latitude, longitude, radius_km, place, country,
                  location_scope, state, employer_country):
    scope = _result_scope(job, latitude, longitude, radius_km, place, country, state, employer_country)
    if scope['reason'] != 'expired' and location_scope == 'remote' and not fully_remote(job):
        return {'eligible': False, 'reason': 'not_remote', 'unknowns': [], 'match_kind': None}
    if scope['eligible'] and location_scope == 'area' and fully_remote(job):
        return {'eligible': False, 'reason': 'remote_not_selected', 'unknowns': [], 'match_kind': 'remote'}
    return scope


def _scoped_candidate_winners(session, candidate_ids, latitude, longitude, radius_km,
                              place, country, location_scope, state, employer_country):
    """Stream scope checks and retain only the canonical job from each duplicate group."""
    groups: dict[int, dict[str, Any]] = {}
    seen: dict[tuple, int] = {}
    filtered = {'expired': 0, 'remote_country': 0, 'outside_area': 0,
                'remote_not_selected': 0, 'not_remote': 0}
    scoped_count = 0
    for start in range(0, len(candidate_ids), 200):
        batch_ids = candidate_ids[start:start + 200]
        jobs = session.scalars(select(Job).where(Job.id.in_(batch_ids)).options(
            joinedload(Job.feed).joinedload(JobFeed.company),
            joinedload(Job.employer_company), selectinload(Job.locations),
        )).unique().all()
        for job in jobs:
            scope = _search_scope(job, latitude, longitude, radius_km, place, country,
                                  location_scope, state, employer_country)
            if not scope['eligible']:
                filtered[scope['reason']] += 1
                continue
            scoped_count += 1
            keys = vacancy_keys(job)
            existing = {seen[key] for key in keys if key in seen}
            group_id = min(existing) if existing else job.id
            quality = (job.is_active and not expiry(job)['expired'],
                       len(job.description or ''), len(job.locations), -job.id)
            merged_keys = set(keys)
            winner = (job.id, quality, scope)
            for old_group in existing:
                previous = groups.pop(old_group)
                merged_keys.update(previous['keys'])
                if previous['winner'][1] > winner[1]:
                    winner = previous['winner']
            groups[group_id] = {'keys': merged_keys, 'winner': winner}
            for key in merged_keys:
                seen[key] = group_id
    winners = sorted((group['winner'][0], group['winner'][2]) for group in groups.values())
    return winners, scoped_count, filtered


def _jobs_page(jobs, sort, offset, limit, latitude, longitude, radius_km,
               profile, min_match_score, include_unknown, place=None, country=None,
               location_scope='area', state=None, employer_country=None,
               include_internal=False, indexed_matches=None, precomputed_scopes=None):
    filtered = {'expired': 0, 'remote_country': 0, 'outside_area': 0,
                'remote_not_selected': 0, 'not_remote': 0,
                'profile_conflict': 0, 'below_score': 0, 'uncertain': 0}
    if precomputed_scopes is not None:
        scoped = jobs
        scopes = precomputed_scopes
        unique = jobs
    else:
        scoped, scopes = [], {}
        for job in jobs:
            scope = _search_scope(job, latitude, longitude, radius_km, place, country,
                                  location_scope, state, employer_country)
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
            indexed = (indexed_matches or {}).get(job.id)
            if indexed_matches is not None and indexed is None:
                match = {'eligible': False, 'score': 0, 'fit_tier': 'unlikely',
                         'uncertain': False, 'unknowns': []}
            elif indexed is None:
                match = _profile_match(job, profile, scopes[job.id])
            else:
                scope_unknowns = scopes[job.id].get('unknowns') or []
                tier = indexed.match_tier
                uncertain = bool(indexed.match_uncertain or scope_unknowns)
                if scope_unknowns and tier == 'recommended':
                    tier = 'possible'
                match = {'eligible': True, 'score': indexed.match_score,
                         'fit_tier': tier, 'uncertain': uncertain,
                         'unknowns': scope_unknowns}
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
    if sort in {'relevance', 'match', 'posted', 'discovered'}:
        def match_order(job):
            match = matches.get(job.id) or {}
            score = match.get('score', -1)
            discovered = _job_date_rank(job.first_seen_at)
            posted = _job_date_rank(job.date_posted)
            if sort == 'posted':
                return (-posted, -score, -discovered, job.title.casefold(), job.id)
            if sort == 'discovered':
                return (-discovered, -score, -posted, job.title.casefold(), job.id)
            return (-score, -discovered, -posted, job.title.casefold(), job.id)
        matching.sort(key=match_order)
    items = [] if include_internal else [_job_json(job) for job in matching[offset:offset + limit]]
    for item in items:
        item['match_kind'] = scopes[item['id']]['match_kind']
        item['geography'] = scopes[item['id']]
        if profile is not None:
            item['profile_match'] = matches[item['id']]
    tiers = [matches[job.id].get('fit_tier', 'possible') for job in matching] if profile else []
    result = {'items': items, 'total': len(matching), 'offset': offset, 'limit': limit,
            'location': {'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
                         'city': place, 'country_code': country},
            'location_scope': location_scope,
            'profile_id': profile.id if profile is not None else None,
            'counts': {'source': len(jobs), 'scoped': len(unique), 'filtered': filtered,
                       'duplicates_removed': len(scoped) - len(unique),
                       'recommended': tiers.count('recommended'), 'possible': tiers.count('possible'),
                       'unlikely': tiers.count('unlikely')},
            'coverage': {'source_count': len({job.feed_id for job in jobs}),
                         'note': 'Observed active source records; selected sources are not exhaustive market coverage.'},
            'rule': ('work location in Germany, with a German-employer fallback when location is missing or unspecified' if employer_country else 'work location in ' + state + ', with an employer-state fallback when location is missing or unspecified' if state else ('explicitly remote within country scope' if location_scope == 'remote' else
                     'mapped or parsed work location within radius' if location_scope == 'area' else
                     'mapped or parsed work location within radius OR explicitly remote within country scope')),
            'state': state, 'country': employer_country}
    if profile is not None:
        result['_matching_job_ids'] = [job.id for job in matching]
    if include_internal:
        result['_search_entries'] = [{
            'job_id': job.id,
            'scope': scopes[job.id],
            'match_score': matches.get(job.id, {}).get('score'),
            'match_tier': matches.get(job.id, {}).get('fit_tier'),
            'match_uncertain': matches.get(job.id, {}).get('uncertain'),
        } for job in matching]
    return result


def _search_generation(session: Session) -> int:
    generation = session.scalar(select(func.max(JobSearchState.id)))
    if generation is None:
        session.add(JobSearchState())
        session.flush()
        generation = session.scalar(select(func.max(JobSearchState.id)))
    return int(generation or 1)


def _search_key(generation: int, criteria: dict[str, Any]) -> str:
    from hiring_scraper.matching import VERSION
    payload = json.dumps({'generation': generation, 'matching_version': VERSION, **criteria},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _search_criteria_hash(criteria: dict[str, Any]) -> str:
    stable = _stable_search_criteria(criteria)
    payload = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _search_page(session: Session, candidate_ids: list[int], profiles: list[Any], key: str,
                 generation: int, source_count: int, source_feed_count: int,
                 prefilter_filtered: dict[str, int],
                 sort: str, latitude: float, longitude: float,
                 radius_km: float, place: str | None, country: str | None,
                 location_scope: str, state: str | None, employer_country: str | None,
                 thresholds: dict[int | None, tuple[int, bool]],
                 criteria: dict[str, Any],
                 preserve_snapshot_key: str | None = None,
                 is_prewarmed: bool = False) -> JobSearchSnapshot:
    """Fetch a cached ordered result or materialize it once for later page reads."""
    snapshot = session.get(JobSearchSnapshot, key)
    if snapshot is not None:
        if is_prewarmed:
            snapshot.criteria_hash = _search_criteria_hash(criteria)
            snapshot.is_prewarmed = True
            session.commit()
        return snapshot

    if session.get_bind().dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    now = utcnow()
    insert_statement = insert(JobSearchSnapshot).values(
        key=key, generation=generation, payload={},
        criteria_hash=_search_criteria_hash(criteria), created_at=now,
        is_prewarmed=is_prewarmed,
    ).on_conflict_do_nothing(index_elements=['key']).returning(JobSearchSnapshot.key)
    inserted_key = session.scalar(insert_statement)
    if inserted_key is None:
        # A concurrent request owns this key and has committed the complete row set.
        snapshot = session.get(JobSearchSnapshot, key)
        if snapshot is not None:
            return snapshot
        # The competing transaction rolled back; this request can safely retry.
        return _search_page(session, candidate_ids, profiles, key, generation, source_count, source_feed_count,
                            prefilter_filtered,
                            sort, latitude, longitude,
                            radius_km, place, country, location_scope, state, employer_country,
                            thresholds, criteria)

    result_count = 0
    combined_counts: dict[str, Any] = {
        'source': 0, 'scoped': 0, 'duplicates_removed': 0,
        'recommended': 0, 'possible': 0, 'unlikely': 0,
        'filtered': dict.fromkeys(('expired', 'remote_country', 'outside_area',
                                   'remote_not_selected', 'not_remote',
                                   'profile_conflict', 'below_score', 'uncertain'), 0),
    }
    winners, scoped_count, geo_filtered = _scoped_candidate_winners(
        session, candidate_ids, latitude, longitude, radius_km, place, country,
        location_scope, state, employer_country,
    )
    winner_ids = [job_id for job_id, _ in winners]
    scopes_by_id = dict(winners)
    duplicates_removed = scoped_count - len(winners)
    search_profiles = profiles or [None]
    for _profile in search_profiles:
        combined_counts['source'] += source_count
        combined_counts['scoped'] += len(winners)
        combined_counts['duplicates_removed'] += duplicates_removed
        for name, count in geo_filtered.items():
            combined_counts['filtered'][name] += count
        for name, count in prefilter_filtered.items():
            combined_counts['filtered'][name] += count

    # The job graph and profile matches stay bounded to one batch. Only the
    # compact canonical candidate IDs and exact scope results survive the scan.
    for start in range(0, len(winner_ids), 200):
        batch_ids = winner_ids[start:start + 200]
        jobs = session.scalars(select(Job).where(Job.id.in_(batch_ids)).options(
            joinedload(Job.feed).joinedload(JobFeed.company),
            joinedload(Job.employer_company), selectinload(Job.locations),
        )).unique().all()
        batch_scopes = {job_id: scopes_by_id[job_id] for job_id in batch_ids}
        indexed_matches: dict[int, dict[int, ProfileJobMatch]] = {
            item.id: {} for item in profiles
        }
        if profiles and batch_ids:
            indexed_rows = session.scalars(select(ProfileJobMatch).where(
                ProfileJobMatch.profile_id.in_([item.id for item in profiles]),
                ProfileJobMatch.job_id.in_(batch_ids),
            )).all()
            for indexed in indexed_rows:
                indexed_matches[indexed.profile_id][indexed.job_id] = indexed

        result_rows = [{
            'snapshot_key': key,
            'job_id': job.id,
            'scope': batch_scopes[job.id],
            'scope_uncertain': bool(batch_scopes[job.id].get('unknowns')),
            'posted_rank': _job_date_rank(job.date_posted),
            'discovered_rank': _job_date_rank(job.first_seen_at),
            'title_sort': job.title.casefold(),
            'company_sort': _job_employer(job).casefold(),
            'remote_sort': not job.is_remote,
        } for job in jobs]
        for profile in search_profiles:
            min_score, include_unknown = thresholds.get(profile.id if profile else None, (0, True))
            page = _jobs_page(
                jobs, sort, 0, 1, latitude, longitude, radius_km, profile,
                min_score, include_unknown, place, country, location_scope,
                state, employer_country, include_internal=True,
                indexed_matches=indexed_matches.get(profile.id, {}) if profile else None,
                precomputed_scopes=batch_scopes,
            )
            entries = page.pop('_search_entries', [])
            page.pop('_matching_job_ids', None)
            for name in ('profile_conflict', 'below_score', 'uncertain'):
                combined_counts['filtered'][name] += page['counts']['filtered'][name]
            for name in ('recommended', 'possible', 'unlikely'):
                combined_counts[name] += page['counts'].get(name, 0)
            result_count += len(entries)
        if result_rows:
            session.execute(insert(JobSearchResult), result_rows)
    snapshot = session.get(JobSearchSnapshot, key)
    snapshot.payload = {
        'counts': combined_counts,
        'coverage': {'source_count': source_feed_count,
                     'note': 'Observed active source records; selected sources are not exhaustive market coverage.'},
        'location': {'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
                     'city': place, 'country_code': country},
        'location_scope': location_scope,
        'profile_ids': [profile.id for profile in profiles],
        'profile_id': profiles[0].id if len(profiles) == 1 else None,
        'total': result_count,
        'rule': ('work location in Germany, with a German-employer fallback when location is missing or unspecified' if employer_country else
                 'work location in ' + state + ', with an employer-state fallback when location is missing or unspecified' if state else
                 ('explicitly remote within country scope' if location_scope == 'remote' else
                  'mapped or parsed work location within radius' if location_scope == 'area' else
                  'mapped or parsed work location within radius OR explicitly remote within country scope')),
        'state': state,
        'country': employer_country,
        'criteria': criteria,
    }
    # A snapshot stores one scoped row per canonical job; bound both age and count.
    stale_filter = or_(
        and_(JobSearchSnapshot.is_prewarmed.is_(True),
             JobSearchSnapshot.created_at < now - timedelta(seconds=_SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)),
        and_(JobSearchSnapshot.is_prewarmed.is_(False),
             JobSearchSnapshot.created_at < now - timedelta(days=1)),
    )
    excess_keys = select(JobSearchSnapshot.key).order_by(
        JobSearchSnapshot.created_at.desc(), JobSearchSnapshot.key).where(
            JobSearchSnapshot.is_prewarmed.is_(False))
    if preserve_snapshot_key is not None:
        stale_filter = and_(stale_filter, JobSearchSnapshot.key != preserve_snapshot_key)
        excess_keys = excess_keys.where(JobSearchSnapshot.key != preserve_snapshot_key)
    excess_keys = excess_keys.offset(32)
    stale_keys = select(JobSearchSnapshot.key).where(or_(
        stale_filter, JobSearchSnapshot.key.in_(excess_keys)))
    session.execute(delete(JobSearchResult).where(JobSearchResult.snapshot_key.in_(stale_keys)))
    session.execute(delete(JobSearchSnapshot).where(JobSearchSnapshot.key.in_(stale_keys)))
    session.commit()
    return snapshot


def _stable_search_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in criteria.items() if name != 'freshness_bucket'}


def _search_criteria_match(snapshot_criteria: Any, criteria: dict[str, Any]) -> bool:
    return (isinstance(snapshot_criteria, dict) and
            _stable_search_criteria(snapshot_criteria) == _stable_search_criteria(criteria))


def _latest_stale_snapshot(session: Session, generation: int,
                           criteria: dict[str, Any]) -> JobSearchSnapshot | None:
    # Freshness buckets are cache versions, not user-visible search criteria.
    # Reuse the prior bucket while the worker builds the current one, but keep
    # the stale window no longer than one bucket so expiry checks stay bounded.
    stale_before = utcnow() - timedelta(seconds=_SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)
    snapshot = session.scalars(select(JobSearchSnapshot).where(
        JobSearchSnapshot.criteria_hash == _search_criteria_hash(criteria),
        JobSearchSnapshot.generation <= generation,
        JobSearchSnapshot.created_at >= stale_before,
    ).order_by(JobSearchSnapshot.created_at.desc(), JobSearchSnapshot.key).limit(1)).first()
    if snapshot is None or not _search_criteria_match((snapshot.payload or {}).get('criteria'), criteria):
        return None
    return snapshot


def _materialize_search_snapshot(session: Session, profiles: list[UserProfile], key: str,
                                 generation: int, criteria: dict[str, Any],
                                 thresholds: dict[int | None, tuple[int, bool]],
                                 *, preserve_snapshot_key: str | None = None,
                                 is_prewarmed: bool = False) -> JobSearchSnapshot:
    """Build the full scoped result set for a request, outside the response path when possible."""
    latitude = criteria['latitude']
    longitude = criteria['longitude']
    radius_km = criteria['radius_km']
    place = criteria['place']
    scope_country = criteria['country']
    location_scope = criteria['location_scope']
    state = criteria['state']
    employer_country = criteria['employer_country']
    company_id = criteria['company_id']
    normalized_query = criteria['query']
    work_style = criteria['work_style']

    employer_company = aliased(Company)
    candidate_statement = (select(Job.id).join(Job.feed).join(JobFeed.company)
                           .outerjoin(employer_company, Job.employer_company_id == employer_company.id))
    filters = [Job.is_active.is_(True)]
    if employer_country:
        scope_candidates = [german_company_filter(employer_company), german_company_filter(Company),
                            Company.source == "default_job_feed",
                            Job.locations.any(func.lower(JobLocation.country_code) == "de"),
                            _job_germany_text_candidates()]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    elif state:
        scope_candidates = [Company.state == state, employer_company.state == state,
                            _job_state_text_candidates(state)]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    if company_id is not None:
        filters.append(or_(JobFeed.company_id == company_id, Job.employer_company_id == company_id))
    if normalized_query:
        pattern = f"%{normalized_query}%"
        filters.append(or_(Job.title.ilike(pattern), Company.name.ilike(pattern), Company.domain.ilike(pattern),
                           employer_company.name.ilike(pattern), employer_company.domain.ilike(pattern),
                           Job.raw_metadata["hiring_organization"]["name"].as_string().ilike(pattern)))
    if work_style == 'remote':
        remote_clause = or_(Job.work_arrangement == 'remote',
                            and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None)))
        filters.append(remote_clause)
    elif work_style in {'hybrid', 'onsite'}:
        filters.append(Job.work_arrangement == work_style)
    source_filters = list(filters)
    if not state and not employer_country:
        filters.append(_job_area_candidates(latitude, longitude, radius_km, place, location_scope))

    candidate_cache_key = (generation, _WORK_LOCATION_GEOGRAPHY_VERSION, company_id, normalized_query, state, employer_country,
                           work_style, latitude, longitude, radius_km, place,
                           scope_country, location_scope)
    reusable = (not normalized_query and company_id is None and state is None and
                employer_country is None and work_style == 'all')
    cached = session.info.get('job_search_candidate_ids') if reusable else None
    source_rows = select(Job.id.label('job_id'), Job.feed_id.label('feed_id')).join(
        Job.feed).join(JobFeed.company).outerjoin(
            employer_company, Job.employer_company_id == employer_company.id
        ).where(*source_filters).subquery()
    source_count, source_feed_count = session.execute(select(
        func.count(source_rows.c.job_id), func.count(func.distinct(source_rows.c.feed_id))
    )).one()
    if cached and cached[0] == candidate_cache_key:
        candidate_ids = cached[1]
    else:
        candidate_ids = session.scalars(
            candidate_statement.where(*filters).order_by(Job.id.asc())
        ).all()
        if reusable:
            session.info['job_search_candidate_ids'] = (candidate_cache_key, candidate_ids)
    prefilter_excluded = max(0, int(source_count) - len(candidate_ids))
    prefilter_filtered = {}
    if prefilter_excluded:
        prefilter_reason = 'not_remote' if location_scope == 'remote' else 'outside_area'
        prefilter_filtered[prefilter_reason] = prefilter_excluded
    return _search_page(session, candidate_ids, profiles, key, generation,
                        source_count, source_feed_count, prefilter_filtered, 'match',
                        latitude, longitude, radius_km, place, scope_country,
                        location_scope, state, employer_country, thresholds, criteria,
                        preserve_snapshot_key=preserve_snapshot_key,
                        is_prewarmed=is_prewarmed)


def _enqueue_search_snapshot_refresh(session: Session, criteria: dict[str, Any], *, prewarm: bool = False) -> None:
    """Coalesce refresh requests with the same search criteria in a durable queue."""
    signature = hashlib.sha256(json.dumps(
        criteria, sort_keys=True, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')).hexdigest()
    if session.get_bind().dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    requested_at = utcnow()
    refresh_table = JobSearchRefresh.__table__
    unleased = or_(refresh_table.c.lease_token.is_(None),
                   refresh_table.c.lease_until.is_(None),
                   refresh_table.c.lease_until <= requested_at)
    coalesced_request_time = case(
        (unleased, refresh_table.c.requested_at),
        else_=requested_at,
    )
    statement = insert(JobSearchRefresh).values(
        key=signature, criteria=criteria, requested_at=requested_at,
        lease_token=None, lease_until=None, prewarm=prewarm,
    ).on_conflict_do_update(index_elements=['key'], set_={
        'criteria': criteria,
        'prewarm': or_(refresh_table.c.prewarm, prewarm),
        # Keep the original debounce start for an idle item so steady search
        # traffic cannot postpone it forever. During a live lease, move this
        # timestamp to mark that a follow-up pass is needed after the lease.
        'requested_at': coalesced_request_time,
    })
    session.execute(statement)
    session.commit()


def prewarm_profile_location_snapshots_once() -> int:
    """Queue profileless browse snapshots for every saved profile/configured area."""
    if IS_SQLITE:
        return 0
    now = utcnow()
    current_bucket = int(now.timestamp() // _SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)
    locations: dict[str, dict[str, Any]] = {}

    def add_area_variants(base: dict[str, Any], *, broad_scope: bool) -> None:
        variants = [("area_remote", "all"), ("remote", "remote")]
        if broad_scope:
            variants.insert(1, ("area", "all"))
        for location_scope, work_style in variants:
            criteria = {**base, "location_scope": location_scope, "work_style": work_style}
            locations[json.dumps(criteria, sort_keys=True, separators=(",", ":"))] = criteria

    with SessionLocal() as session:
        for profile in session.scalars(select(UserProfile).order_by(UserProfile.id)):
            area = (profile.preferences or {}).get("search_area") or {}
            if area.get("latitude") is None or area.get("longitude") is None:
                continue
            base = {
                "matching_version": MATCHING_VERSION,
                "geography_version": _WORK_LOCATION_GEOGRAPHY_VERSION,
                "freshness_bucket": current_bucket,
                "company_id": None, "query": None,
                "latitude": float(area["latitude"]), "longitude": float(area["longitude"]),
                "radius_km": float(area.get("radius_km") or 15),
                "place": area.get("city") or "",
                "country": str(area.get("country_code") or "DE").upper(), "sort": "match",
                "state": None, "employer_country": None, "profiles": [],
            }
            base = {name: value for name, value in base.items() if name != "sort"}
            add_area_variants(base, broad_scope=True)
        for location in session.scalars(select(ConfiguredLocation).where(
            ConfiguredLocation.enabled.is_(True)).order_by(ConfiguredLocation.id)):
            state = _canonical_state(location.state) if location.search_scope == "state" else None
            employer_country = "DE" if location.search_scope == "country" else None
            for scope_country in (None, "DE"):
                base = {
                    "matching_version": MATCHING_VERSION,
                    "geography_version": _WORK_LOCATION_GEOGRAPHY_VERSION,
                    "freshness_bucket": current_bucket,
                    "company_id": None, "query": None,
                    "latitude": float(location.latitude), "longitude": float(location.longitude),
                    "radius_km": float(location.radius_km),
                    "place": location.city or "",
                    "country": scope_country,
                    "state": state, "employer_country": employer_country, "profiles": [],
                }
                # The frontend's boardLocationParams omits coordinates for state and
                # country selections, so use the same safe defaults as list_jobs.
                if state or employer_country:
                    base.update(latitude=49.0068705, longitude=8.4034195, radius_km=15, place=None)
                add_area_variants(base, broad_scope=not (state or employer_country))
        queued = 0
        for criteria in locations.values():
            generation = _search_generation(session)
            current_criteria = {**criteria, "freshness_bucket": current_bucket}
            current_key = _search_key(generation, current_criteria)
            snapshot = session.get(JobSearchSnapshot, current_key)
            if snapshot is None:
                _enqueue_search_snapshot_refresh(session, current_criteria, prewarm=True)
                queued += 1
                continue
            snapshot.is_prewarmed = True
            snapshot.criteria_hash = _search_criteria_hash(current_criteria)
            if snapshot.created_at >= now - timedelta(minutes=5):
                continue
            next_criteria = {**criteria, "freshness_bucket": current_bucket + 1}
            next_key = _search_key(generation, next_criteria)
            next_snapshot = session.get(JobSearchSnapshot, next_key)
            if next_snapshot is None:
                _enqueue_search_snapshot_refresh(session, next_criteria, prewarm=True)
                queued += 1
            else:
                next_snapshot.is_prewarmed = True
                next_snapshot.criteria_hash = _search_criteria_hash(next_criteria)
        session.commit()
        return queued


def _claim_search_snapshot_refresh() -> tuple[str, dict[str, Any], str, datetime, bool] | None:
    token = str(uuid4())
    now = utcnow()
    with SessionLocal.begin() as session:
        refresh = session.scalars(select(JobSearchRefresh).where(
            JobSearchRefresh.requested_at <= now - timedelta(seconds=_SEARCH_SNAPSHOT_REFRESH_DELAY_SECONDS),
            or_(JobSearchRefresh.lease_until.is_(None), JobSearchRefresh.lease_until <= now),
        ).order_by(JobSearchRefresh.requested_at, JobSearchRefresh.key)
            .with_for_update(skip_locked=True).limit(1)).first()
        if refresh is None:
            return None
        refresh.lease_token = token
        refresh.lease_until = now + timedelta(seconds=_SEARCH_SNAPSHOT_REFRESH_LEASE_SECONDS)
        return refresh.key, dict(refresh.criteria), token, refresh.requested_at, refresh.prewarm


def _finish_search_snapshot_refresh(key: str, token: str, requested_at: datetime,
                                    *, retry: bool = False) -> None:
    with SessionLocal.begin() as session:
        refresh = session.get(JobSearchRefresh, key)
        if refresh is None or refresh.lease_token != token:
            return
        unchanged = refresh.requested_at == requested_at
        refresh.lease_token = None
        refresh.lease_until = None
        if not unchanged:
            return
        if retry:
            refresh.requested_at = utcnow()
        else:
            session.delete(refresh)


def process_search_snapshot_refresh_once() -> bool:
    """Process one queued refresh; called by the dedicated search worker."""
    try:
        claimed = _claim_search_snapshot_refresh()
    except Exception:
        _LOG.exception("Could not claim a job search snapshot refresh")
        return False
    if claimed is None:
        return False
    key, criteria, token, requested_at, prewarm = claimed
    try:
        # On-demand work can cross a boundary; proactive work can target the
        # next bucket so it is ready before the current snapshot expires.
        criteria = dict(criteria)
        current_bucket = int(utcnow().timestamp() // _SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)
        try:
            requested_bucket = int(criteria.get('freshness_bucket', current_bucket))
        except (TypeError, ValueError):
            requested_bucket = current_bucket
        criteria['freshness_bucket'] = min(max(requested_bucket, current_bucket), current_bucket + 1)
        profile_criteria = {int(item['id']): item for item in criteria.get('profiles', [])}
        with SessionLocal() as session:
            profile_ids = list(profile_criteria)
            profiles = session.scalars(select(UserProfile).where(
                UserProfile.id.in_(profile_ids)).order_by(UserProfile.id)
            ).all() if profile_ids else []
            current_profiles = (len(profiles) == len(profile_ids) and all(
                profile.match_index_version == profile_criteria[profile.id].get('match_index_version')
                for profile in profiles
            ))
            if current_profiles:
                generation = _search_generation(session)
                current_key = _search_key(generation, criteria)
                current_snapshot = session.get(JobSearchSnapshot, current_key)
                if current_snapshot is None:
                    thresholds = {
                        int(item['id']): (int(item['min_match_score']), bool(item['include_unknown']))
                        for item in criteria.get('profiles', [])
                    }
                    if not profiles:
                        thresholds[None] = (0, True)
                    stale = _latest_stale_snapshot(session, generation, criteria)
                    _materialize_search_snapshot(
                        session, profiles, current_key, generation, criteria, thresholds,
                        preserve_snapshot_key=stale.key if stale is not None else None,
                        is_prewarmed=prewarm,
                    )
                elif prewarm and not current_snapshot.is_prewarmed:
                    current_snapshot.is_prewarmed = True
                    current_snapshot.criteria_hash = _search_criteria_hash(criteria)
                    session.commit()
        _finish_search_snapshot_refresh(key, token, requested_at)
    except Exception:
        _LOG.exception("Could not refresh a materialized job search snapshot")
        try:
            _finish_search_snapshot_refresh(key, token, requested_at, retry=True)
        except Exception:
            _LOG.exception("Could not release a failed search snapshot refresh lease")
    return True


def _semantic_jobs_page(session: Session, vector: list[float], *,
                        latitude: float, longitude: float, radius_km: float,
                        place: str | None, country: str | None,
                        location_scope: str, work_style: str,
                        company_id: int | None, state: str | None,
                        employer_country: str | None, profile: UserProfile | None,
                        offset: int, limit: int, job_status: str | None) -> dict[str, Any]:
    """Return nearby jobs in local-model relevance order without profile-match data."""
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    session.execute(text("SET LOCAL hnsw.max_scan_tuples = 100000"))
    employer_company = aliased(Company)
    distance = SemanticSearchDocument.embedding.cosine_distance(vector)
    statement = (select(Job.id, distance.label("semantic_distance"))
        .select_from(Job)
        .join(SemanticSearchDocument, and_(
            SemanticSearchDocument.entity_type == "job",
            SemanticSearchDocument.entity_id == Job.id,
            SemanticSearchDocument.model_revision == _SEMANTIC_MODEL_KEY,
        ))
        .join(JobFeed, JobFeed.id == Job.feed_id)
        .join(Company, Company.id == JobFeed.company_id)
        .outerjoin(employer_company, Job.employer_company_id == employer_company.id))
    filters = [Job.is_active.is_(True)]
    if employer_country:
        scope_candidates = [german_company_filter(employer_company), german_company_filter(Company),
                            Company.source == "default_job_feed",
                            Job.locations.any(func.lower(JobLocation.country_code) == "de"),
                            _job_germany_text_candidates()]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    elif state:
        scope_candidates = [Company.state == state, employer_company.state == state,
                            _job_state_text_candidates(state)]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    if company_id is not None:
        filters.append(or_(JobFeed.company_id == company_id, Job.employer_company_id == company_id))
    if work_style == "remote":
        filters.append(or_(Job.work_arrangement == "remote",
                           and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None))))
    elif work_style in {"hybrid", "onsite"}:
        filters.append(Job.work_arrangement == work_style)
    if not state and not employer_country:
        filters.append(_job_area_candidates(latitude, longitude, radius_km, place, location_scope))
    candidate_filters = list(filters)
    if job_status is not None:
        candidate_filters.append(Job.id.in_(select(JobApplication.job_id).where(
            JobApplication.profile_id == profile.id,
            JobApplication.status == job_status,
        )))
    filtered_statement = statement.where(*candidate_filters)
    candidate_count = int(session.scalar(select(func.count()).select_from(
        filtered_statement.order_by(None).with_only_columns(Job.id).subquery())) or 0)
    batch_size = max(1_000, limit * 10)
    candidate_ids: list[int] = []
    score_by_id: dict[int, float] = {}
    fetch_offset = 0
    winners = []
    scoped_count = 0
    geo_filtered: dict[str, int] = {}
    ordered_ids: list[int] = []
    scopes: dict[int, dict[str, Any]] = {}
    while fetch_offset < candidate_count:
        candidates = session.execute(filtered_statement.order_by(
            distance, Job.id).offset(fetch_offset).limit(batch_size)).all()
        if not candidates:
            break
        candidate_ids.extend(int(row.id) for row in candidates)
        score_by_id.update({int(row.id): float(row.semantic_distance) for row in candidates})
        fetch_offset += len(candidates)
        winners, scoped_count, geo_filtered = _scoped_candidate_winners(
            session, candidate_ids, latitude, longitude, radius_km, place, country,
            location_scope, state, employer_country,
        )
        scopes = dict(winners)
        ordered_ids = sorted((job_id for job_id, _scope in winners),
                             key=lambda job_id: (score_by_id.get(job_id, 1.0), job_id))
        if len(ordered_ids) >= offset + limit or len(candidates) < batch_size:
            break
    applications_by_job: dict[int, JobApplication] = {}
    if profile is not None and ordered_ids:
        applications_by_job = {row.job_id: row for row in session.scalars(select(JobApplication).where(
            JobApplication.profile_id == profile.id,
            JobApplication.job_id.in_(ordered_ids),
        ))}
    application_counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    if profile is not None:
        application_rows = session.execute(select(
            JobApplication.job_id, JobApplication.status,
        ).select_from(JobApplication).join(Job, Job.id == JobApplication.job_id)
            .join(JobFeed, JobFeed.id == Job.feed_id)
            .join(Company, Company.id == JobFeed.company_id)
            .outerjoin(employer_company, Job.employer_company_id == employer_company.id)
            .where(
                JobApplication.profile_id == profile.id, *filters,
                Job.id.in_(select(SemanticSearchDocument.entity_id).where(
                    SemanticSearchDocument.entity_type == "job",
                    SemanticSearchDocument.model_revision == _SEMANTIC_MODEL_KEY,
                )),
            )).all()
        application_status_by_job = {int(job_id): status for job_id, status in application_rows}
        if application_status_by_job:
            application_winners, _, _ = _scoped_candidate_winners(
                session, list(application_status_by_job), latitude, longitude, radius_km,
                place, country, location_scope, state, employer_country,
            )
            for job_id, _scope in application_winners:
                status = application_status_by_job.get(job_id)
                if status in application_counts:
                    application_counts[status] += 1
    complete_scan = fetch_offset >= candidate_count
    total = len(ordered_ids) if complete_scan else candidate_count
    page_ids = ordered_ids[offset:offset + limit]
    jobs = session.scalars(select(Job).where(Job.id.in_(page_ids)).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations),
    )).unique().all() if page_ids else []
    jobs_by_id = {job.id: job for job in jobs}
    items = []
    for job_id in page_ids:
        job = jobs_by_id.get(job_id)
        if job is None:
            continue
        item = _job_json(job)
        item["match_kind"] = scopes[job_id].get("match_kind")
        item["geography"] = scopes[job_id]
        item["semantic_score"] = round(1 - score_by_id.get(job_id, 1.0), 4)
        if profile is not None:
            item["profile_id"] = profile.id
            item["profile_name"] = profile.name
            application = applications_by_job.get(job_id)
            item["application"] = application_json(application) if application else None
        items.append(item)
    return {
        "items": items, "total": total, "total_is_upper_bound": not complete_scan,
        "offset": offset, "limit": limit,
        "profile_id": profile.id if profile else None,
        "profile_ids": [profile.id] if profile else [],
        "application_counts": application_counts,
        "counts": {"source": candidate_count, "scoped": len(winners),
                   "filtered": {**dict.fromkeys(("profile_conflict", "below_score", "uncertain"), 0),
                                **geo_filtered},
                   "duplicates_removed": max(0, scoped_count - len(winners)),
                   "recommended": 0, "possible": 0, "unlikely": 0},
        "coverage": {"source_count": candidate_count,
                     "note": "Observed active indexed postings; semantic results are ranked by text similarity."},
        "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km,
                     "city": place, "country_code": country},
        "location_scope": location_scope,
        "rule": ("work location in Germany, with a German-employer fallback when location is missing or unspecified" if employer_country else
                 "work location in " + state + ", with an employer-state fallback when location is missing or unspecified" if state else
                 ("explicitly remote within country scope" if location_scope == "remote" else
                  "mapped or parsed work location within radius" if location_scope == "area" else
                  "mapped or parsed work location within radius OR explicitly remote within country scope")),
        "state": state, "country": employer_country, "search_mode": "semantic",
        "semantic_window": fetch_offset, "semantic_candidates": len(candidate_ids),
        "snapshot_stale": False,
    }


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
              country_code: Annotated[str | None, Query(pattern=r'^[A-Za-z]{2}$')] = None,
              state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = _canonical_state(state)
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
    vector = (_query_embedding(normalized_query)
              if normalized_query and not IS_SQLITE and _semantic_index_ready(session, "job") else None)
    if vector is not None:
        semantic_page = _semantic_jobs_page(
            session, vector,
            latitude=latitude, longitude=longitude, radius_km=radius_km,
            place=place, country=scope_country, location_scope=location_scope,
            work_style=work_style, company_id=company_id,
            state=state, employer_country=employer_country, profile=tracking_profile,
            offset=offset, limit=limit, job_status=job_status,
        )
        if semantic_page["semantic_candidates"]:
            semantic_page.pop("semantic_candidates", None)
            return semantic_page
    # Browse snapshots are profileless; profile matching is handled separately
    # and cannot delay or filter ordinary search.
    generation = _search_generation(session)
    criteria = {
        'matching_version': MATCHING_VERSION,
        'geography_version': _WORK_LOCATION_GEOGRAPHY_VERSION,
        # Expiry is time based, so rotate cache keys every 15 minutes even if
        # no crawler has changed the underlying row generation.
        'freshness_bucket': int(utcnow().timestamp() // _SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS),
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
    snapshot_criteria = {name: value for name, value in criteria.items() if name != 'sort'}
    snapshot = None
    snapshot_stale = False
    if snapshot_id is not None:
        snapshot = session.get(JobSearchSnapshot, snapshot_id)
        if snapshot is None or not _search_criteria_match(
                (snapshot.payload or {}).get('criteria'), snapshot_criteria):
            raise HTTPException(status_code=409, detail='Search snapshot expired or criteria changed; restart from page one')
        current_key = _search_key(generation, snapshot_criteria)
        if snapshot.generation != generation or snapshot.key != current_key:
            current_snapshot = session.get(JobSearchSnapshot, current_key)
            if current_snapshot is not None:
                snapshot = current_snapshot
            else:
                latest_snapshot = _latest_stale_snapshot(session, generation, snapshot_criteria)
                if latest_snapshot is None:
                    raise HTTPException(status_code=409, detail='Search snapshot expired or criteria changed; restart from page one')
                snapshot = latest_snapshot
                snapshot_stale = True
                _enqueue_search_snapshot_refresh(session, snapshot_criteria)
        key = snapshot.key
        generation = snapshot.generation
    else:
        key = _search_key(generation, snapshot_criteria)
        snapshot = session.get(JobSearchSnapshot, key)
    if snapshot is None:
        latest_snapshot = _latest_stale_snapshot(session, generation, snapshot_criteria)
        if latest_snapshot is not None:
            snapshot = latest_snapshot
            key = snapshot.key
            generation = snapshot.generation
            snapshot_stale = True
            _enqueue_search_snapshot_refresh(session, snapshot_criteria)
        else:
            snapshot = _materialize_search_snapshot(
                session, profiles, key, generation, snapshot_criteria, thresholds,
            ) if IS_SQLITE else None
            if snapshot is None:
                _enqueue_search_snapshot_refresh(session, snapshot_criteria)
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
        JobSearchResult.snapshot_key == key, Job.is_active.is_(True))
    result_rows = select(JobSearchResult, Job).join(Job, Job.id == JobSearchResult.job_id).where(
        JobSearchResult.snapshot_key == key, Job.is_active.is_(True))
    if job_status is not None:
        application_join = and_(JobApplication.profile_id == tracking_profile.id,
                                JobApplication.job_id == JobSearchResult.job_id)
        result_count = result_count.join(JobApplication, application_join).where(
            JobApplication.status == job_status)
        result_rows = result_rows.join(JobApplication, application_join).where(
            JobApplication.status == job_status)
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
    selected_rows = session.execute(result_rows.options(
        joinedload(Job.feed).joinedload(JobFeed.company),
        joinedload(Job.employer_company), selectinload(Job.locations),
    ).order_by(*result_order).offset(offset).limit(limit)).unique().all()
    selected_results = [(row[0], row[1], None) for row in selected_rows]
    applications_by_job = {}
    if tracking_profile and selected_results:
        selected_job_ids = [result.job_id for result, _, _ in selected_results]
        applications_by_job = {item.job_id: item for item in session.scalars(select(JobApplication).where(
            JobApplication.profile_id == tracking_profile.id,
            JobApplication.job_id.in_(selected_job_ids),
        ))}
    items = []
    for result_row, job, match_row in selected_results:
        item = _job_json(job)
        item['match_kind'] = result_row.scope.get('match_kind')
        item['geography'] = result_row.scope
        if tracking_profile is not None:
            item['profile_id'] = tracking_profile.id
            item['profile_name'] = tracking_profile.name
            application = applications_by_job.get(job.id)
            item['application'] = application_json(application) if application else None
        items.append(item)
    application_counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    if tracking_profile:
        count_statement = select(JobApplication.status, func.count()).select_from(
            JobSearchResult).join(Job, Job.id == JobSearchResult.job_id).join(JobApplication, and_(
                JobApplication.profile_id == tracking_profile.id,
                JobApplication.job_id == JobSearchResult.job_id,
            )).where(JobSearchResult.snapshot_key == key, Job.is_active.is_(True))
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
    applied_events = session.execute(select(
        ApplicationMomentumEvent.job_id,
        ApplicationMomentumEvent.occurred_at,
        ApplicationMomentumEvent.is_legacy,
    ).where(ApplicationMomentumEvent.event_type == 'applied')
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

    interview_count = session.scalar(select(func.count(func.distinct(ApplicationMomentumEvent.job_id))).where(
        ApplicationMomentumEvent.event_type == 'interview',
    )) or 0
    jobs_won = session.scalar(select(func.count(func.distinct(ApplicationMomentumEvent.job_id))).where(
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
    if session.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail='Job not found')
    previous_status = session.scalar(select(JobApplication.status).where(
        JobApplication.profile_id == profile_id, JobApplication.job_id == job_id))
    # Atomic upsert makes repeat saves and simultaneous requests safe on both stores.
    if session.get_bind().dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    now = utcnow()
    statement = insert(JobApplication).values(profile_id=profile_id, job_id=job_id,
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
        'interview': 'interview',
        'accepted': 'accepted',
    }.get(request.status)
    if request.action == 'stage' and momentum_event_type and previous_status != request.status:
        # Count a profile/job only on its first move into Waiting for reply.
        already_counted = momentum_event_type == 'applied' and session.scalar(select(ApplicationMomentumEvent.id).where(
            ApplicationMomentumEvent.profile_id == profile_id,
            ApplicationMomentumEvent.job_id == job_id,
            ApplicationMomentumEvent.event_type == 'applied',
        ).limit(1)) is not None
        if not already_counted:
            session.add(ApplicationMomentumEvent(
                profile_id=profile_id,
                job_id=job_id,
                event_type=momentum_event_type,
                occurred_at=now,
            ))
    session.commit()
    application = session.scalar(select(JobApplication).where(
        JobApplication.profile_id == profile_id, JobApplication.job_id == job_id)
        .execution_options(populate_existing=True))
    return application_json(application)


@app.get('/api/v1/profiles/{profile_id}/applications')
def list_job_applications(profile_id: int,
                          status: ApplicationStatus | None = None,
                          offset: Annotated[int, Query(ge=0)] = 0,
                          limit: Annotated[int, Query(ge=1, le=100)] = 40,
                          session: Session = Depends(get_session)) -> dict[str, Any]:
    require_profile(profile_id, session)
    counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    counts.update(dict(session.execute(select(JobApplication.status, func.count()).where(
        JobApplication.profile_id == profile_id).group_by(JobApplication.status)).all()))
    statement = select(JobApplication).where(JobApplication.profile_id == profile_id)
    if status is not None:
        statement = statement.where(JobApplication.status == status)
    applications = session.scalars(statement.options(
        joinedload(JobApplication.job).joinedload(Job.feed).joinedload(JobFeed.company),
        joinedload(JobApplication.job).joinedload(Job.employer_company),
        joinedload(JobApplication.job).selectinload(Job.locations))
        .order_by(JobApplication.updated_at.desc(), JobApplication.id.desc())
        .offset(offset).limit(limit)).unique().all()
    items = []
    for application in applications:
        item = _job_json(application.job)
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

    # Rebuild selected profiles together so a shared active-job scan can fill
    # their indexes and create New states for newly visible matches.
    try:
        index_rebuilt = ensure_profile_match_indexes(session, profiles)
    except ProfileIndexChanged as error:
        raise HTTPException(status_code=409, detail='Profile changed during search; retry the request') from error
    if index_rebuilt:
        session.commit()

    counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    count_rows = session.execute(select(JobApplication.status, func.count()).where(
        JobApplication.profile_id.in_(selected_ids)).group_by(JobApplication.status)).all()
    counts.update(dict(count_rows))
    statement = select(JobApplication, Job).join(Job, Job.id == JobApplication.job_id).outerjoin(
        ProfileJobMatch, and_(ProfileJobMatch.profile_id == JobApplication.profile_id,
                              ProfileJobMatch.job_id == JobApplication.job_id)
    ).where(
        JobApplication.profile_id.in_(selected_ids))
    if selected_statuses:
        statement = statement.where(JobApplication.status.in_(selected_statuses))
    if sort == 'posted':
        statement = statement.order_by(Job.date_posted.desc().nullslast(),
                                       ProfileJobMatch.match_score.desc().nullslast(),
                                       Job.first_seen_at.desc(), JobApplication.id.desc())
    elif sort == 'discovered':
        statement = statement.order_by(Job.first_seen_at.desc(),
                                       ProfileJobMatch.match_score.desc().nullslast(),
                                       Job.date_posted.desc().nullslast(), JobApplication.id.desc())
    else:
        statement = statement.order_by(ProfileJobMatch.match_score.desc().nullslast(),
                                       Job.first_seen_at.desc(), Job.date_posted.desc().nullslast(),
                                       JobApplication.id.desc())
    total_statement = select(func.count()).select_from(JobApplication).where(
        JobApplication.profile_id.in_(selected_ids))
    if selected_statuses:
        total_statement = total_statement.where(JobApplication.status.in_(selected_statuses))
    total = session.scalar(total_statement) or 0
    rows = session.execute(statement.options(
        joinedload(JobApplication.profile),
        joinedload(JobApplication.job).joinedload(Job.feed).joinedload(JobFeed.company),
        joinedload(JobApplication.job).joinedload(Job.employer_company),
        joinedload(JobApplication.job).selectinload(Job.locations)
    ).offset(offset).limit(limit)).unique().all()
    profiles_by_id = {profile.id: profile for profile in profiles}
    items = []
    for application, job in rows:
        item = _job_json(job)
        item['application'] = application_json(application)
        item['profile_id'] = application.profile_id
        item['profile_name'] = profiles_by_id[application.profile_id].name
        item['profile_match'] = _profile_match(job, profiles_by_id[application.profile_id], None)
        items.append(item)
    return {'profile_ids': selected_ids, 'items': items, 'counts': counts,
            'total': total,
            'offset': offset, 'limit': limit, 'sort': sort}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session),
            profile_id: Annotated[int | None, Query(ge=1)] = None,
            latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
            longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
            radius_km: Annotated[float | None, Query(gt=0, le=200)] = None,
            place: Annotated[str | None, Query(max_length=120)] = None,
            country_code: Annotated[str | None, Query(pattern=r'^[A-Za-z]{2}$')] = None,
            state: str | None = None, country: str | None = None) -> dict[str, Any]:
    state = _canonical_state(state)
    employer_country = _canonical_country(country)
    statement = select(Job).options(joinedload(Job.feed).joinedload(JobFeed.company),
                                    joinedload(Job.employer_company),
                                    selectinload(Job.locations)).where(Job.id == job_id)
    job = session.scalars(statement).unique().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = _job_json(job, include_description=True)
    if employer_country and profile_id is None:
        data['geography'] = _result_scope(job, 0, 0, 15, None, None, state, employer_country)
    if profile_id is not None:
        profile = require_profile(profile_id, session)
        resolved = _resolved_search_area(profile, latitude, longitude, radius_km, place, country_code)
        scope = _result_scope(job, resolved['latitude'], resolved['longitude'],
                                 resolved['radius_km'], resolved['place'],
                                 resolved['country'] if resolved['profile_area'] or isinstance(country_code, str) else None, state, employer_country) if state or employer_country or resolved['profile_area'] or resolved['explicit'] else None
        data['geography'] = scope
        data['profile_match'] = _profile_match(job, profile, scope)
        application = session.scalar(select(JobApplication).where(
            JobApplication.profile_id == profile.id, JobApplication.job_id == job.id))
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
    all_jobs = _deduplicate_jobs(source_jobs)
    active_jobs = _deduplicate_jobs([job for job in all_jobs if job.is_active])
    data = _company_json(company, float("inf"), len(active_jobs))
    data["feeds"] = [_feed_json(feed) for feed in company.feeds]
    job_data = [_job_json(job) for job in all_jobs]
    if profile is not None and job_data:
        applications = {row.job_id: application_json(row) for row in session.scalars(select(JobApplication).where(
            JobApplication.profile_id == profile.id,
            JobApplication.job_id.in_([item["id"] for item in job_data]),
        ))}
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
    feeds = session.scalars(select(JobFeed).options(joinedload(JobFeed.company)).order_by(
        Company.name.asc(), JobFeed.id.asc()).join(JobFeed.company)).all()
    return {"items": [_managed_feed_json(feed) for feed in feeds], "total": len(feeds)}


@app.post("/api/v1/feeds", status_code=201)
def create_managed_feed(request: ManagedFeedCreate,
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    source_url = _managed_page_url(request.feed_url)
    source_config = _managed_feed_config(session, request, request.provider)
    source = _managed_feed_source(source_url, request.provider, source_config)
    website_url = _managed_page_url(request.website_url, "Company website") if request.website_url else None
    company = session.get(Company, request.company_id) if request.company_id else None
    if request.company_id and company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if company is None:
        company_name = (request.company_name or "").strip()
        if len(company_name) < 2:
            if request.provider == "arbeitsagentur":
                company_name = "Agentur für Arbeit"
            else:
                host = (urlsplit(source["board_url"]).hostname or "job source").casefold().removeprefix("www.")
                company_name = f"Jobs · {host}"
        normalized = _key(company_name)
        source_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        company = session.scalar(select(Company).where(
            Company.source == "managed_feed", Company.source_id == source_id))
        if company is None:
            website_host = urlsplit(website_url).hostname if website_url else None
            company = Company(source="managed_feed", source_id=source_id,
                              name=company_name, website_url=website_url,
                              domain=website_host, category="job_board_source",
                              source_url=source["board_url"], career_url=source["board_url"],
                              career_status="jobs_feed_found")
            session.add(company)
            session.flush()
        elif website_url:
            company.website_url = website_url
            if company.domain_match_method != "manual":
                company.domain = urlsplit(website_url).hostname
    duplicate = session.scalar(select(JobFeed).where(
        JobFeed.company_id == company.id, JobFeed.provider == source["provider"],
        JobFeed.feed_url == source["feed_url"]))
    if duplicate:
        raise HTTPException(status_code=409, detail="This page is already managed for this company")
    now = utcnow()
    feed = JobFeed(company_id=company.id, provider=source["provider"], tenant=source["tenant"],
                   source_config=source_config, board_url=source["board_url"], feed_url=source["feed_url"], status="pending",
                   enabled=True, job_count=0,
                   next_scan_at=now if source["provider"] != "arbeitsagentur" else None)
    session.add(feed)
    session.flush()
    if source["provider"] == "arbeitsagentur":
        _schedule_managed_arbeitsagentur_feed(session, feed, now)
    session.commit()
    session.refresh(feed)
    return _managed_feed_json(feed)


@app.patch("/api/v1/feeds/{feed_id}")
def update_managed_feed(feed_id: int, request: ManagedFeedUpdate,
                        session: Session = Depends(get_session)) -> dict[str, Any]:
    # Acquire the feed write lock before reading its revision. The no-op update
    # also serializes this path on SQLite, where SELECT FOR UPDATE is ignored.
    locked = session.execute(update(JobFeed).where(JobFeed.id == feed_id).values(
        source_revision=JobFeed.source_revision).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise HTTPException(status_code=404, detail="Job feed not found")
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
        session.execute(update(Job).where(Job.feed_id == feed.id, Job.is_active.is_(True)).values(
            is_active=False, closed_at=now).execution_options(synchronize_session=False))
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
    state = _canonical_state(state)
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
