"""Deterministic language and work-authorisation/visa ablation for offline eval.

Removes language and work-authorisation/visa requirement sentences from a job's
``description`` and structured ``raw_metadata`` fields without mutating the input
job dictionary or dropping non-language/non-visa requirement sentences.
"""
from __future__ import annotations

import copy

import re
from typing import Any

# Common DE/EN abbreviations that end with a period and should not split sentences.
_ABBREV_RE = re.compile(
    r"(?:"
    r"\b(?:z\.?\s*B|bzw|ca|Dr|Prof|etc|e\.g|i\.e|incl|inkl|min|mind|max|Nr|St|"
    r"u\.a|d\.h|evtl|ggf|vs|Fig|Inc|Ltd|M\.Sc|B\.Sc|M\.A|B\.A|Ph\.D|Dipl|"
    r"Tel|Str|Abb|vgl|gem|abs|sog|zzgl|inkl|excl|exkl)"
    r"|\b\d+"
    r")\.$",
    re.I,
)

_BULLET_RE = re.compile(r"^(\s*(?:[-*•–—]|\d+[.)])\s+)(.*)$")

# Domain noun phrases where "German"/"English"/"Deutsch" is a domain qualifier,
# NOT a language requirement (e.g. "German labour law", "German market").
_NON_LANGUAGE_DOMAIN_RE = re.compile(
    r"\b(?:"
    r"(?:german|deutsch(?:e[nrms]?)?)\s+"
    r"(?:(?:labour|labor|employment|tax|commercial|corporate|data\s+protection|privacy|contract|company)\s+)?"
    r"(?:law|legislation|gaap|hgb|market|markets|clients?|customers?|regulations?|standards?|"
    r"accounting|payroll|entities|subsidiaries|sites?|plants?|healthcare|public\s+sector|"
    r"arbeitsrecht|recht|steuerrecht|handelsrecht|markt|märkte|märkten|kunden|kundinnen|"
    r"gesellschaften|standorte|standorten|werke|werken|gesundheitswesen)\b"
    r"|(?:english|englisch(?:e[nrms]?)?)\s+(?:law|market|markets|clients?|customers?|entities|subsidiaries)\b"
    r"|(?:programming|scripting|query|markup|modeling|modelling|domain[- ]specific)\s+languages?\b"
    r"|(?:large\s+|natural\s+)?language\s+(?:models?|processing|understanding|generation)\b"
    r"|(?:programmier|skript|abfrage|auszeichnungs|modellierungs)sprache(?:n)?\b"
    r")",
    re.I,
)

# Work-authorisation / visa / sponsorship patterns (DE + EN).
# Carefully excludes software "authorization" (OAuth/API) and payment card "Visa".
_VISA_WORK_AUTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bwork\s*[- ]?(?:permits?|authori[sz]ations?|eligibility|rights?)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:rights?|authori[sz]ed|eligible|legally\s+(?:permitted|allowed|eligible|authori[sz]ed))\s+to\s+work\b",
        re.I,
    ),
    re.compile(
        r"\b(?:arbeitserlaubnis|arbeitsbewilligung|arbeitsberechtigung|"
        r"beschäftigungserlaubnis|aufenthaltstitel|aufenthaltsgenehmigung|"
        r"aufenthaltserlaubnis|niederlassungserlaubnis|"
        r"arbeitsvisum|arbeitsvisa|visa[- ]?sponsoring)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:(?:eu\s+)?blue\s*card|blaue\s+karte(?:\s+eu)?)\b",
        re.I,
    ),
    re.compile(
        r"\bvisa\s*[- ]?(?:sponsorship|sponsor\w*|applications?|support|status|requirements?|regulations?)\b",
        re.I,
    ),
    re.compile(
        r"\bsponsor(?:ing|ship)?\b[^.!?\n]{0,30}\bvisas?\b",
        re.I,
    ),
    re.compile(
        r"\bvisas?\b[^.!?\n]{0,40}\bsponsor\w*\b",
        re.I,
    ),
    re.compile(
        r"\b(?:valid|existing|current|unrestricted|gültig\w*|vorhanden\w*|unbefristet\w*)\s+"
        r"(?:work\s+)?(?:visa|visum|visums)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:beantragung|erteilung|beschaffung|unterstützung)\b[^.!?\n]{0,35}\b(?:visum|visums|visa)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:visum|visums)\b[^.!?\n]{0,45}\b(?:erforderlich|voraussetzung|notwendig|benötigt|"
        r"unterstütz\w*|beantrag\w*|sponsor\w*|nicht\s+möglich|kein\w*)\b",
        re.I,
    ),
    re.compile(
        r"\bvisa\b[^.!?\n]{0,45}\b(?:required|needed|necessary|mandatory|support|relocation|"
        r"non-?eu|immigration|not\s+possible|cannot|can't|unable)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:non-?eu|nicht-?eu|relocation|immigration)\b[^.!?\n]{0,45}\b"
        r"(?:visa|visum|visums|work\s*[- ]?permit|sponsorship)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:requir(?:e|es|ing)|need(?:s|ing)?|offer(?:s|ing)?|provid(?:e|es|ing)|no|without)\s+"
        r"(?:future\s+)?(?:visa\s+|work\s+)?sponsorship\b",
        re.I,
    ),
)

