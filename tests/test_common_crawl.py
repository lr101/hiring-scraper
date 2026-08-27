from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import httpx
import pytest

from jobs.common_crawl import (
    ATSTenantDescriptor,
    CommonCrawlIndexClient,
    PublicATSFeedValidator,
)

FIXTURES = Path(__file__).parent / "fixtures" / "common_crawl"


@pytest.fixture(autouse=True)
def stub_public_hostname_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jobs.network.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ],
    )


def test_common_crawl_discovers_deduplicated_known_ats_tenants_from_bounded_queries() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/collinfo.json":
            body = (FIXTURES / "collinfo.json").read_text()
        else:
            body = (FIXTURES / "index.jsonl").read_text()
        return httpx.Response(200, text=body, request=request)

    client = CommonCrawlIndexClient(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        index_base_url="https://index.commoncrawl.test",
        max_records_per_query=5,
        max_patterns=2,
        min_interval_seconds=0,
    )

    descriptors = client.discover(
        patterns=("*.jobs.personio.de/*", "boards.greenhouse.io/*"),
    )

    assert descriptors == (
        ATSTenantDescriptor(
            kind="personio",
            tenant="acme",
            source_url="https://acme.jobs.personio.de/xml",
            evidence_urls=("https://acme.jobs.personio.de/xml",),
            snapshot="CC-MAIN-2026-30",
        ),
        ATSTenantDescriptor(
            kind="greenhouse",
            tenant="acme",
            source_url=("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"),
            evidence_urls=("https://boards.greenhouse.io/acme/jobs/123",),
            snapshot="CC-MAIN-2026-30",
        ),
    )
    assert len(requests) == 3
    assert requests[0].url.path == "/collinfo.json"
    assert [request.url.params["url"] for request in requests[1:]] == [
        "*.jobs.personio.de/*",
        "boards.greenhouse.io/*",
    ]
    assert all(request.url.params["limit"] == "5" for request in requests[1:])


def test_common_crawl_keeps_workday_boards_with_the_same_tenant_separate() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(FIXTURES / "workday-multi-board-index.jsonl").read_text(),
            request=request,
        )

    client = CommonCrawlIndexClient(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        index_base_url="https://index.commoncrawl.test",
        max_records_per_query=5,
        min_interval_seconds=0,
    )

    descriptors = client.discover(
        snapshot="CC-MAIN-2026-30",
        patterns=("*.myworkdayjobs.com/*",),
    )

    assert [
        (descriptor.kind, descriptor.tenant, descriptor.source_url) for descriptor in descriptors
    ] == [
        (
            "workday",
            "acme",
            "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs",
        ),
        (
            "workday",
            "acme",
            "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Students/jobs",
        ),
    ]


