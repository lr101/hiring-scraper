"""Queue stale search snapshot refreshes outside API requests."""
from alembic import op
import sqlalchemy as sa


revision = "0023_search_snapshot_refresh_queue"
down_revision = "0022_materialized_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_search_snapshot_refreshes",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_job_search_snapshot_refresh_claim",
        "job_search_snapshot_refreshes",
        ["requested_at", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_search_snapshot_refresh_claim", table_name="job_search_snapshot_refreshes")
    op.drop_table("job_search_snapshot_refreshes")
