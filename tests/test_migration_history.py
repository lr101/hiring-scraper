"""Populated databases upgrade through historical data migrations to head."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests import pg


class HistoricalUpgradeTests(unittest.TestCase):
    def setUp(self):
        base = make_url(pg.url())
        # Same disposable prefix as the test database; dropped after the test.
        self.name = f'{base.database}_history_{uuid4().hex[:8]}'
        self.admin = create_engine(base.set(database='postgres'), isolation_level='AUTOCOMMIT')
        with self.admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{self.name}" TEMPLATE template0'))
        self.addCleanup(self.drop)
        self.target = base.set(database=self.name).render_as_string(hide_password=False)
        self.config = Config()
        self.config.set_main_option('script_location', 'hiring_scraper/app/migrations')
        self.config.set_main_option('sqlalchemy.url', self.target)

    def drop(self):
        with self.admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        self.admin.dispose()

    def test_populated_profile_upgrades_through_deduplication_to_head(self):
        # Migrations import current application helpers; they must not read
        # columns that later revisions add (0033 once selected source_version).
        with patch.dict(os.environ, {'DATABASE_URL': self.target}):
            command.upgrade(self.config, '0032_profile_photo')
            engine = create_engine(self.target)
            try:
                with engine.begin() as connection:
                    connection.execute(text(
                        "INSERT INTO user_profiles (name, preferences, created_at, updated_at) "
                        "VALUES ('Historical', '{}', now(), now())"))
                command.upgrade(self.config, 'head')
                with engine.connect() as connection:
                    self.assertEqual(connection.execute(text(
                        'SELECT name, source_version FROM user_profiles')).one(), ('Historical', 0))
            finally:
                engine.dispose()


if __name__ == '__main__':
    unittest.main()
