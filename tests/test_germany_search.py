"""Germany scope follows employer membership independently of job location."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from hiring_scraper.app import api, models
from hiring_scraper.app.discovery_jobs import company_ids_for_search

class GermanySearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
    def tearDown(self):
        self.engine.dispose()
    def test_germany_resolves_without_network_and_starts_country_campaign(self):
        with self.factory() as session, patch.object(api, '_nominatim_results', side_effect=AssertionError('network')):
            resolved = api.resolve_location(api.LocationQuery(query='Deutschland'), session)
            self.assertEqual(resolved['results'][0]['search_scope'], 'country')
            saved = api.create_configured_location(SimpleNamespace(search_scope='country', country='DE'), session)
            self.assertEqual(saved['label'], 'Germany')
            self.assertEqual(saved['first_discovery_job']['search_scope'], 'country')
            self.assertIsNone(saved['next_run_at'])
            rerun = api.create_discovery_job(SimpleNamespace(location_id=saved['id']), session)
            self.assertEqual(rerun['search_scope'], 'country')
            repeated = api.create_configured_location(SimpleNamespace(search_scope='country', country='DE'), session)
            self.assertEqual(repeated['id'], saved['id'])
            self.assertTrue(repeated['already_saved'])
            self.assertEqual(repeated['first_discovery_job']['id'], saved['first_discovery_job']['id'])
            coalesced = api.create_discovery_job(SimpleNamespace(search_scope='country', country='DE'), session)
            self.assertEqual(coalesced['id'], saved['first_discovery_job']['id'])
            active_country_jobs = session.scalars(select(models.DiscoveryJob).where(
                models.DiscoveryJob.search_scope == 'country',
                models.DiscoveryJob.stage == 'company_homepage_discovery',
                models.DiscoveryJob.status.in_(('scheduled','queued','running')))).all()
            self.assertEqual(len(active_country_jobs), 1)
    def test_saving_germany_reuses_an_active_inventory_after_delete_or_manual_start(self):
        with self.factory() as session:
            manual = api.create_discovery_job(SimpleNamespace(search_scope='country', country='DE'), session)
            first_location = api.create_configured_location(
                SimpleNamespace(search_scope='country', country='DE'), session)
            self.assertEqual(first_location['first_discovery_job']['id'], manual['id'])
            self.assertEqual(first_location['first_discovery_job']['configured_location_id'], first_location['id'])

            api.delete_configured_location(first_location['id'], session)
            recreated = api.create_configured_location(
                SimpleNamespace(search_scope='country', country='DE'), session)
            self.assertFalse(recreated.get('already_saved', False))
            self.assertEqual(recreated['first_discovery_job']['id'], manual['id'])
            self.assertEqual(recreated['first_discovery_job']['configured_location_id'], recreated['id'])
            active = session.scalars(select(models.DiscoveryJob).where(
                models.DiscoveryJob.search_scope == 'country',
                models.DiscoveryJob.stage == 'company_homepage_discovery',
                models.DiscoveryJob.status.in_(('scheduled', 'queued', 'running')))).all()
            self.assertEqual([job.id for job in active], [manual['id']])
    def test_country_rejects_non_german_input(self):
        with self.factory() as session:
            for invoke in (lambda: api.create_configured_location(SimpleNamespace(search_scope='country', country='FR'), session),
                           lambda: api.list_companies(session=session, country='FR'),
                           lambda: api.resolve_location(SimpleNamespace(search_scope='country', query='France'), session)):
                with self.assertRaises(HTTPException) as error:
                    invoke()
                self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(session.scalars(select(models.ConfiguredLocation)).all(), [])
    def test_country_membership_includes_legacy_state_and_all_active_work_locations(self):
        with self.factory.begin() as session:
            for source_id, state in [('bw','Baden-Württemberg'), ('be','Berlin'), ('outside','Alsace')]:
                company = models.Company(source='test', source_id=source_id, name=source_id, state=state,
                                         website_url='https://' + source_id + '.example', domain=source_id+'.example')
                feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url=company.website_url+'/jobs')
                session.add_all([models.Job(feed=feed, external_id='local', title='Engineer', url=company.website_url+'/local', location_text='Paris'),
                                 models.Job(feed=feed, external_id='remote', title='Remote', url=company.website_url+'/remote', is_remote=True, raw_metadata={'remote_country_codes':['US']}),
                                 models.Job(feed=feed, external_id='expired', title='Expired', url=company.website_url+'/expired', raw_metadata={'validThrough':'2000-01-01'})])
        with self.factory() as session:
            companies = api.list_companies(session=session, query=None, offset=0, limit=100, country='DE')
            self.assertEqual({row['name'] for row in companies['items']}, {'bw','be'})
            ids = company_ids_for_search(session, SimpleNamespace(search_scope='country'))
            self.assertEqual(set(ids), {row['id'] for row in companies['items']})
            jobs = api.list_jobs(session=session, company_id=None, query=None, offset=0, limit=100, country='DE', country_code='DE')
            self.assertEqual(jobs['total'], 4)
            self.assertEqual(jobs['counts']['filtered']['expired'], 2)
            self.assertEqual(api.summary(session=session, country='DE')['jobs_for_location'], 4)
            self.assertEqual(api.summary(session=session, country='DE')['remote_jobs_in_result'], 2)

    def test_shared_domain_page_dedup_keeps_state_membership_on_each_office(self):
        with self.factory.begin() as session:
            hamburg= models.Company(source='test',source_id='hamburg',name='Employer Hamburg',
                state='Hamburg',country_code='DE',website_url='https://www.shared-de.example/',
                domain='www.shared-de.example')
            bw=models.Company(source='test',source_id='baden',name='Employer Baden',
                state='Baden-Württemberg',country_code='DE',website_url='https://shared-de.example/jobs',
                domain='shared-de.example')
            session.add_all([hamburg,bw]);session.flush()
            hamburg_id,bw_id=hamburg.id,bw.id
        with self.factory() as session:
            country_ids=company_ids_for_search(session,SimpleNamespace(search_scope='country'))
            state_ids=company_ids_for_search(session,SimpleNamespace(search_scope='state',state='Baden-Württemberg'))
            self.assertEqual(country_ids,[hamburg_id],
                             'Germany page work is shared by website domain')
            self.assertEqual(state_ids,[bw_id],
                             'State campaigns retain their own office membership')
    def test_explicit_country_without_state_and_rank_information_are_exposed(self):
        with self.factory.begin() as session:
            company = models.Company(source='test', source_id='explicit', name='German employer', country_code='DE',
                                     employee_count=500, crawl_interval_days=1, crawl_reason='Large employer', crawl_priority=100,
                                     website_url='https://de.example', career_status='not_checked')
            session.add(company)
        with self.factory() as session:
            page = api.list_companies(session=session, query=None, offset=0, limit=100, country='DE')
            self.assertEqual(page['total'], 1)
            item = page['items'][0]
            self.assertEqual(item['employee_count'], 500)
            self.assertEqual(item['crawl_interval_days'], 1)
            self.assertEqual(item['crawl_reason'], 'Large employer')
            self.assertFalse(item['crawl_paused'])
            self.assertIsNone(item['next_check_at'])
    def test_country_profile_details_match_directory_geography_and_expiry(self):
        from hiring_scraper.app.profiles import create_profile, ProfileRequest
        with self.factory.begin() as session:
            company = models.Company(source='test', source_id='profile', name='German employer', country_code='DE')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://de.example/jobs')
            session.add(models.Job(feed=feed, external_id='role', title='Engineer', location_text='Paris', url='https://de.example/role'))
        with self.factory() as session:
            profile = create_profile(ProfileRequest(name='Profile', search_area={'label':'Karlsruhe','latitude':49,'longitude':8,'radius_km':15,'country_code':'DE'}), session)
            item = api.list_jobs(session=session, company_id=None, query=None, offset=0, limit=100, country='DE', profile_id=profile['id'], min_match_score=0, include_unknown=True)['items'][0]
            detail = api.get_job(item['id'], session, profile_id=profile['id'], country='DE')
            self.assertEqual(detail['geography'], item['geography'])
            self.assertIn('profile_match', detail)
            self.assertNotIn('profile_match', item)
    def test_country_job_detail_exposes_scope_without_a_profile(self):
        with self.factory.begin() as session:
            company = models.Company(source='test', source_id='detail', name='Employer', country_code='DE')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://de.example/jobs')
            job = models.Job(feed=feed, external_id='role', title='Engineer', url='https://de.example/role')
            session.add(job)
        with self.factory() as session:
            detail = api.get_job(job.id, session, country='DE')
            self.assertEqual(detail['geography']['match_kind'], 'employer_in_country')
    def test_next_check_uses_earliest_persisted_schedule_and_pause_hides_it(self):
        from datetime import datetime, timezone
        career_due = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
        feed_due = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
        with self.factory.begin() as session:
            company = models.Company(source='test', source_id='schedule', name='Employer', country_code='DE', next_discovery_at=career_due)
            session.add(models.JobFeed(company=company, provider='html_jobs', status='parsed', feed_url='https://de.example/jobs', next_scan_at=feed_due))
        with self.factory.begin() as session:
            company = session.scalar(select(models.Company))
            self.assertEqual(api.get_company(company.id, session)['next_check_at'], '2026-10-14T08:00:00')
            company.crawl_paused = True
            self.assertIsNone(api.get_company(company.id, session)['next_check_at'])
