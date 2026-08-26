from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from django.core.management import call_command

from jobs.collection import collector_registry
from jobs.collectors.employers import (
    BoschSmartRecruitersCollector,
    DhlPhenomCollector,
    SapSuccessFactorsCollector,
    SiemensAvatureCollector,
    TelekomJsonCollector,
)
from jobs.models import CareerSource, Company

FIXTURES = Path(__file__).parent / "fixtures" / "collectors"


def fixture_client(routes: dict[str, str]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        for needle, fixture_name in routes.items():
            if needle in str(request.url):
                return httpx.Response(
                    200,
                    text=(FIXTURES / fixture_name).read_text(),
                    request=request,
                )
        return httpx.Response(404, request=request)

    return httpx.Client(transport=httpx.MockTransport(respond))


def source(kind: str) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        source_url="https://example.test/careers",
        tenant="",
        config={},
        request_delay_seconds=0,
        max_pages=5,
    )


def test_siemens_collector_parses_its_germany_rss_page() -> None:
    collector = SiemensAvatureCollector(
        source("siemens_avature"),
        client=fixture_client({"feed": "siemens-page-0.xml"}),
    )

    result = collector.collect()

    assert [(job.external_id, job.title, job.country_code) for job in result.raw_jobs] == [
        ("12345", "Software Engineer", "DE")
    ]
    assert result.raw_jobs[0].description_html == "<p>Build industrial software with Python.</p>"


def test_default_collection_registry_resolves_each_initial_employer_adapter() -> None:
    collectors = {
        "siemens_avature": SiemensAvatureCollector,
        "bosch_smartrecruiters": BoschSmartRecruitersCollector,
        "sap_successfactors": SapSuccessFactorsCollector,
        "telekom_json": TelekomJsonCollector,
        "dhl_phenom": DhlPhenomCollector,
    }

    for kind, expected_type in collectors.items():
        assert isinstance(collector_registry.create(source(kind)), expected_type)


def test_bosch_collector_uses_detail_data_and_ignores_explicit_non_german_jobs() -> None:
    collector = BoschSmartRecruitersCollector(
        source("bosch_smartrecruiters"),
        client=fixture_client(
            {
                "/api/filter/query": "bosch-list-page-0.json",
                "/postings/bosch-987": "bosch-detail.json",
            }
        ),
    )

    result = collector.collect()

    assert len(result.raw_jobs) == 1
    job = result.raw_jobs[0]
    assert job.external_id == "bosch-987"
    assert job.city == "Stuttgart"
    assert job.latitude == 48.7758
    assert "Kubernetes" in job.description_html
    assert result.requests_made == 2


def test_sap_collector_uses_jobposting_detail_data() -> None:
    collector = SapSuccessFactorsCollector(
        source("sap_successfactors"),
        client=fixture_client(
            {
                "search/?": "sap-list-page-0.html",
                "1234567890": "sap-detail.html",
            }
        ),
    )

    result = collector.collect()

    assert len(result.raw_jobs) == 1
    job = result.raw_jobs[0]
    assert job.external_id == "1234567890"
    assert job.city == "Walldorf"
    assert job.employment_type == "FULL_TIME"
    assert job.country_code == "DE"


def test_telekom_collector_uses_paging_and_discards_non_german_results() -> None:
    collector = TelekomJsonCollector(
        source("telekom_json"),
        client=fixture_client(
            {
                "globaljobsearch": "telekom-list-page-0.json",
                "cloud-engineer": "telekom-detail.html",
            }
        ),
    )

    result = collector.collect()

    assert len(result.raw_jobs) == 1
    job = result.raw_jobs[0]
    assert job.external_id == "telekom-42"
    assert job.department == "T-Systems"
    assert job.latitude == 50.7374
    assert job.description_html == "<p>Operate secure cloud services.</p>"


def test_dhl_collector_reads_phenom_state_and_filters_to_germany() -> None:
    collector = DhlPhenomCollector(
        source("dhl_phenom"),
        client=fixture_client(
            {
                "search-results": "dhl-list-page-0.html",
                "/job/dhl-77": "dhl-detail.html",
            }
        ),
    )

    result = collector.collect()

    assert len(result.raw_jobs) == 1
    job = result.raw_jobs[0]
    assert job.external_id == "dhl-77"
    assert job.remote_type == "hybrid"
    assert job.department == "IT"
    assert job.country_code == "DE"


@pytest.mark.django_db
def test_seed_initial_sources_creates_each_company_and_source_once_without_overwriting_source() -> (
    None
):
    call_command("seed_initial_sources")
    source = CareerSource.objects.get(kind="siemens_avature")
    source.is_enabled = False
    source.config = {"custom": "keep"}
    source.save(update_fields=["is_enabled", "config"])

    call_command("seed_initial_sources")

    source.refresh_from_db()
    assert Company.objects.count() == 5
    assert CareerSource.objects.count() == 5
    assert source.is_enabled is False
    assert source.config == {"custom": "keep"}
    assert set(CareerSource.objects.values_list("kind", flat=True)) == {
        "siemens_avature",
        "bosch_smartrecruiters",
        "sap_successfactors",
        "telekom_json",
        "dhl_phenom",
    }
