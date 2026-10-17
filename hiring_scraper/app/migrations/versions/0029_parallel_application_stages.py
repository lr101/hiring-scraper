"""Add parallel stretch-role and browser-assisted application stages."""
from alembic import op

revision = '0029_parallel_application_stages'
down_revision = '0028_profile_cv'
branch_labels = None
depends_on = None

OLD_STATUSES = "'new', 'open', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted'"


def upgrade():
    with op.batch_alter_table('job_applications') as batch:
        batch.drop_constraint('ck_job_application_status', type_='check')
        batch.create_check_constraint('ck_job_application_status',
            f"status IN ({OLD_STATUSES}, 'in_gots_hand', 'automated_application')")


def downgrade():
    # Preserve tracked jobs and undo history when returning to the old stages.
    for column in ('status', 'previous_status'):
        op.execute(f"UPDATE job_applications SET {column} = CASE {column} "
                   "WHEN 'in_gots_hand' THEN 'open' "
                   "WHEN 'automated_application' THEN 'waiting_for_reply' "
                   f"ELSE {column} END")
    with op.batch_alter_table('job_applications') as batch:
        batch.drop_constraint('ck_job_application_status', type_='check')
        batch.create_check_constraint('ck_job_application_status', f'status IN ({OLD_STATUSES})')
