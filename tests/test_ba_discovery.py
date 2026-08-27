from __future__ import annotations

from datetime import date
from io import StringIO
from pathlib import Path

import httpx
import pytest
from django.core.management import call_command
from django.test import override_settings

from jobs.ba_discovery import (
    ArbeitsagenturJobsucheClient,
    BAJobSignal,
    EmployerDiscoveryService,
    JobsucheProviderError,
    group_employer_signals,
)
from jobs.models import EmployerDiscoveryRun, EmployerSignalSnapshot, MonitoringTarget

FIXTURES = Path(__file__).parent / "fixtures" / "ba"


def fixture_client(requests: list[httpx.Request]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(FIXTURES / "jobs-page.json").read_text(),
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(respond))


def fixture_sequence_client(
    fixture_names: list[str], requests: list[httpx.Request]
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


def test_jobsuche_client_requests_configured_german_search_and_parses_typed_signals() -> None:
    requests: list[httpx.Request] = []
    jobsuche = ArbeitsagenturJobsucheClient(
        client=fixture_client(requests),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=50,
        min_interval_seconds=0,
    )

    page = jobsuche.search(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        offer_type=1,
        include_temporary_agencies=False,
        page=1,
    )

    assert len(requests) == 1
    assert requests[0].url.path == "/jobboerse/jobsuche-service/pc/v6/jobs"
    assert dict(requests[0].url.params) == {
        "wo": "Berlin",
        "umkreis": "25",
        "veroeffentlichtseit": "30",
        "zeitarbeit": "false",
        "angebotsart": "1",
        "page": "1",
        "size": "50",
    }
    assert requests[0].headers["x-api-key"] == "test-key"
    assert page.total_results == 4
    assert page.is_complete is True
    assert page.signals == (
        BAJobSignal(
            employer_name="Acme GmbH",
            job_title="Backend Engineer",
            location="Berlin",
            postal_code="10115",
            reference_number="10000-1-S",
            published_at=date(2026, 8, 25),
        ),
        BAJobSignal(
            employer_name="ACME GMBH",
            job_title="Data Engineer",
            location="Munich",
            postal_code="80331",
            reference_number="10000-2-S",
            published_at=date(2026, 8, 20),
        ),
        BAJobSignal(
            employer_name="Other AG",
            job_title="Office Manager",
            location="Hamburg",
            postal_code="20095",
            reference_number="10000-3-S",
            published_at=date(2026, 7, 1),
        ),
    )


def test_jobsuche_client_rejects_a_malformed_job_record() -> None:
    requests: list[httpx.Request] = []
    client = ArbeitsagenturJobsucheClient(
        client=fixture_sequence_client(["malformed-page.json"], requests),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=50,
        min_interval_seconds=0,
    )

    with pytest.raises(JobsucheProviderError, match="invalid job record"):
        client.search(city="Berlin", radius_km=25, publication_age_days=30)


def test_employer_grouping_deduplicates_jobs_and_exposes_recent_activity() -> None:
    signals = (
        BAJobSignal(
            employer_name="Acme GmbH",
            job_title="Backend Engineer",
            location="Berlin",
            postal_code="10115",
            reference_number="10000-1-S",
            published_at=date(2026, 8, 25),
        ),
        BAJobSignal(
            employer_name="ACME GMBH",
            job_title="Data Engineer",
            location="Munich",
            postal_code="80331",
            reference_number="10000-2-S",
            published_at=date(2026, 8, 20),
        ),
        BAJobSignal(
            employer_name="Acme GmbH",
            job_title="Duplicate posting",
            location="Berlin",
            postal_code="10115",
            reference_number="10000-1-S",
            published_at=date(2026, 8, 25),
        ),
        BAJobSignal(
            employer_name="Other AG",
            job_title="Office Manager",
            location="Hamburg",
            postal_code="20095",
            reference_number="10000-3-S",
            published_at=date(2026, 7, 1),
        ),
    )

    employers = group_employer_signals(signals, recent_since=date(2026, 8, 1))

    assert [(employer.normalized_name, employer.employer_name) for employer in employers] == [
        ("acme gmbh", "Acme GmbH"),
        ("other ag", "Other AG"),
    ]
    assert employers[0].total_active_jobs == 2
    assert employers[0].recent_jobs == 2
    assert employers[0].distinct_locations == ("Berlin", "Munich")
    assert len(employers[0].signals) == 2
    assert employers[1].recent_jobs == 0


def test_employer_discovery_follows_bounded_pages_and_groups_all_known_signals() -> None:
    requests: list[httpx.Request] = []
    client = ArbeitsagenturJobsucheClient(
        client=fixture_sequence_client(["jobs-page-1.json", "jobs-page-2.json"], requests),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=2,
        min_interval_seconds=0,
    )

    result = EmployerDiscoveryService(client=client).discover(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        max_pages=2,
        as_of=date(2026, 8, 27),
    )

    assert result.error is None
    assert result.is_complete is True
    assert result.total_active_jobs == 3
    assert [employer.employer_name for employer in result.employers] == [
        "Acme GmbH",
        "Other AG",
    ]
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


def test_discovery_request_count_is_scoped_to_each_run() -> None:
    client = ArbeitsagenturJobsucheClient(
        client=fixture_client([]),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=50,
        min_interval_seconds=0,
    )
    service = EmployerDiscoveryService(client=client)

    first = service.discover(
        city="Berlin", radius_km=25, publication_age_days=30, as_of=date(2026, 8, 27)
    )
    second = service.discover(
        city="Berlin", radius_km=25, publication_age_days=30, as_of=date(2026, 8, 27)
    )

    assert first.requests_made == 1
    assert second.requests_made == 1


@pytest.mark.django_db
def test_employer_discovery_persists_activity_without_creating_monitoring_targets() -> None:
    client = ArbeitsagenturJobsucheClient(
        client=fixture_client([]),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=50,
        min_interval_seconds=0,
    )

    result = EmployerDiscoveryService(client=client).discover_and_persist(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        offer_type=4,
        include_temporary_agencies=True,
        as_of=date(2026, 8, 27),
    )

    snapshot = EmployerSignalSnapshot.objects.get(normalized_name="acme gmbh")
    assert result.is_complete is True
    assert snapshot.employer_name == "Acme GmbH"
    assert snapshot.total_active_jobs == 2
    assert snapshot.recent_jobs == 2
    assert snapshot.distinct_locations == ["Berlin", "Munich"]
    assert len(snapshot.signals) == 2
    assert snapshot.offer_type == 4
    assert snapshot.include_temporary_agencies is True
    assert MonitoringTarget.objects.count() == 0
    assert snapshot.run_id is not None


@pytest.mark.django_db
def test_empty_complete_discovery_creates_a_run_without_employer_snapshots() -> None:
    requests: list[httpx.Request] = []
    client = ArbeitsagenturJobsucheClient(
        client=fixture_sequence_client(["jobs-empty-page.json"], requests),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=50,
        min_interval_seconds=0,
    )

    result = EmployerDiscoveryService(client=client).discover_and_persist(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        as_of=date(2026, 8, 27),
    )

    run = EmployerDiscoveryRun.objects.get(query_city="Berlin")
    assert result.is_complete is True
    assert run.is_complete is True
    assert run.snapshots.count() == 0


@pytest.mark.django_db
def test_empty_complete_discovery_supersedes_previous_employer_activity() -> None:
    requests: list[httpx.Request] = []
    client = ArbeitsagenturJobsucheClient(
        client=fixture_sequence_client(
            ["jobs-page.json", "jobs-empty-page.json"],
            requests,
        ),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        result_limit=50,
        min_interval_seconds=0,
    )
    service = EmployerDiscoveryService(client=client)

    service.discover_and_persist(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        as_of=date(2026, 8, 27),
    )
    service.discover_and_persist(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        as_of=date(2026, 8, 27),
    )

    latest_run = EmployerDiscoveryRun.objects.order_by("-pk").first()
    assert latest_run is not None
    assert latest_run.snapshots.count() == 0
    assert EmployerSignalSnapshot.objects.filter(run__isnull=False).count() == 2
    assert EmployerSignalSnapshot.objects.filter(run=latest_run).count() == 0


@pytest.mark.django_db
def test_provider_failure_keeps_existing_snapshots_and_is_not_complete() -> None:
    existing = EmployerSignalSnapshot.objects.create(
        normalized_name="acme gmbh",
        employer_name="Acme GmbH",
        total_active_jobs=4,
        recent_jobs=3,
        distinct_locations=["Berlin"],
        signals=[{"reference_number": "old"}],
        query_city="Berlin",
        query_radius_km=25,
        publication_age_days=30,
        is_complete=True,
    )

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider offline", request=request)

    client = ArbeitsagenturJobsucheClient(
        client=httpx.Client(transport=httpx.MockTransport(fail)),
        base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
        api_key="test-key",
        min_interval_seconds=0,
    )

    result = EmployerDiscoveryService(client=client).discover_and_persist(
        city="Berlin",
        radius_km=25,
        publication_age_days=30,
        as_of=date(2026, 8, 27),
    )

    existing.refresh_from_db()
    assert result.is_complete is False
    assert result.error is not None
    assert EmployerSignalSnapshot.objects.count() == 1
    assert existing.total_active_jobs == 4


@pytest.mark.django_db
@override_settings(
    BA_JOBS_API_BASE_URL="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
    BA_JOBS_API_KEY="test-key",
    BA_JOBS_RESULT_LIMIT=50,
    BA_JOBS_MIN_REQUEST_INTERVAL_SECONDS=0,
)
def test_discover_employers_command_persists_signals_without_monitoring_targets() -> None:
    output = StringIO()
    from jobs import ba_discovery

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(FIXTURES / "jobs-page.json").read_text(),
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    service = ba_discovery.EmployerDiscoveryService(
        client=ba_discovery.ArbeitsagenturJobsucheClient(
            client=client,
            base_url="https://rest.arbeitsagentur.test/jobboerse/jobsuche-service",
            api_key="test-key",
            result_limit=50,
            min_interval_seconds=0,
        )
    )

    # The command accepts an injected service factory in tests and still owns no account state.
    from unittest.mock import patch

    with patch(
        "jobs.management.commands.discover_employers.EmployerDiscoveryService", return_value=service
    ):
        call_command(
            "discover_employers",
            "Berlin",
            radius_km=25,
            publication_age_days=30,
            stdout=output,
        )

    assert EmployerSignalSnapshot.objects.count() == 2
    assert MonitoringTarget.objects.count() == 0
    assert "2 employers" in output.getvalue()
