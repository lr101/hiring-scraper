"""Deterministic editorial checks for generated prose."""
from __future__ import annotations

import re
from collections.abc import Iterable

from hiring_scraper.documents.schema import CvDocument, Entry, Unit, walk_units
from hiring_scraper.documents.text import unit_full_text

FIRST_PERSON = re.compile(r"\b(?:I|ich)\b", re.IGNORECASE)
EN_CONTRACTION = re.compile(r"\b(?:\w+n['’]t|\w+['’](?:m|ve|re|ll|d)|(?:it|that|there|what|who|how|he|she|here)['’]s)\b", re.I)
CONDITIONAL = re.compile(r"\b(?:würde(?:st|n|t)?|would)\b", re.I)
GENERIC_ENTRY_LABEL = re.compile(
    r'^(?:university|college|hochschule|universität|internship|praktikum|education|ausbildung)$', re.I,
)
_DEGREE = re.compile(
    r'^(?:[BM]\.?\s?(?:Sc|Eng|Tech|A|S)\.?\b|Bachelor\b|Master\b|Diplom\b|PhD\b|Ph\.D\.)', re.I,
)
_CONNECTORS = frozenset(
    'a an and as at by for from in of on or the to with und oder am an auf aus bei das der die '
    'des den dem ein eine einer eines für im in mit nach über von zur zum'.split()
)
_COMMON_ACTIONS = frozenset(
    'designed developed created worked supported managed planned produced '
    'entwickelte erstellte arbeitete unterstützte plante'.split()
)


def words(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', text.casefold())


def ngrams(text: str, size: int = 8) -> set[tuple[str, ...]]:
    tokens = words(text)
    return set(zip(*(tokens[offset:] for offset in range(size)), strict=False))


def cv_ngrams(cv: CvDocument) -> set[tuple[str, ...]]:
    return set().union(*(ngrams(unit_full_text(unit)) for unit in walk_units(cv) if not unit.hidden))


def content_words(text: str) -> set[str]:
    return set(words(text)) - _CONNECTORS


def near_duplicate(left: str, right: str) -> bool:
    if ngrams(left) & ngrams(right):
        return True
    a, b = content_words(left), content_words(right)
    return min(len(a), len(b)) >= 4 and len(a & b) / len(a | b) >= 0.75


def _same_word(a: str, b: str) -> bool:
    # Recognize light inflection while keeping short acronyms and numbers exact.
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return (len(short) >= 4 and short.isalpha() and long.startswith(short)
            and long[len(short):] in {'s', 'es', 'e', 'en', 'er', 'ern', 'n', 'ed', 'ing', 'ung'})


def supports_competency(text: str, cited: Iterable[str], posting: str) -> bool:
    tokens = content_words(text)
    source_tokens = set(words(' '.join(cited)))
    posting_tokens = content_words(posting)
    return (bool(tokens) and all(any(_same_word(token, source) for source in source_tokens) for token in tokens)
            and any(_same_word(token, keyword) for token in tokens for keyword in posting_tokens))


def omitted_details(unit: Unit, evidence: dict[str, str], entry_id: str) -> set[str]:
    source_tokens = set().union(*(
        content_words(evidence[key]) for key in unit.sources
        if key.startswith(f'cv:{entry_id}:b') and key in evidence
    ))
    retained = words(unit_full_text(unit))
    return {
        token for token in source_tokens - _COMMON_ACTIONS
        if (len(token) >= 6 or token.isdigit())
        and not any(_same_word(token, other) for other in retained)
    }


def normalize_study_entry(entry: Entry) -> None:
    """Promote explicit DOCX degree labels and remove redundant degree prefixes."""
    if entry.kind != 'study':
        return
    locked = entry.locked
    if GENERIC_ENTRY_LABEL.fullmatch(locked.company.strip()):
        intro = next((unit for unit in entry.intro if not unit.hidden), None)
        if intro and not intro.lead_in:
            degree, divider, _ = re.match(r'^(.*?)(\s+[–—-]\s+|$)(.*)$', intro.text).groups()
            if _DEGREE.match(degree) and len(degree) <= 300 and (divider or len(degree) <= 100):
                entry.locked = locked = locked.model_copy(update={'company': locked.title, 'title': degree})
    for unit in entry.intro:
        title = re.escape(locked.title)
        text = re.sub(rf'^{title}(?:\s+[–—-]\s+|\s*[:.]\s+|\s*[.]?$)', '', unit.text, count=1, flags=re.I)
        if text != unit.text:
            if text or unit.lead_in:
                unit.text = text
            else:
                unit.hidden = True