# Natural language names (DE + EN). Keep English "Polish" capitalized to avoid verb "polish".
_LANG_NAME_CORE = (
    r"german|deutsch(?:e[nrms]?)?|"
    r"english|englisch(?:e[nrms]?)?|"
    r"french|französisch(?:e[nrms]?)?|"
    r"spanish|spanisch(?:e[nrms]?)?|"
    r"italian|italienisch(?:e[nrms]?)?|"
    r"dutch|niederländisch(?:e[nrms]?)?|"
    r"portuguese|portugiesisch(?:e[nrms]?)?|"
    r"polnisch(?:e[nrms]?)?|"
    r"russian|russisch(?:e[nrms]?)?|"
    r"turkish|türkisch(?:e[nrms]?)?|"
    r"arabic|arabisch(?:e[nrms]?)?|"
    r"chinese|mandarin|cantonese|chinesisch(?:e[nrms]?)?|"
    r"japanese|japanisch(?:e[nrms]?)?|"
    r"korean|koreanisch(?:e[nrms]?)?|"
    r"ukrainian|ukrainisch(?:e[nrms]?)?|"
    r"greek|griechisch(?:e[nrms]?)?|"
    r"czech|tschechisch(?:e[nrms]?)?|"
    r"swedish|schwedisch(?:e[nrms]?)?|"
    r"danish|dänisch(?:e[nrms]?)?|"
    r"norwegian|norwegisch(?:e[nrms]?)?|"
    r"finnish|finnisch(?:e[nrms]?)?|"
    r"romanian|rumänisch(?:e[nrms]?)?|"
    r"hungarian|ungarisch(?:e[nrms]?)?|"
    r"hebrew|hebräisch(?:e[nrms]?)?|"
    r"hindi|hindisch(?:e[nrms]?)?"
)
_LANG_NAME_RE = re.compile(rf"\b(?:{_LANG_NAME_CORE})\b", re.I)
_POLISH_RE = re.compile(r"\bPolish\b")

# Explicit language-skill compound nouns and working-language statements (always language).
_EXPLICIT_LANG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:deutsch|englisch|französisch|spanisch|italienisch|niederländisch|"
        r"portugiesisch|polnisch|russisch|türkisch|arabisch|chinesisch|japanisch|"
        r"koreanisch|ukrainisch|fremd)?sprach(?:en)?kenntnis(?:se|sen)?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:deutsch|englisch|französisch|spanisch|italienisch|niederländisch|"
        r"portugiesisch|polnisch|russisch|türkisch|arabisch|chinesisch|japanisch|"
        r"koreanisch|ukrainisch)kenntnis(?:se|sen)?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:deutsch|englisch|französisch|spanisch|italienisch|niederländisch|portugiesisch)"
        r"-\s*(?:und|oder)\s+"
        r"(?:deutsch|englisch|französisch|spanisch|italienisch|niederländisch|portugiesisch)kenntnis(?:se|sen)?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:sprachniveau|muttersprach(?:e|lich\w*|niveau|ler\w*)|"
        r"geschäftsenglisch|wirtschaftsenglisch|fachenglisch)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:team|arbeits|unternehmens|projekt|konzern)sprache\b",
        re.I,
    ),
    re.compile(
        r"\b(?:working|team|company|office|corporate|business|reporting)\s+language\b",
        re.I,
    ),
    re.compile(r"\bbusiness\s+english\b", re.I),
    re.compile(
        r"\b(?:language|foreign\s+language)\s+(?:skills?|proficiency|fluency|requirements?)\b",
        re.I,
    ),
)

