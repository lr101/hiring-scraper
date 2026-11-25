"""Queue source invalidations and checkpoint bounded Journey backfills."""
from alembic import op
import sqlalchemy as sa

revision = '0037_journey_match_progress'
down_revision = '0036_ai_matching_budget'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('match_dirty_jobs',
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.String(32), nullable=False),
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_match_dirty_jobs_queued_at', 'match_dirty_jobs', ['queued_at'])
    op.create_table('profile_match_progress',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('version', sa.String(40), nullable=False),
        sa.Column('cursor', sa.Integer(), nullable=False),
        sa.Column('high_water', sa.Integer(), nullable=False),
        sa.Column('advanced_at', sa.DateTime(timezone=True), nullable=False))
    # An existing current profile may already have missed a failed ingestion
    # refresh. Reconcile once after upgrade with bounded, restartable pages.
    op.execute('UPDATE user_profiles SET match_index_version = NULL')


def downgrade():
    op.drop_table('profile_match_progress')
    op.drop_table('match_dirty_jobs')
