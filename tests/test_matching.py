from decimal import Decimal

import pytest

from jobs.matching import (
    DEFAULT_WEIGHTS,
    evaluate_job,
    haversine_distance_km,
    normalize_text,
    update_job_match,
)
from jobs.models import (
    CareerSource,
    Company,
    Job,
    JobMatch,
    ProfileLocation,
    SearchProfile,
    WorkspaceUser,
)


def test_normalize_text_and_conservative_synonyms_match_german_and_english_terms() -> None:
    assert normalize_text("  C#-Entwickler:in / München  ") == "c sharp entwickler in munchen"

    evaluation = evaluate_job(
        job=make_job(title="Softwareentwickler", skills=["Amazon Web Services", "Kubernetes"]),
        profile=make_profile(
            included_titles=["software engineer"],
            required_skill_groups=[["AWS"], ["k8s"]],
        ),
    )

    assert evaluation.is_match is True
    assert evaluation.explanation["title"]["matched_terms"] == ["software engineer"]
    assert evaluation.explanation["skills"]["required_groups"] == ["aws", "kubernetes"]


def test_haversine_distance_km_returns_known_city_distance() -> None:
    berlin_to_hamburg = haversine_distance_km(52.52, 13.405, 53.5511, 9.9937)

    assert berlin_to_hamburg == pytest.approx(255.3, abs=0.5)


def test_required_skill_group_explains_the_matching_alternative() -> None:
    evaluation = evaluate_job(
        job=make_job(skills=["Azure"]),
        profile=make_profile(required_skill_groups=[["AWS", "azure"]]),
    )

    assert evaluation.explanation["skills"]["required_groups"] == ["azure"]


@pytest.mark.django_db
def test_remote_jobs_require_remote_enabled_profile_but_ignore_city_radius() -> None:
    profile = make_saved_profile(include_remote=True)
    ProfileLocation.objects.create(
        profile=profile, city="Berlin", latitude=52.52, longitude=13.405, radius_km=10
    )

    evaluation = evaluate_job(
        job=make_job(remote_type=Job.RemoteType.REMOTE, latitude=53.5511, longitude=9.9937),
        profile=profile,
    )

    assert evaluation.is_match is True
    assert evaluation.explanation["location"]["status"] == "remote"
    assert (
        evaluate_job(
            job=make_job(remote_type=Job.RemoteType.REMOTE),
            profile=make_profile(include_remote=False),
        ).reason
        == "remote work is disabled"
    )


@pytest.mark.django_db
def test_hybrid_and_onsite_jobs_need_nearby_location_but_keep_unknown_locations_visible() -> None:
    profile = make_saved_profile()
    ProfileLocation.objects.create(
        profile=profile, city="Berlin", latitude=52.52, longitude=13.405, radius_km=20
    )

    nearby = evaluate_job(
        job=make_job(remote_type=Job.RemoteType.HYBRID, latitude=52.55, longitude=13.4),
        profile=profile,
    )
    too_far = evaluate_job(
        job=make_job(remote_type=Job.RemoteType.ONSITE, latitude=53.5511, longitude=9.9937),
        profile=profile,
    )
    unknown = evaluate_job(job=make_job(remote_type=Job.RemoteType.ONSITE), profile=profile)
    no_radius = evaluate_job(
        job=make_job(remote_type=Job.RemoteType.ONSITE, latitude=52.55, longitude=13.4),
        profile=make_saved_profile(),
    )

    assert nearby.is_match is True
    assert too_far.reason == "outside every configured city radius"
    assert no_radius.reason == "outside every configured city radius"
    assert unknown.is_match is True
    assert unknown.score == DEFAULT_WEIGHTS["unknown_location"]
    assert unknown.explanation["location"]["status"] == "unknown"


@pytest.mark.django_db
def test_onsite_job_matches_when_any_configured_city_radius_contains_it() -> None:
    profile = make_saved_profile()
    ProfileLocation.objects.create(
        profile=profile, city="Near but too small", latitude=52.55, longitude=13.405, radius_km=2
    )
    ProfileLocation.objects.create(
        profile=profile, city="Within radius", latitude=52.7, longitude=13.405, radius_km=30
    )

    evaluation = evaluate_job(
        job=make_job(remote_type=Job.RemoteType.ONSITE, latitude=52.52, longitude=13.405),
        profile=profile,
    )

    assert evaluation.is_match is True
    assert evaluation.explanation["location"]["city"] == "Within radius"


