from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from django.core.management import call_command

from jobs.collection import collect_enabled_sources, collect_source, normalize_raw_job
from jobs.collectors import (
    BotProtectionDetected,
    CollectionResult,
    CollectorRegistry,
    HTTPCollector,
    RawJob,
    is_bot_protection_response,
)
from jobs.models import (
    CareerSource,
    Company,
    CrawlRun,
    Job,
    JobMatch,
    SearchProfile,
    UserJobState,
    WorkspaceUser,
)


def test_normalize_raw_job_generates_stable_content_and_repost_hashes() -> None:
    raw_job = RawJob(
        external_id="job-42",
        canonical_url="https://careers.example.test/jobs/42",
        title="  C# Entwickler:in  ",
        description_html="<p>Build&nbsp; systems</p>",
        city="München",
        country_code="de",
        remote_type="hybrid",
        employment_type="Full-time",
        salary_min=Decimal("70000"),
        posted_at=datetime(2026, 8, 25, tzinfo=UTC),
        raw_payload={"id": 42},
    )

    normalized = normalize_raw_job(raw_job=raw_job, company_domain="example.test")

    assert normalized.normalized_title == "c sharp entwickler in"
    assert normalized.description_text == "Build systems"
    assert normalized.country_code == "DE"
    assert len(normalized.content_hash) == 64
    assert len(normalized.fingerprint) == 64

    changed = normalize_raw_job(
        raw_job=replace(raw_job, latitude=48.1351, language_requirement="B2"),
        company_domain="example.test",
    )
    assert changed.content_hash != normalized.content_hash


def test_adapter_registry_resolves_a_source_kind_to_its_collector() -> None:
    raw_job = RawJob(
        external_id="job-42",
        canonical_url="https://careers.example.test/jobs/42",
        title="Engineer",
    )

    class ExampleCollector:
        requests_made = 1

        def __init__(self, source: object) -> None:
            self.source = source

        def collect(self) -> CollectionResult:
            return CollectionResult(raw_jobs=[raw_job], requests_made=1)

    registry = CollectorRegistry()
    registry.register("custom", ExampleCollector)

    collector = registry.create(SimpleNamespace(kind="custom"))

    assert collector.collect().raw_jobs == [raw_job]


def test_bot_protection_detection_handles_statuses_and_challenge_markers() -> None:
    assert is_bot_protection_response(httpx.Response(403, text="Forbidden")) is True
    assert (
        is_bot_protection_response(
            httpx.Response(200, headers={"cf-mitigated": "challenge"}, text="Please wait")
        )
        is True
    )
    assert is_bot_protection_response(httpx.Response(200, text="<h1>Jobs</h1>")) is False


def test_http_collector_stops_when_its_controlled_response_is_blocked() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    )
    collector = HTTPCollector(
        SimpleNamespace(kind="custom", request_delay_seconds=0), client=client
    )

    with pytest.raises(BotProtectionDetected):
        collector.fetch("https://careers.example.test/jobs")

    assert collector.requests_made == 1


