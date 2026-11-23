"""Store versioned structured application documents, their PDFs and the render lane.

The preparation queue's ``next_attempt_at`` column is applied by the
rollback-safe entrypoint step (``docker/entrypoint.py``), not here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0043_application_documents_v2'
down_revision = '0042_profile_match_explanation'
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
    )
    op.create_index('ix_application_documents_job', 'application_documents', ['job_id'])
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
    )
    op.create_index('ix_application_render_tasks_available_at', 'application_render_tasks', ['available_at'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON application_documents, '
                   'application_document_renders, application_render_tasks TO hiring_app')


def downgrade():
    op.drop_table('application_render_tasks')
    op.drop_table('application_document_renders')
    op.drop_index('ix_application_documents_job', table_name='application_documents')
    op.drop_table('application_documents')