def test_hard_filters_reject_closed_foreign_disabled_and_excluded_jobs() -> None:
    profile = make_profile(
        included_titles=["software engineer"],
        excluded_titles=["lead"],
        required_skill_groups=[["python"], ["aws", "azure"]],
        excluded_skills=["php"],
        employment_types=["full time"],
        departments=["engineering"],
        industries=["technology"],
        seniority_levels=["senior"],
        minimum_salary=90_000,
        maximum_german_level="B2",
    )
    matching_job = make_job(
        title="Senior Software Engineer",
        skills=["Python", "AWS"],
        employment_type="Full-time",
        department="Engineering",
        industry="Technology",
        seniority="Senior",
        salary_max=Decimal("95000"),
        language_requirement="B2",
    )

    assert evaluate_job(job=matching_job, profile=profile).is_match is True
    assert evaluate_job(job=make_job(title="Lead Software Engineer"), profile=profile).reason == (
        "title is excluded"
    )
    assert evaluate_job(
        job=make_job(title="Software Engineer", skills=["Python"]), profile=profile
    ).reason == ("missing required skills")
    assert (
        evaluate_job(
            job=make_job(title="Software Engineer", skills=["Python", "AWS", "PHP"]),
            profile=profile,
        ).reason
        == "excluded skill matched"
    )
    assert (
        evaluate_job(
            job=make_job(
                title="Software Engineer", skills=["Python", "AWS"], employment_type="Contract"
            ),
            profile=profile,
        ).reason
        == "employment type does not match"
    )
    assert (
        evaluate_job(
            job=make_job(
                title="Software Engineer", skills=["Python", "AWS"], salary_max=Decimal("80000")
            ),
            profile=profile,
        ).reason
        == "salary is below profile minimum"
    )
    assert (
        evaluate_job(
            job=make_job(
                title="Software Engineer", skills=["Python", "AWS"], language_requirement="C1"
            ),
            profile=profile,
        ).reason
        == "German requirement exceeds profile maximum"
    )
    assert (
        evaluate_job(
            job=make_job(title="Software Engineer", skills=["Python", "AWS"], country_code="AT"),
            profile=profile,
        ).reason
        == "job is outside Germany"
    )
    assert (
        evaluate_job(
            job=make_job(
                title="Software Engineer",
                skills=["Python", "AWS"],
                closed_at="2026-01-01T00:00:00Z",
            ),
            profile=profile,
        ).reason
        == "job is closed"
    )
    assert (
        evaluate_job(job=matching_job, profile=make_profile(is_enabled=False)).reason
        == "profile is disabled"
    )


def test_weighted_score_and_explanation_report_each_matching_component() -> None:
    evaluation = evaluate_job(
        job=make_job(
            title="Backend Developer",
            skills=["Python", "Docker"],
            remote_type=Job.RemoteType.REMOTE,
        ),
        profile=make_profile(
            included_titles=["backend engineer"],
            required_skill_groups=[["python"]],
            preferred_skills=["docker", "kubernetes"],
            weights={"title": 60, "required_skills": 20, "preferred_skills": 10, "location": 10},
        ),
    )

    assert evaluation.is_match is True
    assert evaluation.score == 95
    assert evaluation.explanation == {
        "title": {"matched_terms": ["backend engineer"], "score": 60},
        "skills": {
            "required_groups": ["python"],
            "preferred_matches": ["docker"],
            "score": 25,
        },
        "location": {"status": "remote", "score": 10},
        "score": 95,
    }


@pytest.mark.django_db
def test_update_job_match_creates_once_updates_score_and_removes_stale_match() -> None:
    profile = make_saved_profile(included_titles=["software engineer"])
    job = make_saved_job(title="Software Engineer")

    created = update_job_match(job=job, profile=profile)

    assert created is not None
    assert JobMatch.objects.get(job=job, profile=profile).score == (
        DEFAULT_WEIGHTS["title"] + DEFAULT_WEIGHTS["location"]
    )

    profile.weights = {"title": 75}
    profile.save(update_fields=["weights"])
    updated = update_job_match(job=job, profile=profile)

    assert updated is not None
    assert updated.pk == created.pk
    assert JobMatch.objects.filter(job=job, profile=profile).count() == 1
    assert updated.score == 75 + DEFAULT_WEIGHTS["location"]

    job.title = "Accountant"
    job.save(update_fields=["title"])

    assert update_job_match(job=job, profile=profile) is None
    assert JobMatch.objects.filter(job=job, profile=profile).exists() is False


def make_profile(**overrides: object) -> SearchProfile:
    defaults: dict[str, object] = {
        "name": "Backend roles",
        "include_remote": True,
    }
    defaults.update(overrides)
    return SearchProfile(**defaults)


def make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "title": "Backend Engineer",
        "country_code": "DE",
        "remote_type": Job.RemoteType.REMOTE,
        "skills": [],
    }
    defaults.update(overrides)
    return Job(**defaults)


@pytest.mark.django_db
def make_saved_profile(**overrides: object) -> SearchProfile:
    user = WorkspaceUser.objects.create(name=f"Test user {WorkspaceUser.objects.count() + 1}")
    defaults: dict[str, object] = {
        "user": user,
        "name": "Backend roles",
        "include_remote": True,
    }
    defaults.update(overrides)
    return SearchProfile.objects.create(**defaults)


def make_saved_job(**overrides: object) -> Job:
    company = Company.objects.create(
        name="Example GmbH", domain="example.com", career_url="https://example.com/careers"
    )
    source = CareerSource.objects.create(company=company, source_url="https://example.com/jobs")
    defaults: dict[str, object] = {
        "source": source,
        "external_id": "backend-engineer",
        "canonical_url": "https://example.com/jobs/backend-engineer",
        "title": "Backend Engineer",
        "normalized_title": "backend engineer",
        "country_code": "DE",
        "remote_type": Job.RemoteType.REMOTE,
        "content_hash": "a" * 64,
        "fingerprint": "b" * 64,
    }
    defaults.update(overrides)
    return Job.objects.create(**defaults)
