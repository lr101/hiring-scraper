"""Track the location company and homepage search before career checks."""
from alembic import op
import sqlalchemy as sa


revision = "0006_location_homepage_stage"
down_revision = "0005_location_discovery_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_jobs", sa.Column(
        "stage", sa.String(length=40), nullable=False,
        server_default="career_page_discovery"))
    op.add_column("discovery_jobs", sa.Column(
        "companies_found", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("discovery_jobs", sa.Column(
        "homepages_found", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("discovery_jobs", sa.Column(
        "location_scan_lease_until", sa.DateTime(timezone=True)))
    op.create_index("ix_discovery_jobs_location_scan_lease_until", "discovery_jobs",
                    ["location_scan_lease_until"])


def downgrade() -> None:
    op.drop_index("ix_discovery_jobs_location_scan_lease_until", table_name="discovery_jobs")
    op.drop_column("discovery_jobs", "location_scan_lease_until")
    op.drop_column("discovery_jobs", "homepages_found")
    op.drop_column("discovery_jobs", "companies_found")
    op.drop_column("discovery_jobs", "stage")
