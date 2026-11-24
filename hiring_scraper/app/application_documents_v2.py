"""Versioned structured application documents: storage, API and worker publication.

Documents are ``CvDocument`` / ``LetterDocument`` JSON (``hiring_scraper.documents``)
stored per (profile, job, kind) as immutable versions. Saves use compare-and-set
on ``expected_version``: the new row is ``expected_version + 1`` and a primary
key conflict, or a newer row, is a 409. The newest ``KEEP_VERSIONS`` versions
are kept; PDFs and render tasks of pruned versions cascade away.

Preparation publication follows the lock order
jobs -> profiles -> derived rows and never overwrites a user's edit made
while the model was running: AI output replaces the latest version only when
that version is the one the task started from or the one it published itself.
Otherwise the output is *held* on the task record (``record.held[kind]``) for the
user to compare and adopt with an ordinary save. Document text is never logged.
"""
from __future__ import annotations

from datetime import date, timezone
from hashlib import sha256
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from hiring_scraper.app import application_preparation_queue as queue, source_changes
from hiring_scraper.app.database import SessionLocal, dialect_insert, get_session, retry_transient
from hiring_scraper.app.models import (
    ApplicationDocument, ApplicationDocumentRender, Job, ProfileCV,
    ProfileIndexChanged, ProfilePhoto, UserProfile, hold_profiles, utcnow,
)
from hiring_scraper.app.profiles import require_profile
from hiring_scraper.documents.schema import CvDocument, LetterDocument, unit_by_id, walk_units

LOG = logging.getLogger(__name__)
NO_STORE = {'Cache-Control': 'no-store'}


