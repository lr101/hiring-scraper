"""Prepare and revise structured application documents with resumable model stages."""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from html import unescape
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hiring_scraper.app.application_pdf import PAGE_LIMITS, RenderResult
from hiring_scraper.documents import editorial
from hiring_scraper.documents.evidence import build_evidence, entry_bullets
from hiring_scraper.documents.grounding import check_document
from hiring_scraper.documents.schema import (
    MAX_SECTION_UNITS,
    MAX_UNIT_TEXT,
    CvDocument,
    DocumentLanguage,
    Entry,
    Finding,
    FindingCode,
    FindingSeverity,
    LetterDocument,
    Unit,
    UnitRole,
    apply_patch,
    unit_by_id,
    walk_units,
)
from hiring_scraper.documents.text import unit_full_text

PIPELINE_VERSION = 'application-documents-v2.8'
SLEEP = time.sleep
PROMPTS = Path(__file__).with_name('prompts')
DocumentKind = Literal['cv', 'letter']
Document = CvDocument | LetterDocument
Generate = Callable[[str, dict], dict]

# Editorial signals feed review. Only the constraints below can reject prose.
_BLOCKING_PROSE_CODES = frozenset({
    'cv_headline_too_long',
    'cv_profile_first_person',
    'letter_duplicate_employer',
    'letter_cv_ngram_overlap',
    'letter_contraction',
})


class GenerationError(Exception):
    def __init__(self, code: str, message: str = '', retryable: bool = False, reasons: tuple[str, ...] = ()):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable
        self.reasons = reasons


@dataclass
class StageHooks:
    load: Callable[[str], dict | None]
    commit: Callable[[str, dict | None, list[tuple[DocumentKind, dict, list[dict]]]], None]
    stage: Callable[[str], None]
    cancelled: Callable[[], bool]


@dataclass
class PreparationInput:
    base_cv: dict
    profile: dict
    job: dict
    language: DocumentLanguage
    mode: Literal['full', 'fast'] = 'full'
    today: date = field(default_factory=date.today)


@dataclass
class PreparationResult:
    cv: dict
    letter: dict
    findings: list[dict]
    telemetry: dict
    stages: dict