# CEFR levels (guarded against driving license classes like "Führerschein Klasse C1").
_CEFR_LEVEL_RE = re.compile(
    r"(?<!Klasse\s)(?<!Autobahn\s)(?<!Bundesstraße\s)\b(?:A1|A2|B1|B2|C1|C2)\b"
)
_CEFR_EXPLICIT_RE = re.compile(r"\b(?:CEFR|GER)\b")
_DRIVING_LICENSE_RE = re.compile(
    r"\b(?:führerschein|fahrerlaubnis|driving\s+licen[cs]e|driver'?s\s+licen[cs]e)\b",
    re.I,
)

# Unambiguous fluency / proficiency words paired with a language name.
_STRONG_FLUENCY_RE = re.compile(
    r"\b(?:"
    r"fluent|fluently|fluency|fließend\w*|fliessend\w*|flüssig\w*|fluessig\w*|"
    r"verhandlungssicher\w*|kommunikationssicher\w*|sprachgewandt\w*|stilsicher\w*|"
    r"sprachlich\s+versiert|bilingual|zweisprachig\w*|"
    r"near[- ]native|native[- ]like|native(?:\s+speaker|\s+level)?|mother\s+tongue|first\s+language|"
    r"proficient|proficiency|conversational|konversationssicher\w*|"
    r"working\s+(?:knowledge|proficiency)|command\s+of|"
    r"in\s+wort\s+und\s+schrift|"
    r"(?:written|spoken|oral|verbal)\s+(?:and|&|/)\s+(?:written|spoken|oral|verbal)|"
    r"(?:mündlich\w*|schriftlich\w*)\s+(?:und|&|/)\s+(?:mündlich\w*|schriftlich\w*)|"
    r"grundkenntnis(?:se|sen)?|basiskenntnis(?:se|sen)?|anfängerkenntnis(?:se|sen)?|"
    r"mittelstufe|auf\s+(?:\w+\s+)?\w*niveau|at\s+(?:a\s+)?(?:\w+\s+)?level|"
    r"from\s+(?:A1|A2|B1|B2|C1|C2)|ab\s+(?:A1|A2|B1|B2|C1|C2)"
    r")\b",
    re.I,
)

# Quality/level adjectives or modality words immediately next to a language name.
_QUALITY_ADJ = (
    r"(?:very\s+)?good|strong|excellent|outstanding|exceptional|solid|confident|"
    r"advanced|intermediate|basic|elementary|beginner|professional|business|"
    r"written|spoken|oral|verbal|"
    r"(?:sehr\s+)?gut(?:e[nrms]?)?|sicher(?:e[nrms]?)?|solide[nrms]?|"
    r"ausgezeichnet(?:e[nrms]?)?|exzellent(?:e[nrms]?)?|hervorragend(?:e[nrms]?)?|"
    r"fortgeschritten(?:e[nrms]?)?|souverän(?:e[nrms]?)?|"
    r"schriftlich(?:e[nrms]?)?|mündlich(?:e[nrms]?)?"
)
_ADJ_BEFORE_LANG_RE = re.compile(
    rf"\b(?:{_QUALITY_ADJ})\s+"
    rf"(?:(?:written|spoken|oral|verbal|business|schriftlich\w*|mündlich\w*|"
    rf"communication\s+(?:skills?\s+)?in|command\s+of|knowledge\s+of|skills?\s+in)\s+)?"
    rf"(?:{_LANG_NAME_CORE}|Polish)\b",
    re.I,
)
_LANG_FOLLOWED_BY_QUAL_RE = re.compile(
    rf"\b(?:{_LANG_NAME_CORE}|Polish)\s+"
    rf"(?:(?:language|communication)\s+)?"
    rf"(?:skills?|knowledge|proficiency|fluency|command|level|\w*niveau|"
    r"written|spoken|required|mandatory|preferred|helpful|essential|"
    r"erforderlich|notwendig|voraussetzung|wünschenswert|hilfreich|"
    r"mindestens|minimum|at\s+least|ab\s+[ABC][12]|auf\s+(?:\w+\s+)?\w*niveau|"
    r"is\s+(?:required|mandatory|preferred|helpful|a\s+plus|sufficient|enough)|"
    r"ist\s+(?:erforderlich|voraussetzung|wünschenswert|hilfreich|von\s+vorteil)|"
    r"für\s+die\s+kommunikation|for\s+(?:daily\s+)?communication)",
    re.I,
)

