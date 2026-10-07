"""Persist location discovery phase details for live progress reporting."""
from alembic import op
import sqlalchemy as sa


revision = "0008_discovery_job_progress"
down_revision = "0007_location_scan_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_jobs", sa.Column("progress_message", sa.String(length=500)))
    op.add_column("discovery_jobs", sa.Column("progress_updated_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("discovery_jobs", "progress_updated_at")
    op.drop_column("discovery_jobs", "progress_message")
