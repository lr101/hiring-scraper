"""Smoke checks follow new migrations while still rejecting a stale database."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from alembic.config import Config
from sqlalchemy import create_engine

from scripts.ci.migration_heads import assert_migration_heads


class MigrationHeadTests(unittest.TestCase):
    def test_new_migration_requires_upgrade_without_changing_the_smoke_assertion(self):
        with TemporaryDirectory() as directory:
            versions = Path(directory) / 'versions'
            versions.mkdir()
            (versions / 'first.py').write_text("revision = 'first'\ndown_revision = None\n")
            config = Config()
            config.set_main_option('script_location', directory)
            engine = create_engine('sqlite://')
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)')
                    connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('first')")
                    assert_migration_heads(connection, config)
                    (versions / 'second.py').write_text("revision = 'second'\ndown_revision = 'first'\n")
                    with self.assertRaisesRegex(AssertionError, 'second'):
                        assert_migration_heads(connection, config)
                    connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'second'")
                    assert_migration_heads(connection, config)
            finally:
                engine.dispose()

    def test_database_without_migrations_is_rejected(self):
        with TemporaryDirectory() as directory:
            versions = Path(directory) / 'versions'
            versions.mkdir()
            (versions / 'first.py').write_text("revision = 'first'\ndown_revision = None\n")
            config = Config()
            config.set_main_option('script_location', directory)
            engine = create_engine('sqlite://')
            try:
                with engine.connect() as connection, self.assertRaisesRegex(AssertionError, 'first'):
                    assert_migration_heads(connection, config)
            finally:
                engine.dispose()
