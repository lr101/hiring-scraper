from __future__ import annotations

from datetime import date
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.utils import timezone

from jobs.ba_discovery import BAJobSignal, EmployerDiscoveryResult, EmployerHiringSignal
from jobs.common_crawl import ATSTenantDescriptor
from jobs.company_discovery import DiscoveredCompany
from jobs.company_locations import CompanyLocationDiscovery
from jobs.employer_resolution import ResolvedEmployer
from jobs.models import (
    CareerSource,
    Company,
    CrawlRun,
    GermanPlace,
    MonitoringTarget,
    WorkspaceUser,
)
from jobs.reverse_discovery import ReverseDiscoveryResult, ReverseDiscoveryService


def hiring_signal() -> EmployerHiringSignal:
    signal = BAJobSignal(
        employer_name="Acme GmbH",
        job_title="Backend Engineer",
        location="Berlin",
        postal_code="10115",
        reference_number="10000-1-S",
        published_at=date(2026, 8, 25),
    )
    return EmployerHiringSignal(
        normalized_name="acme gmbh",
        employer_name="Acme GmbH",
        total_active_jobs=1,
        recent_jobs=1,
        distinct_locations=("Berlin",),
        signals=(signal,),
    )


@pytest.mark.django_db
def test_reverse_discovery_runs_stages_in_order_and_collects_fingerprinted_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    user = WorkspaceUser.objects.create(name="Ada")
    tenant = ATSTenantDescriptor(
        kind="greenhouse",
        tenant="acme",
        source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        evidence_urls=("https://boards.greenhouse.io/acme/jobs/123",),
        snapshot="CC-MAIN-2026-30",
    )

    class CommonCrawl:
        def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]:
            events.append("ats")
            return (tenant,)

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            events.append("ba")
            return EmployerDiscoveryResult(
                employers=(hiring_signal(),), is_complete=True, requests_made=1
            )

    class Resolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer:
            assert employer.normalized_name == "acme gmbh"
            events.append("resolve")
            return ResolvedEmployer(
                employer_name=employer.employer_name,
                normalized_name=employer.normalized_name,
                domain="acme.test",
                website_url="https://acme.test/",
                resolver="osm",
            )

    def discover_company(domain: str) -> DiscoveredCompany:
        assert domain == "https://acme.test/"
        events.append("fingerprint")
        return DiscoveredCompany(
            domain="acme.test",
            name="Acme GmbH",
            website_url="https://acme.test/",
            career_url="https://boards.greenhouse.io/acme/jobs",
            allowed_hosts=("acme.test", "boards.greenhouse.io"),
        )

    def collect(source: CareerSource, *, registry: object) -> SimpleNamespace:
        del registry
        events.append("collect")
        return SimpleNamespace(
            status="success",
            jobs_created=2,
            jobs_closed=0,
            source_id=source.pk,
        )

    monkeypatch.setattr("jobs.reverse_discovery.collect_source", collect)
    service = ReverseDiscoveryService(
        common_crawl_client=CommonCrawl(),
        ba_service=BAService(),
        resolver=Resolver(),
        company_discoverer=discover_company,
    )

    result = service.discover(city="Berlin", user=user)

    assert events == ["ats", "ba", "resolve", "fingerprint", "collect"]
    source = CareerSource.objects.get(company__domain="acme.test")
    assert source.kind == CareerSource.Kind.GREENHOUSE
    assert source.tenant == "acme"
    assert source.source_url == tenant.source_url
    assert MonitoringTarget.objects.get(user=user, company__domain="acme.test")
    assert result.total_active_jobs == 1
    assert result.new_jobs == 2


@pytest.mark.django_db
def test_reverse_discovery_keeps_global_discovery_account_independent() -> None:
    class CommonCrawl:
        def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]:
            return ()

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            return EmployerDiscoveryResult(employers=(), is_complete=True, requests_made=1)

    result = ReverseDiscoveryService(
        common_crawl_client=CommonCrawl(),
        ba_service=BAService(),
    ).discover(city="Berlin")

    assert result.errors == ()
    assert MonitoringTarget.objects.count() == 0


@pytest.mark.django_db
def test_global_validated_ats_tenants_register_and_collect_without_an_account_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    tenant = ATSTenantDescriptor(
        kind="greenhouse",
        tenant="acme",
        source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        evidence_urls=("https://boards.greenhouse.io/acme/jobs/123",),
        snapshot="CC-MAIN-2026-30",
    )

    class CommonCrawl:
        def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]:
            events.append("ats")
            return (tenant,)

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            pytest.fail("A global ATS probe should not query BA")

    def collect(source: CareerSource, *, registry: object) -> SimpleNamespace:
        del registry
        events.append("collect")
        assert source.kind == CareerSource.Kind.GREENHOUSE
        return SimpleNamespace(status=CrawlRun.Status.SUCCESS, jobs_created=3)

    monkeypatch.setattr("jobs.reverse_discovery.collect_source", collect)
    result = ReverseDiscoveryService(
        common_crawl_client=CommonCrawl(),
        ba_service=BAService(),
    ).discover(city=None, collect=True)

    source = CareerSource.objects.get(source_url=tenant.source_url)
    assert events == ["ats", "collect"]
    assert source.tenant == tenant.tenant
    assert source.company.domain == "acme.greenhouse.ats.invalid"
    assert MonitoringTarget.objects.count() == 0
    assert result.companies_created == 1
    assert result.new_jobs == 3


