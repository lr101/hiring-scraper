"""Use the application weekly goal from the environment."""
from alembic import op
import sqlalchemy as sa


revision = "0026_weekly_goal_environment"
down_revision = "0025_application_momentum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("application_momentum_settings")


def downgrade() -> None:
    op.create_table(
        "application_momentum_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("weekly_goal", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("weekly_goal >= 0 AND weekly_goal <= 10",
                           name="ck_application_weekly_goal_range"),
    )
    op.execute(sa.text(
        "INSERT INTO application_momentum_settings (id, weekly_goal, updated_at) "
        "VALUES (1, 10, CURRENT_TIMESTAMP)"
    ))
