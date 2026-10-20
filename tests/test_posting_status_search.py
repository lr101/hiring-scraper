"""Closed postings remain searchable without appearing in the default feed."""
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hiring_scraper.app import api
from hiring_scraper.app.models import Base, Company, Job, JobFeed, JobLocation, utcnow


class PostingStatusSearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.closed_at = utcnow() - timedelta(days=1)
        company = Company(source='test', source_id='archive', name='Example', country_code='DE',
                          state='Baden-Württemberg', latitude=49.0068705, longitude=8.4034195)
        feed = JobFeed(company=company, provider='html_jobs', status='parsed',
                       feed_url='https://example.test/jobs')
        for key, active, location, expired in [
            ('open', True, 'Karlsruhe', False), ('closed', False, 'Karlsruhe', False),
            ('expired-closed', False, 'Karlsruhe', True), ('away', False, 'Berlin', False),
        ]:
            feed.jobs.append(Job(external_id=key, title=f'Engineer {key}',
                url=f'https://example.test/jobs/{key}', is_active=active,
                closed_at=None if active else self.closed_at,
                raw_metadata={'validThrough': '2020-01-01'} if expired else {},
                locations=[JobLocation(label=location, country_code='DE')]))
        self.session.add(feed)
        self.session.commit()

    def search(self, **options):
        return api.list_jobs(49.0068705, 8.4034195, 15, None, None, 'Karlsruhe', 0, 20,
                             session=self.session, **options)

    def test_default_search_shows_only_open_jobs_with_explicit_state(self):
        result = self.search()
        self.assertEqual([item['external_id'] for item in result['items']], ['open'])
        self.assertEqual(result['items'][0]['posting_status'], 'open')
        self.assertIsNone(result['items'][0]['closed_at'])

    def test_closed_search_includes_expired_closed_jobs_and_retains_location_filter(self):
        result = self.search(posting_status='closed')
        self.assertEqual({item['external_id'] for item in result['items']}, {'closed', 'expired-closed'})
        self.assertEqual(result['total'], 2)
        for item in result['items']:
            self.assertFalse(item['is_active'])
            self.assertEqual(item['posting_status'], 'closed')
            self.assertEqual(datetime.fromisoformat(item['closed_at']).replace(tzinfo=timezone.utc), self.closed_at)

    def test_all_search_and_keyword_search_include_closed_jobs(self):
        result = self.search(posting_status='all')
        self.assertEqual({item['external_id'] for item in result['items']}, {'open', 'closed', 'expired-closed'})
        result = api.list_jobs(49.0068705, 8.4034195, 15, None, 'expired-closed', 'Karlsruhe', 0, 20,
                               posting_status='closed', session=self.session)
        self.assertEqual([item['external_id'] for item in result['items']], ['expired-closed'])

    def test_closed_state_and_country_search_include_expired_postings(self):
        for scope in ({'state': 'Baden-Württemberg'}, {'country': 'DE'}):
            with self.subTest(scope=scope):
                result = self.search(posting_status='closed', **scope)
                self.assertIn('expired-closed', {item['external_id'] for item in result['items']})

    def test_snapshot_cannot_be_reused_for_a_different_posting_status(self):
        opened = self.search()
        closed = self.search(posting_status='closed')
        self.assertNotEqual(opened['snapshot_id'], closed['snapshot_id'])
        with self.assertRaises(HTTPException) as error:
            self.search(posting_status='closed', snapshot_id=opened['snapshot_id'])
        self.assertEqual(error.exception.status_code, 409)
        again = self.search()
        self.assertEqual([item['external_id'] for item in again['items']], ['open'])


if __name__ == '__main__':
    unittest.main()
