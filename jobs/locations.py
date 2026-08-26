from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Any

import httpx
from django.conf import settings
from django.db.models import Q

from .models import GermanPlace
from .places import format_place_label, normalize_place_text


class LocationLookupError(RuntimeError):
    """The configured location provider could not answer a user search."""


_rate_lock = threading.Lock()
_last_request_at = 0.0


class NominatimLocationProvider:
    """Fetch user-requested German locations and cache the returned place records."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        endpoint: str | None = None,
        user_agent: str | None = None,
        min_interval_seconds: float | None = None,
    ) -> None:
        request_user_agent = user_agent or str(
            getattr(
                settings,
                "LOCATION_USER_AGENT",
                "hiring-scraper/0.1 (self-hosted location lookup)",
            )
        )
        self._client = client or httpx.Client(
            timeout=float(getattr(settings, "LOCATION_LOOKUP_TIMEOUT_SECONDS", 10)),
            headers={"User-Agent": request_user_agent},
        )
        self._owns_client = client is None
        self.endpoint = endpoint or str(
            getattr(settings, "LOCATION_API_URL", "https://nominatim.openstreetmap.org/search")
        )
        self.min_interval_seconds = (
            min_interval_seconds
            if min_interval_seconds is not None
            else float(getattr(settings, "LOCATION_MIN_REQUEST_INTERVAL_SECONDS", 1.0))
        )

    def search(self, query: str) -> list[GermanPlace]:
        normalized_query = normalize_place_text(query)
        if not normalized_query:
            return []
        _wait_for_provider_rate_limit(self.min_interval_seconds)
        try:
            response = self._client.get(
                self.endpoint,
                params={
                    "q": query.strip(),
                    "format": "jsonv2",
                    "addressdetails": "1",
                    "countrycodes": "de",
                    "limit": "10",
                    "accept-language": "en",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise LocationLookupError(
                "The location provider is unavailable. Try again in a moment."
            ) from error
        if not isinstance(payload, list):
            raise LocationLookupError("The location provider returned an invalid response.")
        places: list[GermanPlace] = []
        for raw_result in payload:
            if not isinstance(raw_result, dict):
                continue
            place = self._save_result(raw_result)
            if place is not None:
                places.append(place)
        return places

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _save_result(self, raw_result: dict[str, Any]) -> GermanPlace | None:
        address = raw_result.get("address")
        address_data = address if isinstance(address, dict) else {}
        country_code = _string(address_data.get("country_code")).casefold()
        if country_code != "de":
            return None
        latitude = _coordinate(raw_result.get("lat"), minimum=-90, maximum=90)
        longitude = _coordinate(raw_result.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None:
            return None
        name = _first_string(
            address_data,
            "city",
            "town",
            "municipality",
            "village",
            "hamlet",
            "suburb",
        )
        if not name:
            display_name = _string(raw_result.get("display_name"))
            name = display_name.split(",", 1)[0].strip()
        if not name:
            return None
        postal_code = _string(address_data.get("postcode"))
        admin_area = _first_string(address_data, "state", "state_district", "region", "county")
        source_id = _source_id(raw_result, name=name, latitude=latitude, longitude=longitude)
        result_type = _string(raw_result.get("type")).casefold()
        source_kind = (
            GermanPlace.SourceKind.POSTAL_CODE
            if result_type in {"postcode", "postalcode"}
            else GermanPlace.SourceKind.CITY
        )
        place, _ = GermanPlace.objects.update_or_create(
            source_id=source_id,
            defaults={
                "name": name[:200],
                "normalized_name": normalize_place_text(name)[:200],
                "postal_code": postal_code[:12],
                "admin_area": admin_area[:200],
                "latitude": latitude,
                "longitude": longitude,
                "population": None,
                "source_kind": source_kind,
                "source_snapshot": "nominatim",
            },
        )
        return place


def search_locations(
    query: str, *, provider: NominatimLocationProvider | None = None
) -> list[GermanPlace]:
    """Use cached results when possible, otherwise query the live provider."""
    normalized_query = normalize_place_text(query)
    if not normalized_query:
        return []
    cached = list(_cached_places(query, normalized_query))
    if cached:
        return cached
    location_provider = provider or NominatimLocationProvider()
    try:
        return location_provider.search(query)
    finally:
        if provider is None:
            location_provider.close()


def location_result(place: GermanPlace) -> dict[str, int | str]:
    return {"id": place.pk, "label": format_place_label(place)}


def _cached_places(query: str, normalized_query: str):  # type: ignore[no-untyped-def]
    return GermanPlace.objects.filter(
        Q(normalized_name__contains=normalized_query) | Q(postal_code__startswith=query.strip())
    ).order_by("name", "admin_area", "postal_code")[:50]


def _wait_for_provider_rate_limit(min_interval_seconds: float) -> None:
    global _last_request_at
    interval = max(0.0, min_interval_seconds)
    with _rate_lock:
        delay = interval - (time.monotonic() - _last_request_at)
        if delay > 0:
            time.sleep(delay)
        _last_request_at = time.monotonic()


def _source_id(raw_result: dict[str, Any], *, name: str, latitude: float, longitude: float) -> str:
    osm_type = _string(raw_result.get("osm_type")).casefold()
    osm_id = raw_result.get("osm_id")
    if osm_type and isinstance(osm_id, int | str) and str(osm_id):
        return f"nominatim:{osm_type}:{osm_id}"
    identity = f"{name}\x1f{latitude}\x1f{longitude}"
    return "nominatim:result:" + hashlib.sha256(identity.encode()).hexdigest()


def _first_string(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        result = _string(value.get(key))
        if result:
            return result
    return ""


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if not minimum <= number <= maximum:
        return None
    return number
