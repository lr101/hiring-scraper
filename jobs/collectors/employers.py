"""Adapters for the first five German company career sites.

These sites do not share one ATS API. The adapters keep their site-specific parsing here and use
the common ``HTTPCollector`` boundary for rate limiting and bot-protection handling.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin
from xml.etree import ElementTree

import httpx
from django.utils.dateparse import parse_date, parse_datetime

from jobs.collectors import CollectionResult, CollectorRegistry, HTTPCollector, RawJob, SourceLike

SIEMENS_FEED_URL = "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/feed/"
BOSCH_LIST_URL = "https://jobs.bosch.de/api/filter/query"
BOSCH_DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/{external_id}"
SAP_SEARCH_URL = "https://jobs.sap.com/search/"
TELEKOM_LIST_URL = "https://www.telekom.com/service/globaljobsearch/ui-com-16-en/1035312"
TELEKOM_SITE_URL = "https://www.telekom.com"
DHL_SEARCH_URL = "https://careers.dhl.com/global/en/search-results"
DHL_SITE_URL = "https://careers.dhl.com"


class SiemensAvatureCollector(HTTPCollector):
    """Collect Germany-filtered Siemens Avature RSS results."""

    page_size = 20

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        try:
            for page in range(_max_pages(self.source)):
                offset = page * self.page_size
                parameters = {
                    "42386": "[812132]",
                    "listFilterMode": 1,
                    "folderOffset": offset,
                }
                response = self.fetch(f"{SIEMENS_FEED_URL}?{urlencode(parameters)}")
                try:
                    root = ElementTree.fromstring(response.text)
                except ElementTree.ParseError as error:
                    raise ValueError("Siemens returned invalid RSS XML.") from error
                items = root.findall(".//item")
                jobs.extend(raw for item in items if (raw := self._parse_item(item)) is not None)
                if len(items) < self.page_size:
                    break
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)

    def _parse_item(self, item: ElementTree.Element) -> RawJob | None:
        title = _element_text(item, "title")
        url = _element_text(item, "link") or _element_text(item, "guid")
        external_id = _path_id(url)
        if not (title and url and external_id):
            return None
        return RawJob(
            external_id=external_id,
            canonical_url=url,
            title=title,
            description_html=_element_text(item, "description"),
            country_code="DE",
            posted_at=_parse_date(_element_text(item, "pubDate")),
            raw_payload={"title": title, "url": url},
        )


class BoschSmartRecruitersCollector(HTTPCollector):
    """Collect Bosch list results and SmartRecruiters detail payloads."""

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        page = 0
        visited_pages: set[int] = set()
        try:
            for _ in range(_max_pages(self.source)):
                if page in visited_pages:
                    break
                visited_pages.add(page)
                query = str(self.source.config.get("query", ""))
                parameters = {
                    "indexName": "bosch_en",
                    "page": page,
                    "q": query,
                    "filter": "%7B%7D",
                }
                response = self.fetch(f"{BOSCH_LIST_URL}?{urlencode(parameters)}")
                payload = _response_json(response, "Bosch list")
                records = payload.get("jobs")
                if not isinstance(records, list):
                    raise ValueError("Bosch list did not contain jobs.")
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    data = record.get("data")
                    if not isinstance(data, dict):
                        continue
                    external_id = _string(data.get("idFS"))
                    if not external_id or _explicitly_non_german(data.get("location")):
                        continue
                    detail = _response_json(
                        self.fetch(BOSCH_DETAIL_URL.format(external_id=external_id)), "Bosch detail"
                    )
                    raw = self._raw_from_detail(data, detail, external_id)
                    if raw is not None:
                        jobs.append(raw)
                next_page = payload.get("nextPage")
                if next_page in (None, False, "", 0):
                    break
                try:
                    page = int(next_page) if isinstance(next_page, int) else page + 1
                except (TypeError, ValueError):
                    break
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)

    def _raw_from_detail(
        self, list_data: dict[str, Any], detail: dict[str, Any], external_id: str
    ) -> RawJob | None:
        location = detail.get("location")
        location_data = location if isinstance(location, dict) else {}
        if _explicitly_non_german(location_data.get("country")):
            return None
        city = _string(location_data.get("city")) or _city_from_location(
            _string(list_data.get("location"))
        )
        title = _string(detail.get("name")) or _string(list_data.get("title"))
        if not title:
            return None
        sections = _nested_dict(detail, "jobAd", "sections")
        description_html = "\n".join(
            _string(section.get("text"))
            for section in sections.values()
            if isinstance(section, dict) and _string(section.get("text"))
        )
        canonical_url = _absolute_url(_string(list_data.get("url")), "https://jobs.bosch.de")
        return RawJob(
            external_id=external_id,
            canonical_url=canonical_url or BOSCH_DETAIL_URL.format(external_id=external_id),
            application_url=_string(detail.get("applyUrl")),
            title=title,
            description_html=description_html,
            city=city,
            state=_string(location_data.get("region")),
            country_code="DE",
            locations=_as_locations(list_data.get("location"), city),
            latitude=_float_or_none(location_data.get("latitude")),
            longitude=_float_or_none(location_data.get("longitude")),
            remote_type=_remote_type(detail.get("remote")),
            employment_type=_label(detail.get("typeOfEmployment")),
            department=_label(detail.get("department")) or _string(list_data.get("department")),
            skills=_string_list(detail.get("skills")),
            posted_at=_parse_date(_string(detail.get("releasedDate"))),
            raw_payload=detail,
        )


class SapSuccessFactorsCollector(HTTPCollector):
    """Collect SAP SuccessFactors result rows and JobPosting details."""

    page_size = 25

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        try:
            for page in range(_max_pages(self.source)):
                offset = page * self.page_size
                parameters = {
                    "q": self.source.config.get("query", ""),
                    "locationsearch": "Germany",
                    "startrow": offset,
                }
                response = self.fetch(f"{SAP_SEARCH_URL}?{urlencode(parameters)}")
                records = _sap_list_records(response.text)
                for title, href, listed_location in records:
                    external_id = _path_id(href)
                    if not external_id:
                        continue
                    detail_url = _absolute_url(href, SAP_SEARCH_URL)
                    detail = _job_posting(self.fetch(detail_url).text)
                    raw = _raw_from_job_posting(
                        external_id=external_id,
                        canonical_url=detail_url,
                        title=title,
                        posting=detail,
                        default_location=listed_location,
                    )
                    if raw is not None:
                        jobs.append(raw)
                if len(records) < self.page_size:
                    break
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)


class TelekomJsonCollector(HTTPCollector):
    """Collect the Deutsche Telekom Germany JSON search endpoint."""

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        try:
            first_page = self._fetch_page(0)
            jobs.extend(self._jobs_from_page(first_page))
            page_count = _nested_int(first_page, "paging", "page_count")
            if page_count is None:
                raise ValueError("Telekom list did not provide a page count.")
            for page in range(1, min(page_count, _max_pages(self.source))):
                jobs.extend(self._jobs_from_page(self._fetch_page(page)))
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)

    def _fetch_page(self, page: int) -> dict[str, Any]:
        parameters = {"countries": "393776", "hits_per_page": 250, "pageNum": page}
        response = self.fetch(f"{TELEKOM_LIST_URL}?{urlencode(parameters)}")
        return _response_json(response, "Telekom list")

    def _jobs_from_page(self, payload: dict[str, Any]) -> list[RawJob]:
        records = _nested_value(payload, "results", "jobs")
        if not isinstance(records, list):
            raise ValueError("Telekom list did not contain jobs.")
        jobs: list[RawJob] = []
        for record in records:
            if not isinstance(record, dict) or _explicitly_non_german(record.get("country")):
                continue
            external_id = _string(record.get("id"))
            title = _string(record.get("title"))
            url = _absolute_url(_string(record.get("url")), TELEKOM_SITE_URL)
            if not (external_id and title and url):
                continue
            posting = _job_posting(self.fetch(url).text)
            raw = _raw_from_job_posting(
                external_id=external_id,
                canonical_url=url,
                title=title,
                posting=posting,
                default_location=_first_string(record.get("locations")),
                defaults={
                    "department": _string(record.get("division")),
                    "latitude": _float_or_none(record.get("latitude")),
                    "longitude": _float_or_none(record.get("longitude")),
                    "locations": _string_list(record.get("locations")),
                    "posted_at": _parse_date(_string(record.get("datetime"))),
                },
            )
            if raw is not None:
                jobs.append(raw)
        return jobs


class DhlPhenomCollector(HTTPCollector):
    """Collect DHL Phenom search state and JobPosting detail pages."""

    page_size = 10

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        try:
            offset = 0
            total_hits: int | None = None
            for _ in range(_max_pages(self.source)):
                response = self.fetch(f"{DHL_SEARCH_URL}?{urlencode({'from': offset, 's': 1})}")
                payload = _dhl_state(response.text)
                records = _find_jobs(payload)
                if records is None:
                    raise ValueError("DHL search state did not contain jobs.")
                if total_hits is None:
                    total_hits = _find_total_hits(payload)
                    if total_hits is None:
                        raise ValueError("DHL search state did not provide total hits.")
                for record in records:
                    if not isinstance(record, dict) or not _is_german(record.get("country")):
                        continue
                    external_id = _string(record.get("jobSeqNo"))
                    title = _string(record.get("title"))
                    if not (external_id and title):
                        continue
                    url = _absolute_url(_string(record.get("jobUrl")), DHL_SITE_URL)
                    if not url:
                        url = f"{DHL_SITE_URL}/global/en/job/{external_id}/{_slug(title)}"
                    posting = _job_posting(self.fetch(url).text)
                    raw = _raw_from_job_posting(
                        external_id=external_id,
                        canonical_url=url,
                        title=title,
                        posting=posting,
                        default_location=_string(record.get("location")),
                        defaults={
                            "department": _string(record.get("category")),
                            "remote_type": _remote_type(record.get("workplaceType")),
                            "posted_at": _parse_date(_string(record.get("postedDate"))),
                        },
                    )
                    if raw is not None:
                        jobs.append(raw)
                offset += self.page_size
                if offset >= total_hits or len(records) < self.page_size:
                    break
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)


def register_employer_collectors(registry: CollectorRegistry) -> None:
    """Register the initial company adapters with a collection registry."""
    registry.register("siemens_avature", SiemensAvatureCollector)
    registry.register("bosch_smartrecruiters", BoschSmartRecruitersCollector)
    registry.register("sap_successfactors", SapSuccessFactorsCollector)
    registry.register("telekom_json", TelekomJsonCollector)
    registry.register("dhl_phenom", DhlPhenomCollector)


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_json_ld = False
        self._parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = {key.casefold(): (value or "").casefold() for key, value in attrs}
        if attributes.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self.documents.append("".join(self._parts))
            self._in_json_ld = False


class _SapListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[tuple[str, str, str]] = []
        self._href = ""
        self._title_parts: list[str] = []
        self._location_parts: list[str] = []
        self._in_title = False
        self._in_location = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        css_class = attributes.get("class", "").casefold()
        if tag == "a" and "jobtitle" in css_class:
            self._href = attributes.get("href", "")
            self._title_parts = []
            self._location_parts = []
            self._in_title = True
        elif self._in_title and "joblocation" in css_class:
            self._in_location = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            if self._in_location:
                self._location_parts.append(data)
            else:
                self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self.records.append(
                (
                    " ".join(self._title_parts).strip(),
                    self._href,
                    " ".join(self._location_parts).strip(),
                )
            )
            self._in_title = False
            self._in_location = False


def _sap_list_records(html: str) -> list[tuple[str, str, str]]:
    """Read simple SuccessFactors rows without a third-party HTML parser."""
    parser = _SapListParser()
    parser.feed(html)
    records = [record for record in parser.records if all(record[:2])]
    if records:
        return records
    pattern = re.compile(
        r'<a[^>]*class="[^"]*jobTitle[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S
    )
    return [(_strip_html(title), href, "") for href, title in pattern.findall(html)]


def _response_json(response: httpx.Response, source_name: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{source_name} returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{source_name} returned a non-object JSON response.")
    return payload


def _job_posting(html: str) -> dict[str, Any]:
    parser = _JsonLdParser()
    parser.feed(html)
    for document in parser.documents:
        try:
            parsed = json.loads(document)
        except json.JSONDecodeError:
            continue
        for value in _walk_json_ld(parsed):
            job_type = value.get("@type")
            types = job_type if isinstance(job_type, list) else [job_type]
            if "JobPosting" in types:
                return value
    return {}


def _walk_json_ld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_json_ld(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_ld(item)


def _raw_from_job_posting(
    *,
    external_id: str,
    canonical_url: str,
    title: str,
    posting: dict[str, Any],
    default_location: str,
    defaults: dict[str, Any] | None = None,
) -> RawJob | None:
    city, state, country, latitude, longitude = _posting_location(posting)
    country_value = country or default_location
    if _explicitly_non_german(country_value):
        return None
    values = defaults or {}
    resolved_title = _string(posting.get("title")) or title
    if not resolved_title:
        return None
    location = city or _city_from_location(default_location)
    return RawJob(
        external_id=external_id,
        canonical_url=canonical_url,
        application_url=_string(posting.get("url")),
        title=resolved_title,
        description_html=_string(posting.get("description")),
        city=location,
        state=state,
        country_code="DE",
        locations=_string_list(values.get("locations"))
        or _as_locations(default_location, location),
        latitude=values.get("latitude") or latitude,
        longitude=values.get("longitude") or longitude,
        remote_type=_remote_type(values.get("remote_type") or posting.get("jobLocationType")),
        employment_type=_string(posting.get("employmentType")),
        department=_string(values.get("department")),
        skills=_string_list(posting.get("skills")),
        posted_at=values.get("posted_at") or _parse_date(_string(posting.get("datePosted"))),
        raw_payload=posting,
    )


def _posting_location(posting: dict[str, Any]) -> tuple[str, str, str, float | None, float | None]:
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else {}
    location_data = location if isinstance(location, dict) else {}
    address = location_data.get("address")
    address_data = address if isinstance(address, dict) else {}
    geo = location_data.get("geo")
    geo_data = geo if isinstance(geo, dict) else {}
    return (
        _string(address_data.get("addressLocality")),
        _string(address_data.get("addressRegion")),
        _string(address_data.get("addressCountry")),
        _float_or_none(geo_data.get("latitude")),
        _float_or_none(geo_data.get("longitude")),
    )


def _dhl_state(html: str) -> dict[str, Any]:
    marker = "phApp.ddo.eagerLoadRefineSearch"
    start = html.find(marker)
    if start == -1:
        raise ValueError("DHL page did not contain Phenom search state.")
    object_start = html.find("{", start)
    if object_start == -1:
        raise ValueError("DHL search state was not an object.")
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[object_start:])
    except json.JSONDecodeError as error:
        raise ValueError("DHL page contained invalid Phenom search state.") from error
    if not isinstance(payload, dict):
        raise ValueError("DHL search state was not an object.")
    return payload


def _find_jobs(value: Any) -> list[Any] | None:
    if isinstance(value, dict):
        jobs = value.get("jobs")
        if isinstance(jobs, list):
            return jobs
        for child in value.values():
            found = _find_jobs(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_jobs(child)
            if found is not None:
                return found
    return None


def _find_total_hits(value: Any) -> int | None:
    if isinstance(value, dict):
        total = value.get("totalHits")
        if isinstance(total, int):
            return total
        for child in value.values():
            found = _find_total_hits(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_total_hits(child)
            if found is not None:
                return found
    return None


def _nested_value(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _nested_dict(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    nested = _nested_value(value, *keys)
    return nested if isinstance(nested, dict) else {}


def _nested_int(value: dict[str, Any], *keys: str) -> int | None:
    nested = _nested_value(value, *keys)
    return nested if isinstance(nested, int) else None


def _element_text(element: ElementTree.Element, tag: str) -> str:
    child = element.find(tag)
    return "" if child is None else "".join(child.itertext()).strip()


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _label(value: Any) -> str:
    return _string(value.get("label")) if isinstance(value, dict) else _string(value)


def _first_string(value: Any) -> str:
    values = _string_list(value)
    return values[0] if values else ""


def _as_locations(value: Any, city: str) -> list[str]:
    locations = _string_list(value)
    return locations or ([city] if city else [])


def _absolute_url(value: str, base: str) -> str:
    return urljoin(base, value) if value else ""


def _path_id(url: str) -> str:
    parts = [part for part in url.rstrip("/").split("/") if part]
    return parts[-1] if parts else ""


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            date = parse_date(value)
            return datetime.combine(date, datetime.min.time(), tzinfo=UTC) if date else None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _is_german(value: Any) -> bool:
    normalized = _string(value).casefold()
    return normalized in {"de", "deu", "germany", "deutschland"}


def _explicitly_non_german(value: Any) -> bool:
    normalized = " ".join(_string_list(value)) if isinstance(value, list) else _string(value)
    normalized = normalized.casefold()
    if not normalized:
        return False
    german_terms = ("de", "deu", "germany", "deutschland")
    if normalized in german_terms:
        return False
    german_location_terms = ("germany", "deutschland", ", de", " de-")
    return not any(term in normalized for term in german_location_terms)


def _city_from_location(value: str) -> str:
    return value.split(",", 1)[0].strip() if value else ""


def _remote_type(value: Any) -> str:
    normalized = _string(value).casefold()
    if any(term in normalized for term in ("remote", "telecommute", "home")):
        return "remote"
    if "hybrid" in normalized:
        return "hybrid"
    if any(term in normalized for term in ("onsite", "on-site", "office")):
        return "onsite"
    return "unknown"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def _max_pages(source: SourceLike) -> int:
    return max(1, source.max_pages)
