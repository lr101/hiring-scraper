"""PostgreSQL preparation queue shared by the API and preparation worker.

Two lanes share one worker process:

* **Inference lane** (``application_preparation_tasks``): at most one active
  task per (profile, job), for preparation or a unit revision.
* **Render lane** (``application_render_tasks``): one row per document version
  and template. PDF exports are claimed by their own worker thread, so a
  render never waits behind a model call that runs for minutes.

Leases are short (``LEASE``) and renewed while the owner makes progress:
``Progress.cancelled()`` renews at most every ``RENEW_EVERY`` seconds and every
checkpoint or stage update renews too. Every update is fenced on the task id
and the lease token, so a worker whose lease expired cannot overwrite the
replacement's state. Transient failures wait ``BACKOFF`` before the next
attempt (the task stays active but is not claimable until ``next_attempt_at``).
"""
from __future__ import annotations

from datetime import timedelta
import logging
import sys
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text

from hiring_scraper.app.database import SessionLocal, is_transient_database_error, retry_transient
from hiring_scraper.app.models import (
    ApplicationDocument, ApplicationDocumentRender, ApplicationPreparationTask as Task,
    ApplicationRenderTask as RenderTask, ProfileIndexChanged, hold_profiles, utcnow,
)

LOG = logging.getLogger(__name__)
ACTIVE = ('running', 'cancelling')
V2_KINDS = ('prepare', 'revise')
LEASE = timedelta(minutes=6)
RENEW_EVERY = 60.0  # seconds between lease renewals from cancelled()
RETENTION = timedelta(days=7)
# Delay before attempt n + 1 after attempt n failed transiently; len + 1 attempts in total.
BACKOFF = (timedelta(minutes=2), timedelta(minutes=10))
MAX_ATTEMPTS = len(BACKOFF) + 1
MAX_ACTIVE = 8

RENDER_LEASE = timedelta(minutes=2)
RENDER_BACKOFF = (timedelta(seconds=15), timedelta(minutes=1))


