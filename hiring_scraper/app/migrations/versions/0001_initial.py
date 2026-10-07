"""Initial employer, feed and job schema."""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("website_url", sa.Text()),
        sa.Column("domain", sa.String(length=255)),
        sa.Column("category", sa.String(length=120)),
        sa.Column("source_url", sa.Text()),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("location_precision", sa.String(length=40)),
        sa.Column("location_label", sa.String(length=255)),
        sa.Column("career_url", sa.Text()),
        sa.Column("career_status", sa.String(length=40), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source", "source_id", name="uq_company_source_record"),
    )
    op.create_index("ix_companies_source", "companies", ["source"])
    op.create_index("ix_companies_name", "companies", ["name"])
    op.create_index("ix_companies_domain", "companies", ["domain"])

    op.create_table(
        "job_feeds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("tenant", sa.String(length=255)),
        sa.Column("board_url", sa.Text()),
        sa.Column("feed_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("job_count", sa.Integer()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("company_id", "provider", "feed_url", name="uq_job_feed_source"),
    )
    op.create_index("ix_job_feeds_company_id", "job_feeds", ["company_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feed_id", sa.Integer(), sa.ForeignKey("job_feeds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("location_text", sa.Text()),
        sa.Column("is_remote", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("work_arrangement", sa.String(length=40)),
        sa.Column("employment_type", sa.String(length=120)),
        sa.Column("schedule", sa.String(length=120)),
        sa.Column("department", sa.String(length=255)),
        sa.Column("seniority", sa.String(length=120)),
        sa.Column("date_posted", sa.String(length=80)),
        sa.Column("salary", sa.String(length=500)),
        sa.Column("raw_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("feed_id", "external_id", name="uq_job_feed_external_id"),
    )
    op.create_index("ix_jobs_feed_id", "jobs", ["feed_id"])
    op.create_index("ix_jobs_title", "jobs", ["title"])
    op.create_index("ix_jobs_remote_active", "jobs", ["is_remote", "is_active"])

    op.create_table(
        "job_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("precision", sa.String(length=40)),
        sa.Column("country_code", sa.String(length=2)),
    )
    op.create_index("ix_job_locations_job_id", "job_locations", ["job_id"])

    op.create_table(
        "location_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_key", sa.String(length=500), nullable=False, unique=True),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
    )

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE companies ADD COLUMN location_geog geography(Point,4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude),4326)::geography) STORED")
        op.execute("ALTER TABLE job_locations ADD COLUMN location_geog geography(Point,4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude),4326)::geography) STORED")
        op.execute("CREATE INDEX ix_companies_location_geog ON companies USING GIST (location_geog)")
        op.execute("CREATE INDEX ix_job_locations_location_geog ON job_locations USING GIST (location_geog)")
        op.execute("GRANT USAGE ON SCHEMA public TO hiring_app")
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hiring_app")
        op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hiring_app")
        op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hiring_app")
        op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO hiring_app")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("ix_job_locations_location_geog", table_name="job_locations")
        op.drop_index("ix_companies_location_geog", table_name="companies")
    op.drop_table("location_cache")
    op.drop_index("ix_job_locations_job_id", table_name="job_locations")
    op.drop_table("job_locations")
    op.drop_index("ix_jobs_remote_active", table_name="jobs")
    op.drop_index("ix_jobs_title", table_name="jobs")
    op.drop_index("ix_jobs_feed_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_job_feeds_company_id", table_name="job_feeds")
    op.drop_table("job_feeds")
    op.drop_index("ix_companies_domain", table_name="companies")
    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_index("ix_companies_source", table_name="companies")
    op.drop_table("companies")
