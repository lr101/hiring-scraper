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
    GermanPlace,
    Job,
    JobMatch,
    ProfileLocation,
    SearchProfile,
    UserJobState,
    WorkspaceUser,
)


@pytest.mark.django_db
def test_profile_location_uses_the_selected_canonical_german_place() -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    profile = SearchProfile.objects.create(user=user, name="Berlin jobs")
    berlin = GermanPlace.objects.create(
        source_id="test:2950159",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        population=3_700_000,
        source_kind=GermanPlace.SourceKind.CITY,
    )

    location = ProfileLocation.objects.create(profile=profile, place=berlin, radius_km=30)

    assert location.city == "Berlin"
    assert location.latitude == 52.52
    assert location.longitude == 13.405
    assert location.place_id == berlin.pk


@pytest.mark.django_db
def test_profile_save_parses_filters_uses_place_coordinates_and_creates_open_job_matches(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    berlin = GermanPlace.objects.create(
        source_id="test:2950159",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    job = make_job(latitude=52.53, longitude=13.4, remote_type=Job.RemoteType.ONSITE)
    client.cookies["workspace_user"] = str(user.pk)

    response = client.post(
        reverse("jobs:profile_create"),
        {
            "name": "Python jobs",
            "is_enabled": "on",
            "included_titles": " Software Engineer, software engineer ",
            "required_skill_groups": "Python, Django\nAWS, Azure",
            "preferred_skills": "Docker, Docker",
            "weight_title": "60",
            "weight_required_skills": "30",
            "weight_preferred_skills": "10",
            "weight_location": "10",
            "weight_unknown_location": "-10",
            "minimum_score": "0",
            "profile_locations-TOTAL_FORMS": "1",
            "profile_locations-INITIAL_FORMS": "0",
            "profile_locations-MIN_NUM_FORMS": "0",
            "profile_locations-MAX_NUM_FORMS": "1000",
            "profile_locations-0-place": str(berlin.pk),
            "profile_locations-0-radius_km": "25",
        },
    )

    assert response.status_code == 302
    profile = SearchProfile.objects.get(user=user)
    assert profile.included_titles == ["Software Engineer"]
    assert profile.required_skill_groups == [["Python", "Django"], ["AWS", "Azure"]]
    assert profile.preferred_skills == ["Docker"]
    assert profile.profile_locations.get().place == berlin
    assert JobMatch.objects.filter(profile=profile, job=job).exists()


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
    MigrationExecutor(connection).migrate([("jobs", "0007_dynamic_company_domains")])


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
def test_exclusion_form_normalizes_patterns_rejects_equivalent_duplicates_and_rechecks_matches(
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
def test_profile_without_a_city_requires_remote_jobs_to_be_enabled(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)

    response = client.post(
        reverse("jobs:profile_create"),
        {
            "name": "No radius",
            "weight_title": "50",
            "weight_required_skills": "30",
            "weight_preferred_skills": "10",
            "weight_location": "10",
            "weight_unknown_location": "-10",
            "minimum_score": "0",
            "profile_locations-TOTAL_FORMS": "1",
            "profile_locations-INITIAL_FORMS": "0",
            "profile_locations-MIN_NUM_FORMS": "0",
            "profile_locations-MAX_NUM_FORMS": "1000",
            "profile_locations-0-radius_km": "25",
        },
    )

    assert response.status_code == 200
    assert SearchProfile.objects.filter(user=user).exists() is False
    assert b"Add a German city radius or include remote jobs" in response.content


@pytest.mark.django_db
def test_profile_location_selector_is_bounded_and_selected_place_survives_post(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    selected = GermanPlace.objects.create(
        source_id="test:selected",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    GermanPlace.objects.bulk_create(
        [
            GermanPlace(
                source_id=f"test:other-{number}",
                name=f"Unselected locality {number}",
                normalized_name=f"unselected locality {number}",
                latitude=50.0,
                longitude=8.0,
                source_kind=GermanPlace.SourceKind.CITY,
            )
            for number in range(500)
        ]
    )
    profile = SearchProfile.objects.create(user=user, name="Existing")
    ProfileLocation.objects.create(profile=profile, place=selected, radius_km=25)
    client.cookies["workspace_user"] = str(user.pk)

    response = client.get(reverse("jobs:profile_edit", args=[profile.pk]))

    assert response.status_code == 200
    assert b"Berlin (Berlin; 52.5200, 13.4050)" in response.content
    assert b"Unselected locality 499" not in response.content
    assert len(response.content) < 100_000

    response = client.post(
        reverse("jobs:profile_edit", args=[profile.pk]),
        {
            "name": "Existing",
            "include_remote": "on",
            "weight_title": "50",
            "weight_required_skills": "30",
            "weight_preferred_skills": "10",
            "weight_location": "10",
            "weight_unknown_location": "-10",
            "minimum_score": "0",
            "profile_locations-TOTAL_FORMS": "1",
            "profile_locations-INITIAL_FORMS": "1",
            "profile_locations-MIN_NUM_FORMS": "0",
            "profile_locations-MAX_NUM_FORMS": "1000",
            "profile_locations-0-id": str(profile.profile_locations.get().pk),
            "profile_locations-0-place": str(selected.pk),
            "profile_locations-0-radius_km": "30",
        },
    )

    assert response.status_code == 302
    assert profile.profile_locations.get().place_id == selected.pk
    assert profile.profile_locations.get().radius_km == 30


@pytest.mark.django_db
def test_remote_enabled_profile_can_save_without_a_city_radius(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)

    response = client.post(
        reverse("jobs:profile_create"),
        {
            "name": "Remote only",
            "include_remote": "on",
            "weight_title": "50",
            "weight_required_skills": "30",
            "weight_preferred_skills": "10",
            "weight_location": "10",
            "weight_unknown_location": "-10",
            "minimum_score": "0",
            "profile_locations-TOTAL_FORMS": "1",
            "profile_locations-INITIAL_FORMS": "0",
            "profile_locations-MIN_NUM_FORMS": "0",
            "profile_locations-MAX_NUM_FORMS": "1000",
            "profile_locations-0-radius_km": "25",
        },
    )

    assert response.status_code == 302
    profile = SearchProfile.objects.get(user=user)
    assert profile.profile_locations.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("title_weight", [None, "40000"])
def test_profile_form_reports_an_invalid_weight_without_raising_key_error(
    client: Client, title_weight: str | None
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)

    form_data = {
        "name": "Invalid title weight",
        "include_remote": "on",
        "weight_required_skills": "30",
        "weight_preferred_skills": "10",
        "weight_location": "10",
        "weight_unknown_location": "-10",
        "minimum_score": "0",
        "profile_locations-TOTAL_FORMS": "1",
        "profile_locations-INITIAL_FORMS": "0",
        "profile_locations-MIN_NUM_FORMS": "0",
        "profile_locations-MAX_NUM_FORMS": "1000",
        "profile_locations-0-radius_km": "25",
    }
    if title_weight is not None:
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
    form_data = {
        "name": "Validated",
        "include_remote": "on",
        "weight_title": "50",
        "weight_required_skills": "30",
        "weight_preferred_skills": "10",
        "weight_location": "10",
        "weight_unknown_location": "-10",
        "minimum_score": "0",
        "profile_locations-TOTAL_FORMS": "1",
        "profile_locations-INITIAL_FORMS": "0",
        "profile_locations-MIN_NUM_FORMS": "0",
        "profile_locations-MAX_NUM_FORMS": "1000",
        "profile_locations-0-radius_km": "25",
    }
    form_data[field] = value

    response = client.post(reverse("jobs:profile_create"), form_data)

    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert SearchProfile.objects.filter(user=user).exists() is False


@pytest.mark.django_db
def test_c_sharp_exclusion_rules_normalize_consistently_deduplicate_and_remove_matches(
    client: Client,
) -> None:
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


@pytest.mark.django_db
def test_profile_form_has_a_control_to_add_another_city_radius(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    client.cookies["workspace_user"] = str(user.pk)

    response = client.get(reverse("jobs:profile_create"))

    assert response.status_code == 200
    assert b'id="add-location"' in response.content
    assert b'id="empty-location-form"' in response.content
    assert b"__prefix__" in response.content


@pytest.mark.django_db
def test_profile_form_add_row_contract_saves_three_city_radii(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    places = [
        GermanPlace.objects.create(
            source_id=f"test:{number}",
            name=name,
            normalized_name=name.casefold(),
            admin_area=admin_area,
            latitude=latitude,
            longitude=longitude,
            source_kind=GermanPlace.SourceKind.CITY,
        )
        for number, name, admin_area, latitude, longitude in (
            (1, "Berlin", "Berlin", 52.52, 13.405),
            (2, "Hamburg", "Hamburg", 53.5511, 9.9937),
            (3, "München", "Bayern", 48.1351, 11.582),
        )
    ]
    client.cookies["workspace_user"] = str(user.pk)
    form_data = {
        "name": "Three cities",
        "is_enabled": "on",
        "weight_title": "50",
        "weight_required_skills": "30",
        "weight_preferred_skills": "10",
        "weight_location": "10",
        "weight_unknown_location": "-10",
        "minimum_score": "0",
        "profile_locations-TOTAL_FORMS": "3",
        "profile_locations-INITIAL_FORMS": "0",
        "profile_locations-MIN_NUM_FORMS": "0",
        "profile_locations-MAX_NUM_FORMS": "1000",
    }
    for index, place in enumerate(places):
        form_data[f"profile_locations-{index}-place"] = str(place.pk)
        form_data[f"profile_locations-{index}-radius_km"] = str(20 + index * 10)

    response = client.post(reverse("jobs:profile_create"), form_data)

    assert response.status_code == 302
    profile = SearchProfile.objects.get(user=user)
    assert list(
        profile.profile_locations.order_by("radius_km").values_list("place_id", "radius_km")
    ) == [(places[0].pk, 20), (places[1].pk, 30), (places[2].pk, 40)]


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
