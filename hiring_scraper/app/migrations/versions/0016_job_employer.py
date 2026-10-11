"""Link feed postings to their hiring company."""
from alembic import op
import sqlalchemy as sa


revision = "0016_job_employer"
down_revision = "0015_managed_feed_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column(
        "employer_company_id", sa.Integer(),
        sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_jobs_employer_company_id", "jobs", ["employer_company_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_employer_company_id", table_name="jobs")
    op.drop_column("jobs", "employer_company_id")
