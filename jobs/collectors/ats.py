"""Public, read-only adapters for common applicant-tracking-system feeds."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from jobs.collectors import (
    CollectionResult,
    CollectorRegistry,
    HTTPCollector,
    RawJob,
    SourceLike,
)
from jobs.network import UnsafeNetworkAddress, validate_public_hostname

PERSONIO = "personio"
SOFTGARDEN = "softgarden"
DVINCI = "dvinci"
GREENHOUSE = "greenhouse"
LEVER = "lever"
ASHBY = "ashby"
SMARTRECRUITERS = "smartrecruiters"
WORKABLE = "workable"
RECRUITEE = "recruitee"
SUCCESSFACTORS = "successfactors"
ONLYFY = "onlyfy"
WORKDAY = "workday"

_TENANT_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,199}\Z")
_WORKDAY_SITE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_WORKDAY_LOCALE_PATTERN = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})+\Z")
_PERSONIO_HOST_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,199}\.jobs\.personio\.de\Z")
_RECRUITEE_HOST_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,199}\.recruitee\.com\Z")
_WORKDAY_HOST_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,199}\.wd[0-9]+\.myworkdayjobs\.com\Z")
_SUCCESSFACTORS_HOST_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,199}\.successfactors\.com\Z")


@dataclass(frozen=True, slots=True)
class ATSUrlFingerprint:
    """A recognized public ATS feed and the tenant needed to collect it."""

    kind: str
    tenant: str
    source_url: str


def normalize_ats_tenant(value: str) -> str:
    """Validate an ATS tenant before placing it in a public feed URL."""
    tenant = value.strip().casefold()
    if not _TENANT_PATTERN.fullmatch(tenant):
        raise ValueError("ATS tenant must contain only lowercase letters, numbers, and hyphens.")
    return tenant


def personio_feed_url(tenant: str) -> str:
    return f"https://{normalize_ats_tenant(tenant)}.jobs.personio.de/xml"


def greenhouse_feed_url(tenant: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{normalize_ats_tenant(tenant)}/jobs?content=true"


def lever_feed_url(tenant: str) -> str:
    return f"https://api.lever.co/v0/postings/{normalize_ats_tenant(tenant)}?mode=json"


def ashby_feed_url(tenant: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{normalize_ats_tenant(tenant)}"


def smartrecruiters_feed_url(tenant: str) -> str:
    return (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{normalize_ats_tenant(tenant)}/postings?limit=100&offset=0"
    )


def workable_feed_url(tenant: str) -> str:
    return (
        "https://apply.workable.com/api/v3/accounts/"
        f"{normalize_ats_tenant(tenant)}/jobs?limit=100&offset=0"
    )


def recruitee_feed_url(tenant: str) -> str:
    return f"https://{normalize_ats_tenant(tenant)}.recruitee.com/api/offers.xml"


def onlyfy_feed_url(tenant: str) -> str:
    return f"https://api.prescreen.io/api/v1/companies/{normalize_ats_tenant(tenant)}/jobs"


def workday_feed_url(board_url: str) -> str:
    """Build Workday's read-only CXS endpoint from a validated board or CXS URL."""
    source_url = _safe_workday_source_url(board_url)
    if source_url is None:
        raise ValueError("Workday source URL must be a safe HTTPS board URL.")
    parsed = urlsplit(source_url)
    tenant = (parsed.hostname or "").split(".", maxsplit=1)[0]
    path = [part for part in parsed.path.split("/") if part]
    if (
        len(path) == 5
        and path[:2] == ["wday", "cxs"]
        and path[2] == tenant
        and _safe_workday_site(path[3])
        and path[4] == "jobs"
    ):
        return source_url
    site = _workday_board_site(path, tenant=tenant)
    if site is None:
        raise ValueError("Workday board URL must include a safe site identifier.")
    return urlunsplit(("https", parsed.netloc, f"/wday/cxs/{tenant}/{site}/jobs", "", ""))


