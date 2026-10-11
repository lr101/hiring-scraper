"""Support whole-state discovery campaigns and observed employer state membership."""
from alembic import op
import sqlalchemy as sa

revision = "0012_state_search"
down_revision = "0011_location_scan_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("configured_locations", "discovery_jobs"):
        op.add_column(table, sa.Column("search_scope", sa.String(16), nullable=False, server_default="radius"))
    op.add_column("companies", sa.Column("state", sa.String(120), nullable=True))
    op.create_index("ix_companies_state", "companies", ["state"])


def downgrade() -> None:
    op.drop_index("ix_companies_state", table_name="companies")
    op.drop_column("companies", "state")
    for table in ("discovery_jobs", "configured_locations"):
        op.drop_column(table, "search_scope")