# Short bare-language bullets like "- English", "- Deutsch und Englisch", "- English; basic German is helpful".
_PURE_LANG_FILLER_RE = re.compile(
    rf"\b(?:{_LANG_NAME_CORE}|Polish|"
    r"and|or|und|oder|sowie|as\s+well\s+as|"
    r"language|skills?|knowledge|proficiency|fluency|command|communication|"
    r"required|mandatory|preferred|helpful|essential|beneficial|advantage|plus|"
    r"erforderlich|notwendig|voraussetzung|wünschenswert|hilfreich|vorteil|von|"
    r"is|are|ist|sind|a|an|the|in|for|with|at|to|of|für|mit|zu|auf|die|der|das|den|dem|"
    r"basic|good|very|strong|excellent|sehr|gut\w*|solid\w*|sicher\w*|"
    r"written|spoken|oral|verbal|schriftlich\w*|mündlich\w*|"
    r"level|\w*niveau|upwards|higher|höher|mindestens|minimum|at\s+least|ab|genügt|genügen|reicht|aus|enough|sufficient|"
    r"dealing|working|international|colleagues|suppliers|zulieferern|clients|customers|kunden|team|"
    r"A1|A2|B1|B2|C1|C2|GER|CEFR"
    r")\b",
    re.I,
)

# Structured raw_metadata keys dedicated to language or visa/work-authorisation.
_META_DROP_KEY_RE = re.compile(
    r"^(?:languages?|language_requirements?|sprachkenntnisse|"
    r"visa|visa_sponsorship|sponsorship|work_permit|work_authorization|"
    r"work_authorisation|arbeitserlaubnis|arbeitsbewilligung|aufenthaltstitel)$",
    re.I,
)

# Structured raw_metadata requirement fields that may contain text/lists of requirements.
_META_REQUIREMENT_FIELDS: tuple[str, ...] = (
    "skills",
    "qualifications",
    "experienceRequirements",
    "educationRequirements",
    "responsibilities",
    "duties",
    "requirements",
)


def _has_language_name(text: str) -> bool:
    return bool(_LANG_NAME_RE.search(text) or _POLISH_RE.search(text))


def _is_visa_or_work_auth(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _VISA_WORK_AUTH_PATTERNS)


def _is_language_requirement(sentence: str) -> bool:
    cleaned = _NON_LANGUAGE_DOMAIN_RE.sub(" ", sentence)

    if any(pattern.search(cleaned) for pattern in _EXPLICIT_LANG_PATTERNS):
        return True

    has_lang = _has_language_name(cleaned)

    # CEFR level (unless driving license sentence without a language name)
    if _CEFR_LEVEL_RE.search(cleaned):
        if has_lang or not _DRIVING_LICENSE_RE.search(cleaned):
            return True
    if _CEFR_EXPLICIT_RE.search(cleaned) and (has_lang or re.search(r"\b(?:niveau|level|sprach|language)\b", cleaned, re.I)):
        return True

    if not has_lang:
        return False

    if _STRONG_FLUENCY_RE.search(cleaned):
        return True

    if _ADJ_BEFORE_LANG_RE.search(cleaned) or _LANG_FOLLOWED_BY_QUAL_RE.search(cleaned):
        return True

    # Short bare language-list bullet (e.g. "English", "Deutsch und Englisch")
    residual = _PURE_LANG_FILLER_RE.sub(" ", cleaned)
    residual_words = re.findall(r"[A-Za-zÄÖÜäöüß0-9]+", residual)
    if not residual_words:
        return True

    return False


def is_lang_visa_sentence(sentence: str) -> bool:
    """Return True if ``sentence`` is a language or work-authorisation/visa requirement."""
    stripped = sentence.strip()
    if not stripped:
        return False
    return _is_visa_or_work_auth(stripped) or _is_language_requirement(stripped)


