from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import jobs.collectors as collectors
from jobs.collection import collect_source, collector_registry
from jobs.collectors import CollectorRegistry
from jobs.collectors.ats import (
    AshbyCollector,
    DVinciCollector,
    GreenhouseCollector,
    LeverCollector,
    OnlyfyPrescreenCollector,
    PersonioXmlCollector,
    RecruiteeXmlCollector,
    SmartRecruitersCollector,
    SoftgardenJsonCollector,
    SuccessFactorsXmlCollector,
    WorkableCollector,
    WorkdayCollector,
)
from jobs.models import CareerSource, Company, Job

FIXTURES = Path(__file__).parent / "fixtures" / "ats"


@pytest.fixture(autouse=True)
def stub_public_hostname_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jobs.network.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))
        ],
    )


def fixture_client(
    fixture_name: str, *, requests: list[httpx.Request] | None = None
) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, text=(FIXTURES / fixture_name).read_text(), request=request)

    return httpx.Client(transport=httpx.MockTransport(respond))


def fixture_sequence_client(
    fixture_names: list[str], *, requests: list[httpx.Request]
) -> httpx.Client:
    fixture_iterator = iter(fixture_names)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(FIXTURES / next(fixture_iterator)).read_text(),
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(respond))


def source(
    kind: str, source_url: str, *, max_pages: int = 5, tenant: str = "acme-gmbh"
) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        source_url=source_url,
        tenant=tenant,
        config={},
        request_delay_seconds=0,
        max_pages=max_pages,
    )


ADAPTER_REQUEST_CONTRACTS: list[tuple[str, type[Any], str, str, dict[str, str]]] = [
    (
        "personio",
        PersonioXmlCollector,
        "personio.xml",
        "https://acme-gmbh.jobs.personio.de/xml",
        {},
    ),
    (
        "softgarden",
        SoftgardenJsonCollector,
        "softgarden.json",
        "https://api.softgarden.io/v1/companies/acme-gmbh/jobs",
        {},
    ),
    (
        "dvinci",
        DVinciCollector,
        "dvinci.json",
        "https://jobs.dvinci.com/acme-gmbh/jobs.json",
        {},
    ),
    (
        "onlyfy",
        OnlyfyPrescreenCollector,
        "onlyfy.json",
        "https://api.prescreen.io/api/v1/companies/acme-gmbh/jobs",
        {},
    ),
    (
        "greenhouse",
        GreenhouseCollector,
        "greenhouse.json",
        "https://boards-api.greenhouse.io/v1/boards/acme-gmbh/jobs?content=true",
        {"content": "true"},
    ),
    (
        "lever",
        LeverCollector,
        "lever.json",
        "https://api.lever.co/v0/postings/acme-gmbh?mode=json",
        {"mode": "json"},
    ),
    (
        "ashby",
        AshbyCollector,
        "ashby.json",
        "https://api.ashbyhq.com/posting-api/job-board/acme-gmbh",
        {},
    ),
    (
        "smartrecruiters",
        SmartRecruitersCollector,
        "smartrecruiters.json",
        "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
        {"limit": "100", "offset": "0"},
    ),
    (
        "workable",
        WorkableCollector,
        "workable.json",
        "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
        {"limit": "100", "offset": "0"},
    ),
    (
        "recruitee",
        RecruiteeXmlCollector,
        "recruitee.xml",
        "https://acme-gmbh.recruitee.com/api/offers.xml",
        {},
    ),
    (
        "workday",
        WorkdayCollector,
        "workday.json",
        "https://acme.wd5.myworkdayjobs.com/en-US/Acme",
        {},
    ),
    (
        "successfactors",
        SuccessFactorsXmlCollector,
        "successfactors.xml",
        "https://acme.successfactors.com/jobs.xml",
        {},
    ),
]


def test_ats_tenant_helpers_build_safe_feed_urls_and_fingerprint_known_urls() -> None:
    builder = getattr(collectors, "personio_feed_url", None)
    fingerprint = getattr(collectors, "fingerprint_ats_url", None)

    assert builder is not None
    assert fingerprint is not None
    assert builder("Acme-GmbH") == "https://acme-gmbh.jobs.personio.de/xml"

    recognized = fingerprint("https://boards.greenhouse.io/acme-gmbh")

    assert recognized is not None
    assert recognized.kind == "greenhouse"
    assert recognized.tenant == "acme-gmbh"
    assert recognized.source_url == (
        "https://boards-api.greenhouse.io/v1/boards/acme-gmbh/jobs?content=true"
    )


