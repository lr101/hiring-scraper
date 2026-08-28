"""Resolve BA employer names to public domains through explicit provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .ba_discovery import EmployerHiringSignal, normalize_employer_identity
from .company_discovery import normalize_domain
from .company_locations import CompanyCandidate, CompanyLocationLookupError, OverpassCompanyProvider
from .locations import LocationLookupError, NominatimLocationProvider


class EmployerResolver(Protocol):
    def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer | None: ...


class PlaceProvider(Protocol):
    def search(self, query: str) -> list[Any]: ...


class CompanyProvider(Protocol):
    def search(self, place: Any, *, radius_km: int) -> list[CompanyCandidate]: ...


@dataclass(frozen=True, slots=True)
class ResolvedEmployer:
    """A provider-backed public website matched to a BA employer signal."""

    employer_name: str
    normalized_name: str
    domain: str
    website_url: str
    resolver: str


class OSMEmployerResolver:
    """Use cached German place lookup and Overpass company websites as a safe fallback."""

    def __init__(
        self,
        *,
        location_provider: PlaceProvider | None = None,
        company_provider: CompanyProvider | None = None,
        radius_km: int = 25,
    ) -> None:
        self.location_provider = location_provider or NominatimLocationProvider()
        self.company_provider = company_provider or OverpassCompanyProvider()
        self.radius_km = max(1, radius_km)
        self._owns_location_provider = location_provider is None
        self._owns_company_provider = company_provider is None

    def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer | None:
        for location in employer.distinct_locations:
            try:
                places = self.location_provider.search(location)
            except (LocationLookupError, CompanyLocationLookupError):
                continue
            for place in places:
                try:
                    candidates = self.company_provider.search(place, radius_km=self.radius_km)
                except CompanyLocationLookupError:
                    continue
                for candidate in candidates:
                    if normalize_employer_identity(candidate.name) != employer.normalized_name:
                        continue
                    try:
                        domain = normalize_domain(candidate.website_url)
                    except ValueError:
                        continue
                    return ResolvedEmployer(
                        employer_name=employer.employer_name,
                        normalized_name=employer.normalized_name,
                        domain=domain,
                        website_url=candidate.website_url,
                        resolver="osm",
                    )
        return None

    def close(self) -> None:
        if self._owns_location_provider:
            close = getattr(self.location_provider, "close", None)
            if callable(close):
                close()
        if self._owns_company_provider:
            close = getattr(self.company_provider, "close", None)
            if callable(close):
                close()


class OvertureEmployerResolver:
    """Optional Overture boundary; no Overture dependency is required by the application."""

    def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer | None:
        del employer
        return None


class EmployerResolverChain:
    """Try configured resolvers in order, returning the first exact match."""

    def __init__(self, *resolvers: EmployerResolver) -> None:
        self.resolvers = tuple(resolvers)

    def resolve(self, employer: EmployerHiringSignal) -> ResolvedEmployer | None:
        for resolver in self.resolvers:
            resolved = resolver.resolve(employer)
            if resolved is not None:
                return resolved
        return None

    def close(self) -> None:
        for resolver in self.resolvers:
            close = getattr(resolver, "close", None)
            if callable(close):
                close()
