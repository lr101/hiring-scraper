"""Private-instance ChatGPT subscription access through the official Codex CLI.

Never reuse the developer's CODEX_HOME, inherited API credentials, or repo config.
"""
from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from fastapi import HTTPException

from .codex_usage import evaluate, UsageUnavailable

LOG = logging.getLogger(__name__)

DEVICE_URL = 'https://auth.openai.com/codex/device'
LOGIN_SECONDS = 900
GENERATION_SECONDS = 300
USAGE_SECONDS = 8
USAGE_MAX_BYTES = 128 * 1024
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


def selected_model():
    root = _root()
    try:
        fd = os.open(root / 'model.json', os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as source:
            data = json.load(source)
        value = data.get('model') if isinstance(data, dict) else None
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', value):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return os.environ.get('APPLICATION_AI_MODEL', '').strip() or None


def set_model(model):
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', model)):
        raise HTTPException(422, 'Enter a valid model ID.')
    root = _root()
    _prepare(root)
    descriptor, name = tempfile.mkstemp(prefix='model-', dir=root)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'model': model}, output)
        os.replace(name, root / 'model.json')
    finally:
        Path(name).unlink(missing_ok=True)


def status():
    root = _root()
    available = bool(shutil.which(os.environ.get('RADIUS_CODEX_BIN', 'codex')))
    connected = available and _connected(root)
    data = {'enabled': connected, 'available': available, 'provider': 'ChatGPT subscription',
            'model': selected_model(),
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
    # The session may still contain descendants after its leader has exited.
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


def subscription_usage():
    """Read only Radius subscription limits; never start a model turn."""
    root = _root()
    try:
        with _lock(root):
            return _subscription_usage(root)
    except HTTPException:
        return evaluate(None)


def _subscription_usage(root):
    # The caller owns the runtime lease, including throughout guarded generation.
    unavailable = evaluate(None)
    if not (root / 'home/auth.json').is_file():
        return {**unavailable, 'reason': 'disconnected'}
    process = None
    try:
        command = _command('app-server') + ['--listen', 'stdio://']
        for feature in _disabled:
            command += ['-c', f'features.{feature}=false']
        process = subprocess.Popen(command, env=_environment(root), cwd=root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic() + USAGE_SECONDS
        pending = bytearray()
        consumed = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)

            def send(message):
                process.stdin.write(json.dumps(message).encode() + b'\n')
                process.stdin.flush()

            def request(identifier, method, params=None):
                nonlocal consumed
                message = {'id': identifier, 'method': method}
                if params is not None:
                    message['params'] = params
                send(message)
                while True:
                    while b'\n' in pending:
                        line, _, remaining = pending.partition(b'\n')
                        pending[:] = remaining
                        response = json.loads(line)
                        if not isinstance(response, dict):
                            raise UsageUnavailable()
                        if response.get('id') == identifier:
                            if 'error' in response or 'result' not in response:
                                raise UsageUnavailable()
                            return response['result']
                    remaining_time = deadline - time.monotonic()
                    if remaining_time <= 0 or not selector.select(remaining_time):
                        raise UsageUnavailable()
                    chunk = os.read(process.stdout.fileno(), 8192)
                    consumed += len(chunk)
                    if not chunk or consumed > USAGE_MAX_BYTES:
                        raise UsageUnavailable()
                    pending.extend(chunk)

            request(1, 'initialize', {'clientInfo': {'name': 'radius', 'title': 'Radius', 'version': '0.2.0'}})
            send({'method': 'initialized'})
            account = request(2, 'account/read', {'refreshToken': False})
            if (not isinstance(account, dict) or not isinstance(account.get('account'), dict) or
                    account['account'].get('type') != 'chatgpt'):
                return {**unavailable, 'reason': 'disconnected'}
            return evaluate(request(3, 'account/rateLimits/read'))
    except (OSError, ValueError, RecursionError, HTTPException):
        return unavailable
    finally:
        if process is not None:
            _stop(process)
            process.stdin.close()
            process.stdout.close()


def _generation_failure(stderr, returncode):
    """Report recognized failures without exposing prompts, credentials, or URLs."""
    diagnostic = _ansi.sub('', stderr).lower()
    if re.search(r'context (?:length|window).*exceed|too many tokens', diagnostic):
        return 'The application exceeds the model context limit. Reduce the CV or job description and try again.'
    if re.search(r'\b429\b|rate.limit|usage limit|too many requests', diagnostic):
        return 'ChatGPT rate limit reached. Check subscription usage and try again after the limit resets.'
    if re.search(r'\b401\b|unauthorized|authentication failed|token.*expired', diagnostic):
        return 'ChatGPT authentication failed or expired. Sign in again in Radius settings.'
    if re.search(r'\b403\b|forbidden', diagnostic):
        return 'ChatGPT denied access to this request. Check that your subscription can use the selected model.'
    if 'model' in diagnostic and re.search(r'not supported|not available|not found|does not exist|no access', diagnostic):
        return 'The selected ChatGPT model is unavailable for this account. Choose another model in Radius settings.'
    if re.search(r'connection refused|connection reset|dns|failed to connect|error sending request|network error', diagnostic):
        return 'Could not connect to ChatGPT. Check the server network connection and try again.'
    if re.search(r'\b50[0234]\b|service unavailable|bad gateway', diagnostic):
        return 'ChatGPT service is unavailable or returned a server error. Try again later.'
    status = re.search(r'(?:http(?: status)?|status(?: code)?)\s*[:=]?\s*([45]\d\d)\b', diagnostic)
    if status:
        return f'ChatGPT returned HTTP {status.group(1)}. Try again or use the ChatGPT handoff.'
    return f'ChatGPT runtime exited with code {returncode}; its error could not be classified. Try again or use the ChatGPT handoff.'


def generate(prompt, schema, *, timeout=GENERATION_SECONDS, cancelled=None, model=None,
             reasoning_effort=None, usage_limit_percent=None):
    root = _root()
    _binary()
    with _lock(root) as descriptor:
        if not _connected(root):
            raise HTTPException(401, 'Continue with ChatGPT to use your subscription before preparing documents.')
        if usage_limit_percent is not None:
            usage = _subscription_usage(root)
            if (not usage['available'] or not usage['allowed'] or
                    usage['used_percent'] is None or usage['used_percent'] >= usage_limit_percent):
                raise HTTPException(429, 'ChatGPT scoring paused: ' + usage['reason'] + '.')
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
            if model is None: model = selected_model()
            if model: command += ['--model', model]
            if reasoning_effort is not None:
                command += ['-c', 'model_reasoning_effort=' + json.dumps(reasoning_effort)]
            command += ['-']
            try:
                diagnostic_file = workspace / 'stderr'
                with diagnostic_file.open('wb') as diagnostics:
                    process = subprocess.Popen(command, env=_environment(root, workspace), cwd=workspace,
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=diagnostics,
                        text=True, start_new_session=True, pass_fds=(descriptor,))
                deadline = time.monotonic() + min(timeout, GENERATION_SECONDS)
                first = True
                while True:
                    if cancelled and cancelled():
                        _stop(process)
                        raise HTTPException(409, 'Preparation cancelled.')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _stop(process)
                        raise HTTPException(504, 'ChatGPT preparation timed out. Your current documents are unchanged.')
                    try:
                        process.communicate(prompt if first else None, timeout=min(remaining, .5) if cancelled else remaining)
                        break
                    except subprocess.TimeoutExpired:
                        first = False
                if process.returncode:
                    with diagnostic_file.open('rb') as diagnostics:
                        diagnostics.seek(0, os.SEEK_END)
                        diagnostics.seek(max(0, diagnostics.tell() - 65536))
                        detail = _generation_failure(diagnostics.read().decode('utf-8', errors='replace'), process.returncode)
                    LOG.warning('ChatGPT preparation failed: %s', detail)
                    raise HTTPException(502, detail)
                if output_path.stat().st_size > 1024 * 1024: raise ValueError()
                result = json.loads(output_path.read_text())
                if not isinstance(result, dict): raise ValueError()
                return result
            except (OSError, ValueError):
                raise HTTPException(502, 'ChatGPT returned an incomplete application. Your current documents are unchanged.') from None
