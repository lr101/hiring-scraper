"""Local-LLM job analysis and reranker evidence tables; matcher v3 rebuild."""
from alembic import op
import sqlalchemy as sa

revision = '0044_local_job_analysis'
down_revision = '0043_drop_ai_assessment'
branch_labels = None
depends_on = None

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
    # Section vectors served only the retired static matchers.
    op.execute('DROP TABLE IF EXISTS match_semantic_documents')
    op.create_table(
        'job_analyses',
        sa.Column('job_id', sa.Integer, sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('version', sa.String(200), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('result_hash', sa.String(64), nullable=False, server_default=''),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('error', sa.String(300)),
        sa.Column('analysed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retry_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_job_analyses_status', 'job_analyses', ['status'])
    op.create_table(
        'profile_evidence_units',
        sa.Column('profile_id', sa.Integer, sa.ForeignKey('user_profiles.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('version', sa.String(200), nullable=False),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('vectors', sa.LargeBinary, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'match_evidence',
        sa.Column('profile_id', sa.Integer, sa.ForeignKey('user_profiles.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('job_id', sa.Integer, sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('version', sa.String(200), nullable=False),
        sa.Column('analysis_hash', sa.String(64), nullable=False),
        sa.Column('units_hash', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_match_evidence_job_id', 'match_evidence', ['job_id'])
    op.create_table(
        'job_analysis_queue',
        sa.Column('job_id', sa.Integer, sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retry_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_job_analysis_queue_queued_at', 'job_analysis_queue', ['queued_at'])
    op.create_table(
        'match_evidence_queue',
        sa.Column('profile_id', sa.Integer, sa.ForeignKey('user_profiles.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('job_id', sa.Integer, sa.ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retry_at', sa.DateTime(timezone=True)),
        sa.Column('attempts', sa.Integer, nullable=False, server_default='0'),
    )
    op.create_index('ix_match_evidence_queue_job_id', 'match_evidence_queue', ['job_id'])
    op.create_index('ix_match_evidence_queue_queued_at', 'match_evidence_queue', ['queued_at'])
    # Every active posting needs a first analysis.
    op.execute("INSERT INTO job_analysis_queue (job_id, queued_at) SELECT id, now() FROM jobs WHERE is_active")
    # Static keyword scores are retired: hide them until the local models have
    # analysed each job. User-set Journey stages and user-created rows stay.
    op.execute(sa.delete(_profile_match_progress))
    op.execute(sa.delete(_profile_job_matches))
    op.execute(
        sa.delete(_job_applications).where(
            _job_applications.c.status == 'new',
            _job_applications.c.auto_created.is_(True),
        )
    )
    op.execute(sa.update(_user_profiles).values(match_index_version=None, presentation_refresh_pending=False))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON '
            'job_analyses, profile_evidence_units, match_evidence, job_analysis_queue, match_evidence_queue '
            'TO hiring_app'
        )


def downgrade():
    op.drop_table('match_evidence_queue')
    op.drop_table('job_analysis_queue')
    op.drop_index('ix_match_evidence_job_id', table_name='match_evidence')
    op.drop_table('match_evidence')
    op.drop_table('profile_evidence_units')
    op.drop_index('ix_job_analyses_status', table_name='job_analyses')
    op.drop_table('job_analyses')
    # Restore the (empty) section-vector cache so earlier downgrades can drop it.
    op.create_table('match_semantic_documents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id', ondelete='CASCADE'), unique=True),
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), unique=True),
        sa.Column('source_hash', sa.String(64), nullable=False),
        sa.Column('version', sa.String(160), nullable=False),
        sa.Column('sections', sa.JSON(), nullable=False),
        sa.Column('vectors', sa.LargeBinary(), nullable=False),
        sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('(job_id IS NULL AND profile_id IS NOT NULL) OR '
                           '(job_id IS NOT NULL AND profile_id IS NULL)', name='ck_match_semantic_document_owner'))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON match_semantic_documents TO hiring_app')
        op.execute('GRANT USAGE, SELECT ON SEQUENCE match_semantic_documents_id_seq TO hiring_app')