class NoStoreRoute(APIRoute):
    """Mark every response private to this request, error responses included."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def no_store(request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **NO_STORE}
                raise
            response.headers['Cache-Control'] = 'no-store'
            return response

        return no_store


router = APIRouter(prefix='/api/v1/profiles', tags=['application-documents-v2'], route_class=NoStoreRoute)
KINDS = ('cv', 'letter')
KEEP_VERSIONS = 20
MODELS = {'cv': CvDocument, 'letter': LetterDocument}
Kind = Literal['cv', 'letter']
Language = Literal['en', 'de']


class VersionConflict(Exception):
    """Another save created the version this save expected to create."""


# --- Pure helpers -----------------------------------------------------------------

def next_version(expected_version: int) -> int:
    """The version a save creates; ``0`` means "no version exists yet"."""
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
        raise ValueError('expected_version must be a non-negative integer')
    return expected_version + 1


def prune_floor(version: int, keep: int = KEEP_VERSIONS) -> int:
    """Versions ``<=`` this are deleted after ``version`` is saved."""
    return version - keep


def validate_body(kind: str, body) -> dict:
    """Validate ``body`` as the document ``kind`` and return canonical JSON."""
    if kind not in MODELS:
        raise ValueError(f'Unknown document kind: {kind!r}')
    return MODELS[kind].model_validate(body).model_dump(mode='json')


def _unit_texts(kind, body) -> dict[str, tuple[str, str]]:
    try:
        document = MODELS[kind].model_validate(body)
    except ValidationError:
        return {}
    return {unit.id: (unit.lead_in, unit.text) for unit in walk_units(document)}


def carry_findings(kind, previous_body, previous_findings, body) -> list[dict]:
    """Keep findings whose unit is unchanged (and document-level ones) across a user save."""
    before, after = _unit_texts(kind, previous_body), _unit_texts(kind, body)
    kept = []
    for finding in previous_findings or []:
        unit_id = finding.get('unit_id') if isinstance(finding, dict) else None
        if unit_id is None or (unit_id in after and before.get(unit_id) == after[unit_id]):
            kept.append(finding)
    return kept


def should_publish(latest_version, base_version, published_version) -> bool:
    """Whether AI output may become the latest version (see module docstring)."""
    return latest_version is None or latest_version in {base_version or 0, published_version}


def public_task(record: dict | None) -> dict | None:
    if not record:
        return None
    task = {key: record.get(key) for key in (
        'id', 'kind', 'state', 'stage', 'attempts', 'error_code', 'error', 'next_attempt_at', 'stages', 'failed_stage')}
    task['kind'] = task['kind'] or 'legacy'
    task['stages'] = task['stages'] or {}
    task['attempts'] = task['attempts'] or 0
    if record.get('kind') in queue.V2_KINDS:
        task['held'] = {kind: {key: value for key, value in held.items() if key != 'input_digest'}
                        for kind, held in (record.get('held') or {}).items()}
        task['published'] = record.get('published') or {}
        task['result'] = record.get('result')
        request = record.get('request') or {}
        if record['kind'] == 'prepare':
            task['preparation'] = {'language': request.get('language', 'en'), 'mode': request.get('mode', 'full')}
        else:
            task['revision'] = {key: request.get(key) for key in ('document', 'unit_id', 'instruction', 'base_version')}
    return task


# --- Storage ----------------------------------------------------------------------

def _version_identity(row):
    return {'version': row.version, 'revision': row.revision, 'created_at': row.created_at.astimezone(timezone.utc).isoformat(timespec='microseconds')}


def document_json(row: ApplicationDocument | None, *, fresh_digest=None) -> dict | None:
    if row is None:
        return None
    return {**_version_identity(row), 'language': row.language, 'body': row.body, 'findings': row.findings or [],
            'origin': row.origin, 'stale_inputs': _is_stale(row.stale_inputs, row.input_digest, fresh_digest)}


def latest(session, profile_id, job_id, kind) -> ApplicationDocument | None:
    return session.scalars(select(ApplicationDocument).where(
        ApplicationDocument.profile_id == profile_id, ApplicationDocument.job_id == job_id,
        ApplicationDocument.kind == kind,
    ).order_by(ApplicationDocument.version.desc()).limit(1)).first()


def _is_stale(stale, digest, fresh_digest):
    return stale or (digest is not None and digest != fresh_digest)


def latest_pair(session, profile_id, job_id) -> dict:
    digests = {}
    documents = {}
    for kind in KINDS:
        row = latest(session, profile_id, job_id, kind)
        if row and row.input_digest and not row.stale_inputs and row.language not in digests:
            digests[row.language] = _fresh_digest(session, profile_id, job_id, row.language)
        documents[kind] = document_json(row, fresh_digest=digests.get(row.language) if row else None)
    return documents


def get_version(session, profile_id, job_id, kind, version) -> ApplicationDocument | None:
    return session.get(ApplicationDocument, (profile_id, job_id, kind, version))


def list_versions(session, profile_id, job_id, kind) -> list[dict]:
    rows = session.execute(select(
        ApplicationDocument.version, ApplicationDocument.origin, ApplicationDocument.language,
        ApplicationDocument.stale_inputs, ApplicationDocument.created_at, ApplicationDocument.input_digest,
        ApplicationDocument.revision,
    ).where(ApplicationDocument.profile_id == profile_id, ApplicationDocument.job_id == job_id,
            ApplicationDocument.kind == kind).order_by(ApplicationDocument.version.desc())).all()
    digests = {language: _fresh_digest(session, profile_id, job_id, language)
               for language in {row.language for row in rows if row.input_digest and not row.stale_inputs}}
    return [{'version': version, 'revision': revision, 'origin': origin, 'language': language,
             'stale_inputs': _is_stale(stale, digest, digests.get(language)), 'created_at': created.astimezone(timezone.utc).isoformat(timespec='microseconds')}
            for version, origin, language, stale, created, digest, revision in rows]


def save_version(session, profile_id, job_id, kind, body, *, expected_version, origin,
                 findings=(), stale_inputs=False, input_digest=None) -> int:
    """Insert ``expected_version + 1`` or raise ``VersionConflict``; the caller commits.

    The max check rejects stale expectations even after old versions were
    pruned; the primary key rejects two concurrent saves of the same version.
    """
    document = validate_body(kind, body)
    version = next_version(expected_version)
    current = session.scalar(select(func.max(ApplicationDocument.version)).where(
        ApplicationDocument.profile_id == profile_id, ApplicationDocument.job_id == job_id,
        ApplicationDocument.kind == kind)) or 0
    if current != expected_version:
        raise VersionConflict(current)
    insert = dialect_insert(session)
    created = session.scalar(insert(ApplicationDocument).values(
        profile_id=profile_id, job_id=job_id, kind=kind, version=version, language=document['language'],
        body=document, findings=list(findings or []), origin=origin, stale_inputs=bool(stale_inputs),
        input_digest=input_digest, created_at=utcnow(),
    ).on_conflict_do_nothing().returning(ApplicationDocument.version))
    if created is None:
        raise VersionConflict(version)
    session.execute(delete(ApplicationDocument).where(
        ApplicationDocument.profile_id == profile_id, ApplicationDocument.job_id == job_id,
        ApplicationDocument.kind == kind, ApplicationDocument.version <= prune_floor(version)))
    return version


def revert(session, profile_id, job_id, kind, version, *, expected_version=None) -> int:
    """Save a copy of an older version as the new latest version (origin ``user``)."""
    source = get_version(session, profile_id, job_id, kind, version)
    if source is None:
        raise HTTPException(404, 'This document version is no longer stored.')
    current = latest(session, profile_id, job_id, kind)
    if expected_version is not None and current.version != expected_version:
        raise VersionConflict(current.version)
    return save_version(session, profile_id, job_id, kind, source.body, expected_version=current.version,
                        origin='user', findings=source.findings, stale_inputs=source.stale_inputs,
                        input_digest=source.input_digest)


def mark_stale(session, profile_id, job_id=None) -> None:
    """Flag stored documents whose inputs (base CV, profile) changed."""
    statement = update(ApplicationDocument).where(
        ApplicationDocument.profile_id == profile_id, ApplicationDocument.stale_inputs.is_(False))
    if job_id is not None:
        statement = statement.where(ApplicationDocument.job_id == job_id)
    session.execute(statement.values(stale_inputs=True))


def get_render(session, profile_id, job_id, kind, version, template) -> ApplicationDocumentRender | None:
    return session.get(ApplicationDocumentRender, (profile_id, job_id, kind, version, template))


# --- Inputs -----------------------------------------------------------------------

_LOCALIZED_KEYS = ('summary', 'custom_instructions', 'application_personalization', 'skills', 'skill_evidence',
                   'education', 'certifications')


def localized_preferences(preferences: dict | None, language: str) -> dict:
    """Saved preferences with the language's translated fields applied (as v1 does)."""
    preferences = dict(preferences or {})
    translated = (preferences.get('localized_content') or {}).get(language) or {}
    shared_voice = {key: value for key, value in (preferences.get('application_personalization') or {}).items()
                    if key in {'voice_notes', 'style_exemplars'}}
    for key in _LOCALIZED_KEYS:
        if key in translated:
            preferences[key] = translated[key]
    if shared_voice:
        preferences['application_personalization'] = {
            **(preferences.get('application_personalization') or {}), **shared_voice,
        }
    details = dict(preferences.get('application_details') or {})
    details['preferences'] = {**(details.get('preferences') or {}), **translated.get('application_preferences', {})}
    preferences['application_details'] = details
    return preferences