def _split_sentences(text: str) -> list[str]:
    """Split a single line of text into sentences without breaking common abbreviations."""
    stripped = text.strip()
    if not stripped:
        return []
    parts: list[str] = []
    start = 0
    for match in re.finditer(r"([.!?]+)(\s+)", stripped):
        end_punct = match.end(1)
        candidate = stripped[start:end_punct]
        # Avoid splitting on abbreviations like "z.B.", "Dr.", "e.g.", or ordinal numbers "1."
        if match.group(1) == "." and _ABBREV_RE.search(candidate):
            continue
        # Only split when the next token begins a new sentence (capital letter, digit, paren, quote)
        next_pos = match.end()
        if next_pos < len(stripped) and not re.match(r"[\"'(\[A-ZÄÖÜ0-9]", stripped[next_pos]):
            continue
        parts.append(candidate.strip())
        start = next_pos
    tail = stripped[start:].strip()
    if tail:
        parts.append(tail)
    return parts


# This evaluator uses its own recognizers and removes whole conjuncts. The
# authored pairs separately test cases where heuristic editing is ambiguous.
_COORDINATION = re.compile(
    r"\([^()]*\)|,\s*(?:and\s+|und\s+)?|"
    r"\s+(?:as well as|in addition to|sowie|and|und|plus)\s+", re.I,
)
_SHARED_PREFIX = re.compile(
    r"^(?:(?:you|applicants?)\s+(?:must|need to)|(?:du|sie)\s+(?:musst|müssen|bringst|bringen))\s+", re.I,
)
_SHARED_SUFFIX = re.compile(
    r"\s+(?:(?:is|are|ist|sind)\s+)?(?:required|mandatory|erforderlich|verpflichtend|voraussetzung|mit)$", re.I,
)


def _filter_sentence(sentence: str) -> tuple[str | None, list[str]]:
    """Remove independently detected language/authorization text for metric ablation."""
    if ";" in sentence:
        results = [_filter_sentence(part.strip()) for part in sentence.split(";") if part.strip()]
        removed = [quote for _, quotes in results for quote in quotes]
        return (("; ".join(text for text, _ in results if text) or None), removed) if removed else (sentence, [])

    def remove_aside(match: re.Match) -> str:
        return "" if is_lang_visa_sentence(match[0][1:-1]) else match[0]

    without_asides = re.sub(r"\([^()]*\)", remove_aside, sentence)
    text = re.sub(r"\s+([.!?])$", r"\1", " ".join(without_asides.split()))
    if not is_lang_visa_sentence(text):
        return (text or None, [sentence]) if without_asides != sentence else (sentence, [])

    punct = text[-1] if text.endswith((".", "!", "?")) else ""
    body = text[:-1] if punct else text
    prefix = _SHARED_PREFIX.match(body)
    if prefix:
        body = body[prefix.end():]
    suffix = _SHARED_SUFFIX.search(body)
    if suffix:
        body = body[:suffix.start()]

    parts, separators = [], []
    start = 0
    for boundary in _COORDINATION.finditer(body):
        if boundary[0].startswith("("):
            continue
        parts.append(body[start:boundary.start()].strip())
        separators.append(boundary[0])
        start = boundary.end()
    parts.append(body[start:].strip())
    keep = [
        index for index, part in enumerate(parts)
        if part and not is_lang_visa_sentence(part) and not _PURE_LANG_FILLER_RE.fullmatch(part)
    ]
    if not keep or len(keep) == len(parts):
        return None, [sentence]

    cleaned = parts[keep[0]]
    for left, right in zip(keep, keep[1:]):
        cleaned += separators[left] + parts[right]
    return (
        ((prefix[0] if prefix else "") + cleaned + (suffix[0] if suffix else "") + punct),
        [sentence],
    )


