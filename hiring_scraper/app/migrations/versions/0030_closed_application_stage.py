"""Track closed postings in the journey without replacing completed outcomes."""
from alembic import op

revision = '0030_closed_application_stage'
down_revision = '0029_parallel_application_stages'
branch_labels = None
depends_on = None

OLD_STATUSES = "'new', 'open', 'in_gots_hand', 'not_interested', 'waiting_for_reply', 'automated_application', 'interview', 'rejected', 'accepted'"


def upgrade():
    with op.batch_alter_table('job_applications') as batch:
        batch.drop_constraint('ck_job_application_status', type_='check')
        batch.create_check_constraint('ck_job_application_status', f"status IN ({OLD_STATUSES}, 'closed')")
    op.execute("""
        UPDATE job_applications
           SET previous_status = status,
               status = CASE WHEN status IN ('waiting_for_reply', 'automated_application') THEN 'rejected' ELSE 'closed' END,
               updated_at = CURRENT_TIMESTAMP
         WHERE job_id IN (SELECT id FROM jobs WHERE NOT is_active)
           AND status NOT IN ('accepted', 'rejected', 'closed')
    """)


def downgrade():
    op.execute(f"""
        UPDATE job_applications
           SET status = CASE WHEN previous_status IN ({OLD_STATUSES}) THEN previous_status ELSE 'not_interested' END
         WHERE status = 'closed'
    """)
    op.execute("UPDATE job_applications SET previous_status = NULL WHERE previous_status = 'closed'")
    with op.batch_alter_table('job_applications') as batch:
        batch.drop_constraint('ck_job_application_status', type_='check')
        batch.create_check_constraint('ck_job_application_status', f'status IN ({OLD_STATUSES})')
