"""The extension catalog is stable, bounded, and independent of search location."""
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from hiring_scraper.app import models


class CompanionCatalogTests(unittest.TestCase):
    def test_keyset_catalog_includes_local_and_remote_jobs_without_cv_or_descriptions(self):
        from hiring_scraper.app.companion import list_companion_jobs
        engine = create_engine('sqlite://')
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company = models.Company(source='test', source_id='companion', name='Employer')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.test/jobs', status='parsed')
            jobs = [models.Job(feed=feed, external_id=str(i), title=f'Role {i}', url=f'https://example.test/jobs/{i}', description='Never included', is_remote=i == 1, is_active=i != 2) for i in range(4)]
            session.add_all(jobs)
            session.commit()
            first = list_companion_jobs(after_id=0, limit=2, session=session)
            self.assertEqual([job['id'] for job in first['items']], [jobs[0].id, jobs[1].id])
            self.assertEqual(first['next_after_id'], jobs[1].id)
            self.assertEqual(set(first['items'][0]), {'id', 'title', 'company_name', 'url', 'is_active'})
            self.assertEqual(first['items'][0]['company_name'], 'Employer')
            second = list_companion_jobs(after_id=first['next_after_id'], limit=2, session=session)
            self.assertEqual([job['id'] for job in second['items']], [jobs[2].id, jobs[3].id])
            self.assertFalse(second['items'][0]['is_active'])
            self.assertIsNone(second['next_after_id'])
        engine.dispose()
