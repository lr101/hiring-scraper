"""Join the journey and PostgreSQL maintenance migration histories."""


revision = "0027_merge_journey_and_main"
down_revision = ("0026_weekly_goal_environment", "0025_postgres_collation_repair")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
