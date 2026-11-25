"""Durable unlimited AI matching queue and worker heartbeat."""
from alembic import op
import sqlalchemy as sa

revision = '0039_durable_ai_matching_queue'
down_revision = '0038_ai_matching_model_setting'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_matching_tasks',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('version', sa.String(160), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('token', sa.String(32)),
        sa.Column('error_type', sa.String(100)))
    op.create_index('ix_ai_matching_tasks_state', 'ai_matching_tasks', ['state'])
    op.create_index('ix_ai_matching_tasks_available_at', 'ai_matching_tasks', ['available_at'])
    op.create_table('ai_matching_workers',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON ai_matching_tasks, ai_matching_workers TO hiring_app')


def downgrade():
    op.drop_table('ai_matching_workers')
    op.drop_table('ai_matching_tasks')
