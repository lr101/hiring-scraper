"""Persist ordered search results and index common company/job queries."""
from alembic import op
import sqlalchemy as sa


revision = "0022_materialized_search"
down_revision = "0021_job_app_previous_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("user_profiles", sa.Column("match_index_version", sa.String(length=40), nullable=True))
    op.add_column("job_applications", sa.Column(
        "auto_created", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "profile_job_matches",
        sa.Column("profile_id", sa.Integer(),
                  sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("job_id", sa.Integer(),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("match_tier", sa.String(length=24), nullable=False),
        sa.Column("match_uncertain", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_profile_job_matches_profile_score", "profile_job_matches",
                    ["profile_id", "match_score", "job_id"])
    op.create_index("ix_profile_job_matches_job_profile", "profile_job_matches",
                    ["job_id", "profile_id"])

    op.create_table(
        "job_search_state",
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    op.execute(sa.text("INSERT INTO job_search_state DEFAULT VALUES"))
    op.create_table(
        "job_search_snapshots",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_job_search_snapshots_generation", "job_search_snapshots", ["generation"])
    op.create_index("ix_job_search_snapshots_created_at", "job_search_snapshots", ["created_at"])
    op.create_table(
        "job_search_results",
        sa.Column("snapshot_key", sa.String(length=64),
                  sa.ForeignKey("job_search_snapshots.key", ondelete="CASCADE"), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scope", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("scope_uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("posted_rank", sa.Float(), nullable=False),
        sa.Column("discovered_rank", sa.Float(), nullable=False),
        sa.Column("title_sort", sa.Text(), nullable=False),
        sa.Column("company_sort", sa.Text(), nullable=False),
        sa.Column("remote_sort", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_job_search_results_snapshot_sort", "job_search_results",
                    ["snapshot_key", "posted_rank", "discovered_rank", "job_id"])

    op.create_index("ix_companies_latitude_longitude", "companies", ["latitude", "longitude"])
    op.create_index("ix_job_locations_latitude_longitude", "job_locations", ["latitude", "longitude"])
    op.create_index("ix_jobs_active_id", "jobs", ["id"],
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active = 1"))

    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("CREATE INDEX ix_companies_name_trgm ON companies USING GIN (name gin_trgm_ops)")
        op.execute("CREATE INDEX ix_companies_domain_trgm ON companies USING GIN (domain gin_trgm_ops)")
        op.execute("CREATE INDEX ix_jobs_title_trgm ON jobs USING GIN (title gin_trgm_ops)")
        op.execute("CREATE INDEX ix_jobs_hiring_org_name_trgm ON jobs USING GIN "
                   "((raw_metadata -> 'hiring_organization' ->> 'name') gin_trgm_ops)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_jobs_hiring_org_name_trgm")
        op.execute("DROP INDEX IF EXISTS ix_jobs_title_trgm")
        op.execute("DROP INDEX IF EXISTS ix_companies_domain_trgm")
        op.execute("DROP INDEX IF EXISTS ix_companies_name_trgm")
    op.drop_index("ix_jobs_active_id", table_name="jobs")
    op.drop_index("ix_job_locations_latitude_longitude", table_name="job_locations")
    op.drop_index("ix_companies_latitude_longitude", table_name="companies")
    op.drop_index("ix_job_search_results_snapshot_sort", table_name="job_search_results")
    op.drop_table("job_search_results")
    op.drop_index("ix_job_search_snapshots_created_at", table_name="job_search_snapshots")
    op.drop_index("ix_job_search_snapshots_generation", table_name="job_search_snapshots")
    op.drop_table("job_search_snapshots")
    op.drop_table("job_search_state")
    op.drop_index("ix_profile_job_matches_job_profile", table_name="profile_job_matches")
    op.drop_index("ix_profile_job_matches_profile_score", table_name="profile_job_matches")
    op.drop_table("profile_job_matches")
    op.drop_column("job_applications", "auto_created")
    op.drop_column("user_profiles", "match_index_version")
