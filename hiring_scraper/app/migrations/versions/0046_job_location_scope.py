"""Precomputed Germany and German-state scope columns, invalidation triggers, and concurrent indexes."""
from collections.abc import Callable
from time import sleep

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0046_job_location_scope'
down_revision = '0045_local_job_analysis'
branch_labels = None
depends_on = None


_CONCURRENT_INDEXES: tuple[tuple[str, str], ...] = (
    (
        'ix_jobs_germany_candidate',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_germany_candidate '
        'ON jobs (is_active, id) WHERE germany_candidate IS TRUE',
    ),
    (
        'ix_jobs_location_states',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_location_states '
        'ON jobs USING gin (location_states) WITH (fastupdate = off)',
    ),
    (
        'ix_jobs_location_scope_version',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_location_scope_version '
        'ON jobs (location_scope_version, id)',
    ),
    (
        'ix_jobs_remote_candidate_active',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_remote_candidate_active '
        "ON jobs (id) WHERE is_active IS TRUE AND (work_arrangement = 'remote' "
        'OR (is_remote IS TRUE AND work_arrangement IS NULL))',
    ),
)


def _ensure_concurrent_index(bind: sa.Connection, index_name: str, create_sql: str) -> None:
    """Create an index concurrently, rebuilding it if a prior run left it INVALID."""
    row = bind.execute(
        sa.text("""
            SELECT i.indisvalid
              FROM pg_class AS c
              JOIN pg_namespace AS n ON n.oid = c.relnamespace
              JOIN pg_index AS i ON i.indexrelid = c.oid
             WHERE n.nspname = current_schema()
               AND c.relname = :index_name
        """),
        {'index_name': index_name},
    ).first()
    if row is not None and not row[0]:
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
    op.execute(create_sql)


def _run_short_ddl(apply: Callable[[], None]) -> None:
    bind = op.get_bind()
    attempts = 5
    for attempt in range(attempts):
        try:
            # Roll back the entire DDL attempt and release its locks before retrying.
            with bind.begin_nested():
                op.execute("SET LOCAL lock_timeout = '2s'")
                # Wait for child writers before requesting the jobs lock. If
                # ingestion owns jobs and is about to write locations, yield.
                op.execute('LOCK TABLE job_locations IN ACCESS EXCLUSIVE MODE')
                op.execute('LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE NOWAIT')
                apply()
            return
        except sa.exc.DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) != '55P03':
                raise
            if attempt == attempts - 1:
                raise RuntimeError(
                    f'0046 job location scope DDL could not acquire its locks after {attempts} attempts'
                ) from error
            sleep(0.25 * 2 ** attempt)


def _add_scope_columns() -> None:
    existing_columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('jobs')}
    if 'germany_candidate' not in existing_columns:
        op.add_column('jobs', sa.Column('germany_candidate', sa.Boolean(), nullable=True))
    if 'location_states' not in existing_columns:
        op.add_column('jobs', sa.Column('location_states', postgresql.ARRAY(sa.Text()), nullable=True))
    if 'location_scope_version' not in existing_columns:
        op.add_column('jobs', sa.Column('location_scope_version', sa.String(40), nullable=True))


