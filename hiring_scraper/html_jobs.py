"""Conservative extraction of jobs from ordinary employer career HTML."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import date, datetime
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from .description import description_text
from .places import parse_places, title_place
from .html_tree import (  # noqa: F401 - re-exported for other modules
    _Element,
    _FORM_CONTROLS,
    _TreeParser,
    _ancestors,
    _clean_text,
    _lines,
    _visible_text,
)
from .html_facts import (  # noqa: F401 - re-exported for other modules
    _MORE_LOCATIONS,
    _TRAINING_FORMATS,
    _employment,
    _employment_fact,
    _hours_in_text,
    _place_label,
    _read_facts,
    _summary_lines,
    _title_employment,
    _title_variants,
)
from .html_titles import (  # noqa: F401 - re-exported for other modules
    _GENDER_MARKER,
    _GENERIC_HEADINGS,
    _INITIATIVE_APPLICATION,
    _NEVER_A_ROLE,
    _not_a_role,
    _STRONG_MARKER,
    _TALENT_POOL,
    _TITLE_MARKER,
    _audience_list,
    _audience_only,
    _marked_title,
    _norm_title,
    _same_role,
    _title_core,
    _title_squash,
)


_JOB_LINK = re.compile(r"(?:^|/)(?:jobs?|stellenangebote?|stellenangebot|jobangebote?|jobangebot|vacanc(?:y|ies)|open-positions|position|offers?)/[^/?#]+(?:/[^/?#]+)?/?$", re.I)
_DETAIL_ROUTE = re.compile(r"(?:^|/)(?:jobs?|stellenangebot|stellenangebote?|jobangebot|jobangebote?|vacanc(?:y|ies)|position|offers?)/[^/?#]+(?:/[^/?#]+)?/?$", re.I)
_CAREER_ROUTE = re.compile(r"karriere|careers?|jobs?|stellenangebote?|jobangebot|vacanc|open[-_/ ]positions|ausbildung|studium|studierende|werkstudent|praktikum|trainee|berufseinstieg|berufseinsteiger", re.I)
_ARTICLE_ROUTE = re.compile(r"(?:^|/)(?:news|blog|press|aktuelles|stories|posts?)(?:/|[-.]|$)", re.I)
_OPEN_SECTION = re.compile(r"^(?:open positions?|open job postings?|open roles?|offene stellen|aktuelle jobs|stellenangebote|vacatures)$", re.I)
_STOP_SECTION = re.compile(r"^(?:our next open position|our office\b|unser büro\b|our team\b|benefits\b|faq\b|continue reading\b|contact\b|kontakt\b|about us\b)", re.I)
_ROLE_WORDS = re.compile(
    r"engineer|developer|administrator|admin|assistant|techniker|technik|support|manager|lead|consultant|berater|"
    r"analyst|analysis|automation|pathologist|scientist|researcher|software|product owner|"
    r"ausbildung|fachinformatiker|werkstudent|working student|intern|praktikum|"
    r"thesis|student|studium|study|bachelor|master|graduate|apprentice|produktmanagement|product management|agronom|ux\s*/?\s*ui|"
    r"vertrieb|designer|qa\b|algorithm|data\b|cloud\b|it-|director|\b(?:a|s)?vp\b|head of|specialist|"
    r"coordinator|architect|representative|leiter(?:in)?\b|referent(?:in)?\b|sachbearbeiter",
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
_INACTIVE_CARD_STATE = re.compile(r"closed|inactive|archiv|expired|filled|template|draft|disabled", re.I)
_INACTIVE_STATE_WORDS = frozenset({"closed", "inactive", "archive", "archived", "expired", "filled", "template",
                                   "draft", "disabled"})
# Closed wording, as whole words: "position filled", but not "this position is unfilled".
_CLOSED_TEXT_WORDS = re.compile(
    r"\b(?:position|job|stelle)\b.{0,16}\b(?:closed|filled|besetzt|vergeben)\b|"
    r"\b(?:closed|filled|besetzt|vergeben)\b.{0,16}\b(?:position|job|stelle)\b", re.I)
_INACTIVE_CARD_CLASS = re.compile(
    r"(?:^|[-_])(?:closed|inactive|archiv(?:ed)?|expired|filled|template|draft|disabled)(?:$|[-_])",
    re.I,
)
_CLOSED_CARD_TEXT = re.compile(r"(?:position|job|stelle).{0,16}(?:closed|filled)|(?:closed|filled).{0,16}(?:position|job|stelle)", re.I)
_TRUE_DATA_FLAG_VALUES = frozenset({"true", "1", "yes"})


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


_COUNTRY_NAMES = {
    "germany": "DE", "deutschland": "DE", "united kingdom": "GB", "uk": "GB", "england": "GB",
    "scotland": "GB", "great britain": "GB", "ireland": "IE", "netherlands": "NL", "the netherlands": "NL",
    "niederlande": "NL", "portugal": "PT", "spain": "ES", "spanien": "ES", "france": "FR",
    "frankreich": "FR", "austria": "AT", "österreich": "AT", "switzerland": "CH", "schweiz": "CH",
    "united states": "US", "usa": "US", "us": "US", "united states of america": "US", "belgium": "BE",
    "belgien": "BE", "luxembourg": "LU", "luxemburg": "LU", "denmark": "DK", "dänemark": "DK",
    "sweden": "SE", "schweden": "SE", "norway": "NO", "norwegen": "NO", "finland": "FI",
    "finnland": "FI", "poland": "PL", "polen": "PL", "czech republic": "CZ", "czechia": "CZ",
    "tschechien": "CZ", "slovakia": "SK", "hungary": "HU", "ungarn": "HU", "romania": "RO",
    "rumänien": "RO", "bulgaria": "BG", "croatia": "HR", "slovenia": "SI", "serbia": "RS",
    "greece": "GR", "italy": "IT", "italien": "IT", "estonia": "EE", "latvia": "LV",
    "lithuania": "LT", "turkey": "TR", "türkei": "TR", "canada": "CA", "mexico": "MX",
    "méxico": "MX", "brazil": "BR", "india": "IN", "indien": "IN", "china": "CN", "japan": "JP",
    "singapore": "SG", "australia": "AU", "new zealand": "NZ", "israel": "IL",
    "united arab emirates": "AE", "south africa": "ZA",
}


_COUNTRY_LABELS = {
    "DE": "Germany", "GB": "United Kingdom", "IE": "Ireland", "NL": "Netherlands", "PT": "Portugal",
    "ES": "Spain", "FR": "France", "AT": "Austria", "CH": "Switzerland", "US": "United States",
    "BE": "Belgium", "LU": "Luxembourg", "DK": "Denmark", "SE": "Sweden", "NO": "Norway",
    "FI": "Finland", "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia", "HU": "Hungary",
    "RO": "Romania", "BG": "Bulgaria", "HR": "Croatia", "SI": "Slovenia", "RS": "Serbia",
    "GR": "Greece", "IT": "Italy", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "TR": "Turkey", "CA": "Canada", "MX": "Mexico", "BR": "Brazil", "IN": "India", "CN": "China",
    "JP": "Japan", "SG": "Singapore", "AU": "Australia", "NZ": "New Zealand", "IL": "Israel",
    "AE": "United Arab Emirates", "ZA": "South Africa",
}
_US_STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
    ("Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
     "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
     "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
     "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
     "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
     "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
     "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia"),
))


def _country_code(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("addressCountry")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.upper() if len(value) == 2 and value.isalpha() else _COUNTRY_NAMES.get(value.casefold())


def _label_country(label: str) -> str | None:
    """Country from a spelled-out trailing segment ("Brasov, Romania").

    Two-letter suffixes are ignored: "Sunnyvale, CA" is a US state, not Canada.
    """
    tail = re.split(r"[,(/]|\s[-–—]\s", label)[-1].strip(" )")
    head = re.split(r"[,(/]|\s[-–—]\s", label)[0].strip()
    return _COUNTRY_NAMES.get(tail.casefold()) or _COUNTRY_NAMES.get(head.casefold())


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
        label = _schema_place_label(address, country)
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


def _schema_place_label(address: dict, country: str | None) -> str | None:
    """City when known; otherwise "California, United States" rather than "CA"."""
    def text(value) -> str | None:
        if isinstance(value, dict):
            value = value.get("name")
        value = value.strip() if isinstance(value, str) else ""
        return value if value and value != "-" else None

    locality = text(address.get("addressLocality"))
    if locality:
        return locality
    country_name = _COUNTRY_LABELS.get(country or "") or text(address.get("addressCountry"))
    region = text(address.get("addressRegion"))
    if region and country == "US":
        region = _US_STATES.get(region.upper(), region)
    if region and country_name and region.casefold() != country_name.casefold():
        return f"{region}, {country_name}"
    return region or country_name


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
    if re.search(r"vor[- ]ort|\bon[- ]?site\b|arbeitsort", lowered):
        return "onsite", None
    return None, None


def _locations(text: str) -> list[dict]:
    found = []
    for match in re.finditer(r"\b(?:location|arbeitsort|standort|einsatzort|ort)\s*[:\-]\s*([^|\n;]{2,100})", text, re.I):
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


def _set_location(info: dict, location: str) -> None:
    info["location"] = location
    info["locations"] = []
    for place in location.split(";"):
        place = place.strip()
        if not place:
            continue
        country = _label_country(place)
        info["locations"].append({"label": place, **({"country_code": country} if country else {})})


def _job(title: str, url: str, method: str, text: str = "", *, description: str | None = None,
         location: str | None = None, employment_type: str | None = None,
         date_posted: str | None = None, application_email: str | None = None,
         extras: dict | None = None, infer_employment: bool = True) -> dict:
    info = _metadata(text, method, application_email=application_email)
    if not infer_employment:
        # The caller read the employment type from facts; running text would only add guesses.
        info["employment_type"] = None
    if location:
        _set_location(info, location)
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
               description=description_text(posting.get("description")), date_posted=posting.get("datePosted"),
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


def main_reading(jobs: list[dict]) -> list[dict]:
    """The postings main's strategies read. Postings the marked-title strategy adds never
    change decisions main makes from a page (completeness, browser rendering)."""
    return [job for job in jobs if (job.get("raw_metadata") or {}).get("extraction_method") != "html_marked_title"]


def _main_candidate_title(value: str) -> bool:
    """main's role test, kept for decisions that existing rows depend on (section boundaries)."""
    clean = _clean_text(value).strip(" ·|–—-:")
    return bool(clean and len(clean) <= 150 and clean.casefold() not in _GENERIC_HEADINGS
                and not re.match(r"^(?:spezialfragen|faq|häufige fragen|frequently asked questions?)\b", clean, re.I)
                and not re.match(r"^(?:stellenangebote|aktuelle stellenangebote|jobs|vacancies|open positions)\s+(?:für|for|in|at)\b", clean, re.I)
                and not re.fullmatch(r"(?:internal|all|current|other|more|interne|alle|weitere)\s+(?:vacancies|jobs|positions|openings|stellen(?:angebote)?)", clean, re.I)
                and _ROLE_WORDS.search(clean))


