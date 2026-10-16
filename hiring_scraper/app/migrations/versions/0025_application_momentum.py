"""Persist weekly application pace and application milestones."""
from alembic import op
import sqlalchemy as sa


revision = "0025_application_momentum"
down_revision = "0024_semantic_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    op.create_table(
        "application_momentum_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("event_type IN ('applied', 'interview', 'accepted')",
                           name="ck_application_momentum_event_type"),
    )
    op.create_index("ix_application_momentum_events_type_date", "application_momentum_events",
                    ["event_type", "occurred_at"])
    op.create_index("ix_application_momentum_events_job", "application_momentum_events", ["job_id"])

    op.execute(sa.text("""
        INSERT INTO application_momentum_events
            (profile_id, job_id, event_type, occurred_at, is_legacy)
        SELECT application.profile_id, application.job_id, 'applied', application.updated_at, true
          FROM job_applications AS application
         WHERE (
                application.status IN ('waiting_for_reply', 'interview', 'accepted')
                OR (application.status = 'rejected'
                    AND application.previous_status IN ('waiting_for_reply', 'interview', 'accepted'))
               )
           AND NOT EXISTS (
                SELECT 1
                  FROM application_momentum_events AS existing
                 WHERE existing.profile_id = application.profile_id
                   AND existing.job_id = application.job_id
                   AND existing.event_type = 'applied'
                   AND existing.is_legacy = true
           )
    """))

    op.execute(sa.text("""
        INSERT INTO application_momentum_events
            (profile_id, job_id, event_type, occurred_at, is_legacy)
        SELECT application.profile_id, application.job_id, 'interview', application.updated_at, true
          FROM job_applications AS application
         WHERE (application.status = 'interview' OR application.previous_status = 'interview')
           AND NOT EXISTS (
                SELECT 1
                  FROM application_momentum_events AS existing
                 WHERE existing.profile_id = application.profile_id
                   AND existing.job_id = application.job_id
                   AND existing.event_type = 'interview'
                   AND existing.is_legacy = true
           )
    """))

    op.execute(sa.text("""
        INSERT INTO application_momentum_events
            (profile_id, job_id, event_type, occurred_at, is_legacy)
        SELECT application.profile_id, application.job_id, 'accepted', application.updated_at, true
          FROM job_applications AS application
         WHERE (application.status = 'accepted' OR application.previous_status = 'accepted')
           AND NOT EXISTS (
                SELECT 1
                  FROM application_momentum_events AS existing
                 WHERE existing.profile_id = application.profile_id
                   AND existing.job_id = application.job_id
                   AND existing.event_type = 'accepted'
                   AND existing.is_legacy = true
           )
    """))


def downgrade() -> None:
    op.drop_index("ix_application_momentum_events_job", table_name="application_momentum_events")
    op.drop_index("ix_application_momentum_events_type_date", table_name="application_momentum_events")
    op.drop_table("application_momentum_events")
    op.drop_table("application_momentum_settings")
