"""Record the evidence used to associate a candidate with its website."""
from alembic import op
import sqlalchemy as sa


revision = "0003_domain_match_evidence"
down_revision = "0002_feed_rescans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("domain_match_method", sa.String(length=60)))
    op.add_column("companies", sa.Column("domain_evidence_url", sa.Text()))
    op.execute("UPDATE companies SET domain_match_method = 'osm_website_tag', domain_evidence_url = source_url WHERE website_url IS NOT NULL")


def downgrade() -> None:
    op.drop_column("companies", "domain_evidence_url")
    op.drop_column("companies", "domain_match_method")
