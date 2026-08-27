import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from django.test import Client, override_settings
from django.urls import reverse

from jobs.ba_discovery import EmployerDiscoveryResult, EmployerHiringSignal
from jobs.company_discovery import DiscoveredCompany
from jobs.company_locations import CompanyLocationDiscovery
from jobs.employer_resolution import ResolvedEmployer
from jobs.models import (
    CareerSource,
    Company,
    CrawlRun,
    GermanPlace,
    Job,
    JobMatch,
    MonitoringTarget,
    SearchProfile,
    WorkspaceUser,
)
from jobs.reverse_discovery import ReverseDiscoveryResult
from jobs.reverse_discovery import ReverseDiscoveryService as RealReverseDiscoveryService

FIXTURES = Path(__file__).parent / "fixtures"


def select_account(client: Client, user: WorkspaceUser) -> None:
    client.cookies["workspace_user"] = str(user.pk)


def make_place() -> GermanPlace:
    return GermanPlace.objects.create(
        source_id="test:berlin",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )


def mock_company_scan(domain: str = "acme.test") -> None:
    respx.get(f"https://{domain}/").mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "company-home.html").read_text(),
            headers={"content-type": "text/html"},
        )
    )
    respx.get(f"https://{domain}/careers").mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "career-page.html").read_text(),
            headers={"content-type": "text/html"},
        )
    )


def use_empty_ba_reverse_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyBA:
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
        ) -> EmployerDiscoveryResult:
            del (
                city,
                radius_km,
                publication_age_days,
                offer_type,
                include_temporary_agencies,
                max_pages,
                as_of,
            )
            return EmployerDiscoveryResult(employers=(), is_complete=True, requests_made=0)

    class EmptyResolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer | None:
            del employer
            return None

    class Service:
        def __init__(self) -> None:
            self.delegate = RealReverseDiscoveryService(
                ba_service=EmptyBA(),
                resolver=EmptyResolver(),
            )

        def discover_city(self, **kwargs: object) -> ReverseDiscoveryResult:
            return self.delegate.discover_city(**kwargs)  # type: ignore[arg-type]

        def close(self) -> None:
            self.delegate.close()

    monkeypatch.setattr("jobs.views.ReverseDiscoveryService", Service)


