"""Work location and employment type stated in the short lines around a posting.

Career sites put the town, hours and contract type near the title in endlessly
varied markup ("Standort" over "Mannheim, hybrid", "Vollzeit | Essen | Hybrid",
a town beside a map icon). These lines are found by layout and accepted by
content: a place must be a real municipality (see places.py), an employment type
must be the whole fact, not a word inside a sentence.
"""
from __future__ import annotations

import re

from .places import parse_places, places_in_segments, trailing_place
from .html_tree import _Element, _ancestors, _clean_text, _lines


def _employment(text: str) -> str | None:
    lowered = text.casefold().replace("_", "-")
    if re.search(r"duales?\s+studium|dual\s+study", lowered):
        return "dual_study"
    if re.search(r"ausbildung|apprenticeship", lowered):
        return "apprenticeship"
    if re.search(r"werkstudent|working student", lowered):
        return "working_student"
    if re.search(r"praktikum|internship|\bintern\b", lowered):
        return "internship"
    if re.search(r"vollzeit|full[- ]?time|voll-\s*(?:oder|und|/|,)\s*teilzeit|voltijd", lowered):
        return "full-time"
    if re.search(r"teilzeit|part[- ]?time", lowered):
        return "part-time"
    return None

_FACT_LABEL = r"\s*(?:\([^)]*\))?\s*(?:[:\-–]\s*|$|(?=\s))"

_LOCATION_FACT = re.compile(
    r"^(?:(?:job|work|primary|office)\s+)?(?:locations?(?:\s*text)?|standort(?:e|\(e\))?|einsatzort(?:e)?|"
    r"arbeitsort(?:e)?|dienstort|ort|stadt(?:\s*/\s*region)?|city|arbeitsplatz)" + _FACT_LABEL, re.I)

_EMPLOYMENT_FACT = re.compile(
    r"^(?:beschäftigungsart|anstellungsart|art der anstellung|arbeitszeit(?:modell)?|vertragsart|anstellung|"
    r"stellenart|stellenumfang|arbeitsumfang|employment\s*type|type of (?:employment|contract)|"
    r"contract\s*type|job\s*type|full time part time)" + _FACT_LABEL, re.I)

_I18N_KEY = re.compile(r"^__(?:[\w-]+\.)*(?:opt-|freetext\.)?([\w ]+?)\s*__\s*")

_EMPLOYMENT_TERMS = {
    "full-time": r"vollzeit|full[- ]?time|voltijd",
    "part-time": r"teilzeit|part[- ]?time|deeltijd|minijob",
    "working_student": r"werkstudent(?:in|en|\*in)?|werkstudierende[rn]?|working student|studentische (?:aushilfen?|hilfskraft|mitarbeit)",
    "internship": r"praktikum|pflichtpraktikum|praktikant(?:in)?|internship|intern",
    "apprenticeship": r"ausbildung|berufsausbildung|azubi|apprenticeship",
    "dual_study": r"duales studium|dual study",
}

_CONTRACT_WORDS = r"festanstellung|unbefristet|befristet|permanent|temporary|ab sofort|flexibel"

_EMPLOYMENT_FACT_LINE = re.compile(
    r"^(?:in\s+)?(?:(?:%s|%s)(?:\s*\([^)]*\))?)(?:\s*(?:/|,|\||oder|und|or|and|bzw\.?)\s*(?:in\s+)?(?:%s|%s)(?:\s*\([^)]*\))?)*$"
    % ("|".join(_EMPLOYMENT_TERMS.values()), _CONTRACT_WORDS, "|".join(_EMPLOYMENT_TERMS.values()), _CONTRACT_WORDS),
    re.I,
)

def _employment_fact(value: str) -> str | None:
    """The employment type a fact states outright ("Vollzeit (40h)", "Festanstellung, Vollzeit / Teilzeit")."""
    value = _clean_text(value).strip(" .:;|·•")
    if not value or len(value) > 80 or not _EMPLOYMENT_FACT_LINE.fullmatch(value):
        return None
    return _employment(value)

