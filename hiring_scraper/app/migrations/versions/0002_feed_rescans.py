"""Add feed leases, scheduled rescans and job closure history."""
from alembic import op
import sqlalchemy as sa


revision = "0002_feed_rescans"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_feeds", sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("job_feeds", sa.Column("next_scan_at", sa.DateTime(timezone=True)))
    op.add_column("job_feeds", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.add_column("job_feeds", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("job_feeds", sa.Column("etag", sa.String(length=500)))
    op.add_column("job_feeds", sa.Column("last_modified", sa.String(length=255)))
    op.add_column("job_feeds", sa.Column("last_error", sa.Text()))
    op.create_index("ix_job_feeds_next_scan_at", "job_feeds", ["next_scan_at"])
    op.add_column("jobs", sa.Column("missing_complete_scans", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feed_id", sa.Integer(), sa.ForeignKey("job_feeds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("item_count", sa.Integer()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_scan_runs_feed_id", "scan_runs", ["feed_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hiring_app")
        op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hiring_app")


def downgrade() -> None:
    op.drop_index("ix_scan_runs_feed_id", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_column("jobs", "closed_at")
    op.drop_column("jobs", "missing_complete_scans")
    op.drop_index("ix_job_feeds_next_scan_at", table_name="job_feeds")
    op.drop_column("job_feeds", "last_error")
    op.drop_column("job_feeds", "last_modified")
    op.drop_column("job_feeds", "etag")
    op.drop_column("job_feeds", "attempt_count")
    op.drop_column("job_feeds", "lease_until")
    op.drop_column("job_feeds", "next_scan_at")
    op.drop_column("job_feeds", "last_attempt_at")
