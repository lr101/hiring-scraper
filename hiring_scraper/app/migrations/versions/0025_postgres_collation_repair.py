"""Rebuild indexes and refresh the database collation version."""
from alembic import op
import sqlalchemy as sa


revision = "0025_postgres_collation_repair"
down_revision = "0024_semantic_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    database_name = bind.scalar(sa.text("SELECT current_database()"))
    database_identifier = bind.dialect.identifier_preparer.quote(database_name)
    # REINDEX CONCURRENTLY must run outside a transaction. Rebuild the full
    # database because a libc provider change can affect any text index.
    with op.get_context().autocommit_block():
        op.execute(f"REINDEX DATABASE CONCURRENTLY {database_identifier}")
        op.execute(f"ALTER DATABASE {database_identifier} REFRESH COLLATION VERSION")


def downgrade() -> None:
    # The repaired indexes and refreshed version are safe to keep on downgrade.
    pass
