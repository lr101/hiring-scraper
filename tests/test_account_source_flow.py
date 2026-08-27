import json
from pathlib import Path

import httpx
import pytest
import respx
from django.test import Client, override_settings
from django.urls import reverse

from jobs.company_discovery import DiscoveredCompany
from jobs.company_locations import CompanyLocationDiscovery
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
def test_adding_a_city_finds_websites_adds_companies_and_starts_scans(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    SearchProfile.objects.create(user=user, name="Engineering")
    place = make_place()
    select_account(client, user)
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
    assert Job.objects.filter(source=source, title="Senior Python Engineer").exists()


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
    assert [(candidate.name, candidate.domain) for candidate in candidates] == [
        ("Acme GmbH", "acme.test")
    ]
