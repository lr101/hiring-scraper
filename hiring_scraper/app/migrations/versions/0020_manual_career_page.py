"""Preserve manually selected company career pages."""
from alembic import op
import sqlalchemy as sa


revision = "0020_manual_career_page"
down_revision = "0019_scheduled_feed_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column(
        "career_url_manual", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("companies", "career_url_manual")
