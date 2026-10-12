"""Link feed postings to their hiring company."""
from alembic import op
import sqlalchemy as sa


revision = "0016_job_employer"
down_revision = "0015_managed_feed_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("employer_company_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_employer_company_id_companies", "companies",
            ["employer_company_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_jobs_employer_company_id", "jobs", ["employer_company_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_employer_company_id", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_employer_company_id_companies", type_="foreignkey")
        batch.drop_column("employer_company_id")
