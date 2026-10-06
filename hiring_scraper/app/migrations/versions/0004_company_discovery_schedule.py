"""Schedule repeatable, leased company career discovery."""
from alembic import op
import sqlalchemy as sa


revision = "0004_company_discovery_schedule"
down_revision = "0003_domain_match_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("next_discovery_at", sa.DateTime(timezone=True)))
    op.add_column("companies", sa.Column("discovery_lease_until", sa.DateTime(timezone=True)))
    op.add_column("companies", sa.Column("discovery_attempt_count", sa.Integer(), nullable=False,
                                         server_default=sa.text("0")))
    op.add_column("companies", sa.Column("discovery_error", sa.Text()))
    op.create_index("ix_companies_next_discovery_at", "companies", ["next_discovery_at"])
    op.create_index("ix_companies_discovery_lease_until", "companies", ["discovery_lease_until"])

    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("pages_checked", sa.Integer()),
        sa.Column("jobs_found", sa.Integer()),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_discovery_runs_company_id", "discovery_runs", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_discovery_runs_company_id", table_name="discovery_runs")
    op.drop_table("discovery_runs")
    op.drop_index("ix_companies_discovery_lease_until", table_name="companies")
    op.drop_index("ix_companies_next_discovery_at", table_name="companies")
    op.drop_column("companies", "discovery_error")
    op.drop_column("companies", "discovery_attempt_count")
    op.drop_column("companies", "discovery_lease_until")
    op.drop_column("companies", "next_discovery_at")
