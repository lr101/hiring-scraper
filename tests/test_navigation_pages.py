import pytest
from django.test import Client
from django.urls import reverse

from jobs.models import (
    CareerSource,
    Company,
    GermanPlace,
    Job,
    JobMatch,
    MonitoringTarget,
    SearchProfile,
    UserJobState,
    WorkspaceUser,
)
from jobs.reverse_discovery import ReverseDiscoveryResult


def make_company(name: str) -> Company:
    slug = name.lower().replace(" ", "-")
    return Company.objects.create(
        name=name,
        domain=f"{slug}.test",
        career_url=f"https://{slug}.test/careers",
    )


def make_job(
    *, company: Company, external_id: str, city: str = "Berlin", **overrides: object
) -> Job:
    source = CareerSource.objects.create(
        company=company,
        source_url=f"https://{company.domain}/jobs/{external_id}",
    )
    defaults: dict[str, object] = {
        "source": source,
        "external_id": external_id,
        "canonical_url": f"https://{company.domain}/jobs/{external_id}",
        "title": f"{company.name} engineer",
        "normalized_title": f"{company.name.lower()} engineer",
        "city": city,
        "remote_type": Job.RemoteType.HYBRID,
        "content_hash": f"{external_id:0>64}"[-64:],
        "fingerprint": f"{external_id:0>64}"[-64:],
    }
    defaults.update(overrides)
    return Job.objects.create(**defaults)


def select_account(client: Client, user: WorkspaceUser) -> None:
    client.cookies["workspace_user"] = str(user.pk)


