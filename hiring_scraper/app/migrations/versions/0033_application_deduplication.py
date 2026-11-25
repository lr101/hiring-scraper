"""Consolidate existing duplicate Journey states, retaining a rollback archive."""
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.orm import Session
import sqlalchemy as sa

from hiring_scraper.app.application_deduplication import (
    migrate_existing_application_duplicates, restore_archived_applications,
)
from hiring_scraper.app.models import (
    AppDataMigration, JobApplicationDeduplicationArchive, JobVacancyGroup, JobVacancyIdentity,
)

revision = '0033_application_deduplication'
down_revision = '0032_profile_photo'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    JobVacancyIdentity.__table__.create(connection, checkfirst=True)
    JobVacancyGroup.__table__.create(connection, checkfirst=True)
    JobApplicationDeduplicationArchive.__table__.create(connection, checkfirst=True)
    AppDataMigration.__table__.create(connection, checkfirst=True)
    job_columns = {column['name'] for column in inspect(connection).get_columns('jobs')}
    if 'vacancy_group_id' in job_columns:
        connection.execute(sa.text(
            'INSERT INTO job_vacancy_groups (job_id, vacancy_group_id) '
            'SELECT id, vacancy_group_id FROM jobs WHERE vacancy_group_id IS NOT NULL '
            'ON CONFLICT (job_id) DO UPDATE SET vacancy_group_id = excluded.vacancy_group_id'
        ))
        if connection.dialect.name == 'sqlite':
            with op.batch_alter_table('jobs', recreate='always') as batch:
                batch.drop_column('vacancy_group_id')
        else:
            job_indexes = {index['name'] for index in inspect(connection).get_indexes('jobs')}
            if 'ix_jobs_vacancy_group_id' in job_indexes:
                op.drop_index('ix_jobs_vacancy_group_id', table_name='jobs')
            constraints = inspect(connection).get_foreign_keys('jobs')
            for constraint in constraints:
                if (constraint.get('constrained_columns') == ['vacancy_group_id']
                        and constraint.get('referred_table') == 'jobs' and constraint.get('name')):
                    op.drop_constraint(constraint['name'], 'jobs', type_='foreignkey')
            op.drop_column('jobs', 'vacancy_group_id')
    with Session(bind=connection) as session:
        migrate_existing_application_duplicates(session)
        session.flush()


def downgrade():
    connection = op.get_bind()
    with Session(bind=connection) as session:
        restore_archived_applications(session)
        session.flush()
    JobVacancyIdentity.__table__.drop(connection, checkfirst=True)
    JobVacancyGroup.__table__.drop(connection, checkfirst=True)
    JobApplicationDeduplicationArchive.__table__.drop(connection)
    AppDataMigration.__table__.drop(connection)
