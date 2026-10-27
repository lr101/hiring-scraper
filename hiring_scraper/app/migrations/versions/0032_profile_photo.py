"""Persist an optional PNG portrait per profile, separate from prompt content."""
from alembic import op
import sqlalchemy as sa

revision = '0032_profile_photo'
down_revision = '0031_cv_languages'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('profile_photos',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('filename', sa.String(200), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('profile_photos')
