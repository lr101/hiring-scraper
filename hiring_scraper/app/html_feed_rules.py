"""Rules both feed workers apply to postings read from career pages."""
from __future__ import annotations

from sqlalchemy import func, select

from hiring_scraper.app.models import Company, Job, JobFeed
from hiring_scraper.geography import normalize_german_state

HTML_PROVIDERS = frozenset({"html_jobs", "browser_html"})

# Companies whose address says nothing about where their postings are: agencies
# place people elsewhere, newspapers and chambers publish others' vacancies.
_NOT_A_WORKPLACE_CATEGORIES = frozenset({
    "employment_agency", "newspaper", "association", "chamber", "employer_from_job_feed", "job_board",
    "recruiter", "staffing", "publisher", "media", "advertising",
})


def _german_state(value: str | None) -> str | None:
    try:
        return normalize_german_state(value) if value else None
    except ValueError:
        return None


def company_address_location(session, company: Company) -> dict | None:
    """A careers page's own company site, when it can stand in for an unstated work location.

    Only a likely single-site employer qualifies: a precise point with positive
    evidence of Germany (country code or a German state), not an agency or
    publisher, and no other company record on the same domain (a group's
    branches share one careers site whose postings are elsewhere). That last
    check cannot prove a single site; the row says "Company address" and its
    precision is marked so the place reads as inferred.
    """
    if company.latitude is None or company.longitude is None or not company.domain:
        return None
    if company.location_precision not in {"point", "feature_center"}:
        return None
    german = (company.country_code or "").casefold() == "de" or (
        not company.country_code and _german_state(company.state) is not None)
    if not german:
        return None
    if (company.category or "").casefold() in _NOT_A_WORKPLACE_CATEGORIES:
        return None
    if session.scalar(select(func.count(Company.id)).where(Company.domain == company.domain)) != 1:
        return None
    label = "Company address" + (f", {company.state}" if company.state else "")
    return {"label": label, "latitude": company.latitude, "longitude": company.longitude,
            "precision": "company_address", "country_code": "DE"}


def feed_company_address(session, feed: JobFeed, rows: list[dict]) -> dict | None:
    """The fallback for this scan, looked up only when some posting names no place."""
    if feed.provider not in HTML_PROVIDERS or not any(_unplaced(data) for data in rows):
        return None
    return company_address_location(session, feed.company)


def _unplaced(data: dict) -> bool:
    return not data.get("locations") and not (data.get("location") or "").strip()


def with_company_address(data: dict, job: Job, address: dict | None, employer: Company | None,
                         feed: JobFeed) -> dict:
    """Place a posting at its own employer's site when the page names no place.

    A posting whose structured data names another employer, or a remote one,
    keeps no location rather than a misleading one.
    """
    employer_id = employer.id if employer is not None else job.employer_company_id
    if address is None or job.is_remote or not _unplaced(data) or employer_id not in {None, feed.company_id}:
        # Includes an employer a previous scan resolved from structured data.
        return data
    job.location_text = address["label"]
    job.raw_metadata = {**(job.raw_metadata or {}), "location_source": "company_address"}
    return {**data, "location": address["label"], "locations": [address]}
