from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.apps import apps
from django.utils import timezone

from jobs.models import CareerSource, Company, Job, UserJobState, WorkspaceUser


def test_core_domain_models_are_registered() -> None:
    expected = {
        "WorkspaceUser",
        "Company",
        "CareerSource",
        "Job",
        "SearchProfile",
        "ProfileLocation",
        "JobMatch",
        "UserJobState",
        "ExclusionRule",
        "CrawlRun",
    }
    registered = {model.__name__ for model in apps.get_app_config("jobs").get_models()}

    assert expected <= registered


@pytest.mark.django_db
def test_job_save_keeps_first_seen_at_and_advances_last_seen_at() -> None:
    company = Company.objects.create(
        name="Example GmbH", domain="example.test", career_url="https://example.test/careers"
    )
    source = CareerSource.objects.create(
        company=company, source_url="https://example.test/careers/jobs"
    )
    created_at = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.get_current_timezone())
    seen_again_at = created_at + timedelta(hours=1)

    with patch("django.db.models.fields.timezone.now", return_value=created_at):
        job = Job.objects.create(
            source=source,
            external_id="role-1",
            canonical_url="https://example.test/careers/role-1",
            title="Engineer",
            normalized_title="engineer",
            content_hash="a" * 64,
            fingerprint="b" * 64,
        )

    with patch("django.db.models.fields.timezone.now", return_value=seen_again_at):
        job.save()

    job.refresh_from_db()

    assert job.first_seen_at == created_at
    assert job.last_seen_at == seen_again_at


@pytest.mark.django_db
def test_company_has_no_shared_notes_field() -> None:
    company_fields = {field.name for field in Company._meta.get_fields()}

    assert "notes" not in company_fields


@pytest.mark.django_db
def test_job_notes_are_private_to_each_users_state() -> None:
    ada = WorkspaceUser.objects.create(name="Ada")
    grace = WorkspaceUser.objects.create(name="Grace")
    company = Company.objects.create(
        name="Example GmbH", domain="example.test", career_url="https://example.test/careers"
    )
    source = CareerSource.objects.create(
        company=company, source_url="https://example.test/careers/jobs"
    )
    job = Job.objects.create(
        source=source,
        external_id="role-1",
        canonical_url="https://example.test/careers/role-1",
        title="Engineer",
        normalized_title="engineer",
        content_hash="a" * 64,
        fingerprint="b" * 64,
    )
    UserJobState.objects.create(user=ada, job=job, notes="Ada's private note")
    UserJobState.objects.create(user=grace, job=job, notes="Grace's private note")

    assert UserJobState.objects.get(user=ada, job=job).notes == "Ada's private note"
    assert UserJobState.objects.get(user=grace, job=job).notes == "Grace's private note"
