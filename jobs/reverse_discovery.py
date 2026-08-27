"""Run bounded reverse discovery through BA signals and existing source collection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

from django.db import transaction

from .ba_discovery import (
    EmployerDiscoveryResult,
    EmployerDiscoveryService,
    EmployerHiringSignal,
)
from .collection import collect_source, collector_registry
from .collectors.ats import fingerprint_ats_url
from .common_crawl import (
    ATSTenantDescriptor,
    CommonCrawlIndexClient,
    CommonCrawlProviderError,
)
from .company_discovery import CompanyDiscoveryError, DiscoveredCompany, discover_company
from .company_locations import CompanyLocationDiscovery
from .employer_resolution import EmployerResolver, OSMEmployerResolver, ResolvedEmployer
from .models import CareerSource, Company, CrawlRun, MonitoringTarget, WorkspaceUser

DEFAULT_RADIUS_KM = 25
DEFAULT_PUBLICATION_AGE_DAYS = 30


class _TenantDiscovery(Protocol):
    def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]: ...


class _BAEmployerDiscovery(Protocol):
    def discover_and_persist(
        self,
        *,
        city: str,
        radius_km: int,
        publication_age_days: int,
        offer_type: int,
        include_temporary_agencies: bool,
        max_pages: int | None,
        as_of: date | None,
    ) -> EmployerDiscoveryResult: ...


@dataclass(frozen=True, slots=True)
class ReverseDiscoveryResult:
    """The observable result of one bounded reverse-discovery run."""

    ats_tenants: tuple[ATSTenantDescriptor, ...] = ()
    ba_result: EmployerDiscoveryResult | None = None
    resolved_employers: tuple[ResolvedEmployer, ...] = ()
    discovered_companies: tuple[DiscoveredCompany, ...] = ()
    runs: tuple[CrawlRun, ...] = ()
    errors: tuple[str, ...] = ()
    used_osm_fallback: bool = False
    unreadable_websites: int = 0
    companies_created: int = 0
    targets_created: int = 0

    @property
    def is_complete(self) -> bool:
        return not self.errors and (self.ba_result is None or self.ba_result.is_complete)

    @property
    def employers(self) -> tuple[ResolvedEmployer, ...]:
        return self.resolved_employers

    @property
    def companies_added(self) -> int:
        return self.targets_created if self.targets_created else self.companies_created

    @property
    def total_active_jobs(self) -> int:
        return self.ba_result.total_active_jobs if self.ba_result is not None else 0

    @property
    def new_jobs(self) -> int:
        return sum(run.jobs_created for run in self.runs)

    @property
    def blocked_sources(self) -> int:
        return sum(run.status == CrawlRun.Status.BLOCKED for run in self.runs)


@dataclass(frozen=True, slots=True)
class _RegisteredSource:
    source: CareerSource
    source_created: bool
    target_created: bool
    company_created: bool


class ReverseDiscoveryService:
    """Coordinate public ATS, BA, domain, and collection stages in a fixed order."""

    def __init__(
        self,
        *,
        common_crawl_client: _TenantDiscovery | None = None,
        ba_service: _BAEmployerDiscovery | None = None,
        resolver: EmployerResolver | None = None,
        company_discoverer: Callable[[str], DiscoveredCompany] = discover_company,
    ) -> None:
        self.common_crawl_client = common_crawl_client or CommonCrawlIndexClient()
        self.ba_service = ba_service or EmployerDiscoveryService()
        self.resolver = resolver or OSMEmployerResolver()
        self.company_discoverer = company_discoverer
        self._owns_common_crawl_client = common_crawl_client is None
        self._owns_ba_service = ba_service is None
        self._owns_resolver = resolver is None

    def discover(
        self,
        *,
        city: str | None = None,
        radius_km: int = DEFAULT_RADIUS_KM,
        publication_age_days: int = DEFAULT_PUBLICATION_AGE_DAYS,
        offer_type: int = 1,
        include_temporary_agencies: bool = False,
        max_pages: int | None = None,
        as_of: date | None = None,
        user: WorkspaceUser | None = None,
        collect: bool = True,
        include_common_crawl: bool = True,
    ) -> ReverseDiscoveryResult:
        """Run each available discovery stage and collect newly registered sources."""
        errors: list[str] = []
        ats_tenants: tuple[ATSTenantDescriptor, ...] = ()
        if include_common_crawl:
            try:
                ats_tenants = self.common_crawl_client.discover_validated()
            except CommonCrawlProviderError as error:
                errors.append(str(error))

        ba_result: EmployerDiscoveryResult | None = None
        resolved_employers: list[ResolvedEmployer] = []
        if city is not None:
            ba_result = self.ba_service.discover_and_persist(
                city=city,
                radius_km=radius_km,
                publication_age_days=publication_age_days,
                offer_type=offer_type,
                include_temporary_agencies=include_temporary_agencies,
                max_pages=max_pages,
                as_of=as_of,
            )
            if ba_result.error is not None:
                errors.append(ba_result.error)
            else:
                resolved_employers = self._resolve_employers(
                    ba_result.employers,
                    errors=errors,
                )

        discovered_companies, runs, companies_created, targets_created = (
            self._discover_resolved_employers(
                resolved_employers,
                user=user,
                collect=collect,
                errors=errors,
            )
        )
        return ReverseDiscoveryResult(
            ats_tenants=ats_tenants,
            ba_result=ba_result,
            resolved_employers=tuple(resolved_employers),
            discovered_companies=tuple(discovered_companies),
            runs=tuple(runs),
            errors=tuple(errors),
            companies_created=companies_created,
            targets_created=targets_created,
        )

    def discover_city(
        self,
        *,
        user: WorkspaceUser,
        place: Any,
        radius_km: int,
        fallback_discoverer: Callable[[], CompanyLocationDiscovery],
        publication_age_days: int = DEFAULT_PUBLICATION_AGE_DAYS,
        offer_type: int = 1,
        include_temporary_agencies: bool = False,
        max_pages: int | None = None,
        as_of: date | None = None,
    ) -> ReverseDiscoveryResult:
        """Use BA-backed resolution for a city and invoke the old OSM sweep only if needed."""
        result = self.discover(
            city=place.name,
            radius_km=radius_km,
            publication_age_days=publication_age_days,
            offer_type=offer_type,
            include_temporary_agencies=include_temporary_agencies,
            max_pages=max_pages,
            as_of=as_of,
            user=user,
            include_common_crawl=False,
        )
        if result.discovered_companies:
            return result

        fallback = fallback_discoverer()
        errors = list(result.errors)
        discovered_companies, runs, companies_created, targets_created = self._register_discovered(
            fallback.companies,
            user=user,
            collect=True,
            errors=errors,
        )
        return replace(
            result,
            discovered_companies=tuple(discovered_companies),
            runs=tuple(runs),
            errors=tuple(errors),
            used_osm_fallback=True,
            unreadable_websites=fallback.unreadable_websites,
            companies_created=result.companies_created + companies_created,
            targets_created=result.targets_created + targets_created,
        )

    def close(self) -> None:
        if self._owns_common_crawl_client:
            _close_if_available(self.common_crawl_client)
        if self._owns_ba_service:
            _close_if_available(self.ba_service)
        if self._owns_resolver:
            _close_if_available(self.resolver)

    def _resolve_employers(
        self,
        employers: tuple[EmployerHiringSignal, ...],
        *,
        errors: list[str],
    ) -> list[ResolvedEmployer]:
        resolved: list[ResolvedEmployer] = []
        seen_domains: set[str] = set()
        for employer in employers:
            try:
                match = self.resolver.resolve(employer)
            except Exception as error:
                errors.append(str(error))
                continue
            if match is None or match.domain in seen_domains:
                continue
            seen_domains.add(match.domain)
            resolved.append(match)
        return resolved

    def _discover_resolved_employers(
        self,
        resolved_employers: Iterable[ResolvedEmployer],
        *,
        user: WorkspaceUser | None,
        collect: bool,
        errors: list[str],
    ) -> tuple[list[DiscoveredCompany], list[CrawlRun], int, int]:
        candidates: list[DiscoveredCompany] = []
        for resolved in resolved_employers:
            try:
                candidates.append(self.company_discoverer(resolved.website_url))
            except CompanyDiscoveryError as error:
                errors.append(str(error))
        return self._register_discovered(
            candidates,
            user=user,
            collect=collect,
            errors=errors,
        )

    def _register_discovered(
        self,
        companies: Iterable[DiscoveredCompany],
        *,
        user: WorkspaceUser | None,
        collect: bool,
        errors: list[str],
    ) -> tuple[list[DiscoveredCompany], list[CrawlRun], int, int]:
        discovered_companies: list[DiscoveredCompany] = []
        runs: list[CrawlRun] = []
        companies_created = 0
        targets_created = 0
        for discovered in companies:
            try:
                registered = _register_source(discovered=discovered, user=user)
            except Exception as error:
                errors.append(str(error))
                continue
            discovered_companies.append(discovered)
            companies_created += int(registered.company_created)
            targets_created += int(registered.target_created)
            if not collect or not (registered.source_created or registered.target_created):
                continue
            if not registered.source.is_enabled or registered.source.blocked_at is not None:
                continue
            runs.append(collect_source(source=registered.source, registry=collector_registry))
        return discovered_companies, runs, companies_created, targets_created


def _register_source(
    *,
    discovered: DiscoveredCompany,
    user: WorkspaceUser | None,
) -> _RegisteredSource:
    fingerprint = fingerprint_ats_url(discovered.career_url)
    if fingerprint is None:
        source_url = discovered.career_url
        source_kind = discovered.source_kind
        tenant = ""
        allowed_hosts = discovered.allowed_hosts
    else:
        source_url = fingerprint.source_url
        source_kind = fingerprint.kind
        tenant = fingerprint.tenant
        source_host = urlsplit(source_url).hostname
        allowed_hosts = (source_host,) if source_host else ()

    with transaction.atomic():
        company, company_created = Company.objects.get_or_create(
            domain=discovered.domain,
            defaults={"name": discovered.name, "career_url": discovered.career_url},
        )
        source, source_created = CareerSource.objects.get_or_create(
            source_url=source_url,
            defaults={
                "company": company,
                "kind": source_kind,
                "tenant": tenant,
                "config": {"allowed_hosts": list(allowed_hosts)},
            },
        )
        target_created = False
        if user is not None:
            _target, target_created = MonitoringTarget.objects.get_or_create(
                user=user,
                kind=MonitoringTarget.Kind.COMPANY,
                company=source.company,
            )
    return _RegisteredSource(
        source=source,
        source_created=source_created,
        target_created=target_created,
        company_created=company_created,
    )


def _close_if_available(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()
