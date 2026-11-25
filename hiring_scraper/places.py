"""Recognize German place names in short lines of job-page text.

Career pages name the work location in endlessly varied markup: a class called
``location``, a map-marker icon, a "Standort" label in a definition list, a
bare line under the title, the tail of a card ("25.11.2026 Firma GmbH
Saarlouis"). Instead of chasing each layout, the parser reads the page as short
lines and asks this module whether a line *is* a place. The answer comes from
the official list of German municipalities (Bundeswahlleiterin, already bundled
for election context), not from the markup.

Places outside Germany are recognized only by their country ("Pune, India"):
Radius serves job seekers in Germany, so a foreign city without a country is
simply not read as a location.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from importlib.resources import files

from .election_data import (
    _ENGLISH_CITY_ALIASES,
    _FOREIGN_COUNTRY_CODES,
    _FOREIGN_COUNTRY_NAMES,
    _GERMANY_LABELS,
    _STATE_ALIASES,
)

# Municipality names that are also everyday words on career pages. Alone on a
# line they are a fact ("Essen" stays a city: it is a common work location and
# never a heading), but these read as words far more often than as places.
_COMMON_WORDS = frozenset({
    "au", "bad", "berg", "buch", "burg", "elbe", "hof", "insel", "lage", "lehre", "list", "mark",
    "post", "rain", "senden", "singen", "stelle", "wald", "wiesen", "wissen", "werben", "weil", "lauf",
})
_BASE_NAME = re.compile(
    r"^(.+?)\s+(?:am|im|an der|an den|a\.\s?d\.|a\.d\.|a\.|ob der|in der|in|bei|vor der|v\.\s?d\.\s?h\.)\s+.+$", re.I)
_POSTAL_CODE = re.compile(r"\b\d{5}\b")
_STREET = re.compile(r"(?:straße|strasse|str\.|weg|allee|platz|ring|gasse|damm|ufer|chaussee|avenue|street)\s*\d|"
                     r"^\s*[a-zäöüß.-]+\s+\d{1,4}\s*[a-z]?\s*$", re.I)
_WORK_STYLE = re.compile(r"\b(?:hybrid|remote|homeoffice|home[- ]office|mobiles arbeiten|vor[- ]ort|on[- ]?site|"
                         r"und|oder|bzw\.?|or|and|deutschlandweit|bundesweit|germany[- ]wide)\b", re.I)
# Pins, stars and arrows decorating a label: "📍 Obertshausen", "Eppendorf <<".
_DECORATION = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]|<<|>>|\*{2,}")
_SEPARATORS = re.compile(r"\s*(?:[|•·;/]|\s[-–—]\s)\s*")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).replace("ß", "ss")
    value = "".join(char for char in value if not unicodedata.combining(char)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


@lru_cache(maxsize=1)
def _municipalities() -> frozenset[str]:
    keys: set[str] = set()
    resource = files("hiring_scraper").joinpath("elections", "btw25_municipalities.csv")
    with resource.open("r", encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            # The official name precedes the comma ("Schwerte, Hansestadt an der Ruhr").
            name = row["name"].split(",", 1)[0].strip()
            name = re.sub(r"\s*\(einschl\.[^)]*\)", "", name)
            variants = {name, re.sub(r"\s*\([^)]*\)", "", name)}
            base = _BASE_NAME.match(name)
            if base:
                variants.add(base.group(1))
            keys.update(normalize(variant) for variant in variants if variant)
    keys.update(_ENGLISH_CITY_ALIASES)
    keys.update({"frankfurt", "freiburg", "halle", "offenbach", "mulheim", "neustadt"})
    keys.discard("")
    return frozenset(keys)


def is_municipality(value: str) -> bool:
    key = normalize(value)
    return bool(key) and key in _municipalities()


def _country(value: str, *, codes: bool) -> str | None:
    """"de" for Germany, "foreign" for another country, None for anything else.

    Two-letter codes ("AT" in "Wels, AT, 4600") count only beside a place:
    alone they are far more often words such as "IT" or "KI".
    """
    key = normalize(value)
    if key in _GERMANY_LABELS and (codes or len(key) > 3):
        return "de"
    if key in _FOREIGN_COUNTRY_NAMES or (codes and len(key) == 2 and key in _FOREIGN_COUNTRY_CODES and value.strip().isupper()):
        return "foreign"
    return None


def _city(part: str) -> str | None:
    """The municipality a fragment names: "22397 Hamburg", "Haren (Ems)", "Stuttgart-Feuerbach"."""
    text = _POSTAL_CODE.sub(" ", part)
    text = re.sub(r"\((?:deutschland|germany|de)\)", " ", text, flags=re.I)
    text = " ".join(text.split()).strip(" ,.:-–")
    if not text or len(text) > 60 or re.search(r"\d", text):
        return None
    if is_municipality(text):
        return text
    # A district joined to its town: "Geisenheim-Marienthal", "Stuttgart-Feuerbach".
    head, _, rest = text.partition("-")
    if (rest and len(head) > 3 and re.fullmatch(r"[A-ZÄÖÜ][\w]+(?:\s[A-ZÄÖÜ][\w]+)?", rest) and
            is_municipality(head) and not is_municipality(rest) and normalize(head) not in _COMMON_WORDS):
        return text
    # A parenthetical qualifier the list spells differently: "Hannover (ivv)".
    bare = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    if bare != text and is_municipality(bare):
        return text
    return None


def parse_places(line: str, *, labeled: bool = False) -> list[str]:
    """The places a short line consists of, or [] when the line is anything else.

    Every comma part must be a place, a postal code, a street, a German state or
    a country; one stray word ("Wir suchen in Berlin") rejects the line. A
    ``labeled`` value ("Standort: …") already says it is a place, so a single
    unknown name is accepted when it looks like one ("Schiphol-Rijk").
    """
    line = " ".join(_DECORATION.sub(" ", line.replace("\u00a0", " ")).split()).strip(" ,.;:|·•-–—")
    if not line or len(line) > 160:
        return []
    # Each group keeps its own country: "Essen, BE; Berlin" is Belgian Essen and Berlin.
    groups = [group for group in re.split(r"\s*[;|•·]\s*|\s+/\s+", line) if group.strip(" ,.-–")]
    if len(groups) > 1:
        found: list[str] = []
        for group in groups:
            places = _parse_group(group, labeled=labeled)
            if not places:
                return []
            found.extend(place for place in places if place not in found)
        return found
    whole = _parse_group(line, labeled=labeled)
    if not whole and "/" in line:
        # "Essen, BE/Berlin": an unspaced slash between two complete place groups.
        parts = [_parse_group(group, labeled=False) for group in line.split("/")]
        if len(parts) > 1 and all(parts):
            return [place for found in parts for place in found]
    return whole


def _parse_group(line: str, *, labeled: bool) -> list[str]:
    """One place expression whose comma parts belong together ("22397 Hamburg, Deutschland")."""
    line = line.strip(" ,.;:|·•-–—")
    original = line
    line = _WORK_STYLE.sub(",", line)
    parts = [part.strip() for part in re.split(r"\s*,\s*", line) if part.strip(" ()-–")]
    parts = [part[:-1] if part.endswith(")") and "(" not in part else part for part in parts]
    cities: list[str] = []
    foreign = german = False
    unknown: list[str] = []
    for index, part in enumerate(parts):
        city = _city(part)
        kind = _country(part, codes=index > 0)
        if city and not (len(parts) == 1 and normalize(city) in _COMMON_WORDS and not labeled):
            cities.append(city)
        elif kind == "foreign":
            foreign = True
        elif kind == "de" or normalize(part) in _STATE_ALIASES or normalize(part) in {"nrw", "bw"}:
            german = True
        elif _STREET.search(part) and index < len(parts) - 1:
            continue
        elif re.fullmatch(r"\d{4,6}", part.strip()):
            continue
        else:
            unknown.append(part)
    if foreign and not cities:
        # "Pune, India" or "United Kingdom": a foreign location stays readable
        # so the posting can be scoped out of German searches.
        if (not unknown or labeled and all(_looks_like_place(item) for item in unknown) or
                len(unknown) == 1 and parts[0] == unknown[0] and _looks_like_place(unknown[0]) and len(parts) > 1):
            return [original]
        return []
    if unknown:
        if labeled and not cities and len(unknown) == 1 and _looks_like_place(unknown[0]):
            return [original if len(parts) == 1 else unknown[0]]
        if labeled and cities and len(unknown) <= 2 and all(_looks_like_place(item) for item in unknown):
            # A labeled list naming some cities outside Germany ("Krakau, Kattowitz, Dresden").
            return [part for part in parts if _city(part) or part in unknown]
        return []
    if foreign and cities and len(cities) == 1:
        return [original]
    if len(cities) > 1 and len(parts) == 2 and normalize(parts[1]) in {"germany", "deutschland"}:
        return [cities[0]]
    return cities if cities or german else []


def _looks_like_place(value: str) -> bool:
    words = value.split()
    return (0 < len(words) <= 4 and value[:1].isupper() and not re.search(r"\d|[@:/]", value)
            and normalize(value) not in {"plz", "ort", "standort", "location", "stadt", "region"}
            and all(word[:1].isupper() or word.casefold() in {"am", "im", "an", "der", "a.", "d.", "ob", "bei", "-"}
                    for word in words))


def places_in_segments(line: str) -> list[str]:
    """Places in a line of separated facts: "Vollzeit | Essen | Hybrid", "58239 Schwerte · AÜ"."""
    whole = parse_places(line)
    if whole:
        return whole
    found: list[str] = []
    for segment in _SEPARATORS.split(line):
        for place in parse_places(segment):
            if place not in found:
                found.append(place)
    return found


def trailing_place(value: str) -> str | None:
    """A municipality ending a run-on card line: "25.11.2026 Firma GmbH Saarlouis"."""
    words = re.sub(r"[,;|·•]", " ", value).split()
    for size in (3, 2, 1):
        if len(words) > size:
            candidate = " ".join(words[-size:])
            if _city(candidate) and normalize(candidate) not in _COMMON_WORDS:
                return candidate
    return None


def title_place(title: str) -> str | None:
    """The place a title ends with: "… (m/w/d) in Hamburg", "… (m/w/d) Hechingen"."""
    clean = " ".join(_DECORATION.sub(" ", title).split())
    clean = re.sub(r"\s*\((?:[mwdfxi*]\s*[/|,]\s*){1,3}[mwdfxi*]\)\s*$|\s*\((?:all genders|alle geschlechter)\)\s*$", "", clean, flags=re.I)
    # The last "in …" wins: "Fachkraft für Lagerlogistik in Dormagen".
    # A place leading the title: "Siegburg – Technischer Produktdesigner (w/m/d)".
    lead = re.match(r"^([A-ZÄÖÜ][\w.()' -]{1,40}?)\s+[–—|]\s+\S", clean)
    if lead:
        places = parse_places(lead.group(1))
        if len(places) == 1:
            return places[0]
    clean = re.sub(r"\s+(?:gesucht|ab sofort|befristet|unbefristet)$", "", clean, flags=re.I)
    for match in reversed(list(re.finditer(r"\b(?i:in|am standort|standort)\s+(?=[A-ZÄÖÜ])", clean))):
        tail = clean[match.end():]
        # "… in Köln, 20€/Std." – the town ends at the first comma, dash or bracket.
        for candidate in (tail, re.split(r"\s*(?:[,|;(]|\s[–—-]\s)", tail, maxsplit=1)[0]):
            if len(candidate) <= 40:
                places = parse_places(candidate)
                if len(places) == 1:
                    return places[0]
        break
    match = re.search(r"\((?:[mwdfx*]\s*/\s*){1,3}[mwdfx*]\)\s*[-–|,]?\s*([A-ZÄÖÜ][\w.()' -]{1,40})$", clean, re.I)
    if match:
        places = parse_places(match.group(1))
        if len(places) == 1:
            return places[0]
    return None
