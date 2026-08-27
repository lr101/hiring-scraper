from collections.abc import Iterator

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from jobs.models import (
    CareerSource,
    Company,
    ExclusionRule,
    Job,
    JobMatch,
    SearchProfile,
    UserJobState,
    WorkspaceUser,
)


@pytest.mark.django_db
def test_profile_save_parses_multiple_entries_and_creates_open_job_matches(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    job = make_job(remote_type=Job.RemoteType.REMOTE)
    client.cookies["workspace_user"] = str(user.pk)

    response = client.post(
        reverse("jobs:profile_create"),
        {
            **profile_form_data("Python jobs"),
            "included_titles": " Software Engineer\nsoftware engineer ",
            "required_skill_groups": "Python, Django\nAWS, Azure",
            "preferred_skills": "Docker, Docker",
        },
    )

    assert response.status_code == 302
    profile = SearchProfile.objects.get(user=user)
    assert profile.included_titles == ["Software Engineer"]
    assert profile.required_skill_groups == [["Python", "Django"], ["AWS", "Azure"]]
    assert profile.preferred_skills == ["Docker"]
    assert JobMatch.objects.filter(profile=profile, job=job).exists()


def profile_form_data(name: str) -> dict[str, str]:
    return {
        "name": name,
        "is_enabled": "on",
        "include_remote": "on",
        "weight_title": "50",
        "weight_required_skills": "30",
        "weight_preferred_skills": "10",
        "weight_location": "10",
        "weight_unknown_location": "-10",
        "minimum_score": "0",
    }


def make_job(**overrides: object) -> Job:
    company = Company.objects.create(
        name="Example GmbH", domain="example.com", career_url="https://example.com/careers"
    )
    source = CareerSource.objects.create(company=company, source_url="https://example.com/jobs")
    defaults: dict[str, object] = {
        "source": source,
        "external_id": "backend-engineer",
        "canonical_url": "https://example.com/jobs/backend-engineer",
        "title": "Software Engineer",
        "normalized_title": "software engineer",
        "country_code": "DE",
        "skills": ["Python", "AWS", "Docker"],
        "content_hash": "a" * 64,
        "fingerprint": "b" * 64,
    }
    defaults.update(overrides)
    return Job.objects.create(**defaults)


@pytest.fixture
def restore_current_migration_leaf() -> Iterator[None]:
    yield
    MigrationExecutor(connection).migrate([("jobs", "0008_account_city_sources")])


@pytest.mark.django_db(transaction=True)
def test_company_domain_migration_merges_duplicate_domains(
    restore_current_migration_leaf: None,
) -> None:
    previous_target = ("jobs", "0006_monitoring_targets")
    current_target = ("jobs", "0007_dynamic_company_domains")
    executor = MigrationExecutor(connection)
    executor.migrate([previous_target])
    old_apps = executor.loader.project_state([previous_target]).apps
    CompanyOld = old_apps.get_model("jobs", "Company")
    CareerSourceOld = old_apps.get_model("jobs", "CareerSource")
    MonitoringTargetOld = old_apps.get_model("jobs", "MonitoringTarget")
    WorkspaceUserOld = old_apps.get_model("jobs", "WorkspaceUser")

    user = WorkspaceUserOld.objects.create(name="Ada")
    survivor = CompanyOld.objects.create(
        name="Older Acme", domain="Example.com", career_url="https://example.com/careers"
    )
    duplicate = CompanyOld.objects.create(
        name="Duplicate Acme", domain="example.com", career_url="https://example.com/jobs"
    )
    CareerSourceOld.objects.create(company=duplicate, source_url="https://example.com/jobs")
    MonitoringTargetOld.objects.create(user=user, kind="company", company=duplicate)

    executor = MigrationExecutor(connection)
    executor.migrate([current_target])
    new_apps = executor.loader.project_state([current_target]).apps
    CompanyNew = new_apps.get_model("jobs", "Company")
    CareerSourceNew = new_apps.get_model("jobs", "CareerSource")
    MonitoringTargetNew = new_apps.get_model("jobs", "MonitoringTarget")

    assert CompanyNew.objects.count() == 1
    company = CompanyNew.objects.get()
    assert company.pk == survivor.pk
    assert company.domain == "example.com"
    assert CareerSourceNew.objects.get().company_id == survivor.pk
    assert MonitoringTargetNew.objects.get().company_id == survivor.pk


@pytest.mark.django_db(transaction=True)
def test_account_city_migration_moves_legacy_profile_locations_to_account_targets(
    restore_current_migration_leaf: None,
) -> None:
    previous_target = ("jobs", "0007_dynamic_company_domains")
    current_target = ("jobs", "0008_account_city_sources")
    executor = MigrationExecutor(connection)
    executor.migrate([previous_target])
    old_apps = executor.loader.project_state([previous_target]).apps
    WorkspaceUserOld = old_apps.get_model("jobs", "WorkspaceUser")
    SearchProfileOld = old_apps.get_model("jobs", "SearchProfile")
    GermanPlaceOld = old_apps.get_model("jobs", "GermanPlace")
    ProfileLocationOld = old_apps.get_model("jobs", "ProfileLocation")
    MonitoringTargetOld = old_apps.get_model("jobs", "MonitoringTarget")

    user = WorkspaceUserOld.objects.create(name="Ada")
    profile = SearchProfileOld.objects.create(user=user, name="Engineering")
    place = GermanPlaceOld.objects.create(
        source_id="test:berlin",
        name="Berlin",
        normalized_name="berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind="city",
    )
    ProfileLocationOld.objects.create(
        profile=profile,
        place=place,
        city="Berlin",
        latitude=52.52,
        longitude=13.405,
        radius_km=25,
    )
    MonitoringTargetOld.objects.create(user=user, kind="city", place=place, radius_km=10)

    executor = MigrationExecutor(connection)
    executor.migrate([current_target])
    new_apps = executor.loader.project_state([current_target]).apps
    MonitoringTargetNew = new_apps.get_model("jobs", "MonitoringTarget")

    target = MonitoringTargetNew.objects.get(user_id=user.pk, place_id=place.pk)
    assert target.radius_km == 25
    with pytest.raises(LookupError):
        new_apps.get_model("jobs", "ProfileLocation")


@pytest.mark.django_db
def test_enabled_company_exclusion_removes_only_that_accounts_matches() -> None:
    from jobs.matching import refresh_user_profile_matches

    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    ada_profile = SearchProfile.objects.create(user=ada, name="Ada profile")
    grace_profile = SearchProfile.objects.create(user=grace, name="Grace profile")
    job = make_job()
    ExclusionRule.objects.create(
        user=ada,
        kind=ExclusionRule.Kind.COMPANY,
        pattern="example",
        normalized_pattern="example",
    )

    refresh_user_profile_matches(user=ada)
    refresh_user_profile_matches(user=grace)

    assert JobMatch.objects.filter(profile=ada_profile, job=job).exists() is False
    assert JobMatch.objects.filter(profile=grace_profile, job=job).exists() is True


@pytest.mark.django_db
def test_private_profile_routes_require_the_explicit_cookie_and_hide_other_accounts(
    client: Client,
) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    grace_profile = SearchProfile.objects.create(user=grace, name="Grace profile")

    no_selection = client.get(reverse("jobs:profile_list"))
    client.cookies["workspace_user"] = str(ada.pk)
    cross_account = client.get(reverse("jobs:profile_edit", args=[grace_profile.pk]))

    assert no_selection.status_code == 302
    assert no_selection["Location"] == reverse("jobs:account_list")
    assert cross_account.status_code == 404


@pytest.mark.django_db
def test_exclusion_form_normalizes_equivalent_duplicates_and_rechecks_matches(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    profile = SearchProfile.objects.create(user=user, name="Profile")
    job = make_job(title="Senior Python Engineer")
    client.cookies["workspace_user"] = str(user.pk)
    JobMatch.objects.create(job=job, profile=profile, score=1, explanation={})

    created = client.post(
        reverse("jobs:exclusion_create"),
        {"kind": ExclusionRule.Kind.TITLE, "pattern": "  python   engineer ", "is_enabled": "on"},
    )
    duplicate = client.post(
        reverse("jobs:exclusion_create"),
        {"kind": ExclusionRule.Kind.TITLE, "pattern": "Python Engineer", "is_enabled": "on"},
    )

    rule = ExclusionRule.objects.get(user=user)
    assert created.status_code == 302
    assert rule.pattern == "python engineer"
    assert rule.normalized_pattern == "python engineer"
    assert JobMatch.objects.filter(job=job, profile=profile).exists() is False
    assert duplicate.status_code == 200
    assert ExclusionRule.objects.filter(user=user).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("kind", "pattern"),
    [
        (ExclusionRule.Kind.COMPANY, "Example GmbH"),
        (ExclusionRule.Kind.WEBSITE, "example.com"),
        (ExclusionRule.Kind.TITLE, "software engineer"),
        (ExclusionRule.Kind.SKILL, "python"),
    ],
)
def test_each_exclusion_kind_removes_matches_without_changing_job_state(
    kind: str, pattern: str
) -> None:
    from jobs.matching import refresh_user_profile_matches

    user = WorkspaceUser.objects.create(name="Ada")
    profile = SearchProfile.objects.create(user=user, name="Profile")
    job = make_job()
    state = UserJobState.objects.create(user=user, job=job, status=UserJobState.Status.SAVED)
    ExclusionRule.objects.create(
        user=user,
        kind=kind,
        pattern=pattern,
        normalized_pattern=pattern.casefold(),
    )

    refresh_user_profile_matches(user=user)

    assert JobMatch.objects.filter(profile=profile, job=job).exists() is False
    assert UserJobState.objects.get(pk=state.pk).status == UserJobState.Status.SAVED


@pytest.mark.django_db
def test_profile_can_save_without_a_city_because_cities_are_account_sources(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)

    response = client.post(reverse("jobs:profile_create"), profile_form_data("Engineering"))

    assert response.status_code == 302
    assert SearchProfile.objects.filter(user=user).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("title_weight", [None, "40000"])
def test_profile_form_reports_an_invalid_weight_without_raising_key_error(
    client: Client, title_weight: str | None
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)
    form_data = profile_form_data("Invalid title weight")
    if title_weight is None:
        del form_data["weight_title"]
    else:
        form_data["weight_title"] = title_weight

    response = client.post(reverse("jobs:profile_create"), form_data)

    assert response.status_code == 200
    assert "weight_title" in response.context["form"].errors
    assert SearchProfile.objects.filter(user=user).exists() is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [("maximum_german_level", "Z9"), ("minimum_score", "32768")],
)
def test_profile_form_rejects_values_outside_matching_bounds(
    client: Client, field: str, value: str
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)
    form_data = profile_form_data("Validated")
    form_data[field] = value

    response = client.post(reverse("jobs:profile_create"), form_data)

    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert SearchProfile.objects.filter(user=user).exists() is False