def fingerprint_ats_url(url: str) -> ATSUrlFingerprint | None:
    """Recognize a public ATS board URL without accepting untrusted URL parts."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    path = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    if host.endswith(".jobs.personio.de") and host.count(".") == 3:
        tenant = host.removesuffix(".jobs.personio.de")
        return _fingerprint(PERSONIO, tenant, personio_feed_url)
    if host in {"boards.greenhouse.io", "boards-api.greenhouse.io"} and path:
        tenant = path[2] if host == "boards-api.greenhouse.io" and len(path) > 2 else path[0]
        return _fingerprint(GREENHOUSE, tenant, greenhouse_feed_url)
    if host in {"jobs.lever.co", "api.lever.co"}:
        tenant = path[2] if host == "api.lever.co" and len(path) > 2 else (path[0] if path else "")
        return _fingerprint(LEVER, tenant, lever_feed_url)
    if host in {"jobs.ashbyhq.com", "api.ashbyhq.com"} and path:
        tenant = path[-1]
        return _fingerprint(ASHBY, tenant, ashby_feed_url)
    if (
        host == "api.smartrecruiters.com"
        and len(path) >= 4
        and path[:2] == ["v1", "companies"]
        and path[3] == "postings"
    ):
        return _fingerprint(SMARTRECRUITERS, path[2], smartrecruiters_feed_url)
    if (
        host == "apply.workable.com"
        and len(path) == 5
        and path[:3] == ["api", "v3", "accounts"]
        and path[4] == "jobs"
    ):
        return _fingerprint(WORKABLE, path[3], workable_feed_url)
    if host.endswith(".recruitee.com") and host.count(".") == 2:
        return _fingerprint(RECRUITEE, host.removesuffix(".recruitee.com"), recruitee_feed_url)
    if (
        host == "api.prescreen.io"
        and len(path) == 5
        and path[:3] == ["api", "v1", "companies"]
        and path[4] == "jobs"
    ):
        return _fingerprint(ONLYFY, path[3], onlyfy_feed_url)
    if host.endswith(".myworkdayjobs.com"):
        tenant = host.split(".", maxsplit=1)[0]
        try:
            normalized_tenant = normalize_ats_tenant(tenant)
        except ValueError:
            return None
        try:
            source_url = workday_feed_url(url)
        except ValueError:
            return None
        return ATSUrlFingerprint(kind=WORKDAY, tenant=normalized_tenant, source_url=source_url)
    return None


def _fingerprint(kind: str, tenant: str, builder: Callable[[str], str]) -> ATSUrlFingerprint | None:
    try:
        normalized_tenant = normalize_ats_tenant(tenant)
    except ValueError:
        return None
    return ATSUrlFingerprint(
        kind=kind, tenant=normalized_tenant, source_url=builder(normalized_tenant)
    )


class UnsafeAtsSourceUrl(ValueError):
    """An ATS source URL cannot safely be requested by its adapter."""


class ATSCollector(HTTPCollector):
    """HTTP collector that restricts its source request to a known public ATS host."""

    def source_feed_url(self) -> str:
        if self.source.kind == WORKDAY:
            try:
                return workday_feed_url(self.source.source_url)
            except ValueError as error:
                raise UnsafeAtsSourceUrl(str(error)) from error
        return self.source.source_url

    def source_allowed_hosts(self) -> frozenset[str]:
        return _ats_source_allowed_hosts(source=self.source, url=self.source_feed_url())

    def fetch_source(self, url: str | None = None) -> Any:
        return self.fetch_trusted(
            url or self.source_feed_url(), allowed_hosts=self.source_allowed_hosts()
        )

    def post_source(self, url: str, *, json: dict[str, Any]) -> Any:
        return self.request_trusted(
            "POST", url, allowed_hosts=self.source_allowed_hosts(), json=json
        )


class PersonioXmlCollector(ATSCollector):
    """Collect Personio's public XML feed."""

    def collect(self) -> CollectionResult:
        return self._collect_xml("position", ("id",), ("name",))

    def _collect_xml(
        self, record_tag: str, id_names: tuple[str, ...], title_names: tuple[str, ...]
    ) -> CollectionResult:
        try:
            root = _xml_root(self.fetch_source().text, self.__class__.__name__)
            jobs = [
                raw
                for record in _xml_records(root, record_tag)
                if (raw := _raw_from_xml(record, id_names=id_names, title_names=title_names))
                is not None
            ]
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)


