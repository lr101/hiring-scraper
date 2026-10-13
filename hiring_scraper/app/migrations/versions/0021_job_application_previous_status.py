"""Remember the previous application stage for undo."""
from alembic import op
import sqlalchemy as sa


revision = "0021_job_app_previous_status"
down_revision = "0020_manual_career_page"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_applications", sa.Column("previous_status", sa.String(length=24), nullable=True))


def downgrade() -> None:
    op.drop_column("job_applications", "previous_status")
