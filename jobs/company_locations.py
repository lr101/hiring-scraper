from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from django.conf import settings

from .company_discovery import (
    CompanyDiscoveryError,
    DiscoveredCompany,
    discover_company,
    normalize_domain,
)
from .locations import _wait_for_provider_rate_limit
from .models import GermanPlace
from .network import UnsafeNetworkAddress, validate_public_hostname


class CompanyLocationLookupError(RuntimeError):
    """The company-location provider could not answer a city search."""


@dataclass(frozen=True, slots=True)
class CompanyCandidate:
    """A mapped business that advertises a public website."""

    name: str
    domain: str
    website_url: str
    source_id: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True, slots=True)
class CompanyLocationDiscovery:
    """Results from finding and reading company websites near a selected place."""

    companies: tuple[DiscoveredCompany, ...]
    unreadable_websites: int = 0


class OverpassCompanyProvider:
    """Find mapped offices and industrial businesses with websites near a place."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        endpoint: str | None = None,
        user_agent: str | None = None,
        min_interval_seconds: float | None = None,
        max_results: int | None = None,
    ) -> None:
        request_user_agent = user_agent or str(
            getattr(
                settings,
                "COMPANY_LOCATION_USER_AGENT",
                "hiring-scraper/0.1 (self-hosted company lookup)",
            )
        )
        self._client = client or httpx.Client(
            timeout=float(getattr(settings, "COMPANY_LOCATION_LOOKUP_TIMEOUT_SECONDS", 20)),
            headers={"User-Agent": request_user_agent},
        )
        self._owns_client = client is None
        self.endpoint = endpoint or str(
            getattr(settings, "COMPANY_LOCATION_API_URL", "https://overpass-api.de/api/interpreter")
        )
        self.min_interval_seconds = (
            min_interval_seconds
            if min_interval_seconds is not None
            else float(getattr(settings, "COMPANY_LOCATION_MIN_REQUEST_INTERVAL_SECONDS", 2.0))
        )
        self.max_results = max(
            1,
            max_results
            if max_results is not None
            else int(getattr(settings, "COMPANY_LOCATION_MAX_RESULTS", 25)),
        )

    def search(self, place: GermanPlace, *, radius_km: int) -> list[CompanyCandidate]:
        _wait_for_provider_rate_limit(
            self.min_interval_seconds,
            state_path=getattr(
                settings,
                "COMPANY_LOCATION_RATE_LIMIT_STATE_PATH",
                "/tmp/hiring-scraper-company-location-rate-limit",
            ),
        )
        query = _overpass_query(place=place, radius_km=radius_km)
        try:
            endpoint_host = urlsplit(self.endpoint).hostname
            if urlsplit(self.endpoint).scheme.casefold() != "https" or endpoint_host is None:
                raise CompanyLocationLookupError(
                    "The company-location provider URL must use HTTPS."
                )
            validate_public_hostname(endpoint_host)
            response = self._client.get(self.endpoint, params={"data": query})
            response.raise_for_status()
            payload = response.json()
        except CompanyLocationLookupError:
            raise
        except UnsafeNetworkAddress as error:
            raise CompanyLocationLookupError(
                "The company-location provider does not resolve to a public address."
            ) from error
        except (httpx.HTTPError, TypeError, ValueError) as error:
            raise CompanyLocationLookupError(
                "The company-location provider is unavailable. Try again in a moment."
            ) from error
        return _candidates_from_payload(payload, max_results=self.max_results)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def discover_companies_in_place(
    place: GermanPlace,
    *,
    radius_km: int,
    provider: OverpassCompanyProvider | None = None,
) -> CompanyLocationDiscovery:
    """Find website-backed companies near a place and read their career pages."""
    location_provider = provider or OverpassCompanyProvider()
    try:
        candidates = location_provider.search(place, radius_km=radius_km)
    finally:
        if provider is None:
            location_provider.close()
    discoveries: list[DiscoveredCompany] = []
    unreadable_websites = 0
    for candidate in candidates:
        try:
            discoveries.append(discover_company(candidate.website_url))
        except CompanyDiscoveryError:
            unreadable_websites += 1
    return CompanyLocationDiscovery(
        companies=tuple(discoveries), unreadable_websites=unreadable_websites
    )


def _overpass_query(*, place: GermanPlace, radius_km: int) -> str:
    radius_meters = min(max(radius_km, 1), 500) * 1000
    latitude = f"{place.latitude:.6f}"
    longitude = f"{place.longitude:.6f}"
    return f"""[out:json][timeout:25];
(
  nwr(around:{radius_meters},{latitude},{longitude})["name"]["office"]["website"];
  nwr(around:{radius_meters},{latitude},{longitude})["name"]["office"]["contact:website"];
  nwr(around:{radius_meters},{latitude},{longitude})["name"]["industrial"]["website"];
  nwr(around:{radius_meters},{latitude},{longitude})["name"]["industrial"]["contact:website"];
  nwr(around:{radius_meters},{latitude},{longitude})["name"]["company"]["website"];
  nwr(around:{radius_meters},{latitude},{longitude})["name"]["company"]["contact:website"];
);
out center tags;"""


def _candidates_from_payload(payload: Any, *, max_results: int) -> list[CompanyCandidate]:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise CompanyLocationLookupError("The company-location provider returned invalid data.")
    candidates: list[CompanyCandidate] = []
    seen_domains: set[str] = set()
    for element in payload["elements"]:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags")
        if not isinstance(tags, dict):
            continue
        website = _website_from_tags(tags)
        name = _first_string(tags, "name", "operator", "brand")
        if website is None or not name:
            continue
        domain, website_url = website
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        source_id = _source_id(element)
        coordinates = _coordinates(element)
        candidates.append(
            CompanyCandidate(
                name=name[:200],
                domain=domain,
                website_url=website_url,
                source_id=source_id,
                latitude=coordinates[0],
                longitude=coordinates[1],
            )
        )
        if len(candidates) >= max_results:
            break
    return candidates


def _website_from_tags(tags: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("website", "contact:website", "operator:website"):
        raw_value = tags.get(key)
        if not isinstance(raw_value, str):
            continue
        for value in raw_value.split(";"):
            value = value.strip()
            if not value:
                continue
            candidate = value if "://" in value else f"https://{value}"
            try:
                parsed = urlsplit(candidate)
                domain = normalize_domain(candidate)
            except ValueError:
                continue
            if parsed.scheme.casefold() not in {"http", "https"}:
                continue
            return domain, urlunsplit(("https", domain, parsed.path or "/", "", ""))
    return None


def _coordinates(element: dict[str, Any]) -> tuple[float | None, float | None]:
    center = element.get("center")
    if not isinstance(center, dict):
        center = element
    return _coordinate(center.get("lat")), _coordinate(center.get("lon"))


def _coordinate(value: Any) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    return coordinate if math.isfinite(coordinate) else None


def _source_id(element: dict[str, Any]) -> str:
    element_type = _first_string(element, "type") or "object"
    element_id = element.get("id")
    return f"overpass:{element_type}:{element_id}" if element_id is not None else "overpass:unknown"


def _first_string(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""
