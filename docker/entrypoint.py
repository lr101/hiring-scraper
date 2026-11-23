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
from sqlalchemy import create_engine, text


APP_UID = 10001
APP_GID = 10001
BOOTSTRAP_ENV_FILE = Path("/run/app-bootstrap.env")


def _prepare_codex_state() -> None:
    """Make the mounted private runtime writable before dropping to the app UID."""
    configured = os.environ.get("RADIUS_CODEX_STATE_DIR")
    if not configured:
        return
    root = Path(configured)
    if not root.is_absolute() or root == Path('/') or root.is_symlink():
        raise RuntimeError("RADIUS_CODEX_STATE_DIR must be an absolute private directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Named volumes can start root-owned. Repair existing native auth/cache
    # files too, without following links outside this dedicated runtime tree.
    repair_owner = os.geteuid() == 0
    for directory, directories, files in os.walk(root, followlinks=False):
        for path in [Path(directory), *(Path(directory) / name for name in directories + files)]:
            if path.is_symlink():
                # The native CLI creates PATH helper aliases in CODEX_HOME/tmp.
                # Keep those disposable links without touching their targets.
                # The cache root and all persistent state must remain real paths.
                temporary = root / 'home' / 'tmp'
                if path != temporary and path.is_relative_to(temporary):
                    continue
                raise RuntimeError("The private Codex state directory must not contain symbolic links")
            if repair_owner:
                os.chown(path, APP_UID, APP_GID, follow_symlinks=False)
    root.chmod(0o700)


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


def _application_database_password() -> str:
    _verify_bootstrap_file_permissions()
    values = dotenv_values(BOOTSTRAP_ENV_FILE)
    password = values.get("HIRING_DB_PASSWORD")
    if not password:
        raise RuntimeError("HIRING_DB_PASSWORD is missing from the bootstrap environment file")
    return password


def _application_database_url() -> str:
    password = _application_database_password()
    return f"postgresql+psycopg://hiring_app:{quote(password, safe='')}@db:5432/hiring"


def _ensure_application_role(migration_url: str, application_password: str) -> None:
    engine = create_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(85793011, 1)"))
            role_exists = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hiring_app')")
            )
            operation = "ALTER" if role_exists else "CREATE"
            role_command = (
                f"{operation} ROLE hiring_app WITH LOGIN NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE PASSWORD %L"
            )
            statement = connection.scalar(
                text("SELECT format(:role_command, CAST(:password AS text))"),
                {"role_command": role_command, "password": application_password},
            )
            connection.exec_driver_sql(statement)
            connection.exec_driver_sql("GRANT CONNECT ON DATABASE hiring TO hiring_app")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO hiring_app")
    finally:
        engine.dispose()


def _ensure_profile_vote_weight(migration_url: str) -> None:
    """Apply the additive profile field without advancing Alembic's revision.

    Keeping the revision unchanged lets a previous application image start
    against this database during a binary rollback.
    """
    engine = create_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS "
                "low_afd_vote_share_weight INTEGER NOT NULL DEFAULT 0"
            ))
            connection.execute(text("""
                UPDATE user_profiles
                   SET low_afd_vote_share_weight = LEAST(20, GREATEST(0,
                           COALESCE(NULLIF(preferences::jsonb -> 'matching_defaults'
                               ->> 'low_afd_vote_share_weight', '')::integer, 0))),
                       preferences = jsonb_set(
                           preferences::jsonb,
                           '{matching_defaults}',
                           (preferences::jsonb -> 'matching_defaults') - 'low_afd_vote_share_weight',
                           true
                       )::json
                 WHERE (preferences::jsonb -> 'matching_defaults') ? 'low_afd_vote_share_weight'
            """))
    finally:
        engine.dispose()


