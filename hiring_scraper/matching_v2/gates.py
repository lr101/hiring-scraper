"""Deterministic gate evaluator over full posting context and saved profile facts.

Hard fails (eligible=False) are emitted ONLY for explicit, cited incompatibilities:
  (a) Stated CEFR minimum or unambiguous mandatory native / native-level /
      near-native / Muttersprache above the profile's saved level for that language.
  (b) Excluded title term matching the job title.
  (c) Explicit mandatory on-site attendance text vs willing_to_work_onsite=False.
  (d) Explicitly denied work authorisation for the jurisdiction bound to the
      permit phrase (with no sponsorship alternative).

Every hard fail carries ``job_quote``, ``profile_source``, and ``profile_quote``.
Qualitative language wording, metadata-only onsite, sponsorship alternatives,
missing profile fields, and unverified formal prerequisites produce structured
verify flags only — never hard fails.
"""
from __future__ import annotations

from collections.abc import Sequence
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
    _LICENSE_CONDITION,
    _credential_evidence,
    _pattern,
    plain_text,
)
from hiring_scraper.requirements import (
    is_source_instruction,
    requirement_polarity,
    scoped_sentences,
    structured_requirement_texts,
    structured_source_scope,
    structured_text,
)

_PAREN_RE = re.compile(r'\([^()]*\)')


def mask_parens(text: str) -> tuple[str, dict[str, str]]:
    """Replace flat parenthetical spans with private-use placeholders."""
    protected: dict[str, str] = {}
    working = text
    for idx, m in reversed(list(enumerate(_PAREN_RE.finditer(working)))):
        marker = f'\ue000p{idx}\ue001'
        protected[marker] = m.group()
        working = working[:m.start()] + marker + working[m.end():]
    return working, protected


def unmask_parens(text: str, protected: dict[str, str]) -> str:
    """Restore placeholders produced by ``mask_parens``."""
    for marker, orig in protected.items():
        text = text.replace(marker, orig)
    return text

# Expanded non-requirement detector (company boasts, employer offers, client language).
NON_REQUIREMENT_V2 = re.compile(
    r'(?:^|[.;:!?\n]\s*)(?:(?:about|über)\s+)?'
    r'(?:our\s+(?:company|team|teams|firm|organi[sz]ation)\s+'
    r'(?:(?:has|have|was|were|speaks?|uses?|runs?|operates?|builds?|develops?|provides?|offers?|delivers?|serves?|employs?|consists?|boasts?|brings?)\b|'
    r'(?:is|are)\s+(?!(?:looking\s+for|seeking|hiring|recruiting|requiring)\b))|'
    r'(?:unser\s+unternehmen|unsere\s+firma|unser\s+team)\s+'
    r'(?:(?:hat|haben|war|waren|bietet|entwickelt|nutzt|arbeitet|spricht|beschäftigt|verfügt|blickt|besteht)\b|'
    r'(?:ist|sind)\s+(?!(?:auf\s+der\s+suche|suchend)\b)))|'
    r'\b(?:we have|the company has|'
    r'we offer|benefits|perks|you receive|you get|you can take|we provide you with|'
    r'du erhältst|sie erhalten|wir bieten|'
    r'wir haben|wir verfügen über|wir blicken|blicken wir|das unternehmen hat|das unternehmen verfügt|'
    r'seit \d{4}|since \d{4}|founded in \d{4}|gegründet|'
    r'in business|am markt|auf dem markt|'
    r'common among our clients|spoken by our clients|our clients speak|our customers speak|our team speaks)\b',
    re.I,
)

# ---------------------------------------------------------------------------
# Jurisdiction normalisation and permit-phrase binding
# ---------------------------------------------------------------------------

AUTHORIZATION_CONDITION = re.compile(
    r'\b(?:work\s+(?:authorization|authorisation|permits?|rights?|eligibility)|'
    r'(?:(?:valid|legal|existing|current)\s+)?rights?\s+to\s+work|'
    r'(?:eligible|authorized|authorised|legally\s+(?:permitted|allowed|eligible|authorized|authorised))\s+to\s+work|'
    r'arbeitserlaubnis|arbeitsberechtigung|arbeitsbewilligung|beschäftigungserlaubnis|'
    r'aufenthalts.?\s*und\s+arbeitserlaubnis|'
    r'aufenthalts(?:genehmigung|bewilligung|titel|erlaubnis)|'
    r'niederlassungs(?:bewilligung|erlaubnis))\b',
    re.I,
)

_JURISDICTION_NAMES = {
    'DE': r'germany|deutschland|german|deutsch(?:e[rnms]?)?',
    'CH': r'switzerland|schweiz(?:er(?:isch(?:e[rnms]?)?)?)?|swiss',
    'GB': r'united\s+kingdom|great\s+britain|großbritannien|grossbritannien|british|britisch(?:e[rnms]?)?',
    'AT': r'austria|österreich|oesterreich|austrian|österreichisch(?:e[rnms]?)?',
    'EU': r'european\s+union|europäische[rnms]?\s+union',
    'EEA': r'european\s+economic\s+area|europäische[rnms]?\s+wirtschaftsraum',
    'US': r'united\s+states|american|amerikanisch(?:e[rnms]?)?',
    'FR': r'france|frankreich|french|französisch(?:e[rnms]?)?',
    'NL': r'netherlands|niederlande|dutch|niederländisch(?:e[rnms]?)?',
    'ES': r'spain|spanien|spanish|spanisch(?:e[rnms]?)?',
    'IT': r'italy|italien|italian|italienisch(?:e[rnms]?)?',
    'PL': r'poland|polen|polish|polnisch(?:e[rnms]?)?',
    'NZ': r'new\s+zealand|neuseeland',
    'AU': r'australia|australien|australian|australisch(?:e[rnms]?)?',
    'BE': r'belgium|belgien|belgian|belgisch(?:e[rnms]?)?',
    'SE': r'sweden|schweden|swedish|schwedisch(?:e[rnms]?)?',
    'DK': r'denmark|dänemark|danish|dänisch(?:e[rnms]?)?',
    'NO': r'norway|norwegen|norwegian|norwegisch(?:e[rnms]?)?',
    'CA': r'canada|kanada|canadian|kanadisch(?:e[rnms]?)?',
    'IE': r'ireland|irland|irish|irisch(?:e[rnms]?)?',
}
_JURISDICTION_ACRONYMS_CI = {
    'GB': r'UK|U\.K\.',
    'EU': r'EU',
    'EEA': r'EEA|EWR',
    'US': r'USA|U\.S\.A\.|U\.S\.',
}
_JURISDICTION_CODES_CS = {
    'DE': r'DE|DEU',
    'CH': r'CH|CHE',
    'GB': r'GB|GBR',
    'AT': r'AT|AUT',
    'US': r'US',
    'FR': r'FR|FRA',
    'NL': r'NL|NLD',
    'ES': r'ES|ESP',
    'IT': r'IT|ITA',
    'PL': r'PL|POL',
    'NZ': r'NZ|NZL',
    'AU': r'AU|AUS',
    'BE': r'BE|BEL',
    'SE': r'SE|SWE',
    'DK': r'DK|DNK',
    'NO': r'NO|NOR',
    'CA': r'CA|CAN',
    'IE': r'IE|IRL',
}