class SoftgardenJsonCollector(ATSCollector):
    """Collect softgarden's public JSON listings."""

    def collect(self) -> CollectionResult:
        return self._collect_json_records("jobs", id_names=("id",), title_names=("name", "title"))

    def _collect_json_records(
        self, record_key: str, *, id_names: tuple[str, ...], title_names: tuple[str, ...]
    ) -> CollectionResult:
        try:
            payload = _json_payload(self.fetch_source().text, self.__class__.__name__)
            records = _records(payload, record_key)
            jobs = [
                raw
                for record in records
                if (raw := _raw_from_record(record, id_names=id_names, title_names=title_names))
                is not None
            ]
            is_complete = _payload_is_complete(payload, returned=len(records), page_size=None)
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


class DVinciCollector(PersonioXmlCollector):
    """Collect d.vinci's XML or JSON feed, selected by its response format."""

    def collect(self) -> CollectionResult:
        is_complete = True
        try:
            body = self.fetch_source().text
            if body.lstrip().startswith("<"):
                root = _xml_root(body, "d.vinci")
                jobs = [
                    raw
                    for record in _xml_records(root, "job")
                    if (
                        raw := _raw_from_xml(
                            record, id_names=("id",), title_names=("title", "name")
                        )
                    )
                    is not None
                ]
            else:
                payload = _json_payload(body, "d.vinci")
                records = _records(payload, "jobs")
                jobs = [
                    raw
                    for record in records
                    if (
                        raw := _raw_from_record(
                            record, id_names=("id",), title_names=("title", "name")
                        )
                    )
                    is not None
                ]
                is_complete = _payload_is_complete(payload, returned=len(records), page_size=None)
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


class OnlyfyPrescreenCollector(SoftgardenJsonCollector):
    """Collect onlyfy/Prescreen's public JSON listings."""

    def collect(self) -> CollectionResult:
        return self._collect_json_records("jobs", id_names=("id",), title_names=("title", "name"))


class GreenhouseCollector(SoftgardenJsonCollector):
    """Collect the Greenhouse board API."""

    def collect(self) -> CollectionResult:
        return self._collect_json_records("jobs", id_names=("id",), title_names=("title",))


class LeverCollector(ATSCollector):
    """Collect Lever's public postings endpoint."""

    def collect(self) -> CollectionResult:
        try:
            payload = _json_payload(self.fetch_source().text, "Lever")
            records = payload if isinstance(payload, list) else _records(payload, "jobs")
            jobs = [
                raw
                for record in records
                if (
                    raw := _raw_from_record(record, id_names=("id",), title_names=("text", "title"))
                )
                is not None
            ]
        finally:
            self.close()
        return CollectionResult(raw_jobs=jobs, requests_made=self.requests_made)


class AshbyCollector(SoftgardenJsonCollector):
    """Collect Ashby's public job-board API."""

    def collect(self) -> CollectionResult:
        return self._collect_json_records("jobs", id_names=("id", "jobId"), title_names=("title",))


class SmartRecruitersCollector(ATSCollector):
    """Collect SmartRecruiters postings with bounded offset pagination."""

    page_size = 100

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        offset = 0
        is_complete = False
        base_url = self.source_feed_url()
        continuation_base_url = _with_query(base_url, limit=self.page_size)
        page_url = _with_query(base_url, limit=self.page_size, offset=offset)
        try:
            for _ in range(_max_pages(self.source)):
                payload = _json_payload(
                    self.fetch_source(page_url).text,
                    "SmartRecruiters",
                )
                records = _records(payload, "content")
                jobs.extend(
                    raw
                    for record in records
                    if (raw := _raw_from_smartrecruiters_record(record)) is not None
                )
                offset += len(records)
                continuation_url = _pagination_continuation_url(payload, continuation_base_url)
                has_more = (
                    continuation_url is not None
                    or _payload_has_more(payload, returned=offset)
                    or (len(records) >= self.page_size and bool(records))
                )
                is_complete = not has_more
                if is_complete or not records:
                    break
                page_url = continuation_url or _with_query(
                    base_url, limit=self.page_size, offset=offset
                )
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