def test_ats_fingerprint_rejects_a_non_feed_smartrecruiters_path() -> None:
    fingerprint = collectors.fingerprint_ats_url

    assert fingerprint("https://api.smartrecruiters.com/v1/companies/acme-gmbh/settings") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://apply.workable.com/api/v3/accounts/acme-gmbh/settings",
        "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs/settings",
        "https://api.prescreen.io/api/v1/companies/acme-gmbh/settings",
        "https://api.prescreen.io/api/v1/companies/acme-gmbh/jobs/settings",
    ],
)
def test_ats_fingerprint_rejects_non_feed_workable_and_onlyfy_paths(url: str) -> None:
    assert collectors.fingerprint_ats_url(url) is None


def test_workday_fingerprint_strips_query_data_from_a_recognized_board_url() -> None:
    recognized = collectors.fingerprint_ats_url(
        "https://acme.wd5.myworkdayjobs.com/en-US/Acme?returnTo=https://evil.test"
    )

    assert recognized is not None
    assert recognized.kind == "workday"
    assert recognized.tenant == "acme"
    assert recognized.source_url == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Acme/jobs"


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.wd5.myworkdayjobs.com//evil.test",
        "https://acme.wd5.myworkdayjobs.com/../evil",
        "https://acme.wd5.myworkdayjobs.com/%2e%2e/evil",
    ],
)
def test_workday_fingerprint_rejects_unsafe_board_paths(url: str) -> None:
    assert collectors.fingerprint_ats_url(url) is None


def test_workday_fingerprint_rejects_a_job_detail_path() -> None:
    assert (
        collectors.fingerprint_ats_url(
            "https://acme.wd5.myworkdayjobs.com/en-US/Acme/job/Berlin/123"
        )
        is None
    )


def test_workday_fingerprint_keeps_a_cxs_path_canonical() -> None:
    recognized = collectors.fingerprint_ats_url(
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Acme/jobs?query=ignored"
    )

    assert recognized is not None
    assert recognized.source_url == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Acme/jobs"


@pytest.mark.parametrize(
    ("kind", "collector_type", "fixture_name", "source_url", "expected_query"),
    ADAPTER_REQUEST_CONTRACTS,
)
def test_ats_collectors_request_the_configured_feed_contract(
    kind: str,
    collector_type: type[Any],
    fixture_name: str,
    source_url: str,
    expected_query: dict[str, str],
) -> None:
    requests: list[httpx.Request] = []

    collector_type(
        source(kind, source_url), client=fixture_client(fixture_name, requests=requests)
    ).collect()

    assert len(requests) == 1
    request = requests[0]
    expected = httpx.URL(source_url)
    expected_method = "POST" if kind == "workday" else "GET"
    expected_path = "/wday/cxs/acme/Acme/jobs" if kind == "workday" else expected.path
    assert request.method == expected_method
    assert request.url.host == expected.host
    assert request.url.path == expected_path
    assert dict(request.url.params) == expected_query
    if kind == "workday":
        assert json.loads(request.content) == {
            "appliedFacets": {},
            "limit": 20,
            "offset": 0,
            "searchText": "",
        }


def test_workday_feed_url_builds_a_cxs_endpoint_from_a_recognized_board() -> None:
    import jobs.collectors.ats as ats

    builder = getattr(ats, "workday_feed_url", None)

    assert builder is not None
    assert builder("https://acme.wd5.myworkdayjobs.com/en-US/Acme") == (
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Acme/jobs"
    )


@pytest.mark.parametrize(
    "source_url",
    [
        "http://api.lever.co/v0/postings/acme-gmbh",
        "https://user@api.lever.co/v0/postings/acme-gmbh",
        "https://api.lever.co:8443/v0/postings/acme-gmbh",
        "https://127.0.0.1/v0/postings/acme-gmbh",
        "https://example.com/v0/postings/acme-gmbh",
    ],
)
def test_ats_collectors_reject_invalid_source_urls_before_request(
    source_url: str,
) -> None:
    requests: list[httpx.Request] = []

    collector = LeverCollector(
        source("lever", source_url),
        client=fixture_client("lever.json", requests=requests),
    )

    with pytest.raises(ValueError):
        collector.collect()

    assert requests == []


