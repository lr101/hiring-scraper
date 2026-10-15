"""Store the city vote preference outside the rollback-sensitive JSON profile."""
from alembic import op
import sqlalchemy as sa


revision = "0026_profile_afd_vote_weight"
down_revision = "0025_postgres_collation_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.add_column("user_profiles", sa.Column(
        "low_afd_vote_share_weight", sa.Integer(), nullable=False,
        server_default=sa.text("0")))
    op.execute("""
        UPDATE user_profiles
           SET low_afd_vote_share_weight = LEAST(20, GREATEST(0,
                   COALESCE(NULLIF(preferences::jsonb -> 'matching_defaults'
                       ->> 'low_afd_vote_share_weight', '')::integer, 0))),
               preferences = jsonb_set(
                   preferences::jsonb,
                   '{matching_defaults}',
                   (preferences::jsonb -> 'matching_defaults') - 'low_afd_vote_share_weight',
                   true
               )::json
         WHERE (preferences::jsonb -> 'matching_defaults') ? 'low_afd_vote_share_weight'
    """)


def downgrade() -> None:
    # Keep this additive column so a binary rollback does not discard saved
    # preferences or require a profile-JSON cleanup before the older app runs.
    pass
