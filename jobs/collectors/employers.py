"""Adapters for discovered and built-in German company career sites.

Career sites do not share one ATS API. The adapters keep site-specific parsing here, while the
generic JSON-LD adapter handles domains added through the UI. All adapters use the common
``HTTPCollector`` boundary for rate limiting and bot-protection handling.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from django.utils.dateparse import parse_date, parse_datetime

from jobs.collectors import (
    BotProtectionDetected,
    CollectionResult,
    CollectorRegistry,
    HTTPCollector,
    RawJob,
    SourceLike,
    UnsafeDetailUrl,
)
from jobs.structured_data import extract_links, iter_json_ld_objects

SIEMENS_FEED_URL = "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/feed/"
BOSCH_LIST_URL = "https://jobs.bosch.de/api/filter/query"
BOSCH_DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/{external_id}"
SAP_SEARCH_URL = "https://jobs.sap.com/search/"
TELEKOM_LIST_URL = "https://www.telekom.com/service/globaljobsearch/ui-com-16-en/1035312"
TELEKOM_SITE_URL = "https://www.telekom.com"
DHL_SEARCH_URL = "https://careers.dhl.com/global/en/search-results"
DHL_SITE_URL = "https://careers.dhl.com"

BOSCH_DETAIL_HOSTS = frozenset({"api.smartrecruiters.com"})
SAP_DETAIL_HOSTS = frozenset({"jobs.sap.com"})
TELEKOM_DETAIL_HOSTS = frozenset({"www.telekom.com"})
DHL_DETAIL_HOSTS = frozenset({"careers.dhl.com"})


class SiemensAvatureCollector(HTTPCollector):
    """Collect Germany-filtered Siemens Avature RSS results."""

    page_size = 20

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        is_complete = False
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
                channel = root.find("./channel")
                if (
                    root.tag.casefold().split("}")[-1] != "rss"
                    or channel is None
                    or channel.find("link") is None
                ):
                    raise ValueError("Siemens RSS did not contain a channel.")
                jobs.extend(raw for item in items if (raw := self._parse_item(item)) is not None)
                if len(items) < self.page_size:
                    is_complete = True
                    break
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )

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
        is_complete = False
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
                    detail = self._detail_or_empty(external_id)
                    raw = self._raw_from_detail(data, detail, external_id)
                    if raw is not None:
                        jobs.append(raw)
                next_page = payload.get("nextPage")
                if next_page in (None, False, "", 0):
                    is_complete = True
                    break
                if next_page is True:
                    page += 1
                elif isinstance(next_page, int):
                    page = next_page
                elif isinstance(next_page, str) and next_page.isdecimal():
                    page = int(next_page)
                else:
                    break
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )

    def _detail_or_empty(self, external_id: str) -> dict[str, Any]:
        try:
            response = self.fetch_trusted(
                BOSCH_DETAIL_URL.format(external_id=external_id),
                allowed_hosts=BOSCH_DETAIL_HOSTS,
            )
            return _response_json(response, "Bosch detail")
        except BotProtectionDetected:
            raise
        except (httpx.HTTPError, UnsafeDetailUrl, ValueError):
            return {}

    def _raw_from_detail(
        self, list_data: dict[str, Any], detail: dict[str, Any], external_id: str
    ) -> RawJob | None:
        location = detail.get("location")
        location_data = location if isinstance(location, dict) else {}
        if not _has_germany_evidence(
            list_data.get("country"),
            list_data.get("locations"),
            list_data.get("location"),
            location_data.get("country"),
            location_data.get("countryCode"),
        ):
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
        reached_end = False
        detail_urls_complete = True
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
                if not _has_sap_result_shape(response.text):
                    raise ValueError("SAP search did not contain a result table.")
                for title, href, listed_location in records:
                    external_id = _path_id(href)
                    if not external_id:
                        continue
                    detail_url = _absolute_url(href, SAP_SEARCH_URL)
                    try:
                        detail = _job_posting(
                            self.fetch_trusted(detail_url, allowed_hosts=SAP_DETAIL_HOSTS).text
                        )
                    except BotProtectionDetected:
                        raise
                    except UnsafeDetailUrl:
                        detail_urls_complete = False
                        continue
                    except (httpx.HTTPError, ValueError):
                        detail = {}
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
                    reached_end = True
                    break
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs,
            requests_made=self.requests_made,
            is_complete=reached_end and detail_urls_complete,
        )


class TelekomJsonCollector(HTTPCollector):
    """Collect the Deutsche Telekom Germany JSON search endpoint."""

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        self._detail_urls_complete = True
        try:
            first_page = self._fetch_page(0)
            jobs.extend(self._jobs_from_page(first_page))
            page_count = _nested_int(first_page, "paging", "page_count")
            if page_count is None:
                raise ValueError("Telekom list did not provide a page count.")
            for page in range(1, min(page_count, _max_pages(self.source))):
                jobs.extend(self._jobs_from_page(self._fetch_page(page)))
            is_complete = page_count <= _max_pages(self.source) and self._detail_urls_complete
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )

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
            try:
                posting = _job_posting(
                    self.fetch_trusted(url, allowed_hosts=TELEKOM_DETAIL_HOSTS).text
                )
            except BotProtectionDetected:
                raise
            except UnsafeDetailUrl:
                self._detail_urls_complete = False
                continue
            except (httpx.HTTPError, ValueError):
                posting = {}
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
        is_complete = False
        self._detail_urls_complete = True
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
                    try:
                        posting = _job_posting(
                            self.fetch_trusted(url, allowed_hosts=DHL_DETAIL_HOSTS).text
                        )
                    except BotProtectionDetected:
                        raise
                    except UnsafeDetailUrl:
                        self._detail_urls_complete = False
                        continue
                    except (httpx.HTTPError, ValueError):
                        posting = {}
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
                if offset >= total_hits:
                    is_complete = self._detail_urls_complete
                    break
                if len(records) < self.page_size:
                    break
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


class JsonLdCareerCollector(HTTPCollector):
    """Collect JobPosting records from a discovered career page and its job links."""

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        pending_urls = [self.source.source_url]
        visited_urls: set[str] = set()
        seen_external_ids: set[str] = set()
        is_complete = True
        saw_job_posting = False
        allowed_hosts = _json_ld_allowed_hosts(self.source)
        try:
            while pending_urls and len(visited_urls) < _max_pages(self.source):
                page_url = pending_urls.pop(0)
                if page_url in visited_urls:
                    continue
                visited_urls.add(page_url)
                try:
                    response = self.fetch_trusted(page_url, allowed_hosts=allowed_hosts)
                except BotProtectionDetected:
                    raise
                except (httpx.HTTPError, UnsafeDetailUrl, ValueError):
                    is_complete = False
                    continue
                postings = _json_ld_postings(response.text)
                page_jobs = _raw_jobs_from_postings(
                    postings, page_url=page_url, allowed_hosts=allowed_hosts
                )
                saw_job_posting = (
                    saw_job_posting
                    or bool(page_jobs)
                    or any(_posting_has_country_evidence(posting) for posting in postings)
                )
                for raw_job in page_jobs:
                    if raw_job.external_id not in seen_external_ids:
                        seen_external_ids.add(raw_job.external_id)
                        jobs.append(raw_job)
                for candidate_url in _json_ld_job_links(
                    response.text, base_url=page_url, allowed_hosts=allowed_hosts
                ):
                    if candidate_url not in visited_urls and candidate_url not in pending_urls:
                        pending_urls.append(candidate_url)
            if pending_urls or not saw_job_posting:
                is_complete = False
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


def register_employer_collectors(registry: CollectorRegistry) -> None:
    """Register the built-in and discovered career-site adapters."""
    registry.register("json_ld", JsonLdCareerCollector)
    registry.register("siemens_avature", SiemensAvatureCollector)
    registry.register("bosch_smartrecruiters", BoschSmartRecruitersCollector)
    registry.register("sap_successfactors", SapSuccessFactorsCollector)
    registry.register("telekom_json", TelekomJsonCollector)
    registry.register("dhl_phenom", DhlPhenomCollector)


def _json_ld_jobs(html: str, *, page_url: str, allowed_hosts: frozenset[str]) -> list[RawJob]:
    return _raw_jobs_from_postings(
        _json_ld_postings(html), page_url=page_url, allowed_hosts=allowed_hosts
    )


def _json_ld_postings(html: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    for posting in iter_json_ld_objects(html):
        posting_type = posting.get("@type")
        types = posting_type if isinstance(posting_type, list) else [posting_type]
        if any(isinstance(value, str) and value.casefold() == "jobposting" for value in types):
            if _string(posting.get("title")):
                postings.append(posting)
    return postings


def _posting_has_country_evidence(posting: dict[str, Any]) -> bool:
    return any(country for _, _, country, _, _ in _posting_locations(posting))


def _raw_jobs_from_postings(
    postings: Iterable[dict[str, Any]],
    *,
    page_url: str,
    allowed_hosts: frozenset[str],
) -> list[RawJob]:
    jobs: list[RawJob] = []
    for posting in postings:
        title = _string(posting.get("title"))
        if not title:
            continue
        canonical_url = (
            _safe_job_url(
                _string(posting.get("url")),
                base_url=page_url,
                allowed_hosts=allowed_hosts,
                require_https=True,
            )
            or page_url
        )
        posting = {**posting, "url": canonical_url}
        external_id = _posting_external_id(posting, canonical_url=canonical_url, title=title)
        raw_job = _raw_from_job_posting(
            external_id=external_id,
            canonical_url=canonical_url,
            title=title,
            posting=posting,
            default_location="",
            require_country_evidence=True,
        )
        if raw_job is not None:
            jobs.append(raw_job)
    return jobs


def _json_ld_job_links(html: str, *, base_url: str, allowed_hosts: frozenset[str]) -> list[str]:
    candidates: list[str] = []
    terms = ("job", "career", "vacanc", "position", "opening", "stellen", "karriere")
    for href, text in extract_links(html):
        candidate = urljoin(base_url, href)
        parsed = urlsplit(candidate)
        host = parsed.hostname.casefold() if parsed.hostname else ""
        haystack = f"{href} {text}".casefold()
        if (
            parsed.scheme.casefold() == "https"
            and host in allowed_hosts
            and not parsed.username
            and not parsed.password
            and any(term in haystack for term in terms)
        ):
            candidates.append(
                urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))
            )
    return candidates


def _json_ld_allowed_hosts(source: SourceLike) -> frozenset[str]:
    hosts: set[str] = set()
    source_host = urlsplit(source.source_url).hostname
    if source_host:
        hosts.add(source_host.casefold())
    configured_hosts = source.config.get("allowed_hosts")
    if isinstance(configured_hosts, list):
        hosts.update(value.casefold() for value in configured_hosts if isinstance(value, str))
    if not hosts:
        raise ValueError("JSON-LD source URL must contain a host.")
    return frozenset(hosts)


def _posting_external_id(posting: dict[str, Any], *, canonical_url: str, title: str) -> str:
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value") or identifier.get("name")
    if isinstance(identifier, str) and identifier.strip():
        return identifier.strip()[:300]
    identity = f"{canonical_url}\x1f{title}"
    return hashlib.sha256(identity.encode()).hexdigest()


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


@dataclass(slots=True)
class _HtmlNode:
    tag: str
    attributes: dict[str, str]
    children: list[_HtmlNode] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


class _MicrodataParser(HTMLParser):
    void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.root = _HtmlNode(tag="document", attributes={})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(
            tag=tag.casefold(),
            attributes={key.casefold(): value or "" for key, value in attrs},
        )
        self._stack[-1].children.append(node)
        if node.tag not in self.void_tags:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in self.void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized_tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].text.append(data)


def _sap_list_records(html: str) -> list[tuple[str, str, str]]:
    """Read simple SuccessFactors rows without a third-party HTML parser."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL)
    row_records: list[tuple[str, str, str]] = []
    title_pattern = re.compile(
        r"<a\b(?P<attributes>[^>]*)>(?P<title>.*?)</a>", re.IGNORECASE | re.DOTALL
    )
    location_pattern = re.compile(
        r'<(?:td|span)[^>]*class=["\'][^"\']*jobLocation[^"\']*["\'][^>]*>(.*?)</(?:td|span)>',
        re.IGNORECASE | re.DOTALL,
    )
    for row in rows:
        anchor = title_pattern.search(row)
        if anchor is None:
            continue
        attributes = anchor.group("attributes")
        class_match = re.search(r"\bclass=[\"']([^\"']*)[\"']", attributes, re.IGNORECASE)
        href_match = re.search(r"\bhref=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
        if (
            class_match is None
            or "jobtitle" not in class_match.group(1).casefold()
            or href_match is None
        ):
            continue
        location_match = location_pattern.search(row)
        row_records.append(
            (
                _strip_html(anchor.group("title")),
                href_match.group(1),
                _strip_html(location_match.group(1)) if location_match else "",
            )
        )
    if row_records:
        return row_records
    parser = _SapListParser()
    parser.feed(html)
    records = [record for record in parser.records if all(record[:2])]
    if records:
        return records
    pattern = re.compile(
        r'<a[^>]*class="[^"]*jobTitle[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S
    )
    return [(_strip_html(title), href, "") for href, title in pattern.findall(html)]


def _has_sap_result_shape(html: str) -> bool:
    return bool(re.search(r"\bid=[\"']searchresults[\"']", html, re.IGNORECASE))


def _response_json(response: httpx.Response, source_name: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{source_name} returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{source_name} returned a non-object JSON response.")
    return payload


def _job_posting(html: str) -> dict[str, Any]:
    for value in iter_json_ld_objects(html):
        job_type = value.get("@type")
        types = job_type if isinstance(job_type, list) else [job_type]
        if any(isinstance(item, str) and item.casefold() == "jobposting" for item in types):
            return value
    microdata_parser = _MicrodataParser()
    microdata_parser.feed(html)
    for item in _microdata_items(microdata_parser.root):
        item_type = item.attributes.get("itemtype", "").casefold()
        if "schema.org/jobposting" in item_type:
            return _microdata_job_posting(item)
    return {}


def _microdata_items(node: _HtmlNode) -> Iterable[_HtmlNode]:
    for child in node.children:
        if "itemscope" in child.attributes:
            yield child
        yield from _microdata_items(child)


def _microdata_job_posting(item: _HtmlNode) -> dict[str, Any]:
    properties = _microdata_properties(item)
    posting: dict[str, Any] = {}
    for source_name, target_name in (
        ("title", "title"),
        ("description", "description"),
        ("datePosted", "datePosted"),
        ("employmentType", "employmentType"),
        ("url", "url"),
        ("skills", "skills"),
    ):
        value = _microdata_first(properties.get(source_name))
        if isinstance(value, str):
            posting[target_name] = value
    locations: list[dict[str, Any]] = []
    for location in properties.get("jobLocation", []):
        if not isinstance(location, dict):
            continue
        address = _microdata_first(location.get("address"))
        if isinstance(address, dict):
            locations.append({"address": _collapse_microdata(address)})
    if locations:
        posting["jobLocation"] = locations
    location_type = _microdata_first(properties.get("jobLocationType"))
    if isinstance(location_type, str):
        posting["jobLocationType"] = location_type
    return posting


def _microdata_properties(item: _HtmlNode) -> dict[str, list[str | dict[str, Any]]]:
    properties: dict[str, list[str | dict[str, Any]]] = {}

    def visit(node: _HtmlNode) -> None:
        for child in node.children:
            names = child.attributes.get("itemprop", "").split()
            is_item = "itemscope" in child.attributes
            if names:
                value: str | dict[str, Any]
                if is_item:
                    value = _microdata_properties(child)
                else:
                    value = _microdata_value(child)
                for name in names:
                    properties.setdefault(name, []).append(value)
            if not is_item:
                visit(child)

    visit(item)
    return properties


def _microdata_value(node: _HtmlNode) -> str:
    for attribute in ("content", "datetime", "href", "src"):
        if value := node.attributes.get(attribute):
            return value.strip()
    return _node_text(node)


def _node_text(node: _HtmlNode) -> str:
    text = [*node.text]
    for child in node.children:
        text.append(_node_text(child))
    return " ".join(" ".join(text).split())


def _microdata_first(value: list[str | dict[str, Any]] | None) -> str | dict[str, Any] | None:
    return value[0] if value else None


def _collapse_microdata(value: dict[str, list[str | dict[str, Any]]]) -> dict[str, Any]:
    collapsed: dict[str, Any] = {}
    for key, values in value.items():
        first = _microdata_first(values)
        if isinstance(first, dict):
            collapsed[key] = _collapse_microdata(first)
        elif isinstance(first, str):
            collapsed[key] = first
    return collapsed


def _raw_from_job_posting(
    *,
    external_id: str,
    canonical_url: str,
    title: str,
    posting: dict[str, Any],
    default_location: str,
    defaults: dict[str, Any] | None = None,
    require_country_evidence: bool = False,
) -> RawJob | None:
    canonical_url = _safe_job_url(canonical_url, base_url=canonical_url)
    if not canonical_url:
        return None
    values = defaults or {}
    detail_locations = _posting_locations(posting)
    german_locations = [location for location in detail_locations if _is_german(location[2])]
    default_locations = _string_list(values.get("locations")) or _as_locations(
        default_location, _city_from_location(default_location)
    )
    default_is_german = _has_germany_evidence(default_location, default_locations)
    if german_locations:
        city, state, _, latitude, longitude = german_locations[0]
        locations = [_format_location(location) for location in german_locations]
    elif require_country_evidence or (
        any(country for _, _, country, _, _ in detail_locations) and not default_is_german
    ):
        return None
    else:
        city = _city_from_location(default_location)
        state = ""
        latitude = None
        longitude = None
        locations = default_locations
    resolved_title = _string(posting.get("title")) or title
    if not resolved_title:
        return None
    location = city or _city_from_location(default_location)
    return RawJob(
        external_id=external_id,
        canonical_url=canonical_url,
        application_url=(
            _safe_job_url(_string(posting.get("url")), base_url=canonical_url) or canonical_url
        ),
        title=resolved_title,
        description_html=_string(posting.get("description")),
        city=location,
        state=state,
        country_code="DE",
        locations=locations or _as_locations(default_location, location),
        latitude=values.get("latitude") if values.get("latitude") is not None else latitude,
        longitude=values.get("longitude") if values.get("longitude") is not None else longitude,
        remote_type=_remote_type(values.get("remote_type") or posting.get("jobLocationType")),
        employment_type=_string(posting.get("employmentType")),
        department=_string(values.get("department")),
        skills=_string_list(posting.get("skills")),
        posted_at=values.get("posted_at") or _parse_date(_string(posting.get("datePosted"))),
        raw_payload=posting,
    )


def _posting_locations(
    posting: dict[str, Any],
) -> list[tuple[str, str, str, float | None, float | None]]:
    value = posting.get("jobLocation")
    candidates = value if isinstance(value, list) else [value]
    locations: list[tuple[str, str, str, float | None, float | None]] = []
    for candidate in candidates:
        location_data = candidate if isinstance(candidate, dict) else {}
        address = location_data.get("address")
        address_data = address if isinstance(address, dict) else {}
        geo = location_data.get("geo")
        geo_data = geo if isinstance(geo, dict) else {}
        locations.append(
            (
                _string(address_data.get("addressLocality")),
                _string(address_data.get("addressRegion")),
                _country_text(address_data.get("addressCountry")),
                _float_or_none(geo_data.get("latitude")),
                _float_or_none(geo_data.get("longitude")),
            )
        )
    return locations


def _format_location(location: tuple[str, str, str, float | None, float | None]) -> str:
    city, _, country, _, _ = location
    return ", ".join(value for value in (city, country) if value)


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


def _safe_job_url(
    value: str,
    *,
    base_url: str,
    allowed_hosts: frozenset[str] | None = None,
    require_https: bool = False,
) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(urljoin(base_url, value))
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if (
        scheme not in {"http", "https"}
        or (require_https and scheme != "https")
        or not host
        or parsed.username
        or parsed.password
        or port is not None
        or (allowed_hosts is not None and host not in allowed_hosts)
    ):
        return ""
    return urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


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
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _country_text(value: Any) -> str:
    if isinstance(value, dict):
        return _string(value.get("name")) or _string(value.get("identifier"))
    return _string(value)


def _is_german(value: Any) -> bool:
    normalized = _string(value).casefold()
    return normalized in {"de", "deu", "germany", "deutschland"}


def _has_germany_evidence(*values: Any) -> bool:
    for value in values:
        if isinstance(value, dict):
            if _has_germany_evidence(
                value.get("country"), value.get("countryCode"), value.get("addressCountry")
            ):
                return True
        elif isinstance(value, list):
            if _has_germany_evidence(*value):
                return True
        elif isinstance(value, str):
            normalized = value.casefold()
            if normalized in {"de", "deu", "germany", "deutschland"}:
                return True
            if any(term in normalized for term in ("germany", "deutschland", ", de", " de-")):
                return True
    return False


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
