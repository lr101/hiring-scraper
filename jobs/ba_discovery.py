"""Typed, bounded access to the public Bundesagentur für Arbeit Jobsuche feed."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from django.conf import settings
from django.db import transaction

from .locations import _wait_for_provider_rate_limit
from .models import EmployerSignalSnapshot
from .network import UnsafeNetworkAddress, validate_public_hostname

DEFAULT_BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
DEFAULT_API_KEY = "jobboerse-jobsuche"
DEFAULT_RESULT_LIMIT = 100
DEFAULT_MAX_PAGES = 5

_GERMAN_COUNTRY_VALUES = {"de", "deu", "germany", "deutschland"}


@dataclass(frozen=True, slots=True)
class BAJobSignal:
    """An immutable, location-qualified signal from one BA job posting."""

    employer_name: str
    job_title: str
    location: str
    postal_code: str
    reference_number: str
    published_at: date | None = None

    @property
    def employer(self) -> str:
        return self.employer_name

    @property
    def title(self) -> str:
        return self.job_title

    @property
    def city(self) -> str:
        return self.location


@dataclass(frozen=True, slots=True)
class BAJobSearchPage:
    """One typed page returned by the BA Jobsuche endpoint."""

    signals: tuple[BAJobSignal, ...]
    total_results: int | None
    page: int
    size: int
    is_complete: bool

    @property
    def jobs(self) -> tuple[BAJobSignal, ...]:
        return self.signals


@dataclass(frozen=True, slots=True)
class EmployerHiringSignal:
    """Grouped BA activity for one normalized employer identity."""

    normalized_name: str
    employer_name: str
    total_active_jobs: int
    recent_jobs: int
    distinct_locations: tuple[str, ...]
    signals: tuple[BAJobSignal, ...]

    @property
    def active_jobs(self) -> int:
        return self.total_active_jobs


@dataclass(frozen=True, slots=True)
class EmployerDiscoveryResult:
    """The bounded result of one BA-backed employer discovery run."""

    employers: tuple[EmployerHiringSignal, ...]
    is_complete: bool
    requests_made: int
    error: str | None = None

    @property
    def total_active_jobs(self) -> int:
        return sum(employer.total_active_jobs for employer in self.employers)

    @property
    def summaries(self) -> tuple[EmployerHiringSignal, ...]:
        return self.employers


class JobsucheProviderError(RuntimeError):
    """The configured BA Jobsuche provider could not return a valid response."""


class ArbeitsagenturJobsucheClient:
    """Read German job signals from the BA Jobsuche API."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        result_limit: int | None = None,
        min_interval_seconds: float | None = None,
        rate_limit_state_path: str | None = None,
    ) -> None:
        self.base_url = _safe_base_url(
            base_url or str(getattr(settings, "BA_JOBS_API_BASE_URL", DEFAULT_BASE_URL))
        )
        self.api_key = api_key or str(getattr(settings, "BA_JOBS_API_KEY", DEFAULT_API_KEY))
        self.result_limit = _bounded_positive_int(
            result_limit
            if result_limit is not None
            else getattr(settings, "BA_JOBS_RESULT_LIMIT", DEFAULT_RESULT_LIMIT),
            field="result_limit",
        )
        self.min_interval_seconds = (
            min_interval_seconds
            if min_interval_seconds is not None
            else float(getattr(settings, "BA_JOBS_MIN_REQUEST_INTERVAL_SECONDS", 2.0))
        )
        self.rate_limit_state_path = rate_limit_state_path or str(
            getattr(
                settings,
                "BA_JOBS_RATE_LIMIT_STATE_PATH",
                "/tmp/hiring-scraper-ba-jobs-rate-limit",
            )
        )
        self._client = client or httpx.Client(
            timeout=timeout_seconds
            if timeout_seconds is not None
            else float(getattr(settings, "BA_JOBS_TIMEOUT_SECONDS", 30.0)),
            headers={
                "Accept": "application/json",
                "User-Agent": str(
                    getattr(
                        settings,
                        "BA_JOBS_USER_AGENT",
                        "hiring-scraper/0.1 (self-hosted BA job discovery)",
                    )
                ),
            },
        )
        self._owns_client = client is None
        self.requests_made = 0

    def search(
        self,
        *,
        city: str,
        radius_km: int,
        publication_age_days: int,
        offer_type: int = 1,
        include_temporary_agencies: bool = False,
        page: int = 1,
        size: int | None = None,
    ) -> BAJobSearchPage:
        """Fetch and parse one bounded page of German vacancy signals."""
        normalized_city = city.strip()
        if not normalized_city:
            raise ValueError("A city is required for BA Jobsuche discovery.")
        if radius_km < 0:
            raise ValueError("radius_km must not be negative.")
        if not 0 <= publication_age_days <= 100:
            raise ValueError("publication_age_days must be between 0 and 100.")
        if offer_type <= 0:
            raise ValueError("offer_type must be positive.")
        if page < 1:
            raise ValueError("page must be at least 1.")
        page_size = _bounded_positive_int(
            size if size is not None else self.result_limit,
            field="size",
        )
        params = {
            "wo": normalized_city,
            "umkreis": str(radius_km),
            "veroeffentlichtseit": str(publication_age_days),
            "zeitarbeit": str(include_temporary_agencies).lower(),
            "angebotsart": str(offer_type),
            "page": str(page),
            "size": str(page_size),
        }
        _wait_for_provider_rate_limit(
            self.min_interval_seconds,
            state_path=self.rate_limit_state_path,
        )
        self.requests_made += 1
        try:
            response = self._client.get(
                self.base_url + "/pc/v6/jobs",
                params=params,
                headers={"X-API-Key": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise JobsucheProviderError(
                "The Bundesagentur für Arbeit Jobsuche provider is unavailable."
            ) from error
        return _parse_search_page(payload, page=page, size=page_size)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class EmployerDiscoveryService:
    """Discover and optionally persist employer activity without account side effects."""

    def __init__(self, *, client: ArbeitsagenturJobsucheClient | None = None) -> None:
        self.client = client or ArbeitsagenturJobsucheClient()
        self._owns_client = client is None

    def discover(
        self,
        *,
        city: str,
        radius_km: int,
        publication_age_days: int,
        offer_type: int = 1,
        include_temporary_agencies: bool = False,
        max_pages: int | None = None,
        as_of: date | None = None,
    ) -> EmployerDiscoveryResult:
        """Fetch at most ``max_pages`` and return grouped signals."""
        page_limit = _bounded_positive_int(
            max_pages
            if max_pages is not None
            else getattr(settings, "BA_JOBS_MAX_PAGES", DEFAULT_MAX_PAGES),
            field="max_pages",
        )
        recent_since = (as_of or date.today()) - timedelta(days=publication_age_days)
        signals: list[BAJobSignal] = []
        is_complete = False
        try:
            for page_number in range(1, page_limit + 1):
                page = self.client.search(
                    city=city,
                    radius_km=radius_km,
                    publication_age_days=publication_age_days,
                    offer_type=offer_type,
                    include_temporary_agencies=include_temporary_agencies,
                    page=page_number,
                )
                signals.extend(page.signals)
                is_complete = page.is_complete
                if is_complete:
                    break
        except JobsucheProviderError as error:
            return EmployerDiscoveryResult(
                employers=(),
                is_complete=False,
                requests_made=self.client.requests_made,
                error=str(error),
            )
        return EmployerDiscoveryResult(
            employers=group_employer_signals(signals, recent_since=recent_since),
            is_complete=is_complete,
            requests_made=self.client.requests_made,
        )

    def discover_and_persist(
        self,
        *,
        city: str,
        radius_km: int,
        publication_age_days: int,
        offer_type: int = 1,
        include_temporary_agencies: bool = False,
        max_pages: int | None = None,
        as_of: date | None = None,
    ) -> EmployerDiscoveryResult:
        """Persist a successful snapshot and leave prior snapshots on provider failure."""
        result = self.discover(
            city=city,
            radius_km=radius_km,
            publication_age_days=publication_age_days,
            offer_type=offer_type,
            include_temporary_agencies=include_temporary_agencies,
            max_pages=max_pages,
            as_of=as_of,
        )
        if result.error is not None:
            return result
        with transaction.atomic():
            EmployerSignalSnapshot.objects.bulk_create(
                [
                    EmployerSignalSnapshot(
                        normalized_name=employer.normalized_name,
                        employer_name=employer.employer_name,
                        total_active_jobs=employer.total_active_jobs,
                        recent_jobs=employer.recent_jobs,
                        distinct_locations=list(employer.distinct_locations),
                        signals=[_signal_payload(signal) for signal in employer.signals],
                        query_city=city.strip(),
                        query_radius_km=radius_km,
                        publication_age_days=publication_age_days,
                        is_complete=result.is_complete,
                    )
                    for employer in result.employers
                ]
            )
        return result

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


def group_employer_signals(
    signals: Iterable[BAJobSignal], *, recent_since: date | None = None
) -> tuple[EmployerHiringSignal, ...]:
    """Deduplicate postings and group them by a stable normalized employer name."""
    grouped: dict[str, list[BAJobSignal]] = {}
    seen_references: set[str] = set()
    for signal in signals:
        if signal.reference_number in seen_references:
            continue
        seen_references.add(signal.reference_number)
        normalized_name = normalize_employer_identity(signal.employer_name)
        if not normalized_name:
            continue
        grouped.setdefault(normalized_name, []).append(signal)

    employers: list[EmployerHiringSignal] = []
    for normalized_name, employer_signals in grouped.items():
        locations: list[str] = []
        seen_locations: set[str] = set()
        for signal in employer_signals:
            normalized_location = " ".join(signal.location.casefold().split())
            if normalized_location and normalized_location not in seen_locations:
                seen_locations.add(normalized_location)
                locations.append(signal.location)
        recent_jobs = sum(
            recent_since is None
            or (signal.published_at is not None and signal.published_at >= recent_since)
            for signal in employer_signals
        )
        employers.append(
            EmployerHiringSignal(
                normalized_name=normalized_name,
                employer_name=employer_signals[0].employer_name,
                total_active_jobs=len(employer_signals),
                recent_jobs=recent_jobs,
                distinct_locations=tuple(locations),
                signals=tuple(employer_signals),
            )
        )
    return tuple(employers)


def normalize_employer_identity(name: str) -> str:
    """Normalize employer spelling without discarding legal-name information."""
    normalized = unicodedata.normalize("NFKC", name).casefold().replace("&", " and ")
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _signal_payload(signal: BAJobSignal) -> dict[str, str | None]:
    return {
        "employer_name": signal.employer_name,
        "job_title": signal.job_title,
        "location": signal.location,
        "postal_code": signal.postal_code,
        "reference_number": signal.reference_number,
        "published_at": signal.published_at.isoformat() if signal.published_at else None,
    }


def _parse_search_page(payload: Any, *, page: int, size: int) -> BAJobSearchPage:
    if not isinstance(payload, dict):
        raise JobsucheProviderError("The BA Jobsuche provider returned an invalid response.")
    raw_records = payload.get("stellenangebote")
    if not isinstance(raw_records, list):
        raise JobsucheProviderError("The BA Jobsuche provider returned no stellenangebote list.")
    signals = tuple(
        signal
        for raw_record in raw_records
        if isinstance(raw_record, dict) and (signal := _parse_job_signal(raw_record)) is not None
    )
    total_results = _first_int(payload, "maxErgebnisse", "total", "totalResults", "total_results")
    is_complete = (
        total_results <= page * size if total_results is not None else len(raw_records) < size
    )
    return BAJobSearchPage(
        signals=signals,
        total_results=total_results,
        page=page,
        size=size,
        is_complete=is_complete,
    )


def _parse_job_signal(record: dict[str, Any]) -> BAJobSignal | None:
    work_location = record.get("arbeitsort") or record.get("arbeitsOrt")
    if isinstance(work_location, list):
        work_location = next((value for value in work_location if isinstance(value, dict)), {})
    if not isinstance(work_location, dict):
        work_location = {}
    employer_name = _first_string(record, "arbeitgeber", "arbeitgeberName", "employer")
    job_title = _first_string(record, "beruf", "titel", "stellenangebotsTitel", "title")
    location = _first_string(work_location, "ort", "city", "name")
    postal_code = _first_string(work_location, "plz", "postalCode", "postal_code")
    country = _first_string(work_location, "land", "country", "countryCode", "country_code")
    reference_number = _first_string(
        record,
        "referenznummer",
        "refnr",
        "referenceNumber",
        "reference_number",
    )
    if not (
        employer_name
        and job_title
        and location
        and postal_code
        and reference_number
        and _has_germany_evidence(country)
    ):
        return None
    return BAJobSignal(
        employer_name=employer_name,
        job_title=job_title,
        location=location,
        postal_code=postal_code,
        reference_number=reference_number,
        published_at=_first_date(
            record,
            "aktuelleVeroeffentlichungsdatum",
            "ersteVeroeffentlichungsdatum",
            "publishedAt",
            "published_at",
        ),
    )


def _has_germany_evidence(country: str) -> bool:
    return country.casefold() in _GERMAN_COUNTRY_VALUES


def _safe_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
        port = parsed.port
    except ValueError as error:
        raise ValueError("BA Jobsuche base URL must be a safe HTTPS URL.") from error
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("BA Jobsuche base URL must be a safe HTTPS URL.")
    try:
        validate_public_hostname(host)
    except UnsafeNetworkAddress as error:
        raise ValueError("BA Jobsuche base URL host must resolve to a public address.") from error
    return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))


def _bounded_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a positive integer.") from error
    if number < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return number


def _first_string(record: dict[str, Any], *names: str) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return ""


def _first_int(record: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = record.get(name)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_date(record: dict[str, Any], *names: str) -> date | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            continue
    return None