def _ensure_preparation_retry_schema(migration_url: str) -> None:
    """Add the preparation queue's retry column without advancing Alembic's revision.

    Older images ignore the nullable column, so a binary rollback still starts;
    their claims leave it NULL, which newer images treat as "claim now".
    """
    engine = create_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE application_preparation_tasks "
                "ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP WITH TIME ZONE"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_application_preparation_tasks_next_attempt_at "
                "ON application_preparation_tasks (next_attempt_at)"
            ))
    finally:
        engine.dispose()


def ensure_rollback_safe_schema(migration_url: str) -> None:
    """Every additive step applied after Alembic (entrypoint, dev migrate and tests)."""
    _ensure_profile_vote_weight(migration_url)
    _ensure_preparation_retry_schema(migration_url)


def _bootstrap() -> str:
    migration_url = _migration_database_url()
    engine = create_engine(migration_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(85793011, 7)"))
            return _migrate(migration_url)
    finally:
        engine.dispose()


def _migrate(migration_url: str) -> str:
    application_url = _application_database_url()
    print("Ensuring the application database role exists", flush=True)
    _ensure_application_role(migration_url, _application_database_password())

    print("Applying database migrations", flush=True)
    migration_environment = _runtime_environment(application_url)
    migration_environment["DATABASE_URL"] = migration_url
    migration_environment.pop("DATABASE_ADMIN_URL", None)
    migration_environment.pop("POSTGRES_PASSWORD", None)
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=migration_environment)
    del migration_environment

    print("Ensuring rollback-safe schema steps", flush=True)
    ensure_rollback_safe_schema(migration_url)

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


WORKER_MODULES = (
    "worker",
    "discovery_worker",
    "search_snapshot_worker",
    "profile_match_worker",
    "semantic_search_worker",
    "application_preparation_worker",
)
WORKER_POOL = {"HIRING_DB_POOL_SIZE": "5", "HIRING_DB_MAX_OVERFLOW": "5"}
# Inference lane + render lane threads, each with short sessions.
PREPARATION_POOL = {"HIRING_DB_POOL_SIZE": "3", "HIRING_DB_MAX_OVERFLOW": "0"}
API_POOL = {"HIRING_DB_POOL_SIZE": "30", "HIRING_DB_MAX_OVERFLOW": "10"}
API_COMMAND = ["uvicorn", "hiring_scraper.app.api:app", "--host", "0.0.0.0", "--port", "8000"]


def _workers_enabled() -> bool:
    setting = os.getenv("RUN_BACKGROUND_WORKERS", "true").lower()
    if setting not in {"true", "false"}:
        raise RuntimeError("RUN_BACKGROUND_WORKERS must be true or false")
    return setting == "true"


def _worker_pool(module: str) -> dict[str, str]:
    return PREPARATION_POOL if module == "application_preparation_worker" else WORKER_POOL


def _worker_command(module: str) -> list[str]:
    return ["python", "-m", f"hiring_scraper.app.{module}"]


def _exec_runtime(command: list[str], pool: dict[str, str]) -> None:
    environment = _runtime_environment(_application_database_url()) | pool
    _drop_privileges()
    os.execvpe(command[0], command, environment)


def _serve() -> int:
    commands = [(_worker_command(module), _worker_pool(module)) for module in WORKER_MODULES] if _workers_enabled() else []
    commands.append((API_COMMAND, API_POOL))

    environment = _runtime_environment()
    children: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        for command, pool_settings in commands:
            process_environment = environment | pool_settings
            children.append(subprocess.Popen(
                command, env=process_environment, preexec_fn=_drop_privileges))
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
    if arguments == ["--migrate"]:
        _prepare_codex_state()
        _bootstrap()
        return 0
    if arguments == ["--api"]:
        _exec_runtime(API_COMMAND, API_POOL)
        return 1
    if len(arguments) == 2 and arguments[0] == "--worker" and arguments[1] in WORKER_MODULES:
        if not _workers_enabled():
            return 0
        _exec_runtime(_worker_command(arguments[1]), _worker_pool(arguments[1]))
        return 1
    _prepare_codex_state()
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
