import json
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from hiring_scraper.app import enrichment
from hiring_scraper.app.discovery_worker import _upsert_jobs
from hiring_scraper.app.models import Base, Company, Job, JobFeed, utcnow
from hiring_scraper.ats import parse_feed
from hiring_scraper.html_jobs import extract_html_jobs


class JobEnrichmentPipelineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory.begin() as session:
            company = Company(source='test', source_id='1', name='Example')
            feed = JobFeed(company=company, provider='html_jobs', feed_url='https://example.org/jobs', status='parsed')
            session.add(feed); session.flush(); self.feed_id = feed.id

    def tearDown(self):
        self.engine.dispose()

    def upsert(self, data):
        with self.factory.begin() as session:
            _upsert_jobs(session, session.get(JobFeed,self.feed_id),[data],True,utcnow())

    def test_crawler_persists_enrichment_and_refreshes_it_on_changed_job_content(self):
        data = {'id':'1','title':'Developer','url':'https://example.org/jobs/1','description':'Python'}
        self.upsert(data)
        with self.factory() as session:
            job = session.scalar(select(Job))
            self.assertEqual([row['name'] for row in job.enrichment.get('skills',[])], ['Python'])
        self.upsert({**data, 'description':'Java'})
        with self.factory() as session:
            job = session.scalar(select(Job))
            self.assertEqual([row['name'] for row in job.enrichment['skills']], ['Java'])

    def test_verified_detail_is_preserved_on_sparse_refresh_but_not_on_changed_identity(self):
        data = {'id':'1','title':'Developer','url':'https://example.org/jobs/1','description':'Python '*100,
                'raw_metadata':{'description_method':'verified_detail_jsonld','description_evidence_url':'https://example.org/jobs/1'}}
        self.upsert(data)
        self.upsert({**data, 'description':None,'raw_metadata':{}})
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Job)).description,'Python '*100)
        self.upsert({**data,'title':'Accountant','description':None,'raw_metadata':{}})
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(Job)).description)
            self.assertNotIn('Python',[row['name'] for row in session.scalar(select(Job)).enrichment['skills']])

    def test_structured_requirements_survive_both_html_and_feed_parsing(self):
        posting = {'@type':'JobPosting','title':'Python Developer','url':'https://example.org/jobs/1',
            'skills':'Python, SQL','experienceRequirements':{'monthsOfExperience':36},'qualifications':'A degree'}
        html = '<script type="application/ld+json">'+json.dumps(posting)+'</script>'
        html_job = extract_html_jobs(html,posting['url'])['jobs'][0]
        feed_job = parse_feed('schema_org',json.dumps({'@type':'DataFeed','dataFeedElement':[posting]}).encode(),posting['url'])['jobs'][0]
        for job in [html_job,feed_job]:
            self.assertEqual(job['raw_metadata'].get('skills'),'Python, SQL')
            self.assertEqual(job['raw_metadata'].get('experienceRequirements'),{'monthsOfExperience':36})

    def test_bounded_detail_backfill_improves_description_and_defers_repeat_requests(self):
        self.assertTrue(callable(getattr(enrichment,'backfill',None)), 'Enrichment backfill is not implemented')
        url='https://example.org/jobs/1'
        self.upsert({'id':'1','title':'Python Developer','url':url})
        posting={'@type':'JobPosting','title':'Python Developer','url':url,'description':'Python and SQL. '*100}
        body='<script type="application/ld+json">'+json.dumps(posting)+'</script>'
        class OfflineClient:
            def get(self,url): return {'state':'ok'},body.encode()
        with patch.object(enrichment,'SessionLocal',self.factory):
            result = enrichment.backfill(1,fetch_details=True,client=OfflineClient())
            self.assertEqual(result['descriptions_improved'],1)
            again = enrichment.backfill(1,fetch_details=True,client=OfflineClient())
            self.assertEqual(again['details_attempted'],0)
            self.assertEqual(again['updated'],0)
        with self.factory() as session:
            job=session.scalar(select(Job))
            self.assertEqual(job.description,'Python and SQL. '*99+'Python and SQL.')
            self.assertEqual(job.raw_metadata['description_evidence_url'],url)
            self.assertEqual(job.enrichment['quality'],'description_available')

    def test_one_bad_detail_response_does_not_stop_other_jobs(self):
        self.upsert({'id':'1','title':'Developer','url':'https://example.org/jobs/1'})
        self.upsert({'id':'2','title':'Developer','url':'https://example.org/jobs/2'})
        class OfflineClient:
            def get(self,url):
                if url.endswith('/1'): raise ValueError('malformed source')
                return {'state':'robots_disallowed'}, b''
        with patch.object(enrichment,'SessionLocal',self.factory):
            try:
                result=enrichment.backfill(2,fetch_details=True,client=OfflineClient())
            except ValueError:
                self.fail('A single malformed detail must not terminate the enrichment pass')
            self.assertEqual(result['details_attempted'],2)
        with self.factory() as session:
            jobs=session.scalars(select(Job)).all()
            self.assertTrue(all(job.raw_metadata.get('detail_enrichment_attempt') for job in jobs))

    def test_paired_applicant_detail_persists_evidence_with_one_bounded_attempt(self):
        url = 'https://example.org/jobs/linux-administrator'
        self.upsert({'id': 'linux', 'title': 'Senior LINUX Administrator (all)', 'url': url,
                     'location': 'Karlsruhe', 'raw_metadata': {'source_page_url': 'https://example.org/jobs'}})
        body = ('<main><h1>Senior LINUX Administrator (all)</h1>'
                '<h3>Wenn folgende Aufgaben Dich begeistern:</h3>'
                '<p>Wir betreiben Linux Server und dokumentieren Lösungen für unsere Kunden.</p>'
                '<p>Wir entwickeln die Infrastruktur unserer Kunden und betreuen technische '
                'Systeme mit nachvollziehbarer Dokumentation der Arbeitsschritte.</p>'
                '<h3>Wenn Du folgende Voraussetzungen mitbringst:</h3>'
                '<ul><li>Python Kenntnisse sind erforderlich.</li>'
                '<li>Ruby Kenntnisse sind von Vorteil.</li></ul></main>')

        class OfflineClient:
            def get(self, requested_url):
                return {'state': 'ok', 'url': requested_url}, body.encode()

        with patch.object(enrichment, 'SessionLocal', self.factory):
            result = enrichment.backfill(1, fetch_details=True, client=OfflineClient())
            self.assertEqual(result['details_attempted'], 1)
            self.assertEqual(result['descriptions_improved'], 1)
            repeated = enrichment.backfill(1, fetch_details=True, client=OfflineClient())
            self.assertEqual(repeated['details_attempted'], 0)
        with self.factory() as session:
            job = session.scalar(select(Job))
            self.assertTrue(job.is_active)
            self.assertEqual(job.location_text, 'Karlsruhe')
            self.assertEqual(job.raw_metadata['source_page_url'], 'https://example.org/jobs')
            self.assertEqual(job.raw_metadata['description_evidence_url'], url)
            self.assertEqual(job.raw_metadata['description_method'], 'verified_detail_html')
            self.assertEqual(job.raw_metadata['detail_enrichment_attempt']['status'], 'improved')
            self.assertIn('Python', [row['name'] for row in job.enrichment['skills']])
            self.assertIn('Python', [row['value'] for row in job.enrichment['requirements']])
            self.assertNotIn('Ruby', [row['value'] for row in job.enrichment['requirements']])

    def test_changed_supplied_description_replaces_an_old_verified_detail(self):
        data={'id':'1','title':'Developer','url':'https://example.org/jobs/1',
              'description':'Python '*100,'raw_metadata':{'description_method':'verified_detail_jsonld'}}
        self.upsert(data)
        self.upsert({**data,'description':'Java '*80,'raw_metadata':{}})
        with self.factory() as session:
            job=session.scalar(select(Job))
            self.assertEqual(job.description,'Java '*80)
            self.assertEqual([row['name'] for row in job.enrichment['skills']],['Java'])
