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

from pydantic import BaseModel, ConfigDict, ValidationError

from hiring_scraper.app.application_pdf import RenderResult
from hiring_scraper.documents.evidence import build_evidence
from hiring_scraper.documents.grounding import check_document
from hiring_scraper.documents.schema import (
    MAX_SECTION_UNITS,
    MAX_UNIT_TEXT,
    CvDocument,
    DocumentLanguage,
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

PIPELINE_VERSION = 'application-documents-v2.1'
SLEEP = time.sleep
PROMPTS = Path(__file__).with_name('prompts')
DocumentKind = Literal['cv', 'letter']
Document = CvDocument | LetterDocument
Generate = Callable[[str, dict], dict]


class GenerationError(Exception):
    def __init__(self, code: str, message: str = '', retryable: bool = False):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable


@dataclass
class StageHooks:
    load: Callable[[str], dict | None]
    save: Callable[[str, dict], None]
    publish: Callable[[str, dict, list[dict]], None]
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
    kind: Literal['rephrase', 'shorten', 'content']


class _ReviewEnvelope(_Response):
    findings: list
    patches: list


class _ReviewResponse(_Response):
    findings: list[_ReviewFinding]
    patches: list[_ReviewPatch]


class _InvalidResponse(ValueError):
    """A fixed validation message safe to include in a retry prompt."""


class _Checkpoint(_Response):
    findings: list[Finding]
    render: dict
    attempts: int
    seconds: float


class _CvCheckpoint(_Checkpoint):
    document: CvDocument
    draft_findings: list[Finding]


class _LetterCheckpoint(_Checkpoint):
    document: LetterDocument
    cv_digest: str


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
    job = {key: inp.job.get(key) or '' for key in ('title', 'company_name', 'location', 'url', 'description')}
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
):
    schema = response.model_json_schema()
    retry_prompt = prompt
    for attempt in range(1, 4):
        _check_cancelled(cancelled)
        stats['attempts'] = attempt
        try:
            output = generate(retry_prompt, schema)
            _check_cancelled(cancelled)
            return accept(output)
        except (ValidationError, _InvalidResponse) as error:
            message = (str(error) if isinstance(error, _InvalidResponse)
                       else 'Return a complete object matching the schema, with valid plain text and source ids.')
            failure = GenerationError('invalid_response', message, retryable=True)
        except GenerationError as error:
            if not error.retryable:
                raise
            failure = error
            message = ('Return a complete JSON object matching the schema.'
                       if error.code == 'invalid_json' else '')
        if attempt == 3:
            raise failure from None
        retry_prompt = prompt + (f'\n\nYour previous answer was rejected: {message}' if message else '')
        _backoff((5, 20)[attempt - 1], cancelled)


def _plain(text: str, limit: int = MAX_UNIT_TEXT) -> str:
    text = ' '.join(text.split())
    if not text or len(text) > limit:
        raise _InvalidResponse(f'Each rewritten text must contain 1 to {limit} characters.')
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


def _cv_entries(base: CvDocument, evidence: dict) -> list[dict]:
    entries = []
    for section in base.sections:
        for entry in section.entries:
            prefix = f'cv:{entry.id}:b'
            bank = [key for key in evidence if key.startswith(prefix)]
            if bank:
                entries.append({'entry_id': entry.id, 'kind': entry.kind, **entry.locked.model_dump(),
                                'base_count': len(bank), 'bullet_bank': bank})
    return entries


