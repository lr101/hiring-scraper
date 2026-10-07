"""Bootstrap the database, then supervise the app as an unprivileged user."""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values


APP_UID = 10001
APP_GID = 10001
BOOTSTRAP_ENV_FILE = Path("/run/app-bootstrap.env")


def _drop_privileges() -> None:
    if os.geteuid() == 0:
        try:
            os.setgroups([])
        except PermissionError:
            # Rootless Docker may disable setgroups in its user namespace. It
            # is safe to continue only when there are no supplementary groups.
            if os.getgroups():
                raise
        os.setgid(APP_GID)
        os.setuid(APP_UID)


def _runtime_environment(database_url: str | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("DATABASE_ADMIN_URL", None)
    environment.pop("POSTGRES_PASSWORD", None)
    environment.pop("HIRING_DB_PASSWORD", None)
    if database_url is not None:
        environment["DATABASE_URL"] = database_url
    environment.setdefault("HOME", "/tmp")
    return environment


def _verify_runtime_cannot_read_bootstrap_file() -> None:
    probe_code = """
import os
import sys

try:
    descriptor = os.open(sys.argv[1], os.O_RDONLY)
except PermissionError:
    raise SystemExit(1)
except OSError:
    raise SystemExit(2)
os.close(descriptor)
raise SystemExit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", probe_code, str(BOOTSTRAP_ENV_FILE)],
        check=False,
        env=_runtime_environment(),
        preexec_fn=_drop_privileges,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        raise RuntimeError("Bootstrap environment file is readable by the app user (UID 10001)")
    if result.returncode != 1:
        raise RuntimeError("Could not verify that the app user cannot read the bootstrap environment file")


def _verify_bootstrap_file_permissions() -> None:
    if not BOOTSTRAP_ENV_FILE.is_file():
        raise RuntimeError(f"Bootstrap environment file is missing: {BOOTSTRAP_ENV_FILE}")
    if BOOTSTRAP_ENV_FILE.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError("Bootstrap environment file must not be accessible by group or other users; run chmod 600 .env")
    _verify_runtime_cannot_read_bootstrap_file()


def _migration_database_url() -> str:
    _verify_bootstrap_file_permissions()
    values = dotenv_values(BOOTSTRAP_ENV_FILE)
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is missing from the bootstrap environment file")
    return f"postgresql+psycopg://postgres:{quote(password, safe='')}@db:5432/hiring"


def _application_database_url() -> str:
    _verify_bootstrap_file_permissions()
    values = dotenv_values(BOOTSTRAP_ENV_FILE)
    password = values.get("HIRING_DB_PASSWORD")
    if not password:
        raise RuntimeError("HIRING_DB_PASSWORD is missing from the bootstrap environment file")
    return f"postgresql+psycopg://hiring_app:{quote(password, safe='')}@db:5432/hiring"


def _bootstrap() -> str:
    application_url = _application_database_url()
    print("Applying database migrations", flush=True)
    migration_environment = _runtime_environment(application_url)
    migration_environment["DATABASE_URL"] = _migration_database_url()
    migration_environment.pop("DATABASE_ADMIN_URL", None)
    migration_environment.pop("POSTGRES_PASSWORD", None)
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=migration_environment)
    del migration_environment

    print("Importing the bundled fixture into an empty database", flush=True)
    subprocess.run(["hiring-seed", "--if-empty"], check=True,
                   env=_runtime_environment(application_url), preexec_fn=_drop_privileges)
    return application_url


def _stop_children(children: list[subprocess.Popen[bytes]]) -> None:
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        if child.poll() is None:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def _serve() -> int:
    worker_setting = os.getenv("RUN_BACKGROUND_WORKERS", "true").lower()
    if worker_setting not in {"true", "false"}:
        raise RuntimeError("RUN_BACKGROUND_WORKERS must be true or false")

    commands = []
    if worker_setting == "true":
        commands.extend([
            ["python", "-m", "hiring_scraper.app.worker"],
            ["python", "-m", "hiring_scraper.app.discovery_worker"],
        ])
    commands.append(["uvicorn", "hiring_scraper.app.api:app", "--host", "0.0.0.0", "--port", "8000"])

    environment = _runtime_environment()
    children: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        for command in commands:
            children.append(subprocess.Popen(command, env=environment, preexec_fn=_drop_privileges))
        exit_code = 0
        while not stop_requested:
            for child in children:
                result = child.poll()
                if result is not None:
                    print(f"Required process exited ({child.args}) with status {result}", flush=True)
                    exit_code = result or 1
                    stop_requested = True
                    break
            if not stop_requested:
                time.sleep(0.5)
        return exit_code
    finally:
        _stop_children(children)


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "--runtime-entrypoint":
        if len(arguments) != 1:
            raise RuntimeError("Unexpected runtime entrypoint arguments")
        _verify_bootstrap_file_permissions()
        runtime_environment = _runtime_environment()
        _drop_privileges()
        os.environ.clear()
        os.environ.update(runtime_environment)
        return _serve()
    if arguments:
        application_url = _application_database_url()
        environment = _runtime_environment(application_url)
        _drop_privileges()
        os.execvpe(arguments[0], arguments, environment)

    application_url = _bootstrap()
    runtime_environment = _runtime_environment(application_url)
    _drop_privileges()
    os.execve(sys.executable,
              [sys.executable, str(Path(__file__).resolve()), "--runtime-entrypoint"],
              runtime_environment)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Startup failed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
