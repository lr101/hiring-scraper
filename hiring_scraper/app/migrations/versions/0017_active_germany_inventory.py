"""Keep the Germany search active through company career-page checks."""
from alembic import op
import sqlalchemy as sa


revision = "0017_active_germany_inventory"
down_revision = "0016_job_employer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_active_germany_inventory", table_name="discovery_jobs")
    op.execute(sa.text(
        "UPDATE discovery_jobs SET status = 'cancelled', stage = 'complete', "
        "finished_at = CURRENT_TIMESTAMP, error = 'Superseded by the active Germany inventory.' "
        "WHERE search_scope = 'country' "
        "AND stage IN ('company_homepage_discovery', 'career_page_discovery') "
        "AND status IN ('scheduled', 'queued', 'running') AND id <> ("
        "SELECT MIN(id) FROM discovery_jobs WHERE search_scope = 'country' "
        "AND stage IN ('company_homepage_discovery', 'career_page_discovery') "
        "AND status IN ('scheduled', 'queued', 'running'))"
    ))
    predicate = sa.text(
        "search_scope = 'country' AND stage IN ('company_homepage_discovery', 'career_page_discovery') "
        "AND status IN ('scheduled', 'queued', 'running')"
    )
    op.create_index("uq_active_germany_inventory", "discovery_jobs", ["search_scope"], unique=True,
                    postgresql_where=predicate, sqlite_where=predicate)


def downgrade() -> None:
    op.drop_index("uq_active_germany_inventory", table_name="discovery_jobs")
    predicate = sa.text(
        "search_scope = 'country' AND stage = 'company_homepage_discovery' "
        "AND status IN ('scheduled', 'queued', 'running')"
    )
    op.create_index("uq_active_germany_inventory", "discovery_jobs", ["search_scope"], unique=True,
                    postgresql_where=predicate, sqlite_where=predicate)
