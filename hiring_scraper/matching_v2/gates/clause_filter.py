"""Language and work-authorization clause filtering for ablation-safe content features."""
from __future__ import annotations

from functools import lru_cache
import re

from hiring_scraper.matching import (
    LANGUAGE_APPLICANT_CUE,
    LANGUAGE_FLUENCY,
    LANGUAGE_PATTERNS,
)
from hiring_scraper.matching_v2.gates.authorization import (
    AUTHORIZATION_CONDITION,
    _EXPLICIT_NO_SPONSORSHIP,
    has_sponsorship_alternative,
)
from hiring_scraper.matching_v2.gates.common import _PAREN_RE
from hiring_scraper.matching_v2.gates.language import (
    _CEFR_TOKEN_RE,
    _DRIVING_LICENSE_RE,
    _LANG_MODALITY_RE,
    _LANGUAGE_PREFILTER,
    _NATIVE_LEVEL_RE,
    _NON_LANG_DOMAIN_RE,
    _STRICT_NATIVE_RE,
    _is_non_language_mention,
)

_WHITESPACE_RE = re.compile(r'\s+')


@lru_cache(maxsize=256)
def _cached_cue_word_pattern(cue_word: str) -> re.Pattern[str]:
    return re.compile(rf'\b{re.escape(cue_word)}\b', re.I)


_PURE_LANG_OR_VISA_HINT = re.compile(
    r'\b(?:deutschkenntnis\w*|englischkenntnis\w*|sprachkenntnis\w*|sprachniveau|'
    r'muttersprach\w*|verhandlungssicher\w*|fließend\w*|fliessend\w*|'
    r'business\s+(?:english|german|fluent)|'
    r'(?:working|team|company|corporate)\s+language|'
    r'(?:arbeits|team|unternehmens)sprache|'
    r'visa[- ]?sponsor\w*|work\s+permit|work\s+authori[sz]ation|right\s+to\s+work|'
    r'arbeitserlaubnis|arbeitsbewilligung|arbeitsberechtigung|aufenthaltstitel|'
    r'aufenthaltsgenehmigung|aufenthaltserlaubnis|niederlassungs(?:bewilligung|erlaubnis)|'
    r'arbeitsvisum|blue\s+card|blaue\s+karte)\b',
    re.I,
)
_CLAUSE_LEAD_IN_RE = re.compile(
    r'^(\s*(?:'
    r'(?:required|requirements?|must\s+have|qualifications?|prerequisites?|erforderlich|voraussetzungen?|anforderungen?)\s*:\s*'
    r'|(?:you|candidates?|applicants?)\s+(?:must|should|need\s+to|will\s+need\s+to|have|bring)\s+'
    r'|(?:du|sie)\s+(?:bringst|bringen|hast|haben|musst|müssen|solltest|sollten|verfügst\s+über|verfügen\s+über)\s+'
    r'|(?:we\s+(?:require|expect|are\s+looking\s+for)|wir\s+(?:erwarten|suchen|setzen\s+voraus))\s+'
    r'))',
    re.I,
)
_SUBCLAUSE_SEP_RE = re.compile(
    r'(\s*;\s*|\s*,\s*(?:(?:and|und|sowie|plus)\s+)?|\s+\b(?:as\s+well\s+as|in\s+addition\s+to|and|und|sowie|plus)\b\s+)',
    re.I,
)
_BARE_LANG_OR_MODALITY_RE = re.compile(
    r'^(?:\s*(?:in|at|on|mit|with|mind(?:estens)?\.?|min\.?|at\s+least|ab|auf|niveau|level|'
    r'written|spoken|oral|verbal|schriftlich\w*|mündlich\w*|wort|schrift|form|'
    r'good|very\s+good|fluent|gute|sehr\s+gute|fließend\w*|verhandlungssicher\w*|'
    r'A1|A2|B1|B2|C1|C2|native[- ]?level|near[- ]?native|muttersprach\w*|'
    r'required|mandatory|erforderlich|notwendig|mit|'
    r'german|deutsch\w*|english|englisch\w*|french|französisch\w*|spanish|spanisch\w*|'
    r'italian|italienisch\w*|dutch|niederländisch\w*|polish|polnisch\w*|portuguese|portugiesisch\w*|'
    r'russian|russisch\w*|turkish|türkisch\w*|arabic|arabisch\w*|chinese|chinesisch\w*|'
    r'japanese|japanisch\w*|korean|koreanisch\w*|swedish|schwedisch\w*|danish|dänisch\w*|'
    r'norwegian|norwegisch\w*|finnish|finnisch\w*|czech|tschechisch\w*|'
    r'language\s+skills?|skills?|kenntnis\w*|proficiency|fluency|'
    r'\([^()]*\)|[.,:;/–—-]))+$',
    re.I,
)
_TRAILING_CLAUSE_CUE_RE = re.compile(
    r'\b((?:(?:is|are|ist|sind)\s+)?(?:required|mandatory|erforderlich|notwendig|verpflichtend|vorausgesetzt|voraussetzung)|mit)\s*[.!?]*\s*$',
    re.I,
)


