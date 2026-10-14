"""State campaigns and employer-scoped results against a real SQLite store."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from hiring_scraper.app import api, discovery_worker as worker, models
from hiring_scraper.location_discovery import fetch_location_companies


class StateSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'state.db'}")
        models.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.patches = [patch.object(worker, 'SessionLocal', self.factory),
                        patch.object(worker, 'engine', self.engine), patch.object(api, 'IS_SQLITE', True)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def save_state(self):
        with self.factory() as session:
            return api.create_configured_location(SimpleNamespace(
                search_scope='state', state='Baden-Wuerttemberg', interval_days=7), session)

    def test_save_state_starts_a_whole_state_campaign_and_manual_rerun_keeps_scope(self):
        location = self.save_state()
        self.assertEqual(location['search_scope'], 'state')
        self.assertEqual(location['state'], 'Baden-Württemberg')
        self.assertIsNone(location['city'])
        self.assertEqual(location['first_discovery_job']['search_scope'], 'state')
        with self.factory() as session:
            job = api.create_discovery_job(SimpleNamespace(location_id=location['id']), session)
        self.assertEqual(job['state'], 'Baden-Württemberg')
        self.assertEqual(job['search_scope'], 'state')

    def test_invalid_state_is_rejected_without_saving_a_location(self):
        with self.factory() as session:
            with self.assertRaises(HTTPException) as error:
                api.create_configured_location(SimpleNamespace(search_scope='state', state='Atlantis'), session)
            self.assertEqual(error.exception.status_code, 422)
            self.assertIn('Unknown German state', error.exception.detail)
            self.assertEqual(session.scalars(select(models.ConfiguredLocation)).all(), [])

    def test_state_resolve_does_not_turn_the_state_into_a_radius_around_its_center(self):
        with self.factory() as session, patch.object(api, '_nominatim_results', side_effect=AssertionError('Network')):
            result = api.resolve_location(api.LocationQuery(query='Baden-Wuerttemberg'), session)
        self.assertEqual(result['results'][0]['search_scope'], 'state')
        self.assertEqual(result['results'][0]['state'], 'Baden-Württemberg')

    def test_explicit_radius_resolution_keeps_berlin_as_a_city_radius(self):
        city = {'label': 'Berlin, Deutschland', 'city': 'Berlin', 'state': 'Berlin',
                'latitude': 52.52, 'longitude': 13.405, 'precision': 'city_centroid'}
        with self.factory() as session, patch.object(api, '_nominatim_results', return_value=[city]):
            resolved = api.resolve_location(api.LocationQuery(query='Berlin', search_scope='radius'), session)
            location = api.create_configured_location(api.ConfiguredLocationCreate(
                **resolved['results'][0], radius_km=10), session)
        self.assertEqual(location['search_scope'], 'radius')
        self.assertEqual(location['city'], 'Berlin')
        self.assertEqual(location['radius_km'], 10)
        self.assertEqual(location['first_discovery_job']['search_scope'], 'radius')

    def test_state_overpass_fetch_retains_companies_far_apart_and_unlisted_websites(self):
        payload = {'elements': [
            {'type': 'node', 'id': 1, 'lat': 47.66, 'lon': 9.17,
             'tags': {'name': 'Southern company', 'office': 'company', 'website': 'https://south.example/'}},
            {'type': 'node', 'id': 2, 'lat': 49.77, 'lon': 9.5,
             'tags': {'name': 'Northern craft', 'craft': 'carpenter', 'addr:city': 'Bad Mergentheim'}},
        ]}
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, limit): return json.dumps(self.data).encode()
        def opener(request, **kwargs):
            from urllib.parse import parse_qs
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements': [{'bounds': {'minlat': 47.5, 'minlon': 8, 'maxlat': 50, 'maxlon': 10}}]})
            self.assertIn('area.region', query)
            self.assertIn('Baden-Württemberg', query)
            self.assertNotIn('around:', query)
            return Response(payload)
        def homepages(rows, client, **kwargs):
            self.assertEqual(kwargs['search_area_hint'], 'Baden-Württemberg')
            return []
        with patch('hiring_scraper.location_discovery._wait_for_overpass_slot'), \
             patch('hiring_scraper.location_discovery.fetch_wikidata_entities', return_value={}), \
             patch('hiring_scraper.location_discovery.discover_missing_websites', side_effect=homepages):
            rows = fetch_location_companies(0, 0, 15000, state='Baden-Wuerttemberg', opener=opener)
        self.assertEqual({row['source_id'] for row in rows}, {'node/1', 'node/2'})

    def test_state_campaign_queues_all_in_state_websites_and_results_include_their_jobs(self):
        location = self.save_state()
        candidates = [
            {'source_id': 'node/south', 'name': 'South', 'latitude': 47.66, 'longitude': 9.17,
             'website_url': 'https://south.example/'},
            {'source_id': 'node/north', 'name': 'North', 'latitude': 49.77, 'longitude': 9.5,
             'website_url': 'https://north.example/'},
            {'source_id': 'node/no-web', 'name': 'No website', 'latitude': 48.5, 'longitude': 9.5},
        ]
        def fetch(*args, **kwargs):
            self.assertEqual(kwargs['state'], 'Baden-Württemberg')
            return candidates
        with patch.object(worker, 'fetch_location_companies', side_effect=fetch):
            self.assertTrue(worker.process_once(location_jobs_only=True))
        with self.factory() as session:
            campaign = session.get(models.DiscoveryJob, location['first_discovery_job']['id'])
            self.assertEqual(campaign.candidate_total, 2)
            self.assertEqual(campaign.companies_found, 3)
            self.assertEqual({item.company.name for item in campaign.companies}, {'North', 'South'})
        def discover(seed, *_args, **_kwargs):
            return {'status': 'jobs_feed_found', 'pages': [], 'boards': [
                {'provider': 'greenhouse', 'feed_url': seed['website'] + 'feed',
                 'feed_state': 'parsed', 'complete': True, 'jobs': [
                     {'id': 'role', 'title': seed['name'] + ' Engineer',
                      'url': seed['website'] + 'jobs/role', 'location': 'Berlin'},
                     {'id': 'unknown', 'title': seed['name'] + ' Unspecified',
                      'url': seed['website'] + 'jobs/unknown'},
                     {'id': 'remote', 'title': seed['name'] + ' Remote',
                      'url': seed['website'] + 'jobs/remote', 'is_remote': True,
                      'raw_metadata': {'remote_country_codes': ['US']}}]}]}
        with patch.object(worker, 'discover', side_effect=discover), patch.object(worker, 'Client'):
            self.assertTrue(worker.process_batch(location_jobs_only=True, max_workers=2))
        with self.factory.begin() as session:
            outside = models.Company(source='test', source_id='outside', name='Outside',
                                     state='Bayern', latitude=48.5, longitude=9.5)
            feed = models.JobFeed(company=outside, provider='html_jobs', feed_url='https://outside.example/feed', status='parsed', job_count=0)
            session.add(models.Job(feed=feed, external_id='outside', title='Outside Engineer',
                                   url='https://outside.example/jobs/outside'))
        with self.factory() as session:
            companies = api.list_companies(query=None, offset=0, limit=100,
                session=session, state='Baden-Wuerttemberg')
            self.assertEqual({row['name'] for row in companies['items']}, {'North', 'South', 'No website'})
            jobs = api.list_jobs(company_id=None, query=None, offset=0, limit=100,
                session=session, state='Baden-Württemberg', country_code='DE')
            self.assertEqual(jobs['total'], 6)
            self.assertEqual(jobs['counts']['filtered']['outside_area'], 0)
            remote = api.list_jobs(company_id=None, query=None, offset=0, limit=100,
                session=session, state='Baden-Württemberg', location_scope='remote', country_code='DE')
            self.assertEqual(remote['total'], 2)
            self.assertEqual(api.summary(session=session, state='Baden-Württemberg')['companies_in_radius'], 3)
            self.assertEqual(session.get(models.DiscoveryJob, location['first_discovery_job']['id']).status, 'completed')

    def test_state_filters_preserve_expiry_pagination_and_profile_detail_consistency(self):
        from hiring_scraper.app.profiles import create_profile, ProfileRequest
        with self.factory.begin() as session:
            company = models.Company(source='test', source_id='bw', name='Employer', state='Baden-Württemberg')
            feed = models.JobFeed(company=company, provider='html_jobs', status='parsed', job_count=3,
                                 feed_url='https://employer.example/feed')
            session.add_all([
                models.Job(feed=feed, external_id='local', title='Engineer', url='https://employer.example/jobs/local',
                           location_text='Berlin', locations=[models.JobLocation(label='Berlin')]),
                models.Job(feed=feed, external_id='remote', title='Remote Engineer', is_remote=True,
                           url='https://employer.example/jobs/remote', raw_metadata={'remote_country_codes': ['US']}),
                models.Job(feed=feed, external_id='expired', title='Expired Engineer',
                           url='https://employer.example/jobs/expired', raw_metadata={'validThrough': '2000-01-01'}),
                models.Job(feed=feed, external_id='inactive', title='Closed Engineer', is_active=False,
                           url='https://employer.example/jobs/inactive'),
            ])
        with self.factory() as session:
            profile = create_profile(ProfileRequest(name='Profile', search_area={
                'label': 'Karlsruhe', 'city': 'Karlsruhe', 'latitude': 49.0,
                'longitude': 8.4, 'radius_km': 15, 'country_code': 'DE'}), session)
            page = api.list_jobs(company_id=None, query=None, offset=0, limit=100, session=session,
                state='Baden-Württemberg', profile_id=profile['id'], min_match_score=0, include_unknown=True)
            self.assertEqual({row['external_id'] for row in page['items']}, {'local', 'remote'})
            self.assertEqual(page['counts']['filtered']['expired'], 1)
            for item in page['items']:
                detail = api.get_job(item['id'], session, profile_id=profile['id'], state='Baden-Württemberg')
                self.assertEqual(detail['geography'], item['geography'])
                self.assertIn('profile_match', detail)
                self.assertNotIn('profile_match', item)
            partial = api.list_jobs(company_id=None, query='Engineer', offset=1, limit=1,
                                   session=session, state='Baden-Württemberg')
            self.assertEqual(partial['total'], 2)
            self.assertEqual(len(partial['items']), 1)

    def test_state_progress_renews_a_live_scan_lease_before_a_long_search_finishes(self):
        location = self.save_state()
        job_id, token = worker._claim_location_company_search()
        now = models.utcnow()
        with self.factory.begin() as session:
            session.get(models.DiscoveryJob, job_id).location_scan_lease_until = now + timedelta(seconds=5)
        with patch.object(worker, 'utcnow', return_value=now + timedelta(seconds=4)):
            self.assertTrue(worker._update_location_search_progress(job_id, token, 'Checking websites'))
        with patch.object(worker, 'utcnow', return_value=now + timedelta(seconds=10)):
            worker._persist_location_companies(job_id, token, [
                {'source_id': 'node/long', 'name': 'Long search company', 'latitude': 47.66,
                 'longitude': 9.17, 'website_url': 'https://long.example/'}])
        with self.factory() as session:
            job = session.get(models.DiscoveryJob, location['first_discovery_job']['id'])
            self.assertEqual(job.stage, 'career_page_discovery')
            self.assertEqual(job.candidate_total, 1)

    def test_upgrade_preserves_existing_radius_searches_on_both_sqlite_paths(self):
        from alembic import command
        from alembic.config import Config
        from hiring_scraper.app.database import initialize_sqlite_schema
        for method in ('alembic', 'startup'):
            with self.subTest(method=method):
                path = Path(self.directory.name) / (method + '.db')
                target = create_engine(f'sqlite:///{path}')
                # Keep migration logging isolated from the rest of the test suite.
                config = Config()
                config.set_main_option('script_location', 'hiring_scraper/app/migrations')
                config.set_main_option('sqlalchemy.url', str(target.url))
                with patch.dict('os.environ', {'DATABASE_URL': str(target.url)}):
                    command.upgrade(config, '0011_location_scan_retries')
                    with target.begin() as connection:
                        connection.exec_driver_sql("INSERT INTO configured_locations "
                            "(label, latitude, longitude, created_at, updated_at) "
                            "VALUES ('Karlsruhe', 49.0, 8.4, '2026-01-01', '2026-01-01')")
                        connection.exec_driver_sql("INSERT INTO companies "
                            "(source, source_id, name, career_status) "
                            "VALUES ('test', 'legacy', 'Existing employer', 'not_checked')")
                    if method == 'alembic':
                        command.upgrade(config, 'head')
                    else:
                        initialize_sqlite_schema(target)
                        initialize_sqlite_schema(target)
                    with sessionmaker(bind=target)() as session:
                        location = session.scalar(select(models.ConfiguredLocation))
                        self.assertEqual(location.search_scope, 'radius')
                        self.assertEqual(location.label, 'Karlsruhe')
                        self.assertIsNone(session.scalar(select(models.Company)).state)
                    target.dispose()

    def test_recurring_state_schedule_refreshes_all_saved_sources_without_rediscovery(self):
        location = self.save_state()
        with self.factory.begin() as session:
            initial = session.get(models.DiscoveryJob, location['first_discovery_job']['id'])
            initial.status, initial.stage = 'completed', 'complete'
            saved = session.get(models.ConfiguredLocation, location['id'])
            saved.next_run_at = models.utcnow()
            company = models.Company(source='test', source_id='known', name='Known', state='Baden-Württemberg',
                                     latitude=47.66, longitude=9.17, website_url='https://known.example/')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://known.example/careers', status='parsed', job_count=0)
            session.add(feed)
        with patch.object(worker, 'fetch_location_companies', side_effect=AssertionError('Rediscovery')):
            self.assertEqual(worker.schedule_due_locations(), 1)
        with self.factory() as session:
            jobs = session.scalars(select(models.DiscoveryJob).where(models.DiscoveryJob.kind == 'recurring')).all()
            self.assertEqual(jobs[0].search_scope, 'state')
            self.assertIn('Scheduled 1', jobs[0].progress_message)
            self.assertIsNotNone(session.scalar(select(models.JobFeed)).next_scan_at)


if __name__ == '__main__':
    unittest.main()