# A training format names the position itself: it opens the title ("Ausbildung
# Mechatroniker", "(Zweit-)Ausbildung …"), or the title says who is trained
# ("Ausbildung zum …", "Azubi", "Praktikant"). "Koordinator Ausbildung" or
# "Ausbildungsmarketing" is a job about training.
_TITLE_START = r"^[\W\d]*(?:\([\w-]+\)\s*|top\s+|neu:?\s+)?"
_TITLE_DUAL = re.compile(_TITLE_START + r"dual(?:es|er)?\s+stud|dual(?:er|e)?\s+student|dual\s+studierende|"
                         r"duales?\s+studium\s+(?:im|in|zum|zur|als|bachelor|b\.\s?(?:a|sc|eng))", re.I)
# Mid-title, only wording that names the trainee counts ("Ausbildung zum Koch", "Azubi",
# "Praktikant"): "Personalreferent Ausbildung für Bonn" or "Werkstudentenprogramm" do not.
_TITLE_APPRENTICESHIP = re.compile(
    _TITLE_START + r"(?:berufs)?ausbildung(?:s(?:platz|plätze|stelle|stellen|start))?(?![a-zäöüß])|"
    r"(?:berufs)?ausbildung\s+(?:zum|zur|als)\b|\bazubi(?:s)?\b|\bauszubildende[rn]?\b|"
    r"\blehrling\b|apprenticeship", re.I)
# "Werkstudentenbetreuer" or "Pflichtpraktikantenprogramm" are jobs about trainees.
_TITLE_WORKING_STUDENT = re.compile(
    r"\bwerkstudent(?:in|en|innen|\*in|:in|/in)?(?![a-zäöüß])|\bworking students?\b|"
    r"\bstudentische (?:aushilfe|hilfskraft|mitarbeit)", re.I)
_TITLE_INTERNSHIP = re.compile(
    _TITLE_START + r"(?:pflicht)?praktikum\b|\b(?:pflicht)?praktikant(?:in|en|innen|\*in|:in|/in)?(?![a-zäöüß])|"
    r"\bpraktikum\s+(?:im|in|bei|als)\b|\binternship\b|\bintern\b", re.I)


def _title_employment(title: str) -> str | None:
    """Training formats a title names ("Ausbildung zum …", "Werkstudent …") outrank any page text."""
    lowered = title.casefold()
    if _TITLE_DUAL.search(lowered):
        return "dual_study"
    if _TITLE_APPRENTICESHIP.search(lowered):
        return "apprenticeship"
    if _TITLE_WORKING_STUDENT.search(lowered):
        return "working_student"
    if _TITLE_INTERNSHIP.search(lowered):
        return "internship"
    if re.search(r"\b(?:in\s+)?(?:vollzeit|full[- ]?time)\b|\bvoll-\s*(?:oder|und|/|,)\s*teilzeit", lowered):
        return "full-time"
    if re.search(r"\b(?:in\s+)?(?:teilzeit|part[- ]?time)\b", lowered):
        return "part-time"
    return None


def _hours_in_text(text: str) -> str | None:
    """Only hours are safe to read from running text: "abgeschlossene Ausbildung" is a requirement."""
    lowered = text.casefold()
    if re.search(r"\b(?:in\s+)?(?:vollzeit|full[- ]?time)\b|\bvoll-\s*(?:oder|und|/|,)\s*teilzeit", lowered):
        return "full-time"
    if re.search(r"\b(?:in\s+)?(?:teilzeit|part[- ]?time)\b", lowered):
        return "part-time"
    return None

_PLACE_SHAPED = re.compile(
    r"(?:[A-ZÄÖÜ][\w.'’()-]*|[A-Z]{2,3}|-|am|an|im|in|der|de|del|la|le|upon|on)"
    r"(?:(?:\s*,\s*|\s+)(?:[A-ZÄÖÜ][\w.'’()-]*|[A-Z]{2,3}|-|am|an|im|in|der|de|del|la|le|upon|on|\d{4,6})){0,7}"
)

_NOT_A_PLACE = frozenset({"plz", "ort", "standort", "standorte", "location", "locations", "stadt", "region", "city"})