def _cv_draft(
    output: dict, base: CvDocument, entries: list[dict],
) -> tuple[CvDocument, list[Finding]]:
    reply = _CvResponse.model_validate(output)
    if not 1 <= len(reply.profile) <= 2:
        raise _InvalidResponse('Return one or two profile units.')
    if any(not unit.sources for unit in [reply.headline, *reply.profile]):
        raise _InvalidResponse('Cite the evidence supporting the headline and each profile unit.')
    counts = {entry['entry_id']: entry['base_count'] for entry in entries}
    findings = []

    def unknown(message: str) -> None:
        findings.append(_finding('cv', 'review_format', message))

    edits = {}
    for proposed in reply.entries:
        entry_id = proposed.entry_id
        if entry_id not in counts:
            unknown('The CV response referred to an entry outside the supplied bullet banks.')
            continue
        if entry_id in edits:
            raise _InvalidResponse('Return each listed entry exactly once.')
        count = counts[entry_id]
        if not max(1, count - 1) <= len(proposed.bullets) <= min(MAX_SECTION_UNITS, count + 1):
            raise _InvalidResponse('Each entry needs between one fewer and one more bullet than its base count, at least one.')
        if any(not bullet.sources for bullet in proposed.bullets):
            raise _InvalidResponse('Cite evidence supporting every rewritten CV bullet.')
        edits[entry_id] = proposed.bullets

    skill_sections = {s.id for s in base.sections if s.kind == 'skills'}
    skills = {}
    for proposed in reply.skills:
        if proposed.section_id not in skill_sections:
            unknown('The CV response referred to an unknown skills section.')
        elif proposed.section_id in skills:
            raise _InvalidResponse('Return each skills section at most once.')
        else:
            skills[proposed.section_id] = proposed

    doc = base.model_copy(deep=True)
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
            *(_ai(new_id(f'u-ai-profile-{i}'), unit) for i, unit in enumerate(reply.profile, 1)),
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
    return CvDocument.model_validate(doc.model_dump()), findings


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
    # A posting's location often describes a region, multiple sites or a work arrangement.
    # Omit ambiguous strings instead of inventing a mailing address from them.
    if re.fullmatch(rf'{_NAME_WORD}(?: {_NAME_WORD})*', value) and not _WORK_ARRANGEMENT.search(value):
        return value[:200]
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
    if (len(name) <= 200 and re.fullmatch(rf'{_NAME_WORD}(?: {_NAME_WORD})+', name)
            and name.split()[0].casefold() not in {'mr', 'ms', 'mrs', 'dr', 'prof', 'frau', 'herr'}
            and re.search(rf'(?<!\w){re.escape(name)}(?!\w)', description)):
        return f'Guten Tag {name},' if inp.language == 'de' else f'Dear {name},'
    if inp.language == 'de':
        return f'Sehr geehrtes {company + "-" if company else ""}Recruiting-Team,'
    return f'Dear {company + " " if company else ""}Recruiting Team,'