@pytest.mark.django_db
def test_ats_canonicalization_inherits_direct_source_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    direct_company = Company.objects.create(
        name="Acme GmbH",
        domain="acme.test",
        career_url="https://acme.test/careers",
    )
    blocked_at = timezone.now()
    direct_source = CareerSource.objects.create(
        company=direct_company,
        kind=CareerSource.Kind.JSON_LD,
        source_url="https://boards.greenhouse.io/acme/jobs",
        is_enabled=False,
        blocked_at=blocked_at,
        request_delay_seconds=9,
        max_pages=4,
    )

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            return EmployerDiscoveryResult(
                employers=(hiring_signal(),), is_complete=True, requests_made=1
            )

    class Resolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer:
            del employer
            return ResolvedEmployer(
                employer_name="Acme GmbH",
                normalized_name="acme gmbh",
                domain="acme.test",
                website_url="https://acme.test/",
                resolver="osm",
            )

    def discover_company(_website_url: str) -> DiscoveredCompany:
        return DiscoveredCompany(
            domain="acme.test",
            name="Acme GmbH",
            website_url="https://acme.test/",
            career_url=direct_source.source_url,
        )

    def collect(**kwargs: object) -> SimpleNamespace:
        del kwargs
        pytest.fail("A blocked equivalent source must not be collected")

    monkeypatch.setattr("jobs.reverse_discovery.collect_source", collect)
    result = ReverseDiscoveryService(
        ba_service=BAService(),
        resolver=Resolver(),
        company_discoverer=discover_company,
    ).discover(city="Berlin", user=user, include_common_crawl=False)

    canonical = CareerSource.objects.get(
        source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    )
    assert canonical.company_id == direct_company.pk
    assert canonical.is_enabled is False
    assert canonical.blocked_at == blocked_at
    assert canonical.request_delay_seconds == direct_source.request_delay_seconds
    assert canonical.max_pages == direct_source.max_pages
    direct_source.refresh_from_db()
    assert direct_source.is_enabled is False
    assert MonitoringTarget.objects.filter(user=user, company=direct_company).exists()
    assert result.errors == ()


@pytest.mark.django_db
def test_global_ats_placeholder_promotes_to_ba_company_without_unique_url_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    tenant = ATSTenantDescriptor(
        kind="greenhouse",
        tenant="acme",
        source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        evidence_urls=("https://boards.greenhouse.io/acme/jobs/123",),
        snapshot="CC-MAIN-2026-30",
    )

    class CommonCrawl:
        def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]:
            return (tenant,)

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            return EmployerDiscoveryResult(
                employers=(hiring_signal(),), is_complete=True, requests_made=1
            )

    class Resolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer:
            del employer
            return ResolvedEmployer(
                employer_name="Acme GmbH",
                normalized_name="acme gmbh",
                domain="acme.test",
                website_url="https://acme.test/",
                resolver="osm",
            )

    def discover_company(_website_url: str) -> DiscoveredCompany:
        return DiscoveredCompany(
            domain="acme.test",
            name="Acme GmbH",
            website_url="https://acme.test/",
            career_url=tenant.source_url,
        )

    service = ReverseDiscoveryService(
        common_crawl_client=CommonCrawl(),
        ba_service=BAService(),
        resolver=Resolver(),
        company_discoverer=discover_company,
    )
    initial = service.discover(city=None, collect=False)
    result = service.discover(
        city="Berlin",
        user=user,
        collect=False,
        include_common_crawl=False,
    )

    source = CareerSource.objects.get(source_url=tenant.source_url)
    assert initial.errors == ()
    assert result.errors == ()
    assert source.company.domain == "acme.test"
    assert MonitoringTarget.objects.filter(user=user, company=source.company).exists()
    assert Company.objects.filter(domain="acme.greenhouse.ats.invalid").exists() is False