def _candidate_title(value: str) -> bool:
    """main's role test, minus text that is never a vacancy (SEO and category links, result
    totals, controls, section headings, audience lists). Facet counts or bare category words,
    which can title a real posting, stay as main reads them."""
    clean = _clean_text(value).strip(" ·|–—-:")
    return bool(clean and len(clean) <= 150 and clean.casefold() not in _GENERIC_HEADINGS
                and not _audience_list(clean) and not _NEVER_A_ROLE.search(clean)
                and not re.match(r"^(?:spezialfragen|faq|häufige fragen|frequently asked questions?)\b", clean, re.I)
                and not re.match(r"^(?:stellenangebote|aktuelle stellenangebote|jobs|vacancies|open positions)\s+(?:für|for|in|at)\b", clean, re.I)
                and not re.fullmatch(r"(?:internal|all|current|other|more|interne|alle|weitere)\s+(?:vacancies|jobs|positions|openings|stellen(?:angebote)?)", clean, re.I)
                and _ROLE_WORDS.search(clean))


def _application_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    return match.group(0) if match else None


def _card(node: _Element, title: str) -> _Element | None:
    """The nearest ancestor that describes this vacancy and no other one."""
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
            return parent
    return None


def _context(node: _Element, title: str) -> str:
    card = _card(node, title)
    return card.text() if card is not None else title


_LOCATION_MARKER = re.compile(
    r"location|standort|arbeitsort|einsatzort|(?:^|[^a-z])(?:ort|city|stadt|street|address|place)(?:$|[^a-z])"
)
_LOCATION_LABEL = re.compile(
    r"^(?:(?:job|work|primary|office)\s+)?(?:locations?|standorte?|arbeitsort|einsatzort|dienstort|ort)\b\s*[:\-–]?\s*",
    re.I,
)


def _node_location(node: _Element, *, exclude: str | None = None) -> str | None:
    for child in node.walk():
        if child.hidden:
            continue
        marker = " ".join((child.attrs.get("class", ""), child.attrs.get("id", ""),
                           child.attrs.get("itemprop", ""), child.attrs.get("data-testid", ""),
                           child.attrs.get("data-automation-id", ""), child.attrs.get("data-id", ""))).casefold()
        if not _LOCATION_MARKER.search(marker):
            continue
        text = _visible_text(child)
        if exclude and exclude.casefold() in text.casefold():
            continue
        value = _LOCATION_LABEL.sub("", text)
        value = re.sub(r"^(?:in|am standort|standort)\s+", "", value, flags=re.I).strip(" ·|,–—-")
        value = re.sub(r"\s+(?:details|mehr erfahren|apply|jetzt bewerben)$", "", value, flags=re.I).strip()
        value = _MORE_LOCATIONS.sub("", value).strip(" ·|,–—-")
        if value and len(value) <= 100 and not _LOCATION_LABEL.fullmatch(value + " "):
            return value
    return None