def test_http_collector_counts_a_transport_failure_as_an_attempt() -> None:
    def fail_request(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = httpx.Client(transport=httpx.MockTransport(fail_request))
    collector = HTTPCollector(
        SimpleNamespace(kind="custom", request_delay_seconds=0), client=client
    )

    with pytest.raises(httpx.ConnectError):
        collector.fetch("https://careers.example.test/jobs")

    assert collector.requests_made == 1


def test_http_collector_uses_the_configured_delay_between_requests_without_sleeping() -> None:
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    source = SimpleNamespace(
        kind="custom",
        source_url="https://careers.example.test/jobs",
        tenant="example",
        config={"locale": "de-DE"},
        request_delay_seconds=3,
        max_pages=10,
    )
    collector = HTTPCollector(
        source,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        sleeper=delays.append,
    )

    collector.fetch("https://careers.example.test/jobs?page=1")
    collector.fetch("https://careers.example.test/jobs?page=2")

    assert len(requests) == 2
    assert delays == [3]


@pytest.mark.django_db
def test_successful_collection_creates_a_normalized_job_and_source_health() -> None:
    source = make_source()
    registry = registry_for(
        CollectionResult(
            raw_jobs=[
                RawJob(
                    external_id="role-1",
                    canonical_url="https://careers.example.test/jobs/role-1",
                    title="Software Engineer",
                    description_html="<p>Build products</p>",
                )
            ],
            requests_made=2,
        )
    )

    run = collect_source(source=source, registry=registry)

    job = Job.objects.get(source=source, external_id="role-1")
    source.refresh_from_db()
    assert run.status == CrawlRun.Status.SUCCESS
    assert run.jobs_seen == 1
    assert run.jobs_created == 1
    assert run.requests_made == 2
    assert job.normalized_title == "software engineer"
    assert job.description_text == "Build products"
    assert source.last_success_at is not None
    assert source.consecutive_failures == 0
    assert source.last_job_count == 1


@pytest.mark.django_db
def test_collection_evaluates_saved_profiles_for_new_jobs() -> None:
    source = make_source()
    user = WorkspaceUser.objects.create(name="Grace")
    profile = SearchProfile.objects.create(
        user=user,
        name="Engineering",
        included_titles=["software engineer"],
    )

    collect_source(
        source=source,
        registry=registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="role-1",
                        canonical_url="https://careers.example.test/jobs/role-1",
                        title="Software Engineer",
                        remote_type="remote",
                    )
                ]
            )
        ),
    )

    assert JobMatch.objects.filter(profile=profile, job__source=source).exists()


@pytest.mark.django_db
def test_seen_job_is_updated_reopened_and_its_miss_count_is_reset() -> None:
    source = make_source()
    collect_source(
        source=source,
        registry=registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="role-1",
                        canonical_url="https://careers.example.test/jobs/role-1",
                        title="Engineer",
                        description_text="Old description",
                    )
                ]
            )
        ),
    )
    job = Job.objects.get(source=source, external_id="role-1")
    job.closed_at = datetime(2026, 8, 24, tzinfo=UTC)
    job.missed_runs = 2
    job.save(update_fields=["closed_at", "missed_runs"])

    run = collect_source(
        source=source,
        registry=registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="role-1",
                        canonical_url="https://careers.example.test/jobs/role-1",
                        title="Engineer II",
                        description_text="New description",
                    )
                ]
            )
        ),
    )

    job.refresh_from_db()
    assert run.jobs_created == 0
    assert run.jobs_updated == 1
    assert job.title == "Engineer II"
    assert job.closed_at is None
    assert job.missed_runs == 0


@pytest.mark.django_db
def test_job_closes_only_after_two_successful_source_runs_miss_it() -> None:
    source = make_source()
    user = WorkspaceUser.objects.create(name="Linus")
    profile = SearchProfile.objects.create(
        user=user,
        name="Engineering",
        included_titles=["engineer"],
    )
    disabled_profile = SearchProfile.objects.create(
        user=user,
        name="Paused engineering",
        included_titles=["engineer"],
        is_enabled=False,
    )
    collect_source(
        source=source,
        registry=registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="role-1",
                        canonical_url="https://careers.example.test/jobs/role-1",
                        title="Engineer",
                    )
                ]
            )
        ),
    )
    job = Job.objects.get(source=source, external_id="role-1")
    assert JobMatch.objects.filter(job=job, profile=profile).exists()
    JobMatch.objects.create(job=job, profile=disabled_profile, score=0, explanation={})

    first_miss = collect_source(source=source, registry=registry_for(CollectionResult(raw_jobs=[])))

    job.refresh_from_db()
    assert first_miss.status == CrawlRun.Status.SUCCESS
    assert first_miss.jobs_closed == 0
    assert job.missed_runs == 1
    assert job.closed_at is None

    second_miss = collect_source(
        source=source, registry=registry_for(CollectionResult(raw_jobs=[]))
    )

    job.refresh_from_db()
    assert second_miss.jobs_closed == 1
    assert job.missed_runs == 2
    assert job.closed_at is not None
    assert not JobMatch.objects.filter(job=job, profile=profile).exists()
    assert not JobMatch.objects.filter(job=job, profile=disabled_profile).exists()


