import pytest
from django.db import IntegrityError, transaction

from jobs.models import CareerSource, Company, GermanPlace, Job, MonitoringTarget, WorkspaceUser
from jobs.monitoring import filter_jobs_for_user


def make_company(name: str) -> Company:
    slug = name.lower().replace(" ", "-")
    return Company.objects.create(
        name=name,
        domain=f"{slug}.example.test",
        career_url=f"https://{slug}.example.test/careers",
    )


def make_job(*, company: Company, external_id: str, **overrides: object) -> Job:
    source = CareerSource.objects.create(
        company=company,
        source_url=f"https://{company.domain}/jobs/{external_id}",
    )
    defaults: dict[str, object] = {
        "source": source,
        "external_id": external_id,
        "canonical_url": f"https://{company.domain}/jobs/{external_id}",
        "title": "Engineer",
        "normalized_title": "engineer",
        "content_hash": "a" * 64,
        "fingerprint": "b" * 64,
    }
    defaults.update(overrides)
    return Job.objects.create(**defaults)


def make_place(
    *, name: str, latitude: float, longitude: float, normalized_name: str | None = None
) -> GermanPlace:
    return GermanPlace.objects.create(
        source_id=f"test:{name.lower().replace(' ', '-')}",
        name=name,
        normalized_name=normalized_name or name.lower(),
        latitude=latitude,
        longitude=longitude,
        source_kind=GermanPlace.SourceKind.CITY,
    )


@pytest.mark.django_db
def test_filter_jobs_for_user_returns_no_jobs_without_selected_sources() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    company = make_company("Example")
    first = make_job(company=company, external_id="first")
    second = make_job(company=company, external_id="second")

    result = filter_jobs_for_user(user=user, jobs=(second, first))

    assert result == []


@pytest.mark.django_db
def test_filter_jobs_for_user_matches_jobs_from_selected_companies() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    selected_company = make_company("Selected")
    other_company = make_company("Other")
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.COMPANY,
        company=selected_company,
    )
    selected_job = make_job(company=selected_company, external_id="selected")
    other_job = make_job(company=other_company, external_id="other")

    result = filter_jobs_for_user(user=user, jobs=[other_job, selected_job])

    assert result == [selected_job]


@pytest.mark.django_db(transaction=True)
def test_filter_jobs_for_user_preserves_input_order_for_multiple_matching_jobs() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    company = make_company("Selected")
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.COMPANY,
        company=company,
    )
    first_job = make_job(company=company, external_id="first")
    second_job = make_job(company=company, external_id="second")

    result = filter_jobs_for_user(user=user, jobs=[second_job, first_job])

    assert result == [second_job, first_job]


@pytest.mark.django_db
def test_filter_jobs_for_user_matches_jobs_within_a_city_radius() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    berlin = make_place(name="Berlin", latitude=52.52, longitude=13.405)
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=berlin,
        radius_km=25,
    )
    company = make_company("Example")
    nearby_job = make_job(
        company=company,
        external_id="nearby",
        latitude=52.53,
        longitude=13.4,
    )
    distant_job = make_job(
        company=company,
        external_id="distant",
        latitude=53.5511,
        longitude=9.9937,
    )

    result = filter_jobs_for_user(user=user, jobs=[distant_job, nearby_job])

    assert result == [nearby_job]


@pytest.mark.django_db
def test_filter_jobs_for_user_normalizes_city_when_job_has_no_coordinates() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    munich = make_place(
        name="München",
        normalized_name="munchen",
        latitude=48.1372,
        longitude=11.5756,
    )
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=munich,
        radius_km=25,
    )
    company = make_company("Example")
    normalized_city_job = make_job(company=company, external_id="normalized", city="Munchen")
    different_city_job = make_job(company=company, external_id="different", city="Berlin")

    result = filter_jobs_for_user(user=user, jobs=[different_city_job, normalized_city_job])

    assert result == [normalized_city_job]


@pytest.mark.django_db
def test_filter_jobs_for_user_uses_only_the_requested_accounts_targets() -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    ada_company = make_company("Ada Company")
    grace_company = make_company("Grace Company")
    MonitoringTarget.objects.create(
        user=ada,
        kind=MonitoringTarget.Kind.COMPANY,
        company=ada_company,
    )
    MonitoringTarget.objects.create(
        user=grace,
        kind=MonitoringTarget.Kind.COMPANY,
        company=grace_company,
    )
    ada_job = make_job(company=ada_company, external_id="ada")
    grace_job = make_job(company=grace_company, external_id="grace")

    assert filter_jobs_for_user(user=ada, jobs=[grace_job, ada_job]) == [ada_job]
    assert filter_jobs_for_user(user=grace, jobs=[grace_job, ada_job]) == [grace_job]


@pytest.mark.django_db(transaction=True)
def test_monitoring_target_database_constraints_reject_malformed_and_duplicate_targets() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    other_user = WorkspaceUser.objects.create(name="Grace")
    company = make_company("Example")
    berlin = make_place(name="Berlin", latitude=52.52, longitude=13.405)

    with pytest.raises(IntegrityError), transaction.atomic():
        MonitoringTarget.objects.create(user=user, kind=MonitoringTarget.Kind.COMPANY)
    with pytest.raises(IntegrityError), transaction.atomic():
        MonitoringTarget.objects.create(
            user=user,
            kind=MonitoringTarget.Kind.CITY,
            company=company,
            place=berlin,
            radius_km=25,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MonitoringTarget.objects.create(
            user=user,
            kind=MonitoringTarget.Kind.CITY,
            place=berlin,
            radius_km=0,
        )

    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.COMPANY,
        company=company,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MonitoringTarget.objects.create(
            user=user,
            kind=MonitoringTarget.Kind.COMPANY,
            company=company,
        )

    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=berlin,
        radius_km=25,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MonitoringTarget.objects.create(
            user=user,
            kind=MonitoringTarget.Kind.CITY,
            place=berlin,
            radius_km=25,
        )

    MonitoringTarget.objects.create(
        user=other_user,
        kind=MonitoringTarget.Kind.COMPANY,
        company=company,
    )
