from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jobs.collection import collector_registry
from jobs.collectors.employers import (
    BoschSmartRecruitersCollector,
    DhlPhenomCollector,
    SapSuccessFactorsCollector,
    SiemensAvatureCollector,
    TelekomJsonCollector,
)

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


def source(kind: str, *, max_pages: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        source_url="https://example.test/careers",
        tenant="",
        config={},
        request_delay_seconds=0,
        max_pages=max_pages,
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


def test_siemens_collector_marks_a_full_capped_rss_page_incomplete() -> None:
    items = "".join(
        f"<item><title>Role {number}</title><link>https://jobs.siemens.com/JobDetail/{number}</link></item>"
        for number in range(20)
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=f"<rss><channel><link>https://jobs.siemens.com/</link>{items}</channel></rss>",
                request=request,
            )
        )
    )
    collector = SiemensAvatureCollector(source("siemens_avature", max_pages=1), client=client)

    result = collector.collect()

    assert len(result.raw_jobs) == 20
    assert result.is_complete is False


@pytest.mark.parametrize(
    ("collector_type", "fixture_name", "route"),
    [
        (SiemensAvatureCollector, "siemens-wrong-shape.xml", "feed"),
        (SapSuccessFactorsCollector, "sap-wrong-shape.html", "search/?"),
    ],
)
def test_wrong_shaped_list_pages_fail_instead_of_claiming_empty_results(
    collector_type: type[SiemensAvatureCollector] | type[SapSuccessFactorsCollector],
    fixture_name: str,
    route: str,
) -> None:
    collector = collector_type(
        source("siemens_avature"), client=fixture_client({route: fixture_name})
    )

    with pytest.raises(ValueError):
        collector.collect()


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


def test_bosch_collector_rejects_a_job_without_positive_germany_evidence() -> None:
    collector = BoschSmartRecruitersCollector(
        source("bosch_smartrecruiters"),
        client=fixture_client(
            {
                "/api/filter/query": "bosch-list-unknown-country.json",
                "/postings/bosch-unknown": "bosch-detail-unknown-country.json",
            }
        ),
    )

    result = collector.collect()

    assert result.raw_jobs == []
    assert result.requests_made == 2


def test_bosch_collector_follows_boolean_next_pages_without_looping() -> None:
    list_fixtures = {
        "page=0": "bosch-list-page-0-pagination.json",
        "page=1": "bosch-list-page-1.json",
        "page=2": "bosch-list-page-2.json",
    }
    collector = BoschSmartRecruitersCollector(
        source("bosch_smartrecruiters"),
        client=fixture_client(
            {
                **list_fixtures,
                "/postings/bosch-987": "bosch-detail.json",
                "/postings/bosch-2": "bosch-detail.json",
                "/postings/bosch-3": "bosch-detail.json",
            }
        ),
    )

    result = collector.collect()

    assert [job.external_id for job in result.raw_jobs] == ["bosch-987", "bosch-2", "bosch-3"]
    assert result.requests_made == 6
    assert result.is_complete is True


def test_bosch_collector_marks_a_capped_next_page_incomplete() -> None:
    collector = BoschSmartRecruitersCollector(
        source("bosch_smartrecruiters", max_pages=1),
        client=fixture_client(
            {
                "/api/filter/query": "bosch-list-page-0-pagination.json",
                "/postings/bosch-987": "bosch-detail.json",
            }
        ),
    )

    result = collector.collect()

    assert [job.external_id for job in result.raw_jobs] == ["bosch-987"]
    assert result.is_complete is False


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
    assert job.description_html == "Develop cloud services with Java."
    assert job.posted_at is not None
    assert job.posted_at.date().isoformat() == "2026-08-25"
    assert job.locations == ["Walldorf, DE"]


