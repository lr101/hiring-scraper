"""Find named local employers and conservative homepage evidence from public OSM data."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from threading import Lock
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from hiring_scraper.geography import build_osm_radius_query, haversine_m, osm_element_coordinates
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
_OVERPASS_LOCK = Lock()
_LAST_OVERPASS_REQUEST = 0.0


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


def _candidate(element: dict, latitude: float, longitude: float, radius_m: float) -> dict | None:
    point = osm_element_coordinates(element)
    tags = element.get("tags") or {}
    name = tags.get("name")
    if point is None or not isinstance(name, str) or not name.strip():
        return None
    distance = haversine_m(latitude, longitude, *point)
    if distance > radius_m:
        return None
    source_id = f"{element.get('type')}/{element.get('id')}"
    website = _safe_homepage(tags.get("website") or tags.get("contact:website"))
    return {
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
                                radius_m: float, *, entities: dict | None = None,
                                progress_callback: Callable[..., None] | None = None) -> list[dict]:
    """Parse an Overpass response and attach directly listed or verified Wikidata homepages."""
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("OpenStreetMap returned an invalid company list")
    if payload.get("remark"):
        raise RuntimeError(f"OpenStreetMap search was incomplete: {payload['remark']}")

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
        candidate.pop("website", None)
        candidate.pop("tags", None)
    if progress_callback:
        progress_callback("Saving company and website results",
                          companies_found=len(candidates),
                          homepages_found=sum(bool(row["website_url"]) for row in candidates))
    return sorted(candidates, key=lambda row: (row["name"].casefold(), row["source_id"]))


def fetch_location_companies(latitude: float, longitude: float, radius_m: float,
                             *, opener=None,
                             progress_callback: Callable[..., None] | None = None) -> list[dict]:
    """Fetch a bounded, location-centered company set from Overpass and enrich homepages."""
    if progress_callback:
        progress_callback("Searching nearby map listings")
    query = build_osm_radius_query(latitude, longitude, radius_m)
    request = Request(
        OVERPASS_URL,
        data=urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    global _LAST_OVERPASS_REQUEST
    with _OVERPASS_LOCK:
        wait = OVERPASS_DELAY_SECONDS - (time.monotonic() - _LAST_OVERPASS_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_OVERPASS_REQUEST = time.monotonic()
    with (opener or urlopen)(request, timeout=110) as response:
        payload = json.loads(response.read(20_000_000))
    return resolve_location_candidates(payload, latitude, longitude, radius_m,
                                       progress_callback=progress_callback)
