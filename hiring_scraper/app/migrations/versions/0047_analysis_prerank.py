"""Pre-ranking columns and concurrent indexes on job_analysis_queue, plus profile vector cleanup."""
from collections.abc import Callable
from time import sleep

from alembic import op
import sqlalchemy as sa


revision = '0047_analysis_prerank'
down_revision = '0046_job_location_scope'
branch_labels = None
depends_on = None


_CONCURRENT_INDEXES: tuple[tuple[str, str], ...] = (
    (
        'ix_job_analysis_queue_priority',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_analysis_queue_priority '
        'ON job_analysis_queue (similarity DESC NULLS LAST, job_id DESC) '
        'WHERE priority IS TRUE',
    ),
    (
        'ix_job_analysis_queue_scored',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_analysis_queue_scored '
        'ON job_analysis_queue (profile_set_hash, similarity DESC, job_id DESC) '
        'WHERE similarity IS NOT NULL',
    ),
    (
        'ix_job_analysis_queue_prerank',
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_analysis_queue_prerank '
        'ON job_analysis_queue (profile_set_hash, job_id DESC)',
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
            with bind.begin_nested():
                op.execute("SET LOCAL lock_timeout = '2s'")
                apply()
            return
        except sa.exc.DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) != '55P03':
                raise
            if attempt == attempts - 1:
                raise RuntimeError(
                    f'0047 analysis pre-rank DDL could not acquire its locks after {attempts} attempts'
                ) from error
            sleep(0.25 * 2 ** attempt)


def _add_columns() -> None:
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('job_analysis_queue')}
    if 'priority' not in existing:
        op.add_column(
            'job_analysis_queue',
            sa.Column('priority', sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if 'similarity' not in existing:
        op.add_column('job_analysis_queue', sa.Column('similarity', sa.Float(), nullable=True))
    if 'profile_set_hash' not in existing:
        op.add_column('job_analysis_queue', sa.Column('profile_set_hash', sa.String(64), nullable=True))


def _apply_ddl() -> None:
    _add_columns()
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON job_analysis_queue TO hiring_app')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON semantic_search_documents TO hiring_app')
    # Reuse purge_semantic_document() from 0024 so deleted profiles drop their vectors.
    op.execute('DROP TRIGGER IF EXISTS user_profiles_semantic_purge ON user_profiles')
    op.execute("""
        CREATE TRIGGER user_profiles_semantic_purge
        AFTER DELETE ON user_profiles
        FOR EACH ROW EXECUTE FUNCTION purge_semantic_document('profile')
    """)


def _remove_ddl() -> None:
    op.execute('DROP TRIGGER IF EXISTS user_profiles_semantic_purge ON user_profiles')
    _drop_columns()


def _drop_columns() -> None:
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('job_analysis_queue')}
    for column_name in ('profile_set_hash', 'similarity', 'priority'):
        if column_name in existing:
            op.drop_column('job_analysis_queue', column_name)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        _run_short_ddl(_apply_ddl)
        # Commit column/trigger DDL first, then build indexes concurrently so
        # job_analysis_queue never blocks concurrent writers.
        with op.get_context().autocommit_block():
            autocommit_bind = op.get_bind()
            for index_name, create_sql in _CONCURRENT_INDEXES:
                _ensure_concurrent_index(autocommit_bind, index_name, create_sql)
    else:
        _add_columns()
        existing_indexes = {index['name'] for index in sa.inspect(bind).get_indexes('job_analysis_queue')}
        if 'ix_job_analysis_queue_priority' not in existing_indexes:
            op.create_index('ix_job_analysis_queue_priority', 'job_analysis_queue', ['similarity', 'job_id'])
        if 'ix_job_analysis_queue_scored' not in existing_indexes:
            op.create_index('ix_job_analysis_queue_scored', 'job_analysis_queue',
                            ['profile_set_hash', 'similarity', 'job_id'])
        if 'ix_job_analysis_queue_prerank' not in existing_indexes:
            op.create_index('ix_job_analysis_queue_prerank', 'job_analysis_queue',
                            ['profile_set_hash', 'job_id'])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            for index_name, _ in reversed(_CONCURRENT_INDEXES):
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {index_name}')
        _run_short_ddl(_remove_ddl)
    else:
        existing_indexes = {index['name'] for index in sa.inspect(bind).get_indexes('job_analysis_queue')}
        for index_name, _ in reversed(_CONCURRENT_INDEXES):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name='job_analysis_queue')
        _drop_columns()