@pytest.mark.django_db
def test_profile_form_is_about_job_criteria_not_location_selection(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    response = client.get(reverse("jobs:profile_create"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "City radii" not in content
    assert "profile_locations" not in content
    assert "Use one title per line or separate titles with commas" in content
    assert "One alternative group per line" in content


@pytest.mark.django_db
def test_setup_page_is_the_single_place_to_choose_companies_and_cities(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    response = client.get(reverse("jobs:setup"))

    assert response.status_code == 200
    assert b"Where to look for jobs" in response.content
    assert b"Every enabled job profile" in response.content
    assert b"Add a company website" in response.content
    assert b"Find companies by city" in response.content
    assert b"Find companies again" not in response.content


@pytest.mark.django_db
@respx.mock
def test_adding_a_website_starts_a_scan_and_matches_every_profile_in_the_account(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    SearchProfile.objects.create(user=user, name="Engineering")
    SearchProfile.objects.create(user=user, name="Backend")
    select_account(client, user)
    mock_company_scan()

    response = client.post(reverse("jobs:company_target_create"), {"domain": "acme.test"})

    assert response.status_code == 302
    company = Company.objects.get(domain="acme.test")
    source = CareerSource.objects.get(company=company)
    job = Job.objects.get(source=source)
    assert MonitoringTarget.objects.filter(user=user, company=company).exists()
    assert CrawlRun.objects.filter(source=source, status=CrawlRun.Status.SUCCESS).count() == 1
    assert JobMatch.objects.filter(job=job, profile__user=user).count() == 2

    feed = client.get(reverse("jobs:feed"))
    assert b"Senior Python Engineer" in feed.content


@pytest.mark.django_db
@respx.mock
@override_settings(
    COMPANY_LOCATION_API_URL="https://overpass.test/api/interpreter",
    COMPANY_LOCATION_MIN_REQUEST_INTERVAL_SECONDS=0,
)
def test_adding_a_city_finds_websites_adds_companies_and_starts_scans(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    SearchProfile.objects.create(user=user, name="Engineering")
    SearchProfile.objects.create(user=user, name="Backend")
    place = make_place()
    select_account(client, user)
    use_empty_ba_reverse_discovery(monkeypatch)
    respx.get("https://overpass.test/api/interpreter").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((FIXTURES / "company_locations" / "berlin.json").read_text()),
        )
    )
    mock_company_scan()

    response = client.post(
        reverse("jobs:city_target_create"),
        {"place": place.pk, "place_query": "Berlin", "radius_km": 25},
    )

    assert response.status_code == 302
    company = Company.objects.get(domain="acme.test")
    source = CareerSource.objects.get(company=company)
    assert MonitoringTarget.objects.filter(user=user, place=place).exists()
    assert MonitoringTarget.objects.filter(user=user, company=company).exists()
    assert CrawlRun.objects.filter(source=source, status=CrawlRun.Status.SUCCESS).exists()
    job = Job.objects.get(source=source, title="Senior Python Engineer")
    assert JobMatch.objects.filter(job=job, profile__user=user).count() == 2


@pytest.mark.django_db
def test_city_discovery_delegates_to_reverse_discovery_before_the_osm_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = make_place()
    events: list[str] = []

    class Service:
        def discover_city(self, **kwargs: object) -> ReverseDiscoveryResult:
            events.append("reverse")
            assert kwargs["user"] is user
            assert kwargs["place"] is place
            assert kwargs["radius_km"] == 25
            assert callable(kwargs["fallback_discoverer"])
            return ReverseDiscoveryResult(companies_created=2, unreadable_websites=1)

        def close(self) -> None:
            events.append("closed")

    monkeypatch.setattr("jobs.views.ReverseDiscoveryService", Service)
    monkeypatch.setattr(
        "jobs.views.discover_companies_in_place",
        lambda *args, **kwargs: pytest.fail("OSM should only run through the fallback callback"),
    )

    from jobs.views import _discover_and_scan_city

    added_companies, scans, unreadable_websites = _discover_and_scan_city(
        user=user,
        place=place,
        radius_km=25,
    )

    assert events == ["reverse", "closed"]
    assert added_companies == 2
    assert scans == []
    assert unreadable_websites == 1


@pytest.mark.django_db
@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
def test_city_search_is_queued_instead_of_blocking_the_web_request(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = make_place()
    select_account(client, user)
    synchronous_calls: list[int] = []
    queued_target_ids: list[int] = []

    def discover_synchronously(
        *, user: WorkspaceUser, place: GermanPlace, radius_km: int
    ) -> tuple[int, list[CrawlRun], int]:
        del user, radius_km
        synchronous_calls.append(place.pk)
        return 0, [], 0

    def queue_search(target: MonitoringTarget) -> None:
        queued_target_ids.append(target.pk)

    monkeypatch.setattr("jobs.views._discover_and_scan_city", discover_synchronously)
    monkeypatch.setattr("jobs.views._queue_city_search", queue_search, raising=False)

    response = client.post(
        reverse("jobs:city_target_create"),
        {"place": place.pk, "place_query": "Berlin", "radius_km": 25},
    )

    assert response.status_code == 302
    assert synchronous_calls == []
    assert len(queued_target_ids) == 1


@pytest.mark.django_db
def test_city_search_can_be_run_again_for_newly_mapped_companies(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = make_place()
    target = MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=place,
        radius_km=25,
    )
    select_account(client, user)
    discovery = DiscoveredCompany(
        domain="newco.test",
        name="Newco GmbH",
        website_url="https://newco.test/",
        career_url="https://newco.test/careers",
        allowed_hosts=("newco.test", "www.newco.test"),
    )
    lookup_calls: list[tuple[int, int]] = []
    use_empty_ba_reverse_discovery(monkeypatch)

    def lookup(selected_place: GermanPlace, radius_km: int) -> CompanyLocationDiscovery:
        lookup_calls.append((selected_place.pk, radius_km))
        return CompanyLocationDiscovery(companies=(discovery,))

    monkeypatch.setattr(
        "jobs.views.discover_companies_in_place",
        lookup,
    )
    monkeypatch.setattr("jobs.views._run_initial_scan", lambda _source: None)

    response = client.post(reverse("jobs:city_target_refresh", args=[target.pk]))

    assert response.status_code == 302
    assert lookup_calls == [(place.pk, 25)]
    assert Company.objects.filter(domain="newco.test").exists()
    assert MonitoringTarget.objects.filter(user=user, company__domain="newco.test").exists()


@pytest.mark.django_db
@respx.mock
@override_settings(COMPANY_LOCATION_MIN_REQUEST_INTERVAL_SECONDS=0)
def test_overpass_company_provider_returns_unique_websites_with_names() -> None:
    from jobs.company_locations import OverpassCompanyProvider

    place = make_place()
    route = respx.get("https://overpass-api.de/api/interpreter").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((FIXTURES / "company_locations" / "berlin.json").read_text()),
        )
    )

    provider = OverpassCompanyProvider()
    candidates = provider.search(place, radius_km=25)
    provider.close()

    assert route.called
    assert "52.52" in route.calls[0].request.url.params["data"]
    assert '"operator:website"' in route.calls[0].request.url.params["data"]
    assert [(candidate.name, candidate.domain) for candidate in candidates] == [
        ("Acme GmbH", "acme.test")
    ]


@pytest.mark.django_db
@respx.mock
@override_settings(
    COMPANY_LOCATION_API_URL="https://overpass.test/api/interpreter",
    COMPANY_LOCATION_FALLBACK_API_URL="https://fallback.test/api/interpreter",
    COMPANY_LOCATION_MIN_REQUEST_INTERVAL_SECONDS=0,
)
def test_overpass_provider_uses_a_fallback_after_a_transient_provider_failure() -> None:
    from jobs.company_locations import OverpassCompanyProvider

    place = make_place()
    primary = respx.get("https://overpass.test/api/interpreter").mock(
        return_value=httpx.Response(504, text="overloaded")
    )
    fallback = respx.get("https://fallback.test/api/interpreter").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((FIXTURES / "company_locations" / "berlin.json").read_text()),
        )
    )

    provider = OverpassCompanyProvider()
    candidates = provider.search(place, radius_km=25)
    provider.close()

    assert primary.called
    assert fallback.called
    assert [(candidate.name, candidate.domain) for candidate in candidates] == [
        ("Acme GmbH", "acme.test")
    ]


@pytest.mark.django_db
@respx.mock
@override_settings(
    COMPANY_LOCATION_API_URL="https://overpass.test/api/interpreter",
    COMPANY_LOCATION_FALLBACK_API_URL="https://fallback.test/api/interpreter",
    COMPANY_LOCATION_MIN_REQUEST_INTERVAL_SECONDS=0,
)
def test_overpass_provider_does_not_treat_a_timeout_remark_as_an_empty_search() -> None:
    from jobs.company_locations import OverpassCompanyProvider

    place = make_place()
    respx.get("https://overpass.test/api/interpreter").mock(
        return_value=httpx.Response(
            200,
            json={
                "version": 0.6,
                "elements": [],
                "remark": 'runtime error: Query timed out in "query" at line 1 after 26 seconds.',
            },
        )
    )
    fallback = respx.get("https://fallback.test/api/interpreter").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((FIXTURES / "company_locations" / "berlin.json").read_text()),
        )
    )

    provider = OverpassCompanyProvider()
    candidates = provider.search(place, radius_km=25)
    provider.close()

    assert fallback.called
    assert [(candidate.name, candidate.domain) for candidate in candidates] == [
        ("Acme GmbH", "acme.test")
    ]


@override_settings(COMPANY_LOCATION_LOOKUP_TIMEOUT_SECONDS=20)
def test_overpass_provider_waits_for_a_queued_query_to_finish() -> None:
    from jobs.company_locations import OverpassCompanyProvider

    provider = OverpassCompanyProvider(min_interval_seconds=0)
    try:
        read_timeout = provider._client.timeout.read
        assert read_timeout is not None
        assert read_timeout >= 40
    finally:
        provider.close()


@pytest.mark.django_db
@respx.mock
def test_manual_website_block_is_explained_after_the_company_is_added(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)
    respx.get("https://acme.test/").mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "company-home.html").read_text(),
            headers={"content-type": "text/html"},
        )
    )
    respx.get("https://acme.test/careers").mock(
        return_value=httpx.Response(403, text="Access denied")
    )

    response = client.post(
        reverse("jobs:company_target_create"), {"domain": "acme.test"}, follow=True
    )

    assert response.status_code == 200
    assert b"blocked automated access" in response.content
    source = CareerSource.objects.get(company__domain="acme.test")
    assert source.blocked_at is not None
    assert source.runs.get().status == CrawlRun.Status.BLOCKED
    assert b"site refused automated access" in client.get(reverse("jobs:source_list")).content
