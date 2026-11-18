"""PostgreSQL preparation queue shared by the API and preparation worker."""
from datetime import timedelta
import logging
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.models import ApplicationPreparationTask as Task, utcnow

LOG = logging.getLogger(__name__)
ACTIVE = ('running', 'cancelling')
LEASE = timedelta(minutes=35)
RETENTION = timedelta(days=7)


def _store(row, **changes):
    now = utcnow()
    row.record = {**row.record, **changes, 'updated_at': now.timestamp()}
    row.state = row.record['state']
    row.updated_at = now


def enqueue(profile_id, job_id, request, revision, *, automatic=False):
    with SessionLocal.begin() as session:
        # Serialize the global capacity check and first insert.
        if session.get_bind().dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(85793011, 8)'))
        row = session.get(Task, (profile_id, job_id), with_for_update=True, populate_existing=True)
        previous = row.record if row else {}
        same_inputs = previous.get('request') == request and previous.get('revision') == revision
        if row and row.state in ACTIVE:
            if not same_inputs:
                raise HTTPException(409, 'Another preparation is already running with different options.')
            return previous
        active = session.scalar(select(func.count()).select_from(Task).where(Task.state.in_(ACTIVE)))
        if active >= 8:
            raise HTTPException(429, 'Too many applications are being prepared. Please try again shortly.')
        if row is None:
            row = Task(profile_id=profile_id, job_id=job_id, record={})
            session.add(row)
        row.record = {}
        row.lease_token = row.lease_until = None
        checkpoints = previous.get('checkpoints', {}) if same_inputs and previous.get('state') in (
            'failed', 'cancelled'
        ) else {}
        _store(row, id=uuid4().hex, state='running', stage='starting', started_at=time.time(),
               request=request, revision=revision, automatic=automatic, checkpoints=checkpoints)
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
        _store(row, state=state, stage=state)
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
        candidate = session.scalars(select(Task).where(
            Task.state.in_(ACTIVE), or_(Task.lease_until.is_(None), Task.lease_until <= now),
        ).order_by(Task.updated_at).limit(1).with_for_update(skip_locked=True)).first()
        if candidate is None:
            return None
        if candidate.state == 'cancelling':
            _store(candidate, state='cancelled', stage='cancelled')
            candidate.lease_token = candidate.lease_until = None
            return None
        candidate.lease_token = uuid4().hex
        candidate.lease_until = now + LEASE
        return (candidate.profile_id, candidate.job_id), candidate.lease_token, candidate.record


class Progress:
    def __init__(self, key, token, checkpoints):
        self.key = key
        self.token = token
        self.checkpoints = dict(checkpoints)

    def _owns(self, row):
        return row is not None and row.lease_token == self.token

    def cancelled(self):
        with SessionLocal() as session:
            row = session.get(Task, self.key)
            return not self._owns(row) or row.state != 'running'

    def _update(self, *, finish=False, **changes):
        with SessionLocal.begin() as session:
            row = session.get(Task, self.key, with_for_update=True)
            if not self._owns(row):
                return False
            if finish and row.state == 'cancelling':
                changes = {'state': 'cancelled', 'stage': 'cancelled'}
            elif row.state != 'running':
                return False
            if changes.get('state') == 'failed':
                changes['failed_stage'] = row.record['stage']
            _store(row, **changes)
            row.lease_token = None if finish else self.token
            row.lease_until = None if finish else utcnow() + LEASE
            return True

    def __call__(self, stage):
        if not self._update(stage=stage):
            raise HTTPException(409, 'Preparation cancelled.')

    def checkpoint(self, name, value):
        if name == 'draft':
            self.checkpoints.pop('review', None)
        self.checkpoints[name] = value
        if not self._update(checkpoints=self.checkpoints):
            raise HTTPException(409, 'Preparation cancelled.')

    def load(self, name):
        return self.checkpoints.get(name)


def process_once(run):
    claimed = _claim()
    if claimed is None:
        return False
    key, token, record = claimed
    progress = Progress(key, token, record['checkpoints'])
    try:
        result = run(key[0], record['request'], record['revision'], progress)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else 'Could not prepare this application.'
        progress._update(finish=True, state='failed', stage='failed', error=detail)
    except Exception as error:
        LOG.error('Application preparation failed unexpectedly (%s)', type(error).__name__)
        progress._update(finish=True, state='failed', stage='failed',
                         error='Preparation failed unexpectedly. Check server logs and try again.')
    else:
        progress._update(finish=True, state='completed', stage='completed', result=result)
    return True


def prune_finished():
    with SessionLocal.begin() as session:
        session.execute(delete(Task).where(
            Task.state.not_in(ACTIVE), Task.updated_at < utcnow() - RETENTION,
            or_(Task.state != 'completed', Task.record['automatic'].as_boolean().is_not(True)),
        ))
