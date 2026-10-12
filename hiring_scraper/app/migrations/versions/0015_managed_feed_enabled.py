"""Track managed feed revisions and allow feeds to be paused."""
from alembic import op
import sqlalchemy as sa


revision = "0015_managed_feed_enabled"
down_revision = "0014_paced_homepage_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_feeds", sa.Column(
        "enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("job_feeds", sa.Column(
        "source_revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("job_feeds", "source_revision")
    op.drop_column("job_feeds", "enabled")
