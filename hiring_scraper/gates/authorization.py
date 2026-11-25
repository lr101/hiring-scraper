"""Work-authorization, jurisdiction binding, and sponsorship gate for v2."""
from __future__ import annotations

import re
from typing import Any

from hiring_scraper.matching import OPTIONAL
from hiring_scraper.gates.common import (
    NON_REQUIREMENT_V2,
    _is_other_actor_subject,
    mask_parens,
    unmask_parens,
)
from hiring_scraper.requirements import (
    is_source_instruction,
    requirement_polarity,
    scoped_sentences,
    structured_text,
)

_SPONSORSHIP_BOUNDARY_SPLIT_RE = re.compile(r'[.;:!?\n]|\b(?:or|oder|but|aber)\b', re.I)
_SPONSORSHIP_SUFFIX_NEG_RE = re.compile(
    r'^\s*(?:is\s+)?(?:not|nicht)\s+(?:available|possible|offered|möglich|verfügbar)',
    re.I,
)
_WAIVER_CLAUSE_SPLIT_RE = re.compile(r'[,;]|\b(?:but|however|aber|jedoch)\b', re.I)
_WAIVER_BEFORE_NEG_RE = re.compile(r'\b(?:no|kein\w*)\s+(?:[\w-]+\s+){0,4}$', re.I)
_WAIVER_AFTER_NEG_RE = re.compile(
    r'\s+(?:(?:is|are|ist|sind)\s+)?(?:not\s+(?:required|necessary|mandatory|essential)|'
    r'nicht\s+(?:erforderlich|notwendig|zwingend))\b',
    re.I,
)
_PERMIT_ASSIST_VERB_RE = re.compile(
    r'\b(?:obtain\w*|get\w*|secur\w*|acquir\w*|apply\w*|sponsor\w*|relocat\w*|beantrag\w*|erhalt\w*|beschaff\w*)\b',
    re.I,
)
_PERMIT_OBTAINABLE_RE = re.compile(
    r'\b(?:can\s+be\s+(?:obtained|arranged|acquired)|kann\s+[^.!?;]{0,45}\b(?:beantragt|beschafft|erworben)\s+werden)\b',
    re.I,
)
_PERMIT_INFO_OFFER_RE = re.compile(
    r'\b(?:information\s+(?:about|on)|questions?\s+about|we\s+can\s+help|wir\s+helfen)\b',
    re.I,
)
_MANDATORY_OVERRIDE_RE = re.compile(
    r'\b(?:mandatory|must|required|erforderlich|voraussetzung|zwingend)\b',
    re.I,
)
_AND_JOIN_RE = re.compile(r'\b(?:and|und)\b', re.I)
_OR_OR_SLASH_RE = re.compile(r'\b(?:or|oder)\b|/', re.I)

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
            boundary = _SPONSORSHIP_BOUNDARY_SPLIT_RE.split(prefix)[-1]
            if _SPONSORSHIP_NEGATION.search(boundary):
                continue
            suffix = sentence[hit.end():min(len(sentence), hit.end() + 45)]
            if _SPONSORSHIP_SUFFIX_NEG_RE.search(suffix):
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
    before = _WAIVER_CLAUSE_SPLIT_RE.split(text[:hit.start()])[-1]
    after = _WAIVER_CLAUSE_SPLIT_RE.split(text[hit.end():])[0]
    return bool(
        _WAIVER_BEFORE_NEG_RE.search(before)
        or _WAIVER_AFTER_NEG_RE.match(after)
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
        _PERMIT_ASSIST_VERB_RE.search(arm, hit.start())
    )


def _has_existing_permit_condition(arm: str, scope: str, hit: re.Match) -> bool:
    """Require an affirmative existing-permit condition (must already hold/have right to work in X)."""
    if _is_other_actor_subject(arm):
        return False
    if _is_employer_permit_offer(arm) or _PERMIT_OBTAINABLE_RE.search(arm, hit.end()):
        return False
    if NON_REQUIREMENT_V2.search(arm) and not _EXISTING_PERMIT_CUE.search(arm):
        return False
    if _EXISTING_PERMIT_CUE.search(arm):
        return True
    # Inside an explicit requirements section, bare phrases like "Valid work permit for Germany" or
    # "Right to work in the UK" count unless informational/offer-like.
    if scope == 'requirements':
        if _PERMIT_INFO_OFFER_RE.search(arm):
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
            if OPTIONAL.search(sentence) and not _MANDATORY_OVERRIDE_RE.search(sentence):
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
                    and _AND_JOIN_RE.search(arm)
                    and not _OR_OR_SLASH_RE.search(arm)
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
