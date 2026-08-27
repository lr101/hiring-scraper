"""Bounded Common Crawl tenant discovery and public ATS feed validation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import islice
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from django.conf import settings

from .collectors import is_bot_protection_response
from .collectors.ats import (
    ASHBY,
    DVINCI,
    GREENHOUSE,
    LEVER,
    ONLYFY,
    PERSONIO,
    RECRUITEE,
    SMARTRECRUITERS,
    SOFTGARDEN,
    SUCCESSFACTORS,
    WORKABLE,
    WORKDAY,
    _ats_source_allowed_hosts,
    ashby_feed_url,
    fingerprint_ats_url,
    normalize_ats_tenant,
    onlyfy_feed_url,
    smartrecruiters_feed_url,
    workable_feed_url,
)
from .locations import _wait_for_provider_rate_limit
from .network import UnsafeNetworkAddress, validate_public_hostname

DEFAULT_INDEX_BASE_URL = "https://index.commoncrawl.org"
DEFAULT_MAX_RECORDS_PER_QUERY = 100
DEFAULT_MAX_PATTERNS = 12
DEFAULT_PATTERNS = (
    "*.jobs.personio.de/*",
    "boards.greenhouse.io/*",
    "jobs.lever.co/*",
    "jobs.ashbyhq.com/*",
    "jobs.smartrecruiters.com/*",
    "apply.workable.com/*",
    "*.recruitee.com/*",
    "*.myworkdayjobs.com/*",
    "api.softgarden.io/*",
    "jobs.dvinci.com/*",
    "api.prescreen.io/*",
    "*.successfactors.com/*",
)

_SNAPSHOT_PATTERN = re.compile(r"CC-MAIN-[0-9]{4}-[0-9]{2}\Z")
_XML_FEED_ROOTS = {
    PERSONIO: {"positions"},
    RECRUITEE: {"offers"},
    DVINCI: {"jobs", "joboffers"},
    SUCCESSFACTORS: {"jobs", "jobpositionlist"},
}


@dataclass(frozen=True, slots=True)
class ATSTenantDescriptor:
    """A recognized ATS tenant backed by an indexed public URL."""

    kind: str
    tenant: str
    source_url: str
    evidence_urls: tuple[str, ...]
    snapshot: str

    @property
    def evidence_url(self) -> str:
        return self.evidence_urls[0] if self.evidence_urls else ""


class CommonCrawlProviderError(RuntimeError):
    """The Common Crawl index returned an unusable response."""


class CommonCrawlIndexClient:
    """Query a small, explicit set of Common Crawl wildcard patterns."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        index_base_url: str | None = None,
        max_records_per_query: int | None = None,
        max_patterns: int | None = None,
        min_interval_seconds: float | None = None,
        rate_limit_state_path: str | None = None,
    ) -> None:
        self.index_base_url = _safe_index_base_url(
            index_base_url
            or str(getattr(settings, "COMMON_CRAWL_INDEX_BASE_URL", DEFAULT_INDEX_BASE_URL))
        )
        self.max_records_per_query = _positive_int(
            max_records_per_query
            if max_records_per_query is not None
            else getattr(
                settings,
                "COMMON_CRAWL_MAX_RECORDS_PER_QUERY",
                DEFAULT_MAX_RECORDS_PER_QUERY,
            ),
            field="max_records_per_query",
        )
        self.max_patterns = _positive_int(
            max_patterns
            if max_patterns is not None
            else getattr(settings, "COMMON_CRAWL_MAX_PATTERNS", DEFAULT_MAX_PATTERNS),
            field="max_patterns",
        )
        self.min_interval_seconds = (
            min_interval_seconds
            if min_interval_seconds is not None
            else float(getattr(settings, "COMMON_CRAWL_MIN_REQUEST_INTERVAL_SECONDS", 1.0))
        )
        self.rate_limit_state_path = rate_limit_state_path or str(
            getattr(
                settings,
                "COMMON_CRAWL_RATE_LIMIT_STATE_PATH",
                "/tmp/hiring-scraper-common-crawl-rate-limit",
            )
        )
        self._client = client or httpx.Client(
            timeout=float(getattr(settings, "COMMON_CRAWL_TIMEOUT_SECONDS", 30.0)),
            headers={
                "Accept": "application/json",
                "User-Agent": str(
                    getattr(
                        settings,
                        "COMMON_CRAWL_USER_AGENT",
                        "hiring-scraper/0.1 (self-hosted ATS discovery)",
                    )
                ),
            },
        )
        self._owns_client = client is None
        self.requests_made = 0

    def discover(
        self,
        *,
        snapshot: str | None = None,
        patterns: Iterable[str] | None = None,
    ) -> tuple[ATSTenantDescriptor, ...]:
        """Return deduplicated recognized tenants from at most the configured queries."""
        configured_snapshot = snapshot or getattr(settings, "COMMON_CRAWL_SNAPSHOT", "")
        snapshot_id = (
            _snapshot_id(str(configured_snapshot))
            if configured_snapshot
            else self._current_snapshot()
        )
        pattern_source = patterns if patterns is not None else DEFAULT_PATTERNS
        selected_patterns = tuple(islice(pattern_source, self.max_patterns))
        descriptors: dict[tuple[str, str], ATSTenantDescriptor] = {}
        for pattern in selected_patterns:
            for record in self._query(snapshot_id, pattern):
                if _string(record.get("status")) != "200":
                    continue
                url = _string(record.get("url"))
                descriptor = _descriptor_from_url(url, snapshot=snapshot_id)
                if descriptor is None:
                    continue
                key = (descriptor.kind, descriptor.source_url)
                existing = descriptors.get(key)
                if existing is None:
                    descriptors[key] = descriptor
                elif url and url not in existing.evidence_urls:
                    descriptors[key] = replace(
                        existing,
                        evidence_urls=(*existing.evidence_urls, url)[:3],
                    )
        return tuple(descriptors.values())

    def discover_validated(
        self,
        *,
        snapshot: str | None = None,
        patterns: Iterable[str] | None = None,
        validator: PublicATSFeedValidator | None = None,
    ) -> tuple[ATSTenantDescriptor, ...]:
        """Return only tenants whose constructed public feed responds with valid data."""
        feed_validator = validator or PublicATSFeedValidator()
        owns_validator = validator is None
        try:
            descriptors = self.discover(snapshot=snapshot, patterns=patterns)
            return tuple(
                descriptor for descriptor in descriptors if feed_validator.validate(descriptor)
            )
        finally:
            if owns_validator:
                feed_validator.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _current_snapshot(self) -> str:
        try:
            response = self._request(self.index_base_url + "/collinfo.json")
            if response is None:
                raise CommonCrawlProviderError("Common Crawl returned no crawl list.")
            payload = response.json()
        except (CommonCrawlProviderError, ValueError, TypeError) as error:
            raise CommonCrawlProviderError(
                "Common Crawl did not return a usable crawl list."
            ) from error
        if not isinstance(payload, list):
            raise CommonCrawlProviderError("Common Crawl returned an invalid crawl list.")
        for item in payload:
            if isinstance(item, dict):
                try:
                    return _snapshot_id(_string(item.get("id")))
                except ValueError:
                    continue
        raise CommonCrawlProviderError("Common Crawl returned no usable crawl snapshot.")

    def _query(self, snapshot: str, pattern: str) -> list[dict[str, Any]]:
        response = self._request(
            f"{self.index_base_url}/{snapshot}-index",
            params={
                "url": pattern,
                "output": "json",
                "filter": "status:200",
                "limit": str(self.max_records_per_query),
            },
            allow_not_found=True,
        )
        if response is None:
            return []
        records: list[dict[str, Any]] = []
        for line in response.text.splitlines()[: self.max_records_per_query]:
            record = _parse_index_line(line)
            if record is not None:
                records.append(record)
        return records

    def _request(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        _wait_for_provider_rate_limit(
            self.min_interval_seconds,
            state_path=self.rate_limit_state_path,
        )
        self.requests_made += 1
        try:
            response = self._client.get(url, params=params, follow_redirects=False)
            if response.is_redirect or is_bot_protection_response(response):
                raise CommonCrawlProviderError("Common Crawl returned an unsafe response.")
            if allow_not_found and response.status_code == 404:
                return None
            response.raise_for_status()
            return response
        except CommonCrawlProviderError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise CommonCrawlProviderError("The Common Crawl index is unavailable.") from error


class PublicATSFeedValidator:
    """Check a constructed ATS feed without following payload-provided redirects."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        min_interval_seconds: float | None = None,
        rate_limit_state_path: str | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=float(getattr(settings, "COMMON_CRAWL_VALIDATION_TIMEOUT_SECONDS", 30.0)),
            headers={
                "Accept": "application/json, application/xml, text/xml",
                "User-Agent": str(
                    getattr(
                        settings,
                        "COMMON_CRAWL_USER_AGENT",
                        "hiring-scraper/0.1 (self-hosted ATS discovery)",
                    )
                ),
            },
        )
        self._owns_client = client is None
        self.min_interval_seconds = (
            min_interval_seconds
            if min_interval_seconds is not None
            else float(getattr(settings, "COMMON_CRAWL_MIN_REQUEST_INTERVAL_SECONDS", 1.0))
        )
        self.rate_limit_state_path = rate_limit_state_path or str(
            getattr(
                settings,
                "COMMON_CRAWL_RATE_LIMIT_STATE_PATH",
                "/tmp/hiring-scraper-common-crawl-rate-limit",
            )
        )

    def validate(self, descriptor: ATSTenantDescriptor) -> bool:
        source = SimpleNamespace(
            kind=descriptor.kind,
            source_url=descriptor.source_url,
            tenant=descriptor.tenant,
            config={},
            request_delay_seconds=0,
            max_pages=1,
        )
        try:
            _ats_source_allowed_hosts(source=source, url=descriptor.source_url)
            _wait_for_provider_rate_limit(
                self.min_interval_seconds,
                state_path=self.rate_limit_state_path,
            )
            if descriptor.kind == WORKDAY:
                response = self._client.post(
                    descriptor.source_url,
                    json={
                        "appliedFacets": {},
                        "limit": 20,
                        "offset": 0,
                        "searchText": "",
                    },
                    follow_redirects=False,
                )
            else:
                response = self._client.get(
                    descriptor.source_url,
                    follow_redirects=False,
                )
            if response.is_redirect or is_bot_protection_response(response):
                return False
            response.raise_for_status()
            return _valid_feed_body(descriptor.kind, response.text)
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _descriptor_from_url(url: str, *, snapshot: str) -> ATSTenantDescriptor | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    path = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.username or parsed.password or port is not None:
        return None

    if (
        host == "api.softgarden.io"
        and len(path) == 4
        and path[:2] == ["v1", "companies"]
        and path[3] == "jobs"
    ):
        return _descriptor(
            SOFTGARDEN,
            path[2],
            lambda tenant: f"https://api.softgarden.io/v1/companies/{tenant}/jobs",
            url,
            snapshot,
        )
    if (
        host == "api.prescreen.io"
        and len(path) == 5
        and path[:3] == ["api", "v1", "companies"]
        and path[4] == "jobs"
    ):
        return _descriptor(ONLYFY, path[3], onlyfy_feed_url, url, snapshot)
    if host == "jobs.smartrecruiters.com":
        return _descriptor(
            SMARTRECRUITERS, _first_path_part(parsed.path), smartrecruiters_feed_url, url, snapshot
        )
    if host == "jobs.ashbyhq.com":
        return _descriptor(ASHBY, _first_path_part(parsed.path), ashby_feed_url, url, snapshot)
    if host == "apply.workable.com":
        return _descriptor(
            WORKABLE, _first_path_part(parsed.path), workable_feed_url, url, snapshot
        )
    if host == "jobs.dvinci.com":
        return _descriptor(
            DVINCI,
            _first_path_part(parsed.path),
            lambda tenant: f"https://jobs.dvinci.com/{tenant}/jobs.json",
            url,
            snapshot,
        )
    if host.endswith(".successfactors.com") and host.count(".") == 2 and path:
        return _descriptor(
            SUCCESSFACTORS,
            host.removesuffix(".successfactors.com"),
            lambda tenant: f"https://{tenant}.successfactors.com/jobs.xml",
            url,
            snapshot,
        )
    fingerprint = fingerprint_ats_url(url)
    if fingerprint is None and host.endswith(".myworkdayjobs.com"):
        fingerprint = _fingerprint_workday_board(host=host, path=path)
    if fingerprint is None:
        return None
    return ATSTenantDescriptor(
        kind=fingerprint.kind,
        tenant=fingerprint.tenant,
        source_url=fingerprint.source_url,
        evidence_urls=(url,),
        snapshot=snapshot,
    )


def _fingerprint_workday_board(
    *,
    host: str,
    path: list[str],
) -> Any | None:
    for length in (3, 2, 1):
        if len(path) < length:
            continue
        board_url = urlunsplit(("https", host, "/" + "/".join(path[:length]), "", ""))
        fingerprint = fingerprint_ats_url(board_url)
        if fingerprint is not None:
            return fingerprint
    return None


def _descriptor(
    kind: str,
    tenant: str,
    builder: Any,
    evidence_url: str,
    snapshot: str,
) -> ATSTenantDescriptor | None:
    if not tenant:
        return None
    try:
        normalized_tenant = normalize_ats_tenant(tenant)
        source_url = builder(normalized_tenant)
    except ValueError:
        return None
    return ATSTenantDescriptor(
        kind=kind,
        tenant=normalized_tenant,
        source_url=source_url,
        evidence_urls=(evidence_url,),
        snapshot=snapshot,
    )


def _first_path_part(path: str) -> str:
    return next((part for part in path.split("/") if part), "")


def _parse_index_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        parts = text.split(" ", maxsplit=2)
        if len(parts) != 3:
            return None
        try:
            payload = json.loads(parts[2])
        except ValueError:
            return None
    return payload if isinstance(payload, dict) else None


def _valid_feed_body(kind: str, body: str) -> bool:
    if kind in {PERSONIO, RECRUITEE, SUCCESSFACTORS}:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            return False
        return _xml_local_name(root.tag).casefold() in _XML_FEED_ROOTS[kind]
    if kind == DVINCI and body.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            return False
        return _xml_local_name(root.tag).casefold() in _XML_FEED_ROOTS[kind]
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    if kind == WORKDAY:
        return isinstance(payload, dict) and isinstance(payload.get("jobPostings"), list)
    if kind == SMARTRECRUITERS:
        return isinstance(payload, dict) and isinstance(payload.get("content"), list)
    if kind == WORKABLE:
        return isinstance(payload, dict) and isinstance(payload.get("results"), list)
    if kind in {SOFTGARDEN, ONLYFY, GREENHOUSE, ASHBY, DVINCI}:
        return isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
    if kind == LEVER:
        return isinstance(payload, list) or (
            isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
        )
    return False


def _safe_index_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
        port = parsed.port
    except ValueError as error:
        raise ValueError("Common Crawl index URL must be a safe HTTPS URL.") from error
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
        raise ValueError("Common Crawl index URL must be a safe HTTPS URL.")
    try:
        validate_public_hostname(host)
    except UnsafeNetworkAddress as error:
        raise ValueError("Common Crawl index host must resolve to a public address.") from error
    return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))


def _snapshot_id(value: str) -> str:
    snapshot = value.strip().upper()
    if not _SNAPSHOT_PATTERN.fullmatch(snapshot):
        raise ValueError("Common Crawl snapshot must use a CC-MAIN-YYYY-WW identifier.")
    return snapshot


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a positive integer.") from error
    if number < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return number


def _string(value: Any) -> str:
    return (
        value.strip() if isinstance(value, str) else str(value).strip() if value is not None else ""
    )


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]