def test_public_ats_feed_validator_accepts_only_a_live_recognized_feed() -> None:
    descriptors = (
        ATSTenantDescriptor(
            kind="greenhouse",
            tenant="acme",
            source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
            evidence_urls=(),
            snapshot="CC-MAIN-2026-30",
        ),
        ATSTenantDescriptor(
            kind="greenhouse",
            tenant="missing",
            source_url="https://boards-api.greenhouse.io/v1/boards/missing/jobs?content=true",
            evidence_urls=(),
            snapshot="CC-MAIN-2026-30",
        ),
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/acme/jobs"):
            return httpx.Response(200, json={"jobs": []}, request=request)
        return httpx.Response(200, json={"unexpected": []}, request=request)

    validator = PublicATSFeedValidator(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        min_interval_seconds=0,
    )

    assert validator.validate(descriptors[0]) is True
    assert validator.validate(descriptors[1]) is False


def test_public_ats_feed_validator_requires_the_recognized_xml_shape() -> None:
    descriptors = (
        ATSTenantDescriptor(
            kind="personio",
            tenant="acme",
            source_url="https://acme.jobs.personio.de/xml",
            evidence_urls=(),
            snapshot="CC-MAIN-2026-30",
        ),
        ATSTenantDescriptor(
            kind="personio",
            tenant="missing",
            source_url="https://missing.jobs.personio.de/xml",
            evidence_urls=(),
            snapshot="CC-MAIN-2026-30",
        ),
    )

    def respond(request: httpx.Request) -> httpx.Response:
        body = (
            (FIXTURES / "personio-empty.xml").read_text()
            if request.url.host == "acme.jobs.personio.de"
            else (FIXTURES / "personio-unrelated.xml").read_text()
        )
        return httpx.Response(200, text=body, request=request)

    validator = PublicATSFeedValidator(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        min_interval_seconds=0,
    )

    assert validator.validate(descriptors[0]) is True
    assert validator.validate(descriptors[1]) is False


def test_public_ats_feed_validator_matches_each_json_collector_shape() -> None:
    cases = {
        "api.softgarden.io": ("softgarden", "softgarden.json"),
        "jobs.dvinci.com": ("dvinci", "dvinci.json"),
        "api.prescreen.io": ("onlyfy", "onlyfy.json"),
        "boards-api.greenhouse.io": ("greenhouse", "greenhouse.json"),
        "api.lever.co": ("lever", "lever.json"),
        "api.ashbyhq.com": ("ashby", "ashby.json"),
    }
    descriptors = tuple(
        ATSTenantDescriptor(
            kind=kind,
            tenant="acme",
            source_url={
                "softgarden": "https://api.softgarden.io/v1/companies/acme/jobs",
                "dvinci": "https://jobs.dvinci.com/acme/jobs.json",
                "onlyfy": "https://api.prescreen.io/api/v1/companies/acme/jobs",
                "greenhouse": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
                "lever": "https://api.lever.co/v0/postings/acme?mode=json",
                "ashby": "https://api.ashbyhq.com/posting-api/job-board/acme",
            }[kind],
            evidence_urls=(),
            snapshot="CC-MAIN-2026-30",
        )
        for kind, _fixture in cases.values()
    )

    def respond(request: httpx.Request) -> httpx.Response:
        kind, fixture_name = cases[request.url.host]
        del kind
        return httpx.Response(
            200,
            text=(Path(__file__).parent / "fixtures" / "ats" / fixture_name).read_text(),
            request=request,
        )

    validator = PublicATSFeedValidator(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        min_interval_seconds=0,
    )

    assert all(validator.validate(descriptor) for descriptor in descriptors)


def test_common_crawl_default_patterns_cover_all_public_feed_families() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(FIXTURES / "all-providers-index.jsonl").read_text(),
            request=request,
        )

    client = CommonCrawlIndexClient(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        index_base_url="https://index.commoncrawl.test",
        max_records_per_query=20,
        max_patterns=12,
        min_interval_seconds=0,
    )

    descriptors = client.discover(snapshot="CC-MAIN-2026-30")

    assert {descriptor.kind for descriptor in descriptors} == {
        "personio",
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "workable",
        "recruitee",
        "workday",
        "softgarden",
        "dvinci",
        "onlyfy",
        "successfactors",
    }


def test_common_crawl_does_not_consume_patterns_past_the_configured_bound() -> None:
    consumed: list[str] = []

    def patterns() -> Iterable[str]:
        for value in ("one", "two", "three"):
            consumed.append(value)
            yield value

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="", request=request)

    client = CommonCrawlIndexClient(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        index_base_url="https://index.commoncrawl.test",
        max_patterns=2,
        min_interval_seconds=0,
    )

    client.discover(snapshot="CC-MAIN-2026-30", patterns=patterns())

    assert consumed == ["one", "two"]


def test_common_crawl_validates_each_discovered_feed_before_returning_it() -> None:
    def index_response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collinfo.json":
            body = (FIXTURES / "collinfo.json").read_text()
        else:
            body = (FIXTURES / "index.jsonl").read_text()
        return httpx.Response(200, text=body, request=request)

    def feed_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/acme/jobs"):
            return httpx.Response(200, json={"jobs": []}, request=request)
        return httpx.Response(200, json={"unexpected": []}, request=request)

    index_client = CommonCrawlIndexClient(
        client=httpx.Client(transport=httpx.MockTransport(index_response)),
        index_base_url="https://index.commoncrawl.test",
        max_records_per_query=5,
        min_interval_seconds=0,
    )
    validator = PublicATSFeedValidator(
        client=httpx.Client(transport=httpx.MockTransport(feed_response)),
        min_interval_seconds=0,
    )

    descriptors = index_client.discover_validated(
        patterns=("boards.greenhouse.io/*",),
        validator=validator,
    )

    assert descriptors == (
        ATSTenantDescriptor(
            kind="greenhouse",
            tenant="acme",
            source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
            evidence_urls=("https://boards.greenhouse.io/acme/jobs/123",),
            snapshot="CC-MAIN-2026-30",
        ),
    )


def test_common_crawl_treats_an_empty_pattern_as_no_results() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/collinfo.json":
            return httpx.Response(
                200,
                text=(FIXTURES / "collinfo.json").read_text(),
                request=request,
            )
        return httpx.Response(404, request=request)

    client = CommonCrawlIndexClient(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        index_base_url="https://index.commoncrawl.test",
        min_interval_seconds=0,
    )

    assert client.discover(patterns=("no-matching-tenant.example/*",)) == ()
    assert len(requests) == 2


def test_common_crawl_rejects_an_index_hostname_that_resolves_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jobs.network.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(ValueError, match="public"):
        CommonCrawlIndexClient(index_base_url="https://index.commoncrawl.test")
