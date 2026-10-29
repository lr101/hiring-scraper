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

from hiring_scraper.app import api, database, default_job_feeds, discovery_worker, enrichment, job_search, profiles
from hiring_scraper.app import worker
from hiring_scraper.app.models import (
    Base, Company, DiscoveryJob, DiscoveryJobCompany, DiscoveryRun, Job, JobApplication, JobFeed, JobLocation,
    JobSearchRefresh, MatchSemanticDocument, ProfileJobMatch, ProfileLLMScore, ScanRun,
    SemanticSearchIndexState, UserProfile, utcnow,
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

    def test_profile_edit_locks_source_before_semantic_cache_lookup(self):
        from sqlalchemy.engine.cursor import CursorResult
        from hiring_scraper.app import profile_semantics
        from hiring_scraper.app.models import MatchSemanticDocument

        with self.factory.begin() as session:
            profile = UserProfile(name='Semantic editor', preferences={
                'desired_roles': ['Software developer'],
                'summary': 'I build web services and maintain relational databases.',
                'matching_defaults': {'min_match_score': 30},
            })
            session.add(profile)
            session.flush()
            profile_id = profile.id

        editor_read_cache = threading.Event()
        resume_editor = threading.Event()
        publisher_waiting_for_source = threading.Event()
        publisher_completed = threading.Event()
        errors = []
        original_scalar = CursorResult.scalar

        def scalar_with_interleaving(result):
            value = original_scalar(result)
            statement = result.context.statement.upper()
            if (threading.current_thread().name == 'profile-editor' and
                    'MATCH_SEMANTIC_DOCUMENTS.SOURCE_HASH' in statement):
                editor_read_cache.set()
                if not resume_editor.wait(5):
                    raise TimeoutError('Profile editor was not resumed')
            return value

        def observe_publisher_lock(_connection, _cursor, statement, _parameters, _context, _many):
            if (threading.current_thread().name == 'semantic-publisher' and
                    'FROM USER_PROFILES' in statement.upper() and 'FOR UPDATE' in statement.upper()):
                publisher_waiting_for_source.set()

        def edit_profile():
            try:
                with self.factory() as session:
                    profiles.update_profile(profile_id, profiles.ProfileRequest(
                        name='Semantic editor', desired_roles=['Software developer'],
                        matching_defaults={'min_match_score': 30},
                        summary='I design circuits and validate electronic hardware.'), session)
            except Exception as error:
                errors.append(error)

        def publish_profile_vectors():
            try:
                with patch.object(profile_semantics, 'SessionLocal', self.factory):
                    profile_semantics.refresh_semantic_documents_once(
                        encode=lambda texts, kind: [[1.0] + [0.0] * 383 for _ in texts])
            except Exception as error:
                errors.append(error)
            finally:
                publisher_completed.set()

        editor = threading.Thread(target=edit_profile, name='profile-editor', daemon=True)
        publisher = None
        event.listen(self.engine, 'before_cursor_execute', observe_publisher_lock)
        try:
            with patch.object(CursorResult, 'scalar', new=scalar_with_interleaving):
                editor.start()
                self.assertTrue(editor_read_cache.wait(3), 'Editor did not query the semantic cache')
                publisher = threading.Thread(target=publish_profile_vectors,
                                             name='semantic-publisher', daemon=True)
                publisher.start()
                self.assertTrue(publisher_waiting_for_source.wait(3), 'Publisher did not lock its profile source')
                self.assertFalse(publisher_completed.wait(.2),
                                 'Publisher passed an editor that had already checked for a cache row')
                resume_editor.set()
                editor.join(5)
                publisher.join(5)
                self.assertFalse(editor.is_alive(), 'Profile editor did not resume')
                self.assertFalse(publisher.is_alive(), 'Semantic publisher did not resume')
                self.assertEqual(errors, [])

                with self.factory() as session:
                    self.assertIsNone(session.scalar(select(MatchSemanticDocument).where(
                        MatchSemanticDocument.profile_id == profile_id)))
                with patch.object(profile_semantics, 'SessionLocal', self.factory):
                    self.assertTrue(profile_semantics.refresh_semantic_documents_once(
                        encode=lambda texts, kind: [[0.0, 1.0] + [0.0] * 382 for _ in texts]))
                with self.factory() as session:
                    self.assertIsNotNone(session.scalar(select(MatchSemanticDocument).where(
                        MatchSemanticDocument.profile_id == profile_id)))
        finally:
            resume_editor.set()
            if editor.is_alive():
                editor.join(5)
            if publisher is not None and publisher.is_alive():
                publisher.join(5)
            event.remove(self.engine, 'before_cursor_execute', observe_publisher_lock)

    def test_job_edit_uses_fk_compatible_lock_during_profile_index_publication(self):
        self.assert_job_writer_compatible_with_profile_index('edit')

    def test_semantic_publication_uses_fk_compatible_lock_during_profile_index_publication(self):
        self.assert_job_writer_compatible_with_profile_index('semantic')

    def test_salary_edit_locks_job_before_profile_during_semantic_publication(self):
        self.assert_salary_edit_lock_order('semantic')

    def test_salary_edit_locks_job_before_profile_during_application_tracking(self):
        self.assert_salary_edit_lock_order('application')

    def test_feed_change_new_job_change_reserves_later_job_before_profiles(self):
        _, feed_id, run_id = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Multi-flush lock test', preferences={})
            session.add(profile)
            session.flush()
            profile_id = profile.id
            job_a = Job(feed_id=feed_id, external_id='multi-a', title='Engineer', salary='90000 USD',
                        description='Build software.', url='https://example.test/multi/a', is_active=True)
            job_b = Job(feed_id=feed_id, external_id='multi-b', title='Engineer', salary='100000 USD',
                        description='Maintain software.', url='https://example.test/multi/b', is_active=True)
            session.add_all([job_a, job_b])
            session.flush()
            job_a_id, job_b_id = job_a.id, job_b.id
            session.add_all([
                ProfileLLMScore(profile_id=profile_id, job_id=job_a_id,
                    source_hash='before-a', version='test', assessment={}),
                ProfileLLMScore(profile_id=profile_id, job_id=job_b_id,
                    source_hash='before-b', version='test', assessment={}),
            ])

        application_at_profile = threading.Event()
        release_application_profile = threading.Event()
        worker_waiting_for_job_b = threading.Event()
        worker_updated_profile = threading.Event()
        application_done = threading.Event()
        worker_done = threading.Event()
        errors = []

        def parameter_values(parameters):
            return set(parameters.values()) if isinstance(parameters, dict) else set(parameters or ())

        def before_cursor(_connection, _cursor, statement, parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            thread_name = threading.current_thread().name
            if (thread_name == 'feed-worker' and 'FROM JOBS' in normalized and
                    ('FOR NO KEY UPDATE' in normalized or 'FOR UPDATE' in normalized) and
                    job_b_id in parameter_values(parameters)):
                worker_waiting_for_job_b.set()
            if (thread_name == 'application-save' and 'FROM USER_PROFILES' in normalized and
                    'FOR UPDATE' in normalized):
                application_at_profile.set()
                if not release_application_profile.wait(5):
                    raise TimeoutError('Application tracking did not reach its profile lock')

        def after_cursor(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            if (threading.current_thread().name == 'feed-worker' and
                    normalized.startswith('UPDATE USER_PROFILES')):
                worker_updated_profile.set()

        def save_application():
            try:
                with self.factory() as session:
                    api.set_job_application(profile_id, job_b_id,
                                            api.ApplicationRequest(status='new'), session)
            except Exception as error:
                errors.append(error)
            finally:
                application_done.set()

        def persist_feed():
            try:
                with patch.object(worker, 'refresh_application_matches_for_jobs', lambda *args, **kwargs: None):
                    worker._persist_result(feed_id, run_id, {
                        'state': 'ok', 'status': 200,
                        'parsed_feed': {'complete': False, 'jobs': [
                            {'id': 'multi-a', 'title': 'Engineer', 'url': 'https://example.test/multi/a',
                             'description': 'Build software.', 'salary': '95000 USD'},
                            {'id': 'multi-x', 'title': 'Analyst', 'url': 'https://example.test/multi/x',
                             'description': 'Analyze systems.', 'salary': '80000 USD'},
                            {'id': 'multi-b', 'title': 'Engineer', 'url': 'https://example.test/multi/b',
                             'description': 'Maintain software.', 'salary': '110000 USD'},
                        ]},
                    }, b'', 1)
            except Exception as error:
                errors.append(error)
            finally:
                worker_done.set()

        application_thread = threading.Thread(target=save_application, name='application-save', daemon=True)
        worker_thread = threading.Thread(target=persist_feed, name='feed-worker', daemon=True)
        event.listen(self.engine, 'before_cursor_execute', before_cursor)
        event.listen(self.engine, 'after_cursor_execute', after_cursor)
        try:
            application_thread.start()
            self.assertTrue(application_at_profile.wait(3),
                            'Application tracking did not lock the later job before its profile')
            worker_thread.start()
            # On the safe path, the feed writer locks both existing changed jobs
            # before any profile invalidation; it waits on B while the application
            # owns B. The old per-new-job flush updates the profile after A first.
            self.assertTrue(worker_waiting_for_job_b.wait(3),
                            'Feed persistence did not reserve the later changed job')
            self.assertFalse(worker_updated_profile.is_set(),
                             'Feed persistence invalidated a profile before locking every changed job')
            release_application_profile.set()
            application_thread.join(8)
            worker_thread.join(8)
            self.assertFalse(application_thread.is_alive(), 'Application tracking did not finish')
            self.assertFalse(worker_thread.is_alive(), 'Feed persistence did not finish')
            self.assertEqual(errors, [])
            self.assertTrue(worker_updated_profile.is_set())
            with self.factory() as session:
                self.assertEqual(session.get(Job, job_a_id).salary, '95000 USD')
                self.assertEqual(session.get(Job, job_b_id).salary, '110000 USD')
                self.assertIsNotNone(session.scalar(select(Job.id).where(
                    Job.feed_id == feed_id, Job.external_id == 'multi-x')))
                self.assertIsNone(session.get(ProfileLLMScore, (profile_id, job_a_id)))
                self.assertIsNone(session.get(ProfileLLMScore, (profile_id, job_b_id)))
        finally:
            release_application_profile.set()
            if application_thread.is_alive():
                application_thread.join(8)
            if worker_thread.is_alive():
                worker_thread.join(8)
            event.remove(self.engine, 'before_cursor_execute', before_cursor)
            event.remove(self.engine, 'after_cursor_execute', after_cursor)

    def test_discovery_change_new_job_change_reserves_later_job_before_profiles(self):
        company_id, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Discovery batch lock test', preferences={})
            job_a = Job(feed_id=feed_id, external_id='discovery-a', title='Engineer',
                        description='Build software.', url='https://example.test/discovery/a', is_active=True)
            job_b = Job(feed_id=feed_id, external_id='discovery-b', title='Engineer',
                        description='Maintain software.', url='https://example.test/discovery/b', is_active=True)
            discovery_run = DiscoveryRun(company_id=company_id, status='running')
            session.add_all([profile, job_a, job_b, discovery_run])
            session.flush()
            profile_id, job_a_id, job_b_id, discovery_run_id = (
                profile.id, job_a.id, job_b.id, discovery_run.id)

        application_at_profile = threading.Event()
        release_application_profile = threading.Event()
        discovery_waiting_for_job_b = threading.Event()
        discovery_attempted_profile_reservation = threading.Event()
        application_done = threading.Event()
        discovery_done = threading.Event()
        errors = []

        def parameter_values(parameters):
            return set(parameters.values()) if isinstance(parameters, dict) else set(parameters or ())

        def before_cursor(_connection, _cursor, statement, parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            thread_name = threading.current_thread().name
            if (thread_name == 'discovery-writer' and 'FROM JOBS' in normalized and
                    ('FOR NO KEY UPDATE' in normalized or 'FOR UPDATE' in normalized) and
                    job_b_id in parameter_values(parameters)):
                discovery_waiting_for_job_b.set()
            if (thread_name == 'discovery-writer' and 'FROM USER_PROFILES' in normalized and
                    'FOR UPDATE' in normalized):
                discovery_attempted_profile_reservation.set()
            if (thread_name == 'application-save' and 'FROM USER_PROFILES' in normalized and
                    'FOR UPDATE' in normalized):
                application_at_profile.set()
                if not release_application_profile.wait(5):
                    raise TimeoutError('Application tracking did not reach its profile lock')

        def save_application():
            try:
                with self.factory() as session:
                    api.set_job_application(profile_id, job_b_id,
                                            api.ApplicationRequest(status='new'), session)
            except Exception as error:
                errors.append(error)
            finally:
                application_done.set()

        def persist_discovery():
            try:
                result = {'status': 'career_content_found', 'pages': [], 'boards': [{
                    'provider': 'rss', 'feed_url': 'https://example.test/jobs.xml',
                    'board_url': 'https://example.test/jobs.xml', 'feed_state': 'parsed',
                    'complete': False, 'jobs': [
                        {'id': 'discovery-a', 'title': 'Engineer', 'url': 'https://example.test/discovery/a',
                         'description': 'Build cloud software.'},
                        {'id': 'discovery-x', 'title': 'Analyst', 'url': 'https://example.test/discovery/x',
                         'description': 'Analyze business systems.'},
                        {'id': 'discovery-b', 'title': 'Engineer', 'url': 'https://example.test/discovery/b',
                         'description': 'Maintain data software.'},
                    ],
                }]}
                with patch.object(discovery_worker, 'refresh_application_matches_for_jobs',
                                  lambda *args, **kwargs: None):
                    discovery_worker._persist_discovery(company_id, discovery_run_id, result)
            except Exception as error:
                errors.append(error)
            finally:
                discovery_done.set()

        application_thread = threading.Thread(target=save_application, name='application-save', daemon=True)
        discovery_thread = threading.Thread(target=persist_discovery, name='discovery-writer', daemon=True)
        event.listen(self.engine, 'before_cursor_execute', before_cursor)
        try:
            application_thread.start()
            self.assertTrue(application_at_profile.wait(3),
                            'Application tracking did not lock the later job before its profile')
            discovery_thread.start()
            self.assertTrue(discovery_waiting_for_job_b.wait(3),
                            'Discovery did not reserve the later existing job before profile rows')
            self.assertFalse(discovery_attempted_profile_reservation.is_set(),
                             'Discovery reserved profiles before all changed jobs were locked')
            release_application_profile.set()
            application_thread.join(8)
            discovery_thread.join(8)
            self.assertFalse(application_thread.is_alive(), 'Application tracking did not finish')
            self.assertFalse(discovery_thread.is_alive(), 'Discovery persistence did not finish')
            self.assertEqual(errors, [])
            self.assertTrue(discovery_attempted_profile_reservation.is_set())
            with self.factory() as session:
                self.assertEqual(session.get(Job, job_a_id).description, 'Build cloud software.')
                self.assertEqual(session.get(Job, job_b_id).description, 'Maintain data software.')
                self.assertIsNotNone(session.scalar(select(Job.id).where(
                    Job.feed_id == feed_id, Job.external_id == 'discovery-x')))
        finally:
            release_application_profile.set()
            if application_thread.is_alive():
                application_thread.join(8)
            if discovery_thread.is_alive():
                discovery_thread.join(8)
            event.remove(self.engine, 'before_cursor_execute', before_cursor)

    def test_job_and_location_changes_reserve_all_profiles_before_invalidation(self):
        _, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profiles = [UserProfile(name=f'Lock order profile {index}', preferences={}) for index in range(3)]
            job = Job(feed_id=feed_id, external_id='profile-lock-order', title='Engineer',
                      url='https://example.test/profile-lock-order', salary='90000 USD', is_active=True)
            session.add_all([*profiles, job])
            session.flush()
            profile_ids = [profile.id for profile in profiles]
            job_id = job.id
            session.add(ProfileLLMScore(profile_id=profile_ids[1], job_id=job_id,
                source_hash='source', version='test', assessment={}))
            session.add(JobLocation(job_id=job_id, label='Berlin', country_code='DE'))

        def assert_all_profile_locks_precede_invalidation(mutate):
            statements = []

            def record(_connection, _cursor, statement, _parameters, _context, _many):
                normalized = ' '.join(statement.upper().split())
                if 'USER_PROFILES' in normalized and (
                        'FOR UPDATE' in normalized or normalized.startswith('UPDATE USER_PROFILES')):
                    statements.append(normalized)

            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                with self.factory.begin() as session:
                    mutate(session)
                    session.flush()
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)

            reservations = [statement for statement in statements if 'FOR UPDATE' in statement]
            invalidations = [statement for statement in statements
                             if statement.startswith('UPDATE USER_PROFILES')]
            self.assertTrue(reservations, f'No profile reservation was taken: {statements}')
            self.assertTrue(invalidations, f'No profile invalidation was issued: {statements}')
            reservation = reservations[0]
            self.assertLess(statements.index(reservation), statements.index(invalidations[0]))
            self.assertIn('ORDER BY USER_PROFILES.ID', reservation)
            self.assertNotIn(' WHERE ', reservation,
                             'Source writes must reserve the complete profile set, not only cached-score owners')

        def change_job(session):
            session.get(Job, job_id).salary = '100000 USD'

        def change_location(session):
            location = session.scalar(select(JobLocation).where(JobLocation.job_id == job_id))
            location.label = 'Hamburg'

        assert_all_profile_locks_precede_invalidation(change_job)
        assert_all_profile_locks_precede_invalidation(change_location)

    def test_source_change_reserves_every_job_updated_in_the_flush_before_profiles(self):
        _, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Bookkeeping lock profile', preferences={})
            job_a = Job(feed_id=feed_id, external_id='source-change-a', title='Engineer',
                        url='https://example.test/source-change/a', salary='90000 USD', is_active=True)
            job_b = Job(feed_id=feed_id, external_id='source-change-b', title='Engineer',
                        url='https://example.test/source-change/b', salary='100000 USD', is_active=True)
            session.add_all([profile, job_a, job_b])
            session.flush()
            job_a_id, job_b_id = job_a.id, job_b.id

        statements = []
        def record(_connection, _cursor, statement, parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            if (('FROM JOBS' in normalized and
                 ('FOR NO KEY UPDATE' in normalized or 'FOR UPDATE' in normalized)) or
                    ('FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized)):
                values = set(parameters.values()) if isinstance(parameters, dict) else set(parameters or ())
                statements.append((normalized, values))

        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            with self.factory.begin() as session:
                session.get(Job, job_a_id).salary = '110000 USD'
                session.get(Job, job_b_id).last_seen_at = utcnow()
                session.flush()
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)

        job_reservations = [(index, values) for index, (statement, values) in enumerate(statements)
                            if 'FROM JOBS' in statement]
        profile_reservations = [index for index, (statement, _values) in enumerate(statements)
                                if 'FROM USER_PROFILES' in statement]
        self.assertTrue(job_reservations, f'No job source lock was taken: {statements}')
        self.assertTrue(profile_reservations, f'No profile lock was taken: {statements}')
        first_job_lock, locked_ids = job_reservations[0]
        self.assertIn(job_a_id, locked_ids)
        self.assertIn(job_b_id, locked_ids,
                      'Bookkeeping-only Job UPDATEs must be locked before profile rows')
        self.assertLess(first_job_lock, profile_reservations[0])

    def test_complete_discovery_reserves_inactive_job_rows_before_profile_lock(self):
        company_id, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Inactive discovery lock profile', preferences={})
            job_a = Job(feed_id=feed_id, external_id='complete-a', title='Engineer',
                        description='Build software.', url='https://example.test/complete/a', is_active=True)
            job_b = Job(feed_id=feed_id, external_id='complete-b', title='Engineer',
                        description='Maintain software.', url='https://example.test/complete/b', is_active=False)
            job_c = Job(feed_id=feed_id, external_id='complete-c', title='Analyst',
                        description='Analyze systems.', url='https://example.test/complete/c', is_active=False)
            discovery_run = DiscoveryRun(company_id=company_id, status='running')
            session.add_all([profile, job_a, job_b, job_c, discovery_run])
            session.flush()
            job_ids = {job_a.id, job_b.id, job_c.id}
            discovery_run_id = discovery_run.id

        locks_before_profile = []
        def record(_connection, _cursor, statement, parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            if ('FROM JOBS' in normalized and
                    ('FOR NO KEY UPDATE' in normalized or 'FOR UPDATE' in normalized)):
                values = set(parameters.values()) if isinstance(parameters, dict) else set(parameters or ())
                locks_before_profile.append(('job', values))
            if 'FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized:
                locks_before_profile.append(('profiles', set()))

        result = {'status': 'career_content_found', 'pages': [], 'boards': [{
            'provider': 'rss', 'feed_url': 'https://example.test/jobs.xml',
            'board_url': 'https://example.test/jobs.xml', 'feed_state': 'parsed', 'complete': True,
            'jobs': [
                {'id': 'complete-a', 'title': 'Engineer', 'url': 'https://example.test/complete/a',
                 'description': 'Build cloud software.'},
                {'id': 'complete-new', 'title': 'Analyst', 'url': 'https://example.test/complete/new',
                 'description': 'Analyze business systems.'},
                {'id': 'complete-b', 'title': 'Engineer', 'url': 'https://example.test/complete/b',
                 'description': 'Maintain cloud software.'},
            ],
        }]}
        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            with patch.object(discovery_worker, 'refresh_application_matches_for_jobs',
                              lambda *args, **kwargs: None):
                discovery_worker._persist_discovery(company_id, discovery_run_id, result)
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)

        profile_index = next(index for index, (kind, _values) in enumerate(locks_before_profile)
                             if kind == 'profiles')
        reserved_ids = set().union(*(values for kind, values in locks_before_profile[:profile_index]
                                     if kind == 'job'))
        self.assertTrue(job_ids.issubset(reserved_ids),
                        'Complete boards update/reactivate inactive jobs, so they must all be reserved first')
        with self.factory() as session:
            self.assertTrue(session.scalar(select(Job).where(Job.external_id == 'complete-b')).is_active)
            job_c = session.scalar(select(Job).where(Job.external_id == 'complete-c'))
            self.assertEqual(job_c.missing_complete_scans, 1)

    def assert_salary_edit_lock_order(self, competitor_kind):
        from hiring_scraper.app import profile_semantics

        _, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Salary lock test', preferences={
                'desired_roles': ['Software developer'],
                'skills': ['Python', 'SQL'],
                'summary': 'I build and maintain software services.',
                'matching_defaults': {'min_match_score': 30},
            })
            session.add(profile)
            session.flush()
            profile_id = profile.id

        if competitor_kind == 'semantic':
            # Cache the profile first so the next semantic batch publishes only
            # the job and then follows the job -> profile lock order.
            with patch.object(profile_semantics, 'SessionLocal', self.factory):
                self.assertTrue(profile_semantics.refresh_semantic_documents_once(
                    encode=lambda texts, kind: [[1.0] + [0.0] * 383 for _ in texts]))

        with self.factory.begin() as session:
            job = Job(feed_id=feed_id, external_id=f'salary-lock-{competitor_kind}',
                      title='Engineer', salary='100000 USD',
                      description='Build and maintain reliable software services.',
                      url=f'https://example.test/salary-lock-{competitor_kind}', is_active=True)
            session.add(job)
            session.flush()
            job_id = job.id
            session.add(ProfileLLMScore(profile_id=profile_id, job_id=job_id,
                source_hash='before-edit', version='test', assessment={}))

        competitor_at_profile = threading.Event()
        release_competitor = threading.Event()
        editor_requested_job = threading.Event()
        editor_updated_profile = threading.Event()
        errors = []

        def before_cursor(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            thread_name = threading.current_thread().name
            if (thread_name == 'salary-editor' and 'FROM JOBS' in normalized and
                    ('FOR NO KEY UPDATE' in normalized or 'FOR UPDATE' in normalized)):
                editor_requested_job.set()
            if thread_name == 'salary-editor' and normalized.startswith('UPDATE USER_PROFILES'):
                editor_updated_profile.set()
            if ('FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized and
                    ((competitor_kind == 'application' and thread_name == 'application-save') or
                     (competitor_kind == 'semantic' and thread_name == 'semantic-publisher'))):
                competitor_at_profile.set()
                if not release_competitor.wait(5):
                    raise TimeoutError('Competing writer was not released')

        def run_competitor():
            try:
                if competitor_kind == 'application':
                    with self.factory() as session:
                        session.execute(text("SET lock_timeout = '8s'"))
                        api.set_job_application(profile_id, job_id,
                                                api.ApplicationRequest(status='new'), session)
                else:
                    with patch.object(profile_semantics, 'SessionLocal', self.factory):
                        profile_semantics.refresh_semantic_documents_once(
                            encode=lambda texts, kind: [[0.0, 1.0] + [0.0] * 382 for _ in texts])
            except Exception as error:
                errors.append(error)

        def edit_salary():
            try:
                with self.factory() as session:
                    session.execute(text("SET lock_timeout = '8s'"))
                    job = session.get(Job, job_id)
                    job.salary = '120000 USD'
                    session.commit()
            except Exception as error:
                errors.append(error)

        competitor_name = 'application-save' if competitor_kind == 'application' else 'semantic-publisher'
        competitor = threading.Thread(target=run_competitor, name=competitor_name, daemon=True)
        editor = threading.Thread(target=edit_salary, name='salary-editor', daemon=True)
        event.listen(self.engine, 'before_cursor_execute', before_cursor)
        try:
            competitor.start()
            self.assertTrue(competitor_at_profile.wait(3),
                            'Competing writer did not hold the job before requesting the profile')
            editor.start()
            self.assertTrue(editor_requested_job.wait(3),
                            'Salary editor did not request the job lock before profile invalidation')
            self.assertFalse(editor_updated_profile.wait(.2),
                             'Salary editor updated the profile before obtaining the job lock')
            release_competitor.set()
            competitor.join(8)
            editor.join(8)
            self.assertFalse(competitor.is_alive(), 'Competing writer did not finish')
            self.assertFalse(editor.is_alive(), 'Salary editor did not finish')
            self.assertEqual(errors, [])
            with self.factory() as session:
                self.assertEqual(session.get(Job, job_id).salary, '120000 USD')
                self.assertIsNone(session.get(ProfileLLMScore, (profile_id, job_id)))
        finally:
            release_competitor.set()
            if competitor.is_alive():
                competitor.join(8)
            if editor.is_alive():
                editor.join(8)
            event.remove(self.engine, 'before_cursor_execute', before_cursor)

    def assert_job_writer_compatible_with_profile_index(self, writer_kind):
        from hiring_scraper.app import profile_semantics

        _, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Index lock test', preferences={
                'desired_roles': ['Software developer'],
                'skills': ['Python', 'SQL'],
                'summary': 'I build and maintain software services.',
                'matching_defaults': {'min_match_score': 30},
            }, match_index_version=None)
            session.add(profile)
            session.flush()
            profile_id = profile.id

        if writer_kind == 'semantic':
            # Index the profile alone so the semantic worker later queues only
            # the job and holds its job lock while waiting for the profile.
            with patch.object(profile_semantics, 'SessionLocal', self.factory):
                self.assertTrue(profile_semantics.refresh_semantic_documents_once(
                    encode=lambda texts, kind: [[1.0] + [0.0] * 383 for _ in texts]))

        with self.factory.begin() as session:
            job = Job(feed_id=feed_id, external_id=f'lock-{writer_kind}', title='Engineer',
                      description='Your tasks:\nBuild and maintain reliable software services.\n',
                      url=f'https://example.test/lock-{writer_kind}', is_active=True)
            session.add(job)
            session.flush()
            job_id = job.id
            if writer_kind == 'edit':
                session.add(ProfileLLMScore(profile_id=profile_id, job_id=job_id,
                    source_hash='before-edit', version='test', assessment={}))

        index_holds_profile = threading.Event()
        continue_index = threading.Event()
        writer_holds_job = threading.Event()
        writer_waits_for_profile = threading.Event()
        index_inserts_matches = threading.Event()
        errors = []
        index_results = []
        writer_results = []

        def after_cursor(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            thread_name = threading.current_thread().name
            if (thread_name == 'profile-index-rebuild' and 'FROM USER_PROFILES' in normalized and
                    'FOR UPDATE' in normalized):
                index_holds_profile.set()
                if not continue_index.wait(5):
                    raise TimeoutError('Index publisher was not released')
            if (thread_name in {'job-editor', 'semantic-publisher'} and 'FROM JOBS' in normalized and
                    ('FOR UPDATE' in normalized or 'FOR NO KEY UPDATE' in normalized)):
                writer_holds_job.set()

        def before_cursor(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = ' '.join(statement.upper().split())
            thread_name = threading.current_thread().name
            if thread_name == 'profile-index-rebuild' and 'INSERT INTO PROFILE_JOB_MATCHES' in normalized:
                index_inserts_matches.set()
            if (thread_name == 'job-editor' and (
                    normalized.startswith('UPDATE USER_PROFILES') or
                    ('FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized))):
                writer_waits_for_profile.set()
            if (thread_name == 'semantic-publisher' and 'FROM USER_PROFILES' in normalized and
                    'FOR UPDATE' in normalized):
                writer_waits_for_profile.set()

        def rebuild_index():
            try:
                with patch.object(job_search, 'SessionLocal', self.factory), \
                        patch.object(job_search, 'match_job', return_value={
                            'eligible': True, 'score': 70, 'fit_tier': 'recommended', 'uncertain': False}), \
                        patch.object(job_search, 'application_match_allowed', return_value=False):
                    index_results.append(job_search.refresh_profile_match_indexes_once())
            except Exception as error:
                errors.append(error)

        def edit_job():
            try:
                with self.factory() as session:
                    job = session.get(Job, job_id)
                    job.description = 'Your tasks:\nDesign and validate electronic hardware.\n'
                    session.commit()
                writer_results.append(True)
            except Exception as error:
                errors.append(error)

        def publish_job_semantics():
            try:
                with patch.object(profile_semantics, 'SessionLocal', self.factory):
                    writer_results.append(profile_semantics.refresh_semantic_documents_once(
                        encode=lambda texts, kind: [[0.0, 1.0] + [0.0] * 382 for _ in texts]))
            except Exception as error:
                errors.append(error)

        writer_thread = threading.Thread(
            target=edit_job if writer_kind == 'edit' else publish_job_semantics,
            name='job-editor' if writer_kind == 'edit' else 'semantic-publisher', daemon=True)
        index_thread = threading.Thread(target=rebuild_index, name='profile-index-rebuild', daemon=True)
        event.listen(self.engine, 'after_cursor_execute', after_cursor)
        event.listen(self.engine, 'before_cursor_execute', before_cursor)
        try:
            index_thread.start()
            self.assertTrue(index_holds_profile.wait(3), 'Index rebuild did not lock the profile before publishing')
            writer_thread.start()
            self.assertTrue(writer_holds_job.wait(3), 'Job writer did not lock its source row')
            self.assertTrue(writer_waits_for_profile.wait(3), 'Job writer did not reach the profile publication lock')
            continue_index.set()
            self.assertTrue(index_inserts_matches.wait(3), 'Full index did not publish the job match')
            index_thread.join(6)
            writer_thread.join(6)
            self.assertFalse(index_thread.is_alive(), 'Profile index rebuild did not finish')
            self.assertFalse(writer_thread.is_alive(), 'Job writer did not finish')
            self.assertEqual(errors, [])
            self.assertEqual(index_results, [True])
            self.assertEqual(writer_results, [True])
            with self.factory() as session:
                self.assertIsNotNone(session.get(ProfileJobMatch, (profile_id, job_id)))
                if writer_kind == 'semantic':
                    self.assertIsNotNone(session.scalar(select(MatchSemanticDocument).where(
                        MatchSemanticDocument.job_id == job_id)))
        finally:
            continue_index.set()
            if index_thread.is_alive():
                index_thread.join(6)
            if writer_thread.is_alive():
                writer_thread.join(6)
            event.remove(self.engine, 'after_cursor_execute', after_cursor)
            event.remove(self.engine, 'before_cursor_execute', before_cursor)

    def test_journey_reads_while_background_matching_is_pending(self):
        _, feed_id, _ = self.seed_feed()
        with self.factory.begin() as session:
            profile = UserProfile(name='Journey refresh', preferences={'matching_defaults': {'min_match_score': 0}})
            job = Job(feed_id=feed_id, external_id='journey', title='Engineer',
                      url='https://example.test/journey', is_remote=True)
            session.add_all([profile, job])
            session.flush()
            profile_id, job_id = profile.id, job.id
        with patch.object(api, 'IS_SQLITE', False):
            with self.factory() as session:
                pending = api.list_applications(profile_ids=str(profile_id), statuses='all', session=session)
            self.assertTrue(pending['matching_pending'])
            self.assertEqual(pending['items'], [])
            # Another replica's worker owns the backfill; this replica must
            # return without blocking or touching the profile's index.
            with self.factory() as owner, patch.object(job_search, 'SessionLocal', self.factory):
                owner.execute(text('SELECT pg_advisory_xact_lock(85793011, 4)'))
                self.assertFalse(job_search.refresh_profile_match_indexes_once())
            with self.factory() as feed_initializer, patch.object(job_search, 'SessionLocal', self.factory):
                # Feed bootstrap has a separate lock and must not prevent
                # matching work (or be blocked by a long matching scan).
                feed_initializer.execute(text('SELECT pg_advisory_xact_lock(85793011, 3)'))
                self.assertTrue(job_search.refresh_profile_match_indexes_once())
                self.assertFalse(job_search.refresh_profile_match_indexes_once())
            with self.factory() as session:
                ready = api.list_applications(profile_ids=str(profile_id), statuses='all', session=session)
            self.assertFalse(ready['matching_pending'])
            self.assertEqual([item['id'] for item in ready['items']], [job_id])

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
            session.add(JobApplication(profile_id=profile.id, job_id=job.id, status='new',
                                        auto_created=True))
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
            application = session.scalar(select(JobApplication).where(
                JobApplication.profile_id == profile_id, JobApplication.job_id == job_id))
            self.assertEqual(application.status, 'closed')
            self.assertEqual(application.previous_status, 'new')

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
            normalized = ' '.join(statement.upper().split())
            if (threading.current_thread().name == 'inventory-writer' and (
                    'PG_ADVISORY_XACT_LOCK(85793011, 2)' in normalized or
                    ('FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized))):
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
                            'inventory writer did not request profile reservation or the generation guard')
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
        application_waiting_for_profile = threading.Event()
        application_done = threading.Event()
        errors = []

        def pause_index(_conn, _cursor, statement, *_args):
            normalized = ' '.join(statement.upper().split())
            if (threading.current_thread().name == 'profile-index-rebuild' and
                    'FROM USER_PROFILES' in normalized and 'FOR UPDATE' in normalized):
                index_at_publish.set()
                if not release_index.wait(5):
                    raise AssertionError('index publication was not released')

        def observe_application(_conn, _cursor, statement, *_args):
            if (threading.current_thread().name == 'application-save' and
                    'FROM USER_PROFILES' in statement.upper() and 'FOR UPDATE' in statement.upper()):
                application_waiting_for_profile.set()

        event.listen(self.engine, 'after_cursor_execute', pause_index)
        event.listen(self.engine, 'before_cursor_execute', observe_application)

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
            progressed = (application_waiting_for_profile.wait(3) or application_done.wait(0.1))
            self.assertTrue(progressed, 'application save did not wait for its profile publication lock')
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
            application = session.scalar(select(JobApplication).where(
                JobApplication.profile_id == profile_id, JobApplication.job_id == job_id))
            self.assertIsNotNone(application)
            self.assertFalse(application.auto_created)

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
