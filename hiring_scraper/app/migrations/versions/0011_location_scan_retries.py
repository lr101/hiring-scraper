"""Persist bounded retries for transient location search failures."""
from alembic import op
import sqlalchemy as sa


revision = "0011_location_scan_retries"
down_revision = "0010_job_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_jobs",
        sa.Column("location_scan_attempt_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("discovery_jobs", "location_scan_attempt_count")
