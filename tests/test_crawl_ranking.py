"""Ranking must follow durable employer evidence, not fetch freshness or errors."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from hiring_scraper.app.models import Base, Company, Job, JobFeed, ScanRun
from hiring_scraper.app import worker

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)

class CrawlRankingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.factory()
        self.company = Company(source='test', source_id='1', name='Example')
        self.session.add(self.company)
        self.session.flush()
        self.feed = JobFeed(company_id=self.company.id, provider='schema_org', feed_url='https://example.org/jobs', status='parsed')
        self.session.add(self.feed)
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def rank(self, result=None):
        from hiring_scraper.app.crawl_ranking import refresh_company_rank
        refresh_company_rank(self.session, self.company, NOW, result)
        return self.company.crawl_interval_days

    def job(self, posted=None, first_seen=None, raw=None, active=True):
        self.session.add(Job(feed_id=self.feed.id, external_id=str(len(self.session.new)), title='Engineer', url='https://example.org/job', date_posted=posted, first_seen_at=first_seen or NOW, last_seen_at=NOW, raw_metadata=raw or {}, is_active=active))
        self.session.flush()

    def test_employee_tag_parser_requires_unambiguous_counts(self):
        from hiring_scraper.app.crawl_ranking import parse_employee_count
        for value, expected in [('250',250), ('250-499',250), ('1.200',1200), ('many',None), ('12.5',None), ('0',None)]:
            with self.subTest(value=value):
                self.assertEqual(parse_employee_count(value), expected)

    def test_large_employer_daily_even_without_vacancies(self):
        self.company.employee_count = 250
        self.assertEqual(self.rank(), 1)
        self.assertFalse(self.company.crawl_paused)

    def test_recent_posting_daily_but_old_posting_is_not_refreshed_by_observation(self):
        self.job('2026-10-01')
        self.assertEqual(self.rank(), 1)
        self.session.query(Job).delete()
        self.job('2025-01-01')
        self.assertEqual(self.rank(), 3)

    def test_expired_posting_cannot_raise_priority(self):
        self.job('2026-10-01', raw={'validThrough':'2026-10-10'})
        self.assertEqual(self.rank(), 7)

    def test_invalid_posting_date_does_not_establish_recency(self):
        self.job('not a date')
        self.assertEqual(self.rank(), 3)

    def test_unknown_and_failed_checks_never_pause_or_slow_to_monthly(self):
        for _ in range(5):
            self.assertEqual(self.rank({'status':'unresolved', 'pages':[{'fetch_state':'http_error'}]}), 7)
        self.assertFalse(self.company.crawl_paused)
        self.assertEqual(self.company.irrelevant_checks, 0)

    def test_repeated_complete_empty_checks_become_monthly_without_pausing(self):
        empty = {'status':'unresolved', 'complete':True, 'pages':[{'fetch_state':'ok'}]}
        self.assertEqual(self.rank(empty), 7)
        self.assertEqual(self.rank(empty), 30)
        self.assertFalse(self.company.crawl_paused)

    def test_only_proved_nonemployer_pauses_and_new_size_evidence_reactivates(self):
        proof = {'irrelevant':True, 'complete':True, 'pages':[{'fetch_state':'ok'}]}
        self.rank(proof)
        self.rank(proof)
        self.assertTrue(self.company.crawl_paused)
        self.rank({'company_size':{'employee_count':600, 'source':'employer_website', 'source_url':'https://example.org/about'}})
        self.assertFalse(self.company.crawl_paused)
        self.assertEqual(self.company.crawl_interval_days, 1)

    def test_daily_schedule_staggers_companies_and_feeds_within_one_day(self):
        from hiring_scraper.app.crawl_ranking import next_company_due, next_feed_due
        self.company.employee_count = 300
        self.rank()
        company_due = next_company_due(self.company, NOW)
        feed_due = next_feed_due(self.company, self.feed, NOW)
        self.assertGreater(company_due, NOW + timedelta(hours=19))
        self.assertLessEqual(company_due, NOW + timedelta(days=1))
        self.assertNotEqual(company_due, feed_due)
        self.assertEqual(company_due, next_company_due(self.company, NOW))
        self.company.crawl_paused = True
        self.assertIsNone(next_company_due(self.company, NOW))
        self.assertIsNone(next_feed_due(self.company, self.feed, NOW))

    def test_claim_skips_paused_and_old_due_work_precedes_priority(self):
        self.company.crawl_paused = True
        self.feed.next_scan_at = NOW - timedelta(days=10)
        active = Company(source='test', source_id='2', name='Small', crawl_priority=1)
        large = Company(source='test', source_id='3', name='Large', crawl_priority=100)
        self.session.add_all([active, large]); self.session.flush()
        old = JobFeed(company_id=active.id, provider='html', feed_url='https://small/jobs', status='parsed', next_scan_at=NOW-timedelta(days=2))
        recent = JobFeed(company_id=large.id, provider='html', feed_url='https://large/jobs', status='parsed', next_scan_at=NOW-timedelta(hours=1))
        self.session.add_all([old, recent]); self.session.commit()
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'engine', self.engine), patch.object(worker, 'utcnow', return_value=NOW):
            self.assertEqual(worker._claim_due_feed()[0], old.id)

    def test_conditional_success_recomputes_stale_jobs_and_schedules_adaptively(self):
        self.job('2025-01-01')
        run = ScanRun(feed_id=self.feed.id, status='running', started_at=NOW)
        self.session.add(run); self.session.commit()
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=NOW):
            worker._persist_result(self.feed.id, run.id, {'state':'not_modified','status':304}, b'')
        self.session.expire_all()
        self.assertEqual(self.company.crawl_interval_days, 3)
        self.assertGreater(self.feed.next_scan_at.replace(tzinfo=timezone.utc), NOW+timedelta(days=2))

    def test_complete_feed_success_ranks_new_persisted_jobs(self):
        run = ScanRun(feed_id=self.feed.id, status='running', started_at=NOW)
        self.session.add(run); self.session.commit()
        result = {'state':'ok','status':200,'parsed_feed':{'complete':True,'jobs':[{'id':'fresh','title':'Engineer','url':'https://example.org/jobs/1','date_posted':'2026-10-11'}]}}
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=NOW):
            worker._persist_result(self.feed.id, run.id, result, b'')
        self.session.expire_all()
        self.assertEqual(self.company.crawl_interval_days, 1)
        self.assertLessEqual(self.feed.next_scan_at.replace(tzinfo=timezone.utc), NOW+timedelta(days=1))

    def test_employee_evidence_from_employer_page_sets_size(self):
        from hiring_scraper.discovery import discover
        from tests.test_discovery import FixtureClient
        home='https://example.org/'
        result=discover({'name':'Example','website':home}, FixtureClient({home:'<p>Wir beschäftigen 1.200 Mitarbeitende.</p>'}), max_pages=1)
        self.assertEqual(result.get('company_size',{}).get('employee_count'), 1200)
        self.assertEqual(result['company_size']['source_url'],home)

    def test_feed_refresh_does_not_postpone_due_career_discovery(self):
        self.company.next_discovery_at = NOW-timedelta(days=1)
        run = ScanRun(feed_id=self.feed.id, status='running', started_at=NOW)
        self.session.add(run); self.session.commit()
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=NOW):
            worker._persist_result(self.feed.id, run.id, {'state':'not_modified','status':304}, b'')
        self.session.expire_all()
        self.assertLess(self.company.next_discovery_at.replace(tzinfo=timezone.utc), NOW)

    def test_partial_feeds_do_not_establish_irrelevance(self):
        run = ScanRun(feed_id=self.feed.id, status='running', started_at=NOW)
        self.session.add(run); self.session.commit()
        result={'state':'ok','status':200,'parsed_feed':{'complete':False,'jobs':[]}}
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=NOW):
            worker._persist_result(self.feed.id, run.id, result, b'')
        self.session.expire_all()
        self.assertEqual(self.company.irrelevant_checks, 0)
        self.assertEqual(self.company.crawl_interval_days, 7)
        self.assertLess(self.feed.next_scan_at.replace(tzinfo=timezone.utc), NOW+timedelta(hours=2))

    def test_external_page_workforce_is_not_attributed_to_employer(self):
        from hiring_scraper.discovery import discover
        from tests.test_discovery import FixtureClient
        home='https://example.org/'
        result=discover({'name':'Example','website':home}, FixtureClient({home:'<a href="https://unrelated.org/careers">Careers</a>', 'https://unrelated.org/careers':'<p>We have 900 employees</p>'}), max_pages=2)
        self.assertNotIn('company_size', result)

    def test_customer_workforce_count_is_not_employer_size(self):
        from hiring_scraper.discovery import discover
        from tests.test_discovery import FixtureClient
        home='https://example.org/'
        result=discover({'name':'Example','website':home}, FixtureClient({home:'<p>Unsere Kunden beschäftigen 8.000 Mitarbeiter.</p>'}), max_pages=1)
        self.assertNotIn('company_size', result)

    def test_unchanged_previously_complete_empty_feed_becomes_monthly(self):
        self.feed.status='complete_empty'
        self.feed.job_count=0
        self.rank({'complete':True, 'status':'complete_empty'})
        run=ScanRun(feed_id=self.feed.id, status='running', started_at=NOW)
        self.session.add(run); self.session.commit()
        with patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'utcnow', return_value=NOW):
            worker._persist_result(self.feed.id, run.id, {'state':'not_modified','status':304}, b'')
        self.session.expire_all()
        self.assertEqual(self.company.crawl_interval_days, 30)
        self.assertFalse(self.company.crawl_paused)

    def test_two_successful_parked_domain_discoveries_pause_source(self):
        from hiring_scraper.discovery import discover
        from tests.test_discovery import FixtureClient
        home='https://example.org/'
        for _ in range(2):
            result=discover({'name':'Example','website':home}, FixtureClient({home:'<h1>This domain is for sale</h1><p>Buy this domain</p>'}), max_pages=6)
            self.assertTrue(result.get('irrelevant'))
            self.assertTrue(result.get('complete'))
            self.rank(result)
        self.assertTrue(self.company.crawl_paused)

    def test_parked_wording_with_career_evidence_cannot_pause_employer(self):
        from hiring_scraper.discovery import discover
        from tests.test_discovery import FixtureClient
        home='https://example.org/'
        result=discover({'name':'Example','website':home}, FixtureClient({home:'<p>Domain for sale</p><a href="/careers">Careers</a>'}), max_pages=1)
        self.assertFalse(result.get('irrelevant', False))

    def test_under_construction_is_not_proved_irrelevance(self):
        from hiring_scraper.discovery import discover
        from tests.test_discovery import FixtureClient
        home='https://example.org/'
        result=discover({'name':'Example','website':home}, FixtureClient({home:'<h1>Under construction</h1>'}), max_pages=1)
        self.assertFalse(result.get('irrelevant', False))
