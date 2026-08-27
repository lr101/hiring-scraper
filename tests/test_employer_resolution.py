from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest

from jobs.ba_discovery import BAJobSignal, EmployerHiringSignal
from jobs.company_locations import CompanyCandidate
from jobs.employer_resolution import (
    EmployerResolverChain,
    OSMEmployerResolver,
    OvertureEmployerResolver,
    ResolvedEmployer,
)


def hiring_signal() -> EmployerHiringSignal:
    signal = BAJobSignal(
        employer_name="Acme GmbH",
        job_title="Backend Engineer",
        location="Berlin",
        postal_code="10115",
        reference_number="10000-1-S",
        published_at=date(2026, 8, 25),
    )
    return EmployerHiringSignal(
        normalized_name="acme gmbh",
        employer_name="Acme GmbH",
        total_active_jobs=1,
        recent_jobs=1,
        distinct_locations=("Berlin",),
        signals=(signal,),
    )


def test_osm_resolver_matches_a_ba_employer_to_an_exact_mapped_website() -> None:
    place = SimpleNamespace(name="Berlin")

    class PlaceProvider:
        def search(self, query: str) -> list[SimpleNamespace]:
            assert query == "Berlin"
            return [place]

    class CompanyProvider:
        def search(
            self, selected_place: SimpleNamespace, *, radius_km: int
        ) -> list[CompanyCandidate]:
            assert selected_place is place
            assert radius_km == 25
            return [
                CompanyCandidate(
                    name="Acme GmbH",
                    domain="acme.test",
                    website_url="https://acme.test/",
                )
            ]

    resolved = OSMEmployerResolver(
        location_provider=PlaceProvider(),
        company_provider=CompanyProvider(),
        radius_km=25,
    ).resolve(hiring_signal())

    assert resolved == ResolvedEmployer(
        employer_name="Acme GmbH",
        normalized_name="acme gmbh",
        domain="acme.test",
        website_url="https://acme.test/",
        resolver="osm",
    )


def test_resolver_chain_keeps_overture_optional_and_uses_the_next_resolver() -> None:
    class FallbackResolver:
        def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer:
            return ResolvedEmployer(
                employer_name=employer.employer_name,
                normalized_name=employer.normalized_name,
                domain="acme.test",
                website_url="https://acme.test/",
                resolver="fallback",
            )

    result = EmployerResolverChain(
        OvertureEmployerResolver(),
        FallbackResolver(),
    ).resolve(hiring_signal())

    assert result is not None
    assert result.resolver == "fallback"


def test_resolved_employer_is_immutable() -> None:
    resolved = ResolvedEmployer(
        employer_name="Acme GmbH",
        normalized_name="acme gmbh",
        domain="acme.test",
        website_url="https://acme.test/",
        resolver="osm",
    )

    with pytest.raises(FrozenInstanceError):
        cast(Any, resolved).domain = "other.test"
