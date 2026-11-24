"""Store versioned structured application documents, their PDFs and the render lane.

The inference lane uses a dedicated table so the previous image can
continue to use its own queue during an image rollback.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0044_application_documents_v2'
down_revision = '0043_drop_ai_assessment'
branch_labels = None
depends_on = None

_DOCUMENT_KEY = ['profile_id', 'job_id', 'kind', 'version']
_DOCUMENT_REFS = [f'application_documents.{column}' for column in _DOCUMENT_KEY]


def upgrade():
    op.create_table(
        'application_documents',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('kind', sa.String(16), primary_key=True),
        sa.Column('version', sa.Integer(), primary_key=True),
        sa.Column('revision', sa.String(32), nullable=False,
                  server_default=sa.text("replace(gen_random_uuid()::text, '-', '')")),
        sa.Column('language', sa.String(8), nullable=False),
        sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column('origin', sa.String(16), nullable=False),
        sa.Column('stale_inputs', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('input_digest', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('cv', 'letter')", name='ck_application_documents_kind'),
        sa.CheckConstraint('version > 0', name='ck_application_documents_version'),
        if_not_exists=True,
    )
    op.execute("""
        ALTER TABLE application_documents ADD COLUMN IF NOT EXISTS revision VARCHAR(32)
        NOT NULL DEFAULT replace(gen_random_uuid()::text, '-', '')
    """)
    op.create_index('ix_application_documents_job', 'application_documents', ['job_id'], if_not_exists=True)
    op.create_table(
        'application_document_renders',
        sa.Column('profile_id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(16), primary_key=True),
        sa.Column('version', sa.Integer(), primary_key=True),
        sa.Column('template', sa.String(40), primary_key=True),
        sa.Column('pdf', sa.LargeBinary(), nullable=False),
        sa.Column('pages', sa.Integer(), nullable=False),
        sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_DOCUMENT_KEY, _DOCUMENT_REFS, ondelete='CASCADE',
                                name='fk_application_document_renders_document'),
        if_not_exists=True,
    )
    op.create_table(
        'application_render_tasks',
        sa.Column('profile_id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(16), primary_key=True),
        sa.Column('version', sa.Integer(), primary_key=True),
        sa.Column('template', sa.String(40), primary_key=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('available_at', sa.DateTime(timezone=True)),
        sa.Column('lease_token', sa.String(32)),
        sa.Column('error_code', sa.String(64)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_DOCUMENT_KEY, _DOCUMENT_REFS, ondelete='CASCADE',
                                name='fk_application_render_tasks_document'),
        if_not_exists=True,
    )
    op.create_index('ix_application_render_tasks_available_at', 'application_render_tasks', ['available_at'], if_not_exists=True)
    ensure_preparation_queue()
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON application_documents, '
               'application_document_renders, application_render_tasks TO hiring_app')


def ensure_preparation_queue():
    op.create_table(
        'application_document_preparation_tasks',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('record', sa.JSON(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_token', sa.String(32)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True)),
        if_not_exists=True,
    )
    for column in ('state', 'next_attempt_at'):
        op.create_index(f'ix_application_document_preparation_tasks_{column}',
                        'application_document_preparation_tasks', [column], if_not_exists=True)
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON application_document_preparation_tasks TO hiring_app')
    # A pre-release v2 worker may have written to the shared queue. Move its
    # records while the deployment is stopped, releasing leases it no longer owns.
    op.execute("""
        WITH moved AS (
            DELETE FROM application_preparation_tasks
            WHERE record->>'kind' IN ('prepare', 'revise')
            RETURNING profile_id, job_id, record, state, updated_at
        )
        INSERT INTO application_document_preparation_tasks (profile_id, job_id, record, state, updated_at)
        SELECT profile_id, job_id, record, state, updated_at FROM moved
        ON CONFLICT (profile_id, job_id) DO NOTHING
    """)


def downgrade():
    # Binary rollback to 0043 keeps user versions and their dependent rows.
    # The previous image never reads these tables.
    ensure_preparation_queue()
