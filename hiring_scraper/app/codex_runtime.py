"""Private-instance ChatGPT subscription access through the official Codex CLI.

Never reuse the developer's CODEX_HOME, inherited API credentials, or repo config.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from fastapi import HTTPException

DEVICE_URL = 'https://auth.openai.com/codex/device'
LOGIN_SECONDS = 900
GENERATION_SECONDS = 180
_ansi = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_disabled = ('shell_tool', 'unified_exec', 'code_mode', 'code_mode_host', 'view_image',
             'multi_agent', 'multi_agent_v2', 'apps', 'plugins', 'hooks', 'skill_search',
             'browser_use', 'browser_use_external', 'computer_use', 'image_generation',
             'sleep_tool', 'daemon_auto_start', 'recommended_plugins', 'goals')


def _root():
    return Path(os.environ.get('RADIUS_CODEX_STATE_DIR', str(Path.home() / '.local/state/radius/codex'))).absolute()


def _prepare(root):
    if root.is_symlink() or (root / 'home').is_symlink():
        raise HTTPException(503, 'Use a private directory for the Radius ChatGPT connection.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True); root.chmod(0o700)
    home = root / 'home'
    home.mkdir(mode=0o700, exist_ok=True); home.chmod(0o700)
    return home


def _binary():
    binary = shutil.which(os.environ.get('RADIUS_CODEX_BIN', 'codex'))
    if not binary:
        raise HTTPException(503, 'The Codex runtime is unavailable. Use the Radius release image or install its pinned runtime.')
    return binary


def _environment(root, workspace=None):
    home = _prepare(root)
    # A whitelist prevents API-key billing, alternate gateways and inherited
    # harness/tools from silently changing this subscription-only operation.
    result = {'PATH': '/usr/local/bin:' + os.defpath, 'LANG': 'C.UTF-8', 'NO_COLOR': '1',
              'HOME': str(workspace or home), 'CODEX_HOME': str(home)}
    for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'CODEX_CA_CERTIFICATE'):
        if os.environ.get(key): result[key] = os.environ[key]
    return result


def _command(subcommand):
    return [_binary(), subcommand, '-c', 'forced_login_method="chatgpt"', '-c', 'cli_auth_credentials_store="file"',
            '-c', 'model_provider="openai"']


@contextmanager
def _lock(root):
    _prepare(root)
    descriptor = os.open(root / 'runtime.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, 'The ChatGPT connection is busy signing in or preparing documents. Try again shortly.') from None
        yield descriptor
    finally:
        os.close(descriptor)


def _write(root, data):
    descriptor, name = tempfile.mkstemp(prefix='login-', dir=root)
    try:
        with os.fdopen(descriptor, 'w') as output: json.dump(data, output)
        os.replace(name, root / 'login.json')
    finally:
        Path(name).unlink(missing_ok=True)


def _read(root):
    try:
        path = root / 'login.json'
        if path.stat().st_size > 16000: return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError): return {}


def _connected(root):
    if not (root / 'home/auth.json').is_file(): return False
    try:
        result = subprocess.run(_command('login') + ['status'], env=_environment(root),
                                cwd=root, capture_output=True, text=True, timeout=8)
        return result.returncode == 0 and 'logged in using chatgpt' in (result.stdout + result.stderr).lower()
    except (OSError, subprocess.TimeoutExpired): return False


def status():
    root = _root()
    available = bool(shutil.which(os.environ.get('RADIUS_CODEX_BIN', 'codex')))
    connected = available and _connected(root)
    data = {'enabled': connected, 'available': available, 'provider': 'ChatGPT subscription',
            'model': os.environ.get('APPLICATION_AI_MODEL', '').strip() or None,
            'login_state': 'connected' if connected else 'disconnected',
            'usage_url': 'https://chatgpt.com/settings/usage'}
    if not connected:
        login = _read(root)
        if login.get('state') in {'starting', 'pending'}:
            if time.time() < login.get('expires_at', 0):
                data['login_state'] = login['state']
                if login.get('verification_url') == DEVICE_URL and re.fullmatch(r'[A-Z0-9]{4,8}-[A-Z0-9]{4,8}', login.get('user_code', '')):
                    data.update(user_code=login['user_code'], verification_url=DEVICE_URL)
            else: data['login_state'] = 'expired'
        elif login.get('state') == 'expired': data['login_state'] = 'expired'
        elif login.get('state') == 'error':
            data.update(login_state='error', login_error='Sign-in did not complete. Enable device code login in ChatGPT security settings, then try again.')
    return data


def _stop(process):
    if process.poll() is None:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    process.wait(timeout=10)


def start_login():
    _binary()
    current = status()
    if current['enabled'] or current['login_state'] in {'starting', 'pending'}: return current
    root = _root()
    # Acquire before starting a worker so concurrent HTTP requests cannot launch
    # duplicate device grants, including when served by separate API processes.
    lease = _lock(root); descriptor = lease.__enter__()
    try:
        expires = time.time() + LOGIN_SECONDS
        _write(root, {'state': 'starting', 'expires_at': expires})
        process = subprocess.Popen(_command('login') + ['--device-auth'], env=_environment(root), cwd=root,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, start_new_session=True, pass_fds=(descriptor,))
    except Exception:
        _write(root, {'state': 'error'})
        lease.__exit__(None, None, None)
        raise HTTPException(503, 'Could not start ChatGPT sign-in. Check the Codex runtime.') from None

    def monitor():
        captured = ''
        def read_instructions():
            nonlocal captured
            for line in iter(lambda: process.stdout.readline(4097), ''):
                captured = (captured + _ansi.sub('', line))[-8000:]
                code = re.search(r'\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b', captured)
                if DEVICE_URL in captured and code:
                    _write(root, {'state': 'pending', 'expires_at': expires,
                                  'verification_url': DEVICE_URL, 'user_code': code[0]})
        reader = threading.Thread(target=read_instructions, daemon=True); reader.start()
        try:
            process.wait(timeout=LOGIN_SECONDS)
            reader.join(timeout=2)
            _write(root, {'state': 'connected' if process.returncode == 0 and _connected(root) else 'error'})
        except subprocess.TimeoutExpired:
            _stop(process); reader.join(timeout=2)
            _write(root, {'state': 'expired'})
        finally:
            process.stdout.close()
            lease.__exit__(None, None, None)
    threading.Thread(target=monitor, daemon=True).start()
    return status()


def logout():
    root = _root()
    with _lock(root):
        try:
            result = subprocess.run(_command('logout'), env=_environment(root), cwd=root,
                                    capture_output=True, timeout=15)
            if result.returncode: raise OSError()
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(503, 'Could not disconnect ChatGPT. Try again from this Radius instance.') from None
        _write(root, {'state': 'disconnected'})
    return status()


def generate(prompt, schema, *, timeout=GENERATION_SECONDS):
    root = _root()
    _binary()
    with _lock(root) as descriptor:
        if not _connected(root):
            raise HTTPException(401, 'Continue with ChatGPT to use your subscription before preparing documents.')
        with tempfile.TemporaryDirectory(prefix='radius-application-') as folder:
            workspace = Path(folder)
            schema_path, output_path = workspace / 'schema.json', workspace / 'application.json'
            schema_path.write_text(json.dumps(schema)); schema_path.chmod(0o600)
            command = _command('exec') + ['--ignore-user-config', '--ignore-rules', '--skip-git-repo-check',
                '--ephemeral', '--sandbox', 'read-only', '--color', 'never',
                '--output-schema', str(schema_path), '--output-last-message', str(output_path),
                '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
                '-c', 'project_doc_max_bytes=0', '-c', 'features.skip_host_skill_discovery=true',
                '-c', 'log_dir=' + json.dumps(str(workspace / 'logs'))]
            for feature in _disabled: command += ['-c', f'features.{feature}=false']
            model = os.environ.get('APPLICATION_AI_MODEL', '').strip()
            if model: command += ['--model', model]
            command += ['-']
            try:
                process = subprocess.Popen(command, env=_environment(root, workspace), cwd=workspace,
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    text=True, start_new_session=True, pass_fds=(descriptor,))
                try: process.communicate(prompt, timeout=min(timeout, GENERATION_SECONDS))
                except subprocess.TimeoutExpired:
                    _stop(process)
                    raise HTTPException(504, 'ChatGPT preparation timed out. Your current documents are unchanged.') from None
                if process.returncode:
                    raise HTTPException(502, 'ChatGPT could not prepare this application. Check your subscription usage or sign in again; you can also use the ChatGPT handoff.')
                if output_path.stat().st_size > 1024 * 1024: raise ValueError()
                result = json.loads(output_path.read_text())
                if not isinstance(result, dict): raise ValueError()
                return result
            except (OSError, ValueError):
                raise HTTPException(502, 'ChatGPT returned an incomplete application. Your current documents are unchanged.') from None
