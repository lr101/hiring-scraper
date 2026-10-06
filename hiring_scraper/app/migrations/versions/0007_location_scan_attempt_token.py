"""Fence late workers from replacing a location search attempt."""
from alembic import op
import sqlalchemy as sa


revision = "0007_location_scan_token"
down_revision = "0006_location_homepage_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_jobs", sa.Column("location_scan_token", sa.String(length=36)))


def downgrade() -> None:
    op.drop_column("discovery_jobs", "location_scan_token")
