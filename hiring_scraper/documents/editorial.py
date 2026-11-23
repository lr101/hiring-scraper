"""Deterministic editorial checks for generated prose."""
from __future__ import annotations

import re
from collections.abc import Iterable

from hiring_scraper.documents.schema import MAX_UNIT_TEXT, CvDocument, Entry, Unit, walk_units
from hiring_scraper.documents.text import unit_full_text

FIRST_PERSON = re.compile(r"\b(?:I|ich)\b", re.IGNORECASE)
_FIRST_PERSON_I = re.compile(r"\bI\b")
_FIRST_PERSON_CONTRACTION = re.compile(r"\bi['’](?:m|ve|d|ll)\b", re.I)
_FIRST_PERSON_DE = re.compile(r"\bich\b", re.I)
_ROMAN_PREFIX = re.compile(
    r'\b(?:phase|level|part|section|tier|stage|grade|class|type|version|annex|appendix)\s+$', re.I,
)
EN_CONTRACTION = re.compile(
    r"\b(?:\w+n['’]t|\w+['’](?:m|ve|re|ll|d)|(?:it|that|there|what|who|how|he|she|here|let|where)['’]s)\b", re.I,
)
CONDITIONAL = re.compile(r"\b(?:würde(?:st|n|t)?|would)\b", re.I)
GENERIC_ENTRY_LABEL = re.compile(
    r'^(?:university|college|hochschule|universität|internship|praktikum|education|ausbildung)$', re.I,
)
_DEGREE = re.compile(
    r'^(?:[BM]\.?\s?(?:Sc|Eng|Tech|A|S)\.?\b|Bachelor\b|Master\b|Diplom\b|PhD\b|Ph\.D\.)', re.I,
)
_CONNECTORS = frozenset(
    'a an and as at by for from in of on or the to with und oder am an auf aus bei das der die '
    'des den dem ein eine einer eines für im in mit nach über von zur zum '
    'across through between during within around into onto toward towards without while whereas '
    'während zwischen sowie dabei damit darüber darunter unter über durch gegen ohne'.split()
)
_COMMON_ACTIONS = frozenset(
    'designed developed created worked supported managed planned produced helped built ensured '
    'leading led coordinated coordinating working using creating building supporting managing '
    'developing providing implementing improving delivering conducting collaborating enabling '
    'various different multiple general overall specific related focused based including '
    'entwickelte erstellte arbeitete unterstützte plante koordinierte betreute begleitete '
    'leitete führte nutzte setzte half verbesserte erhöhte steigerte sorgte ermöglichte '
    'verschiedene unterschiedliche zusammenarbeit umsetzung arbeiteten'.split()
)
_STEM_SUFFIXES = (
    'isation', 'ization', 'ationen', 'ations', 'ation', 'ition', 'ungen', 'ieren', 'ierte',
    'ierten', 'endes', 'enden', 'ender', 'ende', 'ings', 'ing', 'ung', 'ied', 'ies', 'ern',
    'ers', 'ten', 'tet', 'es', 'ed', 'er', 'en', 'te', 's', 'e', 'n', 't',
)
_CRITICAL_ACRONYM = re.compile(r'\b(?:[A-Z]{2,6}[0-9]{0,3}|[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*)\b')


def has_first_person(text: str) -> bool:
    if _FIRST_PERSON_DE.search(text) or _FIRST_PERSON_CONTRACTION.search(text):
        return True
    for match in _FIRST_PERSON_I.finditer(text):
        if not _ROMAN_PREFIX.search(text[:match.start()]):
            return True
    return False


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


def _stem(word: str) -> str:
    if not word.isalpha() or len(word) < 5:
        return word
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def _same_word(a: str, b: str) -> bool:
    # Recognize light EN/DE inflection while keeping short acronyms and numbers exact.
    if a == b:
        return True
    if not (a.isalpha() and b.isalpha()):
        return False
    short, long = sorted((a, b), key=len)
    if len(short) >= 4 and long.startswith(short) and long[len(short):] in {
        's', 'es', 'e', 'en', 'er', 'ern', 'n', 'ed', 'ing', 'ung', 't', 'te', 'ten',
    }:
        return True
    stem_a, stem_b = _stem(a), _stem(b)
    if stem_a == stem_b:
        return True
    if min(len(stem_a), len(stem_b)) >= 5:
        shorter, longer = sorted((stem_a, stem_b), key=len)
        if longer.startswith(shorter) and len(longer) - len(shorter) <= 2:
            return True
    return False


