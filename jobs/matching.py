from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from jobs.models import Job, JobMatch, ProfileLocation, SearchProfile

EARTH_RADIUS_KM = 6371.0088
MIN_JOB_MATCH_SCORE = -32_768
MAX_JOB_MATCH_SCORE = 32_767
DEFAULT_WEIGHTS = {
    "title": 50,
    "required_skills": 30,
    "preferred_skills": 10,
    "location": 10,
    "unknown_location": -10,
}

TITLE_SYNONYMS = {
    "backend engineer": {"backend developer", "backend entwickler", "backend engineer"},
    "data engineer": {"data engineer", "dateningenieur", "daten engineer"},
    "data scientist": {"data scientist", "datenwissenschaftler"},
    "devops engineer": {"devops engineer", "devops entwickler", "devops ingenieur"},
    "frontend engineer": {"frontend developer", "frontend entwickler", "frontend engineer"},
    "machine learning engineer": {
        "machine learning engineer",
        "maschinenlern ingenieur",
        "ml engineer",
    },
    "product manager": {"product manager", "produktmanager"},
    "software engineer": {
        "software developer",
        "software engineer",
        "software entwickler",
        "softwareentwickler",
    },
}

SKILL_SYNONYMS = {
    "ai": {"ai", "artificial intelligence", "kunstliche intelligenz"},
    "aws": {"amazon web services", "aws"},
    "azure": {"azure", "microsoft azure"},
    "c sharp": {"c sharp", "c#"},
    "gcp": {"gcp", "google cloud platform"},
    "javascript": {"javascript", "js"},
    "kubernetes": {"k8s", "kubernetes"},
    "machine learning": {"machine learning", "ml"},
    "postgresql": {"postgres", "postgresql"},
    "python": {"python", "python3"},
    "typescript": {"ts", "typescript"},
}

GERMAN_LEVELS = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}


@dataclass(frozen=True)
class MatchEvaluation:
    is_match: bool
    score: int
    explanation: dict[str, Any]
    reason: str | None = None