def strip_lang_visa_text(text: str) -> tuple[str, list[str]]:
    """Remove language and work-authorisation/visa sentences from ``text``.

    Returns ``(cleaned_text, removed_sentences)``.
    """
    if not text:
        return text, []

    kept_lines: list[str] = []
    removed: list[str] = []

    for line in text.splitlines():
        if not line.strip():
            kept_lines.append("")
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            prefix, body = bullet_match.group(1), bullet_match.group(2)
        else:
            prefix, body = "", line.strip()

        sentences = _split_sentences(body)
        if not sentences:
            continue

        kept_sentences: list[str] = []
        for sent in sentences:
            kept_sent, sent_removed = _filter_sentence(sent)
            removed.extend(sent_removed)
            if kept_sent:
                kept_sentences.append(kept_sent)

        if kept_sentences:
            kept_lines.append(f"{prefix}{' '.join(kept_sentences)}")

    # Collapse >2 consecutive blank lines and trim outer whitespace.
    joined = "\n".join(kept_lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined).strip()
    return joined, removed


def _ablate_structured_value(value: Any) -> tuple[Any, bool]:
    """Recursively clean language/visa items from structured metadata values."""
    if isinstance(value, str):
        cleaned, removed = strip_lang_visa_text(value)
        return cleaned, bool(removed)
    if isinstance(value, list):
        new_items: list[Any] = []
        changed = False
        for item in value:
            if isinstance(item, str):
                cleaned, removed = strip_lang_visa_text(item)
                if removed:
                    changed = True
                    if cleaned:
                        new_items.append(cleaned)
                else:
                    new_items.append(item)
            elif isinstance(item, dict):
                cleaned_item, item_changed = _ablate_item_dict(item)
                if item_changed:
                    changed = True
                # Drop dict entries whose sole content was removed or language/visa
                if cleaned_item:
                    new_items.append(cleaned_item)
                elif item:
                    changed = True
            else:
                new_items.append(item)
        return new_items, changed
    if isinstance(value, dict):
        return _ablate_metadata_dict(value)
    return value, False


def _ablate_item_dict(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Clean a dict element inside a structured requirement list."""
    # If the item has a text/name/value/description field that is itself a language/visa item, drop it.
    text_fields = ("text", "description", "value", "name", "label")
    cleaned: dict[str, Any] = {}
    changed = False
    had_text_field = False
    kept_text_field = False
    for key, val in item.items():
        if _META_DROP_KEY_RE.match(str(key)):
            if val not in (None, "", [], {}):
                changed = True
            continue
        if key in text_fields and isinstance(val, str):
            had_text_field = True
            c_val, removed = strip_lang_visa_text(val)
            if removed:
                changed = True
                if c_val:
                    cleaned[key] = c_val
                    kept_text_field = True
            else:
                cleaned[key] = val
                kept_text_field = True
        else:
            cleaned[key] = copy.deepcopy(val)
    if had_text_field and not kept_text_field:
        return {}, True
    return cleaned, changed


def _ablate_metadata_dict(meta: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Clean language/visa keys and requirement text fields inside a metadata dict."""
    result: dict[str, Any] = {}
    changed = False
    for key, val in meta.items():
        if _META_DROP_KEY_RE.match(str(key)):
            if val not in (None, "", [], {}):
                changed = True
            continue
        if key == "detail_requirements" and isinstance(val, dict):
            cleaned_detail, detail_changed = _ablate_metadata_dict(val)
            result[key] = cleaned_detail
            changed = changed or detail_changed
        elif key == "detail_listing_description" and isinstance(val, str):
            cleaned_desc, removed = strip_lang_visa_text(val)
            result[key] = cleaned_desc
            changed = changed or bool(removed)
        elif key in _META_REQUIREMENT_FIELDS:
            cleaned_val, val_changed = _ablate_structured_value(val)
            result[key] = cleaned_val
            changed = changed or val_changed
        else:
            result[key] = copy.deepcopy(val)
    return result, changed


def ablate_lang_visa(job: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return ``(ablated_job, changed)`` with language/visa requirements removed.

    Does not mutate ``job``. ``changed`` is True iff at least one language or
    work-authorisation/visa requirement was removed from ``description`` or
    ``raw_metadata``.
    """
    ablated = copy.deepcopy(job)
    changed = False

    desc = ablated.get("description")
    if isinstance(desc, str):
        cleaned_desc, removed = strip_lang_visa_text(desc)
        if removed:
            ablated["description"] = cleaned_desc
            changed = True

    raw_meta = ablated.get("raw_metadata")
    if isinstance(raw_meta, dict):
        cleaned_meta, meta_changed = _ablate_metadata_dict(raw_meta)
        if meta_changed:
            ablated["raw_metadata"] = cleaned_meta
            changed = True

    return ablated, changed