_PLACE_VALUE = re.compile(
    r"[A-ZÄÖÜÉ][\w.'’ -]{0,40}(?:\s*[,/]\s*[A-ZÄÖÜÉ0-9][\w.'’ -]{0,40}){0,3}"
)


def _labeled_locations(nodes: list[_Element]) -> list[str]:
    """Explicit "Location: Brasov, Romania" lines or Standort/value term pairs."""
    found: list[str] = []
    for node in nodes:
        if node.hidden or node.tag not in {"p", "li", "dd", "dt", "div", "span", "td", "th"}:
            continue
        text = node.text()
        if not text or len(text) > 140:
            continue
        match = _LOCATION_LABEL.match(text)
        if not match:
            continue
        value = text[match.end():]
        if not value and node.tag in {"dt", "th"}:
            sibling = _next_sibling(node)
            value = sibling.text() if sibling is not None else ""
        value = re.split(r"\s*[(\[]|\s+[-–—|]\s+|;", value, maxsplit=1)[0].strip(" .,:")
        if value and _PLACE_VALUE.fullmatch(value) and value not in found:
            found.append(value)
    # A wrapper block can run the label into the next line ("Brasov Travel");
    # keep the tightest reading when one value extends another.
    return [value for value in found if not any(other != value and value.startswith(other) for other in found)]


def _next_sibling(node: _Element) -> _Element | None:
    if node.parent is None:
        return None
    siblings = [child for child in node.parent.children if isinstance(child, _Element)]
    index = siblings.index(node)
    return siblings[index + 1] if index + 1 < len(siblings) else None


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


def _inactive_card_class(classes: set[str], *, strict: bool = False) -> bool:
    # Theme layout classes ("wp-block-post-template", "section-template--…") are scaffolding,
    # not a template posting.
    return any(
        not name.startswith(("not-", "not_")) and
        _INACTIVE_CARD_CLASS.search(re.sub(r"(?:post|block|section|page|site|theme)-template", "-", name)
                                    if strict else name)
        for name in classes
    )


def _inactive_card(card: _Element, *, strict: bool = False) -> bool:
    """A card a site keeps in its markup but marks closed, disabled or as a template.

    ``strict`` (the paths new in this reader) matches whole state words, so
    "unfilled" is not "filled", and ignores theme layout classes such as
    "wp-block-post-template"; the structured-card reader keeps main's test.
    """
    state = " ".join(card.attrs.get(key, "") for key in ("data-status", "data-state", "status"))
    classes = set(card.attrs.get("class", "").casefold().split())
    state_closed = (any(token in _INACTIVE_STATE_WORDS for token in re.split(r"[\s,;|]+", state.casefold()))
                    if strict else _INACTIVE_CARD_STATE.search(state))
    return bool(card.hidden or card.attrs.get("aria-disabled", "").casefold() == "true" or
                "disabled" in card.attrs or _inactive_card_class(classes, strict=strict) or
                _true_data_flag(card.attrs, "data-disabled") or _true_data_flag(card.attrs, "data-template") or
                state_closed)


