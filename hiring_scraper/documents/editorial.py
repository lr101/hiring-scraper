"""Deterministic editorial checks for generated prose."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from hiring_scraper.documents.schema import (
    MAX_UNIT_TEXT,
    CvDocument,
    DocumentLanguage,
    Entry,
    LetterDocument,
    Section,
    Unit,
    walk_units,
)
from hiring_scraper.documents.text import unit_full_text

FIRST_PERSON = re.compile(r"\b(?:I|ich)\b", re.IGNORECASE)
_FIRST_PERSON_I = re.compile(r"\bI\b")
_FIRST_PERSON_CONTRACTION = re.compile(r"\bi['’](?:m|ve|d|ll)\b", re.I)
_FIRST_PERSON_DE = re.compile(r"\bich\b", re.I)
_ROMAN_PREFIX = re.compile(
    r'\b(?:phase|level|part|section|tier|stage|grade|class|type|version|annex|appendix|'
    r'category|step|track|series|module|generation)\s+$', re.I,
)
EN_CONTRACTION = re.compile(
    r"\b(?:\w+n['’]t|\w+['’](?:m|ve|re|ll|d)|(?:it|that|there|what|who|how|he|she|here|let|where)['’]s)\b", re.I,
)
CONDITIONAL = re.compile(r"\b(?:würde(?:st|n|t)?|would)\b", re.I)
GENERIC_ENTRY_LABEL = re.compile(
    r'^(?:university|college|hochschule|universität|internship|praktikum|education|ausbildung)$', re.I,
)
_ROLE_ENTRY_LABEL = re.compile(r'^(?:internship|praktikum)$', re.I)
LEGAL_SUFFIX = re.compile(
    r'(?:[,\s]+(?:(?:GmbH|gGmbH|AG|SE|UG)\s*(?:&|und)\s*Co\.?\s*(?:KGaA|KG|OHG)|'
    r'GmbH|gGmbH|mbH|AG|SE|Inc(?:orporated)?|Corp(?:oration)?|Ltd|Limited|LL[CP]|PLC|'
    r'L\.L\.[CP]|e\.?\s*V|B\.V|N\.V|S\.A\.R\.L|S\.à\s*r\.l|S\.r\.l|S\.L|S\.A|'
    r'Co|KG|KGaA|OHG|UG|GbR|S[àa]rl|SAS|SA|BV|NV|Pty)\.?)+$',
    re.I,
)
_QUOTED_SPAN = re.compile(r'[“"„][^”"‟]{1,240}[”"‟]|(?<!\w)\'[^\']{3,200}\'(?!\w)')
_DEGREE = re.compile(
    r'^(?:[BM]\.?\s?(?:Sc|Eng|Tech|A|S)\.?\b|Bachelor\b|Master\b|Diplom\b|PhD\b|Ph\.D\.)', re.I,
)
_CONNECTORS = frozenset(
    'a an and as at by for from in of on or the to with und oder am an auf aus bei das der die '
    'des den dem ein eine einer eines für im in mit nach über von zur zum '
    'across through between during within around into onto toward towards without while whereas '
    'während zwischen sowie dabei damit darüber darunter unter über durch gegen ohne'.split()
)
_NGRAM_STOPWORDS = _CONNECTORS | frozenset(
    'i me my we our us you your they their them he him his she her it its that this these those '
    'is am are was were be been being have has had do does did will would shall should can could '
    'may might must also about upon over under than then when where which who whom whose what how '
    'why because although if but nor not so very more most such each every other another some any '
    'all both few many together along ahead apart away back down even just now off out still up well '
    'ich mir mich wir uns sie ihr ihre ihrem ihren ihres er ihm ihn es sein seine seinem seinen '
    'seiner ist sind war waren gewesen haben hat hatte hatten habe werde wird wurden wurde kann '
    'können konnte könnte soll sollte will wollte muss müssen darf dürfen mag möchte um zu vom '
    'beim ins ans aufs als wie auch noch nur aber denn wenn weil dass ob sich man einem einen '
    'nicht kein keine keinem keinen keiner zusammen gemeinsam bereits dafür daran darauf daraus '
    'dazu ebenfalls etwa immer jedoch schon sehr sogar wieder weiter'.split()
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

# Profile voice validation (sentence-initial verb/imperative detection)
_DE_CONJUGATED_VERB_OPENERS = frozenset(
    'bringe verfüge kombiniere verbinde übersetze entwickle unterstütze biete arbeite leite '
    'koordiniere analysiere gestalte plane prüfe steuere strukturiere nutze setze führe '
    'übernehme kenne habe bin kann möchte will schaffe löse baue erstelle konzipiere '
    'organisiere priorisiere recherchiere begleite betreue verfolge kläre stimme bewerte '
    'dokumentiere standardisiere kommuniziere identifiziere liefere trage wirke sammle '
    'überführe bringst bringe'.split()
)
_DE_PRETERITE_VERB_OPENERS = frozenset(
    'koordinierte entwickelte leitete recherchierte erstellte konzipierte analysierte '
    'arbeitete unterstützte organisierte betreute führte steuerte prüfte baute überführte '
    'trug wirkte verfolgte standardisierte priorisierte plante gestaltete begleitete '
    'stimmte nutzte setzte sammelte übernahm bewertete dokumentierte strukturierte '
    'kombinierte verband übersetzte klärte brachte verfügte'.split()
)
_EN_IMPERATIVE_VERB_OPENERS = frozenset(
    'combine turn bring build create develop deliver manage coordinate translate connect '
    'align analyze analyse measure use apply collaborate define prioritize prioritise gather '
    'organize organise validate evaluate assess improve optimize optimise streamline '
    'standardize standardise ensure enable help make take give provide offer demonstrate '
    'leverage utilize utilise execute implement launch own handle oversee supervise guide '
    'facilitate communicate present identify solve produce prepare maintain monitor refine '
    'iterate conduct contribute participate structure'.split()
)
_EN_AMBIGUOUS_VERB_OPENERS = frozenset(
    'lead design research support plan focus test track drive bridge work partner report review map'.split()
)
_EN_ROLE_OR_NOUN_FOLLOWERS = frozenset(
    'engineer manager designer analyst specialist coordinator assistant developer architect '
    'consultant scientist researcher director officer planner strategist lead head graduate '
    'professional areas topics projects skills experience background team teams work focus '
    'and & of for in on across includes spans covers'.split()
)
_EN_THIRD_PERSON_RE = re.compile(r'\b(?:she|he|her|his|herself|himself)\b', re.I)
_DE_THIRD_PERSON_REL_RE = re.compile(
    r',\s*(?:die|den|das|bei\s+der|in\s+der|mit\s+der|an\s+der|wo)\s+(?:sie|er)\b', re.I,
)
_DE_THIRD_PERSON_SUBJ_RE = re.compile(
    r'\b(?:sie|er)\s+(?:koordinierte|leitete|entwickelte|erstellte|führte|begleitete|'
    r'übernahm|verantwortete|steuerte|prüfte|analysierte|gestaltete|organisierte|'
    r'unterstützte|definierte|präzisierte|strukturierte|konzipierte|plante)\b', re.I,
)
_EN_PAST_VERB_OPENERS = frozenset(
    'led built designed developed managed coordinated created delivered gathered analyzed '
    'analysed tracked organized organised translated supported worked provided produced '
    'conducted contributed participated prioritized prioritised defined mapped evaluated '
    'tested researched planned implemented standardized standardised streamlined facilitated '
    'structured validated assessed improved aligned connected combined turned'.split()
)

# German compound coordination hyphen (Ergänzungsstrich) detection
_DE_GENITIVE_DETERMINERS = frozenset(
    'des eines dieses seines ihres unseres jedes keines deren dessen'.split()
)
_DE_ARTICLES_OR_PREPS = frozenset(
    'der die das den dem des ein eine einen einem einer eines im am vom beim zum zur ins ans aufs'.split()
)
_DE_BOUND_STEM_PREFIXES = (
    'ungs', 'täts', 'keits', 'heits', 'schafts', 'tions',
)
_DE_BOUND_EXACT_FIRST = frozenset(
    'geschäfts arbeits betriebs vertriebs sicherheits wirtschafts qualitäts unternehmens '
    'vor ein soft hard ist auf ab an aus be zu in hoch neu bestands'.split()
)
_DE_COMPOUND_MODIFIER_FIRST = frozenset(
    'markt nutzer kunden produkt projekt prozess kosten system fach bauteil daten '
    'software hardware konzept design team stakeholder lieferanten'.split()
)
_DE_COMPOUND_HEAD_SUFFIXES = (
    'bedürfnisse', 'bedürfnis', 'anforderungen', 'anforderung', 'recherchen', 'recherche',
    'analysen', 'analyse', 'entwicklung', 'optimierung', 'management', 'koordination',
    'planung', 'steuerung', 'prozesse', 'prozess', 'abläufe', 'ablauf', 'ziele', 'ziel',
    'feedback', 'daten', 'kosten', 'kriterien', 'konzepte', 'konzept', 'lösungen', 'lösung',
    'modelle', 'modell', 'unterlagen', 'standards', 'strukturen', 'kennzahlen', 'berichte',
    'bewertung', 'validierung', 'prüfung', 'spezifikationen', 'kräfte', 'teile', 'ware',
    'gänge', 'bau', 'kauf', 'bildung', 'land',
)
_DE_COORD_RE = re.compile(r'\b([A-ZÄÖÜ][a-zäöüß]{1,})\s+(und|oder|sowie)\s+([A-ZÄÖÜ][a-zäöüß]{5,})\b')

# Awkward German verb-noun constructions
_DE_AWKWARD_VERB_RE = re.compile(
    r'\b(?:leitete\s+(?:ich\s+)?die\s+koordination|führte\s+(?:ich\s+)?die\s+koordination\s+durch)\b',
    re.I,
)

# High-signal scope qualifiers that must be grounded in cited evidence
_QUALIFIER_STEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('technical', re.compile(r'\b(?:technical|technisch(?:e[nrms]?)?)\b', re.I)),
    ('strategic', re.compile(r'\b(?:strategic|strategisch(?:e[nrms]?)?)\b', re.I)),
    ('commercial', re.compile(r'\b(?:commercial|kommerziell(?:e[nrms]?)?)\b', re.I)),
    ('enterprise', re.compile(r'\benterprise\b', re.I)),
    ('global', re.compile(r'\bglobal(?:e[nrms]?)?\b', re.I)),
    ('software', re.compile(r'\bsoftware\b', re.I)),
)

# German non-noun words that MUST start lowercase after a comma-ended salutation
_DE_LOWERCASE_OPENERS = frozenset(
    'an bei mit für in als durch nach seit über von zu zur zum ich mein meine meinem meinen '
    'meiner meines der die das den dem ein eine einen einem einer eines diese dieser dieses '
    'diesem diesen was wie wenn weil während besonders gerade schon bereits aktuell derzeit '
    'gern gerne viele sowohl nicht direkt aus seitdem warum wo obwohl damit dabei dafür dort '
    'hier heute oft stets im am vom beim ins ans aufs um vor unter neben hinter zwischen '
    'trotz wegen statt ohne gegen bis seitens zudem außerdem darüber hinaus'.split()
)

# Cliché two-word ending starters that should not repeat across paragraphs
_CLICHE_ENDING_PREFIXES = frozenset({
    ('diese', 'erfahrung'),
    ('dieses', 'wissen'),
    ('diese', 'kenntnisse'),
    ('this', 'experience'),
    ('that', 'experience'),
})

_NON_VERB_OPENERS = frozenset(
    'in im am an auf aus bei für mit nach über unter von vor zu zum zur '
    'at by for from in into of on over through to under with'.split()
)



def has_first_person(text: str) -> bool:
    if _FIRST_PERSON_DE.search(text) or _FIRST_PERSON_CONTRACTION.search(text):
        return True
    for match in _FIRST_PERSON_I.finditer(text):
        if not _ROMAN_PREFIX.search(text[:match.start()]):
            return True
    return False


def has_third_person_applicant(text: str, language: DocumentLanguage) -> bool:
    """Detect third-person pronouns referring to the applicant in CV prose (e.g. 'die sie koordinierte')."""
    cleaned = _QUOTED_SPAN.sub(' ', text)
    if language == 'de':
        return bool(_DE_THIRD_PERSON_REL_RE.search(cleaned) or _DE_THIRD_PERSON_SUBJ_RE.search(cleaned))
    return bool(_EN_THIRD_PERSON_RE.search(cleaned))


def has_en_contraction(text: str) -> bool:
    return bool(EN_CONTRACTION.search(_QUOTED_SPAN.sub(' ', text)))


def employer_key(company: str, entry_id: str) -> str:
    """Normalize an employer or institution name so entries at the same organization group consistently."""
    cleaned = ' '.join(company.split())
    if not cleaned or GENERIC_ENTRY_LABEL.fullmatch(cleaned):
        return entry_id
    stripped = re.sub(r'\s*\([^()]*\)\s*$', '', cleaned).strip()
    stripped = LEGAL_SUFFIX.sub('', stripped).rstrip(' ,.-–—').strip()
    normalized = ' '.join(re.findall(r'[^\W_]+', (stripped or cleaned).casefold()))
    if not normalized or GENERIC_ENTRY_LABEL.fullmatch(normalized):
        return entry_id
    return normalized


def words(text: str) -> list[str]:
    return re.findall(r'[^\W_]+', text.casefold())


def ngrams(text: str, size: int = 8) -> set[tuple[str, ...]]:
    tokens = words(text)
    return set(zip(*(tokens[offset:] for offset in range(size)), strict=False))


def _strip_ignored_phrases(text: str, ignore_phrases: Iterable[str]) -> list[str]:
    cleaned = _QUOTED_SPAN.sub('\n', text)
    phrases = sorted(
        {' '.join(p.split()) for p in ignore_phrases if len(words(p)) >= 3},
        key=len,
        reverse=True,
    )
    for phrase in phrases:
        cleaned = re.sub(rf'(?<!\w){re.escape(phrase)}(?!\w)', '\n', cleaned, flags=re.I)
    cleaned = re.sub(r'[.!?;:]+(?:\s+|$)', '\n', cleaned)
    return [segment for segment in cleaned.split('\n') if segment.strip()]


def _prose_words(text: str) -> list[str]:
    return re.findall(r'[^\W_]+(?:[-–][^\W_]+)*', text.casefold())


def prose_ngrams(
    text: str,
    *,
    size: int = 8,
    ignore_phrases: Iterable[str] = (),
    min_content: int = 6,
) -> set[tuple[str, ...]]:
    """Extract substantive n-grams while ignoring quoted titles, proper-name phrases and stopword runs."""
    threshold = min(min_content, size)
    result: set[tuple[str, ...]] = set()
    for segment in _strip_ignored_phrases(text, ignore_phrases):
        tokens = _prose_words(segment)
        for gram in zip(*(tokens[offset:] for offset in range(size)), strict=False):
            if sum(1 for token in gram if token not in _NGRAM_STOPWORDS) >= threshold:
                result.add(gram)
    return result


def cv_ignore_phrases(cv: CvDocument, extra: Iterable[str] = ()) -> tuple[str, ...]:
    phrases: list[str] = [cv.header.name, *extra]
    for section in cv.sections:
        for entry in section.entries:
            phrases.extend((entry.locked.company, entry.locked.title))
    return tuple(dict.fromkeys(' '.join(p.split()) for p in phrases if p and len(words(p)) >= 3))


def cv_ngrams(cv: CvDocument, *, ignore_phrases: Iterable[str] = ()) -> set[tuple[str, ...]]:
    """Extract substantive 8-grams from visible CV prose (profile and experience/education bullets)."""
    ignored = cv_ignore_phrases(cv, ignore_phrases)
    grams: set[tuple[str, ...]] = set()
    for section in cv.sections:
        if section.kind == 'profile':
            for unit in section.units:
                if not unit.hidden:
                    grams.update(prose_ngrams(unit.text, ignore_phrases=ignored))
        elif section.kind in ('experience', 'education'):
            for entry in section.entries:
                for unit in entry.units:
                    if not unit.hidden:
                        grams.update(prose_ngrams(unit.text, ignore_phrases=ignored))
    return grams


def content_words(text: str) -> set[str]:
    return set(words(text)) - _CONNECTORS


def near_duplicate(left: str, right: str) -> bool:
    if prose_ngrams(left) & prose_ngrams(right):
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


def _compound_parts_supported(token: str, corpus_tokens: set[str]) -> bool:
    if any(_same_word(token, src) for src in corpus_tokens):
        return True
    if not token.isalpha() or len(token) < 8:
        return False
    # Check German/English binary compound split with optional linking morpheme (Fugenelement).
    for split_idx in range(4, len(token) - 3):
        left = token[:split_idx]
        if not any(_same_word(left, src) for src in corpus_tokens):
            continue
        for link in ('', 's', 'es', 'n', 'en', 'e'):
            rest_start = split_idx + len(link)
            if rest_start > len(token) - 4:
                continue
            if link and not token[split_idx:rest_start] == link:
                continue
            right = token[rest_start:]
            if any(_same_word(right, src) for src in corpus_tokens):
                return True
    return False


def _posting_matches_token(token: str, posting_tokens: set[str]) -> bool:
    if any(_same_word(token, kw) for kw in posting_tokens):
        return True
    if not token.isalpha() or len(token) < 5:
        return False
    stem = _stem(token)
    for kw in posting_tokens:
        if not kw.isalpha() or len(kw) < 5:
            continue
        kw_stem = _stem(kw)
        if (len(kw_stem) >= 5 and kw_stem in token) or (len(stem) >= 5 and stem in kw):
            return True
    return False


def supports_competency(text: str, cited: Iterable[str], posting: str) -> bool:
    all_words = words(text)
    if not (1 <= len(all_words) <= 4):
        return False
    tokens = content_words(text)
    source_tokens = set(words(' '.join(cited)))
    posting_tokens = content_words(posting)
    return (
        bool(tokens)
        and all(_compound_parts_supported(token, source_tokens) for token in tokens)
        and any(_posting_matches_token(token, posting_tokens) for token in tokens)
    )


def profile_voice_issue(text: str, language: DocumentLanguage) -> str | None:
    """Detect verb-first or imperative CV profile openings that violate noun-phrase-led summary style."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    for idx, sentence in enumerate(sentences):
        tokens = re.findall(r'[^\W\d_]+', sentence)
        if not tokens:
            continue
        first = tokens[0].casefold()
        second = tokens[1].casefold() if len(tokens) > 1 else ''
        if language == 'de':
            if first in _DE_CONJUGATED_VERB_OPENERS:
                return 'de_verb_opener'
            if idx == 0 and first in _DE_PRETERITE_VERB_OPENERS:
                return 'de_first_sentence_verb'
        else:
            if first in _EN_IMPERATIVE_VERB_OPENERS:
                return 'en_imperative_opener'
            if first in _EN_AMBIGUOUS_VERB_OPENERS and second not in _EN_ROLE_OR_NOUN_FOLLOWERS:
                return 'en_imperative_opener'
            if idx == 0 and first in _EN_PAST_VERB_OPENERS:
                return 'en_first_sentence_verb'
    return None