_JURISDICTION_TEXT_PATTERNS = {
    code: re.compile(
        r'(?<!\w)(?:'
        + r'(?i:' + _JURISDICTION_NAMES[code]
        + ('|' + _JURISDICTION_ACRONYMS_CI[code] if code in _JURISDICTION_ACRONYMS_CI else '')
        + r')'
        + (r'|(?-i:' + _JURISDICTION_CODES_CS[code] + r')' if code in _JURISDICTION_CODES_CS else '')
        + r')(?!\w)'
    )
    for code in _JURISDICTION_NAMES
}
_JURISDICTION_FIELD_PATTERNS = {
    code: re.compile(
        r'(?<!\w)(?:'
        + '|'.join(
            part
            for part in (
                _JURISDICTION_NAMES[code],
                _JURISDICTION_ACRONYMS_CI.get(code),
                _JURISDICTION_CODES_CS.get(code),
            )
            if part
        )
        + r')(?!\w)',
        re.I,
    )
    for code in _JURISDICTION_NAMES
}

_JURISDICTION_TOKEN_TEXT = (
    r'(?<!\w)(?:(?i:'
    + '|'.join(list(_JURISDICTION_NAMES.values()) + list(_JURISDICTION_ACRONYMS_CI.values()))
    + r')|(?-i:'
    + '|'.join(_JURISDICTION_CODES_CS.values())
    + r'))(?!\w)'
)
_JURISDICTION_CHAIN_TEXT = (
    rf'{_JURISDICTION_TOKEN_TEXT}'
    rf'(?:\s*(?:/|,|(?i:\bor\b|\boder\b|\band\b|\bund\b))\s*'
    rf'(?:(?i:the|der|die|den|dem|das)\s+)?{_JURISDICTION_TOKEN_TEXT}){{0,3}}'
)
_PERMIT_ADJECTIVES = (
    r'(?i:valid|existing|current|full|permanent|unrestricted|legal|active|'
    r'gültig\w*|bestehend\w*|vorhanden\w*|dauerhaft\w*|unbefristet\w*|uneingeschränkt\w*|'
    r'erforderlich\w*|notwendig\w*)'
)
_PRE_PERMIT_JURISDICTION = re.compile(
    rf'(?P<chain>{_JURISDICTION_CHAIN_TEXT})(?:\s+{_PERMIT_ADJECTIVES}){{0,3}}\s*$'
)
_POST_PERMIT_PAREN = re.compile(rf'^\s*\((?P<chain>{_JURISDICTION_CHAIN_TEXT})\)')
_POST_PERMIT_PREP = re.compile(
    r'^\s*(?i:(?:(?:is|are|ist|sind)\s+)?'
    r'(?:(?:strictly|already|legally|zwingend)\s+)?'
    r'(?:(?:required|needed|mandatory|essential|erforderlich|notwendig|vorausgesetzt)\s+)?'
    r'(?:to\s+work\s+)?(?:in|for|für)\s+(?:(?:the|der|die|den|dem|das)\s+)?)'
    rf'(?P<chain>{_JURISDICTION_CHAIN_TEXT})'
    r'(?![-\w]*based\b|\s+(?i:clients?|customers?|markets?|kunden|teams?|roles?|positions?)\b)'
)


def normalize_jurisdictions(value: str, *, strict_codes: bool = False) -> frozenset[str]:
    """Normalize country/region names and ISO codes to canonical jurisdiction codes."""
    if not value or not isinstance(value, str):
        return frozenset()
    patterns = _JURISDICTION_TEXT_PATTERNS if strict_codes else _JURISDICTION_FIELD_PATTERNS
    return frozenset(code for code, pattern in patterns.items() if pattern.search(value))


def authorization_jurisdictions(text: str, hit: re.Match) -> frozenset[str]:
    """Extract jurisdictions grammatically bound to a single work-authorization phrase."""
    before = text[max(0, hit.start() - 90):hit.start()]
    after = text[hit.end():min(len(text), hit.end() + 90)]
    found: set[str] = set()
    if (pre := _PRE_PERMIT_JURISDICTION.search(before)):
        found.update(normalize_jurisdictions(pre.group('chain'), strict_codes=True))
    if (paren := _POST_PERMIT_PAREN.match(after)):
        found.update(normalize_jurisdictions(paren.group('chain'), strict_codes=True))
    elif (post := _POST_PERMIT_PREP.match(after)):
        found.update(normalize_jurisdictions(post.group('chain'), strict_codes=True))
    return frozenset(found)


_EU_MEMBER_CODES: frozenset[str] = frozenset({
    'DE', 'AT', 'FR', 'NL', 'ES', 'IT', 'PL', 'BE', 'SE', 'DK', 'IE',
})

