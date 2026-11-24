"""CEFR, native-level, and qualitative language requirement gate for v2."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import re
from typing import Any

from hiring_scraper.matching import (
    CEFR_RANK,
    LANGUAGE_APPLICANT_CUE,
    LANGUAGE_EXPECTED_FOLLOWING,
    LANGUAGE_FLUENCY,
    LANGUAGE_PATTERNS,
    OPTIONAL,
    REQUIREMENT,
    _canonical_language,
)
from hiring_scraper.gates.common import (
    NON_REQUIREMENT_V2,
    _PAREN_RE,
    _is_other_actor_subject,
    mask_parens,
    unmask_parens,
)
from hiring_scraper.requirements import is_source_instruction, requirement_polarity

_PROFILE_NATIVE_RE = re.compile(r'\b(?:NATIVE|MUTTERSPRACH|MOTHER\s+TONGUE|FIRST\s+LANGUAGE)\b')
_POLISH_VERB_FOLLOW_RE = re.compile(
    r'\s+(?:up|off|out|your|the|a|an|my|our|their|this|that|these|those)\b',
    re.I,
)
_LANG_PROFICIENCY_NOUN_RE = re.compile(r'\b(?:kenntnis\w*|proficiency|fluency|sprachniveau)\b', re.I)
_CEFR_SUFFICIENCY_ONLY_RE = re.compile(
    r'\b(?:A1|A2|B1|B2|C1|C2)\s+(?:is\s+(?:enough|sufficient|fine|okay|ok)|'
    r'suffices|reicht(?:\s+aus)?|genügt|ist\s+ausreichend)\b',
    re.I,
)
_CEFR_EXPLICIT_MIN_RE = re.compile(
    r'\b(?:at\s+least|minimum|min\.?|mindestens|mind\.?|required|mandatory|erforderlich|zwingend)\b',
    re.I,
)

_EXTERNAL_LANGUAGE_USE = re.compile(
    r'\b(?:is|are|wird|werden)\s+(?:used|spoken|required|verwendet|gesprochen|benötigt)\s+'
    r'(?:by|von)\s+(?:our|their|the|unser\w*|dem|der)\b',
    re.I,
)

_DRIVING_LICENSE_RE = re.compile(
    r'\b(?:führerschein|fahrerlaubnis|driving\s+licen[cs]e|driver[\'’]?s\s+licen[cs]e)\b',
    re.I,
)
_NON_LANG_DOMAIN_RE = re.compile(
    r'\b(?:'
    r'(?:german|deutsch(?:e[nrms]?)?)\s+'
    r'(?:(?:labour|labor|employment|tax|commercial|corporate|data\s+protection|privacy|contract|company)\s+)?'
    r'(?:law|legislation|gaap|hgb|market|markets|clients?|customers?|regulations?|standards?|'
    r'accounting|payroll|entities|subsidiaries|sites?|plants?|healthcare|public\s+sector|'
    r'arbeitsrecht|recht|steuerrecht|handelsrecht|markt|märkte|märkten|kunden|kundinnen|'
    r'gesellschaften|standorte|standorten|werke|werken|gesundheitswesen)\b'
    r'|(?:english|englisch(?:e[nrms]?)?)\s+(?:law|market|markets|clients?|customers?|entities|subsidiaries)\b'
    r'|(?:programming|scripting|query|markup|modeling|modelling)\s+languages?\b'
    r'|(?:large\s+|natural\s+)?language\s+(?:models?|processing|understanding|generation)\b'
    r'|(?:programmier|skript|abfrage|auszeichnungs|modellierungs)sprache(?:n)?\b'
    r')',
    re.I,
)

# Explicit CEFR token (guarded against driving-license Klasse C1 and highway A1/A2/B1/B2).
_CEFR_TOKEN_RE = re.compile(
    r'(?<!Klasse\s)(?<!Class\s)(?<!Autobahn\s)(?<!Bundesstraße\s)'
    r'\b(A1|A2|B1|B2|C1|C2)\b(?!\s*(?:E\b|license|licence|führerschein))',
    re.I,
)
# Unambiguous mandatory native-level / near-native -> C2 (rank 6)
_NATIVE_LEVEL_RE = re.compile(
    r'\b(?:native[- ]?level|near[- ]?native|native[- ]?like|'
    r'auf\s+muttersprach\w*niveau|muttersprach\w*niveau)\b',
    re.I,
)
# Unambiguous mandatory native speaker / Muttersprache -> NATIVE (rank 7)
_STRICT_NATIVE_RE = re.compile(
    r'\b(?:mother\s+tongue|first\s+language|'
    r'muttersprache\w*|muttersprachlich\w*|muttersprachler\w*|'
    r'(?<!-)(?<!cloud\s)(?<!react\s)(?<!digital\s)(?<!near\s)(?<!ai\s)'
    r'native(?:\s+speaker)?'
    r'(?!\s*[- ]?(?:level|like|apps?|mobile|cloud|ios|android|web|code|'
    r'integrations?|tooling|features?|support|ads?|advertising|content|experience|ui|sdk|apis?|modules?|components?|clients?)))\b',
    re.I,
)
# Qualitative fluency offered as an alternative/lower bound alongside native
# (e.g. "fluent or native", "business fluent / native", "verhandlungssicher oder muttersprachlich").
_QUALITATIVE_NATIVE_ALT_RE = re.compile(
    r'\b(?:'
    r'(?:fluent|business[- ]?fluent|verhandlungssicher\w*|fließend\w*|fliessend\w*|'
    r'sehr\s+gut\w*|very\s+good|proficient|professional\s+(?:working\s+)?proficiency|'
    r'excellent|good|gut\w*)\s*(?:/|\bor\b|\boder\b|\bto\b|\bbis\b|[-–])\s*'
    r'(?:near[- ]?native|native[- ]?(?:level|like|speaker)?|muttersprach\w*|mother\s+tongue)'
    r'|(?:near[- ]?native|native[- ]?(?:level|like|speaker)?|muttersprach\w*|mother\s+tongue)'
    r'\s*(?:/|\bor\b|\boder\b)\s*'
    r'(?:fluent|business[- ]?fluent|verhandlungssicher\w*|fließend\w*|fliessend\w*|'
    r'sehr\s+gut\w*|very\s+good|proficient|professional\s+(?:working\s+)?proficiency|excellent)'
    r')\b',
    re.I,
)

# Qualitative fluency (NEVER hard fail: estimate floor for verify flag only).
_QUALITATIVE_LEVELS: tuple[tuple[re.Pattern[str], str, list[str]], ...] = (
    (
        re.compile(
            r'\b(?:verhandlungssicher\w*|business[- ]fluent|full\s+professional\s+proficiency|'
            r'professional\s+(?:working\s+)?proficiency|bilingual(?:\s+proficiency)?|'
            r'fluent|fluently|fluency|fließend\w*|fliessend\w*|flüssig\w*|fluessig\w*|'
            r'very\s+good|sehr\s+gut\w*|excellent|ausgezeichnet\w*|exzellent\w*|hervorragend\w*|'
            r'advanced|fortgeschritten\w*|proficient|souverän\w*|souveraen\w*|'
            r'kommunikationssicher\w*|sprachgewandt\w*|stilsicher\w*|sprachlich\s+versiert|'
            r'outstanding|strong\s+(?:command|knowledge|language\s+skills?|written|spoken)?|'
            r'exceptional\s+(?:command|knowledge)|mastery)\b',
            re.I,
        ),
        'C1',
        ['C1', 'C2'],
    ),
    (
        re.compile(
            r'\b(?:good|gut\w*|conversational|konversationssicher\w*|'
            r'working\s+(?:knowledge|proficiency)|intermediate|mittelstufe|solide\w*|sicher\w*)\b',
            re.I,
        ),
        'B1',
        ['B1', 'B2'],
    ),
    (
        re.compile(
            r'\b(?:basic|grundkenntnis(?:se)?|basiskenntnis(?:se)?|elementary|beginner|anfängerkenntnis(?:se)?)\b',
            re.I,
        ),
        'A1',
        ['A1', 'A2'],
    ),
)

_OPTIONAL_SUBCLAUSE_RE = re.compile(
    r'(?:,\s*|\s+)(?:idealerweise|preferr(?:ed|ably)|wünschenswert|nice[- ]to[- ]have|'
    r'optional|von\s+vorteil|a\s+plus|gerne(?:\s+auch)?)\b[^()]*',
    re.I,
)
_TIGHT_COORD_CONNECTOR = re.compile(r'^\s*[-]?\s*(?:and|und|sowie|or|oder|&|/|,)\s*$', re.I)
_LANG_MODALITY_RE = re.compile(
    r'\b(?:written|spoken|oral|verbal|in\s+writing|in\s+wort\s+und\s+schrift|'
    r'schriftlich\w*|mündlich\w*)\b',
    re.I,
)
# Nouns directly following a language name that denote non-applicant objects/learners/materials
# (e.g. "Support English learners at C1 level", "German courses", "English exams").
_NON_APPLICANT_LANG_OBJECT_RE = re.compile(
    r'^\s+(?:learners?|students?|pupils?|tutors?|teachers?|instructors?|classes?|courses?|lessons?|'
    r'tutorials?|curricul\w*|exams?|tests?|assessments?|materials?|content|texts?|documents?|'
    r'manuals?|publications?|books?|programs?|programmes?|trainings?|workshops?|sessions?|'
    r'lernende\w*|schüler\w*|studierende\w*|lehrkr\w*|kurse?\w*|unterricht\w*|prüfungen?|'
    r'materialien|dokumente\w*|texte\w*)\b',
    re.I,
)
_NON_APPLICANT_LANG_CLAUSE_RE = re.compile(
    r'\b(?:support|teach|tutor|coach|instruct|mentor|assess|grade|evaluate|train|prepare|develop|'
    r'deliver|create|edit|translate|review|unterrichten|betreuen|fördern|coachen|prüfen|erstellen)\b'
    r'[^.!?;]{0,45}\b(?:learners?|students?|pupils?|participants?|classes?|courses?|exams?|tests?|'
    r'materials?|curricul\w*|lernende\w*|schüler\w*|teilnehmende\w*|kurse?\w*|prüfungen?)\b',
    re.I,
)
_LANG_LEVEL_START_RE = re.compile(
    r'^\s*(?:[,:(–—/-]|'
    r'\b(?:at\s+least|minimum|min\.?|mindestens|mind\.?|ab|auf|niveau|level|cefr|ger|'
    r'A1|A2|B1|B2|C1|C2|native|near[- ]?native|mother\s+tongue|muttersprach\w*|'
    r'als\s+(?:muttersprach\w*|erstsprache)|as\s+(?:(?:a|your)\s+)?(?:native|first|mother)|'
    r'fluent|fluently|fluency|proficiency|proficient|command|skills?|knowledge|kenntnis\w*|'
    r'verhandlungssicher\w*|fließend\w*|fliessend\w*|sehr\s+gut\w*|gut\w*|good|very\s+good|basic|'
    r'written|spoken|oral|verbal|schriftlich\w*|mündlich\w*|in\s+wort\s+und\s+schrift|'
    r'in|at|on|mit|with|und|and|oder|or|sowie|is|are|ist|sind|'
    r'required|mandatory|erforderlich|vorausgesetzt|notwendig|zwingend)\b)',
    re.I,
)
_EXPLICIT_MIN_CUE_RE = re.compile(
    r'\b(?:at\s+least|minimum|min\.?|mindestens|mind\.?|required|mandatory|must|essential|'
    r'erforderlich|notwendig|vorausgesetzt|voraussetzung|zwingend|unerlässlich)\b',
    re.I,
)
_DUTY_ACTION_LEAD_RE = re.compile(
    r'^\s*(?:support|help|assist|advise|guide|teach|tutor|coach|mentor|deliver|create|build|'
    r'develop|manage|lead|run|prepare|produce|review|edit|translate|coordinate|facilitate|'
    r'conduct|handle|serve|unterstützen|helfen|beraten|betreuen|unterrichten|erstellen|'
    r'entwickeln|durchführen|leiten|koordinieren|übersetzen)\b',
    re.I,
)
_NON_APPLICANT_PREPOSITION_RE = re.compile(
    r'\b(?:with|for|to|at|für|mit|bei)\s+(?:our|the|your|external|international|global|unser\w*|extern\w*)\b',
    re.I,
)

# Extended localized language names mapping -> canonical English name.
_EXTRA_LANG_ALIASES = {
    'deutsch': 'German',
    'deutschkenntnisse': 'German',
    'german': 'German',
    'englisch': 'English',
    'englischkenntnisse': 'English',
    'english': 'English',
    'französisch': 'French',
    'franzoesisch': 'French',
    'french': 'French',
    'spanisch': 'Spanish',
    'spanish': 'Spanish',
    'italienisch': 'Italian',
    'italian': 'Italian',
    'niederländisch': 'Dutch',
    'niederlaendisch': 'Dutch',
    'dutch': 'Dutch',
    'portugiesisch': 'Portuguese',
    'portuguese': 'Portuguese',
    'polnisch': 'Polish',
    'polish': 'Polish',
    'russisch': 'Russian',
    'russian': 'Russian',
    'türkisch': 'Turkish',
    'tuerkisch': 'Turkish',
    'turkish': 'Turkish',
    'arabisch': 'Arabic',
    'arabic': 'Arabic',
    'chinesisch': 'Chinese',
    'chinese': 'Chinese',
    'mandarin': 'Chinese',
    'japanisch': 'Japanese',
    'japanese': 'Japanese',
    'koreanisch': 'Korean',
    'korean': 'Korean',
    'ukrainisch': 'Ukrainian',
    'ukrainian': 'Ukrainian',
    'schwedisch': 'Swedish',
    'swedish': 'Swedish',
    'dänisch': 'Danish',
    'daenisch': 'Danish',
    'danish': 'Danish',
    'norwegisch': 'Norwegian',
    'norwegian': 'Norwegian',
    'finnisch': 'Finnish',
    'finnish': 'Finnish',
    'tschechisch': 'Czech',
    'czech': 'Czech',
    'griechisch': 'Greek',
    'greek': 'Greek',
    'rumänisch': 'Romanian',
    'rumaenisch': 'Romanian',
    'romanian': 'Romanian',
    'ungarisch': 'Hungarian',
    'hungarian': 'Hungarian',
    'hebräisch': 'Hebrew',
    'hebraeisch': 'Hebrew',
    'hebrew': 'Hebrew',
    'hindi': 'Hindi',
}


def canonical_language(name: str) -> str:
    """Normalize a localized language name (e.g. 'Deutsch', 'Englisch') to canonical English."""
    cleaned = str(name or '').strip()
    if not cleaned:
        return ''
    low = cleaned.casefold()
    if low in _EXTRA_LANG_ALIASES:
        return _EXTRA_LANG_ALIASES[low]
    return _canonical_language(cleaned)


def normalize_profile_level(level: Any) -> str | None:
    """Normalize a profile language level string to A1..C2 or NATIVE."""
    text = str(level or '').strip().upper()
    if text in CEFR_RANK:
        return text
    if _PROFILE_NATIVE_RE.search(text):
        return 'NATIVE'
    m = _CEFR_TOKEN_RE.search(text)
    if m:
        return m.group(1).upper()
    return None


@lru_cache(maxsize=256)
def _cached_profile_languages(
    levels_key: tuple[tuple[str, str], ...],
    langs_key: tuple[str, ...],
) -> tuple[dict[str, tuple[str, str, str]], frozenset[str]]:
    profile_levels: dict[str, tuple[str, str, str]] = {}
    for orig_key, orig_val in levels_key:
        canon = canonical_language(orig_key)
        norm = normalize_profile_level(orig_val)
        if canon and norm:
            profile_levels[canon.casefold()] = (norm, orig_key, orig_val)
    known_languages = frozenset(profile_levels) | frozenset(
        canonical_language(lang).casefold()
        for lang in langs_key
        if lang.strip()
    )
    return profile_levels, known_languages


def _extract_explicit_levels(segment: str) -> list[str]:
    """Extract all explicit CEFR or unambiguous native/native-level tokens in ``segment``."""
    if not segment or _DRIVING_LICENSE_RE.search(segment):
        return []
    cleaned = _OPTIONAL_SUBCLAUSE_RE.sub(' ', segment)
    found: list[str] = []
    for m in _CEFR_TOKEN_RE.finditer(cleaned):
        found.append(m.group(1).upper())
    # If qualitative fluency is offered as an alternative/lower bound to native
    # (e.g. "fluent or native", "verhandlungssicher oder muttersprachlich"),
    # the minimum acceptable level is qualitative -> never a hard native floor.
    if not _QUALITATIVE_NATIVE_ALT_RE.search(cleaned):
        if _NATIVE_LEVEL_RE.search(cleaned):
            found.append('C2')
        elif _STRICT_NATIVE_RE.search(cleaned):
            found.append('NATIVE')
    return found


def _min_level(levels: list[str]) -> str | None:
    if not levels:
        return None
    return min(levels, key=lambda lv: CEFR_RANK[lv])


def _qualitative_estimate(segment: str) -> tuple[str, list[str]] | None:
    for pat, low, rng in _QUALITATIVE_LEVELS:
        if pat.search(segment):
            return low, rng
    return None


def _is_non_language_mention(language: str, clause: str, hit: re.Match) -> bool:
    if language == 'Polish' and _POLISH_VERB_FOLLOW_RE.match(clause, hit.end()):
        return True
    for dom in _NON_LANG_DOMAIN_RE.finditer(clause):
        if dom.start() <= hit.start() < dom.end():
            return True
    if _NON_APPLICANT_LANG_OBJECT_RE.match(clause[hit.end():]):
        return True
    return False


_LEVEL_MODIFIER_STRIP_RE = re.compile(
    r'\b(?:mind(?:estens)?\.?|min\.?|at\s+least|ab|niveau|level|auf|in\s+wort\s+und\s+schrift)\b',
    re.I,
)
_LANG_CLAUSE_SPLIT_RE = re.compile(
    r'\s*;\s*|\s+\b(?:but|aber|jedoch|while|whereas|während)\b\s+|'
    r'\s+[–—-]\s+(?=(?:gute|sehr\s+gute|good|very\s+good|fluent|fließend|verhandlungssicher|'
    r'english|englisch|deutsch|german|french|französisch)\b)',
    re.I,
)


_LEVEL_TOKEN_FRAG = (
    r'(?:A1|A2|B1|B2|C1|C2|native[- ]?(?:level|like|speaker)?|near[- ]?native|'
    r'mother\s+tongue|first\s+language|muttersprach\w*)'
)
_LEVEL_INTERNAL_OR_RE = re.compile(
    r'\b(?:'
    + _LEVEL_TOKEN_FRAG
    + r'(?:[- ]?(?:niveau|level))?\s*(?:or|oder)\s*'
    r'(?:'
    + _LEVEL_TOKEN_FRAG
    + r'(?:[- ]?(?:niveau|level))?|'
    r'(?:(?:an?\s+|ein\w*\s+)?(?:higher|above|better|more|upwards?|equivalent|comparable|similar|'
    r'höher\w*|mehr|besser\w*|vergleichbar\w*|ähnlich\w*|aehnlich\w*|entsprechend\w*)))|'
    r'(?:written|spoken|mündlich\w*|schriftlich\w*|wort)\s+(?:or|oder)\s+'
    r'(?:written|spoken|mündlich\w*|schriftlich\w*|schrift))\b',
    re.I,
)


def _strip_parens_and_levels(text: str) -> str:
    """Strip parentheticals, level-internal ORs, CEFR tokens, and native tokens to inspect bare connectors."""
    s = _PAREN_RE.sub(' ', text)
    s = _LEVEL_INTERNAL_OR_RE.sub(' ', s)
    s = _CEFR_TOKEN_RE.sub(' ', s)
    s = _NATIVE_LEVEL_RE.sub(' ', s)
    s = _STRICT_NATIVE_RE.sub(' ', s)
    s = _LEVEL_MODIFIER_STRIP_RE.sub(' ', s)
    return ' '.join(s.split())


def _split_language_clauses(sentence: str) -> list[str]:
    """Split a sentence into independent language clauses while keeping coordinated pairs intact."""
    working, protected = mask_parens(sentence)
    raw = _LANG_CLAUSE_SPLIT_RE.split(working)
    out = []
    for part in raw:
        restored = unmask_parens(part, protected).strip(' ,.')
        if restored:
            out.append(restored)
    return out


_APPOSITIVE_LEVEL_MOD_RE = re.compile(
    r'^\s*(?:mind(?:estens)?\.?|min\.?|at\s+least|ab|niveau|level|'
    r'A1|A2|B1|B2|C1|C2|native|near[- ]?native|muttersprach\w*|'
    r'in\s+wort\s+und\s+schrift|written|spoken|schriftlich|mündlich)\b',
    re.I,
)
_LANG_SEP_RE = re.compile(r'(?:,\s*|\s+(?:and|und|sowie|or|oder|&|/)\s+)', re.I)


_LANGUAGE_PREFILTER = re.compile(
    r'\b(?:' + '|'.join(pattern.pattern.removeprefix(r'\b').removesuffix(r'\b')
                       for pattern in LANGUAGE_PATTERNS.values()) + r')\b',
    re.I,
)
_OR_WORD_RE = re.compile(r'\b(?:or|oder)\b', re.I)
_AND_WORD_RE = re.compile(r'(?:\b(?:and|und|sowie|plus|as\s+well\s+as)\b|&)', re.I)
_COMMA_ONLY_RE = re.compile(r'\s*,\s*')
_ADDITIVE_TAIL_SPLIT_RE = re.compile(
    r'(?:,|\b(?:and|und|sowie|plus|as\s+well\s+as|with|mit)\b)',
    re.I,
)
_UNRESOLVED_LEADING_OR_RE = re.compile(r'\b(?:or|oder)\s*$', re.I)
_UNRESOLVED_TRAILING_OR_RE = re.compile(
    r'\b(?:or|oder)\s+'
    r'(?!(?:(?:an?\s+|ein\w*\s+)?'
    r'(?:higher|above|better|more|upwards?|equivalent|comparable|similar|'
    r'höher\w*|mehr|besser\w*|vergleichbar\w*|ähnlich\w*|aehnlich\w*|entsprechend\w*)|'
    r'written|spoken|schriftlich\w*|mündlich\w*|schrift)\b)\w',
    re.I,
)


@lru_cache(maxsize=256)
def _extract_language_requirements(
    parsed_texts: tuple[tuple[str, bool, tuple[tuple[str, str], ...]], ...],
) -> list[dict[str, Any]]:
    """Extract structured language requirements (explicit hard floors + qualitative wishes).

    ``parsed_texts`` is a tuple of ``(source, force_requirements_scope, scoped_sentences)``.
    """
    extracted: list[dict[str, Any]] = []
    for source, force_req, sentences in parsed_texts:
        for sentence, scope in sentences:
            if not _LANGUAGE_PREFILTER.search(sentence):
                continue
            if is_source_instruction(sentence) or _DRIVING_LICENSE_RE.search(sentence):
                continue
            if scope == 'optional':
                continue
            if scope == 'benefits' and not REQUIREMENT.search(sentence):
                continue

            req_section = scope == 'requirements' or force_req
            for clause in _split_language_clauses(sentence):
                if NON_REQUIREMENT_V2.search(clause):
                    continue
                if (
                    _NON_APPLICANT_LANG_CLAUSE_RE.search(clause)
                    and not LANGUAGE_APPLICANT_CUE.search(clause)
                    and not _LANG_PROFICIENCY_NOUN_RE.search(clause)
                ):
                    continue
                hits = sorted(
                    (
                        (lang, m)
                        for lang, pat in LANGUAGE_PATTERNS.items()
                        for m in pat.finditer(clause)
                        if not _is_non_language_mention(lang, clause, m)
                    ),
                    key=lambda pair: pair[1].start(),
                )
                if not hits:
                    continue
                # Deduplicate overlapping hits for the same language within one clause.
                dedup_hits: list[tuple[str, re.Match]] = []
                for lang, m in hits:
                    if dedup_hits and m.start() < dedup_hits[-1][1].end():
                        continue
                    dedup_hits.append((lang, m))
                hits = dedup_hits

                # Per-hit local windows bounded by neighbouring language tokens.
                n = len(hits)
                per_hit: list[dict[str, Any]] = []
                for i, (lang, m) in enumerate(hits):
                    prev_end = hits[i - 1][1].end() if i > 0 else 0
                    next_start = hits[i + 1][1].start() if i + 1 < n else len(clause)
                    before = clause[prev_end:m.start()]
                    after = clause[m.end():next_start]

                    # Trim `after` at any separator that introduces a new clause/next language,
                    # but preserve appositive level/modality modifiers (e.g. "Deutschkenntnisse, mindestens Niveau C1").
                    after_for_this = after
                    if i + 1 < n:
                        depth = 0
                        split_pos = None
                        for pos in range(len(after)):
                            ch = after[pos]
                            if ch == '(':
                                depth += 1
                            elif ch == ')':
                                depth = max(0, depth - 1)
                            elif depth == 0:
                                sep_m = _LANG_SEP_RE.match(after, pos)
                                if sep_m:
                                    if sep_m.group(0).lstrip().startswith(',') and _APPOSITIVE_LEVEL_MOD_RE.match(after[sep_m.end():]):
                                        continue
                                    split_pos = pos
                                    break
                        if split_pos is not None:
                            after_for_this = after[:split_pos]

                    # Trim `before` to only what follows the previous separator outside parens.
                    before_for_this = before
                    if i > 0:
                        depth = 0
                        last_sep_end = 0
                        for pos in range(len(before)):
                            ch = before[pos]
                            if ch == '(':
                                depth += 1
                            elif ch == ')':
                                depth = max(0, depth - 1)
                            elif depth == 0:
                                sep_m = _LANG_SEP_RE.match(before, pos)
                                if sep_m:
                                    last_sep_end = sep_m.end()
                        before_for_this = before[last_sep_end:]

                    non_lang_tail = bool(
                        after_for_this.strip()
                        and not LANGUAGE_EXPECTED_FOLLOWING.match(after_for_this)
                        and not _LANG_LEVEL_START_RE.match(after_for_this)
                    )

                    # Polarity check on the local window around this language mention.
                    start = sentence.find(clause) + m.start()
                    pol = requirement_polarity(sentence, start, start + len(m.group()))
                    local_window = f'{before_for_this} {clause[m.start():m.end()]} {after_for_this}'
                    local_optional = (
                        pol in {'optional', 'waived', 'instruction'}
                        or bool(OPTIONAL.search(before_for_this))
                        or (
                            bool(OPTIONAL.search(after_for_this))
                            and not _extract_explicit_levels(
                                _OPTIONAL_SUBCLAUSE_RE.sub(' ', after_for_this)
                            )
                        )
                    )
                    # Check explicit hard level in after_for_this first, then before_for_this.
                    explicit_after = [] if non_lang_tail else _extract_explicit_levels(after_for_this)
                    explicit_before = [] if non_lang_tail else _extract_explicit_levels(before_for_this)
                    explicit_levels = explicit_after or explicit_before
                    explicit_level = _min_level(explicit_levels)
                    level_from_after = bool(explicit_after)
                    level_from_before = bool(explicit_before) and not explicit_after

                    # Distinguish reassuring sufficiency statements ("B2 is enough", "B2 reicht aus")
                    # without a mandatory minimum cue from strict hard floors.
                    sufficiency_only = bool(
                        explicit_level
                        and _CEFR_SUFFICIENCY_ONLY_RE.search(local_window)
                        and not _CEFR_EXPLICIT_MIN_RE.search(local_window)
                    )
                    qual = None
                    if sufficiency_only:
                        qual = (explicit_level, [explicit_level, explicit_level])
                        explicit_level = None
                        level_from_after = False
                        level_from_before = False
                    elif not explicit_level and not non_lang_tail:
                        qual = _qualitative_estimate(f'{before_for_this} {after_for_this}')

                    has_signal = bool(
                        explicit_level
                        or qual
                        or LANGUAGE_FLUENCY.search(local_window)
                        or LANGUAGE_APPLICANT_CUE.search(local_window)
                        or (req_section and _LANG_MODALITY_RE.search(local_window))
                        or (req_section and len(clause.split()) <= 10)
                        or REQUIREMENT.search(clause)
                    )
                    per_hit.append({
                        'lang': lang,
                        'match': m,
                        'before': before_for_this,
                        'after': after_for_this,
                        'local_window': local_window,
                        'optional': local_optional,
                        'explicit_level': explicit_level,
                        'level_from_after': level_from_after,
                        'level_from_before': level_from_before,
                        'qual': qual,
                        'has_signal': has_signal,
                        'non_lang_tail': non_lang_tail,
                        'ambiguous_floor': False,
                    })

                # Propagate SHARED trailing explicit floor backward across tight AND/OR coordination:
                # e.g. "Deutsch und Englisch (C1)", "Deutsch oder Französisch (mindestens C1)",
                # "Deutsch, Englisch oder Französisch (mindestens C1)".
                for i in range(n - 2, -1, -1):
                    connector = clause[hits[i][1].end():hits[i + 1][1].start()]
                    if (
                        not per_hit[i]['explicit_level']
                        and per_hit[i + 1]['explicit_level']
                        and per_hit[i + 1]['level_from_after']
                        and _TIGHT_COORD_CONNECTOR.fullmatch(connector)
                    ):
                        per_hit[i]['explicit_level'] = per_hit[i + 1]['explicit_level']
                        per_hit[i]['level_from_after'] = True
                        per_hit[i]['qual'] = None
                        per_hit[i]['has_signal'] = True
                    elif (
                        not per_hit[i]['explicit_level']
                        and not per_hit[i]['qual']
                        and per_hit[i + 1]['qual']
                        and _TIGHT_COORD_CONNECTOR.fullmatch(connector)
                    ):
                        per_hit[i]['qual'] = per_hit[i + 1]['qual']
                        per_hit[i]['has_signal'] = True

                # Propagate SHARED leading explicit floor forward across tight AND/OR coordination:
                # e.g. "Mindestens C1 in Deutsch oder Französisch", "Sehr gute Deutsch- und Englischkenntnisse".
                for i in range(n - 1):
                    connector = clause[hits[i][1].end():hits[i + 1][1].start()]
                    if (
                        per_hit[i]['explicit_level']
                        and per_hit[i]['level_from_before']
                        and not per_hit[i + 1]['explicit_level']
                        and not per_hit[i + 1]['qual']
                        and _TIGHT_COORD_CONNECTOR.fullmatch(connector)
                    ):
                        per_hit[i + 1]['explicit_level'] = per_hit[i]['explicit_level']
                        per_hit[i + 1]['level_from_before'] = True
                        per_hit[i + 1]['has_signal'] = True
                    elif (
                        per_hit[i]['qual']
                        and not per_hit[i + 1]['explicit_level']
                        and not per_hit[i + 1]['qual']
                        and _TIGHT_COORD_CONNECTOR.fullmatch(connector)
                    ):
                        per_hit[i + 1]['qual'] = per_hit[i]['qual']
                        per_hit[i + 1]['has_signal'] = True

                # Bind every explicit floor to an applicant requirement (applicant cue /
                # requirements section / modality / minimum requirement cue). Ambiguous
                # wording without applicant binding becomes a review flag, never a hard hide.
                clause_other_actor = bool(
                    _is_other_actor_subject(clause)
                    or _EXTERNAL_LANGUAGE_USE.search(clause)
                )
                clause_duty_action = bool(
                    _DUTY_ACTION_LEAD_RE.search(clause)
                    and not LANGUAGE_APPLICANT_CUE.search(clause)
                    and not REQUIREMENT.search(clause)
                )
                clause_has_app_cue = bool(
                    req_section
                    or LANGUAGE_APPLICANT_CUE.search(clause)
                    or REQUIREMENT.search(clause)
                    or _LANG_MODALITY_RE.search(clause)
                    or (_EXPLICIT_MIN_CUE_RE.search(clause) and not clause_duty_action)
                    or (
                        len(clause.split()) <= 8
                        and scope != 'duties'
                        and not clause_duty_action
                        and not _NON_APPLICANT_PREPOSITION_RE.search(clause)
                    )
                )
                for item in per_hit:
                    if not item['explicit_level']:
                        continue
                    item_bound = (
                        not clause_other_actor
                        and not clause_duty_action
                        and (clause_has_app_cue or bool(LANGUAGE_FLUENCY.search(item['local_window'])))
                    )
                    if not item_bound:
                        lv = item['explicit_level']
                        item['qual'] = (lv, [lv, lv])
                        item['explicit_level'] = None
                        item['ambiguous_floor'] = True

                # Detect inter-language OR groups using connector stripped of parentheticals/levels
                # so "Deutsch (C1 oder C2) und Englisch" is NOT an OR group, while
                # "Deutsch oder Französisch (mindestens C1)" and
                # "Deutsch, Englisch oder Französisch (mindestens C1)" ARE OR groups.
                connectors = [
                    _strip_parens_and_levels(clause[left.end():right.start()])
                    for (_, left), (_, right) in zip(hits, hits[1:])
                ]
                or_boundaries = {
                    i for i, connector in enumerate(connectors)
                    if _OR_WORD_RE.search(connector) and not _AND_WORD_RE.search(connector)
                }
                # Comma-separated enumerations ending in OR also denote alternatives.
                for boundary in tuple(or_boundaries):
                    i = boundary - 1
                    while i >= 0 and _COMMA_ONLY_RE.fullmatch(connectors[i]):
                        or_boundaries.add(i)
                        i -= 1
                group = hashlib.sha256(f'{source}:{clause}'.encode()).hexdigest()[:16] if or_boundaries else None

                # An OR arm with no identifiable language condition cannot
                # establish an explicit incompatibility. Truncate trailing text at
                # additive clause joins ('and'/'und'/'sowie'/, ) so unrelated skill
                # ORs do not mask a language floor, and exempt level-equivalence or
                # multi-level range wording ('B2 or C1', 'or equivalent', 'oder besser').
                raw_trailing = clause[hits[-1][1].end():]
                raw_leading = clause[:hits[0][1].start()]
                trailing_scope = _ADDITIVE_TAIL_SPLIT_RE.split(
                    _strip_parens_and_levels(raw_trailing), maxsplit=1
                )[0]
                leading_stripped = _strip_parens_and_levels(raw_leading)
                unresolved_leading = bool(
                    _UNRESOLVED_LEADING_OR_RE.search(leading_stripped)
                    and len(_extract_explicit_levels(raw_leading)) < 2
                )
                unresolved_arm = bool(
                    _UNRESOLVED_TRAILING_OR_RE.search(trailing_scope)
                    or unresolved_leading
                )
                for i, item in enumerate(per_hit):
                    ambiguous = item['ambiguous_floor']
                    explicit_level = item['explicit_level']
                    if item['optional'] or not item['has_signal'] or item['non_lang_tail']:
                        if not group:
                            continue
                        explicit_level = None
                        ambiguous = True
                    if unresolved_arm and explicit_level:
                        ambiguous = True
                    # In neutral/duties scope, require explicit level, fluency cue, or requirement cue.
                    if not group and not req_section and not (
                        explicit_level
                        or item['qual']
                        or REQUIREMENT.search(clause)
                        or LANGUAGE_APPLICANT_CUE.search(clause)
                    ):
                        continue
                    lang = item['lang']
                    extracted.append({
                        'language': lang,
                        'explicit_level': explicit_level,
                        'qualitative_level': item['qual'][0] if item['qual'] else None,
                        'qualitative_range': item['qual'][1] if item['qual'] else None,
                        'ambiguous_floor': ambiguous,
                        'alternative_group': group,
                        'alternative_arm': sum(boundary < i for boundary in or_boundaries),
                        'source': source,
                        'job_quote': clause[:280],
                    })
    return extracted


def _evaluate_languages(
    parsed_texts: tuple[tuple[str, bool, tuple[tuple[str, str], ...]], ...],
    profile: dict,
) -> dict[str, Any]:
    """Evaluate all language requirements against saved profile language facts."""
    raw_levels = profile.get('language_levels') or {}
    levels_key = tuple(sorted((str(k), str(v)) for k, v in raw_levels.items()))
    langs_key = tuple(str(lang) for lang in (profile.get('languages') or []) if str(lang).strip())
    profile_levels, known_languages = _cached_profile_languages(levels_key, langs_key)

    rows = _extract_language_requirements(parsed_texts)

    # Collapse independent mandatory mentions of the same language to the strictest explicit floor
    # (preserving OR groups intact).
    grouped: dict[str, list[dict[str, Any]]] = {}
    mandatory_by_lang: dict[str, dict[str, Any]] = {}
    for row in rows:
        gid = row.get('alternative_group')
        if gid:
            grouped.setdefault(gid, []).append(row)
        else:
            key = row['language'].casefold()
            prev = mandatory_by_lang.get(key)
            if prev is None:
                mandatory_by_lang[key] = row
            else:
                prev_exp = CEFR_RANK.get(prev['explicit_level'] or '', 0)
                curr_exp = CEFR_RANK.get(row['explicit_level'] or '', 0)
                if curr_exp > prev_exp:
                    mandatory_by_lang[key] = row
                elif curr_exp == prev_exp == 0:
                    prev_q = CEFR_RANK.get(prev['qualitative_level'] or '', 0)
                    curr_q = CEFR_RANK.get(row['qualitative_level'] or '', 0)
                    if curr_q > prev_q:
                        mandatory_by_lang[key] = row

    all_groups: list[list[dict[str, Any]]] = [
        *([row] for row in mandatory_by_lang.values()),
        *grouped.values(),
    ]

    hard_fails: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    met_notes: list[str] = []
    met_details: list[dict[str, Any]] = []

    def _eval_single(
        req: dict[str, Any],
        *,
        sibling_explicit_floor: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        lang = req['language']
        lang_key = lang.casefold()
        exp_lv = req['explicit_level']
        qual_lv = req['qualitative_level']
        saved = profile_levels.get(lang_key)

        if exp_lv:
            if saved is not None:
                cand_lv, orig_k, orig_v = saved
                if CEFR_RANK[cand_lv] < CEFR_RANK[exp_lv]:
                    ambiguous = req['ambiguous_floor']
                    return 'unknown' if ambiguous else 'fail', {
                        'kind': 'language_cefr',
                        'gate': 'language',
                        'language': lang,
                        'required_level': exp_lv,
                        'profile_level': cand_lv,
                        'detail': (
                            f'{lang} {exp_lv} stated; verify the alternative requirement'
                            if ambiguous else f'{lang} {exp_lv} required; profile has {cand_lv}'
                        ),
                        'job_source': req['source'],
                        'job_quote': req['job_quote'],
                        'profile_source': f'profile.language_levels.{orig_k}',
                        'profile_quote': f'{orig_k}: {orig_v}',
                    }
                return 'pass', {
                    'kind': 'language_cefr',
                    'gate': 'language',
                    'language': lang,
                    'detail': f'{lang} {exp_lv} satisfied by profile {cand_lv}',
                    'job_source': req['source'],
                    'job_quote': req['job_quote'],
                    'profile_source': f'profile.language_levels.{orig_k}',
                    'profile_quote': f'{orig_k}: {orig_v}',
                }
            return 'unknown', {
                'kind': 'language_unverified',
                'gate': 'language',
                'language': lang,
                'detail': f'{lang} {exp_lv} required; profile level not provided',
                'job_source': req['source'],
                'job_quote': req['job_quote'],
                'profile_source': 'profile.language_levels',
                'profile_quote': '',
            }

        if qual_lv:
            if saved is not None:
                cand_lv, orig_k, orig_v = saved
                if CEFR_RANK[cand_lv] < CEFR_RANK[qual_lv]:
                    rng = req.get('qualitative_range') or [qual_lv, qual_lv]
                    if req.get('ambiguous_floor'):
                        detail = (
                            f'{lang} {qual_lv} mentioned without clear applicant requirement; '
                            f'profile has {cand_lv}; verify proficiency'
                        )
                    else:
                        detail = (
                            f'{lang} qualitative fluency (~{rng[0]}–{rng[-1]}) requested; '
                            f'profile has {cand_lv}; verify proficiency'
                        )
                    return 'unknown', {
                        'kind': 'language_qualitative',
                        'gate': 'language',
                        'language': lang,
                        'detail': detail,
                        'job_source': req['source'],
                        'job_quote': req['job_quote'],
                        'profile_source': f'profile.language_levels.{orig_k}',
                        'profile_quote': f'{orig_k}: {orig_v}',
                    }
                return 'pass', {
                    'kind': 'language_qualitative',
                    'gate': 'language',
                    'language': lang,
                    'detail': f'{lang} fluency aligns with profile {cand_lv}',
                    'job_source': req['source'],
                    'job_quote': req['job_quote'],
                    'profile_source': f'profile.language_levels.{orig_k}',
                    'profile_quote': f'{orig_k}: {orig_v}',
                }
            return 'unknown', {
                'kind': 'language_qualitative',
                'gate': 'language',
                'language': lang,
                'detail': f'{lang} fluency requested; profile level not provided',
                'job_source': req['source'],
                'job_quote': req['job_quote'],
                'profile_source': 'profile.language_levels',
                'profile_quote': '',
            }

        # Bare language requirement with no stated level or qualitative descriptor.
        # If paired in an OR group with an explicit floor (e.g. "Deutsch (C1) oder Französisch"),
        # treat an ambiguous bare arm as a review flag unless profile meets the sibling floor.
        if req['ambiguous_floor']:
            return 'unknown', {
                'kind': 'language_unverified',
                'gate': 'language',
                'language': lang,
                'detail': f'Verify the {lang} alternative requirement',
                'job_source': req['source'],
                'job_quote': req['job_quote'],
                'profile_source': 'profile.language_levels',
                'profile_quote': '',
            }
        if sibling_explicit_floor:
            if saved is not None:
                cand_lv, orig_k, orig_v = saved
                if CEFR_RANK[cand_lv] >= CEFR_RANK[sibling_explicit_floor]:
                    return 'pass', {
                        'kind': 'language_cefr',
                        'gate': 'language',
                        'language': lang,
                        'detail': f'{lang} satisfied by profile {cand_lv}',
                        'job_source': req['source'],
                        'job_quote': req['job_quote'],
                        'profile_source': f'profile.language_levels.{orig_k}',
                        'profile_quote': f'{orig_k}: {orig_v}',
                    }
                return 'unknown', {
                    'kind': 'language_unverified',
                    'gate': 'language',
                    'language': lang,
                    'detail': (
                        f'{lang} level unspecified alongside {sibling_explicit_floor} alternative; '
                        f'profile has {cand_lv}; verify proficiency'
                    ),
                    'job_source': req['source'],
                    'job_quote': req['job_quote'],
                    'profile_source': f'profile.language_levels.{orig_k}',
                    'profile_quote': f'{orig_k}: {orig_v}',
                }
        if lang_key in known_languages:
            return 'pass', {
                'kind': 'language',
                'gate': 'language',
                'language': lang,
                'detail': f'{lang} listed in profile',
                'job_source': req['source'],
                'job_quote': req['job_quote'],
                'profile_source': 'profile.languages',
                'profile_quote': lang,
            }
        return 'unknown', {
            'kind': 'language_unverified',
            'gate': 'language',
            'language': lang,
            'detail': f'{lang} requested; not listed in profile languages',
            'job_source': req['source'],
            'job_quote': req['job_quote'],
            'profile_source': 'profile.languages',
            'profile_quote': '',
        }

    for group in all_groups:
        sibling_floor = None
        if len(group) > 1:
            explicit_floors = [r['explicit_level'] for r in group if r.get('explicit_level')]
            if explicit_floors:
                sibling_floor = _min_level(explicit_floors)
        arm_outcomes: dict[int, list[tuple[str, dict[str, Any]]]] = {}
        for req in group:
            arm_outcomes.setdefault(req['alternative_arm'], []).append(
                _eval_single(req, sibling_explicit_floor=sibling_floor)
            )
        # An AND arm fails on any cited incompatibility and passes only when
        # every member passes. Keep the deciding evidence for each OR arm.
        precedence = {'pass': 0, 'unknown': 1, 'fail': 2}
        outcomes = [
            max(arm, key=lambda outcome: precedence[outcome[0]])
            for arm in arm_outcomes.values()
        ]
        if len(group) == 1:
            status, payload = outcomes[0]
            if status == 'fail':
                hard_fails.append(payload)
            elif status == 'unknown':
                flags.append(payload)
            else:
                met_notes.append(payload['detail'])
                met_details.append(payload)
        else:
            # OR-alternative between languages: passes if ANY arm passes;
            # hard-fails ONLY if ALL arms explicitly hard-fail.
            if any(st == 'pass' for st, _ in outcomes):
                passed = next(p for st, p in outcomes if st == 'pass')
                note = 'Language alternative met: ' + passed['detail']
                met_notes.append(note)
                met_details.append({**passed, 'detail': note})
            elif all(st == 'fail' for st, _ in outcomes):
                hard_fails.append({
                    'kind': 'language_cefr',
                    'gate': 'language',
                    'detail': 'Language alternatives unmet: ' + ' or '.join(p['detail'] for _, p in outcomes),
                    'job_source': outcomes[0][1].get('job_source', 'description'),
                    'job_quote': outcomes[0][1]['job_quote'],
                    'profile_source': ', '.join(dict.fromkeys(p['profile_source'] for _, p in outcomes)),
                    'profile_quote': '; '.join(dict.fromkeys(p['profile_quote'] for _, p in outcomes)),
                })
            else:
                flags.append({
                    'kind': 'language_unverified',
                    'gate': 'language',
                    'detail': 'Verify language alternative: ' + ' or '.join(p['detail'] for _, p in outcomes),
                    'job_source': outcomes[0][1].get('job_source', 'description'),
                    'job_quote': outcomes[0][1]['job_quote'],
                    'profile_source': 'profile.language_levels',
                    'profile_quote': '; '.join(p.get('profile_quote', '') for _, p in outcomes if p.get('profile_quote')),
                })

    status = 'fail' if hard_fails else 'unknown' if flags else 'pass'
    reason = (
        '; '.join(item['detail'] for item in hard_fails)[:500]
        if hard_fails
        else '; '.join(item['detail'] for item in flags)[:500]
        if flags
        else ('Saved language evidence satisfies stated language requirements.' if all_groups else 'No mandatory language requirement was stated.')
    )
    return {
        'status': status,
        'reason': reason,
        'hard_fails': hard_fails,
        'flags': flags,
        'met': met_notes,
        'met_details': met_details,
        'extracted': rows,
    }
