import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import respx
from django.test import Client, override_settings
from django.urls import reverse

from jobs import network
from jobs.collection import collect_source, collector_registry
from jobs.collectors import CollectorRegistry
from jobs.forms import CompanyMonitoringTargetForm
from jobs.models import CareerSource, Company, GermanPlace, Job, MonitoringTarget, WorkspaceUser

FIXTURES = Path(__file__).parent / "fixtures"


def select_account(client: Client, user: WorkspaceUser) -> None:
    client.cookies["workspace_user"] = str(user.pk)


def test_normalize_domain_accepts_a_pasted_website_url() -> None:
    from jobs.company_discovery import normalize_domain

    assert normalize_domain("  https://www.Acme.test/careers?source=jobs  ") == "acme.test"


@pytest.mark.parametrize(
    "value",
    ["", "not a domain", "ftp://acme.test", "https://127.0.0.1", "https://8.8.8.8"],
)
def test_normalize_domain_rejects_unsafe_or_malformed_values(value: str) -> None:
    from jobs.company_discovery import normalize_domain

    with pytest.raises(ValueError):
        normalize_domain(value)


@override_settings(DEBUG=False)
def test_public_hostname_validation_rejects_private_dns_results() -> None:
    with patch(
        "jobs.network.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 0))],
    ):
        with pytest.raises(network.UnsafeNetworkAddress):
            network.validate_public_hostname("company.example")


def test_location_rate_limiter_writes_a_shared_timestamp(tmp_path: Path) -> None:
    from jobs import locations

    state_path = tmp_path / "provider-rate-limit"

    with (
        patch("jobs.locations.time.monotonic", return_value=100.0),
        override_settings(LOCATION_RATE_LIMIT_STATE_PATH=str(state_path)),
    ):
        locations._wait_for_provider_rate_limit(1)

    assert float(state_path.read_text()) == 100.0


@pytest.mark.django_db
def test_company_target_form_uses_a_domain_input_instead_of_a_company_choice() -> None:
    user = WorkspaceUser.objects.create(name="Ada")

    form = CompanyMonitoringTargetForm(user=user)

    assert "domain" in form.fields
    assert "company" not in form.fields


@pytest.mark.django_db
@respx.mock
def test_company_target_uses_the_domain_to_discover_the_company_and_career_source(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)
    respx.get("https://acme.test/").mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "company-home.html").read_text(),
            headers={"content-type": "text/html"},
        )
    )

    response = client.post(reverse("jobs:company_target_create"), {"domain": "www.acme.test"})

    assert response.status_code == 302
    company = Company.objects.get(domain="acme.test")
    assert company.name == "Acme GmbH"
    assert company.career_url == "https://acme.test/careers"
    source = CareerSource.objects.get(company=company)
    assert source.kind == CareerSource.Kind.JSON_LD
    assert source.source_url == "https://acme.test/careers"
    assert source.config["allowed_hosts"] == ["acme.test", "www.acme.test"]
    assert MonitoringTarget.objects.get(user=user, company=company).kind == "company"


@pytest.mark.django_db
@respx.mock
def test_company_target_reuses_an_existing_company_without_fetching_its_website(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    company = Company.objects.create(
        name="Existing Acme", domain="acme.test", career_url="https://acme.test/careers"
    )
    CareerSource.objects.create(
        company=company, kind=CareerSource.Kind.JSON_LD, source_url=company.career_url
    )
    select_account(client, user)

    response = client.post(reverse("jobs:company_target_create"), {"domain": "acme.test"})

    assert response.status_code == 302
    assert Company.objects.count() == 1
    assert MonitoringTarget.objects.filter(user=user, company=company).exists()
    assert not respx.calls


@pytest.mark.django_db
@respx.mock
def test_company_target_shows_a_discovery_error_without_creating_records(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)
    respx.get("https://unreachable.test/").mock(return_value=httpx.Response(503))

    response = client.post(reverse("jobs:company_target_create"), {"domain": "unreachable.test"})

    assert response.status_code == 200
    assert b"Could not reach that company website" in response.content
    assert not Company.objects.exists()
    assert not CareerSource.objects.exists()
    assert not MonitoringTarget.objects.exists()


@pytest.mark.django_db
def test_search_renders_domain_input_and_only_the_new_location_endpoint(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    response = client.get(reverse("jobs:search"))

    assert response.status_code == 200
    assert b'name="domain"' in response.content
    assert b'name="company"' not in response.content
    assert reverse("jobs:location_search").encode() in response.content
    assert b"places/search" not in response.content


@pytest.mark.django_db
@respx.mock
@override_settings(
    LOCATION_API_URL="https://nominatim.test/search",
    LOCATION_USER_AGENT="hiring-scraper-test/1.0 (test@example.test)",
    LOCATION_MIN_REQUEST_INTERVAL_SECONDS=0,
)
def test_location_provider_fetches_and_caches_live_results() -> None:
    from jobs.locations import NominatimLocationProvider

    route = respx.get("https://nominatim.test/search").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((FIXTURES / "locations" / "berlin.json").read_text()),
        )
    )
    provider = NominatimLocationProvider()

    results = provider.search("Berlin")

    assert route.called
    assert route.calls[0].request.url.params["q"] == "Berlin"
    assert route.calls[0].request.headers["user-agent"] == (
        "hiring-scraper-test/1.0 (test@example.test)"
    )
    assert len(results) == 1
    place = GermanPlace.objects.get(source_id="nominatim:node:2950159")
    assert place.name == "Berlin"
    assert place.normalized_name == "berlin"
    assert place.latitude == pytest.approx(52.520008)
    assert place.longitude == pytest.approx(13.404954)
    assert place.source_snapshot == "nominatim"
    provider.close()


