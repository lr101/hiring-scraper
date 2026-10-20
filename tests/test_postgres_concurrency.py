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

from hiring_scraper.app import api, database, default_job_feeds, discovery_worker, enrichment, job_search
from hiring_scraper.app import worker
from hiring_scraper.app.models import (
    Base, Company, DiscoveryJob, DiscoveryJobCompany, DiscoveryRun, Job, JobApplication, JobFeed,
    JobSearchRefresh, ProfileJobMatch, ScanRun, SemanticSearchIndexState, UserProfile, utcnow,
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
                        for module in (worker, api, default_job_feeds, discovery_worker, enrichment)]
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

    def test_cancelled_campaign_finisher_waits_for_company_without_holding_parent(self):
        with self.factory.begin() as session:
            company = Company(source='test', source_id='cancelled', name='Employer',
                              discovery_lease_until=utcnow() + timedelta(minutes=5))
            campaign = DiscoveryJob(label='Cancelled', latitude=49, longitude=8,
                                    radius_km=15, status='cancelled')
            session.add_all([company, campaign])
            session.flush()
            run = DiscoveryRun(company_id=company.id, status='running')
            session.add(run)
            session.flush()
            item = DiscoveryJobCompany(discovery_job_id=campaign.id, company_id=company.id,
                                       discovery_run_id=run.id, status='running')
            session.add(item)
            session.flush()
            company_id, campaign_id, item_id, run_id = company.id, campaign.id, item.id, run.id
        reached_company = threading.Event()
        errors = []

        def before(_conn, _cursor, statement, *_args):
            if threading.current_thread().name == 'campaign-finisher' and (
                    ('FROM companies ' in statement and 'FOR UPDATE' in statement)
                    or statement.startswith('UPDATE companies')):
                reached_company.set()

        def finish():
            try:
                discovery_worker._finish_campaign_company(item_id, run_id)
            except Exception as error:
                errors.append(error)

        event.listen(self.engine, 'before_cursor_execute', before)
        try:
            with self.factory() as persistence:
                persistence.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
                thread = threading.Thread(target=finish, name='campaign-finisher')
                thread.start()
                try:
                    self.assertTrue(reached_company.wait(3))
                    # Crawl persistence already owns Company before checking its campaign.
                    persistence.scalar(select(DiscoveryJob.id).where(
                        DiscoveryJob.id == campaign_id).with_for_update())
                    persistence.commit()
                finally:
                    persistence.rollback()
                    thread.join(6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            with self.factory() as session:
                self.assertEqual(session.get(DiscoveryJobCompany, item_id).status, 'cancelled')
                self.assertEqual(session.get(DiscoveryRun, run_id).status, 'cancelled')
        finally:
            event.remove(self.engine, 'before_cursor_execute', before)

    def test_feed_edit_waits_for_company_without_holding_feed(self):
        def edit(feed_id, _run_id):
            with self.factory() as session:
                api.update_managed_feed(feed_id, api.ManagedFeedUpdate(
                    career_page_url='https://example.test/careers'), session)
        self.assert_company_first(edit)

    def test_cancelled_campaign_finisher_preserves_a_newer_discovery_lease(self):
        lease = utcnow() + timedelta(minutes=5)
        with self.factory.begin() as session:
            company = Company(source='test', source_id='reclaimed', name='Employer',
                              discovery_lease_until=lease)
            campaign = DiscoveryJob(label='Cancelled', latitude=49, longitude=8,
                                    radius_km=15, status='cancelled')
            session.add_all([company, campaign])
            session.flush()
            old = DiscoveryRun(company_id=company.id, status='failed', error='Lease expired')
            session.add(old)
            session.flush()
            newer = DiscoveryRun(company_id=company.id, status='running')
            item = DiscoveryJobCompany(discovery_job_id=campaign.id, company_id=company.id,
                                       discovery_run_id=old.id, status='running')
            session.add_all([newer, item])
            session.flush()
            company_id, old_id, newer_id, item_id = company.id, old.id, newer.id, item.id
        discovery_worker._finish_campaign_company(item_id, old_id)
        with self.factory() as session:
            self.assertEqual(session.get(Company, company_id).discovery_lease_until, lease)
            self.assertEqual(session.get(DiscoveryRun, newer_id).status, 'running')
            self.assertEqual(session.get(DiscoveryRun, old_id).status, 'failed')
            self.assertEqual(session.get(DiscoveryJobCompany, item_id).status, 'cancelled')

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

    def test_retiring_feed_during_index_publish_retracts_rows_after_rebuild(self):
        with self.factory.begin() as session:
            company = Company(source='managed_feed', source_id='index-race', name='Employer')
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider='external_html',
                           feed_url='https://example.test/jobs', board_url='https://example.test/jobs',
                           status='parsed', enabled=True, source_revision=1)
            profile = UserProfile(name='Developer', preferences={})
            session.add_all([feed, profile])
            session.flush()
            job = Job(feed_id=feed.id, external_id='role', title='Engineer',
                      url='https://example.test/jobs/role', is_active=True)
            session.add(job)
            session.flush()
            feed_id, profile_id, job_id = feed.id, profile.id, job.id

        index_at_publish = threading.Event()
        release_index = threading.Event()
        retirement_waiting_for_profile = threading.Event()
        retirement_done = threading.Event()
        generation_reads = 0
        errors = []

        def pause_index_before_publish(_conn, _cursor, statement, *_args):
            nonlocal generation_reads
            if (threading.current_thread().name == 'profile-index-rebuild' and
                    statement.startswith('SELECT max(job_search_state.id)')):
                generation_reads += 1
                if generation_reads == 2:
                    index_at_publish.set()
                    if not release_index.wait(4):
                        raise AssertionError('index race test did not release rebuild')

        def observe_retirement_lock(_conn, _cursor, statement, *_args):
            if (threading.current_thread().name == 'feed-retirement' and
                    'FROM user_profiles' in statement and 'FOR UPDATE' in statement):
                retirement_waiting_for_profile.set()

        event.listen(self.engine, 'after_cursor_execute', pause_index_before_publish)
        event.listen(self.engine, 'before_cursor_execute', observe_retirement_lock)

        def rebuild():
            try:
                with patch.object(job_search, 'match_job', return_value={
                        'eligible': True, 'score': 70, 'fit_tier': 'recommended', 'uncertain': False}), \
                        patch.object(job_search, 'application_match_allowed', return_value=True), \
                        self.factory.begin() as session:
                    profile = session.get(UserProfile, profile_id)
                    job_search.ensure_profile_match_indexes(session, [profile])
            except Exception as error:
                errors.append(error)

        def retire_feed():
            try:
                with self.factory() as session:
                    session.execute(text("SET lock_timeout = '8s'"))
                    api.update_managed_feed(feed_id, api.ManagedFeedUpdate(
                        feed_url='https://example.test/new-jobs.xml', provider='rss'), session)
            except Exception as error:
                errors.append(error)
            finally:
                retirement_done.set()

        index_thread = threading.Thread(target=rebuild, name='profile-index-rebuild')
        retirement_thread = threading.Thread(target=retire_feed, name='feed-retirement')
        try:
            index_thread.start()
            self.assertTrue(index_at_publish.wait(3), 'index rebuild did not reach its publish check')
            retirement_thread.start()
            progressed = (retirement_waiting_for_profile.wait(3) or retirement_done.wait(0.1))
            self.assertTrue(progressed, 'feed retirement neither blocked on nor finished its profile update')
        finally:
            release_index.set()
            index_thread.join(6)
            if retirement_thread.ident is not None:
                retirement_thread.join(6)
            event.remove(self.engine, 'after_cursor_execute', pause_index_before_publish)
            event.remove(self.engine, 'before_cursor_execute', observe_retirement_lock)

        self.assertFalse(index_thread.is_alive())
        self.assertFalse(retirement_thread.is_alive())
        self.assertEqual(errors, [])
        with self.factory() as session:
            self.assertFalse(session.get(Job, job_id).is_active)
            self.assertIsNone(session.get(ProfileJobMatch, (profile_id, job_id)))
            self.assertIsNone(session.scalar(select(JobApplication).where(
                JobApplication.profile_id == profile_id, JobApplication.job_id == job_id,
                JobApplication.status == 'new')))

    def test_inventory_writer_cannot_commit_between_index_check_and_publish(self):
        with self.factory.begin() as session:
            profile = UserProfile(name='Developer', preferences={})
            company = Company(source='test', source_id='generation-guard', name='Employer')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/jobs.xml',
                           board_url='https://example.test/jobs.xml', status='parsed', enabled=True)
            job = Job(feed=feed, external_id='role', title='Engineer',
                      url='https://example.test/jobs/role', is_active=True)
            session.add_all([profile, feed, job])
            session.flush()
            profile_id, job_id = profile.id, job.id

        index_at_final_check = threading.Event()
        release_index = threading.Event()
        writer_waiting_for_guard = threading.Event()
        writer_done = threading.Event()
        generation_reads = 0
        errors = []

        def pause_index(_conn, _cursor, statement, *_args):
            nonlocal generation_reads
            if (threading.current_thread().name == 'guarded-index' and
                    statement.startswith('SELECT max(job_search_state.id)')):
                generation_reads += 1
                if generation_reads == 2:
                    index_at_final_check.set()
                    if not release_index.wait(5):
                        raise AssertionError('index publication was not released')

        def observe_inventory_writer(_conn, _cursor, statement, *_args):
            if (threading.current_thread().name == 'inventory-writer' and
                    'pg_advisory_xact_lock(85793011, 2)' in statement):
                writer_waiting_for_guard.set()

        event.listen(self.engine, 'after_cursor_execute', pause_index)
        event.listen(self.engine, 'before_cursor_execute', observe_inventory_writer)

        def rebuild():
            try:
                with patch.object(job_search, 'match_job', return_value={
                        'eligible': True, 'score': 70, 'fit_tier': 'recommended', 'uncertain': False}), \
                        patch.object(job_search, 'application_match_allowed', return_value=False), \
                        self.factory.begin() as session:
                    job_search.ensure_profile_match_indexes(
                        session, [session.get(UserProfile, profile_id)])
            except Exception as error:
                errors.append(error)

        def update_inventory():
            try:
                with self.factory.begin() as session:
                    job = session.get(Job, job_id)
                    job.description = 'Updated inventory while the index was publishing'
            except Exception as error:
                errors.append(error)
            finally:
                writer_done.set()

        index_thread = threading.Thread(target=rebuild, name='guarded-index')
        writer_thread = threading.Thread(target=update_inventory, name='inventory-writer')
        try:
            index_thread.start()
            self.assertTrue(index_at_final_check.wait(3), 'index rebuild did not reach its final check')
            writer_thread.start()
            self.assertTrue(writer_waiting_for_guard.wait(3),
                            'inventory writer did not request the shared generation guard')
            self.assertFalse(writer_done.wait(0.2),
                             'inventory generation committed before index publication completed')
        finally:
            release_index.set()
            index_thread.join(6)
            if writer_thread.ident is not None:
                writer_thread.join(6)
            event.remove(self.engine, 'after_cursor_execute', pause_index)
            event.remove(self.engine, 'before_cursor_execute', observe_inventory_writer)

        self.assertFalse(index_thread.is_alive())
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(errors, [])
        with self.factory() as session:
            self.assertEqual(session.get(Job, job_id).description,
                             'Updated inventory while the index was publishing')

    def test_detail_enrichment_and_feed_retirement_use_compatible_job_profile_locks(self):
        with self.factory.begin() as session:
            company = Company(source='managed_feed', source_id='enrichment-lock-order', name='Employer')
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider='rss',
                           feed_url='https://example.test/jobs.xml',
                           board_url='https://example.test/jobs.xml', status='parsed', enabled=True,
                           source_revision=1)
            profile = UserProfile(name='Developer', preferences={})
            job = Job(feed=feed, external_id='role', title='Developer',
                      url='https://example.test/jobs/role', is_active=True)
            session.add_all([feed, profile, job])
            session.flush()
            enrichment.refresh_enrichment(job)
            feed_id, job_id = feed.id, job.id

        enrichment_at_profile_lock = threading.Event()
        release_enrichment = threading.Event()
        retirement_at_job_lock = threading.Event()
        retirement_done = threading.Event()
        errors = []
        lock_order = []

        def coordinate(_conn, _cursor, statement, *_args):
            normalized = ' '.join(statement.upper().split())
            if 'FOR UPDATE' not in normalized and 'FOR NO KEY UPDATE' not in normalized:
                return
            if threading.current_thread().name == 'detail-enrichment':
                if 'FROM USER_PROFILES' in normalized:
                    lock_order.append('profiles')
                    enrichment_at_profile_lock.set()
                    if not release_enrichment.wait(6):
                        raise AssertionError('detail enrichment did not resume')
                elif 'FROM JOBS' in normalized:
                    lock_order.append('jobs')
            elif threading.current_thread().name == 'feed-retirement' and 'FROM JOBS' in normalized:
                retirement_at_job_lock.set()

        event.listen(self.engine, 'before_cursor_execute', coordinate)

        class OfflineClient:
            def get(self, _url):
                return {'state': 'network_error'}, b''

        def enrich_detail():
            try:
                enrichment.backfill(1, fetch_details=True, client=OfflineClient())
            except Exception as error:
                errors.append(error)

        def retire_feed():
            try:
                with self.factory() as session:
                    session.execute(text("SET lock_timeout = '8s'"))
                    api.update_managed_feed(feed_id, api.ManagedFeedUpdate(
                        feed_url='https://example.test/replacement.xml'), session)
            except Exception as error:
                errors.append(error)
            finally:
                retirement_done.set()

        enrichment_thread = threading.Thread(target=enrich_detail, name='detail-enrichment')
        retirement_thread = threading.Thread(target=retire_feed, name='feed-retirement')
        try:
            enrichment_thread.start()
            self.assertTrue(enrichment_at_profile_lock.wait(3),
                            'detail enrichment did not begin its match profile lock')
            retirement_thread.start()
            # Retirement must wait for the enrichment job lock before it can
            # refresh profiles. Reversing that order reintroduces the deadlock.
            if not retirement_done.wait(2):
                self.assertTrue(retirement_at_job_lock.wait(2),
                                'feed retirement neither completed nor reached its job lock')
            release_enrichment.set()
            enrichment_thread.join(8)
            retirement_thread.join(8)
        finally:
            release_enrichment.set()
            enrichment_thread.join(8)
            if retirement_thread.ident is not None:
                retirement_thread.join(8)
            event.remove(self.engine, 'before_cursor_execute', coordinate)

        self.assertFalse(enrichment_thread.is_alive())
        self.assertFalse(retirement_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertLess(lock_order.index('jobs'), lock_order.index('profiles'))
        with self.factory() as session:
            self.assertFalse(session.get(Job, job_id).is_active)

    def test_feed_persistence_and_detail_enrichment_share_job_then_profile_order(self):
        with self.factory.begin() as session:
            company = Company(source='managed_feed', source_id='worker-enrichment-lock-order', name='Employer')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/jobs.xml',
                           board_url='https://example.test/jobs.xml', status='parsed', enabled=True,
                           source_revision=1)
            profile = UserProfile(name='Developer', preferences={})
            session.add_all([feed, profile])
            session.flush()
            job = Job(feed_id=feed.id, external_id='role', title='Developer',
                      url='https://example.test/jobs/role', is_active=True,
                      raw_metadata={'description_method': 'verified_detail_jsonld'})
            session.add(job)
            session.flush()
            enrichment.refresh_enrichment(job)
            run = ScanRun(feed_id=feed.id, status='running')
            session.add(run)
            session.flush()
            feed_id, run_id, job_id = feed.id, run.id, job.id

        detail_fetch_started = threading.Event()
        release_detail_fetch = threading.Event()
        worker_matching = threading.Event()
        release_worker_matching = threading.Event()
        detail_waiting_for_job = threading.Event()
        errors = []

        class PausingClient:
            def get(self, _url):
                detail_fetch_started.set()
                if not release_detail_fetch.wait(6):
                    raise AssertionError('detail request did not resume')
                return {'state': 'network_error'}, b''

        def pause_worker_match(*args):
            if threading.current_thread().name == 'feed-worker':
                worker_matching.set()
                if not release_worker_matching.wait(6):
                    raise AssertionError('feed match computation did not resume')
            return {'eligible': False}

        def observe_detail_lock(_conn, _cursor, statement, *_args):
            normalized = ' '.join(statement.upper().split())
            if (threading.current_thread().name == 'detail-enrichment' and
                    'FROM JOBS' in normalized and
                    ('FOR NO KEY UPDATE' in normalized or 'FOR UPDATE' in normalized)):
                detail_waiting_for_job.set()

        event.listen(self.engine, 'before_cursor_execute', observe_detail_lock)

        def enrich_detail():
            try:
                enrichment.backfill(1, fetch_details=True, client=PausingClient())
            except Exception as error:
                errors.append(error)

        def persist_feed():
            try:
                worker._persist_result(feed_id, run_id, {
                    'state': 'ok', 'status': 200,
                    'parsed_feed': {'jobs': [{
                        'id': 'role', 'title': 'Developer', 'url': 'https://example.test/jobs/role',
                        'description': 'New verified description from the feed',
                    }], 'complete': False},
                }, b'', 1)
            except Exception as error:
                errors.append(error)

        detail_thread = threading.Thread(target=enrich_detail, name='detail-enrichment')
        worker_thread = threading.Thread(target=persist_feed, name='feed-worker')
        try:
            detail_thread.start()
            self.assertTrue(detail_fetch_started.wait(3), 'detail enrichment did not fetch its candidate')
            with patch.object(job_search, 'match_job', side_effect=pause_worker_match):
                worker_thread.start()
                self.assertTrue(worker_matching.wait(3), 'feed worker did not begin match refresh')
                release_detail_fetch.set()
                self.assertTrue(detail_waiting_for_job.wait(3),
                                'detail enrichment did not wait for the feed worker job lock')
                release_worker_matching.set()
                worker_thread.join(8)
                detail_thread.join(8)
        finally:
            release_detail_fetch.set()
            release_worker_matching.set()
            detail_thread.join(8)
            if worker_thread.ident is not None:
                worker_thread.join(8)
            event.remove(self.engine, 'before_cursor_execute', observe_detail_lock)

        self.assertFalse(detail_thread.is_alive())
        self.assertFalse(worker_thread.is_alive())
        self.assertEqual(errors, [])
        with self.factory() as session:
            self.assertEqual(session.get(Job, job_id).description,
                             'New verified description from the feed')

    def test_enrichment_backfill_updates_job_rows_before_locking_profiles(self):
        with self.factory.begin() as session:
            company = Company(source='managed_feed', source_id='enrichment-cache-lock-order', name='Employer')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/jobs.xml',
                           board_url='https://example.test/jobs.xml', status='parsed', enabled=True)
            session.add_all([feed, UserProfile(name='Developer', preferences={})])
            session.flush()
            session.add(Job(feed_id=feed.id, external_id='role', title='Developer',
                            url='https://example.test/jobs/role', is_active=True))

        lock_order = []

        def record(_conn, _cursor, statement, *_args):
            normalized = ' '.join(statement.upper().split())
            if normalized.startswith('UPDATE JOBS'):
                lock_order.append('jobs')
            elif 'FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized:
                lock_order.append('profiles')

        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            enrichment.backfill(1, fetch_details=False)
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)

        self.assertLess(lock_order.index('jobs'), lock_order.index('profiles'))

    def test_job_application_uses_no_key_lock_while_index_publication_holds_profile(self):
        with self.factory.begin() as session:
            profile = UserProfile(name='Developer', preferences={})
            company = Company(source='managed_feed', source_id='application-index-lock', name='Employer')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/jobs.xml',
                           board_url='https://example.test/jobs.xml', status='parsed', enabled=True)
            job = Job(feed=feed, external_id='role', title='Engineer',
                      url='https://example.test/jobs/role', is_active=True)
            session.add_all([profile, feed, job])
            session.flush()
            profile_id, job_id = profile.id, job.id

        index_at_publish = threading.Event()
        release_index = threading.Event()
        application_at_insert = threading.Event()
        application_done = threading.Event()
        generation_reads = 0
        errors = []

        def pause_index(_conn, _cursor, statement, *_args):
            nonlocal generation_reads
            if (threading.current_thread().name == 'profile-index-rebuild' and
                    statement.startswith('SELECT max(job_search_state.id)')):
                generation_reads += 1
                if generation_reads == 2:
                    index_at_publish.set()
                    if not release_index.wait(5):
                        raise AssertionError('index publication was not released')

        def observe_application(_conn, _cursor, statement, *_args):
            if (threading.current_thread().name == 'application-save' and
                    statement.upper().startswith('INSERT INTO JOB_APPLICATIONS')):
                application_at_insert.set()

        event.listen(self.engine, 'after_cursor_execute', pause_index)
        event.listen(self.engine, 'before_cursor_execute', observe_application)

        def rebuild():
            try:
                with patch.object(job_search, 'match_job', return_value={
                        'eligible': True, 'score': 70, 'fit_tier': 'recommended', 'uncertain': False}), \
                        patch.object(job_search, 'application_match_allowed', return_value=False), \
                        self.factory.begin() as session:
                    profile = session.get(UserProfile, profile_id)
                    job_search.ensure_profile_match_indexes(session, [profile])
            except Exception as error:
                errors.append(error)

        def save_application():
            try:
                with self.factory() as session:
                    session.execute(text("SET lock_timeout = '8s'"))
                    api.set_job_application(profile_id, job_id,
                                            api.ApplicationRequest(status='new'), session)
            except Exception as error:
                errors.append(error)
            finally:
                application_done.set()

        index_thread = threading.Thread(target=rebuild, name='profile-index-rebuild')
        application_thread = threading.Thread(target=save_application, name='application-save')
        try:
            index_thread.start()
            self.assertTrue(index_at_publish.wait(3), 'profile index did not reach publication')
            application_thread.start()
            progressed = (application_at_insert.wait(3) or application_done.wait(0.1))
            self.assertTrue(progressed, 'application save did not reach its profile foreign key')
            self.assertFalse(application_done.wait(0.2),
                             'application save bypassed the profile lock held by index publication')
        finally:
            release_index.set()
            index_thread.join(6)
            if application_thread.ident is not None:
                application_thread.join(6)
            event.remove(self.engine, 'after_cursor_execute', pause_index)
            event.remove(self.engine, 'before_cursor_execute', observe_application)

        self.assertFalse(index_thread.is_alive())
        self.assertFalse(application_thread.is_alive())
        self.assertEqual(errors, [])
        with self.factory() as session:
            self.assertIsNotNone(session.scalar(select(JobApplication).where(
                JobApplication.profile_id == profile_id, JobApplication.job_id == job_id)))

    def test_cached_enrichment_computation_does_not_hold_profile_locks(self):
        with self.factory.begin() as session:
            company = Company(source='managed_feed', source_id='enrichment-profile-lock-duration', name='Employer')
            feed = JobFeed(company=company, provider='rss', feed_url='https://example.test/jobs.xml',
                           board_url='https://example.test/jobs.xml', status='parsed', enabled=True)
            profile = UserProfile(name='Developer', preferences={})
            job = Job(feed=feed, external_id='role', title='Developer',
                      url='https://example.test/jobs/role', is_active=True)
            session.add_all([feed, profile, job])
            session.flush()
            profile_id = profile.id

        matching_started = threading.Event()
        continue_matching = threading.Event()
        errors = []

        def slow_match(*_args):
            matching_started.set()
            if not continue_matching.wait(6):
                raise AssertionError('cached match computation did not resume')
            return {'eligible': False}

        def backfill():
            try:
                enrichment.backfill(1, fetch_details=False)
            except Exception as error:
                errors.append(error)

        with patch.object(job_search, 'match_job', side_effect=slow_match):
            thread = threading.Thread(target=backfill, name='cached-enrichment')
            thread.start()
            try:
                self.assertTrue(matching_started.wait(3), 'cached refresh did not start matching')
                with self.factory.begin() as editor:
                    editor.execute(update(UserProfile).where(UserProfile.id == profile_id).values(
                        preferences={'application_details': {'first_name': 'Edited'}}))
            finally:
                continue_matching.set()
                thread.join(8)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

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
