from collections.abc import Iterable

from jobs.matching import haversine_distance_km
from jobs.models import GermanPlace, Job, MonitoringTarget, WorkspaceUser
from jobs.places import normalize_place_text


def filter_jobs_for_user(*, user: WorkspaceUser, jobs: Iterable[Job]) -> list[Job]:
    """Return jobs selected by a user's monitoring targets in input order."""
    targets = list(MonitoringTarget.objects.filter(user=user).select_related("company", "place"))
    if not targets:
        return list(jobs)

    company_ids = {
        target.company_id
        for target in targets
        if target.kind == MonitoringTarget.Kind.COMPANY and target.company_id is not None
    }
    city_targets: list[tuple[GermanPlace, int]] = [
        (target.place, target.radius_km)
        for target in targets
        if target.kind == MonitoringTarget.Kind.CITY and target.place is not None
    ]

    return [job for job in jobs if _job_matches_target(job, company_ids, city_targets)]


def _job_matches_target(
    job: Job, company_ids: set[int], city_targets: list[tuple[GermanPlace, int]]
) -> bool:
    if job.source.company_id in company_ids:
        return True

    if job.latitude is None or job.longitude is None:
        normalized_city = normalize_place_text(job.city)
        return any(normalized_city == place.normalized_name for place, _radius_km in city_targets)

    return any(
        haversine_distance_km(job.latitude, job.longitude, place.latitude, place.longitude)
        <= radius_km
        for place, radius_km in city_targets
    )