def base_cv_document(cv: ProfileCV | None, language: str) -> tuple[dict | None, str, list[str]]:
    """``(document, source, errors)`` for the language's base CV, importing when not stored."""
    if cv is None:
        return None, 'imported', ['Attach a CV to this profile first.']
    variant = (cv.variants or {}).get(language)
    if variant and isinstance(variant.get('document'), dict):
        return variant['document'], 'stored', []
    try:
        if variant and variant.get('text'):
            from hiring_scraper.documents.import_tex import import_marked_tex
            return import_marked_tex(variant['text'], language=language).model_dump(mode='json'), 'imported', []
        if not variant and (cv.filename or '').lower().endswith('.docx'):
            from hiring_scraper.documents import import_docx
            return import_docx.import_docx(cv.data, language=language).model_dump(mode='json'), 'imported', []
    except (ImportError, ValueError) as error:
        # import_tex reports unsupported LaTeX as ImportError with a line number.
        return None, 'imported', [str(error)[:500]]
    return None, 'imported', [f'Attach the {"English" if language == "en" else "German"} base CV first.']


def snapshot_inputs(session, profile_id, job_id, language, mode='full') -> tuple[dict, int]:
    """Read the preparation inputs without locks; returns ``(input fields, profile source_version)``."""
    profile = session.get(UserProfile, profile_id)
    job = session.get(Job, job_id)
    if profile is None or job is None:
        raise queue.TaskError('not_found', 'The profile or job no longer exists.')
    if not job.is_active or job.closed_at is not None:
        raise queue.TaskError('job_closed', 'This posting is closed. Check the original job first.')
    document, _, errors = base_cv_document(session.get(ProfileCV, profile_id), language)
    if document is None:
        raise queue.TaskError('base_cv_unavailable', errors[0] if errors else 'Attach a base CV first.')
    from hiring_scraper.app.search_snapshots import job_employer_name

    company = job_employer_name(job)
    return {
        'base_cv': document,
        'profile': localized_preferences(profile.preferences, language),
        'job': {'title': job.title, 'company_name': company, 'location': job.location_text or '',
                'url': job.url, 'description': job.description or ''},
        'language': language, 'mode': mode, 'today': date.today(),
    }, profile.source_version