class WorkableCollector(ATSCollector):
    """Collect Workable listings with bounded offset pagination."""

    page_size = 100

    def collect(self) -> CollectionResult:
        jobs: list[RawJob] = []
        offset = 0
        is_complete = False
        base_url = self.source_feed_url()
        continuation_base_url = _with_query(base_url, limit=self.page_size)
        page_url = _with_query(base_url, limit=self.page_size, offset=offset)
        try:
            for _ in range(_max_pages(self.source)):
                payload = _json_payload(
                    self.fetch_source(page_url).text,
                    "Workable",
                )
                records = _records(payload, "results")
                jobs.extend(
                    raw
                    for record in records
                    if (
                        raw := _raw_from_record(
                            record, id_names=("id", "shortcode"), title_names=("title",)
                        )
                    )
                    is not None
                )
                offset += len(records)
                continuation_url = _pagination_continuation_url(payload, continuation_base_url)
                has_more = (
                    continuation_url is not None
                    or _payload_has_more(payload, returned=offset)
                    or (len(records) >= self.page_size and bool(records))
                )
                is_complete = not has_more
                if is_complete or not records:
                    break
                page_url = continuation_url or _with_query(
                    base_url, limit=self.page_size, offset=offset
                )
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


class RecruiteeXmlCollector(PersonioXmlCollector):
    """Collect Recruitee's XML offers feed."""

    def collect(self) -> CollectionResult:
        return self._collect_xml("offer", ("id",), ("title", "name"))


class WorkdayCollector(ATSCollector):
    """Collect a Workday job-search response stored at the configured source URL."""

    def collect(self) -> CollectionResult:
        try:
            payload = _json_payload(
                self.post_source(
                    self.source_feed_url(),
                    json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                ).text,
                "Workday",
            )
            records = _records(payload, "jobPostings")
            jobs: list[RawJob] = []
            for record in records:
                canonical_url = _workday_job_url(
                    self.source.source_url, _string(record.get("externalPath"))
                )
                if canonical_url is None:
                    continue
                raw = _raw_from_record(
                    record,
                    id_names=("externalPath", "id", "jobReqId"),
                    title_names=("title",),
                )
                if raw is not None:
                    jobs.append(
                        replace(raw, canonical_url=canonical_url, application_url=canonical_url)
                    )
            is_complete = _payload_is_complete(payload, returned=len(records), page_size=None)
        finally:
            self.close()
        return CollectionResult(
            raw_jobs=jobs, requests_made=self.requests_made, is_complete=is_complete
        )


class SuccessFactorsXmlCollector(PersonioXmlCollector):
    """Collect the public SuccessFactors XML job feed."""

    def collect(self) -> CollectionResult:
        return self._collect_xml("job", ("jobId", "id"), ("title", "name"))


def register_ats_collectors(registry: CollectorRegistry) -> None:
    """Register public ATS source kinds without altering employer-specific adapters."""
    registry.register(PERSONIO, PersonioXmlCollector)
    registry.register(SOFTGARDEN, SoftgardenJsonCollector)
    registry.register(DVINCI, DVinciCollector)
    registry.register(ONLYFY, OnlyfyPrescreenCollector)
    registry.register(GREENHOUSE, GreenhouseCollector)
    registry.register(LEVER, LeverCollector)
    registry.register(ASHBY, AshbyCollector)
    registry.register(SMARTRECRUITERS, SmartRecruitersCollector)
    registry.register(WORKABLE, WorkableCollector)
    registry.register(RECRUITEE, RecruiteeXmlCollector)
    registry.register(WORKDAY, WorkdayCollector)
    registry.register(SUCCESSFACTORS, SuccessFactorsXmlCollector)