def repeated_opening_verbs(doc: CvDocument, max_count: int = 2) -> list[tuple[str, int]]:
    """Return opening verbs that start more than ``max_count`` visible experience/education bullets."""
    counts: Counter[str] = Counter()
    has_ai: set[str] = set()
    for section in doc.sections:
        if section.kind not in ('experience', 'education'):
            continue
        for entry in section.entries:
            for unit in entry.units:
                if unit.hidden:
                    continue
                match = re.match(r'^[\s"„“\'(\[]*([^\W\d_]+)', unit.text)
                if not match:
                    continue
                opener = match.group(1).casefold()
                if opener in _NON_VERB_OPENERS:
                    continue
                counts[opener] += 1
                if unit.origin == 'ai':
                    has_ai.add(opener)
    return sorted(
        ((verb, count) for verb, count in counts.items() if count > max_count and verb in has_ai),
        key=lambda item: (-item[1], item[0]),
    )


def missing_german_compound_hyphens(text: str) -> list[str]:
    """Detect coordinated German compounds missing the suspension hyphen (e.g. 'Markt und Nutzerbedürfnisse')."""
    hits: list[str] = []
    for match in _DE_COORD_RE.finditer(text):
        first, _conj, second = match.group(1), match.group(2), match.group(3)
        first_cf = first.casefold()
        second_cf = second.casefold()
        before = text[:match.start()]
        prev_tokens = re.findall(r'[^\W\d_]+', before)
        prev_word = prev_tokens[-1].casefold() if prev_tokens else ''

        # Skip if already hyphenated right after first word (regex excludes hyphen, double-check anyway).
        if match.group(0).startswith(first + '-'):
            continue

        # Signal 1: bound Fugen-s / combining prefix that cannot stand alone (unless genitive determiner precedes).
        is_bound = (
            first_cf in _DE_BOUND_EXACT_FIRST
            or (len(first_cf) >= 6 and first_cf.endswith(_DE_BOUND_STEM_PREFIXES))
        )
        if is_bound and prev_word not in _DE_GENITIVE_DETERMINERS:
            hits.append(match.group(0))
            continue

        # Signal 2: bare singular modifier noun + second noun that is a true compound (non-empty prefix + known head).
        if first_cf in _DE_COMPOUND_MODIFIER_FIRST and prev_word not in _DE_ARTICLES_OR_PREPS:
            for head in _DE_COMPOUND_HEAD_SUFFIXES:
                if second_cf.endswith(head) and len(second_cf) >= len(head) + 3:
                    hits.append(match.group(0))
                    break
    return hits


