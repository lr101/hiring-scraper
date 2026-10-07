"""Saved profile preferences and versioned job enrichment."""
from alembic import op
import sqlalchemy as sa

revision = '0009_profiles_enrichment'
down_revision = '0008_discovery_job_progress'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('enrichment', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table('user_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('preferences', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('user_profiles')
    op.drop_column('jobs', 'enrichment')
