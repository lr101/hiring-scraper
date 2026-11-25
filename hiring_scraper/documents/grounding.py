"""Deterministic grounding checks for AI-origin document units.

Pure, database-free verification producing unit-anchored ``Finding`` objects.
Heuristics are intentionally conservative to avoid false positives on German
compound nouns, sentence-initial capitals and domain loanwords.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from hiring_scraper.documents.editorial import employer_key
from hiring_scraper.documents.schema import (
    CvDocument,
    DocumentLanguage,
    Finding,
    FindingCode,
    FindingSeverity,
    LetterDocument,
    Unit,
    walk_units,
)
from hiring_scraper.documents.text import unit_full_text

_CEFR_CODES = frozenset({'A1', 'A2', 'B1', 'B2', 'C1', 'C2'})

_BENIGN_ACRONYMS = frozenset({
    'DE', 'EN', 'EU', 'US', 'UK', 'UN', 'CH', 'AT', 'FR', 'ES', 'JP', 'MX',
    'HR', 'IT', 'PR', 'VP', 'CEO', 'CTO', 'CFO', 'COO', 'PM', 'AM', 'DR',
    'NR', 'ST', 'STR', 'PLZ', 'TEL', 'FAX', 'GMBH', 'AG', 'KG', 'OHG', 'SE',
    'INC', 'LLC', 'LTD', 'CO', 'CV', 'VS', 'IE', 'EG', 'ETC', 'CA', 'BZW',
    'USW', 'GGF', 'VGL', 'OG', 'UG', 'II', 'III', 'IV', 'VI', 'VII', 'VIII',
    'ZAB', 'BS', 'MS', 'BA', 'MA', 'PHD', 'A1', 'A2', 'B1', 'B2', 'C1', 'C2',
    'EUR', 'USD', 'CHF', 'GBP',
})

# Curated single- or multi-word capitalized tools, platforms, frameworks and credentials.
_KNOWN_TECH_TERMS: tuple[str, ...] = (
    'Power BI', 'Fusion 360', 'Six Sigma', 'Google Cloud', 'Node.js', 'Next.js', 'Vue.js',
    'Spring Boot', 'ISO 9001', 'ISO 27001', 'Prince2', 'PRINCE2', 'MS Project',
    'Python', 'Java', 'Ruby', 'Rust', 'Kotlin', 'Swift', 'Scala', 'Perl', 'Matlab', 'Simulink',
    'React', 'Angular', 'Vue', 'Svelte', 'Django', 'Flask', 'Spring', 'Laravel', 'Rails',
    'Docker', 'Kubernetes', 'Terraform', 'Ansible', 'Jenkins', 'Helm', 'Istio', 'Linux',
    'Azure', 'Figma', 'Sketch', 'InVision', 'Trello', 'Jira', 'Confluence', 'Asana',
    'Notion', 'Miro', 'Slack', 'Salesforce', 'Tableau', 'Excel', 'Looker', 'Snowflake',
    'Databricks', 'Spark', 'Kafka', 'Airflow', 'Redis', 'Elasticsearch', 'Kibana',
    'Grafana', 'Prometheus', 'Splunk', 'Datadog', 'Sentry', 'Nginx', 'Apache',
    'Scrum', 'Kanban', 'Lean', 'Photoshop', 'Illustrator', 'Premiere', 'Blender',
    'Shopify', 'Stripe', 'Zendesk', 'Intercom', 'Marketo', 'Mailchimp', 'Airtable',
    'Zapier', 'Smartsheet', 'Linear', 'Basecamp', 'Bitbucket', 'Git', 'Oracle',
    'Workday', 'Personio', 'Greenhouse', 'Ashby', 'Minitab', 'Stata', 'Qlik',
    'Alteryx', 'Webflow', 'Framer', 'Playwright', 'Cypress', 'Selenium', 'Jest',
    'Pytest', 'Pandas', 'Keras', 'Supabase', 'Firebase', 'Prisma', 'Tailwind',
    'Bootstrap', 'Webpack', 'Vite', 'Vercel', 'Netlify', 'Datev', 'Solidworks',
    'Autocad', 'Catia', 'Siemens', 'Abap', 'Hana',
)

_KNOWN_TECH_REGEXES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        term,
        re.compile(
            r'(?<![A-Za-z0-9ÄÖÜäöüß])'
            + re.escape(term)
            + r'(?![A-Za-z0-9ÄÖÜäöüß])'
        ),
    )
    for term in _KNOWN_TECH_TERMS
)

_SPECIAL_TECH_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:C\+\+|C#|\.NET|CI/CD|R&D|L&D|B2B|B2C)(?![A-Za-z0-9])'
)
_CAMEL_CASE_RE = re.compile(r'\b[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*\b')
_ACRONYM_RE = re.compile(r'(?<![A-Za-z0-9ÄÖÜäöüß])([A-Z]{2,6}[0-9]{0,4})s?(?![A-Za-z0-9ÄÖÜäöüß])')

_NUMBER_RE = re.compile(
    r'(?<![\wÄÖÜäöüß.,])'
    r'(\d+(?:[.,]\d+)*)'
    r'(?![\d.,]?\d)(?![A-Za-zÄÖÜäöüß])'
    r'(\s*(?:%|Prozent\b|percent\b))?'
)

_LANGUAGE_PATTERNS: dict[str, re.Pattern[str]] = {
    'German': re.compile(r'\b(?:german|deutsch(?:kenntnis(?:se)?)?)\b', re.I),
    'English': re.compile(r'\b(?:english|englisch(?:kenntnis(?:se)?)?)\b', re.I),
    'French': re.compile(r'\b(?:french|französisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Spanish': re.compile(r'\b(?:spanish|spanisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Italian': re.compile(r'\b(?:italian|italienisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Dutch': re.compile(r'\b(?:dutch|niederländisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Portuguese': re.compile(r'\b(?:portuguese|portugiesisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Polish': re.compile(r'\b(?:Polish|Polnisch|polnisch(?:e[nrs]?|kenntnis(?:se)?)?)\b'),
    'Russian': re.compile(r'\b(?:russian|russisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Turkish': re.compile(r'\b(?:turkish|türkisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Arabic': re.compile(r'\b(?:arabic|arabisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Chinese': re.compile(r'\b(?:chinese|mandarin|cantonese|chinesisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Japanese': re.compile(r'\b(?:japanese|japanisch(?:kenntnis(?:se)?)?)\b', re.I),
    'Korean': re.compile(r'\b(?:korean|koreanisch(?:kenntnis(?:se)?)?)\b', re.I),
}

_LANG_LEVEL_RE = re.compile(
    r'\b(A1|A2|B1|B2|C1|C2|(?<!near[- ])native(?![- ](?:like|level))(?:\s+speaker)?|'
    r'mother\s+tongue|first\s+language|muttersprache\w*|muttersprachlich\w*)\b',
    re.I,
)

_VISA_CLAIM_RE = re.compile(
    r'\b(?:'
    r'work\s+permit|work\s+authorization|authorized\s+to\s+work|eligible\s+to\s+work|'
    r'visa\s+(?:sponsorship|free|not\s+required)|no\s+(?:visa\s+)?sponsorship\s+(?:needed|required)|'
    r'unrestricted\s+work|permanent\s+residen\w*|'
    r'arbeitserlaubnis|arbeitsgenehmigung|aufenthaltstitel|aufenthaltserlaubnis|'
    r'visum(?:frei)?|kein\s+visum|unbefristete[rn]?\s+aufenthalt\w*'
    r')\b',
    re.I,
)

_SALARY_CLAIM_RE = re.compile(
    r'(?:'
    r'\b(?:salary|compensation|gehalt|gehaltsvorstellung|jahresgehalt|brutto|vergütung)\b[^.,;\n]{0,40}?\b(\d+(?:[.,]\d+)*)\b'
    r'|'
    r'\b(\d+(?:[.,]\d+)*)\s*(?:€|EUR|USD|\$|Euro)\b'
    r')',
    re.I,
)

_NOTICE_CLAIM_RE = re.compile(
    r'\b(?:'
    r'notice\s+period|available\s+(?:from|immediately|as\s+of)|can\s+start\s+(?:on|immediately)|'
    r'kündigungsfrist|verfügbar\s+ab|ab\s+sofort\s+(?:verfügbar|einsatzbereit)|eintrittstermin'
    r')\b',
    re.I,
)

_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r'\[(?:TODO|TBD|XXX|FIXME|Company|Unternehmen|Firma|Role|Position|Stelle|'
        r'Name|Date|Datum|City|Stadt|Address|Adresse|Insert[^\]]*|Your[^\]]*|'
        r'Hier[^\]]*|Platzhalter[^\]]*|\.\.\.)\]',
        re.I,
    ),
    re.compile(r'\[[A-Z][A-Z0-9 _/-]{1,30}\]'),
    re.compile(r'\{\{[^}]+\}\}'),
    re.compile(r'<(?:TODO|TBD|Company|Unternehmen|Name|Role|Position|Insert[^>]*)>', re.I),
    re.compile(r'\b(?:TODO|FIXME|TBD|Lorem\s+ipsum)\b'),
    re.compile(r'\\(?:textbf|emph|textit|item\b|Radius[A-Za-z]+)'),
)

_EN_FUNCTION_WORDS = frozenset({
    'the', 'and', 'with', 'for', 'from', 'that', 'this', 'which', 'through',
    'across', 'between', 'during', 'within', 'their', 'our', 'your', 'have',
    'has', 'were', 'was', 'been', 'being', 'would', 'could', 'should', 'while',
    'into', 'about', 'also', 'more', 'most', 'very', 'because', 'therefore',
    'however', 'including', 'ensuring', 'supporting', 'developing', 'managing',
    'leading', 'built', 'created', 'developed', 'managed', 'worked', 'responsible',
})

_DE_FUNCTION_WORDS = frozenset({
    'und', 'mit', 'für', 'fuer', 'von', 'vom', 'zur', 'zum', 'bei', 'beim',
    'durch', 'über', 'ueber', 'unter', 'zwischen', 'während', 'waehrend',
    'sowie', 'oder', 'aber', 'jedoch', 'deshalb', 'daher', 'dass', 'diese',
    'dieser', 'dieses', 'diesem', 'diesen', 'eine', 'einer', 'einem', 'einen',
    'eines', 'der', 'die', 'das', 'dem', 'den', 'des', 'ich', 'wir', 'sie',
    'ihr', 'ihre', 'ihrem', 'ihren', 'mein', 'meine', 'meinem', 'meinen',
    'habe', 'haben', 'hatte', 'wurde', 'wurden', 'werde', 'werden', 'kann',
    'können', 'koennen', 'möchte', 'moechte', 'freue', 'mich', 'sich', 'auch',
    'sehr', 'mehr', 'bereits', 'insbesondere',
})

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')


def _normalize_number(raw_digits: str, is_percent: bool) -> str:
    s = raw_digits.strip()
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):
        s = s.replace('.', '')
    elif re.fullmatch(r'\d{1,3}(?:,\d{3})+', s):
        s = s.replace(',', '')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+,\d+', s):
        s = s.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:,\d{3})+\.\d+', s):
        s = s.replace(',', '')
    elif re.fullmatch(r'\d+,\d+', s):
        s = s.replace(',', '.')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return f'{s}%' if is_percent else s


def extract_numbers(text: str) -> list[tuple[str, str]]:
    """Extract ``(raw_token, normalized_key)`` pairs for numbers, percentages and years."""
    results: list[tuple[str, str]] = []
    for match in _NUMBER_RE.finditer(text):
        digits = match.group(1)
        pct_suffix = match.group(2)
        is_pct = bool(pct_suffix)
        raw = match.group(0).strip()
        norm = _normalize_number(digits, is_pct)
        results.append((raw, norm))
    return results


def _extract_tech_tokens(text: str) -> list[str]:
    """Conservatively extract CamelCase, acronyms, credentials and known tools."""
    found: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            found.append(token)

    for match in _SPECIAL_TECH_RE.finditer(text):
        add(match.group(0))

    for term, pattern in _KNOWN_TECH_REGEXES:
        if pattern.search(text):
            add(term)

    for match in _CAMEL_CASE_RE.finditer(text):
        tok = match.group(0)
        # Skip German gender-inclusive Binnen-I forms (e.g. MitarbeiterInnen).
        if tok.endswith(('Innen', 'In')) and len(tok) > 4:
            continue
        add(tok)

    for match in _ACRONYM_RE.finditer(text):
        tok = match.group(1)
        if tok in _BENIGN_ACRONYMS:
            continue
        add(tok)

    return found


def _token_in_corpus(token: str, corpus: str) -> bool:
    escaped = re.escape(token)
    if re.search(rf'(?<![A-Za-z0-9ÄÖÜäöüß]){escaped}s?(?![A-Za-z0-9ÄÖÜäöüß])', corpus, re.I):
        return True
    # Allow compact/hyphen variants such as "Power BI" vs "PowerBI" or "CI/CD" vs "CICD".
    compact = re.sub(r'[\s\-/]+', '', token)
    if len(compact) >= 3 and compact.casefold() != token.casefold():
        if re.search(rf'(?<![A-Za-z0-9ÄÖÜäöüß]){re.escape(compact)}(?![A-Za-z0-9ÄÖÜäöüß])', corpus, re.I):
            return True
    return False


def _canonical_language(name: str) -> str | None:
    for canonical, pattern in _LANGUAGE_PATTERNS.items():
        if pattern.search(name):
            return canonical
    return None


def _normalize_level(raw: str) -> str:
    upper = raw.strip().upper()
    if upper in _CEFR_CODES:
        return upper
    return 'NATIVE'


def _extract_declared_languages(
    profile: Mapping[str, Any],
    evidence: Mapping[str, str],
) -> dict[str, str]:
    declared: dict[str, str] = {}
    raw_levels = profile.get('language_levels')
    if isinstance(raw_levels, Mapping):
        for lang, level in raw_levels.items():
            canonical = _canonical_language(str(lang))
            if canonical and str(level).strip():
                declared[canonical] = _normalize_level(str(level))
    ev_langs = evidence.get('profile:language_levels', '')
    if ev_langs:
        for chunk in re.split(r'[,;\n]+', ev_langs):
            canonical = _canonical_language(chunk)
            lvl_match = _LANG_LEVEL_RE.search(chunk)
            if canonical and lvl_match and canonical not in declared:
                declared[canonical] = _normalize_level(lvl_match.group(1))
    return declared


def _collect_entry_metadata(
    doc: CvDocument | LetterDocument,
    base_cv: CvDocument | None,
    profile: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build ``{entry_id: {company, title, start, end}}`` from available CV/profile data."""
    entries: dict[str, dict[str, Any]] = {}

    def ingest_cv(cv: CvDocument) -> None:
        for section in cv.sections:
            for entry in section.entries:
                entries[entry.id] = {
                    'company': entry.locked.company.strip(),
                    'title': entry.locked.title.strip(),
                    'start': entry.locked.start,
                    'end': entry.locked.end or '',
                }

    if base_cv is not None:
        ingest_cv(base_cv)
    if isinstance(doc, CvDocument):
        ingest_cv(doc)

    # Also check structured experiences in profile dict (useful for letter checks without base_cv).
    raw_lists: list[Any] = []
    app_details = profile.get('application_details')
    if isinstance(app_details, Mapping):
        raw_lists.append(app_details.get('experiences'))
    raw_lists.append(profile.get('experiences'))
    cv_facts = profile.get('cv_career_facts')
    if isinstance(cv_facts, Mapping):
        raw_lists.append(cv_facts.get('experiences'))
        raw_lists.append(cv_facts.get('education'))
    for rows in raw_lists:
        if not isinstance(rows, list):
            continue
        for idx, row in enumerate(rows, 1):
            if not isinstance(row, Mapping):
                continue
            eid = str(row.get('id') or f'work-{idx}').strip()
            company = str(row.get('company') or '').strip()
            if eid and company and eid not in entries:
                entries[eid] = {
                    'company': company,
                    'title': str(row.get('title') or '').strip(),
                    'start': str(row.get('start') or '').strip(),
                    'end': str(row.get('end') or '').strip(),
                }
    return entries


