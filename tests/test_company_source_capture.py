"""Browsing capture attaches sources to a company without expanding profile scope."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from hiring_scraper.app import api
from hiring_scraper.app.companion import SaveJobRequest, save_companion_job
from hiring_scraper.app.models import Base, Company, Job, JobApplication, JobFeed, UserProfile


class CompanySourceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, autoflush=False, expire_on_commit=False)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def company(self, domain=None, website_url=None, name='Acme', **extra):
        company = Company(source='test', source_id=str(self.count(Company)), name=name,
                          domain=domain, website_url=website_url, **extra)
        self.session.add(company)
        self.session.commit()
        return company

    def count(self, model):
        return self.session.scalar(select(func.count()).select_from(model))

    def feed(self, url='https://careers.acme.example/jobs', **extra):
        request = api.ManagedFeedCreate(feed_url=url, provider='auto', track_company=True, **extra)
        return api.create_managed_feed(request, self.session)

    def test_careers_subdomain_reuses_company_by_normalized_domain(self):
        company = self.company(domain='WWW.Acme.Example', website_url='https://www.acme.example/')
        result = self.feed(company_name='A different display name')
        self.assertEqual(result['company_id'], company.id)
        self.assertFalse(result['company_created'])
        self.assertEqual(self.count(Company), 1)
        self.assertEqual(company.name, 'Acme')
        self.assertIsNotNone(company.next_discovery_at)
        self.assertIsNotNone(self.session.get(JobFeed, result['id']).next_scan_at)

    def test_legacy_website_without_domain_is_reused(self):
        company = self.company(website_url='https://WWW.Acme.Example/about')
        result = self.feed()
        self.assertEqual(result['company_id'], company.id)
        self.assertEqual(self.count(Company), 1)
        self.assertEqual(company.domain, 'acme.example')

    def test_new_company_is_tracked_and_repeat_capture_is_idempotent(self):
        first = self.feed(company_name='Acme')
        company = self.session.get(Company, first['company_id'])
        self.assertTrue(first['company_created'])
        self.assertEqual(company.domain, 'acme.example')
        self.assertFalse(company.crawl_paused)
        self.assertIsNotNone(company.next_discovery_at)
        repeated = self.feed(company_name='Acme renamed')
        self.assertTrue(repeated['already_tracked'])
        another = self.feed('https://www.acme.example/careers', company_name='Another name')
        self.assertEqual(another['company_id'], company.id)
        self.assertEqual(self.count(Company), 1)
        self.assertEqual(self.count(JobFeed), 2)

    def test_same_name_on_different_domains_does_not_merge_companies(self):
        first = self.feed('https://acme.example/jobs', company_name='Acme')
        second = self.feed('https://other-acme.example/jobs', company_name='Acme')
        self.assertNotEqual(first['company_id'], second['company_id'])

    def test_hosted_ats_uses_explicit_employer_domain_and_auto_detects_feed(self):
        company = self.company(domain='acme.example', website_url='https://acme.example/')
        result = self.feed('https://jobs.lever.co/acme', website_url='https://www.acme.example/')
        self.assertEqual(result['company_id'], company.id)
        self.assertEqual(result['provider'], 'lever')
        self.assertEqual(company.domain, 'acme.example')
        self.assertEqual(self.count(Company), 1)

    def test_shared_ats_host_keeps_tenants_separate_without_employer_website(self):
        first = self.feed('https://jobs.lever.co/acme')
        second = self.feed('https://jobs.lever.co/another')
        repeated = self.feed('https://jobs.lever.co/acme')
        self.assertNotEqual(first['company_id'], second['company_id'])
        self.assertEqual(first['company_id'], repeated['company_id'])
        for result in (first, second):
            company = self.session.get(Company, result['company_id'])
            self.assertIsNone(company.domain)
            self.assertIsNone(company.website_url)

    def test_tracking_preserves_manually_chosen_career_url(self):
        company = self.company(domain='acme.example', career_url='https://acme.example/chosen',
                               career_url_manual=True)
        self.feed()
        self.assertEqual(company.career_url, 'https://acme.example/chosen')

    def test_hosted_company_can_be_enriched_with_its_website_later(self):
        first = self.feed('https://jobs.lever.co/acme')
        second = self.feed('https://jobs.lever.co/acme', website_url='https://acme.example/')
        self.assertEqual(first['company_id'], second['company_id'])
        self.assertEqual(self.count(Company), 1)
        company = self.session.get(Company, first['company_id'])
        self.assertEqual(company.domain, 'acme.example')
        self.assertIsNotNone(company.next_discovery_at)

    def test_single_job_reuses_company_and_only_links_chosen_profile(self):
        company = self.company(domain='acme.example')
        profiles = [UserProfile(name='Developer'), UserProfile(name='Designer')]
        self.session.add_all(profiles)
        self.session.commit()
        request = SaveJobRequest(profile_id=profiles[1].id,
                                 url='https://careers.acme.example/jobs/42', title='Engineer')
        result = save_companion_job(request, self.session)
        self.assertEqual(self.count(Company), 1)
        feed = self.session.scalar(select(JobFeed))
        self.assertEqual(feed.company_id, company.id)
        self.assertFalse(feed.enabled)
        self.assertEqual(api.list_feeds(self.session)['total'], 0)
        application = self.session.scalar(select(JobApplication))
        self.assertEqual(application.profile_id, profiles[1].id)
        self.assertEqual(application.status, 'new')
        application.status = 'interview'
        self.session.commit()
        repeated = save_companion_job(request, self.session)
        self.assertTrue(repeated['already_added'])
        self.assertEqual(repeated['status'], 'interview')
        self.assertEqual(result['job']['id'], repeated['job']['id'])
        self.assertEqual(self.count(JobApplication), 1)

    def test_single_ats_job_can_use_employer_website(self):
        company = self.company(domain='acme.example')
        profile = UserProfile(name='Developer')
        self.session.add(profile)
        self.session.commit()
        save_companion_job(SaveJobRequest(profile_id=profile.id,
            url='https://jobs.lever.co/acme/42', title='Engineer',
            website_url='https://acme.example/'), self.session)
        self.assertEqual(self.session.scalar(select(JobFeed)).company_id, company.id)
        self.assertEqual(self.count(Company), 1)

    def test_company_first_saved_as_single_job_can_later_be_tracked(self):
        profile = UserProfile(name='Developer')
        self.session.add(profile)
        self.session.commit()
        save_companion_job(SaveJobRequest(profile_id=profile.id,
            url='https://acme.example/jobs/42', title='Engineer'), self.session)
        result = self.feed('https://acme.example/jobs')
        self.assertEqual(self.count(Company), 1)
        self.assertFalse(self.session.get(Company, result['company_id']).crawl_paused)
        self.assertEqual(self.count(JobApplication), 1)
        company = self.session.get(Company, result['company_id'])
        company.crawl_paused = True
        self.session.commit()
        self.feed('https://acme.example/jobs')
        self.assertTrue(company.crawl_paused, 'A later deliberate pause must be preserved')

    def test_overlapping_captures_reuse_company_feed_job_and_application(self):
        for kind in ('feed', 'job'):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                engine = create_engine(f'sqlite:///{Path(directory) / "capture.db"}',
                    connect_args={'check_same_thread': False, 'timeout': 10})
                Base.metadata.create_all(engine)
                with Session(engine) as session:
                    profile = UserProfile(name='Developer')
                    session.add(profile)
                    session.commit()
                    profile_id = profile.id
                barrier = Barrier(2)

                def overlap_company_inserts(connection, cursor, statement, parameters, context, executemany):
                    if statement.lower().startswith('insert into companies '):
                        barrier.wait(timeout=5)

                event.listen(engine, 'before_cursor_execute', overlap_company_inserts)

                def capture():
                    with Session(engine, autoflush=False) as session:
                        if kind == 'feed':
                            return api.create_managed_feed(api.ManagedFeedCreate(
                                feed_url='https://acme.example/jobs', track_company=True), session)
                        return save_companion_job(SaveJobRequest(profile_id=profile_id,
                            url='https://acme.example/jobs/42', title='Engineer'), session)

                try:
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        futures = [executor.submit(capture) for _ in range(2)]
                        results = [future.result(timeout=15) for future in futures]
                    with Session(engine) as session:
                        self.assertEqual(session.scalar(select(func.count()).select_from(Company)), 1)
                        self.assertEqual(session.scalar(select(func.count()).select_from(JobFeed)), 1)
                        if kind == 'feed':
                            self.assertEqual(sum(result['already_tracked'] for result in results), 1)
                        else:
                            self.assertEqual(sum(result['created'] for result in results), 1)
                            self.assertEqual(sum(result['already_added'] for result in results), 1)
                            self.assertEqual(session.scalar(select(func.count()).select_from(Job)), 1)
                            self.assertEqual(session.scalar(select(func.count()).select_from(JobApplication)), 1)
                            self.assertEqual(session.scalar(select(JobFeed.job_count)), 1)
                finally:
                    engine.dispose()
