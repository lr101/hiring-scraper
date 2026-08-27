from collections.abc import Sized
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from jobs.reverse_discovery import (
    DEFAULT_PUBLICATION_AGE_DAYS,
    DEFAULT_RADIUS_KM,
    ReverseDiscoveryService,
)


class Command(BaseCommand):
    help = "Discover German employers and collect newly found career sources."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "city",
            nargs="?",
            help="Optional German city for BA-backed employer discovery",
        )
        parser.add_argument(
            "--city",
            dest="city_option",
            help="German city for BA-backed employer discovery",
        )
        parser.add_argument("--radius-km", type=int, default=DEFAULT_RADIUS_KM)
        parser.add_argument(
            "--publication-age-days",
            type=int,
            default=DEFAULT_PUBLICATION_AGE_DAYS,
        )
        parser.add_argument("--offer-type", type=int, default=1)
        parser.add_argument("--include-temporary-agencies", action="store_true")
        parser.add_argument("--max-pages", type=int)
        parser.add_argument(
            "--no-collect",
            dest="collect",
            action="store_false",
            help="Register discoveries without running the source collectors",
        )
        parser.set_defaults(collect=True)

    def handle(self, *args: Any, **options: Any) -> None:
        del args
        positional_city = options.get("city")
        option_city = options.get("city_option")
        if positional_city and option_city:
            raise CommandError("Specify the city either as an argument or with --city, not both.")
        city = option_city or positional_city

        service = ReverseDiscoveryService()
        try:
            try:
                result = service.discover(
                    city=city,
                    radius_km=options["radius_km"],
                    publication_age_days=options["publication_age_days"],
                    offer_type=options["offer_type"],
                    include_temporary_agencies=options["include_temporary_agencies"],
                    max_pages=options.get("max_pages"),
                    collect=options["collect"],
                )
            except ValueError as value_error:
                raise CommandError(str(value_error)) from value_error
        finally:
            service.close()

        status = "complete" if result.is_complete else "partial"
        self.stdout.write(
            f"{len(result.ats_tenants)} ATS tenant{_plural(result.ats_tenants)}; "
            f"{len(result.resolved_employers)} employer{_plural(result.resolved_employers)}; "
            f"{len(result.discovered_companies)} compan{_y_plural(result.discovered_companies)}; "
            f"{result.new_jobs} new job{_plural(result.new_jobs)} ({status})"
        )
        for discovery_error in result.errors:
            self.stderr.write(f"warning: {discovery_error}")
        if result.errors:
            raise CommandError("Reverse discovery completed partially.")


def _plural(value: int | Sized) -> str:
    return "" if _length(value) == 1 else "s"


def _y_plural(value: int | Sized) -> str:
    return "y" if _length(value) == 1 else "ies"


def _length(value: int | Sized) -> int:
    return value if isinstance(value, int) else len(value)