def has_awkward_de_verb_phrase(text: str) -> bool:
    """Detect redundant German verb-noun constructions such as 'Leitete die Koordination'."""
    return bool(_DE_AWKWARD_VERB_RE.search(text))


def added_unsupported_qualifiers(unit: Unit, evidence: dict[str, str], entry_header: str = '') -> set[str]:
    """Return high-signal qualifiers present in ``unit`` but absent from its cited evidence and entry header."""
    if not unit.sources:
        return set()
    cited_parts = [evidence[src] for src in unit.sources if src in evidence]
    if entry_header:
        cited_parts.append(entry_header)
    if not cited_parts:
        return set()
    corpus = '\n'.join(cited_parts)
    text = unit_full_text(unit)
    added: set[str] = set()
    for label, pattern in _QUALIFIER_STEMS:
        if pattern.search(text) and not pattern.search(corpus):
            added.add(label)
    return added


def repeated_paragraph_endings(paragraphs: Iterable[Unit]) -> list[tuple[int, int]]:
    """Return pairs of 1-based body paragraph indices whose final sentences share a closing formula."""
    seen_three: dict[tuple[str, ...], int] = {}
    seen_cliche: dict[tuple[str, ...], int] = {}
    repeats: list[tuple[int, int]] = []
    for idx, unit in enumerate(paragraphs, 1):
        if unit.hidden or unit.role == 'close':
            continue
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', unit.text.strip()) if s.strip()]
        if not sentences:
            continue
        tail_words = words(sentences[-1])
        matched_prev: int | None = None
        if len(tail_words) >= 3:
            prefix3 = tuple(tail_words[:3])
            if prefix3 in seen_three:
                matched_prev = seen_three[prefix3]
            else:
                seen_three[prefix3] = idx
        if len(tail_words) >= 2:
            prefix2 = tuple(tail_words[:2])
            if prefix2 in _CLICHE_ENDING_PREFIXES:
                if prefix2 in seen_cliche and matched_prev is None:
                    matched_prev = seen_cliche[prefix2]
                else:
                    seen_cliche.setdefault(prefix2, idx)
        if matched_prev is not None:
            repeats.append((matched_prev, idx))
    return repeats


