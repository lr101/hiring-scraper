"""Run bounded reverse discovery through BA signals and existing source collection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from django.db.models import Q

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
from .models import (
    CareerSource,
    Company,
    CrawlRun,
    Job,
    JobMatch,
    MonitoringTarget,
    UserJobState,
    WorkspaceUser,
)

DEFAULT_RADIUS_KM = 25
DEFAULT_PUBLICATION_AGE_DAYS = 30
_ATS_PLACEHOLDER_SUFFIX = ".ats.invalid"


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
        return (
            not self.errors
            and (self.ba_result is None or self.ba_result.is_complete)
            and all(run.status == CrawlRun.Status.SUCCESS for run in self.runs)
        )

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

    @property
    def error_count(self) -> int:
        return len(self.errors) + sum(run.status != CrawlRun.Status.SUCCESS for run in self.runs)


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
        ats_companies: list[DiscoveredCompany] = []
        ats_runs: list[CrawlRun] = []
        ats_companies_created = 0
        ats_targets_created = 0
        if include_common_crawl:
            try:
                ats_tenants = self.common_crawl_client.discover_validated()
            except CommonCrawlProviderError as error:
                errors.append(str(error))
            if user is None:
                (
                    ats_companies,
                    ats_runs,
                    ats_companies_created,
                    ats_targets_created,
                ) = self._register_ats_tenants(
                    ats_tenants,
                    collect=collect,
                    errors=errors,
                )

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
            discovered_companies=tuple((*ats_companies, *discovered_companies)),
            runs=tuple((*ats_runs, *runs)),
            errors=tuple(errors),
            companies_created=companies_created + ats_companies_created,
            targets_created=targets_created + ats_targets_created,
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

    def _register_ats_tenants(
        self,
        tenants: Iterable[ATSTenantDescriptor],
        *,
        collect: bool,
        errors: list[str],
    ) -> tuple[list[DiscoveredCompany], list[CrawlRun], int, int]:
        discovered_companies: list[DiscoveredCompany] = []
        runs: list[CrawlRun] = []
        companies_created = 0
        targets_created = 0
        for tenant in tenants:
            try:
                discovered = _discovered_company_from_tenant(tenant)
                registered = _register_source(
                    discovered=discovered,
                    user=None,
                    metadata={
                        "discovery": "common_crawl",
                        "snapshot": tenant.snapshot,
                        "evidence_urls": list(tenant.evidence_urls),
                    },
                )
            except Exception as error:
                errors.append(str(error))
                continue
            discovered_companies.append(discovered)
            companies_created += int(registered.company_created)
            targets_created += int(registered.target_created)
            if not collect or not registered.source_created:
                continue
            if not registered.source.is_enabled or registered.source.blocked_at is not None:
                continue
            run = _collect_registered_source(registered.source, errors=errors)
            if run is not None:
                runs.append(run)
        return discovered_companies, runs, companies_created, targets_created

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
            run = _collect_registered_source(registered.source, errors=errors)
            if run is not None:
                runs.append(run)
        return discovered_companies, runs, companies_created, targets_created


def _register_source(
    *,
    discovered: DiscoveredCompany,
    user: WorkspaceUser | None,
    metadata: dict[str, Any] | None = None,
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
        source = (
            CareerSource.objects.select_related("company").filter(source_url=source_url).first()
        )
        equivalent_sources = _equivalent_ats_sources(source_url)
        company_created = False
        source_created = False
        if source is None:
            company, company_created = _registration_company(
                discovered=discovered,
                source_url=source_url,
                equivalent_sources=equivalent_sources,
            )
            source, source_created = CareerSource.objects.get_or_create(
                source_url=source_url,
                defaults={
                    "company": company,
                    "kind": source_kind,
                    "tenant": tenant,
                    "config": _source_config(allowed_hosts=allowed_hosts, metadata=metadata),
                },
            )
        assert source is not None
        source = (
            CareerSource.objects.select_for_update().select_related("company").get(pk=source.pk)
        )
        equivalent_sources = _equivalent_ats_sources(source_url)
        company, promoted_company_created = _reconcile_source_company(
            source=source,
            discovered=discovered,
            source_url=source_url,
            equivalent_sources=equivalent_sources,
        )
        company_created = company_created or promoted_company_created
        updates: list[str] = []
        if source.kind != source_kind:
            source.kind = source_kind
            updates.append("kind")
        if source.tenant != tenant:
            source.tenant = tenant
            updates.append("tenant")
        if updates:
            source.save(update_fields=updates)
        _merge_source_controls(source, equivalent_sources)
        _retire_equivalent_sources(source, equivalent_sources)
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


def _discovered_company_from_tenant(tenant: ATSTenantDescriptor) -> DiscoveredCompany:
    fingerprint = fingerprint_ats_url(tenant.source_url)
    if fingerprint is None:
        raise ValueError("Common Crawl returned an unrecognized ATS source URL.")
    source_host = urlsplit(fingerprint.source_url).hostname
    if source_host is None:
        raise ValueError("Common Crawl returned an invalid ATS source URL.")
    return DiscoveredCompany(
        domain=f"{fingerprint.tenant}.{fingerprint.kind}{_ATS_PLACEHOLDER_SUFFIX}",
        name=f"{fingerprint.tenant} ({fingerprint.kind})"[:200],
        website_url=tenant.evidence_url or fingerprint.source_url,
        career_url=fingerprint.source_url,
        source_kind=fingerprint.kind,
        allowed_hosts=(source_host,),
    )


def _registration_company(
    *,
    discovered: DiscoveredCompany,
    source_url: str,
    equivalent_sources: Iterable[CareerSource],
) -> tuple[Company, bool]:
    equivalent_company = next(
        (
            source.company
            for source in equivalent_sources
            if not _is_placeholder_company(source.company)
        ),
        None,
    )
    if equivalent_company is not None:
        return equivalent_company, False
    return _get_discovered_company(discovered, source_url=source_url)


def _reconcile_source_company(
    *,
    source: CareerSource,
    discovered: DiscoveredCompany,
    source_url: str,
    equivalent_sources: Iterable[CareerSource],
) -> tuple[Company, bool]:
    company = source.company
    if not _is_placeholder_company(company):
        return company, False

    equivalent_company = next(
        (
            equivalent.company
            for equivalent in equivalent_sources
            if not _is_placeholder_company(equivalent.company)
        ),
        None,
    )
    company_created = False
    if equivalent_company is not None:
        company = equivalent_company
    elif not _is_placeholder_domain(discovered.domain):
        company, company_created = _get_discovered_company(
            discovered,
            source_url=source_url,
        )
    _assign_source_company(source, company)
    return company, company_created


def _assign_source_company(source: CareerSource, company: Company) -> None:
    previous_company = source.company
    if source.company_id == company.pk:
        source.company = company
        return
    source.company = company
    source.save(update_fields=["company"])
    if _is_placeholder_company(previous_company):
        _delete_unused_placeholder(previous_company)


def _delete_unused_placeholder(company: Company) -> None:
    if _is_placeholder_company(company) and not company.sources.exists():
        if not company.monitoring_targets.exists():
            company.delete()


def _get_discovered_company(
    discovered: DiscoveredCompany,
    *,
    source_url: str | None = None,
) -> tuple[Company, bool]:
    company = Company.objects.filter(domain=discovered.domain).first()
    if company is None:
        career_urls = {discovered.career_url}
        if source_url:
            career_urls.add(source_url)
        company = Company.objects.filter(career_url__in=career_urls).order_by("pk").first()
    if company is None:
        try:
            company, created = Company.objects.get_or_create(
                domain=discovered.domain,
                defaults={"name": discovered.name, "career_url": discovered.career_url},
            )
        except IntegrityError:
            company = Company.objects.filter(career_url__in=career_urls).order_by("pk").first()
            if company is None:
                raise
            created = False
    else:
        created = False
    if _is_placeholder_company(company) and not _is_placeholder_domain(discovered.domain):
        company.domain = discovered.domain
        company.name = discovered.name
        company.save(update_fields=["domain", "name"])
    return company, created


def _source_config(
    *, allowed_hosts: tuple[str, ...], metadata: dict[str, Any] | None
) -> dict[str, Any]:
    config: dict[str, Any] = {"allowed_hosts": list(allowed_hosts)}
    if metadata:
        config.update(metadata)
    return config


def _equivalent_ats_sources(source_url: str) -> tuple[CareerSource, ...]:
    fingerprint = fingerprint_ats_url(source_url)
    if fingerprint is None:
        return ()
    matches: list[CareerSource] = []
    sources = CareerSource.objects.select_related("company").exclude(source_url=source_url)
    for source in sources.iterator():
        config = source.config if isinstance(source.config, dict) else {}
        if config.get("canonical_source_url") == source_url:
            continue
        if fingerprint_ats_url(source.source_url) == fingerprint:
            matches.append(source)
    return tuple(matches)


def _is_placeholder_domain(domain: str) -> bool:
    return domain.endswith(_ATS_PLACEHOLDER_SUFFIX)


def _is_placeholder_company(company: Company) -> bool:
    return _is_placeholder_domain(company.domain)


def _merge_source_controls(
    source: CareerSource,
    equivalent_sources: Iterable[CareerSource],
) -> None:
    controls = (source, *equivalent_sources)
    enabled = all(item.is_enabled for item in controls)
    blocked_at = max(
        (item.blocked_at for item in controls if item.blocked_at is not None),
        default=None,
    )
    request_delay_seconds = max(item.request_delay_seconds for item in controls)
    max_pages = min(item.max_pages for item in controls)
    updates: list[str] = []
    if source.is_enabled != enabled:
        source.is_enabled = enabled
        updates.append("is_enabled")
    if source.blocked_at != blocked_at:
        source.blocked_at = blocked_at
        updates.append("blocked_at")
    if source.request_delay_seconds != request_delay_seconds:
        source.request_delay_seconds = request_delay_seconds
        updates.append("request_delay_seconds")
    if source.max_pages != max_pages:
        source.max_pages = max_pages
        updates.append("max_pages")
    if updates:
        source.save(update_fields=updates)


def _retire_equivalent_sources(
    source: CareerSource,
    equivalent_sources: Iterable[CareerSource],
) -> None:
    _migrate_jobs_to_canonical(source, equivalent_sources)
    for equivalent in equivalent_sources:
        config = dict(equivalent.config) if isinstance(equivalent.config, dict) else {}
        config["canonical_source_url"] = source.source_url
        updates: list[str] = []
        if equivalent.is_enabled:
            equivalent.is_enabled = False
            updates.append("is_enabled")
        if equivalent.config != config:
            equivalent.config = config
            updates.append("config")
        if updates:
            equivalent.save(update_fields=updates)


def _migrate_jobs_to_canonical(
    source: CareerSource,
    equivalent_sources: Iterable[CareerSource],
) -> None:
    for equivalent in equivalent_sources:
        for job in Job.objects.filter(source=equivalent).order_by("pk").iterator():
            identity = Q(external_id=job.external_id)
            if job.canonical_url:
                identity |= Q(canonical_url=job.canonical_url)
            canonical_job = Job.objects.filter(source=source).filter(identity).first()
            if canonical_job is None:
                job.source = source
                job.save(update_fields=["source"])
                continue
            _merge_job_references(job, canonical_job)
            job.delete()


def _merge_job_references(source_job: Job, canonical_job: Job) -> None:
    for state in UserJobState.objects.filter(job=source_job):
        existing_state = UserJobState.objects.filter(
            user=state.user,
            job=canonical_job,
        ).first()
        if existing_state is None:
            state.job = canonical_job
            state.save(update_fields=["job"])
        else:
            _merge_user_job_state(existing_state, state)
            state.delete()
    for match in JobMatch.objects.filter(job=source_job):
        existing_match = JobMatch.objects.filter(
            profile=match.profile,
            job=canonical_job,
        ).first()
        if existing_match is None:
            match.job = canonical_job
            match.save(update_fields=["job"])
        else:
            match.delete()


_USER_JOB_STATUS_PRIORITY: dict[str, int] = {
    UserJobState.Status.NONE: 0,
    UserJobState.Status.SAVED: 10,
    UserJobState.Status.IGNORED: 10,
    UserJobState.Status.REJECTED: 20,
    UserJobState.Status.APPLIED: 30,
    UserJobState.Status.INTERVIEWING: 40,
    UserJobState.Status.OFFER: 50,
}


def _merge_user_job_state(target: UserJobState, incoming: UserJobState) -> None:
    updates: list[str] = []
    target_priority = _USER_JOB_STATUS_PRIORITY[target.status]
    incoming_priority = _USER_JOB_STATUS_PRIORITY[incoming.status]
    if incoming_priority > target_priority or (
        incoming_priority == target_priority and incoming.updated_at > target.updated_at
    ):
        target.status = incoming.status
        updates.append("status")

    notes = "\n\n".join(
        dict.fromkeys(note for note in (target.notes.strip(), incoming.notes.strip()) if note)
    )
    if notes != target.notes:
        target.notes = notes
        updates.append("notes")

    seen_at = min(
        (value for value in (target.seen_at, incoming.seen_at) if value is not None),
        default=None,
    )
    if seen_at != target.seen_at:
        target.seen_at = seen_at
        updates.append("seen_at")
    if updates:
        target.save(update_fields=[*updates, "updated_at"])


def _collect_registered_source(
    source: CareerSource,
    *,
    errors: list[str],
) -> CrawlRun | None:
    try:
        run = collect_source(source=source, registry=collector_registry)
        return None if run.status == CrawlRun.Status.RUNNING else run
    except Exception as error:
        errors.append(str(error))
        return None


def _close_if_available(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()