@pytest.mark.django_db
def test_ats_alias_is_retired_after_canonical_source_registration() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    direct_company = Company.objects.create(
        name="Acme GmbH",
        domain="acme.test",
        career_url="https://acme.test/careers",
    )
    direct_source = CareerSource.objects.create(
        company=direct_company,
        kind=CareerSource.Kind.JSON_LD,
        source_url="https://boards.greenhouse.io/acme/jobs",
        request_delay_seconds=7,
        max_pages=8,
    )

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            return EmployerDiscoveryResult(
                employers=(hiring_signal(),), is_complete=True, requests_made=1
            )

    class Resolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer:
            del employer
            return ResolvedEmployer(
                employer_name="Acme GmbH",
                normalized_name="acme gmbh",
                domain="acme.test",
                website_url="https://acme.test/",
                resolver="osm",
            )

    def discover_company(_website_url: str) -> DiscoveredCompany:
        return DiscoveredCompany(
            domain="acme.test",
            name="Acme GmbH",
            website_url="https://acme.test/",
            career_url=direct_source.source_url,
        )

    result = ReverseDiscoveryService(
        ba_service=BAService(),
        resolver=Resolver(),
        company_discoverer=discover_company,
    ).discover(city="Berlin", user=user, collect=False, include_common_crawl=False)

    canonical = CareerSource.objects.get(
        source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    )
    direct_source.refresh_from_db()
    assert result.errors == ()
    assert canonical.is_enabled is True
    assert direct_source.is_enabled is False
    assert direct_source.config["canonical_source_url"] == canonical.source_url


@pytest.mark.django_db
def test_city_discovery_uses_ba_resolution_before_the_osm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    user = WorkspaceUser.objects.create(name="Ada")
    place = SimpleNamespace(name="Berlin")

    class CommonCrawl:
        def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]:
            pytest.fail("City discovery should not run the global Common Crawl scan.")

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            events.append("ba")
            return EmployerDiscoveryResult(
                employers=(hiring_signal(),), is_complete=True, requests_made=1
            )

    class Resolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer:
            events.append("resolve")
            return ResolvedEmployer(
                employer_name=employer.employer_name,
                normalized_name=employer.normalized_name,
                domain="acme.test",
                website_url="https://acme.test/",
                resolver="osm",
            )

    def discover_company(_domain: str) -> DiscoveredCompany:
        events.append("fingerprint")
        return DiscoveredCompany(
            domain="acme.test",
            name="Acme GmbH",
            website_url="https://acme.test/",
            career_url="https://acme.test/careers",
            allowed_hosts=("acme.test",),
        )

    def collect(**kwargs: object) -> SimpleNamespace:
        del kwargs
        events.append("collect")
        return SimpleNamespace(jobs_created=1, status="success")

    monkeypatch.setattr("jobs.reverse_discovery.collect_source", collect)
    service = ReverseDiscoveryService(
        common_crawl_client=CommonCrawl(),
        ba_service=BAService(),
        resolver=Resolver(),
        company_discoverer=discover_company,
    )
    result = service.discover_city(
        user=user,
        place=place,
        radius_km=25,
        fallback_discoverer=lambda: pytest.fail("OSM fallback should not run"),
    )

    assert events == ["ba", "resolve", "fingerprint", "collect"]
    assert result.used_osm_fallback is False
    assert result.companies_added == 1
    assert result.new_jobs == 1
    assert MonitoringTarget.objects.filter(user=user, company__domain="acme.test").exists()


@pytest.mark.django_db
def test_city_discovery_uses_osm_only_when_ba_produces_no_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = SimpleNamespace(name="Berlin")
    fallback_company = DiscoveredCompany(
        domain="fallback.test",
        name="Fallback GmbH",
        website_url="https://fallback.test/",
        career_url="https://fallback.test/careers",
        allowed_hosts=("fallback.test",),
    )

    class CommonCrawl:
        def discover_validated(self) -> tuple[ATSTenantDescriptor, ...]:
            return ()

    class BAService:
        def discover_and_persist(self, **kwargs: object) -> EmployerDiscoveryResult:
            del kwargs
            return EmployerDiscoveryResult(employers=(), is_complete=True, requests_made=1)

    monkeypatch.setattr(
        "jobs.reverse_discovery.collect_source",
        lambda **kwargs: SimpleNamespace(jobs_created=0, status="success"),
    )
    service = ReverseDiscoveryService(common_crawl_client=CommonCrawl(), ba_service=BAService())

    result = service.discover_city(
        user=user,
        place=place,
        radius_km=25,
        fallback_discoverer=lambda: CompanyLocationDiscovery(
            companies=(fallback_company,), unreadable_websites=2
        ),
    )

    assert result.used_osm_fallback is True
    assert result.unreadable_websites == 2
    assert result.companies_added == 1
    assert MonitoringTarget.objects.filter(user=user, company__domain="fallback.test").exists()


