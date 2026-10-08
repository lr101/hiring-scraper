"""Track job applications independently for each saved profile."""
from alembic import op
import sqlalchemy as sa

revision = '0010_job_applications'
down_revision = '0009_profiles_enrichment'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('job_applications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('profile_id', 'job_id', name='uq_job_application_profile_job'),
        sa.CheckConstraint("status IN ('new', 'open', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted')",
                           name='ck_job_application_status'))
    op.create_index('ix_job_applications_profile_status', 'job_applications', ['profile_id', 'status'])
    op.create_index('ix_job_applications_job_id', 'job_applications', ['job_id'])


def downgrade():
    op.drop_table('job_applications')