class TaskError(Exception):
    """A typed task failure raised by worker code; ``message`` is shown to the user."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.message = message
        self.retryable = retryable


def retry_delay(attempts: int, schedule=BACKOFF) -> timedelta | None:
    """Delay before the next attempt after ``attempts`` failed ones, or None when exhausted."""
    if attempts < 1 or attempts > len(schedule):
        return None
    return schedule[attempts - 1]


class RenewalClock:
    """Throttle lease renewals to at most one per ``interval`` seconds."""

    def __init__(self, interval: float = RENEW_EVERY, clock=time.monotonic):
        self.interval = interval
        self.clock = clock
        self.last = clock()

    def due(self) -> bool:
        now = self.clock()
        if now - self.last >= self.interval:
            self.last = now
            return True
        return False

    def reset(self) -> None:
        self.last = self.clock()


def classify(error: BaseException) -> tuple[str, bool, str]:
    """Return ``(error_code, retryable, user message)`` without logging document text.

    Pipeline and render modules are looked up only if already imported: an
    error can only be one of their types once the module is loaded.
    """
    if isinstance(error, TaskError):
        return error.code, error.retryable, error.message
    pipeline = sys.modules.get('hiring_scraper.app.application_pipeline')
    render = sys.modules.get('hiring_scraper.app.application_pdf')
    if pipeline is not None and isinstance(error, getattr(pipeline, 'GenerationError', ())):
        retryable = bool(getattr(error, 'retryable', False))
        message = ('The AI service did not answer in time.' if retryable
                   else 'The AI service could not prepare these documents.')
        return str(getattr(error, 'code', None) or 'generation_failed')[:64], retryable, message
    if render is not None and isinstance(error, getattr(render, 'RenderUnavailable', ())):
        return 'render_unavailable', False, 'PDF rendering is not available on this server.'
    if render is not None and isinstance(error, getattr(render, 'RenderFailed', ())):
        if error.code == 'document_too_large':
            return error.code, False, 'The document is too large to export as a PDF.'
        return (str(getattr(error, 'code', None) or 'render_failed')[:64], True,
                'The document could not be rendered.')
    if isinstance(error, ProfileIndexChanged) or is_transient_database_error(error):
        return 'database_contention', True, 'The database was busy.'
    return 'unexpected', False, 'Preparation failed unexpectedly. Check server logs and try again.'


def _store(row, **changes):
    now = utcnow()
    row.record = {**row.record, **changes, 'updated_at': now.timestamp()}
    row.state = row.record['state']
    row.updated_at = now


def _failure_state(record):
    return 'partial' if record.get('kind') == 'prepare' and (record.get('held') or record.get('published')) else 'failed'


def _reset_row(session, row, profile_id, job_id):
    if row is None:
        row = Task(profile_id=profile_id, job_id=job_id, record={})
        session.add(row)
    row.record = {}
    row.lease_token = row.lease_until = row.next_attempt_at = None
    return row


def _check_capacity(session):
    active = session.scalar(select(func.count()).select_from(Task).where(Task.state.in_(ACTIVE)))
    if active >= MAX_ACTIVE:
        raise HTTPException(429, 'Too many applications are being prepared. Please try again shortly.')


def enqueue_task(profile_id, job_id, kind, request, *, extra=None, automatic=False):
    """Queue a v2 ``prepare`` or ``revise`` task; one active task per (profile, job).

    Re-posting the same request while it is active returns the active task.
    A failed or cancelled task with the same request resumes its checkpoints
    (they are keyed by input digest, so changed inputs never reuse them).
    """
    if kind not in V2_KINDS:
        raise ValueError(f'Unknown task kind: {kind!r}')
    with SessionLocal.begin() as session:
        session.execute(text('SELECT pg_advisory_xact_lock(85793011, 8)'))
        row = session.get(Task, (profile_id, job_id), with_for_update=True, populate_existing=True)
        previous = row.record if row else {}
        same = previous.get('kind') == kind and previous.get('request') == request
        if row and row.state in ACTIVE:
            if not same:
                raise HTTPException(409, 'Another preparation or revision is already running for this job.')
            return previous
        _check_capacity(session)
        row = _reset_row(session, row, profile_id, job_id)
        old_request = previous.get('request') or {}
        same_inputs = same or (kind == previous.get('kind') == 'prepare'
                               and {k: v for k, v in old_request.items() if k != 'mode'}
                               == {k: v for k, v in request.items() if k != 'mode'})
        resume = same_inputs and previous.get('state') in ('failed', 'partial', 'cancelled')
        resumed = {}
        if resume:
            resumed = {key: previous.get(key) or {} for key in (
                'checkpoints', 'held', 'published', 'published_revisions', 'base_revisions',
            )}
        _store(row, **{**(extra or {}), **resumed}, id=uuid4().hex, kind=kind, state='running', stage='queued',
               started_at=time.time(), request=request, automatic=automatic, attempts=0,
               next_attempt_at=None, error_code=None, error=None, stages={})
        return row.record


def status(session, profile_id, job_id):
    row = session.get(Task, (profile_id, job_id))
    return row.record if row else None


def cancel(session, profile_id, job_id, expected_id):
    row = session.get(Task, (profile_id, job_id), with_for_update=True, populate_existing=True)
    if row is None or row.record['id'] != expected_id:
        raise HTTPException(409, 'This preparation changed. Reload to see its current state.')
    if row.state == 'running':
        state = 'cancelling' if row.lease_token else 'cancelled'
        row.next_attempt_at = None
        _store(row, state=state, stage=state, next_attempt_at=None)
    session.commit()
    return row.record


def clear(session, profile_id, job_id=None):
    statement = delete(Task).where(Task.profile_id == profile_id)
    if job_id is not None:
        statement = statement.where(Task.job_id == job_id)
    session.execute(statement)


def has_active(session):
    return session.scalar(select(Task.profile_id).where(Task.state.in_(ACTIVE)).limit(1)) is not None


def _claim():
    now = utcnow()
    with SessionLocal.begin() as session:
        # The state index bounds this to active rows; retention keeps the table small.
        candidate = session.scalars(select(Task).where(
            Task.state.in_(ACTIVE), or_(Task.lease_until.is_(None), Task.lease_until <= now),
            or_(Task.next_attempt_at.is_(None), Task.next_attempt_at <= now),
        ).order_by(Task.updated_at).limit(1).with_for_update(skip_locked=True)).first()
        if candidate is None:
            return None
        if candidate.state == 'cancelling':
            _store(candidate, state='cancelled', stage='cancelled')
            candidate.lease_token = candidate.lease_until = candidate.next_attempt_at = None
            return None
        record = candidate.record
        attempts = int(record.get('attempts') or 0)
        if record.get('kind') in V2_KINDS and attempts >= MAX_ATTEMPTS:
            # The previous owner's lease expired without finishing (crash or hang).
            _store(candidate, state=_failure_state(record), stage='failed', failed_stage=record.get('stage'),
                   error_code='lease_expired', error='Preparation stopped responding. Try again.')
            candidate.lease_token = candidate.lease_until = candidate.next_attempt_at = None
            return None
        candidate.lease_token = uuid4().hex
        candidate.lease_until = now + LEASE
        candidate.next_attempt_at = None
        _store(candidate, attempts=attempts + 1, next_attempt_at=None, stage='starting')
        return (candidate.profile_id, candidate.job_id), candidate.lease_token, candidate.record


class Progress:
    """The claimed task's handle: fenced updates, checkpoints and lease renewal."""

    def __init__(self, key, token, checkpoints, *, task_id=None, record=None, clock=time.monotonic):
        self.key = key
        self.token = token
        self.task_id = task_id
        self.record = dict(record or {})
        self.kind = self.record.get('kind')
        self.checkpoints = dict(checkpoints)
        self.stages = dict(self.record.get('stages') or {})
        self._renewal = RenewalClock(RENEW_EVERY, clock)

    def _owns(self, row):
        return (row is not None and row.lease_token == self.token
                and (self.task_id is None or row.record.get('id') == self.task_id))

    def cancelled(self):
        if self._renewal.due():
            return not self._renew()
        with SessionLocal() as session:
            row = session.get(Task, self.key)
            return not self._owns(row) or row.state != 'running'

    def _renew(self):
        with SessionLocal.begin() as session:
            row = session.get(Task, self.key, with_for_update=True, populate_existing=True)
            if not self._owns(row) or row.state != 'running':
                return False
            row.lease_until = utcnow() + LEASE
            return True

    def _update(self, *, finish=False, retry_at=None, **changes):
        return retry_transient(lambda: self._update_once(finish, retry_at, dict(changes)),
                               action='saving application task progress')

    def _update_once(self, finish, retry_at, changes):
        with SessionLocal.begin() as session:
            row = session.get(Task, self.key, with_for_update=True, populate_existing=True)
            if not self._owns(row):
                return False
            if finish and row.state == 'cancelling':
                changes = {'state': 'cancelled', 'stage': 'cancelled', 'next_attempt_at': None}
                retry_at = None
            elif row.state != 'running':
                return False
            if changes.get('state') == 'failed' or retry_at is not None:
                changes['failed_stage'] = row.record.get('stage')
            if changes.get('state') == 'failed':
                changes['state'] = _failure_state(row.record)
            if retry_at is not None:
                changes['next_attempt_at'] = retry_at.isoformat()
            _store(row, **changes)
            row.lease_token = None if finish else self.token
            row.lease_until = None if finish else utcnow() + LEASE
            row.next_attempt_at = retry_at if finish else None
            self._renewal.reset()
            return True

    def __call__(self, stage):
        self.stages[stage] = time.time()
        if not self._update(stage=stage, stages=self.stages):
            raise HTTPException(409, 'Preparation cancelled.')

    def checkpoint(self, name, value):
        self.checkpoints[name] = value
        if not self._update(checkpoints=self.checkpoints):
            raise HTTPException(409, 'Preparation cancelled.')

    def load(self, name):
        return self.checkpoints.get(name)

    def fenced(self, session):
        """Lock and return the task row inside the caller's transaction, or None if no longer owned.

        Publishers call this after holding jobs and profiles, so a worker whose
        lease expired or whose task was cancelled cannot publish.
        """
        row = session.get(Task, self.key, with_for_update=True, populate_existing=True)
        return row if self._owns(row) and row.state == 'running' else None

    def store(self, session, row, **changes):
        """Update a row returned by :meth:`fenced` in the caller's transaction (renews the lease)."""
        _store(row, **changes)
        row.lease_until = utcnow() + LEASE
        self.record.update(changes)
        self._renewal.reset()