def _company_patterns(company: str) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    cleaned = company.strip()
    if len(cleaned) >= 3:
        patterns.append(re.compile(rf'(?<![A-Za-z0-9ÄÖÜäöüß]){re.escape(cleaned)}(?![A-Za-z0-9ÄÖÜäöüß])', re.I))
    paren = re.match(r'^(.+?)\s*\(([^()]{2,20})\)\s*$', cleaned)
    if paren:
        main_part = paren.group(1).strip()
        abbr = paren.group(2).strip()
        if len(main_part) >= 4:
            patterns.append(re.compile(rf'(?<![A-Za-z0-9ÄÖÜäöüß]){re.escape(main_part)}(?![A-Za-z0-9ÄÖÜäöüß])', re.I))
        if len(abbr) >= 3 and abbr.upper() not in _BENIGN_ACRONYMS:
            patterns.append(re.compile(rf'(?<![A-Za-z0-9ÄÖÜäöüß]){re.escape(abbr)}(?![A-Za-z0-9ÄÖÜäöüß])'))
    return patterns


def _collect_profile_skills_corpus(
    profile: Mapping[str, Any],
    evidence: Mapping[str, str],
    language: DocumentLanguage,
) -> str:
    parts: list[str] = []
    for key, value in evidence.items():
        if key.startswith('skill:') or key == 'profile:certifications':
            parts.append(value)
    for field in ('skills', 'certifications'):
        val = profile.get(field)
        if isinstance(val, list):
            parts.extend(str(item) for item in val)
    skill_ev = profile.get('skill_evidence')
    if isinstance(skill_ev, Mapping):
        for k, v in skill_ev.items():
            parts.append(str(k))
            if isinstance(v, Mapping):
                parts.append(str(v.get('note') or ''))
    localized = profile.get('localized_content')
    if isinstance(localized, Mapping):
        lang_block = localized.get(language)
        if isinstance(lang_block, Mapping):
            for field in ('skills', 'certifications'):
                val = lang_block.get(field)
                if isinstance(val, list):
                    parts.extend(str(item) for item in val)
    return '\n'.join(parts)


