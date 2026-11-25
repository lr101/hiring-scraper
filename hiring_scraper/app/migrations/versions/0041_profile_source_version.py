"""Count profile input changes so publishers verify a version instead of holding locks."""
from alembic import op
import sqlalchemy as sa

revision = '0041_profile_source_version'
down_revision = '0040_application_preparation'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_profiles', sa.Column('source_version', sa.BigInteger(), nullable=False,
                                             server_default=sa.text('0')))


def downgrade():
    op.drop_column('user_profiles', 'source_version')