def process_once(run):
    """Claim one inference task and run ``run(profile_id, request, progress)``."""
    claimed = _claim()
    if claimed is None:
        return False
    key, token, record = claimed
    progress = Progress(key, token, record.get('checkpoints') or {}, task_id=record['id'], record=record)
    v2 = record.get('kind') in V2_KINDS
    try:
        if not v2:
            raise TaskError('obsolete_preparation', 'Start a new preparation to use the current document editor.')
        result = run(key[0], record['request'], progress)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else 'Could not prepare this application.'
        code = 'cancelled' if error.status_code == 409 and detail == 'Preparation cancelled.' else f'http_{error.status_code}'
        progress._update(finish=True, state='failed', stage='failed', error=detail, error_code=code)
    except Exception as error:  # noqa: BLE001 - every failure must finish the task
        code, retryable, message = classify(error)
        attempts = int(record.get('attempts') or 1)
        delay = retry_delay(attempts) if retryable else None
        if delay is not None:
            LOG.warning('Application task %s attempt %s failed (%s); retrying in %s',
                        record.get('kind'), attempts, code, delay)
            progress._update(finish=True, retry_at=utcnow() + delay, state='running', stage='retry_wait',
                             error_code=code, error=message)
        else:
            if code == 'unexpected':
                LOG.error('Application preparation failed unexpectedly (%s)', type(error).__name__)
            else:
                LOG.warning('Application task %s failed (%s)', record.get('kind') or 'legacy', code)
            progress._update(finish=True, state='failed', stage='failed', error=message, error_code=code)
    else:
        progress._update(finish=True, state='completed', stage='completed', result=result,
                         error_code=None, error=None, failed_stage=None)
    return True


