import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import discovery_worker, worker
from hiring_scraper.app.api import get_company
from hiring_scraper.app.models import Base, Company, DiscoveryRun, Job, JobFeed, utcnow
from hiring_scraper.http import Client


HOME = 'https://example.org/'
BOARD = 'https://example.recruitee.com'
FEED = BOARD + '/api/feeds/offers.xml'
XML = b'''<offers><offer><id>2732449</id><slug>engineer</slug><title>System Engineer</title>
<description>&lt;h3&gt;Nice to have&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;Cloud experience&lt;/li&gt;&lt;/ul&gt;</description>
<city>Karlsruhe</city><country_code>DE</country_code><hybrid>true</hybrid>
<careers_url>https://example.org/o/engineer</careers_url><apply_url>https://example.org/o/engineer/c/new</apply_url>
</offer><offer><id>2732440</id><slug>sales-representative</slug><title>Sales Representative</title>
<careers_url>https://example.recruitee.com/o/sales-representative</careers_url>
<apply_url>https://example.recruitee.com/o/sales-representative/c/new</apply_url></offer>
<offer><id>2513967</id><slug>account-manager</slug><title>Account Manager</title>
<careers_url>https://example.recruitee.com/o/account-manager</careers_url>
<apply_url>https://example.recruitee.com/o/account-manager/c/new</apply_url></offer></offers>'''


def capture(folder, url, body, content_type='text/html'):
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    (folder / (key + '.body')).write_bytes(body)
    (folder / (key + '.json')).write_text(json.dumps({'url': url, 'state': 'ok', 'status': 200,
        'content_type': content_type, 'sha256': hashlib.sha256(body).hexdigest(), 'capture': key}))


class RecruiteeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.cache = self.folder / 'cache'
        self.cache.mkdir()
        capture(self.cache, HOME + 'robots.txt', b'User-agent: *\nAllow: /', 'text/plain')
        capture(self.cache, HOME, ('<a href="' + BOARD + '/">Careers</a>').encode())
        capture(self.cache, FEED, XML, 'application/xml')
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.patches = [patch.object(discovery_worker, 'SessionLocal', self.factory),
                        patch.object(discovery_worker, 'engine', self.engine),
                        patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'engine', self.engine),
                        patch.object(worker, '_pace_origin')]
        for item in self.patches:
            item.start()
        with self.factory.begin() as session:
            company = Company(source='test', source_id='1', name='Example GmbH', website_url=HOME,
                              career_status='provider_detected', career_url=BOARD + '/',
                              next_discovery_at=utcnow() - timedelta(minutes=1))
            session.add(company)
            session.flush()
            self.company_id = company.id

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def client(self, name):
        return Client(self.folder / name, cache_from=self.cache, offline_only=True, max_requests=3)

    def rediscover(self):
        claim = discovery_worker._claim_due_company()
        self.assertIsNotNone(claim)
        with patch.object(discovery_worker, 'Client', return_value=self.client('discovery')):
            self.assertTrue(discovery_worker._process_claimed_company((*claim, None, None)))

    def test_normal_rediscovery_revisits_detection_only_company_and_preserves_provenance(self):
        self.rediscover()
        with self.factory() as session:
            company = session.get(Company, self.company_id)
            self.assertEqual(company.career_status, 'jobs_feed_found')
            feeds = session.scalars(select(JobFeed).where(JobFeed.company_id == self.company_id)).all()
            self.assertEqual(len(feeds), 1)
            supported = next(row for row in feeds if row.feed_url == FEED)
            self.assertEqual((supported.company_id, supported.tenant, supported.status, supported.job_count),
                             (self.company_id, 'example', 'parsed', 3))
            jobs = session.scalars(select(Job)).all()
            self.assertEqual(len(jobs), 3)
            job = next(row for row in jobs if row.external_id == '2732449')
            self.assertEqual((job.feed_id, job.external_id, job.url), (supported.id, '2732449', BOARD + '/o/engineer'))
            self.assertEqual(job.raw_metadata['careers_url'], 'https://example.org/o/engineer')
            self.assertEqual(job.locations[0].label, 'Karlsruhe')
            self.assertEqual(job.locations[0].country_code.lower(), 'de')
            evidence = session.scalars(select(DiscoveryRun)).one().evidence['boards'][0]
            self.assertEqual(evidence['discovered_on'], HOME)
            self.assertEqual(evidence['evidence_url'], BOARD + '/')
            self.assertEqual(evidence['feed_url'], FEED)

    def test_rescan_ingests_supported_feed_and_schema_error_preserves_missing_job_counters(self):
        self.rediscover()
        with self.factory.begin() as session:
            supported = session.scalars(select(JobFeed).where(JobFeed.feed_url == FEED)).one()
            self.feed_id = supported.id
            supported.next_scan_at = utcnow() - timedelta(minutes=1)
            missing = Job(feed=supported, external_id='missing', title='Existing job', url=BOARD + '/o/missing',
                          last_seen_at=utcnow() - timedelta(days=10), missing_complete_scans=0, is_active=True)
            session.add(missing)
            session.flush()
            missing_id = missing.id
        with patch.object(worker, 'Client', return_value=self.client('rescan')):
            self.assertTrue(worker.scan_once())
        with self.factory.begin() as session:
            supported = session.get(JobFeed, self.feed_id)
            self.assertEqual((supported.status, supported.job_count), ('parsed', 3))
            supported.next_scan_at = utcnow() - timedelta(minutes=1)
            self.assertEqual(session.get(Job, missing_id).missing_complete_scans, 1)
        capture(self.cache, FEED, b'<offers total="2"><offer/></offers>', 'application/xml')
        with patch.object(worker, 'Client', return_value=self.client('malformed')):
            self.assertTrue(worker.scan_once())
        with self.factory() as session:
            self.assertEqual(session.get(JobFeed, self.feed_id).status, 'schema_error')
            missing = session.get(Job, missing_id)
            self.assertEqual(missing.missing_complete_scans, 1)
            self.assertTrue(missing.is_active)
            self.assertIsNone(missing.closed_at)

    def legacy_html_jobs(self, active=True):
        with self.factory.begin() as session:
            legacy = JobFeed(company_id=self.company_id, provider='html_jobs', board_url=HOME + 'jobs/',
                             feed_url=HOME + 'jobs/', status='parsed', job_count=7)
            legacy.jobs = [Job(external_id='html-' + str(i), title='Existing role ' + str(i),
                               url=HOME + 'jobs/role-' + str(i), is_active=active,
                               missing_complete_scans=1) for i in range(7)]
            session.add(legacy)
            session.flush()
            return legacy.id

    def test_new_recruitee_ingestion_is_deferred_when_active_html_jobs_already_exist(self):
        legacy_id = self.legacy_html_jobs()
        self.rediscover()
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(JobFeed)).all()), 1)
            self.assertEqual(len(session.scalars(select(Job)).all()), 7)
            self.assertEqual(get_company(self.company_id, session)['active_job_count'], 7)
            self.assertEqual(session.get(JobFeed, legacy_id).job_count, 7)
            self.assertTrue(all(job.is_active and job.missing_complete_scans == 1 for job in session.scalars(select(Job))))
            self.assertEqual(session.scalars(select(DiscoveryRun)).one().jobs_found, 0)

    def test_inactive_html_history_does_not_block_new_supported_feed(self):
        self.legacy_html_jobs(active=False)
        self.rediscover()
        with self.factory() as session:
            self.assertEqual(session.scalars(select(JobFeed).where(JobFeed.feed_url == FEED)).one().job_count, 3)
            self.assertEqual(get_company(self.company_id, session)['active_job_count'], 3)

    def test_existing_supported_recruitee_feed_refreshes_with_active_html_history(self):
        self.legacy_html_jobs()
        with self.factory.begin() as session:
            session.add(JobFeed(company_id=self.company_id, provider='recruitee', tenant='example', board_url=BOARD,
                                feed_url=FEED, status='parsed', job_count=0))
        self.rediscover()
        with self.factory() as session:
            self.assertEqual(session.scalars(select(JobFeed).where(JobFeed.feed_url == FEED)).one().job_count, 3)
            self.assertEqual(len(session.scalars(select(JobFeed)).all()), 2)
            self.assertEqual(get_company(self.company_id, session)['active_job_count'], 10)
