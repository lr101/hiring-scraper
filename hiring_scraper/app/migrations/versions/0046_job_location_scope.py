"""Precomputed Germany and German-state scope columns, invalidation triggers, and concurrent indexes."""
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


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column['name'] for column in inspector.get_columns('jobs')}

    # Short transaction: add nullable columns without defaults (metadata-only),
    # refresh grants, and install scope-invalidation triggers.
    if 'germany_candidate' not in existing_columns:
        op.add_column('jobs', sa.Column('germany_candidate', sa.Boolean(), nullable=True))
    if 'location_states' not in existing_columns:
        op.add_column('jobs', sa.Column('location_states', postgresql.ARRAY(sa.Text()), nullable=True))
    if 'location_scope_version' not in existing_columns:
        op.add_column('jobs', sa.Column('location_scope_version', sa.String(40), nullable=True))

    if bind.dialect.name == 'postgresql':
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

                -- Lock even when the version is NULL. A backfill holding
                -- the row lock may be about to publish a current version
                -- using the previous locations. Recheck after it commits.
                PERFORM 1 FROM jobs WHERE id = ANY(parent_ids) ORDER BY id FOR NO KEY UPDATE;
                UPDATE jobs
                   SET location_scope_version = NULL
                 WHERE id = ANY(parent_ids)
                   AND location_scope_version IS NOT NULL;
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

        # Commit the column/trigger transaction, then build indexes concurrently
        # so jobs never holds ACCESS EXCLUSIVE across index builds. Existing rows
        # stay NULL and are backfilled in bounded SQL batches by
        # search_snapshot_worker while queries safely use the indexed fallback.
        with op.get_context().autocommit_block():
            autocommit_bind = op.get_bind()
            for index_name, create_sql in _CONCURRENT_INDEXES:
                _ensure_concurrent_index(autocommit_bind, index_name, create_sql)
    else:
        existing_indexes = {index['name'] for index in inspector.get_indexes('jobs')}
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
        op.execute('DROP TRIGGER IF EXISTS job_locations_invalidate_location_scope ON job_locations')
        op.execute('DROP FUNCTION IF EXISTS invalidate_job_location_scope_on_location_change()')
        op.execute('DROP TRIGGER IF EXISTS jobs_invalidate_location_scope ON jobs')
        op.execute('DROP FUNCTION IF EXISTS invalidate_job_location_scope_on_job_change()')
    else:
        inspector = sa.inspect(bind)
        existing_indexes = {index['name'] for index in inspector.get_indexes('jobs')}
        for index_name, _ in reversed(_CONCURRENT_INDEXES):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name='jobs')

    inspector = sa.inspect(op.get_bind())
    existing_columns = {column['name'] for column in inspector.get_columns('jobs')}
    for column_name in ('location_scope_version', 'location_states', 'germany_candidate'):
        if column_name in existing_columns:
            op.drop_column('jobs', column_name)
