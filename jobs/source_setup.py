from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from .company_discovery import DiscoveredCompany
from .models import CareerSource, Company, MonitoringTarget, WorkspaceUser


@dataclass(frozen=True, slots=True)
class MonitoredCompany:
    company: Company
    source: CareerSource
    target_created: bool
    source_created: bool


def monitor_company(
    *,
    user: WorkspaceUser,
    company: Company | None = None,
    discovery: DiscoveredCompany | None = None,
) -> MonitoredCompany:
    """Create or reuse one company source and attach it to the selected account."""
    if company is None and discovery is None:
        raise ValueError("A company or discovery result is required.")
    with transaction.atomic():
        source: CareerSource | None = None
        source_created = False
        if discovery is not None:
            company, _ = Company.objects.get_or_create(
                domain=discovery.domain,
                defaults={"name": discovery.name, "career_url": discovery.career_url},
            )
            source, source_created = CareerSource.objects.get_or_create(
                source_url=discovery.career_url,
                defaults={
                    "company": company,
                    "kind": discovery.source_kind,
                    "config": {"allowed_hosts": list(discovery.allowed_hosts)},
                },
            )
        else:
            assert company is not None
            source = company.sources.order_by("pk").first()
            if source is None:
                source, source_created = CareerSource.objects.get_or_create(
                    source_url=company.career_url,
                    defaults={
                        "company": company,
                        "kind": CareerSource.Kind.JSON_LD,
                        "config": {
                            "allowed_hosts": [company.domain, f"www.{company.domain}"],
                        },
                    },
                )
        assert company is not None
        assert source is not None
        _target, target_created = MonitoringTarget.objects.get_or_create(
            user=user,
            kind=MonitoringTarget.Kind.COMPANY,
            company=company,
        )
    return MonitoredCompany(
        company=company,
        source=source,
        target_created=target_created,
        source_created=source_created,
    )