@pytest.mark.django_db
def test_ignored_job_state_propagates_to_a_repost_with_the_same_fingerprint() -> None:
    source = make_source()
    user = WorkspaceUser.objects.create(name="Ada")
    collect_source(
        source=source,
        registry=registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="old-role",
                        canonical_url="https://careers.example.test/jobs/old-role",
                        title="Software Engineer",
                        city="Berlin",
                        employment_type="Full-time",
                    )
                ]
            )
        ),
    )
    old_job = Job.objects.get(source=source, external_id="old-role")
    UserJobState.objects.create(user=user, job=old_job, status=UserJobState.Status.IGNORED)

    collect_source(
        source=source,
        registry=registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="repost-role",
                        canonical_url="https://careers.example.test/jobs/repost-role",
                        title="Software Engineer",
                        city="Berlin",
                        employment_type="Full-time",
                    )
                ]
            )
        ),
    )

    repost = Job.objects.get(source=source, external_id="repost-role")
    state = UserJobState.objects.get(user=user, job=repost)
    assert repost.fingerprint == old_job.fingerprint
    assert state.status == UserJobState.Status.IGNORED


@pytest.mark.django_db
def test_bot_block_marks_the_source_and_records_a_blocked_run() -> None:
    source = make_source()

    class BlockingCollector:
        requests_made = 3

        def collect(self) -> CollectionResult:
            raise BotProtectionDetected("challenge page")

    registry = CollectorRegistry()
    registry.register("custom", lambda source: BlockingCollector())

    run = collect_source(source=source, registry=registry)

    source.refresh_from_db()
    assert run.status == CrawlRun.Status.BLOCKED
    assert run.finished_at is not None
    assert run.error == "challenge page"
    assert run.requests_made == 3
    assert source.blocked_at is not None
    assert source.consecutive_failures == 0


@pytest.mark.django_db
def test_collector_failure_records_source_health_without_closing_jobs() -> None:
    source = make_source()
    existing = Job.objects.create(
        source=source,
        external_id="existing-role",
        canonical_url="https://careers.example.test/jobs/existing-role",
        title="Engineer",
        normalized_title="engineer",
        content_hash="a" * 64,
        fingerprint="b" * 64,
    )

    class FailingCollector:
        requests_made = 2

        def collect(self) -> CollectionResult:
            raise RuntimeError("temporary outage")

    registry = CollectorRegistry()
    registry.register("custom", lambda source: FailingCollector())

    run = collect_source(source=source, registry=registry)

    source.refresh_from_db()
    assert run.status == CrawlRun.Status.FAILED
    assert run.error == "temporary outage"
    assert run.requests_made == 2
    assert source.last_failure_at is not None
    assert source.consecutive_failures == 1
    existing.refresh_from_db()
    assert existing.missed_runs == 0
    assert existing.closed_at is None