def prune_finished():
    with SessionLocal.begin() as session:
        session.execute(delete(Task).where(
            Task.state.not_in(ACTIVE), Task.updated_at < utcnow() - RETENTION,
        ))
        session.execute(delete(RenderTask).where(
            RenderTask.state == 'failed', RenderTask.updated_at < utcnow() - RETENTION))


# --- Render lane -----------------------------------------------------------------

def render_status(session, profile_id, job_id, kind, version, template):
    row = session.get(RenderTask, (profile_id, job_id, kind, version, template))
    return None if row is None else {'state': row.state, 'attempts': row.attempts, 'error_code': row.error_code}


def enqueue_render(session, profile_id, job_id, kind, version, template, *, retry=False):
    """Queue a render in the caller's transaction; idempotent per version and template.

    A failed render is re-queued only when ``retry`` is true, so polling a
    broken document does not loop. The caller commits.
    """
    from hiring_scraper.app.database import dialect_insert
    now = utcnow()
    insert = dialect_insert(session)
    session.execute(insert(RenderTask).values(
        profile_id=profile_id, job_id=job_id, kind=kind, version=version, template=template,
        state='queued', attempts=0, available_at=now, updated_at=now,
    ).on_conflict_do_nothing())
    row = session.get(RenderTask, (profile_id, job_id, kind, version, template),
                      with_for_update=True, populate_existing=True)
    if row.state == 'failed' and retry:
        row.state, row.attempts, row.available_at = 'queued', 0, now
        row.error_code = row.lease_token = None
        row.updated_at = now
    return {'state': row.state, 'attempts': row.attempts, 'error_code': row.error_code}