def _install_scope_triggers() -> None:
    _add_scope_columns()
    op.create_table(
        'job_location_scope_dirty',
        sa.Column('job_id', sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column('change_xid', sa.Text(), primary_key=True),
        if_not_exists=True,
    )
    op.execute('GRANT SELECT, INSERT, DELETE ON job_location_scope_dirty TO hiring_app')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO hiring_app')
    op.execute("""
        CREATE OR REPLACE FUNCTION invalidate_job_location_scope_on_job_change() RETURNS trigger AS $$
        BEGIN
            NEW.location_scope_version := NULL;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute('DROP TRIGGER IF EXISTS jobs_invalidate_location_scope ON jobs')
    op.execute("""
        CREATE TRIGGER jobs_invalidate_location_scope
        BEFORE UPDATE OF location_text ON jobs
        FOR EACH ROW
        WHEN (OLD.location_text IS DISTINCT FROM NEW.location_text)
        EXECUTE FUNCTION invalidate_job_location_scope_on_job_change()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION invalidate_job_location_scope_on_location_change() RETURNS trigger AS $$
        DECLARE
            parent_ids integer[];
        BEGIN
            IF TG_OP = 'DELETE' THEN
                parent_ids := ARRAY[OLD.job_id];
            ELSIF TG_OP = 'UPDATE' THEN
                IF (OLD.job_id, OLD.label, OLD.country_code, OLD.latitude, OLD.longitude)
                   IS NOT DISTINCT FROM
                   (NEW.job_id, NEW.label, NEW.country_code, NEW.latitude, NEW.longitude) THEN
                    RETURN NULL;
                END IF;
                parent_ids := ARRAY[OLD.job_id, NEW.job_id];
            ELSE
                parent_ids := ARRAY[NEW.job_id];
            END IF;

            -- Deduplicate within this transaction only. A writer invisible
            -- to the scope calculation must leave its own mark.
            INSERT INTO job_location_scope_dirty (job_id, change_xid)
            SELECT job_id, pg_current_xact_id()::text
              FROM unnest(parent_ids) AS ids(job_id)
            ON CONFLICT DO NOTHING;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute('DROP TRIGGER IF EXISTS job_locations_invalidate_location_scope ON job_locations')
    op.execute("""
        CREATE TRIGGER job_locations_invalidate_location_scope
        AFTER INSERT OR DELETE OR UPDATE OF job_id, label, country_code, latitude, longitude ON job_locations
        FOR EACH ROW
        EXECUTE FUNCTION invalidate_job_location_scope_on_location_change()
    """)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        _run_short_ddl(_install_scope_triggers)
        # Commit the column/trigger transaction, then build indexes concurrently
        # so jobs never holds ACCESS EXCLUSIVE across index builds. Existing rows
        # stay NULL and are backfilled in bounded SQL batches by
        # search_snapshot_worker while queries safely use the indexed fallback.
        with op.get_context().autocommit_block():
            autocommit_bind = op.get_bind()
            for index_name, create_sql in _CONCURRENT_INDEXES:
                _ensure_concurrent_index(autocommit_bind, index_name, create_sql)
    else:
        _add_scope_columns()
        existing_indexes = {index['name'] for index in sa.inspect(bind).get_indexes('jobs')}
        if 'ix_jobs_germany_candidate' not in existing_indexes:
            op.create_index('ix_jobs_germany_candidate', 'jobs', ['is_active', 'id'])
        if 'ix_jobs_location_states' not in existing_indexes:
            op.create_index('ix_jobs_location_states', 'jobs', ['location_states'])
        if 'ix_jobs_location_scope_version' not in existing_indexes:
            op.create_index('ix_jobs_location_scope_version', 'jobs', ['location_scope_version', 'id'])
        if 'ix_jobs_remote_candidate_active' not in existing_indexes:
            op.create_index('ix_jobs_remote_candidate_active', 'jobs', ['id'])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            for index_name, _ in reversed(_CONCURRENT_INDEXES):
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
        _run_short_ddl(_remove_scope_triggers)
    else:
        inspector = sa.inspect(bind)
        existing_indexes = {index['name'] for index in inspector.get_indexes('jobs')}
        for index_name, _ in reversed(_CONCURRENT_INDEXES):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name='jobs')
        _drop_scope_columns()


def _remove_scope_triggers() -> None:
    op.execute('DROP TRIGGER IF EXISTS job_locations_invalidate_location_scope ON job_locations')
    op.execute('DROP FUNCTION IF EXISTS invalidate_job_location_scope_on_location_change()')
    op.execute('DROP TRIGGER IF EXISTS jobs_invalidate_location_scope ON jobs')
    op.execute('DROP FUNCTION IF EXISTS invalidate_job_location_scope_on_job_change()')
    op.drop_table('job_location_scope_dirty', if_exists=True)
    _drop_scope_columns()


def _drop_scope_columns() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column['name'] for column in inspector.get_columns('jobs')}
    for column_name in ('location_scope_version', 'location_states', 'germany_candidate'):
        if column_name in existing_columns:
            op.drop_column('jobs', column_name)