def is_language_or_visa_clause(text: str) -> bool:
    """Return True when a clause mentions applicant language proficiency or work-permit/visa."""
    if not text:
        return False
    if _DRIVING_LICENSE_RE.search(text):
        return False
    if AUTHORIZATION_CONDITION.search(text) or has_sponsorship_alternative(text) or _EXPLICIT_NO_SPONSORSHIP.search(text):
        return True
    if _NON_LANG_DOMAIN_RE.search(text):
        return False
    if _PURE_LANG_OR_VISA_HINT.search(text):
        return True
    # Catch natural-language mentions with CEFR/fluency/applicant cues (excluding non-applicant objects).
    if _LANGUAGE_PREFILTER.search(text) and (
        _CEFR_TOKEN_RE.search(text)
        or _NATIVE_LEVEL_RE.search(text)
        or _STRICT_NATIVE_RE.search(text)
        or LANGUAGE_FLUENCY.search(text)
        or LANGUAGE_APPLICANT_CUE.search(text)
        or _LANG_MODALITY_RE.search(text)
    ):
        return any(
            not _is_non_language_mention(lang, text, match)
            for lang, pattern in LANGUAGE_PATTERNS.items()
            for match in pattern.finditer(text)
        )
    return False


def strip_language_or_visa_subclauses(sentence: str) -> str:
    """Remove only language/visa sub-clauses from ``sentence``, keeping sibling requirement evidence.

    Example:
      "You must speak fluent German and have advanced Kubernetes experience."
      -> "You must have advanced Kubernetes experience."
    Pure language/visa sentences return ``""``.
    """
    if not sentence:
        return ''
    if not is_language_or_visa_clause(sentence):
        return sentence

    # Protect parentheticals so commas/ands inside parens stay attached to their sub-clause.
    protected: dict[str, str] = {}
    working = sentence.strip()
    trailing_punct = ''
    if working and working[-1] in '.!?':
        trailing_punct = working[-1]
        working = working[:-1]

    for idx, m in reversed(list(enumerate(_PAREN_RE.finditer(working)))):
        # Leave level-only parentheticals attached to their language, while
        # removing self-contained language/permit asides in technical clauses.
        parenthetical = m.group()[1:-1]
        outside = f'{working[:m.start()]} {working[m.end():]}'
        if is_language_or_visa_clause(parenthetical):
            working = outside.strip()
        else:
            marker = f'\ue000p{idx}\ue001'
            protected[marker] = m.group()
            working = working[:m.start()] + marker + working[m.end():]

    lead_in = ''
    lead_m = _CLAUSE_LEAD_IN_RE.match(working)
    if lead_m:
        lead_in = lead_m.group(1)
        working = working[lead_m.end():]

    tokens = _SUBCLAUSE_SEP_RE.split(working)
    # tokens alternates: [part0, sep0, part1, sep1, part2, ...]
    if len(tokens) == 1:
        # Single atomic clause — already known to be language/visa.
        cleaned = working
        for marker, original in protected.items():
            cleaned = cleaned.replace(marker, original)
        return '' if is_language_or_visa_clause(cleaned) else ' '.join((lead_in + cleaned).split()) + trailing_punct

    def _restore(s: str) -> str:
        for marker, orig in protected.items():
            s = s.replace(marker, orig)
        return s

    parts = [_restore(tokens[i]) for i in range(0, len(tokens), 2)]
    seps = [tokens[i] for i in range(1, len(tokens), 2)]

    drop = [is_language_or_visa_clause(p) for p in parts]
    # Also drop adjacent bare sibling language / modality tails (e.g. "English", "spoken", "in Schrift")
    # directly coordinated with a dropped language sub-clause.
    changed = True
    while changed:
        changed = False
        for i, p in enumerate(parts):
            if drop[i]:
                continue
            adj_dropped = (i > 0 and drop[i - 1]) or (i + 1 < len(parts) and drop[i + 1])
            if adj_dropped and _BARE_LANG_OR_MODALITY_RE.fullmatch(p.strip()):
                drop[i] = True
                changed = True

    if all(drop):
        return ''
    if not any(drop):
        return _WHITESPACE_RE.sub(' ', lead_in + _restore(working)).strip() + trailing_punct

    kept_indices = [i for i, d in enumerate(drop) if not d]
    # Reassemble kept sub-clauses preserving the original separators between kept siblings.
    out_chunks: list[str] = []
    for pos, idx in enumerate(kept_indices):
        if pos > 0:
            prev_idx = kept_indices[pos - 1]
            sep = seps[prev_idx] if prev_idx < len(seps) else ' and '
            out_chunks.append(sep)
        out_chunks.append(parts[idx].strip())

    # If the final sub-clause was dropped and ended with a shared predicate ("required", "mit"),
    # preserve it when the remaining text lacks that cue.
    trailing_cue = ''
    if drop[-1]:
        cue_m = _TRAILING_CLAUSE_CUE_RE.search(parts[-1])
        if cue_m:
            cue_word = cue_m.group(1)
            joined_so_far = ''.join(out_chunks)
            if not _cached_cue_word_pattern(cue_word).search(f'{lead_in}{joined_so_far}'):
                trailing_cue = f' {cue_word}'

    rebuilt = f'{lead_in}{"".join(out_chunks)}{trailing_cue}'.strip()
    rebuilt = _WHITESPACE_RE.sub(' ', rebuilt)
    if trailing_punct and rebuilt and rebuilt[-1] not in '.!?':
        rebuilt += trailing_punct
    return rebuilt