class _Response(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class _Text(_Response):
    text: str


class _SourcedText(_Text):
    sources: list[str]


class _Bullet(_SourcedText):
    lead_in: str


class _EntryBullets(_Response):
    entry_id: str
    bullets: list[_Bullet]


class _Skills(_Response):
    section_id: str
    unit_ids: list[str]
    hidden_ids: list[str]
    additions: list[_SourcedText]


class _CvResponse(_Response):
    headline: _SourcedText
    profile: list[_SourcedText]
    entries: list[_EntryBullets]
    skills: list[_Skills]


class _Paragraph(_SourcedText):
    role: Literal['motivation', 'experience', 'education', 'bring']


class _LetterResponse(_Response):
    subject: _Text
    contact_name: str
    paragraphs: list[_Paragraph]
    closing: _Text


class _ReviewFinding(_Response):
    document: DocumentKind
    unit_id: str | None
    category: Literal['grounding', 'coverage', 'style', 'format']
    severity: FindingSeverity
    message: str
    suggestion: str


class _ReviewPatch(_Text):
    document: DocumentKind
    unit_id: str
    kind: Literal['rephrase', 'shorten', 'grounding', 'content']


class _ReviewEnvelope(_Response):
    findings: list
    patches: list


class _ReviewResponse(_Response):
    findings: list[_ReviewFinding]
    patches: list[_ReviewPatch]


@dataclass(frozen=True)
class _ProseIssue:
    code: str
    retry_message: str
    finding_code: FindingCode
    finding_message: str
    unit_id: str | None = None
    severity: FindingSeverity = 'medium'


@dataclass(frozen=True)
class _Fallback:
    candidate: object
    issues: tuple[_ProseIssue, ...]


class _InvalidResponse(ValueError):
    """A fixed validation message safe to include in a retry prompt and telemetry."""

    def __init__(
        self,
        message: str,
        *,
        code: str = 'invalid_response',
        codes: tuple[str, ...] = (),
        candidate=None,
        issues: tuple[_ProseIssue, ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.codes = codes or (code,)
        self.candidate = candidate
        self.issues = issues


class _Checkpoint(_Response):
    findings: list[Finding]
    render: dict
    attempts: int
    seconds: float


class _CvCheckpoint(_Checkpoint):
    document: CvDocument
    draft_findings: list[Finding]
    fallback: bool = False


class _LetterCheckpoint(_Checkpoint):
    document: LetterDocument
    cv_digest: str
    non_prose_findings: list[Finding] = Field(default_factory=list)


class _ReviewCheckpoint(_Checkpoint):
    cv: CvDocument
    letter: LetterDocument
    upstream_digest: str
    applied_patches: list[dict]
    malformed_items: int


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _input_digest(inp: PreparationInput, base: CvDocument, evidence: dict) -> str:
    return _digest({
        'version': PIPELINE_VERSION,
        'base_cv': base.model_dump(),
        'evidence': evidence,
        'voice': _voice(inp),
        'grounding_profile': {key: inp.profile.get(key) for key in (
            'language_levels', 'application_details', 'experiences', 'cv_career_facts',
            'skills', 'certifications', 'skill_evidence', 'localized_content',
        )},
        'job': inp.job,
        'language': inp.language,
    })


def input_digest(inp: PreparationInput) -> str:
    base = CvDocument.model_validate(inp.base_cv)
    return _input_digest(inp, base, build_evidence(base, inp.profile))


@cache
def _instructions(stage: str) -> str:
    return (PROMPTS / 'common.txt').read_text(encoding='utf-8').rstrip() + '\n\n' + (
        PROMPTS / f'{stage}.txt'
    ).read_text(encoding='utf-8').rstrip()


def _voice(inp: PreparationInput) -> dict:
    settings = inp.profile.get('application_personalization')
    if not isinstance(settings, dict):
        return {}
    voice = {}
    notes = settings.get('voice_notes')
    if isinstance(notes, str) and notes.strip():
        voice['notes'] = notes[:1500]
    avoid = settings.get('wording_to_avoid')
    if isinstance(avoid, (str, list)) and avoid:
        voice['wording_to_avoid'] = avoid
    examples = settings.get('style_exemplars')
    if isinstance(examples, list):
        voice['exemplars'] = [
            {'kind': item.get('kind'), 'text': item['text']}
            for item in examples
            if isinstance(item, dict) and item.get('language') == inp.language
            and isinstance(item.get('text'), str) and item['text'].strip()
        ][:3]
    return voice


def _prompt(stage: str, inp: PreparationInput, evidence: dict, **blocks) -> str:
    language = {'en': 'English', 'de': 'German'}[inp.language]
    job = {key: inp.job.get(key) or '' for key in ('title', 'company_name', 'location', 'description')}
    job['description'] = str(job['description'])[:12000]
    return '\n\n'.join([
        _instructions(stage),
        f'Target language: {language} ({inp.language})',
        'VOICE (tone only, never facts)\n```json\n' + _json(_voice(inp)) + '\n```',
        'EVIDENCE\n' + _json(evidence),
        'JOB\n' + _json(job),
        *(f'{name.replace("_", " ").upper()}\n{_json(value)}' for name, value in blocks.items()),
    ])


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise GenerationError('cancelled')


def _backoff(seconds: float, cancelled: Callable[[], bool]) -> None:
    remaining = seconds * random.uniform(0.8, 1.2)
    while remaining > 0:
        _check_cancelled(cancelled)
        interval = min(remaining, 0.5)
        SLEEP(interval)
        remaining -= interval


def _generate(
    generate: Generate, prompt: str, response: type[_Response], accept: Callable,
    stats: dict, cancelled: Callable[[], bool],
    *,
    return_best: bool = False,
):
    schema = response.model_json_schema()
    retry_prompt = prompt
    feedback: list[str] = []
    failures: list[dict] = []
    best_candidate = None
    best_issues: tuple[_ProseIssue, ...] = ()
    for attempt in range(1, 4):
        _check_cancelled(cancelled)
        stats['attempts'] = attempt
        try:
            output = generate(retry_prompt, schema)
            _check_cancelled(cancelled)
            return accept(output)
        except ValidationError:
            reason_codes = ('schema_invalid',)
            error_code = 'invalid_response'
            message = 'Return a complete object matching the schema, with valid plain text and source ids.'
            failure = GenerationError('invalid_response', message, retryable=True, reasons=reason_codes)
        except _InvalidResponse as error:
            reason_codes = error.codes
            error_code = 'invalid_response'
            message = str(error)
            failure = GenerationError('invalid_response', message, retryable=True, reasons=reason_codes)
            if error.candidate is not None and (best_candidate is None or len(error.issues) < len(best_issues)):
                best_candidate = error.candidate
                best_issues = error.issues
        except GenerationError as error:
            if not error.retryable:
                failures.append({'attempt': attempt, 'code': error.code, 'reasons': [error.code]})
                stats['attempt_failures'] = failures
                raise
            reason_codes = (error.code,)
            error_code = error.code
            failure = error
            message = ('Return a complete JSON object matching the schema.'
                       if error.code == 'invalid_json' else '')
        failures.append({'attempt': attempt, 'code': error_code, 'reasons': list(reason_codes)})
        stats['attempt_failures'] = failures
        if attempt == 3:
            if return_best and best_candidate is not None:
                stats['fallback_reasons'] = sorted({issue.code for issue in best_issues})
                return _Fallback(best_candidate, best_issues)
            raise failure from None
        if message and message not in feedback:
            feedback.append(message)
            retry_prompt = (
                prompt + '\n\nYour previous answer was rejected: resolve these validation issues.\n- '
                + '\n- '.join(feedback)
            )
        _backoff((5, 20)[attempt - 1], cancelled)


def _plain(text: str, limit: int = MAX_UNIT_TEXT) -> str:
    text = ' '.join(text.split())
    if not text or len(text) > limit:
        raise _InvalidResponse(f'Each rewritten text must contain 1 to {limit} characters.', code='unit_text_length')
    return text


def _ai(unit_id: str, text: _Text, *, role: UnitRole = '', hidden: bool = False) -> Unit:
    return Unit(
        id=unit_id, text=_plain(text.text), origin='ai', role=role, hidden=hidden,
        lead_in=' '.join(text.lead_in.split()) if isinstance(text, _Bullet) else '',
        sources=text.sources if isinstance(text, _SourcedText) else [],
    )


def _finding(
    kind: DocumentKind, code: FindingCode, message: str, *, severity: FindingSeverity = 'info',
    unit_id: str | None = None, suggestion: str = '',
) -> Finding:
    return Finding(id='f-draft', document=kind, unit_id=unit_id, code=code, severity=severity,
                   message=message[:500], suggestion=suggestion[:500])


def _issues_to_findings(kind: DocumentKind, issues: tuple[_ProseIssue, ...] | list[_ProseIssue]) -> list[Finding]:
    return [
        _finding(kind, issue.finding_code, issue.finding_message, severity=issue.severity, unit_id=issue.unit_id)
        for issue in issues
    ]


def _cv_entries(base: CvDocument) -> list[dict]:
    entries = []
    for section in base.sections:
        for entry in section.entries:
            intro = editorial.study_project_intro(entry)
            bank = [source for source, unit in entry_bullets(entry) if unit is not intro]
            if bank:
                entries.append({'entry_id': entry.id, 'kind': entry.kind, **entry.locked.model_dump(),
                                'base_count': len(bank), 'bullet_bank': bank})
    return entries


def _mentions(text: str, name: str) -> bool:
    # Treat C, C++ and C# as distinct names and avoid substring matches.
    return bool(re.search(rf'(?<![\w+#]){re.escape(name)}(?![\w+#])', text, re.I))


def _bullet_coverage(base: Entry, entry: Entry) -> tuple[dict[str, Unit], list[Unit]]:
    """Assign each output bullet to its primary cited base bullet and exact duplicates."""
    caption = editorial.study_project_intro(base)
    promoted = caption is not None and any(unit.id == caption.id and not unit.hidden for unit in entry.intro)
    base_bullets = {source: unit for source, unit in entry_bullets(base) if not (promoted and unit is caption)}
    missing = base_bullets.copy()
    kept = []
    for unit in entry.units:
        if unit.hidden or not unit_full_text(unit):
            kept.append(unit)
            continue
        primary = next((source for source in unit.sources if source in base_bullets), None)
        if primary is None:
            continue
        original = base_bullets[primary]
        base_text = unit_full_text(original)
        if original.lead_in.strip() and not unit.lead_in.strip():
            continue
        covered = [source for source in unit.sources if source in missing
                   and unit_full_text(missing[source]).casefold() == base_text.casefold()]
        if covered:
            kept.append(unit)
            for source in covered:
                missing.pop(source, None)
    return missing, kept


def _check_cv_coverage(doc: CvDocument, base: CvDocument, *, restore: bool = False) -> list[_ProseIssue]:
    originals = {entry.id: entry for section in base.sections for entry in section.entries}
    issues = []
    for section in doc.sections:
        for entry in section.entries:
            missing, kept = _bullet_coverage(originals[entry.id], entry)
            if restore and (missing or len(kept) != len(entry.units)):
                entry.units = kept + [
                    unit.model_copy(update={'origin': 'source', 'sources': [source]})
                    for source, unit in missing.items()
                ]
            if missing:
                issues.append(_ProseIssue(
                    'cv_missing_base_bullet',
                    f'Entry {entry.id}: give each of these sources a distinct visible bullet with its citation first, '
                    f'keeping any project lead-in: {", ".join(missing)}.',
                    'review_coverage',
                    'Restored base bullets whose rewrites lacked a distinct citation or project lead-in.',
                    unit_id=next(iter(missing.values())).id,
                ))
            elif not restore and len(kept) != len(entry.units):
                issues.append(_ProseIssue(
                    'cv_unmatched_bullet',
                    f'Entry {entry.id}: use one bullet per base source and cite the corresponding source first.',
                    'review_coverage',
                    'Removed extra rewrites that did not cover a distinct base bullet.',
                ))
    return issues


def _show_named_skills(doc: CvDocument) -> None:
    visible_bullets = [
        unit_full_text(unit) for section in doc.sections for entry in section.entries
        for unit in entry.units if not unit.hidden
    ]
    visible_bullets.extend(
        unit_full_text(unit) for section in doc.sections
        if section.layout == 'bullets' and section.kind not in ('skills', 'profile')
        for unit in section.units if not unit.hidden
    )
    text = '\n'.join(visible_bullets)
    for section in doc.sections:
        if section.kind == 'skills':
            for unit in section.units:
                if not unit.hidden:
                    continue
                name = re.sub(r'\s+\((?:Basics|Grundlagen)\)$', '', unit_full_text(unit), flags=re.I)
                if name and _mentions(text, name):
                    unit.hidden = False


def _cv_draft(
    output: dict, base: CvDocument, entries: list[dict], evidence: dict[str, str], posting: str,
) -> tuple[CvDocument, list[Finding]]:
    reply = _CvResponse.model_validate(output)
    entry_ids = {entry['entry_id'] for entry in entries}
    findings: list[Finding] = []
    issues: list[_ProseIssue] = []

    def unknown(message: str) -> None:
        issues.append(_ProseIssue('cv_unknown_id', message, 'review_format', message))

    if len(reply.profile) != 1:
        issues.append(_ProseIssue(
            'cv_profile_count',
            f'Profile returned {len(reply.profile)} units; return exactly one profile unit integrating the strongest evidence.',
            'review_format',
            'The CV profile should contain one summary unit.',
        ))
    if not reply.headline.sources:
        issues.append(_ProseIssue(
            'cv_missing_sources',
            'Headline is missing evidence ids; cite the evidence supporting the headline.',
            'review_grounding',
            'The rewritten CV headline lacked evidence source citations.',
            unit_id=base.headline.id,
        ))
    if any(not unit.sources for unit in reply.profile):
        issues.append(_ProseIssue(
            'cv_missing_sources',
            'One or more profile units are missing evidence ids; cite the evidence supporting each profile unit.',
            'review_grounding',
            'A rewritten CV profile unit lacked evidence source citations.',
        ))

    base_entries = {entry.id: entry for section in base.sections for entry in section.entries}
    edits = {}
    for proposed in reply.entries:
        entry_id = proposed.entry_id
        if entry_id not in entry_ids:
            unknown('The CV response referred to an entry outside the supplied bullet banks.')
            continue
        if entry_id in edits:
            raise _InvalidResponse(
                f'Entry {entry_id} appeared more than once; return each listed entry exactly once.',
                code='cv_duplicate_entry',
            )
        if any(not bullet.sources for bullet in proposed.bullets):
            issues.append(_ProseIssue(
                'cv_missing_sources',
                f'Entry {entry_id} has a bullet without evidence ids; cite evidence supporting every rewritten CV bullet.',
                'review_grounding',
                f'A rewritten bullet in {entry_id} lacked evidence source citations.',
            ))
        base_entry = base_entries.get(entry_id)
        base_visible = [u for u in base_entry.units if not u.hidden] if base_entry else []
        all_base_labelled = bool(base_visible) and all(u.lead_in.strip() for u in base_visible)
        if any(
            previous.lead_in.strip()
            and not bullet.lead_in.strip()
            and (all_base_labelled or bool(set(previous.sources) & set(bullet.sources)))
            for previous, bullet in zip(proposed.bullets, proposed.bullets[1:], strict=False)
        ):
            issues.append(_ProseIssue(
                'cv_split_project',
                f'Entry {entry_id}: keep each labelled project in one bullet with its lead-in and merge unlabelled continuations.',
                'review_format',
                f'Entry {entry_id} splits a labelled project across an unlabelled continuation bullet.',
            ))
        edits[entry_id] = proposed.bullets

    skill_sections = {s.id for s in base.sections if s.kind == 'skills'}
    skills = {}
    if sum(len(section.additions) for section in reply.skills) > 4:
        issues.append(_ProseIssue(
            'cv_skills_additions_limit',
            'Add at most four evidence-backed competencies across all skills sections.',
            'review_format',
            'The CV proposed more than four new competency items.',
        ))
    for proposed in reply.skills:
        if proposed.section_id not in skill_sections:
            unknown('The CV response referred to an unknown skills section.')
        elif proposed.section_id in skills:
            raise _InvalidResponse(
                f'Skills section {proposed.section_id} appeared more than once; return each skills section at most once.',
                code='cv_duplicate_skill_section',
            )
        else:
            skills[proposed.section_id] = proposed

    doc = _new_cv(base)
    used_ids = {unit.id for unit in walk_units(doc)}

    def new_id(stem: str) -> str:
        candidate = stem
        suffix = 1
        while candidate in used_ids:
            suffix += 1
            candidate = f'{stem}-{suffix}'
        used_ids.add(candidate)
        return candidate

    doc.headline = _ai(doc.headline.id, reply.headline, hidden=doc.headline.hidden)
    profile = next((s for s in doc.sections if s.kind == 'profile'), None)
    if profile is not None:
        profile.units = [
            *(_ai(new_id(f'u-ai-profile-{i}'), unit) for i, unit in enumerate(reply.profile[:1], 1)),
            *(unit for unit in profile.units if unit.hidden),
        ]

    for section in doc.sections:
        for entry in section.entries:
            if entry.id in edits:
                slug = re.sub('[^a-z0-9-]', '-', entry.id.lower())[:40]
                entry.units = [
                    *(_ai(new_id(f'u-ai-{slug}-b{i}'), bullet)
                      for i, bullet in enumerate(edits[entry.id], 1)),
                    *(unit for unit in entry.units if unit.hidden),
                ]
        if section.id in skills:
            proposed = skills[section.id]
            by_id = {unit.id: unit for unit in section.units}
            order = dict.fromkeys(proposed.unit_ids + list(by_id))
            hidden = set(proposed.hidden_ids)
            for _ in dict.fromkeys(key for key in proposed.unit_ids + proposed.hidden_ids if key not in by_id):
                unknown('The CV response referred to an unknown skill unit.')
            section.units = [by_id[key] for key in order if key in by_id]
            for unit in section.units:
                unit.hidden = unit.hidden or unit.id in hidden
            additions = []
            existing = {unit.text.strip().casefold() for unit in section.units}
            for item in proposed.additions:
                unit = _ai(new_id('u-ai-competency'), item)
                if unit.text.casefold() in existing or len(section.units) + len(additions) >= MAX_SECTION_UNITS:
                    continue
                if len(editorial.words(unit.text)) > 4:
                    issues.append(_ProseIssue(
                        'cv_competency_too_long',
                        'An added competency has more than four words; keep each added competency to a short 1–4 word tag.',
                        'review_style',
                        'An added competency exceeded four words instead of using a short tag.',
                    ))
                    continue
                if not _supported_competency(unit, evidence, posting):
                    findings.append(_finding('cv', 'review_grounding',
                                             'An added competency lacked support from its cited evidence and the posting.'))
                    continue
                additions.append(unit)
                existing.add(unit.text.casefold())
            section.units = additions + section.units
            editorial.normalize_skills(section, preserve_order=True)
    _show_named_skills(doc)
    issues.extend(_check_cv_coverage(doc, base))
    doc = CvDocument.model_validate(doc.model_dump())
    issues.extend(issue for issue in _cv_prose_issues(doc) if issue.code in _BLOCKING_PROSE_CODES)
    if issues:
        codes = tuple(dict.fromkeys(issue.code for issue in issues))
        message = ' '.join(dict.fromkeys(issue.retry_message for issue in issues))
        raise _InvalidResponse(
            message, code=codes[0], codes=codes, candidate=(doc, findings), issues=tuple(issues),
        )
    return doc, findings


def _new_cv(base: CvDocument) -> CvDocument:
    doc = base.model_copy(deep=True)
    doc.page = 'a4'
    doc.photo = True
    for section in doc.sections:
        if section.kind == 'skills':
            editorial.normalize_skills(section)
        for entry in section.entries:
            caption = editorial.study_project_intro(entry)
            if caption is not None:
                caption.sources = [source for source, unit in entry_bullets(entry) if unit is caption]
            editorial.normalize_internship(entry, doc.language)
            editorial.normalize_study_entry(entry)
    return doc


def _supported_competency(unit: Unit, evidence: dict[str, str], posting: str) -> bool:
    return (bool(unit.sources) and all(key in evidence for key in unit.sources)
            and editorial.supports_competency(unit.text, (evidence[key] for key in unit.sources), posting))


def _cv_prose_issues(doc: CvDocument) -> list[_ProseIssue]:
    issues: list[_ProseIssue] = []
    if not doc.headline.hidden and doc.headline.origin == 'ai':
        if len(doc.headline.text) > 70:
            issues.append(_ProseIssue(
                'cv_headline_too_long',
                f'CV headline is {len(doc.headline.text)} characters; keep the CV headline to one line of at most 70 characters.',
                'review_format',
                'The CV headline exceeds 70 characters and may wrap onto a second line.',
                unit_id=doc.headline.id,
            ))
    bullets = [
        unit.text
        for section in doc.sections for entry in section.entries for unit in entry.units if not unit.hidden
    ]
    for section in doc.sections:
        if section.kind == 'profile':
            for idx, unit in enumerate(section.units, 1):
                if unit.hidden or unit.origin != 'ai':
                    continue
                if editorial.has_first_person(unit.text):
                    issues.append(_ProseIssue(
                        'cv_profile_first_person',
                        f'Profile unit {idx} uses explicit first person; write a noun-phrase-led CV profile without I or ich.',
                        'review_style',
                        'The CV profile uses explicit first person instead of a noun-phrase-led summary.',
                        unit_id=unit.id,
                    ))
                voice_error = editorial.profile_voice_issue(unit.text, doc.language)
                if voice_error:
                    issues.append(_ProseIssue(
                        'cv_profile_voice',
                        f'Profile unit {idx} opens a sentence with a bare verb or imperative; start the profile with a noun phrase naming the professional identity from the evidence, and never start sentences with imperatives or conjugated first-person verbs.',
                        'review_style',
                        'The CV profile uses verb-first or imperative phrasing instead of a noun-phrase-led summary.',
                        unit_id=unit.id,
                    ))
                if editorial.repeated_profile_term(unit.text):
                    issues.append(_ProseIssue(
                        'cv_profile_repetition',
                        f'Profile unit {idx} uses a content word at least three times; use precise, varied wording.',
                        'review_style',
                        'The CV profile repeats a content word at least three times.',
                        unit_id=unit.id,
                    ))
                if any(editorial.near_duplicate(sentence, bullet)
                       for sentence in editorial.sentences(unit.text) for bullet in bullets):
                    issues.append(_ProseIssue(
                        'cv_profile_duplicate',
                        f'Profile unit {idx} repeats a CV bullet; synthesize the strongest evidence in the CV profile without repeating a bullet.',
                        'review_style',
                        'The CV profile closely repeats an experience or project bullet.',
                        unit_id=unit.id,
                    ))
                if len(editorial.sentences(unit.text)) < 2 and len(editorial.words(unit.text)) > 28:
                    issues.append(_ProseIssue(
                        'cv_profile_run_on',
                        f'Profile unit {idx} is a single long sentence; write 2–3 short sentences synthesizing the strongest evidence.',
                        'review_style',
                        'The CV profile is a single long run-on sentence instead of 2–3 short sentences.',
                        unit_id=unit.id,
                    ))
                if editorial.has_third_person_applicant(unit.text, doc.language):
                    issues.append(_ProseIssue(
                        'cv_third_person',
                        f'Profile unit {idx} refers to the applicant in third person; remove third-person pronouns (she/he/sie/er).',
                        'review_style',
                        'The CV profile refers to the applicant in the third person.',
                        unit_id=unit.id,
                    ))
                if doc.language == 'de' and editorial.missing_german_compound_hyphens(unit.text):
                    issues.append(_ProseIssue(
                        'cv_de_compound_hyphen',
                        f'Profile unit {idx} is missing a German suspension hyphen in a coordinated compound (e.g. "Markt- und Nutzerbedürfnisse").',
                        'review_style',
                        'A coordinated German compound in the CV profile is missing its suspension hyphen.',
                        unit_id=unit.id,
                    ))
                if doc.language == 'de' and editorial.has_awkward_de_verb_phrase(unit.text):
                    issues.append(_ProseIssue(
                        'cv_de_awkward_verb',
                        f'Profile unit {idx} uses an awkward verb-noun phrase such as "Leitete die Koordination"; use a direct active verb instead.',
                        'review_style',
                        'The CV profile uses a redundant German verb-noun construction.',
                        unit_id=unit.id,
                    ))
        elif section.kind in ('experience', 'education'):
            for entry in section.entries:
                phrases: set[tuple[str, ...]] = set()
                for unit in entry.units:
                    if unit.hidden or unit.origin != 'ai':
                        continue
                    current = editorial.sentence_phrases(unit_full_text(unit))
                    if phrases & current:
                        issues.append(_ProseIssue(
                            'cv_repeated_bullet_phrase',
                            f'Entry {entry.id} repeats a phrase of four or more words across bullets; give each bullet a distinct focus and wording.',
                            'review_style',
                            'Two bullets in this entry share a phrase of four or more words.',
                            unit_id=unit.id,
                        ))
                    phrases.update(current)
                    if editorial.has_third_person_applicant(unit.text, doc.language):
                        issues.append(_ProseIssue(
                            'cv_third_person',
                            f'Entry {entry.id} refers to the applicant in third person (e.g. "die sie koordinierte" or "she/he"); rewrite in direct CV voice without pronouns.',
                            'review_style',
                            'A CV bullet refers to the applicant in the third person.',
                            unit_id=unit.id,
                        ))
                    if doc.language == 'de' and editorial.missing_german_compound_hyphens(unit_full_text(unit)):
                        issues.append(_ProseIssue(
                            'cv_de_compound_hyphen',
                            f'Entry {entry.id} has a coordinated German compound missing its suspension hyphen (e.g. "Markt- und Nutzerbedürfnisse").',
                            'review_style',
                            'A coordinated German compound in a CV bullet is missing its suspension hyphen.',
                            unit_id=unit.id,
                        ))
                    if doc.language == 'de' and editorial.has_awkward_de_verb_phrase(unit.text):
                        issues.append(_ProseIssue(
                            'cv_de_awkward_verb',
                            f'Entry {entry.id} uses an awkward German verb-noun construction such as "Leitete die Koordination"; replace it with a direct active verb.',
                            'review_style',
                            'A CV bullet uses a redundant German verb-noun construction.',
                            unit_id=unit.id,
                        ))
    for verb, count, unit_id in editorial.repeated_opening_verbs(doc):
        issues.append(_ProseIssue(
            'cv_repeated_opening_verb',
            f'Opening verb "{verb}" starts {count} bullets across the CV; vary bullet openings so no verb starts more than two bullets.',
            'review_style',
            f'The opening verb “{verb}” starts {count} bullets across the CV.',
            unit_id=unit_id,
        ))
    return issues


def _detail_findings(doc: CvDocument, evidence: dict[str, str]) -> list[Finding]:
    return [
        _finding('cv', 'review_coverage', 'A rewritten bullet may omit a distinguishing detail from its cited base bullet.',
                 unit_id=unit.id, suggestion='Compare the rewrite with its cited base bullet and retain verified specifics.')
        for section in doc.sections for entry in section.entries for unit in entry.units
        if unit.origin == 'ai' and not unit.hidden and editorial.omitted_details(unit, evidence, entry.id)
    ]


def _qualifier_findings(doc: CvDocument, evidence: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    if doc.headline.origin == 'ai' and not doc.headline.hidden:
        if editorial.added_unsupported_qualifiers(doc.headline, evidence):
            findings.append(_finding(
                'cv', 'review_grounding',
                'The rewritten headline adds a qualifier that does not appear in its cited evidence.',
                unit_id=doc.headline.id,
                suggestion='Verify the qualifier is backed by the evidence or rephrase to match the documented scope.',
            ))
    for section in doc.sections:
        if section.kind == 'profile':
            for unit in section.units:
                if unit.origin == 'ai' and not unit.hidden and editorial.added_unsupported_qualifiers(unit, evidence):
                    findings.append(_finding(
                        'cv', 'review_grounding',
                        'A profile unit adds a qualifier that does not appear in its cited evidence.',
                        unit_id=unit.id,
                        suggestion='Keep qualifiers strictly grounded in the cited evidence.',
                    ))
        elif section.kind in ('experience', 'education'):
            for entry in section.entries:
                header_ctx = f'{entry.locked.company} {entry.locked.title}'
                for unit in entry.units:
                    if unit.origin == 'ai' and not unit.hidden and editorial.added_unsupported_qualifiers(unit, evidence, header_ctx):
                        findings.append(_finding(
                            'cv', 'review_grounding',
                            'A rewritten bullet adds a qualifier that does not appear in its cited evidence.',
                            unit_id=unit.id,
                            suggestion='Remove any adjective or scope label not supported by the cited source.',
                        ))
    return findings


def _date_line(inp: PreparationInput, base: CvDocument) -> str:
    months = ('January February March April May June July August September October November December'
              if inp.language == 'en' else
              'Januar Februar März April Mai Juni Juli August September Oktober November Dezember')
    day = str(inp.today.day) + ('.' if inp.language == 'de' else '')
    formatted = f'{day} {months.split()[inp.today.month - 1]} {inp.today.year}'
    location = next((c.text for c in base.header.contacts if c.kind == 'location'), '')
    city = location.split(',', 1)[0].strip()
    return f'{city[:198 - len(formatted)]}, {formatted}' if city else formatted


_NAME_WORD = r"[^\W\d_]+(?:[-'’][^\W\d_]+)*"
_NAME_PARTICLES = frozenset({'al', 'bin', 'da', 'das', 'de', 'del', 'della', 'den',
                             'der', 'di', 'do', 'dos', 'du', 'la', 'le', 'ten',
                             'ter', 'van', 'von', 'zu', 'zum', 'zur'})
_WORK_ARRANGEMENT = re.compile(
    r'\b(?:hybrid|remote|on[- ]?site|on[- ]?premises|in[- ]?office|home[- ]?office|'
    r'telework|work from home|vor ort|mobiles arbeiten)\b', re.I,
)


def _recipient_city(value: str) -> str:
    # Posting locations commonly put the city before the region or work arrangement.
    city = value.split(',', 1)[0].split('(', 1)[0].strip()
    arrangement = _WORK_ARRANGEMENT.search(city)
    if arrangement:
        city = city[:arrangement.start()].rstrip(' –—-')
        city = re.sub(r'\b(?:fully|mostly|partly|100%)$', '', city, flags=re.I).strip()
    if re.fullmatch(rf'{_NAME_WORD}(?: {_NAME_WORD})*', city):
        return city[:200]
    return ''


def _sender_name(value: str) -> str:
    if value.isupper():
        return ' '.join(word.lower() if word.lower() in _NAME_PARTICLES else word.title()
                        for word in value.split())
    return value


def _salutation(inp: PreparationInput, company: str, proposed_name: str) -> str:
    name = ' '.join(proposed_name.split())
    description = str(inp.job.get('description') or '')
    description = ' '.join(unescape(re.sub(r'<[^>]*>', ' ', description)).split())
    description = re.sub(r'\S+@\S+', ' ', description)
    more_words = '+' if inp.language == 'de' else '*'
    if (name and len(name) <= 200 and re.fullmatch(rf'{_NAME_WORD}(?: {_NAME_WORD}){more_words}', name)
            and name.split()[0].casefold() not in {'mr', 'ms', 'mrs', 'miss', 'mx', 'dr', 'prof', 'frau', 'herr'}
            and re.search(rf'(?<!\w){re.escape(name)}(?!\w)', description)):
        return f'Guten Tag {name},' if inp.language == 'de' else f'Dear {name},'
    if inp.language == 'de':
        return 'Sehr geehrte Damen und Herren,'
    short = editorial.LEGAL_SUFFIX.sub('', company).rstrip(' ,.')
    return f'Dear {short + " " if short else ""}Hiring Team,'


def _letter_prose_issues(
    letter: LetterDocument, cv: CvDocument, base: CvDocument, company: str,
) -> list[_ProseIssue]:
    ignore_phrases = editorial.cv_ignore_phrases(cv, extra=(*letter.recipient_lines, letter.subject.text))
    copied = editorial.cv_ngrams(cv, ignore_phrases=ignore_phrases)
    companies: dict[str, str] = {}
    for source_cv in (base, cv):
        for section in source_cv.sections:
            for entry in section.entries:
                companies[entry.id] = editorial.employer_key(entry.locked.company, entry.id)

    issues: list[_ProseIssue] = []
    first_seen: dict[str, int] = {}
    phrases: set[tuple[str, ...]] = set()
    motifs: set[tuple[str, ...]] = set()
    bridges: set[str] = set()
    company_name = editorial.employer_key(company, '')
    company_pattern = re.compile(
        r'(?<!\w)' + r'[\W_]+'.join(map(re.escape, company_name.split())) + r'(?!\w)', re.I,
    ) if company_name else None
    other_company_mentions = 0
    conditional_total = 0
    recent_openers: list[str] = []
    for idx, unit in enumerate(letter.paragraphs, 1):
        if unit.hidden:
            continue
        label = 'Closing sentence' if unit.role == 'close' else f'Paragraph {idx}'
        parts = editorial.sentences(unit.text)
        openers = [opener for part in parts if (opener := editorial.sentence_opening(part, letter.language))]
        recent_openers.extend(openers)
        if any(a == b == c for a, b, c in zip(
            recent_openers, recent_openers[1:], recent_openers[2:], strict=False,
        )):
            issues.append(_ProseIssue(
                'letter_repeated_opening',
                f'{label} has three consecutive sentences with the same opening structure; vary the sentence structure.',
                'review_style',
                f'{label} repeats the same sentence opening three times in a row.',
                unit_id=unit.id,
            ))
        recent_openers = recent_openers[-2:]
        if unit.role in ('experience', 'education') and len(parts) > 4:
            issues.append(_ProseIssue(
                'letter_experience_length',
                f'{label} has more than four sentences; focus on the contribution, verified outcome and connection to the role.',
                'review_style',
                f'{label} has more than four sentences.',
                unit_id=unit.id,
            ))
        if unit.role not in ('motivation', 'close') and company_pattern and company_pattern.search(unit.text):
            other_company_mentions += 1
            if other_company_mentions > 1:
                issues.append(_ProseIssue(
                    'letter_company_mentions',
                    f'{label} names the company again; keep its name in motivation and closing, with at most one other paragraph naming it.',
                    'review_style',
                    f'{label} names the target company after another experience or bring paragraph already did.',
                    unit_id=unit.id,
                ))
        current = editorial.sentence_phrases(unit.text)
        current_motifs = editorial.letter_content_trigrams(unit.text, ignore_phrases)
        if current & phrases:
            issues.append(_ProseIssue(
                'letter_repeated_phrase',
                f'{label} repeats a phrase of four or more words from an earlier paragraph; rephrase it.',
                'review_style',
                f'{label} repeats a phrase of four or more words from an earlier paragraph.',
                unit_id=unit.id,
            ))
        elif current_motifs & motifs:
            issues.append(_ProseIssue(
                'letter_repeated_motif',
                f'{label} reuses a distinctive sequence of content words from an earlier paragraph.',
                'review_style',
                f'{label} reuses a distinctive phrasing pattern from an earlier paragraph.',
                unit_id=unit.id,
            ))
        phrases.update(current)
        motifs.update(current_motifs)
        if unit.role != 'close':
            current_bridges = editorial.letter_bridge_verbs(unit.text)
            if current_bridges & bridges:
                issues.append(_ProseIssue(
                    'letter_repeated_bridge',
                    f'{label} reuses a bridge verb from an earlier paragraph. Connect the example to a different duty.',
                    'review_style',
                    f'{label} reuses a bridge verb from an earlier paragraph.',
                    unit_id=unit.id,
                ))
            bridges.update(current_bridges)
        if any(editorial.near_duplicate(left, right)
               for i, left in enumerate(parts) for right in parts[i + 1:]):
            issues.append(_ProseIssue(
                'letter_repeated_sentence',
                f'{label} repeats a point. Merge the sentences and keep their distinct verified details.',
                'review_style',
                f'{label} contains sentences that restate the same point.',
                unit_id=unit.id,
            ))
        groups: set[str] = set()
        for source in unit.sources:
            match = re.fullmatch(r'cv:([^:]+):[bi]\d+', source)
            if match and match[1] in companies:
                groups.add(companies[match[1]])
        overlap_prev = sorted({first_seen[group] for group in groups if group in first_seen})
        if overlap_prev:
            prev = overlap_prev[0]
            issues.append(_ProseIssue(
                'letter_duplicate_employer',
                f'{label} cites the same employer or CV entry as Paragraph {prev}. '
                'Use only one paragraph per employer or project; consolidate examples that cite the same entry '
                '(in a bring paragraph, cite job or profile/skill evidence instead).',
                'review_style',
                f'{label} repeats an employer or CV entry already covered in Paragraph {prev}.',
                unit_id=unit.id,
            ))
        for group in groups:
            first_seen.setdefault(group, idx)
        if editorial.prose_ngrams(unit.text, ignore_phrases=ignore_phrases) & copied:
            issues.append(_ProseIssue(
                'letter_cv_ngram_overlap',
                f'{label} repeats phrasing from the CV. Rewrite {label} in fresh prose so no sequence of eight consecutive words matches the CV.',
                'review_style',
                f'{label} closely repeats wording from the CV.',
                unit_id=unit.id,
            ))
        if letter.language == 'en' and editorial.has_en_contraction(unit.text):
            issues.append(_ProseIssue(
                'letter_contraction',
                f'{label} contains a contraction. Spell out all contractions in the English letter.',
                'review_style',
                f'{label} uses a contraction in the English letter.',
                unit_id=unit.id,
            ))
        conditionals = len(editorial.CONDITIONAL.findall(unit.text))
        if conditionals > 1:
            issues.append(_ProseIssue(
                'letter_conditional_count',
                f'{label} uses {conditionals} conditionals (würde or would). Use at most one conditional in the whole letter and prefer the indicative.',
                'review_style',
                f'{label} uses more than one conditional (würde or would).',
                unit_id=unit.id,
            ))
        elif conditionals == 1 and unit.role != 'close' and conditional_total >= 1:
            issues.append(_ProseIssue(
                'letter_conditional_count',
                f'{label} uses another conditional (würde or would) after an earlier paragraph already used one. Use at most one conditional across the entire letter and rewrite this sentence in the indicative.',
                'review_style',
                f'{label} repeats a conditional (würde or would) used in an earlier paragraph.',
                unit_id=unit.id,
            ))
        if unit.role != 'close':
            conditional_total += conditionals
        if letter.language == 'de' and editorial.missing_german_compound_hyphens(unit.text):
            issues.append(_ProseIssue(
                'letter_de_compound_hyphen',
                f'{label} is missing a German suspension hyphen in a coordinated compound (e.g. "Markt- und Nutzerbedürfnisse").',
                'review_style',
                f'{label} has a coordinated German compound missing its suspension hyphen.',
                unit_id=unit.id,
            ))
        if letter.language == 'de' and editorial.has_awkward_de_verb_phrase(unit.text):
            issues.append(_ProseIssue(
                'letter_de_awkward_verb',
                f'{label} uses an awkward German verb-noun phrase such as "leitete ich die Koordination"; use a direct active verb instead.',
                'review_style',
                f'{label} uses a redundant German verb-noun construction.',
                unit_id=unit.id,
            ))
    for prev_idx, curr_idx in editorial.repeated_paragraph_endings(letter.paragraphs):
        unit = letter.paragraphs[curr_idx - 1]
        issues.append(_ProseIssue(
            'letter_repeated_ending',
            f'Paragraph {curr_idx} ends with the same closing formula as Paragraph {prev_idx}. Vary paragraph endings so no two body paragraphs end with sentences sharing their first three words or a repeated bridge formula.',
            'review_style',
            f'Paragraph {curr_idx} repeats the closing sentence formula of Paragraph {prev_idx}.',
            unit_id=unit.id,
        ))
    first_body = next((u for u in letter.paragraphs if not u.hidden and u.role != 'close'), None)
    if letter.language == 'de' and first_body is not None:
        if editorial.has_de_salutation_case_error(letter.salutation.text, first_body.text):
            issues.append(_ProseIssue(
                'letter_de_salutation_case',
                'Paragraph 1 starts with a capitalized non-noun after a comma-ended German salutation; start the first word lowercase unless it is a noun, proper name, or Sie/Ihr*.',
                'review_style',
                'German letter body starts capitalized after a comma-ended salutation.',
                unit_id=first_body.id,
            ))
    return issues


def _letter_draft(output: dict, inp: PreparationInput, base: CvDocument, cv: CvDocument | None = None) -> LetterDocument:
    reply = _LetterResponse.model_validate(output)
    if not reply.paragraphs:
        raise _InvalidResponse(
            'Return 4–5 paragraphs total: 3–4 body paragraphs and the final closing sentence.',
            code='letter_paragraph_count',
        )
    paragraphs = [
        _ai(f'u-letter-p{i}', item, role=item.role) for i, item in enumerate(reply.paragraphs, 1)
    ]
    body_words = sum(len(unit.text.split()) for unit in paragraphs)
    paragraphs.append(_ai('u-letter-closing', reply.closing, role='close'))
    company = ' '.join(str(inp.job.get('company_name') or '').split())
    company = re.sub(r'\s*\([^()]*\)\s*$', '', company).strip()[:200]
    city = _recipient_city(' '.join(str(inp.job.get('location') or '').split()))
    name = _sender_name(base.header.name)
    salutation_text = _salutation(inp, company, reply.contact_name)
    if inp.language == 'de' and paragraphs:
        paragraphs[0].text = editorial.normalize_de_salutation_opening(salutation_text, paragraphs[0].text)
    title = ' '.join(str(inp.job.get('title') or '').split())
    if inp.language == 'en' and title:
        subject = Unit(id='u-letter-subject', text=f'Application for {title}'[:MAX_UNIT_TEXT], sources=['job'])
    else:
        subject = _ai('u-letter-subject', _SourcedText(text=reply.subject.text, sources=['job']))
    letter = LetterDocument(
        language=inp.language, page='a4',
        sender={'name': name, 'lines': [c.text for c in base.header.contacts[:10]]},
        recipient_lines=[line for line in (company, city) if line], place_date=_date_line(inp, base),
        subject=subject,
        salutation=Unit(id='u-letter-salutation', text=salutation_text),
        paragraphs=paragraphs, closing=Unit(id='u-letter-signoff',
                                           text='Mit freundlichen Grüßen' if inp.language == 'de' else 'Kind regards,'),
        signature=name,
    )
    issues: list[_ProseIssue] = []
    if not 3 <= len(reply.paragraphs) <= 4:
        issues.append(_ProseIssue(
            'letter_paragraph_count',
            f'Returned {len(reply.paragraphs)} body paragraphs; return 4–5 paragraphs total: 3–4 body paragraphs and the final closing sentence.',
            'review_format',
            'The letter body should contain 3 to 4 paragraphs plus a closing sentence.',
        ))
    if reply.paragraphs[0].role != 'motivation' or any(p.role == 'motivation' for p in reply.paragraphs[1:]):
        issues.append(_ProseIssue(
            'letter_motivation_position',
            'Open with one employer-focused motivation paragraph (Paragraph 1, role="motivation"), then give relevant examples.',
            'review_format',
            'The letter should open with a single motivation paragraph.',
        ))
    if not 150 <= body_words <= 450:
        issues.append(_ProseIssue(
            'letter_word_count',
            f'Body has {body_words} words; keep the body between 150 and 450 words, ideally 270 to 310 (English) or 260 to 320 (German).',
            'review_format',
            'The letter body length is outside the 150 to 450 word range.',
        ))
    issues.extend(issue for issue in _letter_prose_issues(letter, cv if cv is not None else base, base, company)
                  if issue.code in _BLOCKING_PROSE_CODES)
    if issues:
        codes = tuple(dict.fromkeys(issue.code for issue in issues))
        message = ' '.join(dict.fromkeys(issue.retry_message for issue in issues))
        raise _InvalidResponse(
            message, code=codes[0], codes=codes, candidate=letter, issues=tuple(issues),
        )
    return letter


def _units(doc: Document, *, visible_only: bool = False) -> list[dict]:
    context = {}
    if isinstance(doc, CvDocument):
        for section in doc.sections:
            for unit in section.units:
                context[unit.id] = {'section': section.kind}
            for entry in section.entries:
                for unit in entry.intro + entry.units:
                    context[unit.id] = {'section': section.kind, 'entry_id': entry.id,
                                        'company': entry.locked.company, 'title': entry.locked.title}
    return [{**unit.model_dump(), 'context': context.get(unit.id, {})}
            for unit in walk_units(doc) if not visible_only or not unit.hidden]


def _ground(doc: Document, evidence: dict, inp: PreparationInput, base: CvDocument) -> list[Finding]:
    return check_document(doc, evidence, language=inp.language, profile=inp.profile, base_cv=base, job=inp.job)


def _high(findings: list[Finding]) -> set[tuple]:
    return {(f.unit_id, f.code, f.message) for f in findings if f.severity == 'high'}


def _review(
    output: dict, docs: dict[DocumentKind, Document], ground: Callable[[Document], list[Finding]],
    base: CvDocument, evidence: dict[str, str], posting: str, company: str,
    grounding_targets: set[tuple[DocumentKind, str]],
) -> tuple[dict, list[Finding], list[dict], int]:
    reply = _ReviewEnvelope.model_validate(output)
    docs = docs.copy()
    findings = []
    applied = []
    touched = set()
    malformed = 0
    for item in reply.findings:
        try:
            review = _ReviewFinding.model_validate(item)
            target = unit_by_id(docs[review.document], review.unit_id) if review.unit_id is not None else None
            if review.unit_id is not None and target is None:
                findings.append(_finding(review.document, 'review_format', 'The review referred to an unknown unit.'))
                continue
            if (review.category == 'grounding' and review.severity != 'info'
                    and target is not None and target.origin == 'ai' and not target.hidden):
                grounding_targets.add((review.document, target.id))
                continue
            findings.append(_finding(
                review.document, f'review_{review.category}', _plain(review.message, 500),
                unit_id=review.unit_id, severity=review.severity, suggestion=review.suggestion,
            ))
        except (ValidationError, _InvalidResponse):
            malformed += 1
    patches = []
    for item in reply.patches:
        try:
            patches.append(_ReviewPatch.model_validate(item))
        except ValidationError:
            malformed += 1
    corrected = set()
    # Grounding corrections take precedence when the review proposes multiple edits to one unit.
    for proposed in sorted(patches, key=lambda patch: patch.kind != 'grounding'):
        kind = proposed.document
        doc = docs[kind]
        unit = unit_by_id(doc, proposed.unit_id)
        if unit is None:
            findings.append(_finding(kind, 'review_format', 'The review proposed a patch for an unknown unit.'))
            continue
        key = (kind, unit.id)
        if proposed.kind == 'grounding' and unit.origin == 'ai' and not unit.hidden:
            grounding_targets.add(key)
        try:
            text = _plain(proposed.text)
        except _InvalidResponse:
            malformed += 1
            continue
        if text == unit.text or key in corrected:
            continue
        if key in grounding_targets and proposed.kind != 'grounding':
            continue
        if proposed.kind != 'content' and unit.origin == 'ai' and not unit.hidden and key not in touched:
            candidate = apply_patch(doc, unit.id, text, 'ai')
            if (_valid_review_edit(candidate, docs, unit, base, evidence, posting, company)
                    and not _high(ground(candidate)) - _high(ground(doc))):
                docs[kind] = candidate
                touched.add(key)
                if proposed.kind == 'grounding':
                    corrected.add(key)
                applied.append({'document': kind, 'unit_id': unit.id, 'kind': proposed.kind})
                continue
        code = 'review_coverage' if proposed.kind == 'content' else 'review_style'
        findings.append(_finding(kind, code, 'Review suggested a manual wording change.',
                                 unit_id=unit.id, suggestion=text))
    if missing := grounding_targets - corrected:
        units = ', '.join(f'{kind} {uid}' for kind, uid in sorted(missing))
        raise _InvalidResponse(
            f'Return kind="grounding" patches for {units}. Narrow each unsupported claim to the exact '
            'scope of its existing citations. Keep supported details, avoid copying the CV and return the full review.',
            code='review_grounding_unpatched',
        )
    return docs, findings, applied, malformed


def _valid_review_edit(
    candidate: Document, docs: dict[DocumentKind, Document], unit: Unit,
    base: CvDocument, evidence: dict[str, str], posting: str, company: str,
) -> bool:
    cv = candidate if isinstance(candidate, CvDocument) else docs['cv']
    letter = candidate if isinstance(candidate, LetterDocument) else docs['letter']
    def blocking_keys(issues):
        return {(issue.code, issue.unit_id) for issue in issues if issue.code in _BLOCKING_PROSE_CODES}

    old_cv_issues = blocking_keys(_cv_prose_issues(docs['cv']))
    new_cv_issues = blocking_keys(_cv_prose_issues(cv))
    if new_cv_issues - old_cv_issues:
        return False
    old_letter_all = _letter_prose_issues(docs['letter'], docs['cv'], base, company)
    new_letter_all = _letter_prose_issues(letter, cv, base, company)
    old_letter_issues = blocking_keys(old_letter_all)
    new_letter_issues = blocking_keys(new_letter_all)
    if new_letter_issues - old_letter_issues:
        return False
    _no_regress = frozenset({
        'letter_repeated_ending',
        'letter_repeated_phrase',
        'letter_repeated_motif',
        'letter_repeated_bridge',
        'letter_repeated_sentence',
        'letter_repeated_opening',
        'letter_conditional_count',
    })
    old_style = {(issue.code, issue.unit_id) for issue in old_letter_all if issue.code in _no_regress}
    new_style = {(issue.code, issue.unit_id) for issue in new_letter_all if issue.code in _no_regress}
    if new_style - old_style:
        return False
    if isinstance(candidate, CvDocument):
        before = {(f.unit_id, f.message) for f in _qualifier_findings(docs['cv'], evidence)}
        after = {(f.unit_id, f.message) for f in _qualifier_findings(candidate, evidence)}
        if after - before:
            return False
        for section in candidate.sections:
            if section.kind == 'skills':
                updated = next((u for u in section.units if u.id == unit.id), None)
                if updated is not None:
                    return _supported_competency(updated, evidence, posting)
            for entry in section.entries:
                updated = next((u for u in entry.units if u.id == unit.id), None)
                if updated is not None:
                    return not (editorial.omitted_critical_details(updated, evidence, entry.id)
                                - editorial.omitted_critical_details(unit, evidence, entry.id))
    return True


def _render(
    kind: DocumentKind, doc: Document, render: Callable[[str, dict], RenderResult] | None,
) -> tuple[dict, list[Finding]]:
    if render is None:
        return {}, []
    started = time.monotonic()
    try:
        result = render(kind, doc.model_dump())
    except Exception as error:
        return {'error': getattr(error, 'code', type(error).__name__),
                'seconds': round(time.monotonic() - started, 3)}, [
                    _finding(kind, 'review_format', 'PDF rendering could not be verified.', severity='high'),
                ]
    telemetry = {**result.telemetry, 'pages': result.pages, 'checks': result.checks,
                 'seconds': round(time.monotonic() - started, 3)}
    findings = []
    if result.page_limit_exceeded:
        findings.append(_finding(kind, 'extra_page', f'The PDF spans {result.pages} pages.', severity='medium'))
    elif telemetry.get('overflow_px', 0) > 0:
        findings.append(_finding(kind, 'overflow', 'The document may overflow the page limit.'))
    for unit_id in telemetry.get('beyond_limit_units', [])[:5]:
        if unit_by_id(doc, unit_id) is not None:
            findings.append(_finding(kind, 'overflow', 'This unit extends past the page limit.', unit_id=unit_id))
    for check in result.checks:
        if check['status'] == 'failed':
            findings.append(_finding(kind, 'review_format', f'PDF check failed: {check["code"]}.',
                                     severity='high'))
    return telemetry, findings


def _numbered(findings: list[Finding]) -> list[dict]:
    counts: Counter = Counter()
    result = []
    for finding in findings:
        kind = finding.document
        counts[kind] += 1
        result.append({**finding.model_dump(), 'id': f'f-{kind}-{counts[kind]}'})
    return result


def _load(hooks: StageHooks, key: str, model: type[_Checkpoint]):
    stored = hooks.load(key)
    if stored is not None:
        try:
            return model.model_validate(stored)
        except ValidationError:
            pass
    return None


def prepare_documents(
    inp: PreparationInput, generate: Generate, hooks: StageHooks,
    render: Callable[[str, dict], RenderResult] | None = None,
) -> PreparationResult:
    base = CvDocument.model_validate(inp.base_cv)
    evidence = build_evidence(base, inp.profile)
    posting = ' '.join(str(inp.job.get(key) or '') for key in ('title', 'description'))
    company = ' '.join(str(inp.job.get('company_name') or '').split())
    digest = _input_digest(inp, base, evidence)
    stages = {}
    renders = {}
    applied = []
    malformed = 0

    def ground(doc: Document) -> list[Finding]:
        return _ground(doc, evidence, inp, base)

    def require_grounding(doc: Document) -> None:
        findings = [finding for finding in ground(doc) if finding.severity == 'high']
        qualifiers = _qualifier_findings(doc, evidence) if isinstance(doc, CvDocument) else []
        codes = tuple(dict.fromkeys([f.code for f in findings] +
                                    (['unsupported_qualifier'] if qualifiers else [])))
        if codes:
            instructions = ' '.join(dict.fromkeys(f.suggestion for f in findings + qualifiers))
            raise _InvalidResponse(instructions, codes=codes)

    def accept_grounded(build: Callable, output: dict):
        try:
            result = build(output)
        except _InvalidResponse as error:
            # An imperfect but grounded draft can still be reviewed after retries.
            if error.candidate is not None:
                doc = error.candidate[0] if isinstance(error.candidate, tuple) else error.candidate
                require_grounding(doc)
            raise
        require_grounding(result[0] if isinstance(result, tuple) else result)
        return result

    def commit(key, checkpoint: _Checkpoint | None, docs, findings) -> None:
        _check_cancelled(hooks.cancelled)
        outputs = [(kind, doc.model_dump(), _numbered([f for f in findings if f.document == kind]))
                   for kind, doc in docs.items()]
        hooks.commit(key, checkpoint.model_dump() if checkpoint is not None else None, outputs)

    def start(stage: str) -> tuple[float, str, dict]:
        _check_cancelled(hooks.cancelled)
        hooks.stage(stage)
        stats = stages[stage] = {'attempts': 0}
        return time.monotonic(), f'{stage}:{digest}', stats

    def complete(stats: dict, started: float, state: str) -> dict:
        stats.update(state=state, seconds=round(time.monotonic() - started, 3))
        return {name: stats[name] for name in ('attempts', 'seconds')}

    def resume(stats: dict, checkpoint: _Checkpoint) -> None:
        stats.update(state='resumed', attempts=checkpoint.attempts, seconds=checkpoint.seconds)

    started, key, stats = start('cv')
    checkpoint = _load(hooks, key, _CvCheckpoint)
    cv_state = 'completed'
    if checkpoint is not None:
        cv, cv_findings, renders['cv'] = checkpoint.document, checkpoint.findings, checkpoint.render
        draft_findings = checkpoint.draft_findings
        cv_state = 'fallback' if checkpoint.fallback else 'completed'
        resume(stats, checkpoint)
    else:
        entries = _cv_entries(base)
        skills = [{'section_id': s.id, 'title': s.title, 'units': [u.model_dump() for u in s.units]}
                  for s in base.sections if s.kind == 'skills']
        prompt = _prompt('cv', inp, evidence, entries=entries, skills=skills)
        last_render = None

        def accept_cv(output: dict):
            nonlocal last_render
            doc, findings = accept_grounded(
                lambda reply: _cv_draft(reply, base, entries, evidence, posting), output,
            )
            telemetry, render_findings = _render('cv', doc, render)
            last_render = (doc, telemetry, render_findings)
            if any(finding.code == 'extra_page' for finding in render_findings):
                issue = _ProseIssue(
                    'cv_page_limit',
                    'The rendered CV exceeded two pages. Shorten sentences and eliminate redundant wording '
                    'throughout the profile and bullets. Retain every bullet, project and quantified result.',
                    'extra_page',
                    'The CV draft exceeded two pages.',
                )
                raise _InvalidResponse(issue.retry_message, code=issue.code,
                                       candidate=(doc, findings), issues=(issue,))
            return doc, findings

        cv_outcome = _generate(generate, prompt, _CvResponse, accept_cv,
                               stats, hooks.cancelled, return_best=True)
        if isinstance(cv_outcome, _Fallback):
            cv, base_draft_findings = cv_outcome.candidate
            _check_cv_coverage(cv, base, restore=True)
            _show_named_skills(cv)
            draft_findings = base_draft_findings + _issues_to_findings(
                'cv', [issue for issue in cv_outcome.issues
                       if issue.code not in _BLOCKING_PROSE_CODES and issue.code != 'cv_page_limit'],
            )
            stats['error'] = 'invalid_response'
            cv_state = 'fallback'
        else:
            cv, draft_findings = cv_outcome
        if last_render is not None and cv == last_render[0]:
            _, renders['cv'], render_findings = last_render
        else:
            renders['cv'], render_findings = _render('cv', cv, render)
        cv_findings = (
            draft_findings
            + _issues_to_findings('cv', _cv_prose_issues(cv))
            + _detail_findings(cv, evidence)
            + _qualifier_findings(cv, evidence)
            + ground(cv)
            + render_findings
        )
        timing = complete(stats, started, cv_state)
        saved = _CvCheckpoint(document=cv, findings=cv_findings, draft_findings=draft_findings,
                              render=renders['cv'], fallback=cv_state == 'fallback', **timing)
        commit(key, saved, {'cv': cv}, cv_findings)

    started, key, stats = start('letter')
    cv_digest = _digest(cv.model_dump())
    checkpoint = _load(hooks, key, _LetterCheckpoint)
    letter_non_prose_findings: list[Finding] = []
    if checkpoint is not None and checkpoint.cv_digest == cv_digest:
        letter, letter_findings, renders['letter'] = checkpoint.document, checkpoint.findings, checkpoint.render
        letter_non_prose_findings = checkpoint.non_prose_findings
        resume(stats, checkpoint)
    else:
        letter_cv_source = _new_cv(base) if cv_state == 'fallback' else cv
        prompt = _prompt('letter', inp, evidence, cv_units=_units(letter_cv_source, visible_only=True))
        letter_outcome = _generate(
            generate, prompt, _LetterResponse,
            lambda output: accept_grounded(lambda reply: _letter_draft(reply, inp, base, cv), output),
            stats, hooks.cancelled, return_best=True,
        )
        if isinstance(letter_outcome, _Fallback):
            letter = letter_outcome.candidate
            letter_non_prose_findings = _issues_to_findings(
                'letter', [issue for issue in letter_outcome.issues if issue.code not in _BLOCKING_PROSE_CODES],
            )
            stats['error'] = 'invalid_response'
            letter_state = 'fallback'
        else:
            letter = letter_outcome
            letter_state = 'completed'
        renders['letter'], render_findings = _render('letter', letter, render)
        letter_findings = (
            letter_non_prose_findings
            + _issues_to_findings('letter', _letter_prose_issues(letter, cv, base, company))
            + ground(letter)
            + render_findings
        )
        timing = complete(stats, started, letter_state)
        saved = _LetterCheckpoint(document=letter, cv_digest=cv_digest, findings=letter_findings,
                                  render=renders['letter'], non_prose_findings=letter_non_prose_findings, **timing)
        commit(key, saved, {'letter': letter}, letter_findings)

    docs = {'cv': cv, 'letter': letter}
    findings = cv_findings + letter_findings
    if inp.mode == 'full':
        started, key, stats = start('review')
        upstream_digest = _digest([cv_digest, letter.model_dump()])
        checkpoint = _load(hooks, key, _ReviewCheckpoint)
        if checkpoint is not None and checkpoint.upstream_digest == upstream_digest:
            docs = {'cv': checkpoint.cv, 'letter': checkpoint.letter}
            findings, renders = checkpoint.findings, checkpoint.render
            applied, malformed = checkpoint.applied_patches, checkpoint.malformed_items
            resume(stats, checkpoint)
        else:
            prompt = _prompt('review', inp, evidence, documents={k: _units(d) for k, d in docs.items()},
                             findings=_numbered(findings), render=renders)
            grounding_targets: set[tuple[DocumentKind, str]] = set()

            def accept_review(output):
                reviewed, review_findings, patches, malformed = _review(
                    output, docs, ground, base, evidence, posting, company, grounding_targets,
                )
                checked = {}
                for kind, doc in reviewed.items():
                    if doc == docs[kind]:
                        continue
                    telemetry, render_findings = _render(kind, doc, render)
                    limit = PAGE_LIMITS[kind]
                    if (1 <= renders[kind].get('pages', 0) <= limit
                            and any(finding.code == 'extra_page' for finding in render_findings)):
                        if any(patch['document'] == kind and patch['kind'] == 'grounding' for patch in patches):
                            raise _InvalidResponse(
                                f'The revised {kind} exceeds its {limit}-page limit. Return shorter '
                                'grounding corrections with the same verified facts and omit optional edits.',
                                code='review_grounding_page_limit',
                            )
                        reviewed[kind] = docs[kind]
                        patches = [patch for patch in patches if patch['document'] != kind]
                    else:
                        checked[kind] = (telemetry, render_findings)
                return reviewed, review_findings, patches, malformed, checked

            reviewed, review_findings, applied, malformed, checked = _generate(
                generate, prompt, _ReviewResponse, accept_review, stats, hooks.cancelled,
            )
            final_findings = {'cv': cv_findings, 'letter': letter_findings}
            for kind, (telemetry, render_findings) in checked.items():
                doc = reviewed[kind]
                renders[kind] = telemetry
                final_findings[kind] = ground(doc) + render_findings
                if isinstance(doc, CvDocument):
                    final_findings[kind] = (
                        draft_findings
                        + _issues_to_findings('cv', _cv_prose_issues(doc))
                        + _detail_findings(doc, evidence)
                        + _qualifier_findings(doc, evidence)
                        + final_findings[kind]
                    )
                else:
                    final_findings[kind] = (
                        letter_non_prose_findings
                        + _issues_to_findings('letter', _letter_prose_issues(doc, reviewed['cv'], base, company))
                        + final_findings[kind]
                    )
            docs = reviewed
            findings = final_findings['cv'] + final_findings['letter'] + review_findings
            timing = complete(stats, started, 'completed')
            saved = _ReviewCheckpoint(cv=docs['cv'], letter=docs['letter'], findings=findings,
                                      upstream_digest=upstream_digest, render=renders,
                                      applied_patches=applied, malformed_items=malformed,
                                      **timing)
            commit(key, saved, docs, findings)

    _check_cancelled(hooks.cancelled)
    hooks.stage('finalize')
    telemetry = {
        'attempts': {name: stats['attempts'] for name, stats in stages.items()},
        'stage_seconds': {name: stats['seconds'] for name, stats in stages.items()},
        'attempt_failures': {
            name: stats['attempt_failures']
            for name, stats in stages.items()
            if stats.get('attempt_failures')
        },
        'render': renders,
        'applied_patches': applied,
        'malformed_review_items': malformed,
    }
    return PreparationResult(docs['cv'].model_dump(), docs['letter'].model_dump(), _numbered(findings),
                             telemetry, stages)


def revise_unit(
    document: dict, kind: str, unit_id: str, instruction: str, inp: PreparationInput, generate: Generate,
) -> dict:
    doc = {'cv': CvDocument, 'letter': LetterDocument}[kind].model_validate(document)
    unit = unit_by_id(doc, unit_id)
    if unit is None:
        raise KeyError(unit_id)
    base = CvDocument.model_validate(inp.base_cv)
    evidence = build_evidence(base, inp.profile)
    context = next(item for item in _units(doc) if item['id'] == unit_id)
    prompt = _prompt('revise', inp, evidence, unit=context, instruction=instruction)

    def accept(output: dict) -> Unit:
        reply = _SourcedText.model_validate(output)
        return _ai(unit_id, reply)

    revised = _generate(generate, prompt, _SourcedText, accept, {}, lambda: False)
    candidate = apply_patch(doc, unit_id, revised.text, 'ai', sources=revised.sources)
    findings = [f for f in _ground(candidate, evidence, inp, base) if f.unit_id == unit_id]
    return {'unit_id': unit_id, 'text': revised.text, 'sources': revised.sources, 'findings': _numbered(findings)}


def codex_generate(*, model: str | None, cancelled: Callable[[], bool] | None) -> Generate:
    from fastapi import HTTPException

    from hiring_scraper.app import codex_runtime

    def generate(prompt: str, schema: dict) -> dict:
        try:
            return codex_runtime.generate(prompt, schema, model=model, cancelled=cancelled)
        except HTTPException as error:
            if cancelled is not None:
                _check_cancelled(cancelled)
            if error.status_code == 502 and str(error.detail).startswith('ChatGPT returned an incomplete application.'):
                raise GenerationError('invalid_json', retryable=True) from None
            code, retryable = {
                401: ('not_connected', False), 409: ('busy', True),
                429: ('usage_limit', False), 502: ('provider_error', True),
                503: ('runtime_unavailable', False), 504: ('timeout', True),
            }.get(error.status_code, ('generation_failed', False))
            raise GenerationError(code, retryable=retryable) from None
        except ValueError:
            raise GenerationError('invalid_json', retryable=True) from None

    return generate