@pytest.mark.django_db
@respx.mock
@override_settings(
    LOCATION_API_URL="https://nominatim.test/search",
    LOCATION_MIN_REQUEST_INTERVAL_SECONDS=0,
)
def test_location_search_route_returns_dynamic_place_ids_and_labels(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)
    respx.get("https://nominatim.test/search").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((FIXTURES / "locations" / "berlin.json").read_text()),
        )
    )

    response = client.get(reverse("jobs:location_search"), {"q": "Berlin"})

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "id": GermanPlace.objects.get(source_id="nominatim:node:2950159").pk,
            "label": "Berlin (Berlin; 52.5200, 13.4050)",
        }
    ]

    second_response = client.get(reverse("jobs:location_search"), {"q": "Berlin"})

    assert second_response.status_code == 200
    assert len(respx.calls) == 1


@pytest.mark.django_db
@respx.mock
def test_generic_json_ld_source_is_registered_and_collects_a_job_posting() -> None:
    from jobs.collectors.employers import JsonLdCareerCollector

    source = SimpleNamespace(
        kind=CareerSource.Kind.JSON_LD,
        source_url="https://acme.test/careers",
        tenant="",
        config={},
        request_delay_seconds=0,
        max_pages=1,
    )
    response_body = (FIXTURES / "discovery" / "career-page.html").read_text()
    registry = CollectorRegistry()
    registry.register(CareerSource.Kind.JSON_LD, JsonLdCareerCollector)
    respx.get("https://acme.test/careers").mock(
        return_value=httpx.Response(200, text=response_body, headers={"content-type": "text/html"})
    )
    collector = registry.create(source)
    assert isinstance(collector, JsonLdCareerCollector)

    result = collector.collect()

    assert [job.title for job in result.raw_jobs] == ["Senior Python Engineer"]
    assert result.raw_jobs[0].city == "Berlin"
    assert result.raw_jobs[0].latitude == pytest.approx(52.52)
    assert result.raw_jobs[0].longitude == pytest.approx(13.405)


@pytest.mark.django_db
@respx.mock
def test_generic_json_ld_collector_rejects_unsafe_job_urls() -> None:
    from jobs.collectors.employers import JsonLdCareerCollector

    source = SimpleNamespace(
        kind=CareerSource.Kind.JSON_LD,
        source_url="https://acme.test/careers",
        tenant="",
        config={},
        request_delay_seconds=0,
        max_pages=1,
    )
    respx.get(source.source_url).mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "unsafe-career-page.html").read_text(),
        )
    )

    result = JsonLdCareerCollector(source).collect()

    assert len(result.raw_jobs) == 1
    assert result.raw_jobs[0].canonical_url == source.source_url
    assert result.raw_jobs[0].application_url == source.source_url


@pytest.mark.django_db
@respx.mock
def test_empty_generic_career_page_is_incomplete_and_does_not_close_jobs() -> None:
    company = Company.objects.create(
        name="Acme GmbH", domain="acme.test", career_url="https://acme.test/careers"
    )
    source = CareerSource.objects.create(
        company=company,
        kind=CareerSource.Kind.JSON_LD,
        source_url=company.career_url,
        request_delay_seconds=0,
        max_pages=1,
    )
    job = Job.objects.create(
        source=source,
        external_id="existing",
        canonical_url="https://acme.test/careers/existing",
        title="Existing job",
        normalized_title="existing job",
        content_hash="a" * 64,
        fingerprint="b" * 64,
    )
    respx.get(source.source_url).mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "empty-career-page.html").read_text(),
        )
    )

    run = collect_source(source=source, registry=collector_registry)

    assert run.status == "success"
    job.refresh_from_db()
    assert job.missed_runs == 0
    assert job.closed_at is None


@pytest.mark.django_db
@respx.mock
def test_ambiguous_generic_job_location_is_not_treated_as_a_complete_german_crawl() -> None:
    source = SimpleNamespace(
        kind=CareerSource.Kind.JSON_LD,
        source_url="https://acme.test/careers",
        tenant="",
        config={},
        request_delay_seconds=0,
        max_pages=1,
    )
    respx.get(source.source_url).mock(
        return_value=httpx.Response(
            200,
            text=(FIXTURES / "discovery" / "ambiguous-career-page.html").read_text(),
        )
    )

    from jobs.collectors.employers import JsonLdCareerCollector

    result = JsonLdCareerCollector(source).collect()

    assert result.raw_jobs == []
    assert result.is_complete is False


@pytest.mark.django_db
def test_retired_geo_names_json_endpoint_is_no_longer_routable(client: Client) -> None:
    WorkspaceUser.objects.create(name="Ada")

    response = client.get("/places/search.json", {"q": "Berlin"})

    assert response.status_code == 404