def _covered_word(token: str, retained: Iterable[str]) -> bool:
    stem = _stem(token)
    for other in retained:
        if _same_word(token, other):
            return True
        if len(token) >= 6 and len(other) >= 5 and token.isalpha() and other.isalpha():
            other_stem = _stem(other)
            if (len(other_stem) >= 5 and other_stem in token) or (len(stem) >= 5 and stem in other):
                return True
    return False


def supports_competency(text: str, cited: Iterable[str], posting: str) -> bool:
    tokens = content_words(text)
    source_tokens = set(words(' '.join(cited)))
    posting_tokens = content_words(posting)
    return (bool(tokens) and all(any(_same_word(token, source) for source in source_tokens) for token in tokens)
            and any(_same_word(token, keyword) for token in tokens for keyword in posting_tokens))


def omitted_critical_details(unit: Unit, evidence: dict[str, str], entry_id: str) -> set[str]:
    """Return numbers or uppercase acronyms/CamelCase tools present in cited base bullets but missing here."""
    sources = [
        evidence[key] for key in unit.sources
        if key.startswith(f'cv:{entry_id}:b') and key in evidence
    ]
    if not sources:
        return set()
    retained_text = unit_full_text(unit)
    retained_words = set(words(retained_text))
    missing: set[str] = set()
    for text in sources:
        for token in words(text):
            if token.isdigit() and token not in retained_words:
                missing.add(token)
        for match in _CRITICAL_ACRONYM.finditer(text):
            tok = match.group(0)
            if tok.casefold() not in retained_words and tok.casefold() not in retained_text.casefold():
                missing.add(tok.casefold())
    return missing


def omitted_details(unit: Unit, evidence: dict[str, str], entry_id: str) -> set[str]:
    source_tokens = set().union(*(
        content_words(evidence[key]) for key in unit.sources
        if key.startswith(f'cv:{entry_id}:b') and key in evidence
    ))
    retained = words(unit_full_text(unit))
    critical = omitted_critical_details(unit, evidence, entry_id)
    dropped = {
        token for token in source_tokens - _COMMON_ACTIONS
        if (len(token) >= 6 or token.isdigit())
        and not _covered_word(token, retained)
    }
    if critical:
        return dropped | critical
    return dropped if len(dropped) >= 2 else set()


def merge_split_project_bullets(entry: Entry) -> bool:
    """Merge visible unlabelled continuation bullets into the preceding labelled project bullet."""
    merged = False
    result: list[Unit] = []
    last_labelled_idx: int | None = None
    for unit in entry.units:
        if unit.hidden:
            result.append(unit)
            continue
        if unit.lead_in.strip():
            last_labelled_idx = len(result)
            result.append(unit)
            continue
        if last_labelled_idx is not None:
            previous = result[last_labelled_idx]
            left = previous.text.rstrip()
            right = unit.text.lstrip()
            if left and right:
                sep = ' ' if left[-1] in '.!?;:' else '. '
                combined = f'{left}{sep}{right}'
            else:
                combined = right or left
            if len(combined) <= MAX_UNIT_TEXT:
                previous.text = combined
                previous.sources = list(dict.fromkeys([*previous.sources, *unit.sources]))
                merged = True
                continue
        result.append(unit)
    if merged:
        entry.units = result
    return merged


def normalize_study_entry(entry: Entry) -> None:
    """Promote explicit DOCX degree labels, drop generic labels, and remove redundant degree prefixes."""
    if entry.kind != 'study':
        return
    if GENERIC_ENTRY_LABEL.fullmatch(entry.label.strip()):
        entry.label = ''
    locked = entry.locked
    if GENERIC_ENTRY_LABEL.fullmatch(locked.company.strip()):
        intro = next((unit for unit in entry.intro if not unit.hidden), None)
        promoted = False
        if intro and not intro.lead_in:
            degree, divider, _ = re.match(r'^(.*?)(\s+[–—-]\s+|$)(.*)$', intro.text).groups()
            clean_degree = degree.rstrip('.') if not divider else degree
            if _DEGREE.match(clean_degree) and len(clean_degree) <= 300 and (divider or len(clean_degree) <= 100):
                entry.locked = locked = locked.model_copy(update={'company': locked.title, 'title': clean_degree})
                promoted = True
        if not promoted and locked.title.strip():
            entry.locked = locked = locked.model_copy(update={'company': locked.title})
    if not locked.title.strip():
        return
    for unit in entry.intro:
        title = re.escape(locked.title)
        text = re.sub(rf'^{title}(?:\s+[–—-]\s+|\s*[:.]\s+|\s*[.]?$)', '', unit.text, count=1, flags=re.I)
        if text != unit.text:
            if text or unit.lead_in:
                unit.text = text
            else:
                unit.hidden = True
