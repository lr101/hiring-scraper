"""Exercise native Codex cache creation and repeated private-volume startup offline."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    entrypoint_path = os.environ.get('RADIUS_CI_ENTRYPOINT', '/usr/local/bin/hiring-entrypoint.py')
    binary = os.environ.get('RADIUS_CODEX_BIN', '/opt/radius-codex/bin/codex')
    spec = importlib.util.spec_from_file_location('radius_entrypoint', entrypoint_path)
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    if Path('/app/experiments/application_hillclimb').exists() or Path('/app/scripts/hillclimb').exists():
        raise RuntimeError('Offline benchmark tooling must not be present in the application image')
    # Codex intentionally refuses helper aliases below /tmp. Use a separate
    # disposable directory under /var/tmp, with no real login or provider calls.
    with tempfile.TemporaryDirectory(prefix='radius-codex-startup-', dir='/var/tmp') as folder:
        parent = Path(folder); parent.chmod(0o755)
        root = parent / 'state'
        (root / 'home').mkdir(parents=True, mode=0o700)
        os.environ['RADIUS_CODEX_STATE_DIR'] = str(root)
        environment = {'PATH': os.defpath, 'HOME': str(root / 'home'), 'CODEX_HOME': str(root / 'home')}

        def login_status():
            result = subprocess.run([binary, 'login', 'status'], env=environment,
                                    cwd=folder, preexec_fn=entrypoint._drop_privileges,
                                    capture_output=True, text=True, timeout=15)
            if result.returncode != 1 or 'not logged in' not in (result.stdout + result.stderr).lower():
                raise RuntimeError('Isolated native login-status check failed; no login was supplied')

        entrypoint._prepare_codex_state()
        login_status()
        if not any(path.is_symlink() for path in (root / 'home/tmp').rglob('*')):
            raise RuntimeError('Native Codex did not create the temporary helper links under test')
        entrypoint._prepare_codex_state()
        entrypoint._prepare_codex_state()
        login_status()
    print('Native Codex temporary helper links survive repeated startup; benchmark tooling absent.')


if __name__ == '__main__':
    main()
