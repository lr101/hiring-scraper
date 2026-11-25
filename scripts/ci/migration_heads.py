"""Validate the deployed revision against the migration graph in this checkout."""
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory


def assert_migration_heads(connection, config) -> None:
    expected = set(ScriptDirectory.from_config(config).get_heads())
    actual = set(MigrationContext.configure(connection).get_current_heads())
    assert actual == expected, f'Database revisions {actual!r} do not match migration heads {expected!r}'
