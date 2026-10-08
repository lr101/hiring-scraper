"""Application tracking persists independently of vacancy and matching lifecycles."""
import unittest

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from hiring_scraper.app import api, models, profiles
from hiring_scraper.app.database import initialize_sqlite_schema


class JobApplicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.profile = profiles.create_profile(profiles.ProfileRequest(name='Developer'), self.session)
        self.other = profiles.create_profile(profiles.ProfileRequest(name='Designer'), self.session)
        company = models.Company(source='test', source_id='employer', name='Employer')
        feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.test/jobs', status='parsed')
        self.jobs = [models.Job(feed=feed, external_id=str(index), title=f'Role {index}',
                               url=f'https://example.test/jobs/{index}', is_remote=True)
                     for index in range(8)]
        self.session.add(company)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def save(self, job, status='new', profile=None):
        endpoint = getattr(api, 'set_job_application', None)
        self.assertIsNotNone(endpoint, 'Jobs must support saving application status')
        return endpoint((profile or self.profile)['id'], job.id,
                        api.ApplicationRequest(status=status), self.session)

    def overview(self, profile=None, **kwargs):
        endpoint = getattr(api, 'list_job_applications', None)
        self.assertIsNotNone(endpoint, 'Profiles must expose an application overview')
        return endpoint((profile or self.profile)['id'], session=self.session, **kwargs)

    def test_every_status_roundtrips_without_creating_duplicate_applications(self):
        for status in ['new', 'open', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted']:
            with self.subTest(status=status):
                saved = self.save(self.jobs[0], status)
                self.session.expire_all()
                self.assertEqual(saved['status'], status)
                self.assertEqual(saved['job_id'], self.jobs[0].id)
                self.assertEqual(saved['profile_id'], self.profile['id'])
                self.assertTrue(saved['updated_at'])
                page = self.overview()
                self.assertEqual(page['total'], 1)
                self.assertEqual(page['items'][0]['application']['status'], status)
                self.assertEqual(page['counts'][status], 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 1)

    def test_profiles_track_the_same_job_independently(self):
        self.save(self.jobs[0], 'interview')
        self.save(self.jobs[0], 'not_interested', self.other)
        self.assertEqual(self.overview()['items'][0]['application']['status'], 'interview')
        self.assertEqual(self.overview(self.other)['items'][0]['application']['status'], 'not_interested')
        self.save(self.jobs[1], 'open', self.other)
        self.assertEqual(self.overview()['total'], 1)

    def test_filtering_precedes_pagination_and_counts_include_all_statuses(self):
        self.save(self.jobs[0], 'new')
        self.save(self.jobs[1], 'interview')
        self.save(self.jobs[2], 'interview')
        page = self.overview(status='interview', offset=1, limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['items'][0]['application']['status'], 'interview')
        self.assertEqual(page['counts'], {'new': 1, 'open': 0, 'not_interested': 0,
                         'waiting_for_reply': 0, 'interview': 2, 'rejected': 0, 'accepted': 0})

    def test_closed_expired_and_out_of_scope_applications_remain_in_overview(self):
        self.save(self.jobs[0], 'waiting_for_reply')
        self.jobs[0].is_active = False
        self.jobs[0].raw_metadata = {'validThrough': '2000-01-01'}
        self.jobs[0].is_remote = False
        self.session.commit()
        page = self.overview()
        self.assertEqual(page['total'], 1)
        self.assertFalse(page['items'][0]['is_active'])
        self.assertTrue(page['items'][0]['expired'])
        self.assertEqual(page['items'][0]['company_name'], 'Employer')

    def test_tracking_is_visible_in_board_and_detail_without_changing_vacancy_status(self):
        before = api.get_job(self.jobs[0].id, self.session, profile_id=self.profile['id'])
        self.assertIsNone(before.get('application'))
        self.save(self.jobs[0], 'rejected')
        detail = api.get_job(self.jobs[0].id, self.session, profile_id=self.profile['id'])
        board = api.list_jobs(49, 8, 35, None, None, None, 0, 100,
                              session=self.session, profile_id=self.profile['id'])
        item = next(item for item in board['items'] if item['id'] == self.jobs[0].id)
        self.assertEqual(detail['application'], item['application'])
        self.assertEqual(detail['application']['status'], 'rejected')
        self.assertTrue(detail['is_active'])
        self.assertIsNone(api.get_job(self.jobs[0].id, self.session, profile_id=self.other['id']).get('application'))

    def test_untracked_jobs_are_not_silently_added_to_overview(self):
        page = self.overview()
        self.assertEqual(page['total'], 0)
        self.assertEqual(page['items'], [])
        self.assertEqual(sum(page['counts'].values()), 0)

    def test_invalid_status_and_missing_resources_are_rejected(self):
        self.assertIsNotNone(getattr(api, 'ApplicationRequest', None), 'Status payload must be validated')
        for value in ['applied', '', None, 'NEW']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                api.ApplicationRequest(status=value)
        for profile_id, job_id in [(99999, self.jobs[0].id), (self.profile['id'], 99999)]:
            with self.subTest(profile_id=profile_id), self.assertRaises(HTTPException) as error:
                api.set_job_application(profile_id, job_id, api.ApplicationRequest(status='open'), self.session)
            self.assertEqual(error.exception.status_code, 404)
        with self.assertRaises(HTTPException) as error:
            self.overview(profile={'id': 99999})
        self.assertEqual(error.exception.status_code, 404)

    def test_deleting_profile_cleans_up_tracking_but_preserves_job(self):
        self.save(self.jobs[0], 'accepted')
        profiles.delete_profile(self.profile['id'], self.session)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(models.JobApplication)), 0)
        self.assertIsNotNone(self.session.get(models.Job, self.jobs[0].id))

    def test_sqlite_startup_adds_tracking_to_existing_database_idempotently(self):
        engine = create_engine('sqlite://')
        try:
            models.Base.metadata.create_all(engine, tables=[table for table in models.Base.metadata.sorted_tables
                                                          if table.name != 'job_applications'])
            initialize_sqlite_schema(engine)
            initialize_sqlite_schema(engine)
            with Session(engine) as session:
                endpoint = getattr(api, 'list_job_applications', None)
                self.assertIsNotNone(endpoint, 'SQLite startup must make application tracking available')
                profile = profiles.create_profile(profiles.ProfileRequest(name='Existing database'), session)
                self.assertEqual(endpoint(profile['id'], session=session)['total'], 0)
        finally:
            engine.dispose()