def _preparation_input(fields):
    from hiring_scraper.app.application_pipeline import PreparationInput
    return PreparationInput(**fields)


def _fresh_digest(session, profile_id, job_id, language):
    from hiring_scraper.app.application_pipeline import input_digest
    try:
        fields, _ = snapshot_inputs(session, profile_id, job_id, language)
    except queue.TaskError:
        return None  # The job closed or the source CV was removed.
    return input_digest(_preparation_input(fields))


def _hold_publication(session, profile_id, job_id):
    """Lock order jobs -> profiles; the caller then writes derived rows."""
    # FOR SHARE conflicts with the crawler's FOR NO KEY UPDATE.
    session.execute(select(Job.id).where(Job.id == job_id).with_for_update(read=True))
    hold_profiles(session, [profile_id], wait=False)


# --- Worker: prepare --------------------------------------------------------------

def _publish_stage(profile_id, job_id, progress, digest, request, key, checkpoint, outputs):
    language = request['language']

    def attempt():
        with SessionLocal.begin() as session:
            _hold_publication(session, profile_id, job_id)
            fresh = _fresh_digest(session, profile_id, job_id, language)
            row = progress.fenced(session)
            if row is None:
                raise HTTPException(409, 'Preparation cancelled.')
            record = row.record
            checkpoints = dict(record.get('checkpoints') or {})
            if checkpoint is not None and checkpoints.get(key) == checkpoint:
                return checkpoints  # A previous commit can succeed before its connection drops.
            published = dict(record.get('published') or {})
            published_revisions = dict(record.get('published_revisions') or {})
            held = dict(record.get('held') or {})
            for kind, body, findings in outputs:
                current = latest(session, profile_id, job_id, kind)
                base = (record.get('base_revisions') or {}).get(kind)
                if should_publish(current.revision if current else None, base, published_revisions.get(kind)):
                    published[kind] = save_version(
                        session, profile_id, job_id, kind, body, expected_version=current.version if current else 0,
                        origin='ai', findings=findings, stale_inputs=fresh != digest, input_digest=digest)
                    published_revisions[kind] = get_version(session, profile_id, job_id, kind, published[kind]).revision
                    held.pop(kind, None)
                else:
                    held[kind] = {'body': validate_body(kind, body), 'findings': list(findings or []),
                                  'latest_version': current.version, 'stale_inputs': fresh != digest,
                                  'input_digest': digest}
            if checkpoint is not None:
                checkpoints[key] = checkpoint
            progress.store(session, row, published=published, published_revisions=published_revisions,
                           held=held, checkpoints=checkpoints)
            return checkpoints

    progress.checkpoints = retry_transient(
        attempt, action='publishing an application document stage', retry_on=(ProfileIndexChanged, VersionConflict))


