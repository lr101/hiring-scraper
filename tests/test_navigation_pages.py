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


def make_company(name: str) -> Company:
    slug = name.lower().replace(" ", "-")
    return Company.objects.create(
        name=name,
        domain=f"{slug}.test",
        career_url=f"https://{slug}.test/careers",
    )


def make_job(*, company: Company, external_id: str, city: str = "Berlin") -> Job:
    source = CareerSource.objects.create(
        company=company,
        source_url=f"https://{company.domain}/jobs/{external_id}",
    )
    return Job.objects.create(
        source=source,
        external_id=external_id,
        canonical_url=f"https://{company.domain}/jobs/{external_id}",
        title=f"{company.name} engineer",
        normalized_title=f"{company.name.lower()} engineer",
        city=city,
        remote_type=Job.RemoteType.HYBRID,
        content_hash=f"{external_id:0>64}"[-64:],
        fingerprint=f"{external_id:0>64}"[-64:],
    )


def select_account(client: Client, user: WorkspaceUser) -> None:
    client.cookies["workspace_user"] = str(user.pk)


@pytest.mark.django_db
def test_feed_search_and_profile_routes_use_the_three_item_primary_navigation(
    client: Client,
) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    select_account(client, user)

    for route_name in ("home", "feed", "search", "profile"):
        response = client.get(reverse(f"jobs:{route_name}"))

        assert response.status_code == 200

    response = client.get(reverse("jobs:feed"))
    navigation = response.content.split(b'<nav aria-label="Main navigation">', 1)[1].split(
        b"</nav>", 1
    )[0]
    assert b">Feed<" in navigation
    assert b">Search<" in navigation
    assert b">Profile<" in navigation
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

    for route_name in ("feed", "search", "profile"):
        response = client.get(reverse(f"jobs:{route_name}"))
        content = response.content.decode()

        assert "Grace profile" not in content
        assert "Grace company engineer" not in content
        if route_name == "search":
            assert [target.user_id for target in response.context["company_targets"]] == [ada.pk]
        else:
            assert "Ada company" in content


@pytest.mark.django_db
def test_search_creates_and_removes_company_and_city_targets_for_active_account(
    client: Client,
) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    company = make_company("Target company")
    place = GermanPlace.objects.create(
        source_id="geonames:berlin",
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

    company_response = client.post(reverse("jobs:company_target_create"), {"company": company.pk})
    city_response = client.post(
        reverse("jobs:city_target_create"), {"place": place.pk, "radius_km": 35}
    )

    assert company_response.status_code == 302
    assert city_response.status_code == 302
    company_target = MonitoringTarget.objects.get(user=ada, company=company)
    city_target = MonitoringTarget.objects.get(user=ada, place=place)
    assert city_target.radius_km == 35
    assert (
        client.post(reverse("jobs:monitoring_target_delete", args=[grace_target.pk])).status_code
        == 404
    )
    removal = client.post(reverse("jobs:monitoring_target_delete", args=[company_target.pk]))

    assert removal.status_code == 302
    assert MonitoringTarget.objects.filter(pk=company_target.pk).exists() is False
    assert MonitoringTarget.objects.filter(pk=city_target.pk, user=ada).exists()


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
def test_profile_filters_and_sorts_active_accounts_workflow_jobs(client: Client) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    zeta_job = make_job(company=make_company("Zeta"), external_id="1")
    alpha_job = make_job(company=make_company("Alpha"), external_id="2")
    grace_job = make_job(company=make_company("Grace company"), external_id="3")
    UserJobState.objects.create(user=ada, job=zeta_job, status=UserJobState.Status.SAVED)
    UserJobState.objects.create(user=ada, job=alpha_job, status=UserJobState.Status.SAVED)
    UserJobState.objects.create(user=grace, job=grace_job, status=UserJobState.Status.SAVED)
    select_account(client, ada)

    response = client.get(reverse("jobs:profile"), {"status": "saved", "sort": "company"})
    content = response.content.decode()

    assert "Grace company engineer" not in content
    assert content.index("Alpha engineer") < content.index("Zeta engineer")
    assert b'name="status"' in response.content
    assert b'name="sort"' in response.content
