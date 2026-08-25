from typing import Any

from django.core.management.base import BaseCommand, CommandError

from jobs.collection import collect_enabled_sources, collect_source, collector_registry
from jobs.models import CareerSource


class Command(BaseCommand):
    help = "Collect jobs from every eligible source or one selected source."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--source", type=int, help="CareerSource primary key")

    def handle(self, *args: Any, **options: Any) -> None:
        source_id = options.get("source")
        if source_id is None:
            runs = collect_enabled_sources(registry=collector_registry)
        else:
            try:
                source = CareerSource.objects.get(pk=source_id)
            except CareerSource.DoesNotExist as error:
                raise CommandError(f"Career source {source_id} does not exist.") from error
            runs = [collect_source(source=source, registry=collector_registry)]
        self.stdout.write(f"{len(runs)} source run completed")
