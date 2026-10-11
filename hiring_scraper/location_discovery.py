"""Find named local employers and conservative homepage evidence from public OSM data."""
from __future__ import annotations

import http.client
import json
import logging
import math
import os
import re
import time
import urllib.error
from tempfile import TemporaryDirectory
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from hiring_scraper.http import Client
from hiring_scraper.crawl_policy import RESPECT_ROBOTS, ORIGIN_PACER
from hiring_scraper.website_discovery import configured_website_search, discover_missing_websites
from hiring_scraper.geography import build_osm_radius_query, build_osm_state_query, build_osm_state_bounds_query, normalize_german_state, haversine_m, osm_element_coordinates
from hiring_scraper.osm_websites import (
    fetch_wikidata_entities,
    is_public_hostname,
    referenced_entity_ids,
    resolve_osm_websites,
    select_website_enrichments,
)


LOG = logging.getLogger(__name__)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "HiringScraper/0.2 (German company and homepage discovery)"
OVERPASS_DELAY_SECONDS = max(1.0, float(os.getenv("OVERPASS_REQUEST_DELAY_SECONDS", "2")))
OVERPASS_RETRIES = min(5, max(0, int(os.getenv("OVERPASS_RETRIES", "2"))))
OVERPASS_RETRY_DELAY_SECONDS = min(
    10.0, max(0.0, float(os.getenv("OVERPASS_RETRY_DELAY_SECONDS", "1"))))
OVERPASS_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_OVERPASS_LOCK = Lock()
_LAST_OVERPASS_REQUEST = 0.0
_HOMEPAGE_PACER = ORIGIN_PACER


class LocationSearchCancelled(RuntimeError):
    """The caller cancelled the scan or no longer owns its lease."""


class OverpassIncompleteError(RuntimeError):
    """An otherwise successful response contains incomplete query results."""


def _validate_overpass_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("OpenStreetMap returned an invalid company list")
    if payload.get("remark"):
        message = f"OpenStreetMap search was incomplete: {payload['remark']}"
        if any(word in str(payload['remark']).casefold() for word in ('timed out', 'timeout', 'out of memory')):
            raise OverpassIncompleteError(message)
        raise RuntimeError(message)