@pytest.mark.django_db
def test_c_sharp_exclusion_rules_normalize_consistently_and_deduplicate(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    profile = SearchProfile.objects.create(user=user, name="Profile")
    job = make_job(skills=["C#"])
    client.cookies["workspace_user"] = str(user.pk)

    created = client.post(
        reverse("jobs:exclusion_create"),
        {"kind": ExclusionRule.Kind.SKILL, "pattern": " C# ", "is_enabled": "on"},
    )
    duplicate = client.post(
        reverse("jobs:exclusion_create"),
        {"kind": ExclusionRule.Kind.SKILL, "pattern": "c sharp", "is_enabled": "on"},
    )

    assert created.status_code == 302
    assert ExclusionRule.objects.get(user=user).normalized_pattern == "c sharp"
    assert JobMatch.objects.filter(profile=profile, job=job).exists() is False
    assert duplicate.status_code == 200
    assert ExclusionRule.objects.filter(user=user).count() == 1


@pytest.mark.django_db(transaction=True)
def test_profile_location_migration_reuses_one_legacy_place_for_shared_coordinates(
    restore_current_migration_leaf: None,
) -> None:
    previous_target = ("jobs", "0004_add_initial_employer_source_kinds")
    current_target = ("jobs", "0005_german_place_and_profile_locations")
    executor = MigrationExecutor(connection)
    executor.migrate([previous_target])
    old_apps = executor.loader.project_state([previous_target]).apps
    WorkspaceUserOld = old_apps.get_model("jobs", "WorkspaceUser")
    SearchProfileOld = old_apps.get_model("jobs", "SearchProfile")
    ProfileLocationOld = old_apps.get_model("jobs", "ProfileLocation")
    ExclusionRuleOld = old_apps.get_model("jobs", "ExclusionRule")

    user = WorkspaceUserOld.objects.create(name="Ada")
    first_profile = SearchProfileOld.objects.create(user=user, name="First")
    second_profile = SearchProfileOld.objects.create(user=user, name="Second")
    ProfileLocationOld.objects.create(
        profile=first_profile, city="Berlin", latitude=52.52, longitude=13.405, radius_km=25
    )
    ProfileLocationOld.objects.create(
        profile=second_profile, city="Berlin", latitude=52.52, longitude=13.405, radius_km=25
    )
    ExclusionRuleOld.objects.create(user=user, kind="skill", pattern="C#", is_enabled=False)
    ExclusionRuleOld.objects.create(user=user, kind="skill", pattern="c sharp", is_enabled=True)

    executor = MigrationExecutor(connection)
    executor.migrate([current_target])
    new_apps = executor.loader.project_state([current_target]).apps
    GermanPlaceNew = new_apps.get_model("jobs", "GermanPlace")
    ProfileLocationNew = new_apps.get_model("jobs", "ProfileLocation")
    ExclusionRuleNew = new_apps.get_model("jobs", "ExclusionRule")

    places = GermanPlaceNew.objects.filter(source_snapshot="legacy")
    locations = ProfileLocationNew.objects.order_by("profile_id")
    assert places.count() == 1
    assert [location.place_id for location in locations] == [places.get().pk, places.get().pk]
    rules = ExclusionRuleNew.objects.filter(user_id=user.pk, kind="skill")
    assert rules.count() == 1
    assert rules.get().is_enabled is True


@pytest.mark.django_db(transaction=True)
def test_profile_location_migration_reverse_merges_same_city_places_per_profile(
    restore_current_migration_leaf: None,
) -> None:
    previous_target = ("jobs", "0004_add_initial_employer_source_kinds")
    current_target = ("jobs", "0005_german_place_and_profile_locations")
    executor = MigrationExecutor(connection)
    executor.migrate([previous_target])
    old_apps = executor.loader.project_state([previous_target]).apps
    WorkspaceUserOld = old_apps.get_model("jobs", "WorkspaceUser")
    SearchProfileOld = old_apps.get_model("jobs", "SearchProfile")
    ProfileLocationOld = old_apps.get_model("jobs", "ProfileLocation")

    user = WorkspaceUserOld.objects.create(name="Ada")
    profile = SearchProfileOld.objects.create(user=user, name="Local")
    ProfileLocationOld.objects.create(
        profile=profile, city="Berlin", latitude=52.52, longitude=13.405, radius_km=25
    )
    ProfileLocationOld.objects.create(
        profile=profile, city="Hamburg", latitude=53.5511, longitude=9.9937, radius_km=25
    )

    executor = MigrationExecutor(connection)
    executor.migrate([current_target])
    new_apps = executor.loader.project_state([current_target]).apps
    GermanPlaceNew = new_apps.get_model("jobs", "GermanPlace")
    ProfileLocationNew = new_apps.get_model("jobs", "ProfileLocation")
    berlin_copy = GermanPlaceNew.objects.create(
        source_id="test:berlin-copy",
        name="Berlin",
        normalized_name="berlin",
        latitude=52.61,
        longitude=13.51,
        source_kind="city",
    )
    ProfileLocationNew.objects.create(
        profile_id=profile.pk,
        place=berlin_copy,
        city="Berlin",
        latitude=52.61,
        longitude=13.51,
        radius_km=60,
    )

    executor = MigrationExecutor(connection)
    executor.migrate([previous_target])
    reversed_apps = executor.loader.project_state([previous_target]).apps
    ProfileLocationReversed = reversed_apps.get_model("jobs", "ProfileLocation")

    assert ProfileLocationReversed.objects.filter(profile_id=profile.pk, city="Berlin").count() == 1
    assert (
        ProfileLocationReversed.objects.filter(profile_id=profile.pk, city="Hamburg").count() == 1
    )

    executor = MigrationExecutor(connection)
    executor.migrate([current_target])
