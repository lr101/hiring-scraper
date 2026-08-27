from celery import shared_task  # type: ignore[import-untyped]

from jobs.collection import collect_enabled_sources
from jobs.models import MonitoringTarget


@shared_task  # type: ignore[misc]
def collect_all_sources() -> list[int]:
    """Daily scheduled collection for every eligible career source."""
    return [run.id for run in collect_enabled_sources()]


@shared_task  # type: ignore[misc]
def discover_city_sources(target_id: int) -> dict[str, int | str]:
    """Find and scan companies for one saved city target outside the web request."""
    from jobs.views import _discover_city_result

    target = MonitoringTarget.objects.select_related("user", "place").get(
        pk=target_id, kind=MonitoringTarget.Kind.CITY
    )
    assert target.place is not None
    result = _discover_city_result(
        user=target.user,
        place=target.place,
        radius_km=target.radius_km,
    )
    return {
        "status": "complete" if result.is_complete else "partial",
        "added_companies": result.companies_added,
        "scanned_sources": len(result.runs),
        "new_jobs": result.new_jobs,
        "blocked_sources": result.blocked_sources,
        "unreadable_websites": result.unreadable_websites,
        "errors": len(result.errors),
    }


@shared_task  # type: ignore[misc]
def reverse_discover_sources() -> dict[str, int | str]:
    """Probe public ATS indexes and refresh every saved city target."""
    from jobs.reverse_discovery import ReverseDiscoveryService

    service = ReverseDiscoveryService()
    try:
        global_result = service.discover(city=None, collect=False)
    finally:
        service.close()

    summary: dict[str, int | str] = {
        "status": "complete" if global_result.is_complete else "partial",
        "ats_tenants": len(global_result.ats_tenants),
        "city_targets": 0,
        "added_companies": 0,
        "scanned_sources": 0,
        "new_jobs": 0,
        "blocked_sources": 0,
        "unreadable_websites": 0,
        "errors": len(global_result.errors),
    }
    target_ids = MonitoringTarget.objects.filter(kind=MonitoringTarget.Kind.CITY).values_list(
        "pk", flat=True
    )
    for target_id in target_ids:
        summary["city_targets"] = int(summary["city_targets"]) + 1
        try:
            city_result = discover_city_sources.run(target_id)
        except Exception:
            summary["errors"] = int(summary["errors"]) + 1
            continue
        if not isinstance(city_result, dict):
            summary["errors"] = int(summary["errors"]) + 1
            continue
        if city_result.get("status") != "complete":
            summary["errors"] = int(summary["errors"]) + 1
        for key in (
            "added_companies",
            "scanned_sources",
            "new_jobs",
            "blocked_sources",
            "unreadable_websites",
        ):
            summary[key] = int(summary[key]) + _result_int(city_result, key)
    if int(summary["errors"]):
        summary["status"] = "partial"
    return summary


def _result_int(result: dict[str, int | str], key: str) -> int:
    value = result.get(key, 0)
    return value if isinstance(value, int) else 0