def is_retryable_overpass_error(error: Exception) -> bool:
    """Return whether a failed Overpass request may succeed on a later attempt."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in OVERPASS_RETRYABLE_STATUSES
    return isinstance(error, (OverpassIncompleteError, urllib.error.URLError, OSError, http.client.HTTPException))


def _wait_for_overpass_slot() -> None:
    """Keep every Overpass attempt within the shared request pacing limit."""
    global _LAST_OVERPASS_REQUEST
    with _OVERPASS_LOCK:
        wait = OVERPASS_DELAY_SECONDS - (time.monotonic() - _LAST_OVERPASS_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_OVERPASS_REQUEST = time.monotonic()


def _overpass_retry_delay(attempt: int, retry_after: str | None = None) -> float:
    delay = min(10.0, OVERPASS_RETRY_DELAY_SECONDS * (attempt + 1))
    if not retry_after:
        return delay
    try:
        requested = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            requested = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return delay
    return max(delay, min(300.0, max(0.0, requested)))


def _safe_homepage(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        hostname = parsed.hostname.casefold().rstrip(".")
        try:
            dns_name = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        labels = dns_name.split(".")
        if (len(dns_name) > 253 or len(labels) < 2 or
                any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels) or not is_public_hostname(hostname)):
            return None
        return value
    except ValueError:
        return None


def _candidate(element: dict, latitude: float, longitude: float, radius_m: float | None) -> dict | None:
    point = osm_element_coordinates(element)
    tags = element.get("tags") or {}
    name = tags.get("name")
    if point is None or not isinstance(name, str) or not name.strip():
        return None
    distance = haversine_m(latitude, longitude, *point)
    if radius_m is not None and distance > radius_m:
        return None
    source_id = f"{element.get('type')}/{element.get('id')}"
    website = _safe_homepage(tags.get("website") or tags.get("contact:website"))
    from hiring_scraper.app.crawl_ranking import parse_employee_count
    employee_count = next((count for key in ('employees', 'staff_count', 'company:employees')
                           if (count := parse_employee_count(tags.get(key))) is not None), None)
    return {
        "employee_count": employee_count,
        "size_evidence": {'source': 'openstreetmap', 'source_url': f'https://www.openstreetmap.org/{source_id}',
                          'employee_count': employee_count} if employee_count else {},
        "source_id": source_id,
        "name": name.strip(),
        "website": website,
        "category": tags.get("office") or tags.get("craft") or tags.get("industrial"),
        "latitude": point[0],
        "longitude": point[1],
        "location_precision": "point" if element.get("type") == "node" else "feature_center",
        "location_label": tags.get("office") or tags.get("craft") or tags.get("industrial") or "Mapped business",
        "source_url": f"https://www.openstreetmap.org/{source_id}",
        "tags": tags,
    }


def resolve_location_candidates(payload: dict, latitude: float, longitude: float,
                                radius_m: float | None, *, entities: dict | None = None,
                                homepage_resolver: Callable | None = None,
                                progress_callback: Callable[..., bool | None] | None = None) -> list[dict]:
    """Parse an Overpass response and attach directly listed or verified Wikidata homepages."""
    _validate_overpass_payload(payload)

    candidates = []
    for element in payload["elements"]:
        if not isinstance(element, dict):
            continue
        row = _candidate(element, latitude, longitude, radius_m)
        if row:
            candidates.append(row)
    # Overpass can return the same object through more than one matching tag.
    candidates = list({row["source_id"]: row for row in candidates}.values())

    if progress_callback:
        progress_callback("Checking company website information",
                          companies_found=len(candidates),
                          homepages_found=sum(bool(row["website"]) for row in candidates))

    if entities is None:
        try:
            entities = fetch_wikidata_entities(referenced_entity_ids(payload))
        except Exception as error:  # Direct OSM website tags remain useful if Wikidata is unavailable.
            LOG.warning("could not look up linked public company homepages: %s", error)
            entities = {}
    suggestions = resolve_osm_websites(candidates, payload, entities or {})
    selected = select_website_enrichments(suggestions)
    for candidate in candidates:
        direct = candidate["website"]
        resolved = selected.get(candidate["source_id"])
        website = direct or (_safe_homepage(resolved.get("website_url")) if resolved else None)
        candidate["website_url"] = website
        candidate["domain"] = (urlsplit(website).hostname or "").casefold().removeprefix("www.") if website else None
        candidate["domain_match_method"] = (
            "osm_website_tag" if direct else resolved.get("method") if website and resolved else None
        )
        candidate["domain_evidence_url"] = (
            candidate["source_url"] if direct else resolved.get("evidence_url") if website and resolved else None
        )
    if homepage_resolver:
        homepage_resolver(candidates)
    for candidate in candidates:
        candidate.pop("website", None)
        # National inventory resolves homepages in a separately paced worker.
        if homepage_resolver:
            candidate.pop("tags", None)
    if progress_callback:
        progress_callback("Saving company and website results",
                          companies_found=len(candidates),
                          homepages_found=sum(bool(row["website_url"]) for row in candidates))
    return sorted(candidates, key=lambda row: (row["name"].casefold(), row["source_id"]))


def _fetch_overpass(query, transport, *, split_on_timeout=False):
    request = Request(
        OVERPASS_URL,
        data=urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    for attempt in range(OVERPASS_RETRIES + 1):
        _wait_for_overpass_slot()
        try:
            with transport(request, timeout=110) as response:
                response_limit = 20_000_000
                response_body = response.read(response_limit + 1)
                if len(response_body) > response_limit:
                    raise ValueError("OpenStreetMap response exceeded the company search size limit")
            payload = json.loads(response_body)
            _validate_overpass_payload(payload)
            return payload
        except urllib.error.HTTPError as error:
            if error.code not in OVERPASS_RETRYABLE_STATUSES or attempt == OVERPASS_RETRIES:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            error.close()
            failure = error
        except (OverpassIncompleteError, urllib.error.URLError, OSError, http.client.HTTPException) as error:
            if (split_on_timeout and isinstance(error, OverpassIncompleteError)) or attempt == OVERPASS_RETRIES:
                raise
            retry_after = None
            failure = error
        delay = _overpass_retry_delay(attempt, retry_after)
        if isinstance(failure, urllib.error.HTTPError) and failure.code == 429:
            delay = max(30.0, delay)
        LOG.warning("Overpass request failed attempt=%s/%s; retrying in %.1fs: %s",
                    attempt + 1, OVERPASS_RETRIES + 1, delay, failure)
        time.sleep(delay)


def resolve_missing_company_websites(candidates: list[dict], *, progress_callback=None,
                                     search_area_hint: str | None = None,
                                     max_companies: int | None = None,
                                     max_searches: int | None = None,
                                     max_requests: int | None = None) -> list[dict]:
    """Resolve missing company homepages with bounded, isolated network work."""
    if not any(not row.get("website_url") for row in candidates):
        return candidates
    search = configured_website_search()
    if max_requests is None:
        max_requests = min(1000, max(0, int(os.getenv("HOMEPAGE_DISCOVERY_MAX_REQUESTS", "180"))))
    if max_companies is None:
        max_companies = min(500, max(0, int(os.getenv("HOMEPAGE_DISCOVERY_MAX_COMPANIES", "100"))))
    if max_searches is None:
        max_searches = min(200, max(0, int(os.getenv("HOMEPAGE_DISCOVERY_MAX_SEARCHES", "50"))))
    with TemporaryDirectory(prefix="hiring-homepages-") as capture_dir:
        client = Client(capture_dir, timeout=8, delay=1, max_requests=max_requests,
                        user_agent=USER_AGENT, origin_pacer=_HOMEPAGE_PACER,
                        respect_robots=RESPECT_ROBOTS)
        decisions = discover_missing_websites(
            candidates, client, search=search, max_companies=max_companies,
            max_searches=max_searches, search_area_hint=search_area_hint,
            progress_callback=progress_callback)
        LOG.info("homepage verification checked=%s accepted=%s requests=%s search_enabled=%s",
                 len(decisions), sum(row["accepted"] for row in decisions), len(client.records), bool(search))
    return candidates


def state_map_bounds(state, transport):
    payload = _fetch_overpass(build_osm_state_bounds_query(state), transport)
    bounds = [element.get('bounds') for element in payload['elements'] if isinstance(element, dict)]
    if not bounds or any(not isinstance(bound, dict) for bound in bounds):
        raise ValueError('OpenStreetMap returned no state boundary bounds')
    try:
        south = min(float(b['minlat']) for b in bounds)
        west = min(float(b['minlon']) for b in bounds)
        north = max(float(b['maxlat']) for b in bounds)
        east = max(float(b['maxlon']) for b in bounds)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('OpenStreetMap returned invalid state boundary bounds') from error
    if not all(math.isfinite(v) for v in (south, west, north, east)) or not (47 <= south < north <= 56 and 5 <= west < east <= 16):
        raise ValueError('OpenStreetMap returned invalid German state boundary bounds')
    return south, west, north, east


def _fetch_state_map(state, transport, progress_callback):
    south, west, north, east = state_map_bounds(state, transport)
    height, width = math.ceil((north-south)/.5), math.ceil((east-west)/.5)
    pending = [((south+y*.5, west+x*.5, min(north, south+(y+1)*.5), min(east, west+(x+1)*.5)), 0)
               for y in range(height) for x in range(width)]
    elements = {}
    completed = 0
    while pending:
        box, depth = pending.pop(0)
        if progress_callback:
            progress_callback(f'Searching state map sections: {completed} complete, {len(pending)+1} remaining',
                              companies_found=len(elements))
        try:
            payload = _fetch_overpass(build_osm_state_query(state, bbox=box), transport, split_on_timeout=depth < 2)
        except OverpassIncompleteError as error:
            if depth >= 2:
                raise
            s, w, n, e = box
            midlat, midlon = (s+n)/2, (w+e)/2
            pending[0:0] = [(child, depth+1) for child in (
                (s,w,midlat,midlon), (s,midlon,midlat,e), (midlat,w,n,midlon), (midlat,midlon,n,e))]
            LOG.warning('Subdividing timed-out state map section %s: %s', box, error)
            continue
        for element in payload['elements']:
            if isinstance(element, dict):
                elements[(element.get('type'), element.get('id'))] = element
        completed += 1
    return {'elements': list(elements.values())}


def fetch_location_companies(latitude: float, longitude: float, radius_m: float,
                             *, state: str | None = None, opener=None,
                             progress_callback: Callable[..., bool | None] | None = None) -> list[dict]:
    """Fetch a bounded, location-centered company set from Overpass and enrich homepages."""
    if progress_callback:
        callback = progress_callback
        def report_progress(message, **counts):
            if callback(message, **counts) is False:
                raise LocationSearchCancelled('Location search was cancelled or its lease was lost')
        progress_callback = report_progress
    if progress_callback:
        progress_callback("Searching state-wide map listings" if state else "Searching nearby map listings")
    state = normalize_german_state(state) if state else None
    transport = opener or urlopen
    payload = (_fetch_state_map(state, transport, progress_callback) if state else
               _fetch_overpass(build_osm_radius_query(latitude, longitude, radius_m), transport))
    # A shared request cap includes robots and redirects. No search credentials are
    # required for email-domain verification; API lookup is enabled when configured.
    def enrich(candidates):
        if not any(not row.get("website_url") for row in candidates):
            return
        # The nearest mapped city's name is a search-area hint, never a source tag
        # or an acceptance criterion for a company whose address is missing.
        tagged = [row for row in candidates if isinstance((row.get('tags') or {}).get('addr:city'), str)
                  and row['tags']['addr:city'].strip()]
        nearest = min(tagged, key=lambda row: haversine_m(latitude, longitude,
                                                        row['latitude'], row['longitude'])) if tagged else None
        search_area_hint = state or (nearest['tags']['addr:city'].strip() + ' Region' if nearest else None)
        resolve_missing_company_websites(candidates, progress_callback=progress_callback,
                                         search_area_hint=search_area_hint)
    return resolve_location_candidates(payload, latitude, longitude, None if state else radius_m,
                                       homepage_resolver=enrich, progress_callback=progress_callback)