def _iter_units_with_context(doc: CvDocument | LetterDocument) -> list[tuple[Unit, str | None]]:
    """Return ``(unit, owning_entry_id)`` in reading order."""
    if isinstance(doc, CvDocument):
        items: list[tuple[Unit, str | None]] = [(doc.headline, None)]
        for section in doc.sections:
            for unit in section.units:
                items.append((unit, None))
            for entry in section.entries:
                for unit in entry.intro:
                    items.append((unit, entry.id))
                for unit in entry.units:
                    items.append((unit, entry.id))
        return items
    return [(unit, None) for unit in walk_units(doc)]


def _normalize_sentence(sentence: str) -> str:
    cleaned = re.sub(r'\s+', ' ', sentence).strip().strip('.,;:!?()[]"\'“”„‟')
    return cleaned.casefold()


def _split_sentences(text: str) -> list[str]:
    return [seg.strip() for seg in _SENTENCE_SPLIT_RE.split(text.strip()) if seg.strip()]


_LEGAL_FORMS = frozenset({'gmbh', 'ag', 'se', 'kg', 'inc', 'ltd'})


def check_document(
    doc: CvDocument | LetterDocument,
    evidence: Mapping[str, str],
    *,
    language: DocumentLanguage | None = None,
    profile: Mapping[str, Any] | None = None,
    base_cv: CvDocument | None = None,
    job: Mapping[str, Any] | None = None,
) -> list[Finding]:
    """Run deterministic grounding checks on AI-origin units and return findings."""
    target_lang: DocumentLanguage = language or doc.language
    prof: Mapping[str, Any] = profile or {}
    doc_kind: str = 'cv' if isinstance(doc, CvDocument) else 'letter'

    entry_meta = _collect_entry_metadata(doc, base_cv, prof)
    company_matchers = {
        eid: (meta['company'], _company_patterns(meta['company']))
        for eid, meta in entry_meta.items()
        if meta['company']
    }
    declared_languages = _extract_declared_languages(prof, evidence)
    skills_corpus = _collect_profile_skills_corpus(prof, evidence, target_lang)
    job_corpus = '\n'.join(str((job or {}).get(key) or '') for key in ('title', 'company_name', 'description'))

    app_details = prof.get('application_details') if isinstance(prof.get('application_details'), Mapping) else {}
    prefs = app_details.get('preferences') if isinstance(app_details.get('preferences'), Mapping) else {}
    loc_prefs: Mapping[str, Any] = {}
    if isinstance(prof.get('localized_content'), Mapping):
        lb = prof['localized_content'].get(target_lang)
        if isinstance(lb, Mapping) and isinstance(lb.get('application_preferences'), Mapping):
            loc_prefs = lb['application_preferences']

    findings: list[Finding] = []
    seen_sentences: set[str] = set()

    def add_finding(
        unit_id: str | None,
        code: FindingCode,
        severity: FindingSeverity,
        message: str,
        suggestion: str = '',
    ) -> None:
        findings.append(
            Finding(
                id=f'f-{len(findings) + 1}',
                document=doc_kind,  # type: ignore[arg-type]
                unit_id=unit_id,
                code=code,
                severity=severity,
                message=message[:500],
                suggestion=suggestion[:500],
            )
        )

    for unit, owning_entry_id in _iter_units_with_context(doc):
        if unit.hidden:
            continue
        full_text = unit_full_text(unit)
        sentences = _split_sentences(full_text)

        if unit.origin != 'ai':
            for sent in sentences:
                norm = _normalize_sentence(sent)
                if len(norm) >= 25 and len(norm.split()) >= 4:
                    seen_sentences.add(norm)
            continue

        # 1. Unknown source IDs
        valid_sources: list[str] = []
        for src in unit.sources:
            if src == 'job':
                continue
            if src not in evidence:
                add_finding(
                    unit.id,
                    'review_grounding',
                    'high',
                    f"Unknown evidence source ID '{src}'.",
                    'Cite only valid IDs present in the evidence pack.',
                )
            else:
                valid_sources.append(src)

        cited_texts = [evidence[src] for src in valid_sources]
        cited_corpus = '\n'.join(cited_texts)

        cited_entry_ids: set[str] = set()
        for src in valid_sources:
            parts = src.split(':')
            if len(parts) == 3 and parts[0] == 'cv' and parts[2][:1] in ('b', 'i'):
                cited_entry_ids.add(parts[1])

        # Augment entry context text for cited entries (locked company/title/dates).
        cited_entry_header_parts: list[str] = []
        date_source_ids = set(cited_entry_ids)
        if owning_entry_id:
            date_source_ids.add(owning_entry_id)
        for eid in date_source_ids:
            meta = entry_meta.get(eid)
            if meta:
                cited_entry_header_parts.append(
                    f"{meta['company']} {meta['title']} {meta['start']} {meta['end']}"
                )
        cited_with_headers = '\n'.join([cited_corpus, *cited_entry_header_parts])

        # 2. Placeholders
        for pat in _PLACEHOLDER_PATTERNS:
            match = pat.search(full_text)
            if match:
                add_finding(
                    unit.id,
                    'placeholder',
                    'high',
                    f"Unresolved placeholder or markup '{match.group(0)}' found.",
                    'Replace placeholders with verified plain text.',
                )
                break

        # 3. Numbers / percentages / years
        number_corpus = cited_with_headers
        if (isinstance(doc, LetterDocument) and unit.id == doc.subject.id) or (
            'job' in unit.sources and not valid_sources
        ):
            number_corpus = f'{number_corpus}\n{job_corpus}'
        allowed_numbers = {norm for _, norm in extract_numbers(number_corpus)}
        flagged_nums: set[str] = set()
        for raw_num, norm_num in extract_numbers(full_text):
            if norm_num in allowed_numbers or norm_num in flagged_nums:
                continue
            # Also accept bare year if a YYYY-MM date with that year is in cited headers/sources.
            if len(norm_num) == 4 and norm_num.isdigit() and re.search(rf'\b{re.escape(norm_num)}\b', number_corpus):
                continue
            flagged_nums.add(norm_num)
            add_finding(
                unit.id,
                'unsupported_number',
                'high',
                f"Number '{raw_num}' does not appear in cited sources.",
                'Verify this figure against the cited evidence or remove it.',
            )

        # 4. Capitalized tool / tech / acronym / CamelCase / credential tokens
        term_corpus = '\n'.join([cited_with_headers, skills_corpus])
        if 'job' in unit.sources or unit.role in ('motivation', 'bring', 'close'):
            term_corpus = f'{term_corpus}\n{job_corpus}'
        tech_severity: FindingSeverity = (
            'info' if doc_kind == 'letter' and unit.role in ('motivation', 'bring') else 'medium'
        )
        for token in _extract_tech_tokens(full_text):
            if token.casefold() not in _LEGAL_FORMS and not _token_in_corpus(token, term_corpus):
                add_finding(
                    unit.id,
                    'unsupported_term',
                    tech_severity,
                    f"Tool, acronym or credential '{token}' is not supported by the evidence or applicable job context.",
                    'Cite a supporting source or remove unverified technical claims.',
                )

        # 5. Employer / company attribution
        if owning_entry_id:
            for cited_eid in cited_entry_ids:
                if cited_eid != owning_entry_id:
                    add_finding(
                        unit.id,
                        'misattributed',
                        'high',
                        f"Unit under entry '{owning_entry_id}' cites source from different entry '{cited_eid}'.",
                        f"Use only evidence belonging to '{owning_entry_id}'.",
                    )
        cited_employers = {employer_key(entry_meta.get(eid, {}).get('company', ''), eid)
                           for eid in cited_entry_ids}
        owning_company = employer_key(entry_meta.get(owning_entry_id, {}).get('company', ''), owning_entry_id or '')
        for eid, (company_name, patterns) in company_matchers.items():
            if not any(p.search(full_text) for p in patterns):
                continue
            company = employer_key(company_name, eid)
            if owning_entry_id:
                if company == owning_company:
                    continue
                add_finding(
                    unit.id,
                    'misattributed',
                    'high',
                    f"Employer '{company_name}' mentioned under entry '{owning_entry_id}'.",
                    'Attribute each accomplishment to the correct employer entry.',
                )
            else:
                if company in cited_employers:
                    continue
                if any(p.search(cited_corpus) for p in patterns):
                    continue
                add_finding(
                    unit.id,
                    'misattributed',
                    'high',
                    f"Employer '{company_name}' ({eid}) is mentioned without citing an entry source for '{eid}'.",
                    f"Cite a source from '{eid}' or verify employer attribution.",
                )

        # 6. Sensitive facts (language levels, visa/work permit, salary, notice period)
        _check_sensitive_facts(
            full_text=full_text,
            unit_id=unit.id,
            declared_languages=declared_languages,
            prefs=prefs,
            loc_prefs=loc_prefs,
            cited_corpus=cited_corpus,
            add_finding=add_finding,
        )

        # 7. Obvious language mix
        _check_language_mix(
            full_text=full_text,
            unit_id=unit.id,
            target_lang=target_lang,
            add_finding=add_finding,
        )

        # 8. Exact repeated sentences
        for sent in sentences:
            norm = _normalize_sentence(sent)
            if len(norm) < 25 or len(norm.split()) < 4:
                continue
            if norm in seen_sentences:
                snippet = sent if len(sent) <= 90 else sent[:87] + '...'
                add_finding(
                    unit.id,
                    'repetition',
                    'medium',
                    f"Repeated sentence: '{snippet}'.",
                    'Remove or rephrase duplicate sentences.',
                )
            else:
                seen_sentences.add(norm)

    return findings