def has_de_salutation_case_error(salutation: str, first_paragraph: str) -> bool:
    """Return True when a German letter body opens with a capitalized non-noun after a comma-ended salutation."""
    if not salutation.rstrip().endswith(','):
        return False
    match = re.match(r'^(\s*)([A-ZÄÖÜ][a-zäöüß]+)\b', first_paragraph)
    if not match:
        return False
    word = match.group(2)
    return word.casefold() in _DE_LOWERCASE_OPENERS


def normalize_de_salutation_opening(salutation: str, first_paragraph: str) -> str:
    """Lowercase an opening German non-noun after a comma-ended salutation (preserving nouns and Sie/Ihr*)."""
    if not has_de_salutation_case_error(salutation, first_paragraph):
        return first_paragraph
    return re.sub(
        r'^(\s*)([A-ZÄÖÜ])',
        lambda m: m.group(1) + m.group(2).lower(),
        first_paragraph,
        count=1,
    )


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
    """Promote explicit DOCX degree labels, keep role labels for program entries, and promote project lead-ins."""
    if entry.kind != 'study':
        return
    original_label = entry.label.strip()
    role_hint = original_label if _ROLE_ENTRY_LABEL.fullmatch(original_label) else ''
    if GENERIC_ENTRY_LABEL.fullmatch(original_label):
        entry.label = ''
    locked = entry.locked
    if GENERIC_ENTRY_LABEL.fullmatch(locked.company.strip()):
        if not role_hint and _ROLE_ENTRY_LABEL.fullmatch(locked.company.strip()):
            role_hint = locked.company.strip()
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
    if role_hint and locked.company.strip().casefold() == locked.title.strip().casefold():
        entry.label = 'Praktikum' if role_hint.casefold() == 'praktikum' else 'Internship'
    visible_units = [u for u in entry.units if not u.hidden]
    if (
        len(visible_units) >= 2
        and visible_units[0].origin == 'source'
        and not visible_units[0].lead_in.strip()
        and visible_units[0].text.rstrip().endswith(':')
        and len(visible_units[0].text.strip()) <= 100
    ):
        lead_unit = visible_units[0]
        entry.units = [u for u in entry.units if u.id != lead_unit.id]
        entry.intro.append(lead_unit)
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

