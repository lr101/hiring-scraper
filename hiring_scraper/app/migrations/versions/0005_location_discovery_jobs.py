"""Add configured locations and durable location discovery campaigns."""
from alembic import op
import sqlalchemy as sa


revision = "0005_location_discovery_jobs"
down_revision = "0004_company_discovery_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "configured_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=120)),
        sa.Column("state", sa.String(length=120)),
        sa.Column("postcode", sa.String(length=20)),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("radius_km", sa.Float(), nullable=False, server_default=sa.text("15")),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default=sa.text("7")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_configured_locations_next_run_at", "configured_locations", ["next_run_at"])

    op.create_table(
        "discovery_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("configured_location_id", sa.Integer(),
                  sa.ForeignKey("configured_locations.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="manual"),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=120)),
        sa.Column("state", sa.String(length=120)),
        sa.Column("postcode", sa.String(length=20)),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("radius_km", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("candidate_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("jobs_found", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_discovery_jobs_configured_location_id", "discovery_jobs", ["configured_location_id"])
    op.create_index("ix_discovery_jobs_status", "discovery_jobs", ["status"])
    op.create_index("ix_discovery_jobs_scheduled_for", "discovery_jobs", ["scheduled_for"])

    op.create_table(
        "discovery_job_companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("discovery_job_id", sa.Integer(),
                  sa.ForeignKey("discovery_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discovery_run_id", sa.Integer(), sa.ForeignKey("discovery_runs.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("jobs_found", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("discovery_job_id", "company_id", name="uq_discovery_job_company"),
    )
    op.create_index("ix_discovery_job_companies_discovery_run_id", "discovery_job_companies", ["discovery_run_id"])
    op.create_index("ix_discovery_job_companies_status", "discovery_job_companies", ["status"])
    op.create_index("ix_discovery_job_companies_claim", "discovery_job_companies", ["status", "discovery_job_id"])


def downgrade() -> None:
    op.drop_index("ix_discovery_job_companies_claim", table_name="discovery_job_companies")
    op.drop_index("ix_discovery_job_companies_status", table_name="discovery_job_companies")
    op.drop_index("ix_discovery_job_companies_discovery_run_id", table_name="discovery_job_companies")
    op.drop_table("discovery_job_companies")
    op.drop_index("ix_discovery_jobs_scheduled_for", table_name="discovery_jobs")
    op.drop_index("ix_discovery_jobs_status", table_name="discovery_jobs")
    op.drop_index("ix_discovery_jobs_configured_location_id", table_name="discovery_jobs")
    op.drop_table("discovery_jobs")
    op.drop_index("ix_configured_locations_next_run_at", table_name="configured_locations")
    op.drop_table("configured_locations")
