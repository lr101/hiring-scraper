"""Only a complete source listing can establish that a posting disappeared."""
import unittest
from datetime import timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import discovery_worker, worker
from hiring_scraper.app.models import Base, Company, Job, JobApplication, JobFeed, ScanRun, UserProfile, utcnow


class JobClosureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.now = utcnow()
        with self.factory.begin() as session:
            company = Company(source='test', source_id='closure', name='Example')
            feed = JobFeed(company=company, provider='html_jobs', status='parsed',
                           feed_url='https://example.test/jobs', enabled=True)
            session.add(feed)
            session.flush()
            job = Job(feed=feed, external_id='old', title='Engineer',
                      url='https://example.test/jobs/old', last_seen_at=self.now)
            session.add(job)
            session.flush()
            self.feed_id, self.job_id = feed.id, job.id

    def scan(self, path, jobs, *, complete=True, state='ok', status=200, body=None):
        if path == 'discovery':
            with self.factory.begin() as session:
                return discovery_worker._upsert_jobs(
                    session, session.get(JobFeed, self.feed_id), jobs, complete, self.now)[1]
        with self.factory.begin() as session:
            run = ScanRun(feed_id=self.feed_id, status='running')
            session.add(run)
            session.flush()
            run_id = run.id
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=self.now):
            worker._persist_result(self.feed_id, run_id, {
                'state': state, 'status': status,
                'parsed_feed': {'jobs': jobs, 'complete': complete} if body is None else None}, body or b'')

    def assert_closure_lifecycle(self, path, remaining):
        self.scan(path, remaining)
        with self.factory() as session:
            job = session.get(Job, self.job_id)
            self.assertFalse(job.is_active)
            self.assertEqual(job.closed_at.replace(tzinfo=timezone.utc), self.now)
            self.assertEqual(job.missing_complete_scans, 1)
            active_urls = set(session.scalars(select(Job.url).where(Job.is_active.is_(True))).all())
            self.assertEqual(active_urls, {row['url'] for row in remaining})
        # Repeated scans must preserve when the job first closed.
        self.now += timedelta(hours=6)
        self.scan(path, remaining)
        with self.factory() as session:
            self.assertEqual(session.get(Job, self.job_id).closed_at.replace(tzinfo=timezone.utc),
                             self.now - timedelta(hours=6))
        self.scan(path, [{'id': 'old', 'title': 'Engineer', 'url': 'https://example.test/jobs/old'}])
        with self.factory() as session:
            job = session.get(Job, self.job_id)
            self.assertTrue(job.is_active)
            self.assertIsNone(job.closed_at)
            self.assertEqual(job.missing_complete_scans, 0)
            self.assertEqual(session.scalars(select(Job).where(Job.external_id == 'old')).all(), [job])

    def test_feed_scan_closes_missing_job_immediately_and_reopens_same_job(self):
        self.assert_closure_lifecycle('feed', [
            {'id': 'new', 'title': 'New role', 'url': 'https://example.test/jobs/new'}])

    def test_empty_feed_scan_closes_missing_job(self):
        self.assert_closure_lifecycle('feed', [])

    def test_discovery_closes_missing_job_immediately_and_reopens_same_job(self):
        self.assert_closure_lifecycle('discovery', [
            {'id': 'new', 'title': 'New role', 'url': 'https://example.test/jobs/new'}])

    def test_empty_discovery_scan_closes_missing_job(self):
        self.assert_closure_lifecycle('discovery', [])

    def assert_journey_closure(self, path):
        stages = {'waiting_for_reply': 'rejected', 'automated_application': 'rejected',
                  'new': 'closed', 'open': 'closed', 'in_gots_hand': 'closed', 'interview': 'closed',
                  'accepted': 'accepted', 'rejected': 'rejected', 'not_interested': 'closed', 'closed': 'closed'}
        with self.factory.begin() as session:
            for stage in stages:
                profile = UserProfile(name=stage, preferences={})
                session.add(profile)
                session.flush()
                session.add(JobApplication(profile_id=profile.id, job_id=self.job_id,
                                           status=stage, auto_created=stage == 'new'))
        self.scan(path, [], complete=False)
        with self.factory() as session:
            for application in session.scalars(select(JobApplication)):
                self.assertEqual(application.status, application.profile.name)
                self.assertIsNone(application.previous_status)
        self.scan(path, [])
        with self.factory() as session:
            applications = session.scalars(select(JobApplication)).all()
            for application in applications:
                old_stage = application.profile.name
                self.assertEqual(application.status, stages[old_stage])
                self.assertEqual(application.previous_status, old_stage if stages[old_stage] != old_stage else None)
            timestamps = {row.id: row.updated_at for row in applications}
        self.now += timedelta(hours=6)
        self.scan(path, [])
        with self.factory() as session:
            for application in session.scalars(select(JobApplication)):
                self.assertEqual(application.updated_at, timestamps[application.id])
        self.scan(path, [{'id': 'old', 'title': 'Engineer', 'url': 'https://example.test/jobs/old'}])
        with self.factory() as session:
            for application in session.scalars(select(JobApplication)):
                self.assertEqual(application.status, stages[application.profile.name])

    def test_feed_closure_updates_each_profiles_journey(self):
        self.assert_journey_closure('feed')

    def test_discovery_closure_updates_each_profiles_journey(self):
        self.assert_journey_closure('discovery')

    def test_unrecognized_empty_html_pages_do_not_close_jobs(self):
        for body in (b'<h1>Service unavailable</h1><p>Please try again later.</p>',
                     b'<h1>Careers</h1><p>Meet our team.</p>'):
            with self.subTest(body=body):
                self.scan('feed', [], body=body)
                with self.factory() as session:
                    job = session.get(Job, self.job_id)
                    self.assertTrue(job.is_active)
                    self.assertIsNone(job.closed_at)
                    self.assertEqual(job.missing_complete_scans, 0)
                    self.assertEqual(session.get(JobFeed, self.feed_id).status, 'incomplete')

    def test_explicit_empty_html_page_closes_missing_job(self):
        self.scan('feed', [], body=b'<h1>Careers</h1><p>No open positions at this time.</p>')
        with self.factory() as session:
            self.assertFalse(session.get(Job, self.job_id).is_active)
            self.assertIsNotNone(session.get(Job, self.job_id).closed_at)
            self.assertEqual(session.get(JobFeed, self.feed_id).status, 'complete_empty')

    def test_incomplete_or_failed_scans_do_not_close_missing_jobs(self):
        for path, options in [
            ('discovery', {'complete': False}),
            ('feed', {'complete': False}),
            ('feed', {'state': 'http_error', 'status': 500}),
            ('feed', {'state': 'http_error', 'status': 404}),
            ('feed', {'state': 'bot_challenge', 'status': 403}),
            ('feed', {'state': 'not_modified', 'status': 304}),
        ]:
            with self.subTest(path=path, options=options):
                self.scan(path, [], **options)
                with self.factory() as session:
                    job = session.get(Job, self.job_id)
                    self.assertTrue(job.is_active)
                    self.assertIsNone(job.closed_at)
                    self.assertEqual(job.missing_complete_scans, 0)


if __name__ == '__main__':
    unittest.main()
