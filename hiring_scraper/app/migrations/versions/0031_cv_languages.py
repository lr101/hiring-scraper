"""Keep English and German LaTeX base CVs under one revision."""
from alembic import op
import sqlalchemy as sa

revision = '0031_cv_languages'
down_revision = '0030_closed_application_stage'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('profile_cvs', sa.Column('variants', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    op.drop_column('profile_cvs', 'variants')