def run_prepare(profile_id, request, progress):
    from hiring_scraper.app import application_pipeline as pipeline
    job_id, language, mode = request['job_id'], request['language'], request.get('mode', 'full')
    with SessionLocal() as session:
        fields, _ = snapshot_inputs(session, profile_id, job_id, language, mode)
        photo = _photo(session, profile_id) if fields['base_cv'].get('photo') else None
    inputs = _preparation_input(fields)
    digest = pipeline.input_digest(inputs)
    render = None
    from hiring_scraper.app import application_pdf
    if application_pdf.available():
        def render(kind, document):
            return application_pdf.render_document(kind, document, photo_png=photo if kind == 'cv' else None)
    hooks = pipeline.StageHooks(
        load=progress.load, stage=progress, cancelled=progress.cancelled,
        commit=lambda key, checkpoint, outputs: _publish_stage(
            profile_id, job_id, progress, digest, request, key, checkpoint, outputs),
    )
    generate = pipeline.codex_generate(model=request.get('model') or None, cancelled=progress.cancelled)
    result = pipeline.prepare_documents(inputs, generate, hooks, render)
    return {'published': progress.record.get('published') or {}, 'held': sorted(progress.record.get('held') or {}),
            'input_digest': digest, 'telemetry': result.telemetry}


# --- Worker: revise ---------------------------------------------------------------

def run_revise(profile_id, request, progress):
    from hiring_scraper.app import application_pipeline as pipeline
    job_id, kind, unit_id = request['job_id'], request['document'], request['unit_id']
    with SessionLocal() as session:
        source = get_version(session, profile_id, job_id, kind, request['base_version'])
        if source is None or source.revision != request.get('base_revision'):
            raise queue.TaskError('document_missing', 'This document version is no longer stored.')
        body = source.body
        fields, _ = snapshot_inputs(session, profile_id, job_id, source.language)
    progress('revising')
    generate = pipeline.codex_generate(model=request.get('model') or None, cancelled=progress.cancelled)
    revision = pipeline.revise_unit(body, kind, unit_id, request['instruction'], _preparation_input(fields), generate)
    original = unit_by_id(MODELS[kind].model_validate(body), unit_id)
    return {'applied': False, 'document': kind, 'base_version': request['base_version'],
            'base_revision': source.revision, 'unit_id': unit_id, 'original_text': original.text, 'text': revision['text'],
            'sources': list(revision.get('sources') or []), 'findings': list(revision.get('findings') or [])}


# --- Worker: render lane ----------------------------------------------------------

def _photo(session, profile_id):
    photo = session.get(ProfilePhoto, profile_id)
    return photo.data if photo is not None else None


def render_key(session, profile_id, kind, body):
    template = body.get('template', 'classic-serif-v1')
    if kind != 'cv' or not body.get('photo'):
        return template
    # The existing template column is the render discriminator. Include the
    # exact portrait bytes (or absence) in that key for portrait-enabled CVs.
    photo = _photo(session, profile_id) or b''
    return sha256(template.encode() + b'\x00' + photo).hexdigest()[:40]


def render_for_worker(profile_id, kind, body):
    from hiring_scraper.app import application_pdf
    photo = None
    if kind == 'cv' and body.get('photo'):
        with SessionLocal() as session:
            photo = _photo(session, profile_id)
    return application_pdf.render_document(kind, body, photo_png=photo)


# --- API ----------------------------------------------------------------------------

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PrepareRequest(StrictModel):
    job_id: int = Field(gt=0)
    language: Language = 'en'
    mode: Literal['full', 'fast'] = 'full'