def normalize_text(value: str) -> str:
    """Return a comparison-safe form for titles, skills, and metadata."""
    value = value.casefold().replace("c#", "c sharp").replace("c++", "cplusplus")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def haversine_distance_km(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Return the great-circle distance between two WGS84 coordinates in kilometres."""
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    latitude_a_radians = math.radians(latitude_a)
    latitude_b_radians = math.radians(latitude_b)
    half_chord = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_a_radians)
        * math.cos(latitude_b_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(half_chord))


def evaluate_job(*, job: Job, profile: SearchProfile) -> MatchEvaluation:
    """Evaluate a job deterministically without changing persisted matches."""
    rejected = _hard_filter_result(job=job, profile=profile)
    if rejected is not None:
        return rejected

    weights = _weights_for(profile)
    title_terms = _normalized_list(profile.included_titles)
    title_matches = _matching_terms(job.title, title_terms, TITLE_SYNONYMS)
    title_score = weights["title"] if title_matches else 0

    job_skills = {
        _canonical_term(skill, SKILL_SYNONYMS) for skill in job.skills if isinstance(skill, str)
    }
    required_groups = _required_groups(profile.required_skill_groups)
    matched_required_groups = [
        matched_skill
        for group in required_groups
        if (matched_skill := _matching_skill_in_group(job_skills, group)) is not None
    ]
    required_score = weights["required_skills"] if required_groups else 0

    preferred_skills = _normalized_list(profile.preferred_skills)
    preferred_matches = [
        skill for skill in preferred_skills if _canonical_term(skill, SKILL_SYNONYMS) in job_skills
    ]
    preferred_score = _proportional_score(
        weight=weights["preferred_skills"],
        matched=len(preferred_matches),
        available=len(preferred_skills),
    )

    location_score, location_explanation = _location_score(
        job=job, profile=profile, weights=weights
    )
    score = _clamp_score(title_score + required_score + preferred_score + location_score)
    explanation: dict[str, Any] = {
        "title": {"matched_terms": title_matches, "score": title_score},
        "skills": {
            "required_groups": matched_required_groups,
            "preferred_matches": preferred_matches,
            "score": required_score + preferred_score,
        },
        "location": location_explanation,
        "score": score,
    }
    salary_explanation = _salary_explanation(job=job, profile=profile)
    if salary_explanation is not None:
        explanation["salary"] = salary_explanation
    if score < profile.minimum_score and location_explanation["status"] != "unknown":
        return MatchEvaluation(
            is_match=False,
            score=score,
            explanation=explanation,
            reason="score is below profile minimum",
        )
    return MatchEvaluation(is_match=True, score=score, explanation=explanation)


def update_job_match(*, job: Job, profile: SearchProfile) -> JobMatch | None:
    """Create or refresh the single current match for a job and profile."""
    evaluation = evaluate_job(job=job, profile=profile)
    if not evaluation.is_match:
        JobMatch.objects.filter(job=job, profile=profile).delete()
        return None
    match, _ = JobMatch.objects.update_or_create(
        job=job,
        profile=profile,
        defaults={"score": evaluation.score, "explanation": evaluation.explanation},
    )
    return match


def _hard_filter_result(*, job: Job, profile: SearchProfile) -> MatchEvaluation | None:
    if not profile.is_enabled:
        return _rejected("profile is disabled")
    if not job.is_open:
        return _rejected("job is closed")
    if normalize_text(job.country_code) != "de":
        return _rejected("job is outside Germany")

    title = normalize_text(job.title)
    included_titles = _normalized_list(profile.included_titles)
    if included_titles and not _matching_terms(title, included_titles, TITLE_SYNONYMS):
        return _rejected("title does not match")
    if _matching_terms(title, _normalized_list(profile.excluded_titles), TITLE_SYNONYMS):
        return _rejected("title is excluded")

    job_skills = {
        _canonical_term(skill, SKILL_SYNONYMS) for skill in job.skills if isinstance(skill, str)
    }
    required_groups = _required_groups(profile.required_skill_groups)
    if any(not _skill_group_matches(job_skills, group) for group in required_groups):
        return _rejected("missing required skills")
    excluded_skills = {_canonical_term(skill, SKILL_SYNONYMS) for skill in profile.excluded_skills}
    if job_skills & excluded_skills:
        return _rejected("excluded skill matched")

    if _nonempty_value_is_not_allowed(job.employment_type, profile.employment_types):
        return _rejected("employment type does not match")
    if _salary_is_below_profile_minimum(job=job, profile=profile):
        return _rejected("salary is below profile minimum")
    if _german_requirement_exceeds(job.language_requirement, profile.maximum_german_level):
        return _rejected("German requirement exceeds profile maximum")
    if _nonempty_value_is_not_allowed(job.department, profile.departments):
        return _rejected("department does not match")
    if _nonempty_value_is_not_allowed(job.industry, profile.industries):
        return _rejected("industry does not match")
    if _nonempty_value_is_not_allowed(job.seniority, profile.seniority_levels):
        return _rejected("seniority does not match")

    if job.remote_type == Job.RemoteType.REMOTE and not profile.include_remote:
        return _rejected("remote work is disabled")
    if job.remote_type != Job.RemoteType.REMOTE and _has_known_location(job):
        if not _within_any_profile_radius(job=job, profile=profile):
            return _rejected("outside every configured city radius")
    return None


def _location_score(
    *, job: Job, profile: SearchProfile, weights: dict[str, int]
) -> tuple[int, dict[str, object]]:
    if job.remote_type == Job.RemoteType.REMOTE:
        return weights["location"], {"status": "remote", "score": weights["location"]}
    if not _has_known_location(job):
        return weights["unknown_location"], {
            "status": "unknown",
            "score": weights["unknown_location"],
        }
    matching_location = _matching_profile_location(job=job, profile=profile)
    if matching_location is None:
        raise ValueError("A known job location within a profile radius is required")
    nearest_location, distance_km = matching_location
    return weights["location"], {
        "city": nearest_location.city,
        "distance_km": round(distance_km, 1),
        "status": "within_radius",
        "score": weights["location"],
    }


def _weights_for(profile: SearchProfile) -> dict[str, int]:
    weights = DEFAULT_WEIGHTS.copy()
    profile_weights = profile.weights
    if not isinstance(profile_weights, dict):
        return weights
    for key, value in profile_weights.items():
        if (
            key in weights
            and isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_JOB_MATCH_SCORE <= value <= MAX_JOB_MATCH_SCORE
        ):
            weights[key] = value
    return weights


def _clamp_score(score: int) -> int:
    return max(MIN_JOB_MATCH_SCORE, min(MAX_JOB_MATCH_SCORE, score))


def _salary_is_below_profile_minimum(*, job: Job, profile: SearchProfile) -> bool:
    return (
        job.salary_max is not None
        and profile.minimum_salary is not None
        and _salary_currency_is_eur(job)
        and job.salary_max < Decimal(profile.minimum_salary)
    )


def _salary_explanation(*, job: Job, profile: SearchProfile) -> dict[str, str] | None:
    if profile.minimum_salary is None or job.salary_max is None or _salary_currency_is_eur(job):
        return None
    return {"status": "unavailable", "currency": job.salary_currency}


def _salary_currency_is_eur(job: Job) -> bool:
    return normalize_text(job.salary_currency) == "eur"


def _normalized_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        normalize_text(value)
        for value in values
        if isinstance(value, str) and normalize_text(value)
    ]


def _required_groups(values: object) -> list[list[str]]:
    if not isinstance(values, list):
        return []
    groups: list[list[str]] = []
    for group in values:
        if isinstance(group, str):
            normalized_group = _normalized_list([group])
        else:
            normalized_group = _normalized_list(group)
        if normalized_group:
            groups.append(normalized_group)
    return groups


def _canonical_term(term: str, synonyms: dict[str, set[str]]) -> str:
    normalized = normalize_text(term)
    for canonical, aliases in synonyms.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized


def _matching_terms(text: str, terms: list[str], synonyms: dict[str, set[str]]) -> list[str]:
    normalized_text = normalize_text(text)
    matches: list[str] = []
    for term in terms:
        canonical = _canonical_term(term, synonyms)
        aliases = synonyms.get(canonical, {canonical})
        if any(_contains_phrase(normalized_text, alias) for alias in aliases):
            matches.append(canonical)
    return matches


def _contains_phrase(text: str, phrase: str) -> bool:
    return f" {normalize_text(phrase)} " in f" {text} "


def _skill_group_matches(job_skills: set[str], group: list[str]) -> bool:
    return _matching_skill_in_group(job_skills, group) is not None


def _matching_skill_in_group(job_skills: set[str], group: list[str]) -> str | None:
    return next(
        (
            canonical_skill
            for skill in group
            if (canonical_skill := _canonical_term(skill, SKILL_SYNONYMS)) in job_skills
        ),
        None,
    )


def _proportional_score(*, weight: int, matched: int, available: int) -> int:
    if available == 0:
        return 0
    return round(weight * matched / available)


def _nonempty_value_is_not_allowed(value: str, allowed_values: object) -> bool:
    normalized_value = normalize_text(value)
    normalized_allowed = _normalized_list(allowed_values)
    return bool(
        normalized_value and normalized_allowed and normalized_value not in normalized_allowed
    )


def _german_requirement_exceeds(requirement: str, maximum: str) -> bool:
    required_level = GERMAN_LEVELS.get(requirement.upper())
    maximum_level = GERMAN_LEVELS.get(maximum.upper())
    return (
        required_level is not None and maximum_level is not None and required_level > maximum_level
    )


def _has_known_location(job: Job) -> bool:
    return job.latitude is not None and job.longitude is not None


def _within_any_profile_radius(*, job: Job, profile: SearchProfile) -> bool:
    return _matching_profile_location(job=job, profile=profile) is not None


def _matching_profile_location(
    *, job: Job, profile: SearchProfile
) -> tuple[ProfileLocation, float] | None:
    locations = list(profile.profile_locations.all()) if profile.pk is not None else []
    if not locations or job.latitude is None or job.longitude is None:
        return None
    return min(
        (
            (
                location,
                haversine_distance_km(
                    job.latitude, job.longitude, location.latitude, location.longitude
                ),
            )
            for location in locations
            if haversine_distance_km(
                job.latitude, job.longitude, location.latitude, location.longitude
            )
            <= location.radius_km
        ),
        key=lambda candidate: candidate[1],
        default=None,
    )


def _rejected(reason: str) -> MatchEvaluation:
    return MatchEvaluation(is_match=False, score=0, explanation={"score": 0}, reason=reason)
