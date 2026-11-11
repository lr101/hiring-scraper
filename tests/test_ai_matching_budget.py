"""A durable budget counts provider attempts, not only successful matches."""
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from hiring_scraper.app import models


class AIMatchingBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + str(Path(self.directory.name) / 'budget.db'))
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine)
        self.now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def budget(self):
        return importlib.import_module('hiring_scraper.app.ai_matching_budget')

    def test_rolling_limit_survives_restart_and_midnight(self):
        budget = self.budget()
        now = self.now.replace(hour=23)
        self.assertTrue(budget.reserve(self.factory, limit=2, now=now))
        self.assertTrue(budget.reserve(self.factory, limit=2, now=now + timedelta(hours=1)))
        self.engine.dispose()
        self.assertFalse(budget.reserve(self.factory, limit=2, now=now + timedelta(hours=2)))
        self.assertTrue(budget.reserve(self.factory, limit=2, now=now + timedelta(hours=5)))
        self.assertFalse(budget.reserve(self.factory, limit=2, now=now + timedelta(hours=5, minutes=1)))
        self.assertTrue(budget.reserve(self.factory, limit=2, now=now + timedelta(hours=6)))

    def test_default_allows_fifty_then_blocks(self):
        budget = self.budget()
        with patch.dict(os.environ, {}, clear=True):
            for _ in range(50):
                self.assertTrue(budget.reserve(self.factory, now=self.now))
            self.assertFalse(budget.reserve(self.factory, now=self.now))

    def test_status_counts_legacy_reservations_until_they_expire(self):
        with self.factory() as session:
            session.add(models.AIMatchingCall(day='2026-11-10', slot=1, reserved_at=self.now))
            session.commit()
        with patch.dict(os.environ, {'RADIUS_MATCH_AI_CALLS_PER_5H': '1'}):
            self.assertEqual(self.budget().status(self.factory, now=self.now)['remaining'], 0)
            self.assertFalse(self.budget().reserve(self.factory, now=self.now))
            self.assertEqual(self.budget().status(self.factory, now=self.now + timedelta(hours=5))['remaining'], 1)

    def test_expired_reservations_are_pruned_without_refunding_active_attempts(self):
        from sqlalchemy import func, select
        budget = self.budget()
        self.assertTrue(budget.reserve(self.factory, limit=1, now=self.now))
        later = self.now + timedelta(hours=6)
        self.assertTrue(budget.reserve(self.factory, limit=1, now=later))
        self.assertFalse(budget.reserve(self.factory, limit=1, now=later))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(models.AIMatchingCall)), 1)

    def test_zero_budget_disables_all_provider_attempts(self):
        self.assertFalse(self.budget().reserve(self.factory, limit=0, now=self.now))

    def test_concurrent_replicas_cannot_exceed_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        budget = self.budget()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: budget.reserve(self.factory, limit=3, now=self.now), range(12)))
        self.assertEqual(sum(results), 3)

    def test_migration_creates_durable_slot_constraint_and_downgrades(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect, text
        from sqlalchemy.exc import IntegrityError
        migration = importlib.import_module('hiring_scraper.app.migrations.versions.0036_ai_matching_budget')
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                connection.execute(text("INSERT INTO ai_matching_calls VALUES ('2026-11-10', 1, CURRENT_TIMESTAMP)"))
                with self.assertRaises(IntegrityError):
                    connection.execute(text("INSERT INTO ai_matching_calls VALUES ('2026-11-10', 1, CURRENT_TIMESTAMP)"))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                self.assertNotIn('ai_matching_calls', inspect(connection).get_table_names())
        finally:
            engine.dispose()

    def test_invalid_configuration_fails_closed(self):
        for value in ('-1', 'unlimited', ''):
            with self.subTest(value=value), patch.dict(os.environ, {'RADIUS_MATCH_AI_CALLS_PER_5H': value}):
                self.assertEqual(self.budget().call_limit(), 0)