class SaveRequest(StrictModel):
    expected_version: int = Field(ge=0)
    expected_revision: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    body: dict
    dismissed_findings: list[str] = Field(default_factory=list, max_length=1000)
    held_task_id: str | None = Field(default=None, min_length=1, max_length=64)


class RevertRequest(StrictModel):
    version: int = Field(gt=0)
    expected_version: int = Field(gt=0)
    expected_revision: str = Field(pattern=r'^[0-9a-f]{32}$')


class ReviseRequest(StrictModel):
    unit_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9-]{1,64}$')
    instruction: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(gt=0)
    expected_revision: str = Field(pattern=r'^[0-9a-f]{32}$')


class StructureRequest(StrictModel):
    expected_revision: str = Field(pattern=r'^[0-9]{1,19}$')
    document: dict


def _json(content, status_code=200):
    return JSONResponse(content, status_code=status_code, headers=NO_STORE)


def _require_job(session, job_id):
    if session.get(Job, job_id, with_for_update={'read': True, 'key_share': True}) is None:
        raise HTTPException(404, 'Job not found.')


def _hold_cv(session, profile_id, *, exclusive=False):
    profile = session.get(UserProfile, profile_id, with_for_update={'read': not exclusive}, populate_existing=True)
    if profile is None:
        raise HTTPException(404, 'Profile not found.')
    cv = session.get(ProfileCV, profile_id, populate_existing=True)
    if cv is None:
        raise HTTPException(404, 'Attach a CV to this profile first.')
    return cv


def _validated(kind, body):
    try:
        return validate_body(kind, body)
    except ValidationError as error:
        raise HTTPException(422, f'The {kind} document is invalid: {error.errors()[0]["msg"]}') from None


def _base_revisions(session, profile_id, job_id):
    return {kind: row.revision for kind in KINDS if (row := latest(session, profile_id, job_id, kind))}


@router.post('/{profile_id}/application-documents/prepare', status_code=202)
def start_preparation(profile_id: int, request: PrepareRequest, session: Session = Depends(get_session)):
    from hiring_scraper.app import codex_runtime
    if not codex_runtime.status()['enabled']:
        raise HTTPException(503, 'Connect ChatGPT in Radius settings before preparing documents.')
    require_profile(profile_id, session)
    try:
        snapshot_inputs(session, profile_id, request.job_id, request.language, request.mode)
    except queue.TaskError as error:
        status = 404 if error.code == 'not_found' else 409 if error.code == 'job_closed' else 422
        raise HTTPException(status, error.message) from None
    base_revisions = _base_revisions(session, profile_id, request.job_id)
    session.rollback()
    payload = {**request.model_dump(), 'model': codex_runtime.selected_model() or ''}
    task = queue.enqueue_task(profile_id, request.job_id, 'prepare', payload, extra={'base_revisions': base_revisions})
    return _json({'task': public_task(task)}, 202)


