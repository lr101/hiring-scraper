"""Store durable application preparation jobs."""
from alembic import op
import sqlalchemy as sa

revision = '0040_application_preparation'
down_revision = '0039_durable_ai_matching_queue'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'application_preparation_tasks',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('record', sa.JSON(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_token', sa.String(32)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_application_preparation_tasks_state', 'application_preparation_tasks', ['state'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON application_preparation_tasks TO hiring_app')


def downgrade():
    op.drop_table('application_preparation_tasks')
