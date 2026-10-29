"""Private, file-backed preparation jobs shared by API processes and devices."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from fastapi import HTTPException

STALE_SECONDS = 1100
RETENTION_SECONDS = 7 * 86400
_capacity = threading.BoundedSemaphore(8)


def _directory(root):
    root = Path(root)
    if root.is_symlink():
        raise HTTPException(503, 'The preparation state directory must be private.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    folder = root / 'application-jobs'
    if folder.is_symlink():
        raise HTTPException(503, 'The preparation state directory must be private.')
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    return folder


@contextmanager
def profile_lock(root, profile_id):
    """Serialize private application writes with profile and CV deletion."""
    root = Path(root)
    _directory(root)
    path = root / f'profile-{int(profile_id)}.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _path(root, profile_id, job_id):
    return _directory(root) / f'{int(profile_id)}-{int(job_id)}.json'


def _lease(root, profile_id, job_id):
    path = _directory(root) / f'{int(profile_id)}-{int(job_id)}.lease'
    return os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)


def _alive(root, profile_id, job_id):
    fd = _lease(root, profile_id, job_id)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


@contextmanager
def _lock(root, profile_id, job_id):
    path = _directory(root) / f'{int(profile_id)}-{int(job_id)}.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(root, profile_id, job_id, value):
    path = _path(root, profile_id, job_id)
    fd, name = tempfile.mkstemp(prefix='job-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _read(root, profile_id, job_id):
    path = _path(root, profile_id, job_id)
    try:
        if path.is_symlink() or path.stat().st_size > 4000000:
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, dict): return None
        if time.time() - data.get('updated_at', 0) > RETENTION_SECONDS and not (data.get('automatic') and data.get('state') == 'completed'):
            path.unlink(missing_ok=True)
            return None
        if data.get('state') in ('running', 'cancelling') and (time.time() - data.get('updated_at', 0) > STALE_SECONDS or not _alive(root, profile_id, job_id)):
            cancelled = data.get('state') == 'cancelling'
            if not cancelled: data['failed_stage'] = data.get('stage')
            data.update(state='cancelled' if cancelled else 'failed', stage='cancelled' if cancelled else 'failed', error='Preparation was interrupted or timed out. Please try again.' if not cancelled else None, updated_at=time.time())
            _write(root, profile_id, job_id, data)
        return data
    except (OSError, ValueError, TypeError):
        return None


def _sweep(root):
    checked = 0
    for path in _directory(root).iterdir():
        match = re.fullmatch(r'(\d+)-(\d+)(\.draft)?\.json', path.name)
        if not match or path.is_symlink(): continue
        try:
            if time.time() - path.stat().st_mtime <= RETENTION_SECONDS: continue
        except OSError: continue
        profile_id, job_id = int(match[1]), int(match[2])
        with _lock(root, profile_id, job_id):
            if match[3]: _load_draft(root, profile_id, job_id)
            else: _read(root, profile_id, job_id)
        checked += 1
        if checked >= 100: break


def status(root, profile_id, job_id):
    _sweep(root)
    with _lock(root, profile_id, job_id):
        return _read(root, profile_id, job_id)


def start(root, profile_id, job_id, runner, request=None, revision=None, automatic=False):
    _sweep(root)
    with _lock(root, profile_id, job_id):
        current = _read(root, profile_id, job_id)
        if current and current['state'] in ('running', 'cancelling'):
            if current.get('request') != request or current.get('revision') != revision:
                raise HTTPException(409, 'Another preparation for this application is already running with different options. Wait or cancel it before starting a new one.')
            return current
        capacity = _capacity
        if not capacity.acquire(blocking=False):
            raise HTTPException(429, 'Too many applications are being prepared. Please try again shortly.')
        try:
            lease = _lease(root, profile_id, job_id)
        except Exception:
            capacity.release()
            raise
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lease)
            capacity.release()
            raise HTTPException(409, 'Preparation is already starting. Please try again shortly.') from None
        checkpoints = current.get('checkpoints', {}) if current and current.get('state') in ('failed', 'cancelled') and current.get('request') == request and current.get('revision') == revision else {}
        record = {'id': uuid.uuid4().hex, 'state': 'running', 'stage': 'starting', 'started_at': time.time(), 'updated_at': time.time(), 'request': request, 'revision': revision, 'checkpoints': checkpoints, 'automatic': automatic}
        try: _write(root, profile_id, job_id, record)
        except Exception:
            os.close(lease)
            capacity.release()
            raise

    def update(**values):
        with _lock(root, profile_id, job_id):
            current = _read(root, profile_id, job_id)
            if not current or current.get('id') != record['id'] or current.get('state') != 'running': return
            if values.get('state') == 'failed': values['failed_stage'] = current.get('stage')
            current.update(values, updated_at=time.time())
            _write(root, profile_id, job_id, current)

    class Control:
        usage_limit_percent = 50 if automatic else None
        def __call__(self, stage):
            if self.cancelled(): raise HTTPException(409, 'Preparation cancelled.')
            update(stage=stage)
        def cancelled(self):
            current = status(root, profile_id, job_id)
            return not current or current.get('id') != record['id'] or current.get('state') != 'running'
        def checkpoint(self, name, value):
            if self.cancelled(): raise HTTPException(409, 'Preparation cancelled.')
            if name == 'draft': checkpoints.pop('review', None)
            checkpoints[name] = value
            update(checkpoints=dict(checkpoints))
        def load(self, name): return checkpoints.get(name)

    def work():
        try:
            result = runner(Control())
            update(state='completed', stage='completed', result=result)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else 'Could not prepare this application. Please try again.'
            update(state='failed', stage='failed', error=detail)
        except Exception:
            update(state='failed', stage='failed', error='Could not prepare this application. Please try again.')
        finally:
            with _lock(root, profile_id, job_id):
                current = _read(root, profile_id, job_id)
                if current and current.get('id') == record['id'] and current.get('state') == 'cancelling':
                    current.update(state='cancelled', stage='cancelled', updated_at=time.time())
                    _write(root, profile_id, job_id, current)
            os.close(lease)
            capacity.release()
    try: threading.Thread(target=work, daemon=True).start()
    except Exception:
        os.close(lease)
        capacity.release()
        raise
    return record


def cancel(root, profile_id, job_id, expected_id=None):
    with _lock(root, profile_id, job_id):
        current = _read(root, profile_id, job_id)
        if expected_id is not None and (not current or current.get('id') != expected_id):
            raise HTTPException(409, 'This preparation changed. Reload to see its current state.')
        if current and current.get('state') == 'running':
            current.update(state='cancelling', stage='cancelling', updated_at=time.time())
            _write(root, profile_id, job_id, current)
        return current


def _draft_path(root, profile_id, job_id):
    return _directory(root) / f'{int(profile_id)}-{int(job_id)}.draft.json'


def _load_draft(root, profile_id, job_id):
    try:
        path = _draft_path(root, profile_id, job_id)
        if path.is_symlink() or path.stat().st_size > 2000000: return None
        value = json.loads(path.read_text())
        if not isinstance(value, dict): return None
        if time.time() - value.get('updated_at', 0) > RETENTION_SECONDS:
            path.unlink(missing_ok=True)
            return None
        return value
    except (OSError, ValueError): return None


def load_draft(root, profile_id, job_id):
    with _lock(root, profile_id, job_id): return _load_draft(root, profile_id, job_id)


def save_draft(root, profile_id, job_id, draft, version):
    with _lock(root, profile_id, job_id):
        old = _load_draft(root, profile_id, job_id)
        if (old['version'] if old else None) != version:
            raise HTTPException(409, 'This draft changed on another device. Reload it or keep your local copy before overwriting.')
        value = {'version': uuid.uuid4().hex, 'draft': draft, 'updated_at': time.time()}
        path = _draft_path(root, profile_id, job_id)
        fd, name = tempfile.mkstemp(prefix='draft-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as output: json.dump(value, output, ensure_ascii=False)
            os.replace(name, path)
        finally: Path(name).unlink(missing_ok=True)
        return value


def delete(root, profile_id, job_id):
    with _lock(root, profile_id, job_id):
        _path(root, profile_id, job_id).unlink(missing_ok=True)
        _draft_path(root, profile_id, job_id).unlink(missing_ok=True)


def delete_if_id(root, profile_id, job_id, expected_id):
    with _lock(root, profile_id, job_id):
        current = _read(root, profile_id, job_id)
        if not current or current.get('id') != expected_id:
            raise HTTPException(409, 'This suggestion changed. Reload to see the current version.')
        _path(root, profile_id, job_id).unlink(missing_ok=True)


def delete_profile(root, profile_id):
    folder = Path(root) / 'application-jobs'
    if not folder.exists(): return
    jobs = set()
    for path in _directory(root).iterdir():
        match = re.fullmatch(r'(\d+)-(\d+)(?:\.draft)?\.json', path.name)
        if match and int(match[1]) == int(profile_id): jobs.add(int(match[2]))
    for job_id in jobs: delete(root, profile_id, job_id)
