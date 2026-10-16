"""Real PostgreSQL regressions; use a disposable TEST_POSTGRES_URL database."""
import os
import threading
import unittest
from datetime import timedelta
from itertools import product
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.exc import OperationalError, TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import sessionmaker
from psycopg.errors import LockNotAvailable

from hiring_scraper.app import api, database, default_job_feeds, discovery_worker, job_search
from hiring_scraper.app import worker
from hiring_scraper.app.models import (
    Base, Company, Job, JobFeed, JobSearchRefresh, ScanRun, SemanticSearchIndexState, UserProfile, utcnow,
)


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'requires disposable TEST_POSTGRES_URL')
class PostgresConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'review_' + uuid4().hex
        self.admin = create_engine(os.environ['TEST_POSTGRES_URL'])
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {self.schema}'))
        self.engine = create_engine(os.environ['TEST_POSTGRES_URL'], pool_size=8, max_overflow=0,
            connect_args={'options': f'-c search_path={self.schema},public -c lock_timeout=1000 -c statement_timeout=5000'})
        # These tests exercise scheduling, not vector search; pgvector is optional here.
        Base.metadata.create_all(self.engine, tables=[table for table in Base.metadata.sorted_tables
                                                     if table.name != 'semantic_search_documents'])
        self.factory = sessionmaker(self.engine, autoflush=False, expire_on_commit=False)
        self.patches = [patch.object(module, 'SessionLocal', self.factory)
                        for module in (worker, api, default_job_feeds, discovery_worker)]
        self.patches += [patch.object(worker, 'engine', self.engine),
                         patch.object(discovery_worker, 'engine', self.engine),
                         patch.object(default_job_feeds, 'IS_SQLITE', False)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        with self.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {self.schema} CASCADE'))
        self.admin.dispose()

    def seed_feed(self):
        with self.factory.begin() as session:
            company = Company(source='test', source_id='employer', name='Employer')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/jobs.xml', board_url='https://example.test/jobs.xml',
                           status='pending', next_scan_at=utcnow() - timedelta(minutes=1))
            session.add(feed)
            session.flush()
            run = ScanRun(feed_id=feed.id, status='running')
            session.add(run)
            session.flush()
            return company.id, feed.id, run.id

    def test_disjoint_source_flushes_do_not_share_a_lock_until_commit(self):
        company_id, _, _ = self.seed_feed()
        with self.factory.begin() as session:
            other = Company(source='test', source_id='other', name='Other')
            session.add(other)
            session.flush()
            other_id = other.id
        with self.factory() as first, self.factory() as second:
            first.get(Company, company_id).name = 'First write'
            first.flush()
            second.get(Company, other_id).name = 'Second write'
            second.flush()
            second.commit()
            with self.factory() as observer:
                before_first_commit = api._search_generation(observer)
            first.commit()
            with self.factory() as observer:
                self.assertGreater(api._search_generation(observer), before_first_commit)

    def test_feed_finalization_waits_for_company_without_holding_feed(self):
        self.assert_company_first(lambda feed_id, run_id: worker._persist_result(
            feed_id, run_id, {'state': 'not_modified', 'status': 304}, b'', 1))

    def test_feed_edit_waits_for_company_without_holding_feed(self):
        def edit(feed_id, _run_id):
            with self.factory() as session:
                api.update_managed_feed(feed_id, api.ManagedFeedUpdate(
                    career_page_url='https://example.test/careers'), session)
        self.assert_company_first(edit)

    def assert_company_first(self, action):
        company_id, feed_id, run_id = self.seed_feed()
        reached_lock = threading.Event()
        errors = []

        def before(_conn, _cursor, statement, *_args):
            if threading.current_thread().name == 'feed-finalizer' and 'FOR UPDATE' in statement and 'companies' in statement:
                reached_lock.set()

        def after(_conn, _cursor, statement, *_args):
            if threading.current_thread().name == 'feed-finalizer' and statement.startswith('UPDATE job_feeds'):
                reached_lock.set()

        event.listen(self.engine, 'before_cursor_execute', before)
        event.listen(self.engine, 'after_cursor_execute', after)
        def finish():
            try:
                action(feed_id, run_id)
            except Exception as error:
                errors.append(error)

        with self.factory() as discovery:
            discovery.scalar(select(Company).where(Company.id == company_id).with_for_update())
            thread = threading.Thread(target=finish, name='feed-finalizer')
            thread.start()
            try:
                self.assertTrue(reached_lock.wait(3), 'feed finalizer did not reach its first lock')
                # Discovery owns Company and must be able to touch its feed. The
                # old Feed -> Company order creates a two-transaction cycle here.
                discovery.execute(update(JobFeed).where(JobFeed.id == feed_id).values(job_count=2))
                discovery.commit()
            finally:
                discovery.rollback()
                thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_feed_geocoding_does_not_hold_company_or_feed_locks(self):
        company_id, feed_id, _ = self.seed_feed()
        def geocode(_query):
            with self.factory.begin() as other_writer:
                other_writer.execute(update(Company).where(Company.id == company_id).values(name='Updated'))
                other_writer.execute(update(JobFeed).where(JobFeed.id == feed_id).values(job_count=2))
            return [{'city': 'Berlin', 'label': 'Berlin', 'latitude': 52.52, 'longitude': 13.4}]
        with self.factory() as session, patch.object(api, '_nominatim_results', side_effect=geocode):
            result = api.update_managed_feed(feed_id,
                api.ManagedFeedUpdate(feed_url='https://example.test/new-jobs.xml',
                                      location_query='Berlin', radius_km=35), session)
        self.assertEqual(result['source_config']['location_filter']['city'], 'Berlin')

    def test_feed_persistence_contention_does_not_mark_the_source_failed(self):
        _, feed_id, _ = self.seed_feed()
        failure = OperationalError('persist feed', {}, LockNotAvailable('lock timeout'))
        with patch.object(worker, '_request', return_value=({'state': 'ok'}, b'')), \
                patch.object(worker, '_persist_result', side_effect=failure):
            with self.assertRaises(OperationalError):
                worker.scan_once()
        with self.factory() as session:
            feed = session.get(JobFeed, feed_id)
            self.assertEqual(feed.status, 'pending')
            self.assertIsNone(feed.last_error)

    def test_discovery_persistence_contention_does_not_mark_the_source_failed(self):
        company_id, _, _ = self.seed_feed()
        with self.factory.begin() as session:
            company = session.get(Company, company_id)
            company.website_url = 'https://example.test'
            company.domain = 'example.test'
        claim = discovery_worker._claim_due_company()
        failure = OperationalError('persist discovery', {}, LockNotAvailable('lock timeout'))
        with patch.object(discovery_worker, 'discover', return_value={'status': 'career_content_found'}), \
                patch.object(discovery_worker, '_persist_discovery', side_effect=failure):
            with self.assertRaises(OperationalError):
                discovery_worker._process_claimed_company((*claim, None, None))
        with self.factory() as session:
            self.assertIsNone(session.get(Company, company_id).discovery_error)

    def test_expired_feed_attempt_cannot_clear_a_newer_same_revision_lease(self):
        _, feed_id, old_run_id = self.seed_feed()
        claimed = worker._claim_due_feed()
        self.assertEqual(claimed[0], feed_id)
        with self.factory() as session:
            newer_lease = session.get(JobFeed, feed_id).lease_until
        worker._persist_result(feed_id, old_run_id, {'state': 'ok', 'status': 200,
            'parsed_feed': {'complete': True, 'jobs': [
                {'id': 'stale', 'title': 'Stale job', 'url': 'https://example.test/stale'}]}}, b'', 1)
        with self.factory() as session:
            self.assertEqual(session.get(JobFeed, feed_id).lease_until, newer_lease)
            self.assertEqual(session.get(ScanRun, claimed[1]).status, 'running')
            self.assertEqual(session.scalars(select(Job)).all(), [])

    def test_superseded_adapter_cannot_renew_the_new_owners_lease(self):
        _, feed_id, old_run_id = self.seed_feed()
        with self.factory() as session:
            old_feed = session.get(JobFeed, feed_id)
        worker._claim_due_feed()
        with self.factory() as session:
            newer_lease = session.get(JobFeed, feed_id).lease_until
        with self.assertRaises(RuntimeError):
            worker._heartbeat_adapter_feed(old_feed, old_run_id)
        with self.factory() as session:
            self.assertEqual(session.get(JobFeed, feed_id).lease_until, newer_lease)

    def test_old_snapshot_finisher_preserves_reclaim_and_followup_request(self):
        for retry, reclaim in product((False, True), repeat=2):
            with self.subTest(retry=retry, reclaim=reclaim):
                key = uuid4().hex
                requested = utcnow() - timedelta(minutes=2)
                with self.factory.begin() as session:
                    session.add(JobSearchRefresh(key=key, criteria={}, requested_at=requested,
                        lease_token='old', lease_until=utcnow() - timedelta(seconds=1)))
                ready, resume = threading.Event(), threading.Event()
                errors = []
                def pause(_conn, _cursor, statement, *_args):
                    if threading.current_thread().name == 'snapshot-finisher' and (
                        statement.startswith('DELETE FROM job_search_snapshot_refreshes') or
                        statement.startswith('UPDATE job_search_snapshot_refreshes')):
                        ready.set()
                        if not resume.wait(4):
                            raise AssertionError('snapshot test did not release finisher')
                event.listen(self.engine, 'before_cursor_execute', pause)
                def finish():
                    try:
                        api._finish_search_snapshot_refresh(key, 'old', requested, retry=retry)
                    except Exception as error:
                        errors.append(error)
                thread = threading.Thread(target=finish, name='snapshot-finisher')
                thread.start()
                try:
                    self.assertTrue(ready.wait(3))
                    with self.factory.begin() as session:
                        values = {'requested_at': utcnow()}
                        if reclaim:
                            values.update(lease_token='new', lease_until=utcnow() + timedelta(minutes=1))
                        session.execute(update(JobSearchRefresh).where(JobSearchRefresh.key == key).values(**values))
                finally:
                    resume.set()
                    thread.join(6)
                    event.remove(self.engine, 'before_cursor_execute', pause)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                with self.factory() as session:
                    refresh = session.get(JobSearchRefresh, key)
                    self.assertIsNotNone(refresh, 'new work was erased by the old finisher')
                    if reclaim:
                        self.assertEqual(refresh.lease_token, 'new')

    def test_default_feeds_initialize_concurrently_without_unique_failures(self):
        config = [{'id': 'public', 'name': 'Public board', 'provider': 'rss',
            'feed_url': 'https://example.test/jobs.xml', 'source_page_url': 'https://example.test/jobs',
            'website_url': 'https://example.test'}]
        rendezvous = threading.Barrier(2)
        errors, results = [], []
        seen = set()
        def synchronize(_conn, _cursor, statement, *_args):
            name = threading.current_thread().name
            if name.startswith('default-feed-') and statement.startswith('SELECT companies.') and name not in seen:
                seen.add(name)
                try:
                    rendezvous.wait(.2)
                except threading.BrokenBarrierError:
                    pass
        event.listen(self.engine, 'after_cursor_execute', synchronize)
        def initialize():
            try:
                results.append(default_job_feeds.ensure_default_job_feeds())
            except Exception as error:
                errors.append(error)
        with patch.object(default_job_feeds, '_configured_feeds', return_value=config):
            threads = [threading.Thread(target=initialize, name=f'default-feed-{i}') for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(6)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [0, 1])
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(JobFeed)).all()), 1)

    def test_fresh_profile_index_check_does_not_hold_a_profile_row_lock(self):
        with self.factory.begin() as session:
            profile = UserProfile(name='Developer', preferences={})
            session.add(profile)
            session.flush()
            profile.match_index_version = job_search._profile_index_version(profile)
            profile_id = profile.id
        with self.factory() as reader:
            self.assertFalse(job_search.ensure_profile_match_indexes(reader, [reader.get(UserProfile, profile_id)]))
            with self.factory.begin() as editor:
                editor.execute(update(UserProfile).where(UserProfile.id == profile_id).values(name='Edited'))

    def test_embedding_wait_releases_the_request_connection(self):
        with self.factory.begin() as session:
            for kind in ('company', 'job'):
                session.add(SemanticSearchIndexState(entity_type=kind,
                    model_revision=api._SEMANTIC_MODEL_KEY, backfill_complete=True))
        single = create_engine(os.environ['TEST_POSTGRES_URL'], pool_size=1, max_overflow=0,
            pool_timeout=.1, connect_args={'options': f'-c search_path={self.schema},public'})
        class EmbeddingReached(Exception):
            pass
        def embedding(_query):
            with single.connect() as health_check:
                self.assertEqual(health_check.scalar(text('SELECT 1')), 1)
            raise EmbeddingReached()
        try:
            for kind in ('company', 'job'):
                with self.subTest(kind=kind), self.factory.class_(single) as session, \
                        patch.object(api, 'IS_SQLITE', False), \
                        patch.object(api, '_query_embedding', side_effect=embedding):
                    with self.assertRaises(EmbeddingReached):
                        if kind == 'company':
                            api.list_companies(49, 8, 35, 'engineer', 0, 10, session)
                        else:
                            api.list_jobs(49, 8, 35, None, 'engineer', None, 0, 10, session=session)
        finally:
            single.dispose()

    def test_empty_pool_overrides_keep_the_process_connection_budget(self):
        with patch.dict(os.environ, {'DB_POOL_SIZE': '', 'DB_MAX_OVERFLOW': '',
                                    'HIRING_DB_POOL_SIZE': '2', 'HIRING_DB_MAX_OVERFLOW': '0',
                                    'DB_POOL_TIMEOUT_SECONDS': '.1'}):
            bounded = database.create_runtime_engine(os.environ['TEST_POSTGRES_URL'])
        try:
            with bounded.connect(), bounded.connect():
                with self.assertRaises(DatabasePoolTimeout):
                    with bounded.connect():
                        self.fail('The process exceeded its two-connection budget')
            with bounded.connect() as recovered:
                self.assertEqual(recovered.scalar(text('SELECT 1')), 1)
        finally:
            bounded.dispose()

    def test_runtime_postgres_engine_bounds_lock_and_connection_waits(self):
        factory = getattr(database, 'create_runtime_engine', None)
        self.assertTrue(callable(factory), 'runtime engine needs explicit PostgreSQL budgets')
        with patch.dict(os.environ, {'DB_POOL_SIZE': '1', 'DB_MAX_OVERFLOW': '0',
                                    'DB_LOCK_TIMEOUT_MS': '100', 'DB_STATEMENT_TIMEOUT_MS': '1000',
                                    'DB_IDLE_TRANSACTION_TIMEOUT_MS': '1000'}):
            bounded = factory(os.environ['TEST_POSTGRES_URL'])
        try:
            with bounded.connect() as connection:
                self.assertEqual(connection.scalar(text('SHOW lock_timeout')), '100ms')
                self.assertEqual(connection.scalar(text('SHOW statement_timeout')), '1s')
                self.assertEqual(connection.scalar(text('SHOW idle_in_transaction_session_timeout')), '1s')
            self.assertEqual(bounded.pool.size(), 1)
            with self.engine.begin() as blocker:
                blocker.execute(text('SELECT pg_advisory_xact_lock(85793011, 9)'))
                with self.assertRaises(OperationalError) as failure:
                    with bounded.begin() as waiting:
                        waiting.execute(text('SELECT pg_advisory_xact_lock(85793011, 9)'))
                self.assertTrue(database.is_transient_database_error(failure.exception))
            # The failed transaction returned its one connection to the pool.
            with bounded.connect() as recovered:
                self.assertEqual(recovered.scalar(text('SELECT 1')), 1)
        finally:
            bounded.dispose()