def _place_label(value: str | None, *, labeled: bool = True) -> str | None:
    """A location value checked against real places; run-on card lines keep their trailing town."""
    if not value:
        return None
    value = _MORE_LOCATIONS.sub("", value)
    places = parse_places(value, labeled=labeled) or places_in_segments(value)
    if places:
        return "; ".join(places)
    if not labeled:
        return None
    # The markup says this is a location, so a value shaped like one stays even when
    # it is outside Germany ("Princeton, NJ", "UK - Remote"): it is what scopes the
    # posting out of German searches. Prose and dates are not places.
    clean = _clean_text(value).strip(" ,.;:|·•-–—")
    if _PLACE_SHAPED.fullmatch(clean) and clean.casefold() not in _NOT_A_PLACE:
        return clean
    return trailing_place(value)

def _split_label(line: str, pattern: re.Pattern, following: str | None) -> str | None:
    match = pattern.match(line)
    if not match:
        return None
    value = line[match.end():].strip()
    return value or (following or "")

def _read_facts(lines: list[str], *, unlabeled_lines: int = 15) -> tuple[str | None, str | None, str | None]:
    """(labeled location, unlabeled location, employment type) stated in a run of short lines.

    A labeled fact wins over an unlabeled one; an unlabeled line counts only
    within the first ``unlabeled_lines``, where a page shows its summary.
    """
    labeled_place = labeled_employment = place = employment = None
    for index, raw in enumerate(lines):
        if len(raw) > 200:
            continue
        line = _I18N_KEY.sub(lambda match: re.sub(r"(?<=[a-z])(?=[A-Z])", " ", match.group(1)) + ": ", raw)
        following = lines[index + 1] if index + 1 < len(lines) else None
        value = _split_label(line, _LOCATION_FACT, following)
        if value is not None:
            labeled_place = labeled_place or _place_label(value)
            continue
        value = _split_label(line, _EMPLOYMENT_FACT, following)
        if value is not None:
            labeled_employment = labeled_employment or _employment_fact(value) or _employment(value)
            continue
        if index >= unlabeled_lines:
            continue
        segments = [segment for segment in re.split(r"\s*(?:[|•·]|\s[-–—]\s)\s*", line) if segment]
        if place is None and len(line) <= 120:
            found = parse_places(line) or places_in_segments(line)
            place = "; ".join(found) if found else None
        if employment is None:
            employment = next((kind for kind in map(_employment_fact, [line, *segments]) if kind), None)
    return labeled_place, place, labeled_employment or employment

def _summary_lines(root: _Element, heading: _Element) -> list[str]:
    """The short lines around a posting's title, up to where its running text starts.

    Lines sharing the title's header block come first, so facts printed just
    above the title ("Vollzeit / Teilzeit", "Lingen") count as well.
    """
    title = heading.text()
    lines: list[str] = []
    # Up to four short lines right above the title inside its article ("Vollzeit / Teilzeit", "Lingen").
    container = next((parent for parent in _ancestors(heading) if parent.tag in {"article", "main", "section"}), None)
    if container is not None:
        every, after = _lines(container), _lines(container, after=heading)
        before = every[:max(0, len(every) - len(after))]
        while before and before[-1] in {title, title.strip()} | {line for line in before[-1:] if title.startswith(line)}:
            before.pop()
        lines = [line for line in before[-4:] if len(line) <= 80]
    for line in _lines(root, after=heading)[:40]:
        if len(line) > 200:
            break
        if line not in lines:
            lines.append(line)
    return lines

def _title_variants(title: str, page_title: str, lines: list[str]) -> list[str]:
    """The title as shown, plus fuller copies when responsive markup cut it short."""
    variants = [title]
    stem = title[:30].casefold()
    for candidate in (page_title, *lines[:12]):
        if candidate and len(candidate) > len(title) and candidate.casefold().startswith(stem):
            variants.append(re.split(r"\s+[|]\s+", candidate)[0])
    return variants

_TRAINING_FORMATS = frozenset({"apprenticeship", "dual_study", "working_student", "internship"})

_MORE_LOCATIONS = re.compile(
    r"\s*(?:\+|and|und)\s*\d+\s*(?:more|other|further|additional|weitere|andere)?\s*(?:locations?|standorte?|orte?)?\s*…?\s*$",
    re.I,
)
