from celery import shared_task  # type: ignore[import-untyped]

from jobs.collection import collect_enabled_sources


@shared_task  # type: ignore[misc]
def collect_all_sources() -> list[int]:
    """Daily scheduled collection for every eligible career source."""
    return [run.id for run in collect_enabled_sources()]
