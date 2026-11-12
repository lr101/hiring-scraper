"""Persist the selected AI matching model for API and worker processes."""
from alembic import op
import sqlalchemy as sa

revision = '0038_ai_matching_model_setting'
down_revision = '0037_journey_match_progress'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_matching_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_matching_settings TO hiring_app")


def downgrade():
    op.drop_table('ai_matching_settings')
