"""Store version-stamped match explanations and the pending presentation refresh flag."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0042_profile_match_explanation'
down_revision = '0041_profile_source_version'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'profile_job_matches',
        sa.Column('explanation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'profile_job_matches',
        sa.Column('score_version', sa.String(length=80), nullable=True),
    )
    op.add_column(
        'user_profiles',
        sa.Column('presentation_refresh_pending', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade():
    op.drop_column('user_profiles', 'presentation_refresh_pending')
    op.drop_column('profile_job_matches', 'score_version')
    op.drop_column('profile_job_matches', 'explanation')