def test_ats_collectors_reject_private_hostname_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    monkeypatch.setattr(
        "jobs.network.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0))
        ],
    )
    collector = LeverCollector(
        source("lever", "https://api.lever.co/v0/postings/acme-gmbh"),
        client=fixture_client("lever.json", requests=requests),
    )

    with pytest.raises(ValueError, match="must resolve to a public address"):
        collector.collect()

    assert requests == []


@pytest.mark.parametrize(
    ("kind", "collector_type", "fixture_name", "source_url"),
    [
        (
            "personio",
            PersonioXmlCollector,
            "personio.xml",
            "https://evil.acme-gmbh.jobs.personio.de/xml",
        ),
        (
            "recruitee",
            RecruiteeXmlCollector,
            "recruitee.xml",
            "https://evil.acme-gmbh.recruitee.com/api/offers.xml",
        ),
        (
            "workday",
            WorkdayCollector,
            "workday.json",
            "https://evil.acme.wd5.myworkdayjobs.com/en-US/Acme",
        ),
        (
            "successfactors",
            SuccessFactorsXmlCollector,
            "successfactors.xml",
            "https://evil.acme.successfactors.com/jobs.xml",
        ),
    ],
)
def test_ats_collectors_reject_nested_ats_subdomains_before_request(
    kind: str,
    collector_type: type[Any],
    fixture_name: str,
    source_url: str,
) -> None:
    requests: list[httpx.Request] = []

    collector = collector_type(
        source(kind, source_url),
        client=fixture_client(fixture_name, requests=requests),
    )

    with pytest.raises(ValueError):
        collector.collect()

    assert requests == []


