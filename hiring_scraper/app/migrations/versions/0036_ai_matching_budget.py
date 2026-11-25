"""Count matching provider attempts durably across workers and restarts."""
from alembic import op
import sqlalchemy as sa

revision = '0036_ai_matching_budget'
down_revision = '0035_profile_llm_scores'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_matching_calls',
        sa.Column('day', sa.String(10), primary_key=True),
        sa.Column('slot', sa.Integer(), primary_key=True),
        sa.Column('reserved_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_ai_matching_calls_reserved_at', 'ai_matching_calls', ['reserved_at'])

    op.create_table('ai_matching_selections',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('job_ids', sa.JSON(), nullable=False),
        sa.Column('refreshed_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('ai_matching_selections')
    op.drop_table('ai_matching_calls')