@pytest.mark.django_db
def test_processing_failure_rolls_back_earlier_jobs_and_allows_later_sources() -> None:
    failing_source = make_source("failing")
    later_source = make_source("later")
    failing_result = CollectionResult(
        raw_jobs=[
            RawJob(
                external_id="valid-first",
                canonical_url="https://careers.failing.test/jobs/valid-first",
                title="Engineer",
            ),
            RawJob(
                external_id=cast(str, None),
                canonical_url="https://careers.failing.test/jobs/malformed",
                title="Malformed",
            ),
        ],
        requests_made=2,
    )
    later_result = CollectionResult(
        raw_jobs=[
            RawJob(
                external_id="later-role",
                canonical_url="https://careers.later.test/jobs/later-role",
                title="Engineer",
            )
        ],
        requests_made=1,
    )
    registry = CollectorRegistry()
    registry.register(
        "custom",
        lambda source: FixtureCollector(
            source,
            failing_result if source.source_url == failing_source.source_url else later_result,
        ),
    )

    runs = collect_enabled_sources(registry=registry)

    failing_run = next(run for run in runs if run.source_id == failing_source.id)
    failing_source.refresh_from_db()
    assert failing_run.status == CrawlRun.Status.FAILED
    assert failing_run.finished_at is not None
    assert failing_run.requests_made == 2
    assert failing_run.error
    assert failing_source.consecutive_failures == 1
    assert not Job.objects.filter(source=failing_source).exists()
    assert Job.objects.filter(source=later_source, external_id="later-role").exists()


@pytest.mark.django_db
def test_existing_running_run_serializes_a_second_collection_for_the_source() -> None:
    source = make_source()
    running = CrawlRun.objects.create(source=source)
    calls: list[str] = []

    class CountingCollector:
        requests_made = 0

        def collect(self) -> CollectionResult:
            calls.append("called")
            return CollectionResult(raw_jobs=[])

    registry = CollectorRegistry()
    registry.register("custom", lambda source: CountingCollector())

    returned = collect_source(source=source, registry=registry)

    assert returned.pk == running.pk
    assert returned.status == CrawlRun.Status.RUNNING
    assert calls == []


@pytest.mark.django_db
def test_enabled_source_orchestration_skips_disabled_and_blocked_sources() -> None:
    active = make_source()
    disabled = make_source("disabled")
    disabled.is_enabled = False
    disabled.save(update_fields=["is_enabled"])
    blocked = make_source("blocked")
    blocked.blocked_at = datetime(2026, 8, 25, tzinfo=UTC)
    blocked.save(update_fields=["blocked_at"])

    runs = collect_enabled_sources(registry=registry_for(CollectionResult(raw_jobs=[])))

    assert [run.source_id for run in runs] == [active.id]


def test_daily_celery_task_returns_completed_run_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobs.tasks import collect_all_sources

    monkeypatch.setattr("jobs.tasks.collect_enabled_sources", lambda: [SimpleNamespace(id=3)])

    assert collect_all_sources.run() == [3]


@pytest.mark.django_db
def test_collect_jobs_command_collects_a_single_source(monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_source()
    from jobs.management.commands import collect_jobs

    monkeypatch.setattr(
        collect_jobs,
        "collector_registry",
        registry_for(
            CollectionResult(
                raw_jobs=[
                    RawJob(
                        external_id="role-1",
                        canonical_url="https://careers.example.test/jobs/role-1",
                        title="Engineer",
                    )
                ]
            )
        ),
    )
    output = StringIO()

    call_command("collect_jobs", "--source", str(source.id), stdout=output)

    assert Job.objects.filter(source=source).count() == 1
    assert "1 source run completed" in output.getvalue()


class FixtureCollector:
    def __init__(self, source: object, result: CollectionResult) -> None:
        self.source = source
        self.result = result
        self.requests_made = result.requests_made

    def collect(self) -> CollectionResult:
        return self.result


def registry_for(result: CollectionResult) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register("custom", lambda source: FixtureCollector(source, result))
    return registry


def make_source(slug: str = "example") -> CareerSource:
    company = Company.objects.create(
        name=f"{slug.title()} GmbH",
        domain=f"{slug}.test",
        career_url=f"https://careers.{slug}.test",
    )
    return CareerSource.objects.create(
        company=company,
        kind=CareerSource.Kind.CUSTOM,
        source_url=f"https://careers.{slug}.test/jobs",
    )