def test_sap_collector_keeps_a_list_record_when_one_detail_page_fails() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "search/?" in url:
            return httpx.Response(
                200, text=(FIXTURES / "sap-list-two.html").read_text(), request=request
            )
        if "/101/" in url:
            return httpx.Response(404, request=request)
        return httpx.Response(200, text=(FIXTURES / "sap-detail.html").read_text(), request=request)

    collector = SapSuccessFactorsCollector(
        source("sap_successfactors"),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = collector.collect()

    assert [job.external_id for job in result.raw_jobs] == ["101", "102"]
    assert result.raw_jobs[0].description_html == ""
    assert result.raw_jobs[0].locations == ["Berlin, Germany"]
    assert result.is_complete is True


def test_sap_collector_marks_a_full_capped_page_incomplete() -> None:
    rows = "".join(
        "<tr><td>"
        f"<a class='jobTitle-link' href='/job/German-role-{number}/{number}/'>Role {number}</a>"
        "</td><td class='jobLocation'>Berlin, Germany</td></tr>"
        for number in range(25)
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/":
            return httpx.Response(
                200,
                text=f"<table id='searchresults'>{rows}</table>",
                request=request,
            )
        return httpx.Response(404, request=request)

    collector = SapSuccessFactorsCollector(
        source("sap_successfactors", max_pages=1),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = collector.collect()

    assert len(result.raw_jobs) == 25
    assert result.is_complete is False


def test_sap_collector_prefers_a_german_joblocation_after_a_foreign_one() -> None:
    collector = SapSuccessFactorsCollector(
        source("sap_successfactors"),
        client=fixture_client(
            {
                "search/?": "sap-list-page-0.html",
                "1234567890": "sap-detail-mixed-locations.html",
            }
        ),
    )

    result = collector.collect()

    assert len(result.raw_jobs) == 1
    job = result.raw_jobs[0]
    assert job.city == "Berlin"
    assert job.latitude == 52.52
    assert job.locations == ["Berlin, DE"]


@pytest.mark.parametrize("payload_url", ["http://127.0.0.1/job", "//evil.example/job"])
def test_sap_collector_does_not_request_untrusted_detail_urls(payload_url: str) -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200,
            text=(
                "<table id='searchresults'><tr><td>"
                f"<a class='jobTitle-link' href='{payload_url}'>Unsafe role</a>"
                "</td><td class='jobLocation'>Berlin, Germany</td></tr></table>"
            ),
            request=request,
        )

    collector = SapSuccessFactorsCollector(
        source("sap_successfactors"),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = collector.collect()

    assert result.raw_jobs == []
    assert result.is_complete is False
    assert len(requested) == 1


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
    assert result.is_complete is True


def test_telekom_collector_marks_a_capped_page_count_incomplete() -> None:
    list_body = (
        (FIXTURES / "telekom-list-page-0.json")
        .read_text()
        .replace('"page_count": 1', '"page_count": 2')
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if "globaljobsearch" in str(request.url):
            return httpx.Response(200, text=list_body, request=request)
        return httpx.Response(
            200, text=(FIXTURES / "telekom-detail.html").read_text(), request=request
        )

    collector = TelekomJsonCollector(
        source("telekom_json", max_pages=1),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = collector.collect()

    assert [job.external_id for job in result.raw_jobs] == ["telekom-42"]
    assert result.is_complete is False


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
    assert result.is_complete is True


def test_dhl_collector_marks_a_capped_total_incomplete() -> None:
    list_body = (
        (FIXTURES / "dhl-list-page-0.html").read_text().replace('"totalHits":2', '"totalHits":20')
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if "search-results" in str(request.url):
            return httpx.Response(200, text=list_body, request=request)
        return httpx.Response(200, text=(FIXTURES / "dhl-detail.html").read_text(), request=request)

    collector = DhlPhenomCollector(
        source("dhl_phenom", max_pages=1),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = collector.collect()

    assert [job.external_id for job in result.raw_jobs] == ["dhl-77"]
    assert result.is_complete is False
