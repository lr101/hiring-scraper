"""Queue saved discovery scopes for managed source scans."""
from alembic import op
import sqlalchemy as sa


revision = "0019_scheduled_feed_scopes"
down_revision = "0018_managed_feed_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_feeds", sa.Column(
        "pending_search_scopes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("scan_runs", sa.Column("search_scope", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_runs", "search_scope")
    op.drop_column("job_feeds", "pending_search_scopes")