def _check_sensitive_facts(
    *,
    full_text: str,
    unit_id: str,
    declared_languages: dict[str, str],
    prefs: Mapping[str, Any],
    loc_prefs: Mapping[str, Any],
    cited_corpus: str,
    add_finding: Any,
) -> None:
    # 6a. Language levels (paired language + level, plus standalone CEFR codes)
    lang_hits = sorted(
        ((lang, m) for lang, pat in _LANGUAGE_PATTERNS.items() for m in pat.finditer(full_text)),
        key=lambda item: item[1].start(),
    )
    matched_cefr_spans: list[tuple[int, int]] = []
    for idx, (lang, match) in enumerate(lang_hits):
        next_start = lang_hits[idx + 1][1].start() if idx + 1 < len(lang_hits) else len(full_text)
        prev_end = lang_hits[idx - 1][1].end() if idx > 0 else 0
        after = full_text[match.end():min(next_start, match.end() + 40)]
        before = full_text[max(prev_end, match.start() - 30):match.start()]

        # Stop search at clause boundaries
        after_clause = re.split(r'[.;\n]', after, maxsplit=1)[0]
        before_clause = re.split(r'[.;\n]', before)[-1]
        lvl = _LANG_LEVEL_RE.search(after_clause)
        if lvl:
            span_start = match.end() + lvl.start()
            span_end = match.end() + lvl.end()
        else:
            lvl = _LANG_LEVEL_RE.search(before_clause)
            if lvl:
                offset = match.start() - len(before_clause)
                span_start = offset + lvl.start()
                span_end = offset + lvl.end()
            else:
                continue
        matched_cefr_spans.append((span_start, span_end))
        claimed = _normalize_level(lvl.group(1))
        declared = declared_languages.get(lang)
        if declared != claimed:
            add_finding(
                unit_id,
                'sensitive_fact',
                'high',
                f"Language level claim '{lang} {claimed}' does not match profile ({declared or 'undeclared'}).",
                'Use only language levels saved in the profile.',
            )

    for cefr_match in re.finditer(r'\b(A1|A2|B1|B2|C1|C2)\b', full_text):
        if any(s <= cefr_match.start() < e for s, e in matched_cefr_spans):
            continue
        code = cefr_match.group(1)
        if code not in declared_languages.values() and not re.search(rf'\b{code}\b', cited_corpus):
            add_finding(
                unit_id,
                'sensitive_fact',
                'high',
                f"CEFR level '{code}' does not match any language level in the profile.",
                'Verify language proficiency against the profile.',
            )

    # 6b. Visa / work authorization
    visa_match = _VISA_CLAIM_RE.search(full_text)
    if visa_match:
        authorized = prefs.get('authorized_to_work')
        sponsorship_req = prefs.get('sponsorship_required')
        claim_lower = visa_match.group(0).casefold()
        no_sponsor_claim = 'no ' in claim_lower or 'not required' in claim_lower or 'kein' in claim_lower or 'frei' in claim_lower
        valid = False
        if no_sponsor_claim:
            valid = sponsorship_req is False or authorized is True
        else:
            valid = authorized is True
        if not valid:
            add_finding(
                unit_id,
                'sensitive_fact',
                'high',
                f"Work authorization/visa claim '{visa_match.group(0)}' is not confirmed in profile preferences.",
                'State work authorization only when explicitly saved in profile preferences.',
            )

    # 6c. Salary
    salary_match = _SALARY_CLAIM_RE.search(full_text)
    if salary_match:
        raw_amount = salary_match.group(1) or salary_match.group(2) or ''
        claimed_num = _normalize_number(raw_amount, False)
        saved_salary = str(prefs.get('salary_amount') or '').strip()
        saved_num = _normalize_number(saved_salary, False) if saved_salary else ''
        if not saved_num or claimed_num != saved_num:
            add_finding(
                unit_id,
                'sensitive_fact',
                'high',
                f"Salary figure '{raw_amount}' does not match saved profile salary ({saved_salary or 'undeclared'}).",
                'Match salary expectations to the saved profile preference or omit them.',
            )

    # 6d. Notice period / availability
    notice_match = _NOTICE_CLAIM_RE.search(full_text)
    if notice_match:
        saved_notice = str(loc_prefs.get('notice_period') or prefs.get('notice_period') or '').strip()
        saved_available = str(prefs.get('available_from') or '').strip()
        if not saved_notice and not saved_available:
            add_finding(
                unit_id,
                'sensitive_fact',
                'high',
                f"Notice period / availability claim '{notice_match.group(0)}' is not saved in profile preferences.",
                'State notice period or availability only when saved in profile preferences.',
            )
        else:
            saved_combined = f'{saved_notice} {saved_available}'.casefold()
            # Check for explicit date or numeric duration conflict near the mention
            window = full_text[max(0, notice_match.start() - 25):min(len(full_text), notice_match.end() + 45)]
            iso_dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', window)
            if iso_dates and saved_available and any(d != saved_available for d in iso_dates):
                add_finding(
                    unit_id,
                    'sensitive_fact',
                    'high',
                    f"Availability date '{iso_dates[0]}' does not match profile ({saved_available}).",
                    'Use the exact availability date saved in the profile.',
                )
            dur_match = re.search(
                r'\b(\d+)\s*(?:weeks?|months?|days?|wochen?|monate?n?|tage?n?)\b',
                window,
                re.I,
            )
            if dur_match:
                num_str = dur_match.group(1)
                if num_str not in re.findall(r'\d+', saved_notice):
                    add_finding(
                        unit_id,
                        'sensitive_fact',
                        'high',
                        f"Notice period '{dur_match.group(0)}' conflicts with saved profile ({saved_notice or saved_available}).",
                        'Match notice period to the saved profile preference.',
                    )