def test_ats_collectors_reject_an_evil_redirect_before_requesting_it() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://evil.test/jobs"},
            request=request,
        )

    collector = LeverCollector(
        source("lever", "https://api.lever.co/v0/postings/acme-gmbh"),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(ValueError):
        collector.collect()

    assert [str(request.url) for request in requests] == [
        "https://api.lever.co/v0/postings/acme-gmbh"
    ]


def test_workday_rejects_an_evil_redirect_before_requesting_it() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://evil.test/jobs"},
            request=request,
        )

    collector = WorkdayCollector(
        source("workday", "https://acme.wd5.myworkdayjobs.com/en-US/Acme"),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(ValueError):
        collector.collect()

    assert [(request.method, str(request.url)) for request in requests] == [
        ("POST", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Acme/jobs")
    ]


def test_workday_does_not_follow_a_same_host_redirect() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": str(request.url)},
            request=request,
        )

    collector = WorkdayCollector(
        source("workday", "https://acme.wd5.myworkdayjobs.com/en-US/Acme"),
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(httpx.TooManyRedirects):
        collector.collect()

    assert len(requests) == 1


@pytest.mark.parametrize(
    ("class_name", "kind", "fixture_name", "source_url", "external_id", "city"),
    [
        (
            "PersonioXmlCollector",
            "personio",
            "personio.xml",
            "https://acme.jobs.personio.de/xml",
            "personio-de",
            "Berlin",
        ),
        (
            "SoftgardenJsonCollector",
            "softgarden",
            "softgarden.json",
            "https://api.softgarden.io/v1/companies/acme-gmbh/jobs",
            "softgarden-de",
            "Berlin",
        ),
        (
            "DVinciCollector",
            "dvinci",
            "dvinci.json",
            "https://jobs.dvinci.com/acme-gmbh/jobs.json",
            "dvinci-json-de",
            "Munich",
        ),
        (
            "DVinciCollector",
            "dvinci",
            "dvinci.xml",
            "https://jobs.dvinci.com/acme-gmbh/jobs.xml",
            "dvinci-xml-de",
            "Cologne",
        ),
        (
            "OnlyfyPrescreenCollector",
            "onlyfy",
            "onlyfy.json",
            "https://api.prescreen.io/api/v1/companies/acme-gmbh/jobs",
            "onlyfy-de",
            "Hamburg",
        ),
        (
            "GreenhouseCollector",
            "greenhouse",
            "greenhouse.json",
            "https://boards-api.greenhouse.io/v1/boards/acme-gmbh/jobs?content=true",
            "101",
            "Cologne",
        ),
        (
            "LeverCollector",
            "lever",
            "lever.json",
            "https://api.lever.co/v0/postings/acme-gmbh?mode=json",
            "lever-de",
            "Berlin",
        ),
        (
            "AshbyCollector",
            "ashby",
            "ashby.json",
            "https://api.ashbyhq.com/posting-api/job-board/acme-gmbh",
            "ashby-de",
            "Munich",
        ),
        (
            "SmartRecruitersCollector",
            "smartrecruiters",
            "smartrecruiters.json",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
            "smart-de",
            "Berlin",
        ),
        (
            "WorkableCollector",
            "workable",
            "workable.json",
            "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
            "workable-de",
            "Berlin",
        ),
        (
            "RecruiteeXmlCollector",
            "recruitee",
            "recruitee.xml",
            "https://acme-gmbh.recruitee.com/api/offers.xml",
            "recruitee-de",
            "Hamburg",
        ),
        (
            "WorkdayCollector",
            "workday",
            "workday.json",
            "https://acme.wd5.myworkdayjobs.com/en-US/Acme",
            "/job/Berlin/workday-de",
            "Munich",
        ),
        (
            "SuccessFactorsXmlCollector",
            "successfactors",
            "successfactors.xml",
            "https://acme.successfactors.com/jobs.xml",
            "successfactors-de",
            "Walldorf",
        ),
    ],
)
def test_public_ats_collectors_keep_only_jobs_with_germany_evidence(
    class_name: str,
    kind: str,
    fixture_name: str,
    source_url: str,
    external_id: str,
    city: str,
) -> None:
    import jobs.collectors.ats as ats

    collector_type = getattr(ats, class_name, None)

    assert collector_type is not None
    result = collector_type(source(kind, source_url), client=fixture_client(fixture_name)).collect()

    assert [(job.external_id, job.city, job.country_code) for job in result.raw_jobs] == [
        (external_id, city, "DE")
    ]
    if kind == "workday":
        assert result.raw_jobs[0].canonical_url == (
            "https://acme.wd5.myworkdayjobs.com/job/Berlin/workday-de"
        )
    assert result.is_complete is True


def test_smartrecruiters_keeps_its_public_ref_url() -> None:
    result = SmartRecruitersCollector(
        source(
            "smartrecruiters",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
        ),
        client=fixture_client("smartrecruiters.json"),
    ).collect()

    assert (
        result.raw_jobs[0].canonical_url
        == "https://jobs.smartrecruiters.com/acme-gmbh/job/smart-de"
    )
    assert (
        result.raw_jobs[0].application_url
        == "https://jobs.smartrecruiters.com/acme-gmbh/job/smart-de"
    )


@pytest.mark.parametrize(
    ("collector_type", "fixture_names", "source_url", "expected_ids"),
    [
        (
            SmartRecruitersCollector,
            ["smartrecruiters-cursor-page-1.json", "smartrecruiters-cursor-page-2.json"],
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
            ["smart-de", "smart-next-de"],
        ),
        (
            WorkableCollector,
            ["workable-cursor-page-1.json", "workable-cursor-page-2.json"],
            "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
            ["workable-de", "workable-next-de"],
        ),
    ],
)
def test_cursor_pagination_continues_within_the_page_limit(
    collector_type: type[Any],
    fixture_names: list[str],
    source_url: str,
    expected_ids: list[str],
) -> None:
    requests: list[httpx.Request] = []

    result = collector_type(
        source(
            "smartrecruiters" if collector_type is SmartRecruitersCollector else "workable",
            source_url,
        ),
        client=fixture_sequence_client(fixture_names, requests=requests),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == expected_ids
    assert result.is_complete is True
    assert [dict(request.url.params) for request in requests] == (
        [{"limit": "100", "offset": "0"}, {"limit": "100", "cursor": "smart-page-2"}]
        if collector_type is SmartRecruitersCollector
        else [{"limit": "100", "offset": "0"}, {"limit": "100", "token": "workable-page-2"}]
    )


def test_workday_collects_bounded_offset_pages_and_posts_each_offset() -> None:
    requests: list[httpx.Request] = []

    result = WorkdayCollector(
        source(
            "workday",
            "https://acme.wd5.myworkdayjobs.com/en-US/Acme",
            max_pages=2,
        ),
        client=fixture_sequence_client(
            ["workday-page-1.json", "workday-page-2.json"], requests=requests
        ),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == [
        f"/job/Berlin/workday-page-{number}" for number in range(1, 22)
    ]
    assert result.is_complete is True
    assert [json.loads(request.content) for request in requests] == [
        {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
        {"appliedFacets": {}, "limit": 20, "offset": 20, "searchText": ""},
    ]


def test_workday_marks_a_full_page_without_completion_evidence_as_incomplete() -> None:
    result = WorkdayCollector(
        source(
            "workday",
            "https://acme.wd5.myworkdayjobs.com/en-US/Acme",
            max_pages=1,
        ),
        client=fixture_client("workday-capped-full.json"),
    ).collect()

    assert len(result.raw_jobs) == 20
    assert result.is_complete is False


def test_workday_marks_changed_pagination_totals_as_incomplete() -> None:
    result = WorkdayCollector(
        source(
            "workday",
            "https://acme.wd5.myworkdayjobs.com/en-US/Acme",
            max_pages=2,
        ),
        client=fixture_sequence_client(
            ["workday-page-1.json", "workday-total-shift-page-2.json"], requests=[]
        ),
    ).collect()

    assert len(result.raw_jobs) == 20
    assert result.is_complete is False


@pytest.mark.parametrize(
    ("collector_type", "fixture_name", "page_two_fixture", "source_url"),
    [
        (
            SmartRecruitersCollector,
            "smartrecruiters-unsafe-cross-tenant-page-1.json",
            "smartrecruiters-unsafe-page-2.json",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
        ),
        (
            SmartRecruitersCollector,
            "smartrecruiters-unsafe-path-page-1.json",
            "smartrecruiters-unsafe-page-2.json",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
        ),
        (
            SmartRecruitersCollector,
            "smartrecruiters-unsafe-host-page-1.json",
            "smartrecruiters-unsafe-page-2.json",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
        ),
        (
            WorkableCollector,
            "workable-unsafe-cross-tenant-page-1.json",
            "workable-unsafe-page-2.json",
            "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
        ),
        (
            WorkableCollector,
            "workable-unsafe-path-page-1.json",
            "workable-unsafe-page-2.json",
            "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
        ),
        (
            WorkableCollector,
            "workable-unsafe-host-page-1.json",
            "workable-unsafe-page-2.json",
            "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
        ),
    ],
)
def test_pagination_ignores_next_urls_outside_the_configured_endpoint(
    collector_type: type[Any],
    fixture_name: str,
    page_two_fixture: str,
    source_url: str,
) -> None:
    requests: list[httpx.Request] = []

    result = collector_type(
        source(
            "smartrecruiters" if collector_type is SmartRecruitersCollector else "workable",
            source_url,
            max_pages=2,
        ),
        client=fixture_sequence_client([fixture_name, page_two_fixture], requests=requests),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["unsafe-page-one", "unsafe-page-two"]
    assert result.is_complete is False
    assert [str(request.url) for request in requests] == [
        f"{source_url}?limit=100&offset=0",
        f"{source_url}?limit=100&offset=1",
    ]


def test_smartrecruiters_marks_a_capped_page_as_incomplete() -> None:
    import jobs.collectors.ats as ats

    collector_type = getattr(ats, "SmartRecruitersCollector", None)

    assert collector_type is not None
    result = collector_type(
        source(
            "smartrecruiters",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
            max_pages=1,
        ),
        client=fixture_client("smartrecruiters-capped.json"),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["smart-de"]
    assert result.is_complete is False


def test_dvinci_marks_an_unfollowed_json_next_page_as_incomplete() -> None:
    import jobs.collectors.ats as ats

    result = ats.DVinciCollector(
        source("dvinci", "https://jobs.dvinci.com/acme-gmbh/jobs.json"),
        client=fixture_client("dvinci-incomplete.json"),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["dvinci-json-de"]
    assert result.is_complete is False


@pytest.mark.parametrize(
    "fixture_name",
    [
        "softgarden-top-level-total.json",
        "softgarden-top-level-next.json",
        "softgarden-top-level-token.json",
    ],
)
def test_shared_json_collectors_mark_top_level_pagination_metadata_incomplete(
    fixture_name: str,
) -> None:
    import jobs.collectors.ats as ats

    result = ats.SoftgardenJsonCollector(
        source("softgarden", "https://api.softgarden.io/v1/companies/acme-gmbh/jobs"),
        client=fixture_client(fixture_name),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["softgarden-de"]
    assert result.is_complete is False


def test_generic_germany_evidence_rejects_ambiguous_de_suffix_and_keeps_country_code() -> None:
    result = SoftgardenJsonCollector(
        source("softgarden", "https://api.softgarden.io/v1/companies/acme-gmbh/jobs"),
        client=fixture_client("softgarden-germany-evidence.json"),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["berlin-explicit-de"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("kind", "collector_type", "initial_fixture", "cursor_fixture", "source_url", "external_id"),
    [
        (
            CareerSource.Kind.SMARTRECRUITERS,
            SmartRecruitersCollector,
            "smartrecruiters.json",
            "smartrecruiters-cursor-page-1.json",
            "https://api.smartrecruiters.com/v1/companies/acme-gmbh/postings",
            "smart-de",
        ),
        (
            CareerSource.Kind.WORKABLE,
            WorkableCollector,
            "workable.json",
            "workable-cursor-page-1.json",
            "https://apply.workable.com/api/v3/accounts/acme-gmbh/jobs",
            "workable-de",
        ),
    ],
)
def test_cursor_response_does_not_record_a_missing_job(
    kind: str,
    collector_type: type[Any],
    initial_fixture: str,
    cursor_fixture: str,
    source_url: str,
    external_id: str,
) -> None:
    company = Company.objects.create(
        name="Smart GmbH",
        domain="smart-cursor.test",
        career_url="https://smart-cursor.test/careers",
    )
    career_source = CareerSource.objects.create(
        company=company,
        kind=kind,
        source_url=source_url,
        request_delay_seconds=0,
        max_pages=1,
    )
    fixture_names = iter([initial_fixture, cursor_fixture])
    registry = CollectorRegistry()
    registry.register(
        kind,
        lambda configured_source: collector_type(
            configured_source, client=fixture_client(next(fixture_names))
        ),
    )

    collect_source(source=career_source, registry=registry)
    cursor_run = collect_source(source=career_source, registry=registry)

    job = Job.objects.get(source=career_source, external_id=external_id)
    assert cursor_run.jobs_closed == 0
    assert job.missed_runs == 0


def test_workday_rejects_unsafe_external_paths() -> None:
    result = WorkdayCollector(
        source("workday", "https://acme.wd5.myworkdayjobs.com/en-US/Acme"),
        client=fixture_client("workday-unsafe-paths.json"),
    ).collect()

    assert [(job.external_id, job.canonical_url) for job in result.raw_jobs] == [
        ("/job/Berlin/workday-de", "https://acme.wd5.myworkdayjobs.com/job/Berlin/workday-de")
    ]


@pytest.mark.django_db
def test_workday_ats_source_collects_and_persists_fixture_jobs() -> None:
    company = Company.objects.create(
        name="Acme GmbH",
        domain="acme.test",
        career_url="https://acme.test/careers",
    )
    career_source = CareerSource.objects.create(
        company=company,
        kind=CareerSource.Kind.WORKDAY,
        source_url="https://acme.wd5.myworkdayjobs.com/acme/en-US/Acme",
        tenant="acme",
        request_delay_seconds=0,
    )
    registry = CollectorRegistry()
    registry.register(
        CareerSource.Kind.WORKDAY,
        lambda configured_source: WorkdayCollector(
            configured_source, client=fixture_client("workday.json")
        ),
    )

    run = collect_source(source=career_source, registry=registry)

    job = Job.objects.get(source=career_source, external_id="/job/Berlin/workday-de")
    assert run.status == "success"
    assert run.jobs_seen == 1
    assert job.country_code == "DE"
    assert job.canonical_url == "https://acme.wd5.myworkdayjobs.com/job/Berlin/workday-de"


@pytest.mark.django_db
def test_incomplete_workday_result_does_not_close_a_live_job() -> None:
    company = Company.objects.create(
        name="Workday lifecycle GmbH",
        domain="workday-lifecycle.test",
        career_url="https://workday-lifecycle.test/careers",
    )
    career_source = CareerSource.objects.create(
        company=company,
        kind=CareerSource.Kind.WORKDAY,
        source_url="https://acme.wd5.myworkdayjobs.com/en-US/Acme",
        request_delay_seconds=0,
        max_pages=1,
    )
    fixture_names = iter(["workday.json", "workday-capped-full.json"])
    registry = CollectorRegistry()
    registry.register(
        CareerSource.Kind.WORKDAY,
        lambda configured_source: WorkdayCollector(
            configured_source, client=fixture_client(next(fixture_names))
        ),
    )

    collect_source(source=career_source, registry=registry)
    capped_run = collect_source(source=career_source, registry=registry)

    job = Job.objects.get(source=career_source, external_id="/job/Berlin/workday-de")
    assert capped_run.jobs_closed == 0
    assert job.missed_runs == 0
    assert job.closed_at is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("kind", "collector_type", "fixture_name", "source_url", "expected_external_id"),
    [
        (kind, collector_type, fixture_name, source_url, "101" if kind == "greenhouse" else None)
        for kind, collector_type, fixture_name, source_url, _query in ADAPTER_REQUEST_CONTRACTS
    ],
)
def test_ats_sources_persist_and_upsert_fixture_jobs(
    kind: str,
    collector_type: type[Any],
    fixture_name: str,
    source_url: str,
    expected_external_id: str | None,
) -> None:
    company = Company.objects.create(
        name=f"{kind.title()} GmbH",
        domain=f"{kind}.test",
        career_url=f"https://{kind}.test/careers",
    )
    career_source = CareerSource.objects.create(
        company=company,
        kind=kind,
        source_url=source_url,
        tenant="acme-gmbh",
        request_delay_seconds=0,
    )
    registry = CollectorRegistry()
    registry.register(
        kind,
        lambda configured_source: collector_type(
            configured_source, client=fixture_client(fixture_name)
        ),
    )

    first_run = collect_source(source=career_source, registry=registry)
    second_run = collect_source(source=career_source, registry=registry)

    jobs = Job.objects.filter(source=career_source)
    assert first_run.status == "success"
    assert first_run.jobs_created == 1
    assert second_run.status == "success"
    assert second_run.jobs_created == 0
    assert jobs.count() == 1
    if expected_external_id is not None:
        assert jobs.get().external_id == expected_external_id


@pytest.mark.django_db
def test_softgarden_ats_source_closes_a_missing_fixture_job_after_two_complete_runs() -> None:
    company = Company.objects.create(
        name="Softgarden GmbH",
        domain="softgarden-lifecycle.test",
        career_url="https://softgarden-lifecycle.test/careers",
    )
    career_source = CareerSource.objects.create(
        company=company,
        kind=CareerSource.Kind.SOFTGARDEN,
        source_url="https://api.softgarden.io/v1/companies/acme-gmbh/jobs",
        request_delay_seconds=0,
    )
    fixture_names = iter(["softgarden.json", "softgarden-empty.json", "softgarden-empty.json"])
    registry = CollectorRegistry()
    registry.register(
        CareerSource.Kind.SOFTGARDEN,
        lambda configured_source: SoftgardenJsonCollector(
            configured_source, client=fixture_client(next(fixture_names))
        ),
    )

    collect_source(source=career_source, registry=registry)
    first_missing_run = collect_source(source=career_source, registry=registry)
    second_missing_run = collect_source(source=career_source, registry=registry)

    job = Job.objects.get(source=career_source, external_id="softgarden-de")
    assert first_missing_run.jobs_closed == 0
    assert second_missing_run.jobs_closed == 1
    assert job.closed_at is not None


def test_new_ats_kinds_are_persisted_and_registered() -> None:
    import jobs.collectors.ats as ats

    expected = {
        ats.DVINCI,
        ats.ONLYFY,
        ats.ASHBY,
        ats.SMARTRECRUITERS,
        ats.WORKABLE,
        ats.RECRUITEE,
    }
    persisted_kinds = {value for value, _label in CareerSource.Kind.choices}

    assert expected <= persisted_kinds
    for kind in expected | {
        ats.PERSONIO,
        ats.SOFTGARDEN,
        ats.GREENHOUSE,
        ats.LEVER,
        ats.WORKDAY,
        ats.SUCCESSFACTORS,
    }:
        assert collector_registry.create(
            source(kind, "https://feeds.test/jobs")
        ).__class__.__module__ == ("jobs.collectors.ats")