def _destination_key(url: str) -> str:
    """One posting destination: scheme/host/path without trailing slash, sorted query, no fragment."""
    parts = urlsplit(_normalized_destination(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", parts.query, ""))


def _link_key(url: str) -> str:
    """A link's identity: its destination, plus a hash route ("#/jobs/12") that names a page."""
    fragment = urlsplit(url).fragment
    return _destination_key(url) + (f"#{fragment}" if fragment.startswith(("/", "!/")) else "")


# Query parameters that name one posting rather than filter a list: ?jobId=123, ?id=7.
_POSTING_QUERY = re.compile(
    # Explicit posting parameters name a vacancy whatever their value (?jobId=abc) …
    r"(?:^|&)(?:job_?id|jobid|vacancy_?id|posting_?id|stellen_?id|req(?:uisition)?_?id|offer_?id|"
    r"ausschreibungs?_?id|anzeigen_?id|job_?ref|jobnr|stellennr)=[^&]+|"
    # … generic ones only with a number in the value ("?id=7", but "?ref=lager" filters).
    r"(?:^|&)(?:id|vacancy|posting|stelle|ref|offer|ausschreibung|anzeige|job)=[^&]*\d[^&]*", re.I)
# Parameters that only filter or sort a list: a link carrying nothing else is navigation.
_FILTER_QUERY = re.compile(
    r"^(?:ref|category|kategorie|filter|bereich|q|query|search|suche|s|type|typ|location|standort|ort|city|"
    r"department|abteilung|sort|order|page|seite|lang|sprache|utm_\w+|jobtype|jobprofile|profession)$", re.I)


def _structured_job_cards(nodes: list[_Element], page_url: str) -> tuple[list[dict], list[dict]]:
    """Read explicit, server-rendered vacancy cards whose detail URLs use a query string."""
    jobs, candidates = [], []
    for card in nodes:
        if card.tag != "a" or card.attrs.get("data-guide-id", "").casefold() != "joblist-card":
            continue
        if _inactive_card(card):
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
    if method == "html_marked_title" and "#role-" not in url:
        # Like the posting's own detail page, unless a query or hash route names the vacancy.
        key = _link_key(url)
        if parsed and _DETAIL_ROUTE.search(path) and not _POSTING_QUERY.search(parsed.query) and "#" not in key:
            return "url", (parsed.hostname or "").casefold(), path.casefold()
        return "destination", key
    if (parsed and method in {"html_job_link", "html_job_detail", "html_marked_title"} and _DETAIL_ROUTE.search(path)):
        return "url", (parsed.hostname or "").casefold(), path.casefold()
    title = re.sub(r"\W+", " ", (job.get("title") or "").casefold()).strip()
    locations = sorted(item.get("label", "").casefold() for item in job.get("locations", [])
                       if isinstance(item, dict) and item.get("label"))
    return "title", title, ";".join(locations)


def _role_title_key(value: str) -> str:
    """Normalize a role label so linked cards suppress duplicate heading hints."""
    _, title = _location_from_title(value)
    return re.sub(r"\W+", " ", _clean_text(title).casefold()).strip()


_CONTACT_HEADING = re.compile(
    r"^(?:kontakt|contact|ansprechpartner(?:in)?|ihr(?:e)? ansprechpartner(?:in)?|dein(?:e)? ansprechpartner(?:in)?|"
    r"your contact(?: person)?|contact person|recruiting contact)$",
    re.I,
)


def _detail_scope(heading: _Element) -> _Element | None:
    # The nearest substantive ancestor bounds the role, keeping earlier biographies
    # and neighboring columns out.
    return next((parent for parent in _ancestors(heading)
                 if parent.tag not in {"body", "html", "document"} and
                 len(parent.text()) > len(heading.text()) + (16 if parent.tag in {"main", "article"} else 80)), None)


def _fact_label(node: _Element) -> str | None:
    """A short leaf label ("Vollzeit") in an overview made of icons and spans."""
    if node.hidden or node.tag not in {"span", "div", "dd"}:
        return None
    if any(isinstance(child, _Element) and child.text() for child in node.children):
        return None
    if any(parent.tag in {"a", "button", "label", "p", "li"} for parent in _ancestors(node)):
        return None
    text = node.text().strip(" ·|,–—-")
    return text if text and len(text) <= 60 else None


def _detail_sections(heading: _Element) -> tuple[str | None, list[str], str]:
    """Description after the confirmed title, overview facts, and contact-block text.

    A section built only from short labels (an "At a glance" row of icons) keeps
    them as bullets. The contact block names a recruiter rather than describing
    the role, so it ends the description but stays readable for an application
    address.
    """
    scope = _detail_scope(heading)
    if scope is None:
        return None, [], ""
    active, parts, taken = False, [], set()
    facts: list[str] = []
    section_facts: list[str] | None = None
    contact: list[str] | None = None

    def flush_facts():
        if section_facts:
            facts.extend(section_facts[:8])
            parts.extend("- " + fact for fact in section_facts[:8])

    for node in scope.walk():
        if node is heading:
            active = True
            continue
        if not active or node.hidden:
            continue
        if contact is not None:
            if node.tag in {"p", "li"} and node.text():
                contact.append(node.text())
            continue
        if section_facts is not None and node.tag not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            fact = _fact_label(node)
            if fact and fact not in section_facts:
                section_facts.append(fact)
        if node.tag not in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            continue
        # A paragraph inside a list item is already part of that item.
        if any(id(parent) in taken for parent in _ancestors(node)):
            continue
        taken.add(id(node))
        text = node.text()
        if node.tag in {"p", "li"}:
            section_facts = None
        else:
            flush_facts()
            section_facts = []
        if (node.tag in {"h2", "h3", "h4", "h5", "h6"} and
                re.fullmatch(r"our team|unser team|meet (?:our )?team|staff members", text.strip(" :·–—-"), re.I)):
            section_facts = None
            break
        if node.tag in {"h2", "h3", "h4", "h5", "h6"} and _CONTACT_HEADING.match(text.strip(" :·–—-")):
            section_facts, contact = None, []
            continue
        if re.match(r"^(?:why join us|meet (?:EMBO )?staff|related (?:jobs|content)|other (?:jobs|vacancies))\b", text, re.I):
            section_facts = None
            break
        if node.tag == "h1":
            section_facts = None
            break
        if text:
            parts.append(("## " if node.tag != "p" and node.tag != "li" else "- " if node.tag == "li" else "") + text)
    flush_facts()
    # Drop a heading left with nothing under it.
    parts = [part for index, part in enumerate(parts)
             if not (part.startswith("## ") and (index + 1 == len(parts) or parts[index + 1].startswith("## ")))]
    # Keep bullets of one list together; separate every other block.
    joined = ""
    for index, part in enumerate(parts):
        if index:
            joined += "\n" if part.startswith("- ") and parts[index - 1].startswith("- ") else "\n\n"
        joined += part
    return joined[:12000] or None, facts, " ".join(contact or [])


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
        # Where a section ends is main's decision; whether a heading is emitted is the stricter
        # one ("Praktikum" between two roles is skipped without ending the section).
        section_role = _main_candidate_title(title)
        role_title = _candidate_title(title)
        if active and found_in_section and int(heading.tag[1]) <= section_level and not section_role:
            stopped = True
        if not active or stopped or not section_role:
            continue
        found_in_section = True
        if not role_title:
            continue
        context = _context(heading, title)
        email = _application_email(context)
        url = page_url + "#role-" + re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:70]
        card = _card(heading, title)
        card_lines = [line for line in (_lines(card) if card is not None else [])
                      if title.casefold() not in line.casefold()]
        labeled_place, card_place, card_employment = _read_facts(card_lines, unlabeled_lines=6)
        job = _job(title, url, "open_positions_heading", context,
                   description=context if len(context) > len(title) + 35 else None,
                   location=labeled_place or title_place(title) or card_place,
                   employment_type=_title_employment(title) or card_employment or _hours_in_text(" ".join(card_lines)),
                   infer_employment=False, application_email=email)
        jobs.append(job)
    return jobs


_TITLE_TAGS = frozenset({"a", "b", "dd", "div", "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p",
                         "span", "strong", "td", "th"})
_NOT_POSTING_HOSTS = re.compile(r"(?:^|\.)(?:facebook|instagram|linkedin|xing|twitter|x|youtube|kununu|google|"
                                r"whatsapp|tiktok)\.", re.I)


def _marked_title_jobs(root: _Element, page_url: str, existing: list[dict], *, detail_page: bool,
                       vacancy_page: bool = False) -> list[dict]:
    texts: dict[int, str] = {}

    def text_of(node: _Element) -> str:
        # Bottom-up once: _Element.text() recomputes its subtree on every call.
        if id(node) not in texts:
            if node.hidden or node.tag in _FORM_CONTROLS:
                texts[id(node)] = ""
            else:
                parts = [text_of(child) if isinstance(child, _Element) else child for child in node.children]
                texts[id(node)] = _clean_text(" ".join(part for part in parts if part and part.strip()))
        return texts[id(node)]

    page = _destination_key(page_url)
    # Rows other strategies read without a URL of their own (a heading on this page,
    # a structured row falling back to the page) can match any link.
    unlocated = lambda job: ("#role-" in (job.get("url") or "") or _destination_key(job.get("url") or page_url) == page or
                             bool((job.get("raw_metadata") or {}).get("microdata_page_fallback")))
    # By link key, so a row main read for "/jobs/list#/jobs/12" keeps standing for that route.
    seen_urls = {_link_key(job.get("url") or "") for job in existing if not unlocated(job)}
    seen_titles = {_norm_title(job.get("title") or "") for job in existing if unlocated(job)}
    # Titles already read, indexed by word and spelling, and partitioned by destination:
    # a linked title is only compared with rows for its own destination or rows read
    # without one, so a list of 1,600 postings (or 40 "Rettungssanitäter" in 40 towns)
    # stays near-linear.
    taken: list[tuple[frozenset[str], str]] = []
    synthetic: set[int] = set()  # taken indexes of existing heading rows with no URL of their own
    by_word: dict[str, list[int]] = {}
    by_squash: dict[str, list[int]] = {}
    unlinked_words: dict[str, list[int]] = {}
    unlinked_squash: dict[str, list[int]] = {}
    by_destination: dict[str, list[int]] = {}

    def remember(title: str, url: str, job: dict | None = None) -> None:
        core, squash = _title_core(title), _title_squash(title)
        index = len(taken)
        taken.append((core, url))
        words, spellings = (unlinked_words, unlinked_squash) if url == "*" else (by_word, by_squash)
        for word in core:
            words.setdefault(word, []).append(index)
            if url == "*":
                by_word.setdefault(word, []).append(index)
        if squash:
            spellings.setdefault(squash, []).append(index)
            if url == "*":
                by_squash.setdefault(squash, []).append(index)
        if url != "*":
            by_destination.setdefault(url, []).append(index)
        if job is not None and "#role-" in (job.get("url") or ""):
            synthetic.add(index)

    def repeats(title: str, target: str | None) -> list[int]:
        """Rows this title repeats (same role; for a link, same destination or no URL).

        A linked title is compared strictly with rows read without a URL: "Senior
        Cloud Engineer" on its own link does not repeat a bare "Cloud Engineer".
        """
        core, squash = _title_core(title), _title_squash(title)
        if target:
            spelled = set(unlinked_squash.get(squash, ())) if squash else set()
            pool = spelled.union(*(unlinked_words.get(word, ()) for word in core), by_destination.get(target, ()))
        else:
            spelled = set(by_squash.get(squash, ())) if squash else set()
            pool = spelled.union(*(by_word.get(word, ()) for word in core))

        def same(index: int) -> bool:
            other, url = taken[index]
            if bool(target) != (url != "*"):
                # A linked and an unlinked title are one posting only when they name the same role
                # exactly; "Cloud Engineer" and "Senior Cloud Engineer" are two postings.
                return bool(core and other) and core == other
            return index in spelled or _same_role(core, other)

        return [index for index in pool if index not in replaced and same(index)]

    for job in existing:
        remember(job.get("title") or "", "*" if unlocated(job) else _link_key(job.get("url") or ""), job)
    replaced: set[int] = set()  # heading rows already standing for one link; no longer compared
    candidates: list[tuple] = []
    jobs: list[dict] = []
    text_of(root)

    def title_line(node: _Element) -> str | None:
        """The marked title among an element's lines ("… (m/w/d)" plus the line before a lone marker)."""
        text = texts.get(id(node), "")
        if not text or len(text) > 400 or not _TITLE_MARKER.search(text):
            return None
        lines = _lines(node)
        for index, line in enumerate(lines):
            if not _TITLE_MARKER.search(line):
                continue
            if index and not re.sub(_TITLE_MARKER, "", line).strip(" ()|/-"):
                line = f"{lines[index - 1]} {line}"
            if _marked_title(line):
                return line
        return None

    titled = {id(node): line for node in root.walk()
              if not node.hidden and node.tag in _TITLE_TAGS and (line := title_line(node))}
    nodes_by_id = {id(node): node for node in root.walk()}
    leaves = {id(node) for node in root.walk()
              if id(node) in titled and not any(id(child) in titled for child in node.walk() if child is not node)}

    def heading_like(node: _Element) -> bool:
        marker = " ".join((node.attrs.get("class", ""), node.attrs.get("itemprop", ""))).casefold()
        return bool(re.fullmatch(r"h[1-6]", node.tag) or "title" in marker or "titel" in marker)

    headings = {leaf for leaf in leaves if heading_like(nodes_by_id[leaf])}
    # The page's own title: the title inside its first marked <h1> (even when wrapped,
    # "<h1><span>…</span></h1>"), or its first marked heading when it has no <h1>.
    marked_h1 = next((node for node in root.walk() if node.tag == "h1" and id(node) in titled), None)
    if marked_h1 is not None:
        main_heading = next((node for node in marked_h1.walk() if id(node) in leaves), marked_h1)
    else:
        main_heading = next((node for node in root.walk() if id(node) in headings and re.fullmatch(r"h[1-6]", node.tag)),
                            None)
    tag = object()

    def in_link(node: _Element) -> bool:
        return node.tag == "a" or any(parent.tag == "a" for parent in list(_ancestors(node))[:4])

    def tag_like(index: int) -> bool:
        """A marked line the page styles as a tag ("<span class=tag>Gastronomie (m/w/d)</span>")."""
        node = nodes_by_id[index]
        if index in headings or in_link(node):
            return False
        marker = " ".join(element.attrs.get("class", "") for element in (node, node.parent) if element is not None)
        return bool(re.search(r"(?:^|[\s_-])(?:tags?|badges?|chips?|pills?|labels?|categor(?:y|ies)|kategorien?)"
                              r"(?:$|[\s_-])", marker, re.I))

    def own_card(node: _Element, title: str):
        """The nearest container describing this posting and no other (cached text, bounded walk).

        It stops below any container holding another posting's title, so a heading
        never borrows a neighbouring card's link. A line styled as a tag inside a
        card whose single link it shares with the card's heading yields ``tag``.
        """
        card = None
        for parent in list(_ancestors(node))[:8]:
            text = texts.get(id(parent), "")
            if len(text) > 2200:
                break
            others = [id(child) for child in parent.walk() if child is not node and id(child) in leaves]
            if others:
                hrefs = {link.attrs.get("href") for link in parent.walk()
                         if link.tag == "a" and link.attrs.get("href", "").strip() not in {"", "#"}}
                if tag_like(id(node)) and len(hrefs) == 1 and any(other in headings for other in others):
                    return tag
                break
            if len(text) > len(title) + 2:
                card = parent
        return card

    for node in root.walk():
        if id(node) not in leaves:
            continue
        title = titled[id(node)]
        if any(parent.tag in {"title", "option", "select", "label", "form", "script", "textarea"}
               for parent in _ancestors(node)):
            continue
        title = re.sub(r"\s+(?:mehr erfahren|details|zur stelle|jetzt bewerben|weiterlesen|→|»)$", "", title, flags=re.I)
        title = re.sub(r"^(?:wanted|neu|new|top|stellenangebot|stellenanzeige|job)\s*[:!]\s*", "", title, flags=re.I)
        # A slogan after the role: "Fliesenleger (m/w/d) – Du rockst Baustellen wie ein Profi!"
        title = re.split(r"\s+[–—-]\s+(?=(?:du|sie|wir|dein|deine|ihr|ihre)\b)", title, maxsplit=1, flags=re.I)[0]
        title = title.strip(" ·|–—-:.")
        if not _marked_title(title):
            continue
        anchor = node if node.tag == "a" else next((parent for parent in list(_ancestors(node))[:4] if parent.tag == "a"), None)
        card = own_card(node, title)
        if card is tag:
            continue
        if card is not None and any(
                element is not node and heading_like(element) and element.text() and
                not any(parent is node for parent in _ancestors(element)) and
                not any(parent is element for parent in _ancestors(node))
                for element in card.walk()):
            # Another (unmarked) heading in the container: a neighbouring card, not this one's.
            card = None
        # Only the posting's own elements: page sections carry classes like "section-template--…".
        own = [node]
        stop = card if card is not None else (anchor if anchor is not node else None)
        for depth, parent in enumerate(_ancestors(node)):
            if stop is None and depth >= 2:
                break  # without a card, the title's own wrapper ("<div data-status=closed>")
            own.append(parent)
            if parent is stop:
                break
        if anchor is None and card is not None:
            links = {link.attrs.get("href") for link in card.walk() if link.tag == "a" and link.attrs.get("href")}
            if len(links) == 1:
                anchor = next(link for link in card.walk() if link.tag == "a" and link.attrs.get("href"))
        if any(_inactive_card(element, strict=True) for element in (*own, *([anchor] if anchor is not None else []))) or (
                card is not None and _CLOSED_TEXT_WORDS.search(texts.get(id(card), ""))):
            continue
        href = _absolute_http(anchor.attrs.get("href", ""), page_url) if anchor is not None else None
        query = urlsplit(href).query if href else ""
        if query and not _POSTING_QUERY.search(query) and (
                (urlsplit(href).netloc, urlsplit(href).path.rstrip("/")) ==
                (urlsplit(page_url).netloc, urlsplit(page_url).path.rstrip("/")) or
                all(_FILTER_QUERY.match(name) for name, _ in parse_qsl(query, keep_blank_values=True))):
            # A list with another filter ("Prophylaxe (w/m/d)" → ?jobType=13, "?ref=lager") is
            # navigation; ?jobId=123 names a posting.
            continue
        target = _link_key(href) if href else None
        if target and (_NOT_POSTING_HOSTS.search(urlsplit(href).hostname or "") or target == page):
            href = target = None
        if vacancy_page and node is main_heading:
            # /careers?jobId=123 is this vacancy's own page: its title is saved under the page
            # URL, never under a nearby "Apply" link.
            href, target = page_url, _link_key(page_url)
        if (detail_page or not _STRONG_MARKER.search(title)) and not target:
            # On a posting's own page, unlinked mentions repeat its title in prose or requirements;
            # an unlinked "Kolleg*in" form is too often a benefit or a sentence.
            continue
        rank = 2 if heading_like(node) else 1
        candidates.append((node, title, target, card, rank, href))

    # One link, one posting: a card's category tag ("Prophylaxe (w/m/d)") yields to its title.
    best: dict[str, tuple] = {}
    for candidate in candidates:
        target = candidate[2]
        if target and (target not in best or (candidate[4], len(candidate[1])) > (best[target][4], len(best[target][1]))):
            best[target] = candidate
    # Linked titles first: a bare heading repeating one ("Laminierer (m/w/d)" over two
    # linked "Laminierer" cards) yields to the postings that have their own destination.
    ordered = sorted(enumerate(candidates), key=lambda item: item[1][2] is None)
    placed: list[tuple[int, dict]] = []
    for order, (node, title, target, card, _, href) in ordered:
        if target and best[target][0] is not node:
            continue
        # The job keeps the link as written; ``target`` is only its identity.
        url = href or page_url + "#role-" + re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:70]
        key = _norm_title(title)
        # Two links to /vacancy?id=123 and ?id=124 are two postings even with one title.
        if (target and target in seen_urls and not (vacancy_page and node is main_heading and target == page)) or (
                not target and key in seen_titles):
            continue
        # A second heading for a posting already read (its card title, a subtitle, a repeat,
        # or a row read without its own URL, like a structured row falling back to the page).
        matches = repeats(title, target)
        if matches and target and all(index in synthetic for index in matches):
            # A heading row another strategy already read is this posting: it keeps its id
            # (and its Journey history). It stands for one link only, so a second identical
            # title linking elsewhere (?id=123, ?id=124) is a distinct posting.
            replaced.add(matches[0])
            continue
        if matches:
            continue
        seen_titles.add(key)
        remember(title, target or "*")
        if target:
            seen_urls.add(target)
        # The title may share a line with its facts ("Laminierer (m/w/d) Bonn"): keep the rest.
        card_lines = [rest for line in (_lines(card) if card is not None else [])
                      if (rest := line.replace(title, " ").strip(" ·|,–—-"))]
        labeled_place, card_place, card_employment = _read_facts(card_lines, unlabeled_lines=8)
        context = texts.get(id(card), title) if card is not None else title
        job = _job(title, url, "html_marked_title", context,
                   location=labeled_place or title_place(title) or card_place,
                   employment_type=_title_employment(title) or card_employment, infer_employment=False,
                   application_email=_application_email(context))
        if target:
            # The destination names the posting, including identity-bearing query parameters
            # and hash routes ("#/jobs/12").
            job["id"] = hashlib.sha1(target.encode()).hexdigest()[:20]
        placed.append((order, job))
    jobs = [job for _, job in sorted(placed, key=lambda item: item[0])]
    return jobs