def _check_language_mix(
    *,
    full_text: str,
    unit_id: str,
    target_lang: DocumentLanguage,
    add_finding: Any,
) -> None:
    # Strip quoted titles (e.g. English paper title cited in a German CV).
    unquoted = re.sub(r'[“"„][^”"‟]{1,200}[”"‟]', ' ', full_text)
    for sent in _split_sentences(unquoted):
        words = [w.casefold() for w in re.findall(r'[A-Za-zÄÖÜäöüß]{2,}', sent)]
        if len(words) < 6:
            continue
        en_hits = sum(1 for w in words if w in _EN_FUNCTION_WORDS)
        de_hits = sum(1 for w in words if w in _DE_FUNCTION_WORDS)
        if target_lang == 'de' and en_hits >= 3 and en_hits >= 2 * max(de_hits, 1):
            add_finding(
                unit_id,
                'language_mix',
                'medium',
                'Sentence appears to be written in English inside a German document.',
                'Rewrite this sentence in German.',
            )
            return
        if target_lang == 'en' and de_hits >= 3 and de_hits >= 2 * max(en_hits, 1):
            add_finding(
                unit_id,
                'language_mix',
                'medium',
                'Sentence appears to be written in German inside an English document.',
                'Rewrite this sentence in English.',
            )
            return
