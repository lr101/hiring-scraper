"""Persist one original CV and its extracted text per profile."""
from alembic import op
import sqlalchemy as sa

revision = '0028_profile_cv'
down_revision = '0027_merge_journey_and_main'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('profile_cvs',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('filename', sa.String(200), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('extracted_text', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('profile_cvs')
