from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx
from django.conf import settings

from jobs.network import UnsafeNetworkAddress, validate_public_hostname


@dataclass(frozen=True, slots=True)
class RawJob:
    """A source-independent job record returned by a collector adapter."""

    external_id: str
    canonical_url: str
    title: str
    application_url: str = ""
    description_html: str = ""
    description_text: str = ""
    city: str = ""
    state: str = ""
    country_code: str = "DE"
    locations: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    remote_type: str = "unknown"
    employment_type: str = ""
    seniority: str = ""
    department: str = ""
    industry: str = ""
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = "EUR"
    language_requirement: str = ""
    skills: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    raw_jobs: list[RawJob]
    requests_made: int = 0
    is_complete: bool = True


class Collector(Protocol):
    @property
    def requests_made(self) -> int: ...

    def collect(self) -> CollectionResult: ...


class SourceLike(Protocol):
    kind: str
    source_url: str
    tenant: str
    config: dict[str, Any]
    request_delay_seconds: int
    max_pages: int


CollectorFactory = Callable[[SourceLike], Collector]


class CollectorRegistry:
    """Maps persisted source kinds to collector factories."""

    def __init__(self) -> None:
        self._factories: dict[str, CollectorFactory] = {}

    def register(self, kind: str, factory: CollectorFactory) -> None:
        self._factories[kind] = factory

    def create(self, source: SourceLike) -> Collector:
        try:
            factory = self._factories[source.kind]
        except KeyError as error:
            message = f"No collector is registered for source kind {source.kind!r}."
            raise LookupError(message) from error
        return factory(source)


class BotProtectionDetected(RuntimeError):
    """A source returned a bot challenge instead of a jobs response."""


class UnsafeDetailUrl(ValueError):
    """A payload-provided detail URL is not safe for a server-side request."""


class HTTPCollector:
    """Small HTTP boundary for adapters that fetch career-site responses."""

    def __init__(
        self,
        source: SourceLike,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=30.0,
            headers={
                "User-Agent": str(
                    getattr(
                        settings,
                        "COLLECTION_USER_AGENT",
                        "hiring-scraper/0.1 (self-hosted job collection)",
                    )
                )
            },
        )
        self._owns_client = client is None
        self._sleeper = sleeper
        self.requests_made = 0

    def fetch(self, url: str, *, follow_redirects: bool = True) -> httpx.Response:
        if self.requests_made:
            self._sleeper(self.source.request_delay_seconds)
        self.requests_made += 1
        response = self._client.get(url, follow_redirects=follow_redirects)
        if is_bot_protection_response(response):
            raise BotProtectionDetected(f"Bot protection detected for {url}.")
        if not response.is_redirect:
            response.raise_for_status()
        return response

    def fetch_trusted(
        self, url: str, *, allowed_hosts: frozenset[str], max_redirects: int = 5
    ) -> httpx.Response:
        """Fetch a payload URL only after validating its host and every redirect."""
        current_url = _validate_detail_url(url, allowed_hosts=allowed_hosts)
        for _ in range(max_redirects + 1):
            response = self.fetch(current_url, follow_redirects=False)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                raise httpx.HTTPStatusError(
                    "Redirect response did not include a Location header.",
                    request=response.request,
                    response=response,
                )
            current_url = _validate_detail_url(
                urljoin(current_url, location), allowed_hosts=allowed_hosts
            )
        raise httpx.TooManyRedirects("Too many trusted detail redirects.")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def is_bot_protection_response(response: httpx.Response) -> bool:
    """Recognize common challenge pages before adapters try to parse them."""
    if response.status_code in {403, 429}:
        return True
    headers = {key.casefold(): value.casefold() for key, value in response.headers.items()}
    if headers.get("cf-mitigated") == "challenge":
        return True
    body = response.text.casefold()
    return any(
        marker in body
        for marker in (
            "captcha",
            "cloudflare ray id",
            "unusual traffic",
            "verify you are human",
            "access denied",
        )
    )


def _validate_detail_url(url: str, *, allowed_hosts: frozenset[str]) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise UnsafeDetailUrl("Detail URL must use HTTPS on an allowed host.") from error
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if (
        parsed.scheme != "https"
        or not host
        or host not in allowed_hosts
        or parsed.username
        or parsed.password
    ):
        raise UnsafeDetailUrl("Detail URL must use HTTPS on an allowed host.")
    try:
        port = parsed.port
    except ValueError:
        raise UnsafeDetailUrl("Detail URL must use HTTPS on an allowed host.") from None
    if port is not None:
        raise UnsafeDetailUrl("Detail URL must use HTTPS on an allowed host.")
    try:
        validate_public_hostname(host)
    except UnsafeNetworkAddress as error:
        raise UnsafeDetailUrl("Detail URL host must resolve to a public address.") from error
    return url
