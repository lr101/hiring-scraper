"""Whether a line of a career page names a vacancy.

German job titles carry a gender marker by law ("(m/w/d)", "( m | w | d )",
"Referent*in"), which marks a role more reliably than link routes or profession
words. The other half is knowing what is *not* a posting: category and search
links, result counts, form controls, audience navigation and prose.
"""
from __future__ import annotations

import re
import unicodedata
from .html_tree import _clean_text


_GENERIC_HEADINGS = {
    "careers", "career", "jobs", "job", "about", "about us", "offene stellen", "open positions",
    "open job postings", "open roles", "aktuelle jobs", "stellenangebote",
    "unser büro", "our office", "our team", "benefits", "kontakt", "contact",
    "ausbildung", "duales studium", "students", "studierende",
}

_INITIATIVE_APPLICATION = re.compile(
    r"(?:\binitiativ(?:bewerbung|application)\b|^(?:initiative|unsolicited|general|speculative|open)\s+application(?:\s*\([^)]*\))?$)",
    re.I,
)

_TALENT_POOL = re.compile(r"^(?:join (?:our )?)?talent (?:pool|community|network)(?:\s*\([^)]*\))?$", re.I)

# German job titles carry a gender marker by law (AGG): "(m/w/d)", "(w/m/x)", "(all genders)".
_GENDER_MARKER = re.compile(
    r"\(\s*(?:[mwdfxi*]\s*[/|,]\s*){1,3}[mwdfxi*]\s*\)|\b[mwdf]/[mwdf]/[mwdfx]\b|\((?:all genders|alle geschlechter|gn\*?)\)",
    re.I,
)

# Link and heading text that names a role word without being a vacancy.
# Text that is never a vacancy title, wherever it appears or links to.
_NEVER_A_ROLE = re.compile(
    # Search-engine and category links: "Kältetechniker Jobs", "Jobs Studium in Darmstadt".
    # Only the plural, in a short plain title: "Job Developer (m/w/d)", "Job Placement Specialist
    # in Berlin" or "Software Engineer – Search Jobs" (a product name after a dash) are roles.
    r"^jobs\s+(?:[\w-]+\s+){0,3}(?:in|bei|für|im)\s+[^\s–—|:-][^–—|:]*$|"
    r"^[^–—|:]{1,40}?\s+jobs(?:\s+(?:in|bei|für|im)\s+[\w. -]+)?$|"
    # Result totals: "IT Jobs: 469 Stellenangebote".
    r"\b\d+\s+(?:stellenangebote|stellen|jobs|treffer|ergebnisse|results|vacancies|positions)\s*$|"
    # Plural category pages and hashtag navigation: "#Ausbildungsplätze", "Ausbildungsstellen".
    r"^#|^(?:ausbildungs|praktikums|studien|lehr|werkstudenten)(?:plätze|stellen|angebote|berufe|gänge)(?:$|\s+(?:der|des|bei|im|in)\b)|"
    # Section and welcome headings: "Die Ausbildung", "Das lernst Du …", "Willkommen bei …", "Interne Karriere".
    r"^(?:die|das|der|unsere|deine|ihre)\s+(?:ausbildung|stelle|aufgaben|vorteile|benefits)$|"
    r"^(?:das|was|wie|warum|wieso)\s+\w+\s+(?:du|sie|dich|dir|wir|ihr)\b|^willkommen\b|\bkarriere$|"
    # Form and list controls: "Schule/Ausbildung hinzufügen".
    r"\b(?:hinzufügen|entfernen|bearbeiten|löschen|speichern|merken|teilen|filtern|finden|suchen|entdecken)$|"
    # Section headings inside a posting: "Inhalte der Ausbildung", "Ablauf des Studiums".
    r"^(?:inhalte|ablauf|dauer|ziele|vorteile|voraussetzungen|infos?|informationen|fakten|wissenswertes)\s+"
    r"(?:der|des|zur|zum|zu|rund um|über|deiner|ihrer|unserer)\b",
    re.I,
)
# Text that is usually navigation, but can title a real posting page ("Werkstudent",
# "Cloud Engineer (123)"). Rejected only where nothing links it to its own posting.
_UNLINKED_NOT_A_ROLE = re.compile(
    # Facet counts: "Projektleiter 37". (A number in brackets is often a reference
    # number of a real posting, "Cloud Engineer (123)", so it is not read as a count.)
    r"^\D+?\s(?!19\d\d\b|20\d\d\b)\d{2,4}$|"
    r"^(?:praktikum|praktika|ausbildung|studium|duales studium|werkstudent(?:in)?)$",
    re.I,
)


def _not_a_role(text: str, *, linked: bool = False) -> bool:
    return bool(_NEVER_A_ROLE.search(text) or (not linked and _UNLINKED_NOT_A_ROLE.search(text)))

def _audience_only(value: str) -> bool:
    """"Ausbildung, Duales Studium, Quereinsteiger" or "Schüler:in": who a page is for, not a role."""
    words = re.sub(r"[^a-z]+", " ", unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()).split()
    return bool(words) and set(words) <= _AUDIENCE_WORDS and not _STRONG_MARKER.search(value)

def _audience_list(value: str) -> bool:
    """Several audiences in one line ("Ausbildung, Duales Studium, Quereinsteiger"): navigation, never a role."""
    return (_audience_only(value) and bool(re.search(r"[,/|&]|\s(?:und|oder|and|or)\s", value)) and
            len(set(re.findall(r"[a-zäöüß]{4,}", value.casefold()))) >= 2)


