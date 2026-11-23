"""Drop AI job-fit assessment tables; job matching is local-only."""
from alembic import op
import sqlalchemy as sa

revision = '0043_drop_ai_assessment'
down_revision = '0042_profile_match_explanation'
branch_labels = None
depends_on = None

_DROP_TABLES = (
    'ai_matching_observed_config',
    'ai_matching_workers',
    'ai_matching_tasks',
    'ai_matching_settings',
    'ai_matching_selections',
    'ai_matching_calls',
    'profile_llm_scores',
)


_profile_match_progress = sa.table('profile_match_progress')
_profile_job_matches = sa.table('profile_job_matches')
_job_applications = sa.table(
    'job_applications',
    sa.column('status', sa.String),
    sa.column('auto_created', sa.Boolean),
)
_user_profiles = sa.table(
    'user_profiles',
    sa.column('match_index_version', sa.String),
    sa.column('presentation_refresh_pending', sa.Boolean),
)


def upgrade():
    for table in _DROP_TABLES:
        op.execute(f'DROP TABLE IF EXISTS {table}')
    # Invalidate legacy derived match state so the profile match worker rebuilds
    # locally without serving AI-derived scores/explanations during backfill.
    # Mirrors job_search.clear_profile_match_scores: preserves all user-set
    # Journey stages and user-created 'new' rows. Downgrade is a no-op for rows.
    op.execute(sa.delete(_profile_match_progress))
    op.execute(sa.delete(_profile_job_matches))
    op.execute(
        sa.delete(_job_applications).where(
            _job_applications.c.status == 'new',
            _job_applications.c.auto_created.is_(True),
        )
    )
    op.execute(
        sa.update(_user_profiles).values(
            match_index_version=None,
            presentation_refresh_pending=False,
        )
    )


def downgrade():
    op.create_table(
        'profile_llm_scores',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('version', sa.String(160), nullable=False),
        sa.Column('assessment', sa.JSON(), nullable=False),
        sa.Column('scored_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'ai_matching_calls',
        sa.Column('day', sa.String(10), primary_key=True),
        sa.Column('slot', sa.Integer(), primary_key=True),
        sa.Column('reserved_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_ai_matching_calls_reserved_at', 'ai_matching_calls', ['reserved_at'])
    op.create_table(
        'ai_matching_selections',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('job_ids', sa.JSON(), nullable=False),
        sa.Column('refreshed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'ai_matching_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'ai_matching_tasks',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('version', sa.String(160), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('token', sa.String(32)),
        sa.Column('error_type', sa.String(100)),
    )
    op.create_index('ix_ai_matching_tasks_state', 'ai_matching_tasks', ['state'])
    op.create_index('ix_ai_matching_tasks_available_at', 'ai_matching_tasks', ['available_at'])
    op.create_table(
        'ai_matching_workers',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON '
            'profile_llm_scores, ai_matching_calls, ai_matching_selections, '
            'ai_matching_settings, ai_matching_tasks, ai_matching_workers '
            'TO hiring_app'
        )