@router.get('/{profile_id}/application-documents/task')
def preparation_task(profile_id: int, job_id: int, summary: bool = False,
                     session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    record = queue.status(session, profile_id, job_id)
    if summary:
        task = ({key: record.get(key) for key in ('kind', 'state', 'stage', 'error')}
                if record and record.get('kind') == 'prepare' else None)
    else:
        task = public_task(record)
    return _json({'task': task})


@router.post('/{profile_id}/application-documents/task/cancel')
def cancel_task(profile_id: int, job_id: int, task_id: str = Query(min_length=1, max_length=64),
                session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    return _json({'task': public_task(queue.cancel(session, profile_id, job_id, task_id))})


@router.get('/{profile_id}/application-documents')
def get_documents(profile_id: int, job_id: int, session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    return _json(latest_pair(session, profile_id, job_id))


@router.get('/{profile_id}/application-documents/{kind}/versions')
def get_versions(profile_id: int, kind: Kind, job_id: int, session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    return _json({'versions': list_versions(session, profile_id, job_id, kind)})


@router.put('/{profile_id}/application-documents/{kind}')
def save_document(profile_id: int, kind: Kind, job_id: int, request: SaveRequest,
                  session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    _require_job(session, job_id)
    body = _validated(kind, request.body)
    _hold_cv(session, profile_id)
    previous = get_version(session, profile_id, job_id, kind, request.expected_version) if request.expected_version else None
    if (request.expected_version and (previous is None or previous.revision != request.expected_revision)
            or not request.expected_version and request.expected_revision is not None):
        raise HTTPException(409, 'This document changed. Reload before saving.')
    findings = carry_findings(kind, previous.body, previous.findings, body) if previous else []
    stale, digest = (previous.stale_inputs, previous.input_digest) if previous else (False, None)
    if request.held_task_id:
        record = queue.status(session, profile_id, job_id) or {}
        held = (record.get('held') or {}).get(kind) if record.get('id') == request.held_task_id else None
        if held is None or body != held['body']:
            raise HTTPException(409, 'This generated suggestion changed. Reload to see the latest result.')
        findings = held.get('findings') or []
        stale = stale or held.get('stale_inputs', False)
        digest = held.get('input_digest')
    dismissed = set(request.dismissed_findings)
    try:
        version = save_version(session, profile_id, job_id, kind, body, expected_version=request.expected_version,
                               origin='user', findings=[item for item in findings if item.get('id') not in dismissed],
                               stale_inputs=stale, input_digest=digest)
        identity = _version_identity(get_version(session, profile_id, job_id, kind, version))
        session.commit()
    except VersionConflict:
        session.rollback()
        raise HTTPException(409, 'This document changed in another tab or by the assistant. Reload to continue.') from None
    return _json(identity)


@router.post('/{profile_id}/application-documents/{kind}/revert')
def revert_document(profile_id: int, kind: Kind, job_id: int, request: RevertRequest,
                    session: Session = Depends(get_session)):
    _require_job(session, job_id)
    _hold_cv(session, profile_id)
    current = latest(session, profile_id, job_id, kind)
    if current is None or current.revision != request.expected_revision:
        raise HTTPException(409, 'This document changed meanwhile. Reload and try again.')
    try:
        version = revert(session, profile_id, job_id, kind, request.version,
                         expected_version=request.expected_version)
        session.commit()
    except VersionConflict:
        session.rollback()
        raise HTTPException(409, 'This document changed meanwhile. Reload and try again.') from None
    return _json({'version': version})


@router.post('/{profile_id}/application-documents/{kind}/revise', status_code=202)
def revise_document(profile_id: int, kind: Kind, job_id: int, request: ReviseRequest,
                    session: Session = Depends(get_session)):
    from hiring_scraper.app import codex_runtime
    if not codex_runtime.status()['enabled']:
        raise HTTPException(503, 'Connect ChatGPT in Radius settings before revising documents.')
    require_profile(profile_id, session)
    current = latest(session, profile_id, job_id, kind)
    if current is None:
        raise HTTPException(404, 'Prepare this document first.')
    if (request.expected_version, request.expected_revision) != (current.version, current.revision):
        raise HTTPException(409, 'This document changed. Reload before requesting a revision.')
    if unit_by_id(MODELS[kind].model_validate(current.body), request.unit_id) is None:
        raise HTTPException(422, 'This part of the document no longer exists. Reload to continue.')
    payload = {'job_id': job_id, 'document': kind, 'unit_id': request.unit_id, 'instruction': request.instruction,
               'base_version': current.version, 'base_revision': current.revision, 'model': codex_runtime.selected_model() or ''}
    session.rollback()
    task = queue.enqueue_task(profile_id, job_id, 'revise', payload)
    return _json({'task': public_task(task)}, 202)


@router.get('/{profile_id}/application-documents/{kind}/pdf')
def document_pdf(profile_id: int, kind: Kind, job_id: int, version: int = Query(gt=0), retry: bool = False,
                 revision: str | None = Query(default=None, pattern=r'^[0-9a-f]{32}$'),
                 session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    if kind == 'cv':
        hold_profiles(session, [profile_id])
    # Queue inserts reference this version. Keep it through enqueue so a concurrent
    # prune or CV deletion cannot leave the render task without its parent.
    document = session.get(ApplicationDocument, (profile_id, job_id, kind, version),
                           with_for_update={'read': True, 'key_share': True})
    if document is None:
        raise HTTPException(404, 'This document version is no longer stored.')
    if revision is not None and revision != document.revision:
        raise HTTPException(409, 'This document changed. Open the latest version before downloading.')
    template = render_key(session, profile_id, kind, document.body)
    from hiring_scraper.app.application_pdf import export_verified

    cached = get_render(session, profile_id, job_id, kind, version, template)
    if cached is not None and export_verified(cached.checks):
        return Response(cached.pdf, media_type='application/pdf', headers={
            **NO_STORE, 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': f'attachment; filename="{kind}-{version}.pdf"'})
    if cached is not None:
        session.delete(cached)
        session.flush()
    state = queue.enqueue_render(session, profile_id, job_id, kind, version, template, retry=retry)
    session.commit()
    if state['state'] == 'failed':
        detail = ('The PDF did not pass its readability checks. Review the name, contact details and text before retrying.'
                  if state['error_code'] == 'render_verification_failed'
                  else 'This document could not be rendered. Try again, or edit the document.')
        return _json({'state': 'failed', 'error_code': state['error_code'], 'detail': detail}, 422)
    return _json({'state': 'rendering'}, 202)


@router.get('/{profile_id}/cv/structure')
def get_cv_structure(profile_id: int, language: Language = 'en', session: Session = Depends(get_session)):
    require_profile(profile_id, session)
    versions = hold_profiles(session, [profile_id])
    document, source, errors = base_cv_document(session.get(ProfileCV, profile_id), language)
    return _json({'document': document, 'source': source, 'errors': errors,
                  'revision': str(versions[profile_id]) if profile_id in versions else None})


@router.put('/{profile_id}/cv/structure')
def put_cv_structure(profile_id: int, request: StructureRequest, language: Language = 'en',
                     session: Session = Depends(get_session)):
    from hiring_scraper.app.profiles import _cv_career_facts
    from hiring_scraper.app.tex_cv import history_facts, parse_tex_cv
    from hiring_scraper.documents.project_tex import project_tex
    require_profile(profile_id, session)
    body = _validated('cv', request.document)
    if body['language'] != language:
        raise HTTPException(422, 'The CV language does not match the selected base CV.')
    cv = _hold_cv(session, profile_id, exclusive=True)
    if str(hold_profiles(session, [profile_id])[profile_id]) != request.expected_revision:
        raise HTTPException(409, 'The base CV changed. Reload it before saving.')
    text = project_tex(CvDocument.model_validate(body))
    try:
        history = parse_tex_cv(text)
        for other_language, variant in (cv.variants or {}).items():
            if other_language != language and history_facts(history) != history_facts(parse_tex_cv(variant['text'])):
                raise HTTPException(422, 'English and German CVs must use the same history IDs, employers and dates.')
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    previous_facts = _cv_career_facts(cv)
    variant = (cv.variants or {}).get(language) or {}
    cv.variants = {**(cv.variants or {}), language: {
        **variant, 'filename': variant.get('filename') or f'cv-{language}.tex', 'text': text, 'document': body}}
    cv.updated_at = utcnow()
    # Profile (source) row first, then derived rows: the same order publishers use.
    source_changes.profile_changed(session, profile_id, career_facts_changed=_cv_career_facts(cv) != previous_facts)
    mark_stale(session, profile_id)
    revision = str(hold_profiles(session, [profile_id])[profile_id])
    session.commit()
    return _json({'document': body, 'source': 'stored', 'errors': [], 'revision': revision})
