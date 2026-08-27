from celery import shared_task  # type: ignore[import-untyped]

from jobs.collection import collect_enabled_sources
from jobs.models import CrawlRun, MonitoringTarget


@shared_task  # type: ignore[misc]
def collect_all_sources() -> list[int]:
    """Daily scheduled collection for every eligible career source."""
    return [run.id for run in collect_enabled_sources()]


@shared_task  # type: ignore[misc]
def discover_city_sources(target_id: int) -> dict[str, int | str]:
    """Find and scan companies for one saved city target outside the web request."""
    from jobs.views import _discover_and_scan_city

    target = MonitoringTarget.objects.select_related("user", "place").get(
        pk=target_id, kind=MonitoringTarget.Kind.CITY
    )
    assert target.place is not None
    added_companies, scans, unreadable_websites = _discover_and_scan_city(
        user=target.user,
        place=target.place,
        radius_km=target.radius_km,
    )
    return {
        "status": "complete",
        "added_companies": added_companies,
        "scanned_sources": len(scans),
        "new_jobs": sum(run.jobs_created for run in scans if run.status == CrawlRun.Status.SUCCESS),
        "blocked_sources": sum(run.status == CrawlRun.Status.BLOCKED for run in scans),
        "unreadable_websites": unreadable_websites,
    }
