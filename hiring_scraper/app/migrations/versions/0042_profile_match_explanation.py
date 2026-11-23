"""Store version-stamped match explanations alongside profile job scores."""
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
    op.create_table(
        'ai_matching_observed_config',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version', sa.String(length=160), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_matching_observed_config TO hiring_app")


def downgrade():
    op.drop_table('ai_matching_observed_config')
    op.drop_column('profile_job_matches', 'score_version')
    op.drop_column('profile_job_matches', 'explanation')