def _letter_draft(output: dict, inp: PreparationInput, base: CvDocument) -> LetterDocument:
    reply = _LetterResponse.model_validate(output)
    if not 3 <= len(reply.paragraphs) <= 6:
        raise _InvalidResponse('Return 3 to 6 body paragraphs plus the final closing sentence.')
    paragraphs = [
        _ai(f'u-letter-p{i}', item, role=item.role) for i, item in enumerate(reply.paragraphs, 1)
    ]
    if not 150 <= sum(len(unit.text.split()) for unit in paragraphs) <= 450:
        raise _InvalidResponse('Keep the body between 150 and 450 words, ideally 260 to 320.')
    paragraphs.append(_ai('u-letter-closing', reply.closing, role='close'))
    company = ' '.join(str(inp.job.get('company_name') or '').split())[:200]
    city = _recipient_city(' '.join(str(inp.job.get('location') or '').split()))
    name = _sender_name(base.header.name)
    return LetterDocument(
        language=inp.language, page=base.page,
        sender={'name': name, 'lines': [c.text for c in base.header.contacts[:10]]},
        recipient_lines=[line for line in (company, city) if line], place_date=_date_line(inp, base),
        subject=_ai('u-letter-subject', _SourcedText(text=reply.subject.text, sources=['job'])),
        salutation=Unit(id='u-letter-salutation', text=_salutation(inp, company, reply.contact_name)),
        paragraphs=paragraphs, closing=Unit(id='u-letter-signoff',
                                           text='Mit freundlichen Grüßen' if inp.language == 'de' else 'Kind regards,'),
        signature=name,
    )


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
            if review.unit_id is not None and unit_by_id(docs[review.document], review.unit_id) is None:
                findings.append(_finding(review.document, 'review_format', 'The review referred to an unknown unit.'))
                continue
            findings.append(_finding(
                review.document, f'review_{review.category}', _plain(review.message, 500),
                unit_id=review.unit_id, severity=review.severity, suggestion=review.suggestion,
            ))
        except (ValidationError, _InvalidResponse):
            malformed += 1
    for item in reply.patches:
        try:
            proposed = _ReviewPatch.model_validate(item)
            text = _plain(proposed.text)
        except (ValidationError, _InvalidResponse):
            malformed += 1
            continue
        kind = proposed.document
        doc = docs[kind]
        unit = unit_by_id(doc, proposed.unit_id)
        if unit is None:
            findings.append(_finding(kind, 'review_format', 'The review proposed a patch for an unknown unit.'))
            continue
        key = (kind, unit.id)
        if text == unit.text:
            continue
        if proposed.kind in ('rephrase', 'shorten') and unit.origin == 'ai' and not unit.hidden and key not in touched:
            candidate = apply_patch(doc, unit.id, text, 'ai')
            if not _high(ground(candidate)) - _high(ground(doc)):
                docs[kind] = candidate
                touched.add(key)
                applied.append({'document': kind, 'unit_id': unit.id, 'kind': proposed.kind})
                continue
        code = 'review_coverage' if proposed.kind == 'content' else 'review_style'
        findings.append(_finding(kind, code, 'Review suggested a manual wording change.',
                                 unit_id=unit.id, suggestion=text))
    return docs, findings, applied, malformed


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
    digest = _input_digest(inp, base, evidence)
    stages = {}
    renders = {}
    applied = []
    malformed = 0

    def ground(doc: Document) -> list[Finding]:
        return _ground(doc, evidence, inp, base)

    def publish(kind: DocumentKind, doc: Document, findings: list[Finding]) -> None:
        _check_cancelled(hooks.cancelled)
        hooks.publish(kind, doc.model_dump(), _numbered([f for f in findings if f.document == kind]))

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
    if checkpoint is not None:
        cv, cv_findings, renders['cv'] = checkpoint.document, checkpoint.findings, checkpoint.render
        draft_findings = checkpoint.draft_findings
        resume(stats, checkpoint)
    else:
        entries = _cv_entries(base, evidence)
        skills = [{'section_id': s.id, 'units': [u.model_dump() for u in s.units]}
                  for s in base.sections if s.kind == 'skills']
        prompt = _prompt('cv', inp, evidence, entries=entries, skills=skills)
        try:
            cv, draft_findings = _generate(generate, prompt, _CvResponse,
                                           lambda output: _cv_draft(output, base, entries), stats, hooks.cancelled)
            state = 'completed'
        except GenerationError as error:
            if not error.retryable:
                raise
            cv = base
            draft_findings = [_finding('cv', 'review_format', 'CV tailoring stopped; the base CV is available.')]
            stats['error'] = error.code
            state = 'fallback'
        renders['cv'], render_findings = _render('cv', cv, render)
        cv_findings = draft_findings + ground(cv) + render_findings
        publish('cv', cv, cv_findings)
        timing = complete(stats, started, state)
        if state == 'completed':
            hooks.save(key, _CvCheckpoint(document=cv, findings=cv_findings, draft_findings=draft_findings,
                                          render=renders['cv'],
                                          **timing).model_dump())

    started, key, stats = start('letter')
    cv_digest = _digest(cv.model_dump())
    checkpoint = _load(hooks, key, _LetterCheckpoint)
    if checkpoint is not None and checkpoint.cv_digest == cv_digest:
        letter, letter_findings, renders['letter'] = checkpoint.document, checkpoint.findings, checkpoint.render
        resume(stats, checkpoint)
    else:
        prompt = _prompt('letter', inp, evidence, cv_units=_units(cv, visible_only=True))
        letter = _generate(generate, prompt, _LetterResponse, lambda output: _letter_draft(output, inp, base),
                           stats, hooks.cancelled)
        renders['letter'], render_findings = _render('letter', letter, render)
        letter_findings = ground(letter) + render_findings
        publish('letter', letter, letter_findings)
        timing = complete(stats, started, 'completed')
        hooks.save(key, _LetterCheckpoint(document=letter, cv_digest=cv_digest, findings=letter_findings,
                                          render=renders['letter'], **timing).model_dump())

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
            try:
                reviewed, review_findings, applied, malformed = _generate(
                    generate, prompt, _ReviewResponse, lambda output: _review(output, docs, ground),
                    stats, hooks.cancelled,
                )
                state = 'completed'
            except GenerationError as error:
                if not error.retryable:
                    raise
                reviewed, review_findings = docs, []
                stats['error'] = error.code
                state = 'skipped'
            final_findings = {'cv': cv_findings, 'letter': letter_findings}
            for kind, doc in reviewed.items():
                if doc != docs[kind]:
                    renders[kind], render_findings = _render(kind, doc, render)
                    final_findings[kind] = (draft_findings if kind == 'cv' else []) + ground(doc) + render_findings
            docs = reviewed
            findings = final_findings['cv'] + final_findings['letter'] + review_findings
            for kind, doc in docs.items():
                publish(kind, doc, findings)
            timing = complete(stats, started, state)
            if state == 'completed':
                hooks.save(key, _ReviewCheckpoint(cv=docs['cv'], letter=docs['letter'], findings=findings,
                                                  upstream_digest=upstream_digest, render=renders,
                                                  applied_patches=applied, malformed_items=malformed,
                                                  **timing).model_dump())

    _check_cancelled(hooks.cancelled)
    hooks.stage('finalize')
    telemetry = {
        'attempts': {name: stats['attempts'] for name, stats in stages.items()},
        'stage_seconds': {name: stats['seconds'] for name, stats in stages.items()},
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
