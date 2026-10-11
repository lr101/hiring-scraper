"""Pace missing homepage verification and coalesce Germany map inventories."""
from alembic import op
import sqlalchemy as sa


revision = "0014_paced_homepage_resolution"
down_revision = "0013_ranked_germany"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("homepage_source_tags", sa.JSON(), nullable=True),
        sa.Column("homepage_resolution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("homepage_resolution_next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("homepage_resolution_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("homepage_resolution_token", sa.String(36), nullable=True),
    ):
        op.add_column("companies", column)
    op.create_index("ix_companies_homepage_resolution_next_at", "companies", ["homepage_resolution_next_at"])
    op.create_index("ix_companies_homepage_resolution_lease_until", "companies", ["homepage_resolution_lease_until"])

    # Keep the oldest in-progress national inventory. The set-based update also
    # works in Alembic's offline SQL-generation mode.
    op.execute(sa.text(
        "UPDATE discovery_jobs SET status = 'cancelled', stage = 'complete', "
        "finished_at = CURRENT_TIMESTAMP, error = 'Superseded by the active Germany inventory.' "
        "WHERE search_scope = 'country' AND stage = 'company_homepage_discovery' "
        "AND status IN ('scheduled', 'queued', 'running') AND id <> ("
        "SELECT MIN(id) FROM discovery_jobs WHERE search_scope = 'country' "
        "AND stage = 'company_homepage_discovery' AND status IN ('scheduled', 'queued', 'running'))"
    ))

    predicate = sa.text(
        "search_scope = 'country' AND stage = 'company_homepage_discovery' "
        "AND status IN ('scheduled', 'queued', 'running')"
    )
    op.create_index("uq_active_germany_inventory", "discovery_jobs", ["search_scope"], unique=True,
                    postgresql_where=predicate, sqlite_where=predicate)


def downgrade() -> None:
    op.drop_index("uq_active_germany_inventory", table_name="discovery_jobs")
    op.drop_index("ix_companies_homepage_resolution_lease_until", table_name="companies")
    op.drop_index("ix_companies_homepage_resolution_next_at", table_name="companies")
    for column in ("homepage_resolution_lease_until", "homepage_resolution_next_at",
                   "homepage_resolution_token", "homepage_resolution_attempts", "homepage_source_tags"):
        op.drop_column("companies", column)