def _claim_render():
    now = utcnow()
    with SessionLocal.begin() as session:
        # available_at is NULL once failed, so this index range holds only due work.
        row = session.scalars(select(RenderTask).where(RenderTask.available_at <= now)
                              .order_by(RenderTask.available_at).limit(1)
                              .with_for_update(skip_locked=True)).first()
        if row is None:
            return None
        key = (row.profile_id, row.job_id, row.kind, row.version, row.template)
        if session.get(ApplicationDocumentRender, key) is not None:
            session.delete(row)
            return None
        if row.attempts >= MAX_ATTEMPTS:
            row.state, row.available_at, row.lease_token = 'failed', None, None
            row.error_code, row.updated_at = 'lease_expired', now
            return None
        document = session.get(ApplicationDocument, (row.profile_id, row.job_id, row.kind, row.version))
        row.state, row.attempts = 'running', row.attempts + 1
        row.lease_token = uuid4().hex
        row.available_at = now + RENDER_LEASE
        row.updated_at = now
        return key, row.lease_token, row.attempts, document.body


def _finish_render(key, token, result=None, *, retry_at=None, error_code=None):
    from hiring_scraper.app.database import dialect_insert
    with SessionLocal.begin() as session:
        if result is not None:
            if key[2] == 'cv' and not hold_profiles(session, [key[0]]):
                return False
            # The PDF FK points to the document. Take that lock before the
            # render task so cascading CV deletion and version pruning agree.
            document = session.get(ApplicationDocument, key[:4],
                                   with_for_update={'read': True, 'key_share': True})
            if document is None:
                return False
        row = session.get(RenderTask, key, with_for_update=True, populate_existing=True)
        if row is None or row.lease_token != token:
            return False
        if result is not None:
            from hiring_scraper.app.application_documents_v2 import render_key

            if render_key(session, key[0], key[2], document.body) != key[4]:
                session.delete(row)
                return False
            insert = dialect_insert(session)
            profile_id, job_id, kind, version, template = key
            session.execute(insert(ApplicationDocumentRender).values(
                profile_id=profile_id, job_id=job_id, kind=kind, version=version, template=template,
                pdf=result.pdf, pages=result.pages, checks=list(result.checks or []), created_at=utcnow(),
            ).on_conflict_do_nothing())
            session.delete(row)
            return True
        row.lease_token = None
        row.error_code = error_code
        row.updated_at = utcnow()
        if retry_at is not None:
            row.state, row.available_at = 'queued', retry_at
        else:
            row.state, row.available_at = 'failed', None
        return True


def process_render_once(render):
    """Claim one render and run ``render(profile_id, kind, body) -> RenderResult``."""
    from hiring_scraper.app.application_pdf import export_verified

    claimed = _claim_render()
    if claimed is None:
        return False
    key, token, attempts, body = claimed
    try:
        result = render(key[0], key[2], body)
        if not export_verified(result.checks):
            raise TaskError('render_verification_failed', 'The PDF did not pass its readability checks.')
    except Exception as error:  # noqa: BLE001 - every failure must finish the task
        code, retryable, _ = classify(error)
        delay = retry_delay(attempts, RENDER_BACKOFF) if retryable else None
        LOG.warning('Document render attempt %s failed (%s)', attempts, code)
        retry_transient(lambda: _finish_render(
            key, token, retry_at=utcnow() + delay if delay else None, error_code=code),
            action='recording a render failure')
    else:
        retry_transient(lambda: _finish_render(key, token, result), action='storing a rendered PDF')
    return True