def _json_payload(body: str, source_name: str) -> dict[str, Any] | list[Any]:
    import json

    try:
        payload = json.loads(body)
    except ValueError as error:
        raise ValueError(f"{source_name} returned invalid JSON.") from error
    if not isinstance(payload, dict | list):
        raise ValueError(f"{source_name} returned an invalid JSON shape.")
    return payload


def _records(payload: dict[str, Any] | list[Any], key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ValueError(f"ATS feed did not contain a {key} list.")
    return [record for record in payload[key] if isinstance(record, dict)]


def _xml_root(body: str, source_name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise ValueError(f"{source_name} returned invalid XML.") from error


def _xml_records(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _raw_from_xml(
    record: ElementTree.Element, *, id_names: tuple[str, ...], title_names: tuple[str, ...]
) -> RawJob | None:
    external_id = _xml_text(record, *id_names) or _string(record.attrib.get("id"))
    title = _xml_text(record, *title_names)
    location = _xml_location(record)
    country = _xml_text(record, "country", "countryCode", "country_code")
    if not external_id or not title or not _has_germany_evidence(location, country):
        return None
    city = _xml_text(record, "city") or _city_from_location(location)
    url = _xml_text(record, "url", "careers_url", "jobUrl", "job_url")
    return RawJob(
        external_id=external_id,
        canonical_url=url or "",
        application_url=url,
        title=title,
        description_html=_xml_text(record, "description", "descriptionHtml", "description_html"),
        city=city,
        country_code="DE",
        locations=[location] if location else [],
        department=_xml_text(record, "department", "recruitingCategory"),
        raw_payload={"id": external_id, "title": title, "location": location},
    )


def _raw_from_record(
    record: dict[str, Any], *, id_names: tuple[str, ...], title_names: tuple[str, ...]
) -> RawJob | None:
    external_id = _first_value(record, id_names)
    title = _first_value(record, title_names)
    location = (
        record.get("location")
        or record.get("locationsText")
        or _nested(record, "categories", "location")
    )
    locations = record.get("locations") or record.get("secondaryLocations") or location
    country = (
        record.get("country")
        or record.get("countryCode")
        or _nested(record, "location", "country")
        or _nested(record, "location", "countryCode")
    )
    if not external_id or not title or not _has_germany_evidence(location, locations, country):
        return None
    location_text = _location_text(location) or _location_text(locations)
    city = _string(_nested(record, "location", "city")) or _city_from_location(location_text)
    url = _first_value(record, ("absolute_url", "hostedUrl", "jobUrl", "url", "careers_url"))
    if not url and external_id.startswith("/"):
        url = external_id
    return RawJob(
        external_id=external_id,
        canonical_url=url or self_url(record),
        application_url=_first_value(record, ("applyUrl", "apply_url")) or url,
        title=title,
        description_html=_first_value(
            record, ("descriptionHtml", "description_html", "description", "content")
        ),
        city=city,
        country_code="DE",
        locations=_string_list(locations) or ([location_text] if location_text else []),
        department=_first_value(record, ("department",))
        or _string(_nested(record, "categories", "team")),
        employment_type=_first_value(record, ("employmentType", "employment_type")),
        raw_payload=record,
    )


def _raw_from_smartrecruiters_record(record: dict[str, Any]) -> RawJob | None:
    raw = _raw_from_record(record, id_names=("id", "uuid"), title_names=("name", "title"))
    ref = _string(record.get("ref"))
    return replace(raw, canonical_url=ref, application_url=ref) if raw is not None and ref else raw


def self_url(record: dict[str, Any]) -> str:
    return _string(record.get("externalPath"))


def _first_value(record: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = _string(record.get(name))
        if value:
            return value
    return ""


def _nested(record: dict[str, Any], *names: str) -> Any:
    value: Any = record
    for name in names:
        if not isinstance(value, dict):
            return ""
        value = value.get(name)
    return value


def _xml_text(record: ElementTree.Element, *names: str) -> str:
    for element in record.iter():
        if _local_name(element.tag) in names:
            value = _string(element.text)
            if value:
                return value
    return ""


def _xml_location(record: ElementTree.Element) -> str:
    for element in record.iter():
        if _local_name(element.tag) in {"location", "office", "locations"}:
            values = [part.strip() for part in element.itertext() if part.strip()]
            if values:
                return ", ".join(values)
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _location_text(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(
            part
            for part in (
                _string(value.get("name")),
                _string(value.get("city")),
                _string(value.get("country")) or _string(value.get("countryCode")),
            )
            if part
        )
    if isinstance(value, list):
        return ", ".join(part for item in value if (part := _location_text(item)))
    return _string(value)


def _has_germany_evidence(*values: Any) -> bool:
    for value in values:
        if isinstance(value, dict):
            if _has_germany_evidence(*value.values()):
                return True
            continue
        if isinstance(value, list):
            if _has_germany_evidence(*value):
                return True
            continue
        normalized = _string(value).casefold()
        if normalized in {"de", "deu", "germany", "deutschland"}:
            return True
        if "germany" in normalized or "deutschland" in normalized:
            return True
        if normalized.endswith(", de") or " de-" in normalized:
            return True
    return False


def _city_from_location(value: str) -> str:
    return value.split(",", maxsplit=1)[0].strip() if value else ""


def _string(value: Any) -> str:
    return (
        value.strip() if isinstance(value, str) else str(value).strip() if value is not None else ""
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := _location_text(item))]
    text = _location_text(value)
    return [text] if text else []


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _max_pages(source: Any) -> int:
    return max(1, _integer(getattr(source, "max_pages", 1)) or 1)


def _with_query(url: str, **parameters: Any) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in parameters.items()})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _ats_source_allowed_hosts(*, source: SourceLike, url: str) -> frozenset[str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise UnsafeAtsSourceUrl(
            "ATS source URL must use HTTPS on an allowed public host."
        ) from error
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or port is not None
        or not _is_allowed_ats_source_host(source.kind, host)
    ):
        raise UnsafeAtsSourceUrl("ATS source URL must use HTTPS on an allowed public host.")
    try:
        validate_public_hostname(host)
    except UnsafeNetworkAddress as error:
        raise UnsafeAtsSourceUrl("ATS source URL host must resolve to a public address.") from error
    return frozenset({host})


def _is_allowed_ats_source_host(kind: str, host: str) -> bool:
    exact_hosts = {
        SOFTGARDEN: {"api.softgarden.io"},
        DVINCI: {"jobs.dvinci.com"},
        ONLYFY: {"api.prescreen.io"},
        GREENHOUSE: {"boards-api.greenhouse.io"},
        LEVER: {"api.lever.co"},
        ASHBY: {"api.ashbyhq.com"},
        SMARTRECRUITERS: {"api.smartrecruiters.com"},
        WORKABLE: {"apply.workable.com"},
    }
    if kind in exact_hosts:
        return host in exact_hosts[kind]
    if kind == PERSONIO:
        return bool(_PERSONIO_HOST_PATTERN.fullmatch(host))
    if kind == RECRUITEE:
        return bool(_RECRUITEE_HOST_PATTERN.fullmatch(host))
    if kind == WORKDAY:
        return bool(_WORKDAY_HOST_PATTERN.fullmatch(host))
    if kind == SUCCESSFACTORS:
        return bool(_SUCCESSFACTORS_HOST_PATTERN.fullmatch(host))
    return False


def _safe_workday_source_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not _WORKDAY_HOST_PATTERN.fullmatch(host)
        or parsed.username
        or parsed.password
        or port is not None
        or not _safe_workday_path(parsed.path)
    ):
        return None
    return urlunsplit(("https", host, parsed.path, "", ""))


def _safe_workday_site(value: str) -> bool:
    return bool(_WORKDAY_SITE_PATTERN.fullmatch(value))


def _workday_board_site(path: list[str], *, tenant: str) -> str | None:
    if len(path) == 1 and _safe_workday_site(path[0]):
        return path[0]
    if len(path) == 2 and _safe_workday_site(path[1]):
        if _WORKDAY_LOCALE_PATTERN.fullmatch(path[0]) or path[0].casefold() == tenant:
            return path[1]
    if (
        len(path) == 3
        and path[0].casefold() == tenant
        and _WORKDAY_LOCALE_PATTERN.fullmatch(path[1])
        and _safe_workday_site(path[2])
    ):
        return path[2]
    return None


def _workday_job_url(source_url: str, external_path: str) -> str | None:
    source = _safe_workday_source_url(source_url)
    if source is None:
        return None
    try:
        parsed = urlsplit(external_path)
    except ValueError:
        return None
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not _safe_workday_path(parsed.path)
    ):
        return None
    source_parts = urlsplit(source)
    return urlunsplit(("https", source_parts.netloc, parsed.path, "", ""))