_STRUCTURED_METHODS = frozenset({"schema_org_jobposting", "schema_org_microdata"})
_DETAIL_METHODS = frozenset({"html_job_detail", "softgarden_job_detail"})


def _posting_alias(job: dict) -> tuple[str, ...] | None:
    """Same page and title: a structured row and the page's own HTML reading."""
    try:
        parts = urlsplit(job.get("url") or "")
    except ValueError:
        return None
    if not parts.hostname:
        return None
    title = re.sub(r"\W+", " ", (job.get("title") or "").casefold()).strip()
    return parts.hostname.casefold(), parts.path.rstrip("/").casefold(), title


def _merge(jobs: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    keys_by_id: dict[str, tuple] = {}
    # One alias per side, so two structured postings (or two detail readings)
    # that happen to share a page and title are never collapsed together.
    structured_keys: dict[tuple, tuple] = {}
    detail_keys: dict[tuple, tuple] = {}
    for job in jobs:
        key = html_job_key(job)
        method = (job.get("raw_metadata") or {}).get("extraction_method")
        alias = _posting_alias(job)
        # The feed worker upserts by id, so two rows with one id would let the
        # later (often poorer) row overwrite the first. Treat them as one posting.
        if key not in merged and str(job.get("id") or "") in keys_by_id:
            key = keys_by_id[str(job["id"])]
        elif key not in merged and alias and method in _DETAIL_METHODS and alias in structured_keys:
            key = structured_keys[alias]
        elif key not in merged and alias and method in _STRUCTURED_METHODS and alias in detail_keys:
            key = detail_keys[alias]
        existing = merged.get(key)
        if existing is None:
            merged[key] = job
            if job.get("id"):
                keys_by_id.setdefault(str(job["id"]), key)
            if alias and method in _STRUCTURED_METHODS:
                structured_keys.setdefault(alias, key)
            elif alias and method in _DETAIL_METHODS:
                detail_keys.setdefault(alias, key)
            continue
        old_method = (existing.get("raw_metadata") or {}).get("extraction_method")
        new_method = (job.get("raw_metadata") or {}).get("extraction_method")
        if new_method in _STRUCTURED_METHODS and old_method in _DETAIL_METHODS:
            # Structured data carries the source's own id and address fields.
            # The employer's own description beats a scrape that includes page chrome.
            for field in ("id", "locations", "location", "description"):
                if job.get(field):
                    existing[field] = job[field]
            existing["raw_metadata"] = {**(existing.get("raw_metadata") or {}),
                                        **(job.get("raw_metadata") or {})}
        elif new_method in _DETAIL_METHODS and old_method in _STRUCTURED_METHODS:
            existing["raw_metadata"] = {**(job.get("raw_metadata") or {}),
                                        **(existing.get("raw_metadata") or {})}
            # The record's "FULL_TIME" states hours; a title's "Ausbildung" states the format.
            if (job.get("employment_type") in _TRAINING_FORMATS and
                    existing.get("employment_type") not in _TRAINING_FORMATS):
                existing["employment_type"] = job["employment_type"]
            job = {field: value for field, value in job.items()
                   if field != "raw_metadata" and not (field == "description" and existing.get("description"))}
        elif new_method == "html_job_detail" and old_method != "html_job_detail":
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
        if old_method in _STRUCTURED_METHODS:
            existing.setdefault("raw_metadata", {})["extraction_method"] = old_method
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
                    continue
                # "Regensburg, DE" without a postal code: accept it when it names a real place.
                street = str(address.get('streetAddress', ''))
                found = parse_places(street)
                if len(found) == 1 and not re.search(r'\d', street):
                    country = re.search(r',\s*([A-Z]{2})$', street)
                    address.update(addressLocality=found[0], **({'addressCountry': country[1]} if country else {}))
        job = _schema_posting({**posting, 'url': target}, page_url)
        if job:
            if not posting.get('identifier'):
                job['id'] = _stable_id(title, job['url'], destination_identity=bool(explicit_url))
            job['raw_metadata']['extraction_method'] = 'schema_org_microdata'
            job['raw_metadata']['microdata_page_fallback'] = not bool(explicit_url)
            jobs.append(job)
    return jobs, bool(scopes)




# Explicit page wording needs no particular URL: "Weiter zu Seite 2", "Nächste Seite", "Go to page 3".
_PAGE_WORDING = re.compile(
    r"^(?:[‹«<←]\s*)?(?:weiter|vor|nächste|next|go)\b[\w ]{0,12}?\b(?:seite|page)\b|^(?:seite|page)\s+\d+\s*[›»>→]", re.I)
_MORE_POSTINGS = re.compile(
    r"^(?:next|nächste|weitere|more|mehr)\s+(?:\d+\s+)?(?:stellen|stellenangebote|jobs|ergebnisse|treffer|"
    r"einträge|results|positions|vacancies|openings|postings)\b", re.I)


def _next_page_link(node: _Element) -> bool:
    """A link to more of this list. main's rule ("Next", "Weiter", "Nächste", "Ältere", rel=next)
    plus explicit page wording ("Weiter zu Seite 2", "Next 20 vacancies") and a rel token
    ("nofollow next"): a page main reads as partial is never read as complete."""
    if "next" in node.attrs.get("rel", "").casefold().split():
        return True
    if node.tag != "a":
        return False
    text = node.text()
    if re.search(r"^(?:next|weiter|nächste|ältere)", text, re.I):
        return True
    labels = (_clean_text(text), node.attrs.get("aria-label", "").strip(), node.attrs.get("title", "").strip())
    return any(_PAGE_WORDING.search(label) for label in labels) or bool(_MORE_POSTINGS.match(labels[0]))


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
    failures: list[str] = []
    phenom_partial = microdata_partial = False
    microdata_jobs: list[dict] = []
    try:
        phenom_jobs, phenom_partial = _phenom_public_jobs(nodes, page_url)
        jobs.extend(phenom_jobs)
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("phenom: " + type(error).__name__)
    try:
        microdata_jobs, microdata_partial = _microdata_jobs(nodes, page_url)
        jobs.extend(microdata_jobs)
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("microdata: " + type(error).__name__)

    try:
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
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("embedded_json: " + type(error).__name__)

    try:
        structured_jobs, structured_candidates = _structured_job_cards(nodes, page_url)
        jobs.extend(structured_jobs)
        role_candidates.extend(structured_candidates)
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("job_cards: " + type(error).__name__)

    try:
        anchors = [node for node in nodes if node.tag == "a" and node.attrs.get("href")]
        for anchor in anchors:
            target = _absolute_http(anchor.attrs["href"], page_url)
            if not target or urlsplit(target).hostname != urlsplit(page_url).hostname:
                continue
            path = urlsplit(target).path
            softgarden_id = _softgarden_job_id(target)
            # Numeric `/job/<id>/<slug>` routes are a Softgarden contract. Other
            # sites use them for unrelated pages, so only accept those routes on a
            # recognized Softgarden tenant.
            softgarden_only_route = bool(re.match(r'^/job/[^/]+/[^/]+/?$', path, re.I))
            if softgarden_only_route and not softgarden_id:
                continue
            if not _JOB_LINK.search(path) and not softgarden_id:
                continue
            title_text = _job_link_title(anchor)
            valid_vendor_title = (softgarden_id and title_text and len(title_text) <= 150 and
                                  title_text.casefold() not in _GENERIC_HEADINGS and
                                  not _INITIATIVE_APPLICATION.search(title_text) and not _TALENT_POOL.search(title_text))
            # main's checks, in main's order (its card lookup can fail and mark the scan partial);
            # never-a-posting titles are dropped only afterwards.
            if not _main_candidate_title(title_text) and not valid_vendor_title:
                continue
            card = _card(anchor, title_text)
            if not _candidate_title(title_text) and not valid_vendor_title:
                continue
            context = card.text() if card is not None else title_text
            title_location, title_text = _location_from_title(title_text)
            card_lines = [line for line in (_lines(card) if card is not None else [])
                          if title_text.casefold() not in line.casefold()]
            labeled_place, card_place, card_employment = _read_facts(card_lines, unlabeled_lines=8)
            marked = _place_label(_node_location(anchor)) or (
                _place_label(_node_location(card, exclude=title_text)) if card is not None else None)
            location = (labeled_place or marked or title_place(title_text) or
                        (_place_label(title_location) if title_location else None) or card_place or
                        _location_from_url(target))
            employment = (_title_employment(title_text) or card_employment or
                          _hours_in_text(" ".join(card_lines)))
            job = _job(title_text, target, "softgarden_job_link" if softgarden_id else "html_job_link", context,
                       location=location, employment_type=employment, infer_employment=False,
                       application_email=_application_email(context))
            if softgarden_id:
                job['id'] = softgarden_id
            jobs.append(job)
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("job_links: " + type(error).__name__)

    try:
        path = urlsplit(page_url).path
        softgarden_id = _softgarden_job_id(page_url)
        # Structured data for this very page is the employer's own record; the
        # heading/paragraph reading below would only duplicate it, less precisely.
        page_key = (urlsplit(page_url).hostname or "").casefold(), urlsplit(page_url).path.rstrip("/").casefold()
        structured_page = any(
            (job.get("raw_metadata") or {}).get("extraction_method") in _STRUCTURED_METHODS and
            job.get("description") and
            ((urlsplit(job.get("url") or "").hostname or "").casefold(),
             urlsplit(job.get("url") or "").path.rstrip("/").casefold()) == page_key
            for job in jobs)
        if structured_page:
            # The employer's record is kept as is; the page only fills what it left out
            # (many JSON-LD postings omit employmentType or name only the company seat).
            h1_nodes = [node for node in nodes if node.tag == "h1" and node.text()]
            body = next((node for node in nodes if node.tag == "body"), parser.root)
            if len(h1_nodes) == 1:
                summary = _summary_lines(body, h1_nodes[0])
                labeled_place, summary_place, fact_employment = _read_facts(summary)
                same_page = [job for job in jobs
                             if (job.get("raw_metadata") or {}).get("extraction_method") in _STRUCTURED_METHODS and
                             ((urlsplit(job.get("url") or "").hostname or "").casefold(),
                              urlsplit(job.get("url") or "").path.rstrip("/").casefold()) == page_key]
                # This page's facts describe this page's posting: on /careers?jobId=123 they
                # must not fill the row for ?jobId=124.
                exact = [job for job in same_page if _destination_key(job.get("url") or "") == _destination_key(page_url)]
                # A page naming its vacancy in the query (?jobId=123) fills only that exact row.
                names_vacancy = bool(_POSTING_QUERY.search(urlsplit(page_url).query))
                for job in exact or (same_page if len(same_page) == 1 and not names_vacancy else []):
                    titled = _title_employment(job.get("title") or "")
                    employment = titled or fact_employment
                    # "FULL_TIME" in the record states hours; "Ausbildung" in the title states
                    # the format, which is what a job seeker filters on.
                    if employment and (not job.get("employment_type") or
                                       titled in _TRAINING_FORMATS and job["employment_type"] not in _TRAINING_FORMATS):
                        job["employment_type"] = employment
                        job["raw_metadata"]["employment_type_evidence"] = "page_title" if titled else "page_summary"
                    place = (labeled_place or next(filter(None, map(title_place, _title_variants(
                        job.get("title") or "", title, summary))), None) or summary_place)
                    if not job.get("location") and place:
                        _set_location(job, place)
                        job["raw_metadata"]["location_evidence"] = "page_summary"
        if not structured_page and (_DETAIL_ROUTE.search(path) or softgarden_id) and not re.search(r"no (?:current |open )?positions|keine (?:offenen )?stellen", lower_text):
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
                detail_description, detail_facts, contact_text = _detail_sections(h1_node)
                detail_description = detail_description or ""
                detail_context = re.sub(r"^(?:## |- )", "", detail_description, flags=re.M)
                # The page's own title names its posting, like a link to it ("Project Engineer 37").
                role_title = _candidate_title(h1)
                # main confirmed on all text after the title, contact block included.
                confirmed = (role_title and (apply or vacancy_marker or len(detail_context + contact_text) >= 30))
                if confirmed and not _INITIATIVE_APPLICATION.search(h1) and not _TALENT_POOL.search(h1):
                    body = next((node for node in nodes if node.tag == "body"), parser.root)
                    summary = _summary_lines(body, h1_node)
                    labeled_place, summary_place, fact_employment = _read_facts(summary)
                    labeled = [place for place in map(_place_label, _labeled_locations(nodes)) if place]
                    titled_place = next(filter(None, map(title_place, _title_variants(h1, title, summary))), None)
                    detail_location = (labeled_place or titled_place or summary_place or
                                       _place_label(_leading_location(detail_context)) or
                                       (labeled[0] if len(labeled) == 1 else None))
                    # A line that is nothing but an employment term ("Vollzeit oder Teilzeit")
                    # is precise enough to read further down, e.g. an "at a glance" box.
                    # Only hours, though: a lone "Ausbildung" further down is usually a menu link.
                    later_employment = next((kind for kind in map(_employment_fact, [
                        line for line in _lines(body, after=h1_node)[:120] if len(line) <= 80])
                        if kind and kind not in _TRAINING_FORMATS), None)
                    employment = (_title_employment(h1) or fact_employment or
                                  _employment_fact(" ".join(detail_facts)) or later_employment or
                                  _hours_in_text(detail_context))
                    job = _job(h1, page_url, "softgarden_job_detail" if softgarden_id else "html_job_detail", detail_context,
                               location=detail_location, employment_type=employment, infer_employment=False,
                               description=detail_description or None,
                               application_email=_application_email(detail_context + " " + contact_text))
                    if softgarden_id:
                        job['id'] = softgarden_id
                    deadline = _application_deadline(visible_text)
                    if deadline:
                        job["raw_metadata"]["validThrough"] = deadline
                    jobs.append(job)
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("job_detail: " + type(error).__name__)

    try:
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
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("heading_jobs: " + type(error).__name__)

    # A page whose query names one vacancy (/careers?jobId=123) is that vacancy's page:
    # it cannot list all postings, so nothing is closed from it.
    vacancy_page = bool(_POSTING_QUERY.search(urlsplit(page_url).query))
    try:
        detail_page = any((job.get("raw_metadata") or {}).get("extraction_method") in _DETAIL_METHODS | _STRUCTURED_METHODS and
                          (job.get("url") or "").rstrip("/") == page_url.rstrip("/") for job in jobs)
        jobs.extend(_marked_title_jobs(parser.root, page_url, jobs, detail_page=detail_page or vacancy_page,
                                       vacancy_page=vacancy_page))
    except Exception as error:  # noqa: BLE001 - one broken strategy must not lose the others
        failures.append("marked_titles: " + type(error).__name__)

    page_locations = _locations(visible_text)
    jobs = _merge(jobs)
    if len(page_locations) == 1:
        page_vacancy = _destination_key(page_url) if _POSTING_QUERY.search(urlsplit(page_url).query) else None
        for job in main_reading(jobs):
            # On /careers?jobId=123 the page's place belongs to that vacancy, not to ?jobId=124.
            if page_vacancy and _destination_key(job.get("url") or "") != page_vacancy:
                continue
            if not job.get("locations"):
                job["locations"] = page_locations
                job["location"] = "; ".join(item["label"] for item in page_locations)
                job.setdefault("raw_metadata", {})["location_evidence"] = "career_page_context"
    # HTML that signals a next page cannot safely close jobs missing from this page
    # (main's rule, plus rel tokens and explicit page wording; see _next_page_link).
    has_next = any(_next_page_link(node) for node in nodes)
    softgarden_partial = bool(re.fullmatch(r'[a-z0-9-]+\.softgarden\.(io|de)', urlsplit(page_url).hostname or '', re.I))
    return {"jobs": jobs, "role_candidates": role_candidates, "strategy_errors": failures,
            "complete": not failures and not has_next and not vacancy_page and not phenom_partial and not softgarden_partial and not microdata_partial and not bool(_DETAIL_ROUTE.search(urlsplit(page_url).path)),
            "page_title": title, "has_open_vacancy_signal": bool(re.search(r"open position|offene stellen|aktuelle jobs|current opportunities|job posting", lower_text))}
