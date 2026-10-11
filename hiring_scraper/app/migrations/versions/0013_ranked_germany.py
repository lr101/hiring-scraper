"""Durable company relevance rankings and resumable national discovery."""
from alembic import op
import sqlalchemy as sa

revision = '0013_ranked_germany'
down_revision = '0012_state_search'
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column('country_code',sa.String(2)), sa.Column('employee_count',sa.Integer()),
        sa.Column('size_evidence',sa.JSON(),nullable=False,server_default='{}'),
        sa.Column('crawl_priority',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('crawl_interval_days',sa.Integer(),server_default='7'), sa.Column('crawl_reason',sa.Text()),
        sa.Column('crawl_paused',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('irrelevant_checks',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('ranking_updated_at',sa.DateTime(timezone=True)),
    ):
        op.add_column('companies',column)
    op.create_index('ix_companies_country_code','companies',['country_code'])
    op.create_index('ix_companies_crawl_priority','companies',['crawl_priority'])
    op.add_column('discovery_jobs',sa.Column('map_cursor',sa.JSON(),nullable=False,server_default='{}'))
    op.execute("UPDATE companies SET country_code='DE' WHERE state IS NOT NULL")


def downgrade():
    op.drop_column('discovery_jobs','map_cursor')
    for name in ('country_code','crawl_priority'):
        op.drop_index('ix_companies_'+name,table_name='companies')
    for name in ('ranking_updated_at','irrelevant_checks','crawl_paused','crawl_reason','crawl_interval_days',
                 'crawl_priority','size_evidence','employee_count','country_code'):
        op.drop_column('companies',name)
