from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand

from jobs.models import CareerSource, Company


@dataclass(frozen=True, slots=True)
class InitialSource:
    company_name: str
    domain: str
    career_url: str
    kind: str
    source_url: str


INITIAL_SOURCES = (
    InitialSource(
        company_name="Siemens",
        domain="siemens.com",
        career_url="https://jobs.siemens.com/",
        kind=CareerSource.Kind.SIEMENS_AVATURE,
        source_url="https://jobs.siemens.com/en_US/externaljobs/SearchJobs/",
    ),
    InitialSource(
        company_name="Bosch",
        domain="bosch.de",
        career_url="https://jobs.bosch.de/",
        kind=CareerSource.Kind.BOSCH_SMARTRECRUITERS,
        source_url="https://jobs.bosch.de/en/",
    ),
    InitialSource(
        company_name="SAP",
        domain="sap.com",
        career_url="https://jobs.sap.com/",
        kind=CareerSource.Kind.SAP_SUCCESSFACTORS,
        source_url="https://jobs.sap.com/search/?locationsearch=Germany",
    ),
    InitialSource(
        company_name="Deutsche Telekom",
        domain="telekom.com",
        career_url="https://www.telekom.com/en/careers",
        kind=CareerSource.Kind.TELEKOM_JSON,
        source_url="https://www.telekom.com/service/globaljobsearch/ui-com-16-en/1035312",
    ),
    InitialSource(
        company_name="DHL",
        domain="dhl.com",
        career_url="https://careers.dhl.com/",
        kind=CareerSource.Kind.DHL_PHENOM,
        source_url="https://careers.dhl.com/global/en/search-results",
    ),
)


class Command(BaseCommand):
    help = "Create the five initial company career sources without changing existing records."

    def handle(self, *args: Any, **options: Any) -> None:
        created_companies = 0
        created_sources = 0
        for initial_source in INITIAL_SOURCES:
            company, company_created = Company.objects.get_or_create(
                career_url=initial_source.career_url,
                defaults={
                    "name": initial_source.company_name,
                    "domain": initial_source.domain,
                },
            )
            _, source_created = CareerSource.objects.get_or_create(
                source_url=initial_source.source_url,
                defaults={
                    "company": company,
                    "kind": initial_source.kind,
                },
            )
            created_companies += int(company_created)
            created_sources += int(source_created)
        self.stdout.write(
            f"Created {created_companies} companies and {created_sources} career sources."
        )