_SPONSORSHIP_OFFER = re.compile(
    r'\b(?:'
    r'(?:we|company|employer|wir)\s+(?:(?:can|will|may|could|are\s+(?:happy|glad|able|willing)\s+to|gerne)\s+)?'
    r'(?:sponsor|offer|provide|support|assist|help|guide|arrange|facilitate)\s+'
    r'(?:(?:you|candidates?|employees?|applicants?|new\s+hires?|staff|talent)\s+)?'
    r'(?:(?:to|with|in)\s+)?'
    r'(?:(?:obtain(?:ing)?|get(?:ting)?|secur(?:e|ing)|acquir(?:e|ing)|apply(?:ing)?\s+for)\s+)?'
    r'(?:(?:a|an|the|your|their)\s+)?'
    r'(?:(?:relocation\s+and\s+)?(?:(?:german|deutsch\w*|eu|swiss|uk|british|us|canadian|austrian|dutch|french)\s+)?'
    r'(?:work\s+)?(?:visas?(?:\s+applications?)?|work\s+permits?|permits?|work\s+authori[sz]ation|sponsorship|arbeitserlaubnis|arbeitsbewilligung|visum|visa))|'
    r'(?:(?:visa|work\s+permit)\s+)?(?:sponsorship|visa\s+(?:support|assistance))\s+'
    r'(?:is\s+)?(?:available|offered|provided|possible|supported|included)|'
    r'visum.?sponsoring\s+(?:ist\s+)?(?:möglich|verfügbar|wird\s+angeboten|übernommen)|'
    r'(?:wir\s+)?(?:unterstützen|helfen)\s+(?:(?:gerne|dich|sie|mitarbeit\w*|bewerber\w*|kandidat\w*)\s+){0,3}'
    r'(?:bei\s+(?:der\s+|dem\s+)?|beim\s+|dabei,?\s+)?'
    r'(?:(?:beantragung|beschaffung|antrag|erhalt\w*)\s+(?:eines?\s+|einer\s+)?)?'
    r'(?:(?:deutsch\w*|schweizer\w*|österreich\w*|eu[- ]?)\s+)?(?:visums?|visa|arbeitserlaubnis|arbeitsbewilligung)|'
    r'(?:bei\s+(?:der\s+|dem\s+)?|beim\s+)(?:(?:beantragung|beschaffung|antrag)\s+(?:eines?\s+|einer\s+)?)?'
    r'(?:(?:deutsch\w*|schweizer\w*|österreich\w*|eu[- ]?)\s+)?(?:visums?|visa|arbeitserlaubnis|arbeitsbewilligung)\s+(?:unterstützen|helfen)\s+wir|'
    r'unterstützung\s+(?:bei\s+(?:der\s+beantragung\s+)?)?(?:eines?\s+|beim\s+)?'
    r'(?:(?:deutsch\w*|schweizer\w*|österreich\w*|eu[- ]?)\s+)?(?:visums?|visa|arbeitserlaubnis|arbeitsbewilligung)|'
    r'(?:or|oder)\s+(?:we\s+(?:can\s+|will\s+)?)?sponsor(?:ing)?\s+(?:(?:a|an)\s+)?(?:work\s+)?(?:visas?|work\s+permits?)'
    r')\b',
    re.I,
)
_SPONSORSHIP_NEGATION = re.compile(
    r'\b(?:no|not|never|unable\s+to|cannot|can[\'’]t|do\s+not|don[\'’]t|does\s+not|doesn[\'’]t|'
    r'without|kein\w*|nicht|ohne)\b',
    re.I,
)
_EXPLICIT_NO_SPONSORSHIP = re.compile(
    r'\b(?:'
    r'(?:no|without)\s+(?:visa|work\s+permit)?\s*sponsorship|'
    r'(?:cannot|can[\'’]t|unable\s+to|do\s+not|don[\'’]t|does\s+not|doesn[\'’]t|will\s+not|won[\'’]t)\s+'
    r'(?:offer|provide|sponsor)\s+(?:(?:a|an)\s+)?(?:work\s+)?(?:visa|sponsorship|work\s+permit)|'
    r'(?:visa|work\s+permit)\s+sponsorship\s+(?:is\s+)?(?:not|no\s+longer)\s+(?:available|offered|provided|possible)|'
    r'kein\w*\s+visum.?sponsoring|'
    r'visum.?sponsoring\s+(?:ist\s+)?nicht\s+(?:möglich|verfügbar)'
    r')\b',
    re.I,
)
_SPONSORSHIP_OTHER_ROLE_SCOPE = re.compile(
    r'\b(?:'
    r'(?:only|exclusively|limited|restricted)\s+(?:for|to)\s+(?!(?:this|the\s+current|diese)\s+(?:role|position|job|stelle)\b)'
    r'[^.;:!?\n]{0,50}\b(?:roles?|positions?|jobs?|offices?|locations?|sites?|teams?|hires?|stellen|standorte?)\b|'
    r'(?:for|in)\s+(?:our\s+)?(?:other|select|certain|specific)\s+(?:roles?|positions?|offices?|locations?)\b|'
    r'nur\s+für\s+(?!diese\s+stelle\b)[^.;:!?\n]{0,50}\b(?:stellen|positionen|standorte?)\b'
    r')',
    re.I,
)
_EMPLOYER_PERMIT_ASSISTANCE = re.compile(
    r'\b(?:'
    r'(?:we|company|employer|our\s+hr(?:\s+team)?|wir)\s+'
    r'(?:(?:can|will|may|could|are\s+(?:happy|glad|able|willing)\s+to|gerne)\s+)?'
    r'(?:help|assist|support|guide|sponsor|arrange|facilitate|handle|process|unterstützen|helfen|begleiten)\b'
    r')',
    re.I,
)
_OTHER_ACTOR_SUBJECT = re.compile(
    r'^(?:(?:our|the|a|another|other)\s+)?(?:finance|people|hr|recruiting|legal|design|innovation)\s+(?:team|department)\b|'
    r'^(?:another|other|andere\w*)\s+(?:[\w-]+\s+){0,2}(?:teams?|departments?|abteilung\w*)\b|'
    r'^(?:our|their|the|my|another|other|unsere?\w*|deren|die|das)\s+(?:[\w-]+\s+){0,4}'
    r'(?:staff|employees?|workers?|department|partners?|contractors?|colleague\w*|kolleg\w*|abteilung|'
    r'clients?|customers?|kunden|kundinnen|'
    r'[\w-]*mitarbeit\w*|[\w-]*kräfte\w*|[\w-]*kraefte\w*|[\w-]*personal)\b|'
    r'^(?:(?:(?:a|the|our|ein\w*|der|die)\s+)?senior\s+(?:colleague\w*|manager\w*|kolleg\w*)|'
    r'the\s+director|die\s+personalabteilung|unsere\s+personalabteilung)\b',
    re.I,
)
_HIRING_TEAM_LEAD = re.compile(
    r'^(?:our|the|unser\w*)\s+(?:[\w-]+\s+){0,2}(?:team|department|abteilung)\s+'
    r'(?:(?:is|are)\s+(?:looking\s+for|seeking|hiring|recruiting|requiring)|'
    r'sucht|ist\s+auf\s+der\s+suche|benötigt|erfordert)\b',
    re.I,
)


def _is_other_actor_subject(text: str) -> bool:
    """Return True when ``text`` starts with a third-party/other-staff subject rather than the vacancy/applicant."""
    stripped = (text or '').strip()
    if not stripped or _HIRING_TEAM_LEAD.search(stripped):
        return False
    return bool(_OTHER_ACTOR_SUBJECT.search(stripped))
_EXTERNAL_LANGUAGE_USE = re.compile(
    r'\b(?:is|are|wird|werden)\s+(?:used|spoken|required|verwendet|gesprochen|benötigt)\s+'
    r'(?:by|von)\s+(?:our|their|the|unser\w*|dem|der)\b',
    re.I,
)
_SPONSORSHIP_OTHER_ASSIGNMENT = re.compile(
    r'^\s+(?:for|für)\s+(?!(?:this|the current|diese\w*)\s+(?:role|position|job|stelle)\b)'
    r'[^.;!?]{0,65}\b(?:roles?|positions?|jobs?|stellen|positionen|teams?)\b',
    re.I,
)
_SPONSORSHIP_LOCATION = re.compile(
    r'^\s+(?:in|at|am)\s+(?=[\w])', re.I,
)

_EXISTING_PERMIT_CUE = re.compile(
    r'\b(?:'
    r'already\s+(?:hold\w*|have|possess\w*|authori[sz]ed|eligible)|'
    r'must\s+(?:already\s+)?(?:hold|have|possess|be\s+(?:legally\s+)?(?:authori[sz]ed|eligible|permitted))|'
    r'need(?:s)?\s+to\s+(?:already\s+)?(?:hold|have|possess|be)|'
    r'requires?|required|mandatory|essential|necessary|prerequisite|'
    r'erfordert|verlangt|erforderlich|zwingend|notwendig|vorausgesetzt|voraussetzung|pflicht|verpflichtend|'
    r'muss\s+(?:vorliegen|vorhanden\s+sein|bestehen)|benötig\w*'
    r')\b',
    re.I,
)


def _sponsorship_matches_jurisdiction(
    offer_jurs: frozenset[str],
    target_jurs: frozenset[str],
    *,
    role_restricted: bool = False,
) -> bool:
    """Return True if a sponsorship offer scoped to ``offer_jurs`` applies to ``target_jurs``."""
    if role_restricted:
        return False
    if offer_jurs:
        if not target_jurs:
            return False
        if offer_jurs & target_jurs:
            return True
        if 'EU' in offer_jurs and (target_jurs & _EU_MEMBER_CODES):
            return True
        return False
    return True


