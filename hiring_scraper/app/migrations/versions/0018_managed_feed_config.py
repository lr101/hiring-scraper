"""Persist adapter and location settings on scheduled job feeds."""
from alembic import op
import sqlalchemy as sa


revision = "0018_managed_feed_config"
down_revision = "0017_active_germany_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_feeds", sa.Column(
        "source_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("job_feeds", "source_config")
