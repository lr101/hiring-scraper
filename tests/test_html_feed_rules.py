"""Career-page rules both feed workers apply when they save postings."""
import unittest
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import discovery_worker, worker
from hiring_scraper.app.models import Base, Company, Job, JobFeed, ScanRun, utcnow
from tests import pg


class HtmlFeedRulesTests(unittest.TestCase):
    def setUp(self):
        self.engine = pg.engine(self)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.now = utcnow()
        with self.factory.begin() as session:
            company = Company(source='openstreetmap', source_id='node/1', name='Gramm Technik', domain='gramm.example',
                              latitude=48.1, longitude=11.5, location_precision='point', country_code='DE',
                              category='company', state='Bayern')
            feed = JobFeed(company=company, provider='html_jobs', status='incomplete',
                           feed_url='https://gramm.example/karriere/', enabled=True)
            session.add(feed)
            session.flush()
            session.add_all([
                Job(feed=feed, external_id='nav', title='Jobs Studium in Bremen',
                    url='https://gramm.example/jobs/studium', last_seen_at=self.now),
                Job(feed=feed, external_id='real', title='Servicemonteur (m/w/d)',
                    url='https://gramm.example/karriere/#servicemonteur', last_seen_at=self.now),
            ])
            self.feed_id, self.company_id = feed.id, company.id

    def scan(self, path, jobs, *, complete):
        if path == 'discovery':
            with self.factory.begin() as session:
                discovery_worker._upsert_jobs(session, session.get(JobFeed, self.feed_id), jobs, complete, self.now)
            return
        with self.factory.begin() as session:
            run = ScanRun(feed_id=self.feed_id, status='running')
            session.add(run)
            session.flush()
            run_id = run.id
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=self.now):
            worker._persist_result(self.feed_id, run_id, {
                'state': 'ok', 'status': 200, 'parsed_feed': {'jobs': jobs, 'complete': complete}}, b'')

    def jobs(self):
        with self.factory() as session:
            return {job.external_id: job for job in session.query(Job).all()}

    def test_partial_scans_close_nothing(self):
        for path in ('feed', 'discovery'):
            with self.subTest(path=path):
                self.scan(path, [{'id': f'new-{path}', 'title': 'Detektiv (m/w/d)',
                                  'url': 'https://gramm.example/jobs/studium'}], complete=False)
                jobs = self.jobs()
                self.assertTrue(jobs['nav'].is_active)
                self.assertTrue(jobs['real'].is_active)

    def test_unplaced_postings_take_a_single_site_employers_address(self):
        postings = [
            {'id': 'new', 'title': 'Laminierer (m/w/d)', 'url': 'https://gramm.example/karriere/#laminierer'},
            {'id': 'placed', 'title': 'Monteur (m/w/d)', 'url': 'https://gramm.example/karriere/#monteur',
             'location': 'Erding', 'locations': [{'label': 'Erding'}]},
        ]
        for path in ('feed', 'discovery'):
            with self.subTest(path=path):
                self.scan(path, postings, complete=True)
                with self.factory() as session:
                    new = session.query(Job).filter_by(external_id='new').one()
                    placed = session.query(Job).filter_by(external_id='placed').one()
                    self.assertEqual([(item.label, item.latitude, item.precision) for item in new.locations],
                                     [('Company address, Bayern', 48.1, 'company_address')])
                    self.assertEqual(new.raw_metadata.get('location_source'), 'company_address')
                    self.assertEqual([item.label for item in placed.locations], ['Erding'])

    def test_a_group_sharing_one_careers_domain_gets_no_address(self):
        with self.factory.begin() as session:
            session.add(Company(source='openstreetmap', source_id='node/2', name='Gramm Technik Werk Nord',
                                domain='gramm.example', latitude=53.5, longitude=10.0, location_precision='point'))
        self.scan('feed', [{'id': 'new', 'title': 'Laminierer (m/w/d)',
                            'url': 'https://gramm.example/karriere/#laminierer'}], complete=True)
        with self.factory() as session:
            self.assertEqual(session.query(Job).filter_by(external_id='new').one().locations, [])


if __name__ == '__main__':
    unittest.main()
