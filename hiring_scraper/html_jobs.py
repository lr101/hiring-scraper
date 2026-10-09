"""Conservative extraction of jobs from ordinary employer career HTML."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_HIDDEN = {"script", "style", "svg", "noscript", "template"}
_JOB_LINK = re.compile(r"(?:^|/)(?:jobs?|stellenangebote?|stellenangebot|jobangebote?|jobangebot|vacanc(?:y|ies)|open-positions|position)/([^/?#]+)/?$", re.I)
_DETAIL_ROUTE = re.compile(r"(?:^|/)(?:jobs?|stellenangebot|stellenangebote?|jobangebot|jobangebote?|vacanc(?:y|ies)|position)/([^/?#]+)/?$", re.I)
_CAREER_ROUTE = re.compile(r"karriere|careers?|jobs?|stellenangebote?|jobangebot|vacanc|open[-_/ ]positions", re.I)
_ARTICLE_ROUTE = re.compile(r"(?:^|/)(?:news|blog|press|aktuelles|stories|posts?)(?:/|[-.]|$)", re.I)
_OPEN_SECTION = re.compile(r"^(?:open positions?|open job postings?|open roles?|offene stellen|aktuelle jobs|stellenangebote|vacatures)$", re.I)
_STOP_SECTION = re.compile(r"^(?:our next open position|our office\b|unser büro\b|our team\b|benefits\b|faq\b|continue reading\b|contact\b|kontakt\b|about us\b)", re.I)
_GENERIC_HEADINGS = {
    "careers", "career", "jobs", "job", "about", "about us", "offene stellen", "open positions",
    "open job postings", "open roles", "aktuelle jobs", "stellenangebote",
    "unser büro", "our office", "our team", "benefits", "kontakt", "contact",
    "ausbildung", "duales studium", "students", "studierende",
}
_ROLE_WORDS = re.compile(
    r"engineer|developer|administrator|admin|techniker|technik|support|manager|lead|"
    r"analyst|analysis|automation|pathologist|scientist|researcher|software|"
    r"ausbildung|fachinformatiker|werkstudent|working student|intern|praktikum|"
    r"thesis|student|produktmanagement|product management|agronom|ux\s*/?\s*ui|"
    r"vertrieb|designer|qa\b|algorithm|data\b|cloud\b|it-",
    re.I,
)
_CITY_NAMES = (
    "Karlsruhe", "Berlin", "München", "Munich", "Stuttgart", "Hamburg",
    "Frankfurt am Main", "Frankfurt", "Köln", "Cologne", "Düsseldorf",
    "Leipzig", "Dresden", "Freiburg", "Heidelberg", "Mannheim", "Konstanz", "Halle",
    "Constance", "Ulm", "Augsburg", "Bonn", "Potsdam", "Wiesbaden", "Mainz",
    "Fulda", "Ludwigsburg", "Herrenberg", "Ravensburg", "Nürtingen", "Koblenz",
    "Walldorf", "Kaiserslautern",
)
_CITY_PATTERN = re.compile(r"\b(" + "|".join(re.escape(city) for city in sorted(_CITY_NAMES, key=len, reverse=True)) + r")\b", re.I)
_DISPLAY_NONE = re.compile(r"(?:^|;)display:none(?:!important)?(?:;|$)")
_VISIBILITY_HIDDEN = re.compile(r"(?:^|;)visibility:hidden(?:!important)?(?:;|$)")
_DISPLAY_BLOCK = re.compile(r"(?:^|;)display:block(?:!important)?(?:;|$)")
_INACTIVE_CARD_STATE = re.compile(r"closed|inactive|archiv|expired|filled|template|draft|disabled", re.I)
_INACTIVE_CARD_CLASS = re.compile(
    r"(?:^|[-_])(?:closed|inactive|archiv(?:ed)?|expired|filled|template|draft|disabled)(?:$|[-_])",
    re.I,
)
_CLOSED_CARD_TEXT = re.compile(r"(?:position|job|stelle).{0,16}(?:closed|filled)|(?:closed|filled).{0,16}(?:position|job|stelle)", re.I)
_INITIATIVE_APPLICATION = re.compile(
    r"(?:\binitiativ(?:bewerbung|application)\b|^(?:initiative|unsolicited|general|speculative|open)\s+application(?:\s*\([^)]*\))?$)",
    re.I,
)
_TALENT_POOL = re.compile(r"^(?:join (?:our )?)?talent (?:pool|community|network)(?:\s*\([^)]*\))?$", re.I)
_TRUE_DATA_FLAG_VALUES = frozenset({"true", "1", "yes"})


def _element_hidden(tag: str, attrs: dict[str, str]) -> bool:
    style = re.sub(r"\s+", "", attrs.get("style", "")).casefold()
    classes = set(attrs.get("class", "").casefold().split())
    modal_hidden = ("modal" in classes and "show" not in classes and
                    attrs.get("aria-hidden", "").casefold() != "false" and
                    attrs.get("aria-modal", "").casefold() != "true" and
                    not _DISPLAY_BLOCK.search(style))
    return bool(
        tag in _HIDDEN or "hidden" in attrs or attrs.get("aria-hidden", "").casefold() == "true" or
        classes.intersection({"d-none", "hidden", "invisible"}) or modal_hidden or
        _DISPLAY_NONE.search(style) or _VISIBILITY_HIDDEN.search(style)
    )


class _Element:
    def __init__(self, tag: str, attrs: dict[str, str], parent: _Element | None = None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.hidden = bool((parent and parent.hidden) or _element_hidden(tag, attrs))
        self.children: list[_Element | str] = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Element):
                yield from child.walk()

    def text(self) -> str:
        if self.hidden:
            return ""
        values = []
        for child in self.children:
            if isinstance(child, _Element):
                value = child.text()
            else:
                value = child
            if value.strip():
                values.append(value)
        return _clean_text(" ".join(values))


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Element(tag.casefold(), {key.casefold(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.casefold() not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\u00ad\u200b-\u200f\u2060\ufeff]", "", value)
    return " ".join(value.split())


def _plain(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _clean_text(re.sub(r"<[^>]*>", " ", value)) or None


def _types(value) -> set[str]:
    types = value.get("@type", []) if isinstance(value, dict) else []
    return {types} if isinstance(types, str) else {item for item in types if isinstance(item, str)} if isinstance(types, list) else set()


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _country_code(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("addressCountry")
    if not isinstance(value, str):
        return None
    value = value.strip()
    countries = {"germany": "DE", "deutschland": "DE", "united kingdom": "GB", "uk": "GB",
                 "ireland": "IE", "netherlands": "NL", "portugal": "PT", "spain": "ES",
                 "france": "FR", "austria": "AT", "switzerland": "CH", "united states": "US"}
    return value.upper() if len(value) == 2 and value.isalpha() else countries.get(value.casefold())


def _coordinate(value, limit: int) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and abs(number) <= limit else None


def _schema_locations(value) -> list[dict]:
    locations = []
    for place in value if isinstance(value, list) else [value]:
        if not isinstance(place, dict):
            continue
        address = place.get("address", place)
        if not isinstance(address, dict):
            continue
        country_value = address.get("addressCountry")
        country = _country_code(country_value)
        label = next((item.strip() for item in (address.get("addressLocality"), address.get("addressRegion"),
                                               country_value if isinstance(country_value, str) else country)
                      if isinstance(item, str) and item.strip() and item.strip() != "-"), None)
        if not label:
            continue
        location = {"label": label}
        if country:
            location["country_code"] = country
        geo = place.get("geo")
        if isinstance(geo, dict):
            latitude, longitude = _coordinate(geo.get("latitude"), 90), _coordinate(geo.get("longitude"), 180)
            if latitude is not None and longitude is not None:
                location.update(latitude=latitude, longitude=longitude, precision="source_coordinates")
        if location not in locations:
            locations.append(location)
    return locations


def _location_name(value) -> str | None:
    return "; ".join(item["label"] for item in _schema_locations(value)) or None


def _stable_id(title: str, url: str, *, destination_identity: bool = False) -> str:
    value = (_normalized_destination(url) if destination_identity else
             title.casefold().strip() + "\n" + urlsplit(url).path.casefold())
    return hashlib.sha1(value.encode()).hexdigest()[:20]


def _absolute_http(url: str, base: str) -> str | None:
    try:
        target = urljoin(base, url.strip())
        parts = urlsplit(target)
        # Accessing port performs additional validation in urllib.parse.
        _ = parts.port
    except (AttributeError, TypeError, ValueError):
        return None
    return target if (parts.scheme in {"http", "https"} and parts.hostname and
                      not parts.username and not parts.password) else None


def _normalized_destination(url: str) -> str:
    """Return a stable HTTP destination identity, preserving meaningful query IDs."""
    parts = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path or "/", query, ""))


def _same_origin(left: str, right: str) -> bool:
    left_parts, right_parts = urlsplit(left), urlsplit(right)
    return (left_parts.scheme.casefold(), left_parts.netloc.casefold()) == (
        right_parts.scheme.casefold(), right_parts.netloc.casefold()
    )


def _inactive_framework_row(row: dict) -> bool:
    if any(row.get(key) is False for key in ("active", "enabled", "published")):
        return True
    if any(bool(row.get(key)) for key in ("template", "isTemplate", "archived", "closed", "disabled")):
        return True
    state = " ".join(str(row.get(key, "")) for key in ("status", "state", "availability"))
    return bool(_INACTIVE_CARD_STATE.search(state))


def _arrangement(text: str) -> tuple[str | None, str | None]:
    lowered = text.casefold()
    if re.search(r"hybrid|hybrides arbeiten", lowered):
        return "hybrid", None
    if re.search(r"remote|fully distributed", lowered) and not re.search(r"remote (?:options?|available)", lowered):
        return "remote", "remote"
    if re.search(r"vor[- ]ort|onsite|on-site|arbeitsort", lowered):
        return "onsite", None
    return None, None


def _employment(text: str) -> str | None:
    lowered = text.casefold().replace("_", "-")
    if re.search(r"ausbildung|apprenticeship", lowered):
        return "apprenticeship"
    if re.search(r"werkstudent|working student", lowered):
        return "working_student"
    if re.search(r"praktikum|internship|\bintern\b", lowered):
        return "internship"
    if re.search(r"vollzeit|full[- ]time", lowered):
        return "full-time"
    if re.search(r"teilzeit|part[- ]time", lowered):
        return "part-time"
    return None


def _locations(text: str) -> list[dict]:
    found = []
    for match in re.finditer(r"(?:location|arbeitsort|standort|ort)\s*[:\-]\s*([^|\n;]{2,100})", text, re.I):
        value = re.split(r"\b(?:type|art|employment|beschäftigung)\s*:", match.group(1), flags=re.I)[0]
        for city in _CITY_PATTERN.findall(value):
            if city.casefold() not in {item["label"].casefold() for item in found}:
                found.append({"label": city})
    # Some German vacancy cards use the short form "Karlsruhe (Vor-Ort)".
    for city in _CITY_PATTERN.findall(text):
        near = re.search(r"\b" + re.escape(city) + r"\b(?:\s*\((?:vor[- ]ort|onsite)\))?", text, re.I)
        if near and re.search(r"vor[- ]ort|onsite", near.group(0), re.I):
            if city.casefold() not in {item["label"].casefold() for item in found}:
                found.append({"label": city})
    for match in re.finditer(r"(?:jobs?|stellenangebote?|open positions?|vacancies)\s+(?:in|at)\s+([^|,.;\n]{2,60})", text, re.I):
        for city in _CITY_PATTERN.findall(match.group(1)):
            if city.casefold() not in {item["label"].casefold() for item in found}:
                found.append({"label": city})
    for match in re.finditer(r"(?:working|work|based|located|arbeiten|tätig)\s+(?:in|from|at)\s+([^|,.;\n]{2,60})", text, re.I):
        for city in _CITY_PATTERN.findall(match.group(1)):
            if city.casefold() not in {item["label"].casefold() for item in found}:
                found.append({"label": city})
    for match in re.finditer(r"\blocated\b[^.;\n]{0,100}\bin\s+(" + "|".join(re.escape(city) for city in _CITY_NAMES) + r")\b", text, re.I):
        city = match.group(1)
        if city.casefold() not in {item["label"].casefold() for item in found}:
            found.append({"label": city})
    return found


def _metadata(text: str, extraction_method: str, *, application_email: str | None = None) -> dict:
    meta = {"extraction_method": extraction_method}
    arrangement, remote = _arrangement(text)
    employment = _employment(text)
    locations = _locations(text)
    if arrangement:
        meta["work_arrangement"] = arrangement
    if application_email:
        meta["application_email"] = application_email
    start = re.search(r"(?:start|ausbildungsstart|beginn)\s*:?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", text, re.I)
    if start:
        try:
            meta["start_date"] = date(int(start.group(3)), int(start.group(2)), int(start.group(1))).isoformat()
        except ValueError:
            pass
    return {"employment_type": employment, "work_arrangement": arrangement,
            "is_remote": remote == "remote" if remote else None,
            "locations": locations, "raw_metadata": meta}


def _job(title: str, url: str, method: str, text: str = "", *, description: str | None = None,
         location: str | None = None, employment_type: str | None = None,
         date_posted: str | None = None, application_email: str | None = None,
         extras: dict | None = None) -> dict:
    info = _metadata(text, method, application_email=application_email)
    if location:
        info["location"] = location
        info["locations"] = [{"label": place.strip()} for place in location.split(";") if place.strip()]
    if employment_type:
        info["employment_type"] = employment_type
    if isinstance(date_posted, str) and date_posted.strip():
        info["date_posted"] = date_posted[:10]
    if description:
        info["description"] = description[:12000]
    if extras:
        info["raw_metadata"].update(extras)
    info.update({"id": _stable_id(title, url, destination_identity=method == "structured_job_card"), "title": title, "url": url,
                 "location": info.get("location") or "; ".join(item["label"] for item in info["locations"])})
    return info


def _schema_posting(posting: dict, page_url: str) -> dict | None:
    title = _plain(posting.get("title"))
    url = posting.get("url") or posting.get("sameAs")
    absolute_url = _absolute_http(url, page_url) if isinstance(url, str) else None
    if not title or not absolute_url:
        return None
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value") or identifier.get("name")
    hiring_organization = posting.get("hiringOrganization")
    if isinstance(hiring_organization, list):
        hiring_organization = next((item for item in hiring_organization if isinstance(item, (str, dict))), None)
    organization = {}
    if isinstance(hiring_organization, dict):
        org_name = _plain(hiring_organization.get("name"))
        org_url_value = hiring_organization.get("url")
        org_url = _absolute_http(org_url_value, page_url) if isinstance(org_url_value, str) else None
        if org_name:
            organization["name"] = org_name[:300]
        if org_url:
            organization["url"] = org_url[:2000]
    elif isinstance(hiring_organization, str):
        org_name = _plain(hiring_organization)
        if org_name:
            organization["name"] = org_name[:300]
    job = _job(title, absolute_url, "schema_org_jobposting",
               location=_location_name(posting.get("jobLocation")),
               employment_type=_employment(str(posting.get("employmentType"))) if posting.get("employmentType") else None,
               description=_plain(posting.get("description")), date_posted=posting.get("datePosted"),
               extras={**{key: posting[key] for key in ("skills", "qualifications", "experienceRequirements", "educationRequirements", "applicantLocationRequirements", "validThrough") if key in posting},
                       **({"schema_identifier": str(identifier)} if identifier else {}),
                       **({"hiring_organization": organization} if organization else {})})
    job["locations"] = _schema_locations(posting.get("jobLocation"))
    if identifier:
        job["id"] = str(identifier)
    if posting.get("jobLocationType") == "TELECOMMUTE":
        job["is_remote"] = True
        job["work_arrangement"] = "remote"
    return job


def _candidate_title(value: str) -> bool:
    clean = _clean_text(value).strip(" ·|–—-:")
    return bool(clean and len(clean) <= 150 and clean.casefold() not in _GENERIC_HEADINGS
                and not re.match(r"^(?:spezialfragen|faq|häufige fragen|frequently asked questions?)\b", clean, re.I)
                and not re.match(r"^(?:stellenangebote|aktuelle stellenangebote|jobs|vacancies|open positions)\s+(?:für|for|in|at)\b", clean, re.I)
                and _ROLE_WORDS.search(clean))


def _application_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    return match.group(0) if match else None


def _context(node: _Element, title: str) -> str:
    title_key = title.casefold()
    for parent in _ancestors(node):
        text = parent.text()
        if title_key not in text.casefold() or len(text) <= len(title) + 16 or len(text) > 2200:
            continue
        other_job_links = [child for child in parent.walk()
                           if child is not node and child.tag == "a" and child.attrs.get("href")
                           and (_JOB_LINK.search(urlsplit(child.attrs["href"]).path) or
                                _DETAIL_ROUTE.search(urlsplit(child.attrs["href"]).path))
                           and _candidate_title(child.text())]
        if not other_job_links:
            return text
    return title


def _node_location(node: _Element) -> str | None:
    for child in node.walk():
        marker = " ".join((child.attrs.get("class", ""), child.attrs.get("id", ""),
                           child.attrs.get("itemprop", ""), child.attrs.get("data-testid", ""))).casefold()
        if not re.search(r"location|standort|arbeitsort|(?:^|[^a-z])ort(?:$|[^a-z])", marker):
            continue
        value = re.sub(r"^(?:in|am standort|standort)\s+", "", child.text(), flags=re.I).strip(" ·|,–—-")
        value = re.sub(r"\s+(?:details|mehr erfahren|apply|jetzt bewerben)$", "", value, flags=re.I).strip()
        if value and len(value) <= 100:
            return value
    return None


def _location_from_title(title: str) -> tuple[str | None, str]:
    clean = _clean_text(title).strip()
    for city in _CITY_NAMES:
        match = re.search(r"\b(?:in|am standort|standort)\s+" + re.escape(city) + r"\b", clean, re.I)
        if match:
            title_without_location = clean[:match.start()].strip(" ·|,–—-")
            title_without_location = re.sub(r"\s+(?:details|mehr erfahren|apply)$", "", title_without_location, flags=re.I)
            return city, title_without_location or clean
    for city in _CITY_NAMES:
        match = re.match(r"^" + re.escape(city) + r"\b\s*(?:[·|,–—-]|details\b|→)", clean, re.I)
        if match:
            title_without_location = clean[match.end():].strip(" ·|,–—-")
            return city, title_without_location or clean
    title_without_suffix = re.sub(r"\s+(?:details|mehr erfahren|apply|jetzt bewerben)$", "", clean, flags=re.I).strip()
    return None, title_without_suffix


def _location_from_url(url: str) -> str | None:
    path = unicodedata.normalize("NFKD", urlsplit(url).path.casefold())
    path = "".join(char for char in path if not unicodedata.combining(char))
    for city in _CITY_NAMES:
        slug = city.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        slug = unicodedata.normalize("NFKD", slug)
        slug = "".join(char for char in slug if not unicodedata.combining(char))
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
        if re.search(r"(?:^|[-/])(?:in-)?" + re.escape(slug) + r"(?:[-/]|$)", path):
            return city
    return None


def _leading_location(text: str | None) -> str | None:
    clean = _clean_text(text or "")
    for city in _CITY_NAMES:
        if re.match(r"^" + re.escape(city) + r"\s*(?:\||·|•|–|—|-)", clean, re.I):
            return city
    return None


def _job_link_title(anchor: _Element) -> str:
    preferred, headings = [], []
    for child in anchor.walk():
        if child is anchor:
            continue
        text = _clean_text(child.text()).strip()
        if not _candidate_title(text):
            continue
        marker = " ".join((child.attrs.get("class", ""), child.attrs.get("id", ""))).casefold()
        if re.search(r"title|titel", marker):
            preferred.append(text)
        elif re.fullmatch(r"h[1-6]", child.tag):
            headings.append(text)
    if preferred:
        return preferred[-1]
    if headings:
        return headings[-1]
    return _clean_text(anchor.text()).strip()


def _true_data_flag(attrs: dict[str, str], name: str) -> bool:
    return attrs.get(name, "").strip().casefold() in _TRUE_DATA_FLAG_VALUES


def _softgarden_job_id(url: str) -> str | None:
    parts = urlsplit(url)
    if not re.fullmatch(r'[a-z0-9-]+\.softgarden\.(io|de)', parts.hostname or '', re.I):
        return None
    match = re.fullmatch(r'/job/([0-9]+)/[^/]+/?', parts.path)
    return match[1] if match else None


def _inactive_card_class(classes: set[str]) -> bool:
    return any(
        _INACTIVE_CARD_CLASS.search(name) and not name.startswith(("not-", "not_"))
        for name in classes
    )


def _structured_job_cards(nodes: list[_Element], page_url: str) -> tuple[list[dict], list[dict]]:
    """Read explicit, server-rendered vacancy cards whose detail URLs use a query string."""
    jobs, candidates = [], []
    for card in nodes:
        if card.tag != "a" or card.attrs.get("data-guide-id", "").casefold() != "joblist-card":
            continue
        state = " ".join(card.attrs.get(key, "") for key in ("data-status", "data-state", "status"))
        classes = set(card.attrs.get("class", "").casefold().split())
        if (card.hidden or card.attrs.get("aria-disabled", "").casefold() == "true" or
                "disabled" in card.attrs or _inactive_card_class(classes) or
                _true_data_flag(card.attrs, "data-disabled") or _true_data_flag(card.attrs, "data-template") or
                _INACTIVE_CARD_STATE.search(state)):
            continue
        context = card.text()
        if _CLOSED_CARD_TEXT.search(context):
            continue
        target = _absolute_http(card.attrs.get("href"), page_url)
        if not target or not _same_origin(target, page_url):
            continue
        title_node = next((child for child in card.walk()
                           if child.attrs.get("data-guide-id", "").casefold() == "joblist-card-title"), None)
        title = title_node.text() if title_node else ""
        title = _clean_text(title).strip(" ·|–—-:")
        if not title or len(title) > 150 or title.casefold() in _GENERIC_HEADINGS:
            continue
        location_node = next((child for child in card.walk()
                              if child.attrs.get("data-guide-id", "").casefold() == "joblist-card-location"), None)
        location = location_node.text() if location_node else None
        tags_node = next((child for child in card.walk()
                          if child.attrs.get("data-guide-id", "").casefold() == "joblist-card-tags"), None)
        employment_type = _employment(tags_node.text()) if tags_node else None
        if _INITIATIVE_APPLICATION.search(title) or _TALENT_POOL.fullmatch(title):
            candidates.append({"title": title, "url": target,
                               "method": "structured_job_card", "confidence": "unconfirmed_role"})
            continue
        jobs.append(_job(title, target, "structured_job_card", context, location=location,
                         employment_type=employment_type, application_email=_application_email(context),
                         extras={"posting_identity": _normalized_destination(target)}))
    return jobs, candidates


def html_job_key(job: dict) -> tuple[str, ...]:
    """Use a detail URL to merge card/detail variants; keep same-title sites distinct."""
    metadata = job.get("raw_metadata") or {}
    method = metadata.get("extraction_method") if isinstance(metadata, dict) else None
    url = job.get("url", "")
    softgarden_id = _softgarden_job_id(url)
    if softgarden_id and method in {'schema_org_jobposting', 'schema_org_microdata', 'softgarden_job_link', 'softgarden_job_detail'}:
        return "softgarden", urlsplit(url).netloc.casefold(), softgarden_id
    if method == "phenom_public_job":
        return "phenom", urlsplit(url).netloc.casefold(), str(job["id"])
    try:
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/")
    except ValueError:
        parsed, path = None, ""
    if method in {'schema_org_jobposting', 'schema_org_microdata'}:
        if metadata.get('schema_identifier'):
            return 'schema', (parsed.hostname or '').casefold(), metadata['schema_identifier']
        if not metadata.get('microdata_page_fallback'):
            return 'destination', _normalized_destination(url)
    if method == "structured_job_card":
        return "destination", _normalized_destination(url)
    if (parsed and method in {"html_job_link", "html_job_detail"} and _DETAIL_ROUTE.search(path)):
        return "url", (parsed.hostname or "").casefold(), path.casefold()
    title = re.sub(r"\W+", " ", (job.get("title") or "").casefold()).strip()
    locations = sorted(item.get("label", "").casefold() for item in job.get("locations", [])
                       if isinstance(item, dict) and item.get("label"))
    return "title", title, ";".join(locations)


def _role_title_key(value: str) -> str:
    """Normalize a role label so linked cards suppress duplicate heading hints."""
    _, title = _location_from_title(value)
    return re.sub(r"\W+", " ", _clean_text(title).casefold()).strip()


def _ancestors(node: _Element):
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _detail_description(heading: _Element) -> str | None:
    # The nearest substantive ancestor bounds the role, keeping earlier biographies
    # and neighboring columns out. Read only blocks after the confirmed title.
    scope = next((parent for parent in _ancestors(heading)
                  if parent.tag not in {"body", "html", "document"} and
                  len(parent.text()) > len(heading.text()) + (16 if parent.tag in {"main", "article"} else 80)), None)
    if scope is None:
        return None
    active, parts = False, []
    for node in scope.walk():
        if node is heading:
            active = True
            continue
        if not active or node.hidden or node.tag not in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            continue
        text = node.text()
        if (node.tag in {"h2", "h3", "h4", "h5", "h6"} and
                re.fullmatch(r"our team|unser team|meet (?:our )?team|staff members", text.strip(" :·–—-"), re.I)):
            break
        if re.match(r"^(?:why join us|meet (?:EMBO )?staff|related (?:jobs|content)|other (?:jobs|vacancies))\b", text, re.I):
            break
        if node.tag == "h1":
            break
        if text:
            parts.append(text)
    return "\n\n".join(parts)[:12000] or None


def _application_deadline(text: str) -> str | None:
    match = re.search(r"application deadline\s*:?\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text, re.I)
    if not match:
        return None
    try:
        return datetime.strptime(" ".join(match.groups()), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def _heading_jobs(nodes: list[_Element], page_url: str) -> list[dict]:
    headings = [node for node in nodes if re.fullmatch(r"h[1-6]", node.tag) and node.text()]
    active = False
    stopped = False
    section_level = 6
    found_in_section = False
    jobs = []
    for heading in headings:
        if any(parent.attrs.get("cc-gh-id") == "department-item" or
               parent.attrs.get("cc-t-item") == "careers_deparment" for parent in _ancestors(heading)):
            continue
        title = heading.text().strip(" ·|–—-:")
        if _OPEN_SECTION.match(title):
            active, stopped = True, False
            section_level = int(heading.tag[1])
            found_in_section = False
            continue
        if active and _STOP_SECTION.match(title):
            stopped = True
        role_title = _candidate_title(title)
        if active and found_in_section and int(heading.tag[1]) <= section_level and not role_title:
            stopped = True
        if not active or stopped or not role_title:
            continue
        found_in_section = True
        context = _context(heading, title)
        email = _application_email(context)
        url = page_url + "#role-" + re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:70]
        job = _job(title, url, "open_positions_heading", context,
                   description=context if len(context) > len(title) + 35 else None,
                   application_email=email)
        jobs.append(job)
    return jobs


def _merge(jobs: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for job in jobs:
        key = html_job_key(job)
        existing = merged.get(key)
        if existing is None:
            merged[key] = job
            continue
        old_method = (existing.get("raw_metadata") or {}).get("extraction_method")
        new_method = (job.get("raw_metadata") or {}).get("extraction_method")
        if new_method == "html_job_detail" and old_method != "html_job_detail":
            existing["title"] = job["title"]
            existing["url"] = job["url"]
            existing["id"] = _stable_id(job["title"], job["url"])
        for field, value in job.items():
            if value in (None, "", [], {}) or field == "id":
                continue
            if field == "description" and len(value) <= len(existing.get(field) or ""):
                continue
            if field == "raw_metadata":
                existing[field] = {**existing.get(field, {}), **value}
            elif not existing.get(field) or field == "description":
                existing[field] = value
        # Prefer a true job detail route over a synthetic heading fragment.
        if new_method == "html_job_detail" and old_method != "html_job_detail":
            existing["url"] = job["url"]
    return list(merged.values())


def _phenom_public_jobs(nodes: list[_Element], page_url: str) -> tuple[list[dict], bool]:
    """Read literal public Phenom DDOs, without running JS or following apply APIs.

    Only the captured search/detail contracts are recognized. Search selections
    and single detail pages never establish a complete inventory.
    """
    scripts = ["".join(child for child in node.children if isinstance(child, str))
               for node in nodes if node.tag == "script" and
               node.attrs.get("type", "").lower() in {"", "text/javascript", "application/javascript"}]

    def literal(pattern):
        found = []
        for script in scripts:
            for match in re.finditer(pattern, script):
                try:
                    value, end = json.JSONDecoder().raw_decode(script[match.end():])
                    tail = script[match.end()+end:].lstrip()
                    if isinstance(value, dict) and (not tail or tail.startswith(';')):
                        found.append(value)
                except (ValueError, TypeError):
                    pass
        return found[0] if len(found) == 1 else None

    config = literal(r'\bvar\s+phApp\s*=\s*phApp\s*\|\|\s*(?=\{)')
    ddo = literal(r'\bphApp\.ddo\s*=\s*(?=\{)')
    routes = literal(r'\bphApp\.urlMap\s*=\s*(?=\{)')
    if not ddo or not any(key in ddo for key in ('eagerLoadRefineSearch', 'jobDetail')):
        return [], False
    if not config or not routes or routes.get('job') != 'job/:jobSeqNo/:title':
        return [], True
    base = _absolute_http(config.get('baseUrl'), page_url)
    if (not base or not _same_origin(base, page_url) or config.get('siteType') != 'external' or
            urlsplit(base).query or urlsplit(base).fragment or
            not urlsplit(page_url).path.startswith(urlsplit(base).path.rstrip('/')+'/')):
        return [], True
    rows = []
    for key in ('eagerLoadRefineSearch', 'jobDetail'):
        result = ddo.get(key)
        if not isinstance(result, dict) or result.get('status') != 200 or not isinstance(result.get('data'), dict):
            continue
        items = result['data'].get('jobs') if key == 'eagerLoadRefineSearch' else [result['data'].get('job')]
        if isinstance(items, list):
            rows.extend(items)
    jobs, seen = [], set()
    for row in rows:
        if (not isinstance(row, dict) or _inactive_framework_row(row) or row.get('siteType') != 'external' or
                row.get('visibilityType') != 'External' or
                any(str(row.get(key, '')).lower() in _TRUE_DATA_FLAG_VALUES for key in ('isPrivate', 'isConfidential'))):
            continue
        title, job_id = _plain(row.get('title')), row.get('jobSeqNo')
        if (not title or len(title) > 200 or title.casefold() in _GENERIC_HEADINGS or
                _INITIATIVE_APPLICATION.search(title) or _TALENT_POOL.search(title) or
                not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', job_id) or job_id in seen):
            continue
        slug = re.sub(r'[^\w-]+', '-', title, flags=re.UNICODE).strip('-')
        target = base.rstrip('/')+'/job/'+job_id+'/'+quote(slug)
        structure = row.get('structureData')
        job = None
        if isinstance(structure, dict) and 'JobPosting' in _types(structure) and structure.get('title') == row.get('title'):
            job = _schema_posting({**structure, 'url':target}, page_url)
        if not job:
            job = _job(title, target, 'phenom_public_job', location=_plain(row.get('location')),
                       description=_plain(row.get('descriptionTeaser')), date_posted=row.get('postedDate'),
                       employment_type=_plain(row.get('workHours')))
            city, country = _plain(row.get('city')), _country_code(row.get('country'))
            if city and country:
                job['locations'] = [{'label':city, 'country_code':country}]
        job['id'] = job_id
        job.setdefault('raw_metadata', {})['extraction_method'] = 'phenom_public_job'
        job['raw_metadata']['posting_identity'] = job_id
        # Machine-generated skills/teasers do not establish mandatory requirements.
        job['raw_metadata']['description_scope'] = 'job_detail' if isinstance(structure, dict) else 'teaser'
        jobs.append(job)
        seen.add(job_id)
    return jobs, True


def _microdata_jobs(nodes, page_url):
    """Read scoped Schema.org microdata without mixing adjacent organizations."""
    def properties(scope):
        result = {}
        def visit(node):
            if node.hidden:
                return
            names = node.attrs.get('itemprop', '').split()
            nested = 'itemscope' in node.attrs
            if names:
                value = properties(node) if nested else next(
                    (node.attrs[key] for key in ('content', 'datetime', 'href') if node.attrs.get(key)), node.text())
                for name in names:
                    result.setdefault(name, []).append(value)
            if not nested and not names:
                for child in node.children:
                    if isinstance(child, _Element):
                        visit(child)
        for child in scope.children:
            if isinstance(child, _Element):
                visit(child)
        return {key: values[0] if len(values) == 1 else values for key, values in result.items()}

    scopes = [node for node in nodes if 'itemscope' in node.attrs and any(
        re.fullmatch(r'https?://schema\.org/JobPosting/?', item)
        for item in node.attrs.get('itemtype', '').split())]
    jobs = []
    for scope in scopes:
        if scope.hidden:
            continue
        posting = properties(scope)
        title, description = posting.get('title'), posting.get('description')
        if (not isinstance(title, str) or not title.strip() or len(title) > 200 or
                title.casefold() in _GENERIC_HEADINGS or _INITIATIVE_APPLICATION.search(title) or
                _TALENT_POOL.search(title) or not isinstance(description, str) or not description.strip()):
            continue
        explicit_url = posting.get('url')
        target = _absolute_http(explicit_url or page_url, page_url) if isinstance(explicit_url or page_url, str) else None
        if not target or not _same_origin(target, page_url):
            continue
        posted = posting.get('datePosted')
        if isinstance(posted, str):
            try:
                posting['datePosted'] = datetime.fromisoformat(posted.replace('Z', '+00:00')).date().isoformat()
            except ValueError:
                try:
                    posting['datePosted'] = datetime.strptime(posted, '%a %b %d %H:%M:%S %Z %Y').date().isoformat()
                except ValueError:
                    posting.pop('datePosted', None)
        # Some public SuccessFactors pages put the full address in streetAddress.
        places = posting.get('jobLocation')
        for place in places if isinstance(places, list) else [places]:
            address = place.get('address') if isinstance(place, dict) else None
            if isinstance(address, dict) and not address.get('addressLocality'):
                match = re.fullmatch(r'([^,]{2,100}),\s*([A-Z]{2}),\s*(\d{4,6})', str(address.get('streetAddress', '')))
                if match:
                    address.update(addressLocality=match[1].strip(), addressCountry=match[2], postalCode=match[3])
        job = _schema_posting({**posting, 'url': target}, page_url)
        if job:
            if not posting.get('identifier'):
                job['id'] = _stable_id(title, job['url'], destination_identity=bool(explicit_url))
            job['raw_metadata']['extraction_method'] = 'schema_org_microdata'
            job['raw_metadata']['microdata_page_fallback'] = not bool(explicit_url)
            jobs.append(job)
    return jobs, bool(scopes)


def extract_html_jobs(body: bytes | str, page_url: str) -> dict:
    """Extract high-confidence vacancy data and separately retain role-list hints."""
    if _ARTICLE_ROUTE.search(urlsplit(page_url).path):
        return {"jobs": [], "role_candidates": [], "complete": True,
                "page_title": "", "has_open_vacancy_signal": False}
    source = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    parser = _TreeParser()
    parser.feed(source)
    nodes = list(parser.root.walk())
    title_nodes = [node for node in nodes if node.tag == "title"]
    title = title_nodes[0].text() if title_nodes else ""
    visible_text = " ".join(node.text() for node in nodes if node.tag == "body")
    if not visible_text:
        visible_text = parser.root.text()
    lower_text = visible_text.casefold()
    jobs = []
    role_candidates = []
    json_payloads = []
    phenom_jobs, phenom_partial = _phenom_public_jobs(nodes, page_url)
    jobs.extend(phenom_jobs)
    microdata_jobs, microdata_partial = _microdata_jobs(nodes, page_url)
    jobs.extend(microdata_jobs)

    for node in nodes:
        if node.tag != "script":
            continue
        script_type = node.attrs.get("type", "").casefold()
        script_id = node.attrs.get("id", "").casefold()
        if script_type == "application/ld+json" or script_type == "application/json" or script_id in {"__next_data__", "__nuxt_data__"}:
            try:
                json_payloads.append(json.loads("".join(child for child in node.children if isinstance(child, str))))
            except (ValueError, TypeError):
                continue

    for payload in json_payloads:
        for item in _walk_json(payload):
            if "JobPosting" in _types(item):
                posting = _schema_posting(item, page_url)
                if posting:
                    jobs.append(posting)
            if "ItemList" in _types(item):
                elements = item.get("itemListElement") or []
                if isinstance(elements, dict):
                    elements = [elements]
                for element in elements:
                    if not isinstance(element, dict):
                        continue
                    item_data = element.get("item") if isinstance(element.get("item"), dict) else element
                    name = _plain(item_data.get("name"))
                    url = item_data.get("url")
                    if name and isinstance(url, str):
                        absolute = _absolute_http(url, page_url)
                        if absolute:
                            role_candidates.append({"title": name, "url": absolute,
                                                    "method": "schema_org_itemlist", "confidence": "unconfirmed_role"})
            # Common framework payloads often expose an array named jobs/openings.
            for key in ("jobs", "openings", "vacancies", "positions", "jobOffers"):
                rows = item.get(key)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict) or _inactive_framework_row(row):
                        continue
                    role_title = _plain(row.get("title") or row.get("name") or row.get("jobTitle"))
                    role_url = row.get("url") or row.get("absolute_url") or row.get("jobUrl") or row.get("applyUrl")
                    if not _candidate_title(role_title or "") or not isinstance(role_url, str):
                        continue
                    absolute = _absolute_http(role_url, page_url)
                    if absolute and _same_origin(absolute, page_url):
                        role_candidates.append({"title": role_title, "url": absolute,
                                                "method": "embedded_json", "confidence": "unconfirmed_role"})

    structured_jobs, structured_candidates = _structured_job_cards(nodes, page_url)
    jobs.extend(structured_jobs)
    role_candidates.extend(structured_candidates)

    anchors = [node for node in nodes if node.tag == "a" and node.attrs.get("href")]
    for anchor in anchors:
        target = _absolute_http(anchor.attrs["href"], page_url)
        if not target or urlsplit(target).hostname != urlsplit(page_url).hostname:
            continue
        path = urlsplit(target).path
        softgarden_id = _softgarden_job_id(target)
        if not _JOB_LINK.search(path) and not softgarden_id:
            continue
        title_text = _job_link_title(anchor)
        valid_vendor_title = (softgarden_id and title_text and len(title_text) <= 150 and
                              title_text.casefold() not in _GENERIC_HEADINGS and
                              not _INITIATIVE_APPLICATION.search(title_text) and not _TALENT_POOL.search(title_text))
        if not _candidate_title(title_text) and not valid_vendor_title:
            continue
        context = _context(anchor, title_text)
        title_location, title_text = _location_from_title(title_text)
        location = _node_location(anchor) or title_location or _location_from_url(target)
        job = _job(title_text, target, "softgarden_job_link" if softgarden_id else "html_job_link", context,
                   location=location, application_email=_application_email(context))
        if softgarden_id:
            job['id'] = softgarden_id
        jobs.append(job)

    path = urlsplit(page_url).path
    softgarden_id = _softgarden_job_id(page_url)
    if not any(job["url"] == page_url for job in microdata_jobs) and (_DETAIL_ROUTE.search(path) or softgarden_id) and not re.search(r"no (?:current |open )?positions|keine (?:offenen )?stellen", lower_text):
        h1_nodes = [node for node in nodes if node.tag == "h1" and node.text()]
        h1_node = h1_nodes[0] if len(h1_nodes) == 1 else None
        if h1_node:
            h1 = h1_node.text().strip(" ·|–—-:")
            apply = any(re.search(r"^(?:apply|jetzt bewerben|bewerben|bewerbung)\b", node.text(), re.I) for node in anchors)
            form_apply = any(node.tag in {'h2', 'h3'} and re.match(r'^(?:jetzt bewerben|apply now)', node.text(), re.I) for node in nodes) and any(
                node.tag == 'form' and not node.hidden and re.search(r'lebenslauf|curriculum vitae|resume|\bcv\b', node.text(), re.I) and
                any(not child.hidden and (child.tag == 'input' and child.attrs.get('type') == 'file' or 'ginput_container_fileupload' in child.attrs.get('class', '').split()) for child in node.walk())
                for node in nodes)
            apply = apply or form_apply
            vacancy_marker = any(re.search(r"(?:^|[ _-])vacancy(?:$|[ _-])", parent.attrs.get("class", ""), re.I)
                                 for parent in _ancestors(h1_node))
            confirmed = (_candidate_title(h1) or
                         (apply or vacancy_marker) and 0 < len(h1) <= 150 and h1.casefold() not in _GENERIC_HEADINGS)
            if confirmed and not _INITIATIVE_APPLICATION.search(h1) and not _TALENT_POOL.search(h1):
                detail_context = _detail_description(h1_node) or ""
                job = _job(h1, page_url, "softgarden_job_detail" if softgarden_id else "html_job_detail", detail_context, location=_leading_location(detail_context),
                           description=detail_context or None, application_email=_application_email(detail_context))
                if softgarden_id:
                    job['id'] = softgarden_id
                deadline = _application_deadline(visible_text)
                if deadline:
                    job["raw_metadata"]["validThrough"] = deadline
                jobs.append(job)

    if not re.search(r"no (?:current |open )?positions|keine (?:offenen )?stellen|currently have no open positions", lower_text):
        linked_role_titles = {
            _role_title_key(job.get("title") or "")
            for job in jobs
            if (job.get("raw_metadata") or {}).get("extraction_method") in {
                "html_job_link", "structured_job_card",
            }
        }
        jobs.extend(job for job in _heading_jobs(nodes, page_url)
                    if _role_title_key(job.get("title") or "") not in linked_role_titles)
        # A small number of employers place one student/thesis opportunity outside
        # an explicit "open positions" section. Require the role itself in a heading.
        for heading in nodes:
            if heading.tag not in {"h2", "h3", "h4", "h5"}:
                continue
            role_title = heading.text()
            if not _candidate_title(role_title):
                continue
            if _role_title_key(role_title) in linked_role_titles:
                continue
            if not re.search(r"working student|master thesis|werkstudent|praktikum", role_title, re.I):
                continue
            role_url = page_url + "#role-" + re.sub(r"[^a-z0-9]+", "-", role_title.casefold()).strip("-")[:70]
            role_candidates.append({"title": role_title, "url": role_url,
                                    "method": "html_role_heading", "confidence": "unconfirmed_role"})

    page_locations = _locations(visible_text)
    jobs = _merge(jobs)
    if len(page_locations) == 1:
        for job in jobs:
            if not job.get("locations"):
                job["locations"] = page_locations
                job["location"] = "; ".join(item["label"] for item in page_locations)
                job.setdefault("raw_metadata", {})["location_evidence"] = "career_page_context"
    # HTML that signals a next page cannot safely close jobs missing from this page.
    has_next = any((node.attrs.get("rel", "").casefold() == "next") or
                   (node.tag == "a" and re.search(r"^(?:next|weiter|nächste|ältere)", node.text(), re.I))
                   for node in nodes)
    softgarden_partial = bool(re.fullmatch(r'[a-z0-9-]+\.softgarden\.(io|de)', urlsplit(page_url).hostname or '', re.I))
    return {"jobs": jobs, "role_candidates": role_candidates, "complete": not has_next and not phenom_partial and not softgarden_partial and not microdata_partial and not bool(_DETAIL_ROUTE.search(urlsplit(page_url).path)),
            "page_title": title, "has_open_vacancy_signal": bool(re.search(r"open position|offene stellen|aktuelle jobs|current opportunities|job posting", lower_text))}
