from typing import Any

from django.core.management.base import BaseCommand, CommandError

from jobs.ba_discovery import EmployerDiscoveryService


class Command(BaseCommand):
    help = "Discover German employers with active Bundesagentur für Arbeit job signals."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("city", help="German city or place used for the BA job search")
        parser.add_argument("--radius-km", type=int, default=25)
        parser.add_argument("--publication-age-days", type=int, default=30)
        parser.add_argument("--offer-type", type=int, default=1)
        parser.add_argument("--include-temporary-agencies", action="store_true")
        parser.add_argument("--max-pages", type=int)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        service = EmployerDiscoveryService()
        try:
            result = service.discover_and_persist(
                city=options["city"],
                radius_km=options["radius_km"],
                publication_age_days=options["publication_age_days"],
                offer_type=options["offer_type"],
                include_temporary_agencies=options["include_temporary_agencies"],
                max_pages=options.get("max_pages"),
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        finally:
            service.close()
        if result.error is not None:
            raise CommandError(result.error)
        status = "complete" if result.is_complete else "partial"
        self.stdout.write(
            f"{len(result.employers)} employers discovered, "
            f"{result.total_active_jobs} active jobs ({status})"
        )