def _extract_sponsorship_offers_from_sentences(
    sentences: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Extract non-negated sponsorship offers with their bound jurisdiction and role-scope metadata."""
    offers: list[dict[str, Any]] = []
    for sentence, _ in sentences:
        for hit in _SPONSORSHIP_OFFER.finditer(sentence):
            prefix_start = max(0, hit.start() - 50)
            prefix = sentence[prefix_start:hit.start()]
            boundary = re.split(r'[.;:!?\n]|\b(?:or|oder|but|aber)\b', prefix, flags=re.I)[-1]
            if _SPONSORSHIP_NEGATION.search(boundary):
                continue
            suffix = sentence[hit.end():min(len(sentence), hit.end() + 45)]
            if re.search(r'^\s*(?:is\s+)?(?:not|nicht)\s+(?:available|possible|offered|möglich|verfügbar)', suffix, re.I):
                continue
            # Bind offer to jurisdictions mentioned in the same sponsorship clause.
            # Stop before a preceding semicolon/period, or before a comma if an unrelated
            # authorization requirement preceded the comma (e.g. "German permit required, sponsorship for Canada").
            clause_start = max(
                sentence.rfind(';', 0, hit.start()) + 1,
                sentence.rfind('.', 0, hit.start()) + 1,
            )
            comma_pos = sentence.rfind(',', clause_start, hit.start())
            if comma_pos != -1 and AUTHORIZATION_CONDITION.search(sentence[clause_start:comma_pos]):
                clause_start = comma_pos + 1
            semi_end = sentence.find(';', hit.end())
            clause = sentence[clause_start:semi_end] if semi_end != -1 else sentence[clause_start:]
            explicit_location = _SPONSORSHIP_LOCATION.match(suffix)
            # An offer scoped to a location must establish its own jurisdiction.
            # Never borrow one from a preceding permit condition.
            jurisdiction_text = sentence[hit.end():semi_end if semi_end != -1 else len(sentence)] if explicit_location else clause
            offer_jurs = normalize_jurisdictions(jurisdiction_text, strict_codes=True)
            role_restricted = bool(
                _SPONSORSHIP_OTHER_ROLE_SCOPE.search(clause)
                or _SPONSORSHIP_OTHER_ASSIGNMENT.match(suffix)
                or (explicit_location and not offer_jurs)
                or _is_other_actor_subject(sentence)
                or is_source_instruction(sentence)
            )
            offers.append({
                'jurisdictions': offer_jurs,
                'role_restricted': role_restricted,
                'sentence': sentence[:260],
            })
            break
    return offers


def _extract_sponsorship_offers(text: str) -> list[dict[str, Any]]:
    """Extract non-negated sponsorship offers with their bound jurisdiction and role-scope metadata."""
    if not _SPONSORSHIP_OFFER.search(text):
        return []
    return _extract_sponsorship_offers_from_sentences(list(scoped_sentences(structured_text(text))))


def has_sponsorship_alternative(
    text: str,
    *,
    target_jurisdictions: frozenset[str] | None = None,
) -> bool:
    """Return True when ``text`` offers visa/permit sponsorship matching ``target_jurisdictions``."""
    if not text:
        return False
    offers = _extract_sponsorship_offers(text)
    if not offers:
        return False
    if target_jurisdictions is None:
        return any(not o['role_restricted'] for o in offers)
    return any(
        _sponsorship_matches_jurisdiction(
            o['jurisdictions'],
            target_jurisdictions,
            role_restricted=o['role_restricted'],
        )
        for o in offers
    )


def _condition_waived(text: str, hit: re.Match) -> bool:
    before = re.split(r'[,;]|\b(?:but|however|aber|jedoch)\b', text[:hit.start()], flags=re.I)[-1]
    after = re.split(r'[,;]|\b(?:but|however|aber|jedoch)\b', text[hit.end():], flags=re.I)[0]
    return bool(
        re.search(r'\b(?:no|kein\w*)\s+(?:[\w-]+\s+){0,4}$', before, re.I)
        or re.match(
            r'\s+(?:(?:is|are|ist|sind)\s+)?(?:not\s+(?:required|necessary|mandatory|essential)|'
            r'nicht\s+(?:erforderlich|notwendig|zwingend))\b',
            after,
            re.I,
        )
    )


def _is_employer_permit_offer(arm: str) -> bool:
    """Return True when the clause is an employer offer to help obtain a permit, not an existing-permit requirement."""
    if has_sponsorship_alternative(arm):
        return True
    hit = _EMPLOYER_PERMIT_ASSISTANCE.search(arm)
    if not hit:
        return False
    prefix = arm[max(0, hit.start() - 35):hit.start()]
    if _SPONSORSHIP_NEGATION.search(prefix) or _SPONSORSHIP_NEGATION.search(arm[hit.start():min(len(arm), hit.end() + 25)]):
        return False
    return bool(
        re.search(
            r'\b(?:obtain\w*|get\w*|secur\w*|acquir\w*|apply\w*|sponsor\w*|relocat\w*|beantrag\w*|erhalt\w*|beschaff\w*)\b',
            arm[hit.start():],
            re.I,
        )
    )


def _has_existing_permit_condition(arm: str, scope: str, hit: re.Match) -> bool:
    """Require an affirmative existing-permit condition (must already hold/have right to work in X)."""
    if _is_other_actor_subject(arm):
        return False
    if _is_employer_permit_offer(arm) or re.search(
        r'\b(?:can\s+be\s+(?:obtained|arranged|acquired)|kann\s+[^.!?;]{0,45}\b(?:beantragt|beschafft|erworben)\s+werden)\b',
        arm[hit.end():], re.I,
    ):
        return False
    if NON_REQUIREMENT_V2.search(arm) and not _EXISTING_PERMIT_CUE.search(arm):
        return False
    if _EXISTING_PERMIT_CUE.search(arm):
        return True
    # Inside an explicit requirements section, bare phrases like "Valid work permit for Germany" or
    # "Right to work in the UK" count unless informational/offer-like.
    if scope == 'requirements':
        if re.search(r'\b(?:information\s+(?:about|on)|questions?\s+about|we\s+can\s+help|wir\s+helfen)\b', arm, re.I):
            return False
        return True
    return False


_ELIGIBILITY_OR_SPLIT_RE = re.compile(r'(?:,\s*|\s+)\b(?:or|oder)\b\s+', re.I)
_NON_BARE_JURISDICTION_ARM_RE = re.compile(
    r'\b(?:citizen\w*|national\w*|passport\w*|residen\w*|staatsbürg\w*|staatsbuerg\w*|'
    r'staatsangehörig\w*|staatsangehoerig\w*|pass|aufenthalt\w*|visa|visum|permit|card|karte)\b',
    re.I,
)


def _split_eligibility_or_arms(text: str) -> list[str]:
    """Split a clause on top-level OR conjunctions without splitting inside jurisdiction lists."""
    working, protected = mask_parens(text)
    parts = [p.strip() for p in _ELIGIBILITY_OR_SPLIT_RE.split(working) if p.strip()]
    if len(parts) <= 1:
        return [text]
    # Merge back bare jurisdiction OR pairs like 'Germany or Switzerland' inside one permit phrase,
    # while keeping distinct status alternatives like 'or EU citizenship' separate.
    merged: list[str] = [parts[0]]
    for part in parts[1:]:
        if (
            not AUTHORIZATION_CONDITION.search(part)
            and not has_sponsorship_alternative(part)
            and not _NON_BARE_JURISDICTION_ARM_RE.search(part)
            and normalize_jurisdictions(part, strict_codes=True)
            and len(part.split()) <= 3
        ):
            merged[-1] = merged[-1] + ' or ' + part
        else:
            merged.append(part)
    return [unmask_parens(item, protected).strip() for item in merged]


# ---------------------------------------------------------------------------
# Explicit mandatory onsite attendance detection
# ---------------------------------------------------------------------------

_NO_REMOTE_EXP_GUARD = (
    r'(?!\s*(?:[-–]\s*)?(?:work\s+|arbeit[s-]*)?'
    r'(?:experience|background|history|skills?|knowledge|setup|equipment|tools?|'
    r'erfahrung\w*|vorkenntnis\w*|kenntnis\w*|ausstattung|'
    r'(?:is\s+)?(?:required|needed|necessary|erforderlich|notwendig))\b)'
)

_EXPLICIT_NO_REMOTE = re.compile(
    r'\b(?:'
    r'no\s+(?:remote(?:\s+work(?:ing)?)?|home\s*office|work\s+from\s+home)' + _NO_REMOTE_EXP_GUARD + r'|'
    r'(?:does|do)\s+not\s+(?:offer|allow|permit|support)\s+(?:remote(?:\s+work(?:ing)?)?|home\s*office|work\s+from\s+home)|'
    r'(?:kein\w*|ohne)\s+(?:remote(?:[- ]?(?:work|arbeit|möglichkeit))?|home\s*office|mobiles?\s+arbeiten)' + _NO_REMOTE_EXP_GUARD + r'|'
    r'(?:remote(?:\s+work(?:ing)?)?|home\s*office|mobiles?\s+arbeiten)\s+(?:is|ist)?\s*(?:not|nicht)\s+'
    r'(?:possible|available|offered|permitted|an\s+option|möglich|vorgesehen|verfügbar)|'
    r'100\s*%\s*(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|präsenz(?:pflicht)?)|'
    r'präsenzpflicht|anwesenheitspflicht)\b',
    re.I,
)
_ONSITE_LOCATION_TERM = re.compile(
    r'\b(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|präsenz(?:arbeit|tätigkeit)?|'
    r'büroanwesenheit|anwesenheit\s+(?:im\s+büro|vor[- ]?ort|am\s+standort)|'
    r'im\s+büro|in\s+unserem\s+büro|am\s+standort|an\s+unserem\s+(?:standort|hauptsitz|sitz)|'
    r'in\s+the\s+office|in\s+our\s+office|'
    r'at\s+our\s+(?:office|site|plant|facility|warehouse|location|headquarters|hq|premises|branch|campus))\b',
    re.I,
)
_ONSITE_DAYS = (
    r'(?:(?:[2-5]|two|three|four|five|zwei|drei|vier|fünf|fuenf)\s+'
    r'(?:days?|tage)\s*(?:/|\s+(?:per|a|pro|in\s+der)\s+)(?:week|wk|woche))'
)
_ONSITE_INTERVENING_LOC = (
    r'(?:(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|im\s+büro|am\s+standort(?:\s+[\wäöüß-]+)?|'
    r'(?:in|at|am)\s+(?:(?:our|the|unserem|dem)\s+)?[\wäöüß-]+)\s+){0,3}'
)
_ONSITE_MANDATE = re.compile(
    r'\b(?:'
    r'must\s+(?:work|be|attend|report)\b|'
    r'required\s+to\s+(?:work|be|attend|report)\b|'
    r'(?:role|position|job|vacancy|stelle|tätigkeit|arbeit)\s+(?:requires?|demands?|erfordert|verlangt)\b|'
    r'requires?\s+(?:(?:regular|daily|full[- ]?time|100\s*%|physical|in[- ]?person)\s+)?'
    r'(?:on[- ]?site|in[- ]?office|in[- ]?person|office)\s+(?:work|attendance|presence)\b|'
    r'(?:erfordert|verlangt)\s+(?:(?:eine?\s+)?(?:tägliche[rnms]?|regelmäßige[rnms]?|vollständige[rnms]?|durchgehende[rnms]?|100\s*%)\s+)?'
    r'(?:anwesenheit|präsenz|büroanwesenheit|arbeit)\b|'
    r'(?:muss|müssen)\s+[^.;:!?\n]{0,45}\b(?:vor[- ]?ort|im\s+büro|in\s+präsenz|am\s+standort)\b|'
    r'(?:vor[- ]?ort|im\s+büro|in\s+präsenz|am\s+standort)\s+[^.;:!?\n]{0,40}\b(?:arbeiten|tätig\s+sein|anwesend\s+sein)\s+(?:muss|müssen)\b|'
    r'(?:attendance|presence|büroanwesenheit|anwesenheit|präsenz|work|working|arbeit)\s+'
    + _ONSITE_INTERVENING_LOC
    + r'(?:is|ist)?\s*(?:required|mandatory|expected|essential|erforderlich|notwendig|verpflichtend|pflicht|zwingend|vorausgesetzt)|'
    r'(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|präsenz)\s+'
    r'(?:(?:work|attendance|presence|location|role|position|tätigkeit|arbeit|anwesenheit)\s+)?'
    + _ONSITE_INTERVENING_LOC
    + r'(?:is|ist)?\s*(?:required|mandatory|expected|essential|erforderlich|notwendig|verpflichtend|pflicht|zwingend|vorausgesetzt)|'
    r'(?:full[- ]?time|fully|strictly|exclusively|purely|solely|ausschließlich\w*|vollständig\w*|komplett|rein|vollzeit|täglich\w*|daily)\s+'
    r'(?:on[- ]?site|in[- ]?office|in[- ]?person|vor[- ]?ort|im\s+büro|in\s+präsenz|präsenz|anwesenheit|at\s+our\s+(?:office|site|plant|warehouse|location|facility))|'
    r'(?:this\s+(?:is\s+an?|(?:role|position|job|vacancy)\s+is)|(?:role|position|job|vacancy)\s+is)\s+'
    r'(?:a\s+)?(?:(?:full[- ]?time|100\s*%)\s+)?(?:on[- ]?site|in[- ]?office|in[- ]?person)\b|'
    r'(?:stelle|position|tätigkeit|arbeit|ausschreibung)\s+(?:ist|erfolgt|findet)\s+'
    r'(?:(?:eine|vollständig|ausschließlich|komplett|rein|vollzeit|überwiegend|100\s*%)\s+){0,2}(?:vor[- ]?ort|im\s+büro|in\s+präsenz|am\s+standort)|'
    r'(?:monday\s*(?:to|through|-|–)\s*friday|mon\s*[-–]\s*fri|'
    r'montag\s*(?:bis|-|–)\s*freitag|mo\.?\s*[-–]\s*fr\.?|'
    + _ONSITE_DAYS
    + r'))\b',
    re.I,
)
_ONSITE_NEGATIVE = re.compile(
    r'\b(?:'
    r'no\s+(?:prior\s+)?(?:remote|home\s*office|on[- ]?site|in[- ]?office)(?:\s+work(?:ing)?)?\s+'
    r'(?:experience|background|history|skills?|knowledge|setup|equipment)\b|'
    r'kein\w*\s+(?:remote|home\s*office|vor[- ]?ort)[- ]?(?:erfahrung\w*|vorkenntnis\w*|kenntnis\w*|ausstattung)\b|'
    r'(?:on[- ]?site|in[- ]?office|vor[- ]?ort)[- ]+(?:work[- ]+|arbeits?)?'
    r'(?:experience|background|history|skills?|knowledge|erfahrung\w*|vorkenntnis\w*)|'
    r'no\s+(?:on[- ]?site|in[- ]?office|office)\s+(?:attendance|presence|work)\s+(?:is\s+)?required|'
    r'kein\w*\s+(?:präsenz|anwesenheit)\s+(?:vor[- ]?ort\s+)?(?:erforderlich|notwendig)|'
    r'(?:fully|vollständig|100\s*%)\s+remote|remote[- ]first|'
    r'(?:optional|occasional|gelegentlich\w*)\s+(?:on[- ]?site|office|in[- ]?office|vor[- ]?ort)|'
    r'(?:travel|reisebereitschaft|kundenbesuch\w*|vor[- ]?ort[- ]?termin\w*)\b[^.!?\n]{0,35}\b(?:client|customer|kunden)|'
    r'(?:on[- ]?site|vor[- ]?ort)\s+(?:at|with|beim?|bei\s+den)\s+(?:client|customer|kunden))\b',
    re.I,
)


def _explicit_onsite_from_sentences(sentences: Sequence[tuple[str, str]]) -> str | None:
    for sentence, scope in sentences:
        if (
            is_source_instruction(sentence)
            or _is_other_actor_subject(sentence)
            or _ONSITE_NEGATIVE.search(sentence)
        ):
            continue
        if (hit := _EXPLICIT_NO_REMOTE.search(sentence)):
            if requirement_polarity(sentence, hit.start(), hit.end()) == 'required':
                return sentence[:240]
        if scope == 'optional':
            continue
        if (loc := _ONSITE_LOCATION_TERM.search(sentence)) and _ONSITE_MANDATE.search(sentence):
            if requirement_polarity(sentence, loc.start(), loc.end()) == 'required':
                return sentence[:240]
    return None


def explicit_onsite_evidence(text: str) -> str | None:
    """Return explicit mandatory on-site attendance evidence from posting text, if any."""
    if not text or not isinstance(text, str):
        return None
    return _explicit_onsite_from_sentences(list(scoped_sentences(structured_text(text))))


# ---------------------------------------------------------------------------
# Language parsing (CEFR, native/near-native/Muttersprache, shared floors, OR)
# ---------------------------------------------------------------------------

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
    if re.search(r'\b(?:NATIVE|MUTTERSPRACH|MOTHER\s+TONGUE|FIRST\s+LANGUAGE)\b', text):
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
    if language == 'Polish' and re.match(
        r'\s+(?:up|off|out|your|the|a|an|my|our|their|this|that|these|those)\b',
        clause[hit.end():],
        re.I,
    ):
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
                    and not re.search(r'\b(?:kenntnis\w*|proficiency|fluency|sprachniveau)\b', clause, re.I)
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
                        and re.search(
                            r'\b(?:A1|A2|B1|B2|C1|C2)\s+(?:is\s+(?:enough|sufficient|fine|okay|ok)|'
                            r'suffices|reicht(?:\s+aus)?|genügt|ist\s+ausreichend)\b',
                            local_window,
                            re.I,
                        )
                        and not re.search(
                            r'\b(?:at\s+least|minimum|min\.?|mindestens|mind\.?|required|mandatory|erforderlich|zwingend)\b',
                            local_window,
                            re.I,
                        )
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


# ---------------------------------------------------------------------------
# Work authorisation / visa gate
# ---------------------------------------------------------------------------

def _evaluate_authorization(
    parsed_texts: tuple[tuple[str, bool, tuple[tuple[str, str], ...]], ...],
    profile: dict,
    raw_metadata: dict,
) -> dict[str, Any]:
    preferences = (
        profile.get('application_preferences')
        or (profile.get('application_details') or {}).get('preferences')
        or {}
    )
    country = str(preferences.get('authorization_country') or '').strip()
    profile_jurisdictions = normalize_jurisdictions(country, strict_codes=False)
    auth_flag = preferences.get('authorized_to_work')
    spons_req = preferences.get('sponsorship_required')

    all_sponsorship_offers: list[dict[str, Any]] = []
    no_sponsorship_jurs: list[frozenset[str]] = []
    for _, _, sentences in parsed_texts:
        all_sponsorship_offers.extend(_extract_sponsorship_offers_from_sentences(sentences))
        for sentence, _ in sentences:
            if _EXPLICIT_NO_SPONSORSHIP.search(sentence):
                no_sponsorship_jurs.append(normalize_jurisdictions(sentence, strict_codes=True))

    sponsorship_offered = (
        raw_metadata.get('visa_sponsorship') is True
        or bool(all_sponsorship_offers)
    )

    hard_fails: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    met_notes: list[str] = []
    met_details: list[dict[str, Any]] = []

    seen_clauses: set[str] = set()
    for source, force_req, sentences in parsed_texts:
        for sentence, scope in sentences:
            if is_source_instruction(sentence) or scope == 'optional':
                continue
            if not AUTHORIZATION_CONDITION.search(sentence):
                continue
            if OPTIONAL.search(sentence) and not re.search(r'\b(?:mandatory|must|required|erforderlich|voraussetzung|zwingend)\b', sentence, re.I):
                continue
            norm_key = ' '.join(sentence.casefold().split())
            if norm_key in seen_clauses:
                continue
            seen_clauses.add(norm_key)

            effective_scope = 'requirements' if (force_req or scope == 'requirements') else scope
            or_arms = _split_eligibility_or_arms(sentence)

            # Collect jurisdictions from requirement arms so any sponsorship OR arm is
            # checked against the same jurisdiction & role scope.
            arm_req_jurs: set[str] = set()
            for a in or_arms:
                if (ah := AUTHORIZATION_CONDITION.search(a)) and not _is_employer_permit_offer(a):
                    arm_req_jurs.update(authorization_jurisdictions(a, ah))
            sentence_req_jurs = frozenset(arm_req_jurs)

            # Pure employer permit-assistance offer (e.g. "We can help employees obtain
            # a German work permit") has no existing-permit requirement -> never fail.
            if len(or_arms) == 1 and _is_employer_permit_offer(sentence):
                continue

            sentence_no_spons = bool(_EXPLICIT_NO_SPONSORSHIP.search(sentence))
            arm_outcomes: list[tuple[str, dict[str, Any]]] = []

            for arm in or_arms:
                if _is_employer_permit_offer(arm):
                    if len(or_arms) == 1:
                        continue
                    arm_offers = _extract_sponsorship_offers_from_sentences([(arm, effective_scope)])
                    if arm_offers:
                        applies = any(
                            _sponsorship_matches_jurisdiction(
                                o['jurisdictions'],
                                sentence_req_jurs,
                                role_restricted=o['role_restricted'],
                            )
                            for o in arm_offers
                        )
                    else:
                        applies = _sponsorship_matches_jurisdiction(
                            normalize_jurisdictions(arm, strict_codes=True),
                            sentence_req_jurs,
                            role_restricted=bool(_SPONSORSHIP_OTHER_ROLE_SCOPE.search(arm)),
                        )
                    if applies:
                        arm_outcomes.append((
                            'unknown',
                            {
                                'kind': 'work_authorization',
                                'gate': 'eligibility',
                                'detail': f'Employer sponsorship alternative stated; verify authorization for: {sentence[:200]}',
                                'job_source': source,
                                'job_quote': sentence[:260],
                                'profile_source': 'profile.application_preferences.authorized_to_work',
                                'profile_quote': f'authorization_country={country}; authorized_to_work={auth_flag}',
                            },
                        ))
                    continue

                hit = AUTHORIZATION_CONDITION.search(arm)
                if not hit:
                    if len(or_arms) > 1:
                        # Unresolved non-permit OR arm (e.g. "or EU citizenship") -> unknown, never drop.
                        arm_outcomes.append((
                            'unknown',
                            {
                                'kind': 'work_authorization',
                                'gate': 'eligibility',
                                'detail': f'Verify work authorization alternative: {arm[:200]}',
                                'job_source': source,
                                'job_quote': sentence[:260],
                                'profile_source': 'profile.application_preferences.authorized_to_work',
                                'profile_quote': '',
                            },
                        ))
                    continue

                if requirement_polarity(arm, hit.start(), hit.end()) != 'required' or _condition_waived(arm, hit):
                    continue
                if not _has_existing_permit_condition(arm, effective_scope, hit):
                    continue

                req_jurs = authorization_jurisdictions(arm, hit)
                overlap = req_jurs & profile_jurisdictions
                and_joined = bool(
                    len(req_jurs) > 1
                    and re.search(r'\b(?:and|und)\b', arm, re.I)
                    and not re.search(r'\b(?:or|oder)\b|/', arm, re.I)
                )
                authorized_scope = (req_jurs <= profile_jurisdictions) if and_joined else bool(overlap)
                denied_scope = bool(req_jurs) and req_jurs <= profile_jurisdictions

                # Bind sponsorship alternatives to the same role and jurisdiction.
                explicit_no_spons = sentence_no_spons or any(
                    (not ns_jurs)
                    or bool(ns_jurs & req_jurs)
                    or ('EU' in ns_jurs and bool(req_jurs & _EU_MEMBER_CODES))
                    for ns_jurs in no_sponsorship_jurs
                )
                sponsorship_for_req = (
                    not explicit_no_spons
                    and (
                        raw_metadata.get('visa_sponsorship') is True
                        or any(
                            _sponsorship_matches_jurisdiction(
                                o['jurisdictions'],
                                req_jurs,
                                role_restricted=o['role_restricted'],
                            )
                            for o in all_sponsorship_offers
                        )
                    )
                )

                authorized = bool(req_jurs) and authorized_scope and auth_flag is True and spons_req is False
                denied = (not sponsorship_for_req) and denied_scope and auth_flag is False

                prof_quote = (
                    f'authorization_country={country}; authorized_to_work={auth_flag}'
                    + (f'; sponsorship_required={spons_req}' if spons_req is not None else '')
                ) if country else ''

                if authorized:
                    arm_outcomes.append((
                        'pass',
                        {
                            'kind': 'work_authorization',
                            'gate': 'eligibility',
                            'detail': f'Saved work authorization ({prof_quote}) covers {sorted(req_jurs)}',
                            'job_source': source,
                            'job_quote': arm[:260],
                            'profile_source': 'profile.application_preferences.authorized_to_work',
                            'profile_quote': prof_quote,
                        },
                    ))
                elif denied:
                    arm_outcomes.append((
                        'fail',
                        {
                            'kind': 'work_authorization',
                            'gate': 'eligibility',
                            'jurisdictions': sorted(req_jurs),
                            'detail': f'Saved facts explicitly deny work authorization for {sorted(req_jurs)}: {arm[:200]}',
                            'job_source': source,
                            'job_quote': arm[:260],
                            'profile_source': 'profile.application_preferences.authorized_to_work',
                            'profile_quote': prof_quote,
                        },
                    ))
                else:
                    msg = (
                        f'Employer sponsorship or visa assistance stated; verify authorization: {arm[:200]}'
                        if sponsorship_for_req
                        else f'Required jurisdiction-specific work authorization is unverified: {arm[:200]}'
                    )
                    arm_outcomes.append((
                        'unknown',
                        {
                            'kind': 'work_authorization',
                            'gate': 'eligibility',
                            'jurisdictions': sorted(req_jurs),
                            'detail': msg,
                            'job_source': source,
                            'job_quote': arm[:260],
                            'profile_source': 'profile.application_preferences.authorized_to_work' if prof_quote else '',
                            'profile_quote': prof_quote,
                        },
                    ))

            if not arm_outcomes:
                continue
            if any(st == 'pass' for st, _ in arm_outcomes):
                passed = next(p for st, p in arm_outcomes if st == 'pass')
                met_notes.append(passed['detail'])
                met_details.append(passed)
            elif all(st == 'fail' for st, _ in arm_outcomes):
                hard_fails.append(arm_outcomes[0][1])
            else:
                flags.append(next(p for st, p in arm_outcomes if st == 'unknown'))

    status = 'fail' if hard_fails else 'unknown' if flags else 'pass'
    reason = (
        '; '.join(item['detail'] for item in hard_fails)[:500]
        if hard_fails
        else '; '.join(item['detail'] for item in flags)[:500]
        if flags
        else ('Saved authorization covers stated permit requirement.' if met_notes else 'No mandatory work authorization restriction stated.')
    )
    return {
        'status': status,
        'reason': reason,
        'hard_fails': hard_fails,
        'flags': flags,
        'met': met_notes,
        'met_details': met_details,
        'sponsorship_offered': sponsorship_offered,
    }


# ---------------------------------------------------------------------------
# Onsite attendance & location gate
# ---------------------------------------------------------------------------

def _evaluate_location(
    job: dict,
    parsed_texts: tuple[tuple[str, bool, tuple[tuple[str, str], ...]], ...],
    profile: dict,
    enriched: dict,
) -> dict[str, Any]:
    preferences = (
        profile.get('application_preferences')
        or (profile.get('application_details') or {}).get('preferences')
        or {}
    )
    refused_onsite = preferences.get('willing_to_work_onsite') is False
    work_style = job.get('work_arrangement') or ('remote' if job.get('is_remote') else enriched.get('work_style'))

    onsite_quote: str | None = None
    onsite_source = 'description'
    for source, _, sentences in parsed_texts:
        found = _explicit_onsite_from_sentences(sentences)
        if found:
            onsite_quote = found
            onsite_source = source
            break

    hard_fails: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    metadata_only_onsite = False

    if refused_onsite and onsite_quote:
        hard_fails.append({
            'kind': 'declined_onsite',
            'gate': 'location',
            'detail': f'Saved preferences decline explicit on-site attendance: {onsite_quote}',
            'job_source': onsite_source,
            'job_quote': onsite_quote,
            'profile_source': 'profile.application_preferences.willing_to_work_onsite',
            'profile_quote': 'willing_to_work_onsite=False',
        })
    elif refused_onsite and work_style in {'onsite', 'hybrid'}:
        metadata_only_onsite = True
        flags.append({
            'kind': 'metadata_onsite',
            'gate': 'location',
            'detail': (
                f'Metadata lists {work_style} without explicit on-site attendance text; '
                f'verify attendance expectations ({job.get("location_text") or "location unspecified"}).'
            ),
            'job_source': 'metadata',
            'job_quote': f'work_arrangement={work_style}',
            'profile_source': 'profile.application_preferences.willing_to_work_onsite',
            'profile_quote': 'willing_to_work_onsite=False',
        })
    elif onsite_quote and preferences.get('willing_to_work_onsite') is not True:
        flags.append({
            'kind': 'onsite_verify',
            'gate': 'location',
            'detail': f'Verify on-site attendance expectations: {onsite_quote}',
            'job_source': onsite_source,
            'job_quote': onsite_quote,
            'profile_source': 'profile.application_preferences.willing_to_work_onsite',
            'profile_quote': str(preferences.get('willing_to_work_onsite')),
        })

    # Residence restriction check (flag only, never hard fail).
    found_residence = False
    for source, _, sentences in parsed_texts:
        if found_residence:
            break
        for sentence, scope in sentences:
            if scope in {'benefits', 'optional'} or is_source_instruction(sentence):
                continue
            res = re.search(
                r'\b(?:must\s+reside|must\s+live|residency\s+is\s+required|required\s+residence|'
                r'wohnsitz\s+erforderlich|wohnsitz\s+in)\b',
                sentence,
                re.I,
            )
            if res and requirement_polarity(sentence, res.start(), res.end()) == 'required':
                flags.append({
                    'kind': 'residence',
                    'gate': 'location',
                    'detail': f'Verify required residence/payroll jurisdiction: {sentence[:220]}',
                    'job_source': source,
                    'job_quote': sentence[:240],
                    'profile_source': '',
                    'profile_quote': '',
                })
                found_residence = True
                break

    status = 'fail' if hard_fails else 'unknown' if flags else 'pass'
    reason = (
        hard_fails[0]['detail']
        if hard_fails
        else '; '.join(f['detail'] for f in flags)[:500]
        if flags
        else 'No mandatory workplace condition conflicted with saved preferences.'
    )
    return {
        'status': status,
        'reason': reason,
        'hard_fails': hard_fails,
        'flags': flags,
        'onsite_evidence': onsite_quote,
        'metadata_only_onsite': metadata_only_onsite,
    }


# ---------------------------------------------------------------------------
# Excluded title terms gate
# ---------------------------------------------------------------------------

_INACTIVE_CREDENTIAL = re.compile(
    r'^(?:expired|revoked|inactive|lapsed|abgelaufen|widerrufen)\s+|'
    r'\s+\(?(?:expired|revoked|inactive|lapsed|abgelaufen|widerrufen)\)?$',
    re.I,
)


def _inactive_credential_conflicts(parsed_texts, profile):
    certifications = [str(value).strip() for value in profile.get('certifications') or []]
    unavailable = [
        (index, credential, _INACTIVE_CREDENTIAL.sub(' current ', credential).strip())
        for index, credential in enumerate(certifications)
        if _INACTIVE_CREDENTIAL.search(credential)
    ]
    if not unavailable:
        return []
    failures = []
    seen = set()
    for source, _, sentences in parsed_texts:
        for sentence, scope in sentences:
            hit = _LICENSE_CONDITION.search(sentence)
            if not hit or scope in {'duties', 'optional', 'benefits'} or is_source_instruction(sentence):
                continue
            if scope != 'requirements' and not REQUIREMENT.search(sentence):
                continue
            if re.search(r'\b(?:or|oder|renew|verlänger\w*)\b', sentence, re.I):
                continue
            if requirement_polarity(sentence, hit.start(), hit.end()) != 'required' or _condition_waived(sentence, hit):
                continue
            if hit.group().casefold() in {'active license', 'active licence', 'current license', 'current licence'}:
                continue
            if _credential_evidence(sentence, hit, certifications):
                continue
            for index, quote, current_form in unavailable:
                key = (sentence.casefold(), quote.casefold())
                if key in seen or not _credential_evidence(sentence, hit, [current_form]):
                    continue
                seen.add(key)
                failures.append({
                    'kind': 'license', 'gate': 'eligibility',
                    'detail': f'Saved credential is inactive: {quote}',
                    'job_source': source, 'job_quote': sentence[:260],
                    'profile_source': f'profile.certifications[{index}]', 'profile_quote': quote,
                })
                break
    return failures


def _evaluate_excluded_terms(job: dict, profile: dict) -> dict[str, Any]:
    title = plain_text(job.get('title'))
    hard_fails: list[dict[str, Any]] = []
    for term in profile.get('excluded_terms') or []:
        term_str = str(term or '').strip()
        if term_str and _pattern(term_str).search(title):
            hard_fails.append({
                'kind': 'excluded_term',
                'gate': 'excluded_terms',
                'detail': f'Excluded title term: {term_str}',
                'job_source': 'title',
                'job_quote': title[:240],
                'profile_source': 'profile.excluded_terms',
                'profile_quote': term_str,
            })
    status = 'fail' if hard_fails else 'pass'
    reason = '; '.join(item['detail'] for item in hard_fails)[:500] if hard_fails else 'No excluded title term matched.'
    return {
        'status': status,
        'reason': reason,
        'hard_fails': hard_fails,
        'flags': [],
    }


# ---------------------------------------------------------------------------
# Public helpers for features.py to avoid language/visa ablation leakage
# ---------------------------------------------------------------------------

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

    for idx, m in reversed(list(enumerate(re.finditer(r'\([^()]*\)', working)))):
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
        return re.sub(r'\s+', ' ', lead_in + _restore(working)).strip() + trailing_punct

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
            if not re.search(rf'\b{re.escape(cue_word)}\b', f'{lead_in}{joined_so_far}', re.I):
                trailing_cue = f' {cue_word}'

    rebuilt = f'{lead_in}{"".join(out_chunks)}{trailing_cue}'.strip()
    rebuilt = re.sub(r'\s+', ' ', rebuilt)
    if trailing_punct and rebuilt and rebuilt[-1] not in '.!?':
        rebuilt += trailing_punct
    return rebuilt


# ---------------------------------------------------------------------------
# Unified gate entry point
# ---------------------------------------------------------------------------

def job_requirement_texts(job: dict) -> tuple:
    """The content and source scopes that determine the parsed posting cache key."""
    raw = job.get('raw_metadata') or {}
    detail_req = raw.get('detail_requirements') or {}

    description = str(job.get('description') or '')
    texts: list[tuple[str, str, bool]] = [('description', description, False)]

    # Include structured metadata fields (qualifications, skills, languages, visa/auth, etc.).
    for prefix, container in (('structured', raw), ('detail', detail_req)):
        if not isinstance(container, dict):
            continue
        for field in (
            'responsibilities',
            'duties',
            'skills',
            'qualifications',
            'experienceRequirements',
            'educationRequirements',
        ):
            val = container.get(field)
            if val:
                for src, txt in structured_requirement_texts({field: val}, prefix):
                    texts.append((src, str(txt), False))
        # Explicit language lists in structured metadata are requirements-scoped.
        langs_meta = container.get('languages')
        if langs_meta:
            for src, txt in structured_requirement_texts({'languages': langs_meta}, prefix):
                texts.append((src, str(txt), True))
        for auth_field in ('work_authorization', 'work_permit', 'arbeitserlaubnis', 'visa'):
            val = container.get(auth_field)
            if isinstance(val, str) and val.strip():
                texts.append((f'{prefix}.{auth_field}', f'{auth_field}: {val}', True))

    return tuple(texts)


@lru_cache(maxsize=256)
def parse_job_requirement_texts(texts: tuple) -> tuple:
    return tuple(
        (source, force_req, tuple(dict.fromkeys(
            scoped_sentences(
                structured_text(text),
                initial_scope='requirements' if force_req else structured_source_scope(source),
            )
        )))
        for source, text, force_req in texts if text
    )


def evaluate_gates(job: dict, profile: dict, enriched: dict) -> dict[str, Any]:
    """Run the single deterministic gate evaluator over full posting context + profile facts."""
    raw = job.get('raw_metadata') or {}
    parsed_texts = parse_job_requirement_texts(job_requirement_texts(job))

    lang_res = _evaluate_languages(parsed_texts, profile)
    auth_res = _evaluate_authorization(parsed_texts, profile, raw)
    loc_res = _evaluate_location(job, parsed_texts, profile, enriched)
    excl_res = _evaluate_excluded_terms(job, profile)
    credential_fails = _inactive_credential_conflicts(parsed_texts, profile)

    hard_fails = [
        *lang_res['hard_fails'],
        *auth_res['hard_fails'],
        *credential_fails,
        *loc_res['hard_fails'],
        *excl_res['hard_fails'],
    ]
    flags = [
        *lang_res['flags'],
        *auth_res['flags'],
        *loc_res['flags'],
    ]

    return {
        'eligible': bool(not hard_fails),
        'hard_fails': hard_fails,
        'flags': flags,
        'language': lang_res,
        'authorization': auth_res,
        'location': loc_res,
        'excluded_terms': excl_res,
        'gates': {
            'language': {'status': lang_res['status'], 'reason': lang_res['reason']},
            'location': {'status': loc_res['status'], 'reason': loc_res['reason']},
            'eligibility': (
                {'status': 'fail', 'reason': credential_fails[0]['detail']} if credential_fails
                else {'status': auth_res['status'], 'reason': auth_res['reason']}
            ),
            'excluded_terms': {'status': excl_res['status'], 'reason': excl_res['reason']},
        },
        'language_review': lang_res['status'] == 'unknown',
        'authorization_review': auth_res['status'] == 'unknown',
        'metadata_only_onsite': loc_res['metadata_only_onsite'],
        'parsed_texts': parsed_texts,
    }