# A posting title, wherever it sits: German titles carry a gender marker by law
# ("(m/w/d)", "( m | w | d )", "[m/w/d]", "Referent*in"), which marks a role far
# more reliably than link routes or profession word lists. Cards whose links do
# not look like /jobs/… and plain lists of headings are read this way.
_TITLE_MARKER = re.compile(
    r"\(\s*(?:[mwdfxi*]\s*[/|,)]\s*){1,3}[mwdfxi*]\s*\)|\[\s*(?:[mwdfx]\s*[/|,]\s*){1,3}[mwdfx]\s*\]|"
    r"(?<![\w/])[mwdf]\s*/\s*[mwdf]\s*/\s*[mwdfx](?![\w/])|\((?:all genders|alle geschlechter|all|gn\*?)\)|"
    r"[a-zäöüß](?:\*|:)in\b",
    re.I,
)
# "Referent*in" names a role, but "Kolleg*innen" (plural) is prose; and a lone star form
# without "(m/w/d)" counts only when it links to its posting.
_STRONG_MARKER = re.compile(
    r"\(\s*(?:[mwdfxi*]\s*[/|,)]\s*){1,3}[mwdfxi*]\s*\)|\[\s*(?:[mwdfx]\s*[/|,]\s*){1,3}[mwdfx]\s*\]|"
    r"(?<![\w/])[mwdf]\s*/\s*[mwdf]\s*/\s*[mwdfx](?![\w/])|\((?:all genders|alle geschlechter|all|gn\*?)\)",
    re.I,
)
_TITLE_STOPWORDS = frozenset({"zum", "zur", "der", "die", "das", "als", "m", "w", "d", "f", "x", "i",
                              "in", "ab", "und", "stellenangebot", "job", "fur", "im", "bei", "the", "and", "or", "oder"})
_TRAINING_WORDS = frozenset({"ausbildung", "azubi", "auszubildende", "auszubildender", "praktikum", "praktikant",
                             "werkstudent", "duales", "studium", "trainee", "berufsausbildung"})


def _title_core(title: str) -> frozenset[str]:
    words = re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", title.casefold().replace("ß", "ss"))
                   .encode("ascii", "ignore").decode()).split()
    return frozenset(word for word in words if word not in _TITLE_STOPWORDS and not word.isdigit())


def _same_role(left: frozenset[str], right: frozenset[str]) -> bool:
    """Two titles naming one posting: "Ausbildung zum Industriekaufmann (m/w/d)" and "… ab 01.09.2027"."""
    if not left or not right:
        return False
    # "Fliesenleger" and "Ausbildung Fliesenleger" are a job and an apprenticeship.
    if (left & _TRAINING_WORDS) != (right & _TRAINING_WORDS):
        return False
    return left <= right or right <= left or len(left & right) / len(left | right) >= 0.75


def _title_squash(title: str) -> str:
    """A title's letters in order, without gender markers: "Patentanwalts-Kandidat/in" and
    "Patentanwaltskandidat:in" spell one role."""
    text = _TITLE_MARKER.sub(" ", title)
    text = unicodedata.normalize("NFKD", text.casefold().replace("ß", "ss")).encode("ascii", "ignore").decode()
    return re.sub(r"(?:in|innen)$", "", re.sub(r"[^a-z]+", "", text))

_TITLE_PROSE = re.compile(
    r"^(?:als|auch|sie|du|wir|ihr|ihre|unser|unsere|dein|deine|diese|dieser|das|der|die stadt|jetzt|idealerweise|"
    r"starte|beginne|werde|komm|mehrwerte|bei uns|hier|für unser|für unsere|mit|zum|zur|vorteile|gesucht wird|"
    r"oder|und|sowie|bzw|abgeschlossene?|abgeschlosse|ausgebildete?|erfahrung|kenntnisse|ansprechpartner|"
    r"aufstiegschancen|karrierechancen|entwicklungsmöglichkeiten|perspektiven)\b|\bbis hin zu[mr]?\b|"
    r"\b(?:mit|und|oder|für|als|von|zum|zur|bei)$|"
    r"\b(?:vergleichbare|qualifikation|erwarten dich|solltest du|deine vorteile|ihre vorteile|bewirb dich|"
    r"bewerben sie sich|jetzt bewerben|suchen wir|sucht zum)\b|[;?]$",
    re.I,
)

def _norm_title(title: str) -> str:
    return re.sub(r"\W+", " ", _clean_text(title).casefold()).strip()

# Audience navigation ("Schüler:in", "Student:in/Praktikant:in/Thesis", "Berufseinsteiger:in").
_AUDIENCE_WORDS = frozenset({
    "schuler", "schulerin", "student", "studentin", "studierende", "studenten", "absolvent", "absolventin",
    "absolventen", "berufseinsteiger", "berufseinsteigerin", "berufserfahrene", "quereinsteiger",
    "quereinsteigerin", "thesis", "praktikant", "praktikantin", "young", "professionals", "schulabganger",
    "in", "innen", "und", "m", "w", "d", "ausbildung", "duales", "studium", "duale", "studiengange", "praktika",
})

def _marked_title(text: str) -> bool:
    if not text or len(text) > 140 or len(text.split()) > 20 or not _TITLE_MARKER.search(text):
        return False
    words = re.sub(r"[^a-z]+", " ", unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()).split()
    if (set(words) <= _AUDIENCE_WORDS and not _STRONG_MARKER.search(text)) or text.count("|") >= 3:
        return False
    rest = _TITLE_MARKER.sub(" ", text)
    return (len(re.findall(r"[A-Za-zÄÖÜäöüß]", rest)) >= 3 and not _TITLE_PROSE.search(text.strip())
            and not _not_a_role(text) and not _INITIATIVE_APPLICATION.search(text)
            and not _TALENT_POOL.search(text))
