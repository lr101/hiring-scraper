"""Persist profile-specific Luna job assessments."""
from alembic import op
import sqlalchemy as sa

revision = '0035_profile_llm_scores'
down_revision = '0034_profile_semantics'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('profile_llm_scores',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('version', sa.String(160), nullable=False),
        sa.Column('assessment', sa.JSON(), nullable=False),
        sa.Column('scored_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('profile_llm_scores')