@pytest.mark.django_db
def test_product_routes_use_the_three_item_primary_navigation(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    for route_name in ("home", "feed", "setup", "profile"):
        response = client.get(reverse(f"jobs:{route_name}"))

        assert response.status_code == 200

    response = client.get(reverse("jobs:feed"))
    navigation = response.content.split(b'<nav aria-label="Main navigation">', 1)[1].split(
        b"</nav>", 1
    )[0]
    assert b">Feed<" in navigation
    assert b">Job profiles<" in navigation
    assert b">Where to look<" in navigation
    for legacy_label in (b">New<", b">Jobs<", b">Companies<", b">Sources<", b">Runs<"):
        assert legacy_label not in navigation


@pytest.mark.django_db
def test_pages_show_only_the_selected_accounts_data(client: Client) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    ada_company = make_company("Ada company")
    grace_company = make_company("Grace company")
    ada_job = make_job(company=ada_company, external_id="1")
    grace_job = make_job(company=grace_company, external_id="2")
    ada_profile = SearchProfile.objects.create(user=ada, name="Ada profile")
    grace_profile = SearchProfile.objects.create(user=grace, name="Grace profile")
    JobMatch.objects.create(job=ada_job, profile=ada_profile, score=88, explanation={})
    JobMatch.objects.create(job=grace_job, profile=grace_profile, score=99, explanation={})
    UserJobState.objects.create(user=ada, job=ada_job, status=UserJobState.Status.SAVED)
    UserJobState.objects.create(user=grace, job=grace_job, status=UserJobState.Status.APPLIED)
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
    select_account(client, ada)

    for route_name in ("feed", "setup", "profile"):
        response = client.get(reverse(f"jobs:{route_name}"))
        content = response.content.decode()

        assert "Grace profile" not in content
        assert "Grace company engineer" not in content
        if route_name == "setup":
            assert [target.user_id for target in response.context["company_targets"]] == [ada.pk]
        else:
            assert "Ada company" in content


@pytest.mark.django_db
def test_setup_creates_and_removes_company_and_city_targets_for_active_account(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    company = make_company("Target company")
    place = GermanPlace.objects.create(
        source_id="test:berlin",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    grace_target = MonitoringTarget.objects.create(
        user=grace,
        kind=MonitoringTarget.Kind.COMPANY,
        company=company,
    )
    select_account(client, ada)
    monkeypatch.setattr("jobs.views._run_initial_scan", lambda _source: None)
    monkeypatch.setattr(
        "jobs.views._discover_city_result", lambda **kwargs: ReverseDiscoveryResult()
    )

    company_response = client.post(
        reverse("jobs:company_target_create"), {"domain": company.domain}
    )
    city_response = client.post(
        reverse("jobs:city_target_create"), {"place": place.pk, "radius_km": 35}
    )

    assert company_response.status_code == 302
    assert city_response.status_code == 302
    company_target = MonitoringTarget.objects.get(user=ada, company=company)
    city_target = MonitoringTarget.objects.get(user=ada, place=place)
    assert city_target.radius_km == 35
    assert b"Find companies again" in client.get(reverse("jobs:setup")).content
    assert (
        client.post(reverse("jobs:monitoring_target_delete", args=[grace_target.pk])).status_code
        == 404
    )
    removal = client.post(reverse("jobs:monitoring_target_delete", args=[company_target.pk]))

    assert removal.status_code == 302
    assert MonitoringTarget.objects.filter(pk=company_target.pk).exists() is False
    assert MonitoringTarget.objects.filter(pk=city_target.pk, user=ada).exists()


@pytest.mark.django_db
def test_setup_renders_a_bound_company_form_when_a_duplicate_target_is_submitted(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    company = make_company("Target company")
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.COMPANY,
        company=company,
    )
    select_account(client, user)

    response = client.post(reverse("jobs:company_target_create"), {"domain": company.domain})

    assert response.status_code == 200
    assert b"already monitored" in response.content
    assert f'value="{company.domain}"'.encode() in response.content


@pytest.mark.django_db
def test_setup_renders_a_bound_city_form_when_the_radius_is_invalid(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = GermanPlace.objects.create(
        source_id="test:berlin",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    select_account(client, user)

    response = client.post(
        reverse("jobs:city_target_create"),
        {"place": place.pk, "place_query": "Berlin", "radius_km": 0},
    )

    assert response.status_code == 200
    assert b"Ensure this value is greater than or equal to 1." in response.content
    assert b'value="Berlin"' in response.content
    assert b'value="0"' in response.content
    assert MonitoringTarget.objects.filter(user=user).exists() is False


@pytest.mark.django_db
def test_setup_shows_an_invalid_city_selection_error_and_retains_the_query(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    response = client.post(
        reverse("jobs:city_target_create"),
        {"place": 999_999, "place_query": "Unknown", "radius_km": 25},
    )

    assert response.status_code == 200
    assert b"Select a valid choice." in response.content
    assert b'value="Unknown"' in response.content
    assert MonitoringTarget.objects.filter(user=user).exists() is False


@pytest.mark.django_db
def test_feed_applies_targets_and_hides_jobs_ignored_by_active_account(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    selected_company = make_company("Selected company")
    other_company = make_company("Other company")
    selected_job = make_job(company=selected_company, external_id="1", city="Munich")
    ignored_job = make_job(company=selected_company, external_id="2", city="Munich")
    other_job = make_job(company=other_company, external_id="3")
    profile = SearchProfile.objects.create(user=user, name="Engineering")
    JobMatch.objects.create(job=selected_job, profile=profile, score=81, explanation={})
    JobMatch.objects.create(job=ignored_job, profile=profile, score=91, explanation={})
    JobMatch.objects.create(job=other_job, profile=profile, score=99, explanation={})
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.COMPANY,
        company=selected_company,
    )
    UserJobState.objects.create(user=user, job=ignored_job, status=UserJobState.Status.IGNORED)
    select_account(client, user)

    response = client.get(reverse("jobs:feed"))
    content = response.content.decode()

    assert "Selected company engineer" in content
    assert "Other company engineer" not in content
    assert content.count("Selected company engineer") == 1
    assert "81 points" in content
    assert "View job" in content


@pytest.mark.django_db
def test_feed_keeps_a_job_when_only_another_account_ignored_it(client: Client) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    job = make_job(company=make_company("Shared company"), external_id="1")
    profile = SearchProfile.objects.create(user=ada, name="Engineering")
    JobMatch.objects.create(job=job, profile=profile, score=81, explanation={})
    MonitoringTarget.objects.create(
        user=ada,
        kind=MonitoringTarget.Kind.COMPANY,
        company=job.source.company,
    )
    UserJobState.objects.create(user=ada, job=job, status=UserJobState.Status.SAVED)
    UserJobState.objects.create(user=grace, job=job, status=UserJobState.Status.IGNORED)
    select_account(client, ada)

    response = client.get(reverse("jobs:feed"))

    assert "Shared company engineer" in response.content.decode()


@pytest.mark.django_db
def test_feed_applies_city_radius_targets(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    berlin = GermanPlace.objects.create(
        source_id="test:berlin",
        name="Berlin",
        normalized_name="berlin",
        admin_area="Berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    near_job = make_job(
        company=make_company("Near company"),
        external_id="1",
        title="Near job",
        latitude=52.53,
        longitude=13.4,
    )
    distant_job = make_job(
        company=make_company("Distant company"),
        external_id="2",
        title="Distant job",
        latitude=53.5511,
        longitude=9.9937,
    )
    profile = SearchProfile.objects.create(user=user, name="Engineering")
    JobMatch.objects.create(job=near_job, profile=profile, score=81, explanation={})
    JobMatch.objects.create(job=distant_job, profile=profile, score=91, explanation={})
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=berlin,
        radius_km=25,
    )
    select_account(client, user)

    response = client.get(reverse("jobs:feed"))
    content = response.content.decode()

    assert "Near job" in content
    assert "Distant job" not in content


@pytest.mark.django_db
def test_feed_normalizes_city_names_without_coordinates(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    munich = GermanPlace.objects.create(
        source_id="test:munich",
        name="München",
        normalized_name="munchen",
        admin_area="Bavaria",
        latitude=48.1372,
        longitude=11.5756,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    matching_job = make_job(
        company=make_company("Munich company"),
        external_id="1",
        title="Normalized city job",
        city="Munchen",
    )
    other_job = make_job(
        company=make_company("Berlin company"),
        external_id="2",
        title="Different city job",
        city="Berlin",
    )
    profile = SearchProfile.objects.create(user=user, name="Engineering")
    JobMatch.objects.create(job=matching_job, profile=profile, score=81, explanation={})
    JobMatch.objects.create(job=other_job, profile=profile, score=91, explanation={})
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=munich,
        radius_km=25,
    )
    select_account(client, user)

    response = client.get(reverse("jobs:feed"))
    content = response.content.decode()

    assert "Normalized city job" in content
    assert "Different city job" not in content


@pytest.mark.django_db
def test_feed_shows_setup_prompt_without_selected_sources(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    first_job = make_job(
        company=make_company("First company"), external_id="1", title="First fallback job"
    )
    second_job = make_job(
        company=make_company("Second company"), external_id="2", title="Second fallback job"
    )
    profile = SearchProfile.objects.create(user=user, name="Engineering")
    JobMatch.objects.create(job=first_job, profile=profile, score=81, explanation={})
    JobMatch.objects.create(job=second_job, profile=profile, score=91, explanation={})
    select_account(client, user)

    response = client.get(reverse("jobs:feed"))

    content = response.content.decode()
    assert "First fallback job" not in content
    assert "Second fallback job" not in content
    assert "Choose where to look" in content


@pytest.mark.django_db
def test_feed_explains_when_a_city_has_no_company_sources(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    place = GermanPlace.objects.create(
        source_id="test:berlin",
        name="Berlin",
        normalized_name="berlin",
        latitude=52.52,
        longitude=13.405,
        source_kind=GermanPlace.SourceKind.CITY,
    )
    SearchProfile.objects.create(user=user, name="Engineering")
    MonitoringTarget.objects.create(
        user=user,
        kind=MonitoringTarget.Kind.CITY,
        place=place,
        radius_km=25,
    )
    select_account(client, user)

    response = client.get(reverse("jobs:feed"))

    content = response.content.decode()
    assert "No company sources yet" in content
    assert "Find companies again" in content


@pytest.mark.django_db
def test_profile_filters_and_sorts_mixed_active_account_workflow_statuses(client: Client) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    zeta_job = make_job(company=make_company("Zeta"), external_id="1", title="Zeta saved job")
    alpha_job = make_job(company=make_company("Alpha"), external_id="2", title="Alpha saved job")
    applied_job = make_job(company=make_company("Beta"), external_id="3", title="Beta applied job")
    grace_job = make_job(company=make_company("Grace company"), external_id="4")
    UserJobState.objects.create(user=ada, job=zeta_job, status=UserJobState.Status.SAVED)
    UserJobState.objects.create(user=ada, job=alpha_job, status=UserJobState.Status.SAVED)
    UserJobState.objects.create(user=ada, job=applied_job, status=UserJobState.Status.APPLIED)
    UserJobState.objects.create(user=grace, job=grace_job, status=UserJobState.Status.SAVED)
    select_account(client, ada)

    all_statuses = client.get(reverse("jobs:profile"), {"sort": "company"})
    response = client.get(reverse("jobs:profile"), {"status": "saved", "sort": "company"})
    content = response.content.decode()

    assert "Beta applied job" in all_statuses.content.decode()
    assert "Grace company engineer" not in content
    assert "Beta applied job" not in content
    assert content.index("Alpha saved job") < content.index("Zeta saved job")
    assert b'name="status"' in response.content
    assert b'name="sort"' in response.content


@pytest.mark.django_db
def test_profile_workflow_link_opens_a_saved_job_without_a_current_match(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    job = make_job(company=make_company("Saved company"), external_id="1")
    UserJobState.objects.create(user=user, job=job, status=UserJobState.Status.SAVED)
    select_account(client, user)

    profile = client.get(reverse("jobs:profile"))
    detail = client.get(reverse("jobs:job_detail", args=[job.pk]))
    update = client.post(
        reverse("jobs:job_state", args=[job.pk]),
        {"status": UserJobState.Status.APPLIED, "notes": "Submitted"},
    )

    assert "Saved company engineer" in profile.content.decode()
    assert detail.status_code == 200
    assert update.status_code == 302
    assert UserJobState.objects.get(user=user, job=job).status == UserJobState.Status.APPLIED


@pytest.mark.django_db
def test_profile_keeps_the_legacy_jobs_tool_as_a_secondary_link(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    response = client.get(reverse("jobs:profile"))

    assert b'<a href="/jobs/">Jobs</a>' in response.content
