"""Precomputed Germany and German-state scope columns, indexes, and batched backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from hiring_scraper.app.job_location_scope import backfill_job_location_scopes_connection


revision = '0046_job_location_scope'
down_revision = '0045_local_job_analysis'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column['name'] for column in inspector.get_columns('jobs')}
    existing_indexes = {index['name'] for index in inspector.get_indexes('jobs')}

    if 'germany_candidate' not in existing_columns:
        op.add_column('jobs', sa.Column('germany_candidate', sa.Boolean(), nullable=True))
    if 'location_states' not in existing_columns:
        op.add_column('jobs', sa.Column('location_states', postgresql.ARRAY(sa.Text()), nullable=True))
    if 'location_scope_version' not in existing_columns:
        op.add_column('jobs', sa.Column('location_scope_version', sa.String(40), nullable=True))

    if 'ix_jobs_germany_candidate' not in existing_indexes:
        op.create_index(
            'ix_jobs_germany_candidate',
            'jobs',
            ['is_active', 'id'],
            postgresql_where=sa.text('germany_candidate IS TRUE'),
        )
    if 'ix_jobs_location_states' not in existing_indexes:
        op.create_index(
            'ix_jobs_location_states',
            'jobs',
            ['location_states'],
            postgresql_using='gin',
            postgresql_with={'fastupdate': 'off'},
        )
    if 'ix_jobs_location_scope_version' not in existing_indexes:
        op.create_index(
            'ix_jobs_location_scope_version',
            'jobs',
            ['location_scope_version', 'id'],
        )
    if 'ix_jobs_remote_candidate_active' not in existing_indexes:
        op.create_index(
            'ix_jobs_remote_candidate_active',
            'jobs',
            ['id'],
            postgresql_where=sa.text(
                "is_active IS TRUE AND (work_arrangement = 'remote' "
                "OR (is_remote IS TRUE AND work_arrangement IS NULL))"
            ),
        )

    if bind.dialect.name == 'postgresql':
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO hiring_app')
        # Backfill in bounded autocommitted batches so row locks are released
        # after each batch instead of being held across the entire inventory.
        with op.get_context().autocommit_block():
            backfill_job_location_scopes_connection(op.get_bind(), batch_size=500)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column['name'] for column in inspector.get_columns('jobs')}
    existing_indexes = {index['name'] for index in inspector.get_indexes('jobs')}

    for index_name in (
        'ix_jobs_remote_candidate_active',
        'ix_jobs_location_scope_version',
        'ix_jobs_location_states',
        'ix_jobs_germany_candidate',
    ):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name='jobs')

    for column_name in ('location_scope_version', 'location_states', 'germany_candidate'):
        if column_name in existing_columns:
            op.drop_column('jobs', column_name)