def test_reverse_discover_command_passes_bounded_options_to_the_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    tenant = ATSTenantDescriptor(
        kind="greenhouse",
        tenant="acme",
        source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        evidence_urls=(),
        snapshot="CC-MAIN-2026-30",
    )

    class Service:
        def discover(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                ats_tenants=(tenant,),
                resolved_employers=(SimpleNamespace(),),
                discovered_companies=(SimpleNamespace(),),
                new_jobs=2,
                companies_added=1,
                blocked_sources=0,
                errors=(),
                is_complete=True,
            )

        def close(self) -> None:
            calls.append({"closed": True})

    monkeypatch.setattr(
        "jobs.management.commands.reverse_discover.ReverseDiscoveryService", Service
    )
    output = StringIO()

    call_command(
        "reverse_discover",
        "--city",
        "Berlin",
        "--radius-km",
        "15",
        "--publication-age-days",
        "7",
        "--offer-type",
        "2",
        "--include-temporary-agencies",
        "--max-pages",
        "3",
        "--no-collect",
        stdout=output,
    )

    assert calls == [
        {
            "city": "Berlin",
            "radius_km": 15,
            "publication_age_days": 7,
            "offer_type": 2,
            "include_temporary_agencies": True,
            "max_pages": 3,
            "collect": False,
        },
        {"closed": True},
    ]
    assert "1 ATS tenant" in output.getvalue()
    assert "1 employer" in output.getvalue()
    assert "2 new jobs" in output.getvalue()


@pytest.mark.django_db
def test_scheduled_reverse_discovery_scans_saved_city_targets_after_global_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    target = MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=GermanPlace.objects.create(
            source_id="test:scheduled-berlin",
            name="Berlin",
            normalized_name="berlin",
            latitude=52.52,
            longitude=13.405,
            source_kind=GermanPlace.SourceKind.CITY,
        ),
        radius_km=25,
    )
    calls: list[object] = []

    class Service:
        def discover(self, **kwargs: object) -> SimpleNamespace:
            calls.append(("global", kwargs))
            return SimpleNamespace(
                ats_tenants=(SimpleNamespace(),),
                errors=(),
                is_complete=True,
                companies_added=1,
                runs=(SimpleNamespace(status="success", jobs_created=2),),
                new_jobs=2,
                blocked_sources=0,
            )

        def close(self) -> None:
            calls.append("closed")

    monkeypatch.setattr("jobs.reverse_discovery.ReverseDiscoveryService", Service)

    def run_city(target_id: int) -> dict[str, int | str]:
        calls.append(("city", target_id))
        return {
            "status": "complete",
            "added_companies": 2,
            "scanned_sources": 2,
            "new_jobs": 3,
            "blocked_sources": 1,
            "unreadable_websites": 4,
        }

    monkeypatch.setattr("jobs.tasks.discover_city_sources", SimpleNamespace(run=run_city))

    from jobs.tasks import reverse_discover_sources

    result = reverse_discover_sources.run()

    assert calls == [
        ("global", {"city": None, "collect": True}),
        "closed",
        ("city", target.pk),
    ]
    assert result == {
        "status": "complete",
        "ats_tenants": 1,
        "city_targets": 1,
        "added_companies": 3,
        "scanned_sources": 3,
        "new_jobs": 5,
        "blocked_sources": 1,
        "unreadable_websites": 4,
        "errors": 0,
    }


@pytest.mark.django_db
def test_city_task_preserves_partial_provider_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = GermanPlace.objects.create(
        source_id="test:partial-berlin",
        name="Berlin",
        normalized_name="berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    target = MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=place,
        radius_km=25,
    )

    def partial_result(**kwargs: object) -> ReverseDiscoveryResult:
        del kwargs
        return ReverseDiscoveryResult(errors=("BA provider unavailable",))

    monkeypatch.setattr("jobs.views._discover_city_result", partial_result)

    from jobs.tasks import discover_city_sources

    result = discover_city_sources.run(target.pk)

    assert result["status"] == "partial"
    assert result["errors"] == 1


@pytest.mark.django_db
def test_scheduled_reverse_discovery_counts_all_city_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = GermanPlace.objects.create(
        source_id="test:error-count-berlin",
        name="Berlin",
        normalized_name="berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=place,
        radius_km=25,
    )

    class Service:
        def discover(self, **kwargs: object) -> ReverseDiscoveryResult:
            del kwargs
            return ReverseDiscoveryResult()

        def close(self) -> None:
            pass

    monkeypatch.setattr("jobs.reverse_discovery.ReverseDiscoveryService", Service)

    def run_city(_target_id: int) -> dict[str, int | str]:
        return {"status": "partial", "errors": 2}

    monkeypatch.setattr("jobs.tasks.discover_city_sources", SimpleNamespace(run=run_city))

    from jobs.tasks import reverse_discover_sources

    result = reverse_discover_sources.run()

    assert result["status"] == "partial"
    assert result["errors"] == 2
