"""Cache local section embeddings for profile matching on both databases."""
from alembic import op
import sqlalchemy as sa

revision = '0034_profile_semantics'
down_revision = '0033_application_deduplication'
branch_labels = None
depends_on = None


def upgrade():
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


def downgrade():
    op.drop_table('match_semantic_documents')
