import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

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


def make_job() -> Job:
    company = Company.objects.create(
        name="Acme", domain="acme.test", career_url="https://acme.test/careers"
    )
    source = CareerSource.objects.create(company=company, source_url="https://acme.test/jobs")
    return Job.objects.create(
        source=source,
        external_id="1",
        canonical_url="https://acme.test/jobs/1",
        title="Python engineer",
        normalized_title="python engineer",
        content_hash="a" * 64,
        fingerprint="b" * 64,
        country_code="DE",
    )


@pytest.mark.django_db
def test_new_and_jobs_pages_show_only_selected_accounts_matches(client: Client) -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    profile = SearchProfile.objects.create(user=ada, name="Engineering")
    job = make_job()
    JobMatch.objects.create(job=job, profile=profile, score=82, explanation={})
    client.cookies["workspace_user"] = str(ada.pk)

    assert client.get(reverse("jobs:home")).status_code == 200
    response = client.get(reverse("jobs:job_list"))
    assert response.status_code == 200
    assert "Python engineer" in response.content.decode()
    client.cookies["workspace_user"] = str(grace.pk)
    assert "Python engineer" not in client.get(reverse("jobs:job_list")).content.decode()


@pytest.mark.django_db
def test_job_detail_marks_seen_and_state_form_updates_notes(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    job = make_job()
    profile = SearchProfile.objects.create(user=user, name="Engineering")
    JobMatch.objects.create(job=job, profile=profile, score=70, explanation={})
    client.cookies["workspace_user"] = str(user.pk)

    response = client.get(reverse("jobs:job_detail", args=[job.pk]))
    assert response.status_code == 200
    assert UserJobState.objects.get(user=user, job=job).seen_at is not None
    response = client.post(
        reverse("jobs:job_state", args=[job.pk]),
        {"status": "saved", "notes": "Call hiring manager"},
    )
    assert response.status_code == 302
    state = UserJobState.objects.get(user=user, job=job)
    assert state.status == UserJobState.Status.SAVED
    assert state.notes == "Call hiring manager"


@pytest.mark.django_db
def test_job_detail_is_safe_and_unique_when_job_matches_several_profiles(client: Client) -> None:
    user = WorkspaceUser.objects.create(name="Ada")
    job = make_job()
    for name in ("Engineering", "Backend"):
        profile = SearchProfile.objects.create(user=user, name=name)
        JobMatch.objects.create(job=job, profile=profile, score=70, explanation={})
    job.description_html = '<p>Good</p><script>alert("no")</script>'
    job.description_text = 'Good alert("no")'
    job.save(update_fields=["description_html", "description_text"])
    client.cookies["workspace_user"] = str(user.pk)

    response = client.get(reverse("jobs:job_detail", args=[job.pk]))

    assert response.status_code == 200
    assert '<script>alert("no")</script>' not in response.content.decode()


@pytest.mark.django_db
def test_blocked_source_cannot_be_manually_run(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceUser.objects.create(name="Ada")
    source = make_job().source
    source.blocked_at = timezone.now()
    source.save(update_fields=["blocked_at"])
    called = False

    def fail_if_called(**_: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("jobs.views.collect_source", fail_if_called)
    client.post(reverse("jobs:source_run", args=[source.pk]))

    assert called is False


@pytest.mark.django_db
def test_source_toggle_and_unblock_are_scoped_to_source(client: Client) -> None:
    WorkspaceUser.objects.create(name="Ada")
    job = make_job()
    source = job.source
    source.blocked_at = timezone.now()
    source.save(update_fields=["blocked_at"])
    response = client.post(reverse("jobs:source_toggle", args=[source.pk]))
    assert response.status_code == 302
    source.refresh_from_db()
    assert source.is_enabled is False
    client.post(reverse("jobs:source_unblock", args=[source.pk]))
    source.refresh_from_db()
    assert source.blocked_at is None


@pytest.mark.django_db
def test_company_and_run_pages_show_related_data(client: Client) -> None:
    WorkspaceUser.objects.create(name="Ada")
    job = make_job()
    CrawlRun.objects.create(source=job.source, status=CrawlRun.Status.SUCCESS, jobs_seen=1)
    assert client.get(reverse("jobs:company_list")).status_code == 200
    assert (
        "Acme"
        in client.get(reverse("jobs:company_detail", args=[job.source.company.pk])).content.decode()
    )
    assert client.get(reverse("jobs:source_list")).status_code == 200
    assert client.get(reverse("jobs:run_list")).status_code == 200