def _safe_workday_path(path: str) -> bool:
    decoded_path = unquote(path)
    return (
        decoded_path.startswith("/")
        and not decoded_path.startswith("//")
        and "\\" not in decoded_path
        and all(segment not in {".", ".."} for segment in decoded_path.split("/"))
    )


def _pagination_metadata(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    metadata = [payload]
    for key in ("paging", "pagination", "meta", "metadata", "page"):
        value = payload.get(key)
        if isinstance(value, dict):
            metadata.append(value)
    return metadata


def _pagination_continuation(payload: dict[str, Any] | list[Any]) -> tuple[str, Any] | None:
    for values in _pagination_metadata(payload):
        for key in (
            "next",
            "nextPage",
            "next_page",
            "nextPageToken",
            "next_page_token",
            "nextToken",
            "next_token",
            "pageToken",
            "page_token",
            "cursor",
            "token",
        ):
            value = values.get(key)
            if value not in (None, False, ""):
                return key, value
    return None


def _pagination_continuation_url(payload: dict[str, Any] | list[Any], base_url: str) -> str | None:
    continuation = _pagination_continuation(payload)
    if continuation is None:
        return None
    key, value = continuation
    return _pagination_value_url(value, key=key, base_url=base_url)


def _pagination_value_url(value: Any, *, key: str, base_url: str) -> str | None:
    if isinstance(value, dict):
        for url_key in ("href", "url", "link"):
            candidate = _string(value.get(url_key))
            if candidate:
                continuation_url = _pagination_url(candidate, base_url=base_url)
                if continuation_url is not None:
                    return continuation_url
        for nested_key in ("cursor", "token", "pageToken", "page_token"):
            nested_value = value.get(nested_key)
            if nested_value not in (None, False, ""):
                return _with_query(base_url, **{nested_key: nested_value})
        return None
    candidate = _string(value)
    if not candidate:
        return None
    if candidate.startswith(("/", "https://")):
        return _pagination_url(candidate, base_url=base_url)
    query_key = "cursor" if key.startswith("next") else key
    return _with_query(base_url, **{query_key: candidate})


def _pagination_url(candidate: str, *, base_url: str) -> str | None:
    continuation_url = urljoin(base_url, candidate)
    try:
        configured = urlsplit(base_url)
        continuation = urlsplit(continuation_url)
    except ValueError:
        return None
    if (
        continuation.scheme != configured.scheme
        or continuation.netloc != configured.netloc
        or continuation.path != configured.path
    ):
        return None
    return continuation_url


def _payload_is_complete(
    payload: dict[str, Any] | list[Any], *, returned: int, page_size: int | None
) -> bool:
    if _payload_has_more(payload, returned=returned):
        return False
    return page_size is None or returned < page_size


def _payload_has_more(payload: dict[str, Any] | list[Any], *, returned: int) -> bool:
    for values in _pagination_metadata(payload):
        for key in ("total", "totalCount", "total_count", "totalFound"):
            total = _integer(values.get(key))
            if total is not None and returned < total:
                return True
    return _pagination_continuation(payload) is not None
