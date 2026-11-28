"""Plain E5 pre-rank similarity on job_analysis_queue, next to the title-boosted score.

Some LLM slots are filled by plain similarity so postings that fit without naming a target
role in their title are still read. A nullable column without a default is a metadata-only
change; rows get their value from the next pre-rank pass.
"""
from time import sleep

from alembic import op
import sqlalchemy as sa


revision = '0048_analysis_raw_similarity'
down_revision = '0047_analysis_prerank'
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns('job_analysis_queue')}


def _short_ddl(apply) -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        apply()
        return
    attempts = 5
    for attempt in range(attempts):
        try:
            with bind.begin_nested():
                op.execute("SET LOCAL lock_timeout = '2s'")
                apply()
            return
        except sa.exc.DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) != '55P03' or attempt == attempts - 1:
                raise
            sleep(0.25 * 2 ** attempt)


def upgrade() -> None:
    def apply() -> None:
        if 'raw_similarity' not in _columns():
            op.add_column('job_analysis_queue', sa.Column('raw_similarity', sa.Float(), nullable=True))
    _short_ddl(apply)


def downgrade() -> None:
    def apply() -> None:
        if 'raw_similarity' in _columns():
            op.drop_column('job_analysis_queue', 'raw_similarity')
    _short_ddl(apply)
