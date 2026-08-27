from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import jobs.collectors as collectors
from jobs.collection import collect_source, collector_registry
from jobs.collectors import CollectorRegistry
from jobs.collectors.ats import WorkdayCollector
from jobs.models import CareerSource, Company, Job

FIXTURES = Path(__file__).parent / "fixtures" / "ats"


def fixture_client(fixture_name: str) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=(FIXTURES / fixture_name).read_text(), request=request)

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
        "https://api.prescreen.io/api/v1/companies/acme-gmbh/settings",
    ],
)
def test_ats_fingerprint_rejects_non_feed_workable_and_onlyfy_paths(url: str) -> None:
    assert collectors.fingerprint_ats_url(url) is None


def test_workday_fingerprint_strips_query_data_from_a_recognized_board_url() -> None:
    recognized = collectors.fingerprint_ats_url(
        "https://acme.wd5.myworkdayjobs.com/acme/en-US/Acme?returnTo=https://evil.test"
    )

    assert recognized is not None
    assert recognized.kind == "workday"
    assert recognized.tenant == "acme"
    assert recognized.source_url == "https://acme.wd5.myworkdayjobs.com/acme/en-US/Acme"


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
            "https://api.softgarden.test/jobs",
            "softgarden-de",
            "Berlin",
        ),
        (
            "DVinciCollector",
            "dvinci",
            "dvinci.json",
            "https://jobs.dvinci.test/jobs.json",
            "dvinci-json-de",
            "Munich",
        ),
        (
            "DVinciCollector",
            "dvinci",
            "dvinci.xml",
            "https://jobs.dvinci.test/jobs.xml",
            "dvinci-xml-de",
            "Cologne",
        ),
        (
            "OnlyfyPrescreenCollector",
            "onlyfy",
            "onlyfy.json",
            "https://api.prescreen.test/jobs",
            "onlyfy-de",
            "Hamburg",
        ),
        (
            "GreenhouseCollector",
            "greenhouse",
            "greenhouse.json",
            "https://boards-api.greenhouse.test/jobs",
            "101",
            "Cologne",
        ),
        (
            "LeverCollector",
            "lever",
            "lever.json",
            "https://api.lever.test/jobs",
            "lever-de",
            "Berlin",
        ),
        (
            "AshbyCollector",
            "ashby",
            "ashby.json",
            "https://api.ashbyhq.test/jobs",
            "ashby-de",
            "Munich",
        ),
        (
            "SmartRecruitersCollector",
            "smartrecruiters",
            "smartrecruiters.json",
            "https://api.smartrecruiters.test/jobs",
            "smart-de",
            "Berlin",
        ),
        (
            "WorkableCollector",
            "workable",
            "workable.json",
            "https://apply.workable.test/jobs",
            "workable-de",
            "Berlin",
        ),
        (
            "RecruiteeXmlCollector",
            "recruitee",
            "recruitee.xml",
            "https://acme.recruitee.test/offers.xml",
            "recruitee-de",
            "Hamburg",
        ),
        (
            "WorkdayCollector",
            "workday",
            "workday.json",
            "https://acme.wd5.myworkdayjobs.com/acme/en-US/Acme",
            "/job/Berlin/workday-de",
            "Munich",
        ),
        (
            "SuccessFactorsXmlCollector",
            "successfactors",
            "successfactors.xml",
            "https://sf.test/jobs.xml",
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


def test_smartrecruiters_marks_a_capped_page_as_incomplete() -> None:
    import jobs.collectors.ats as ats

    collector_type = getattr(ats, "SmartRecruitersCollector", None)

    assert collector_type is not None
    result = collector_type(
        source("smartrecruiters", "https://api.smartrecruiters.test/jobs", max_pages=1),
        client=fixture_client("smartrecruiters-capped.json"),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["smart-de"]
    assert result.is_complete is False


def test_dvinci_marks_an_unfollowed_json_next_page_as_incomplete() -> None:
    import jobs.collectors.ats as ats

    result = ats.DVinciCollector(
        source("dvinci", "https://jobs.dvinci.test/jobs.json"),
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
        source("softgarden", "https://api.softgarden.test/jobs"),
        client=fixture_client(fixture_name),
    ).collect()

    assert [job.external_id for job in result.raw_jobs] == ["softgarden-de"]
    assert result.is_complete is False


def test_workday_rejects_unsafe_external_paths() -> None:
    result = WorkdayCollector(
        source("workday", "https://acme.wd5.myworkdayjobs.com/acme/en-US/Acme"),
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
