import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from hiring_scraper.app import discovery_worker as worker
from hiring_scraper.app.models import Base, Company, DiscoveryJob, DiscoveryJobCompany, utcnow

class Response:
    def __init__(self,payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def read(self,_): return json.dumps(self.payload).encode()

class NationalDiscoveryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=create_engine(f'sqlite:///{Path(self.tmp.name)/"national.db"}')
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,autoflush=False,expire_on_commit=False)
        self.patches=[patch.object(worker,'engine',self.engine),patch.object(worker,'SessionLocal',self.factory),
                      patch('hiring_scraper.location_discovery._wait_for_overpass_slot'),
                      patch('hiring_scraper.location_discovery.fetch_wikidata_entities',return_value={})]
        for p in self.patches: p.start()
        with self.factory.begin() as session:
            j=DiscoveryJob(label='Germany',search_scope='country',latitude=0,longitude=0,radius_km=15,
                status='queued',stage='company_homepage_discovery')
            session.add(j);session.flush();self.job_id=j.id
        self.queries=[]
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.engine.dispose();self.tmp.cleanup()
    def opener(self,request,**_):
        query=parse_qs(request.data.decode())['data'][0];self.queries.append(query)
        if 'out bb;' in query:
            return Response({'elements':[{'bounds':{'minlat':48,'minlon':8,'maxlat':49,'maxlon':9}}]})
        return Response({'elements':[{'type':'node','id':1,'lat':48.5,'lon':8.5,
            'tags':{'name':'Large employer','office':'company','employees':'1000','website':'https://national-employer-test.de'}}]})
    def step(self):
        with self.factory.begin() as session:
            j=session.get(DiscoveryJob,self.job_id);j.scheduled_for=utcnow()-timedelta(seconds=1)
        with patch('hiring_scraper.location_discovery.urlopen',side_effect=self.opener):
            return worker.process_once(location_jobs_only=True)
    def test_national_map_checkpoints_one_bounded_step_then_releases_worker(self):
        self.step()
        with self.factory() as session:
            job=session.get(DiscoveryJob,self.job_id)
            self.assertEqual(job.stage,'company_homepage_discovery')
            self.assertEqual(job.status,'scheduled')
            self.assertTrue(job.map_cursor)
            self.assertIsNone(job.location_scan_token)
        self.assertEqual(len(self.queries),1)
        self.step()
        with self.factory() as session:
            job=session.get(DiscoveryJob,self.job_id)
            company=session.scalar(select(Company))
            self.assertIsNotNone(company)
            self.assertEqual(company.state,'Baden-Württemberg')
            self.assertEqual(company.country_code,'DE')
            self.assertEqual(company.employee_count,1000)
            self.assertEqual(company.crawl_interval_days,1)
            self.assertEqual(job.companies_found,1)
            self.assertEqual(len(session.scalars(select(DiscoveryJobCompany)).all()),1)
            self.assertGreater(job.scheduled_for.replace(tzinfo=utcnow().tzinfo),utcnow())
        self.assertEqual(len(self.queries),2)
        self.assertFalse(worker.process_once(location_jobs_only=True),'Neither next map section nor staggered career check is due yet')
    def test_national_cursor_visits_all_sixteen_states_and_finishes_only_after_inventory(self):
        from hiring_scraper.geography import GERMAN_STATES
        completed=[]
        def opener(request,**_):
            query=parse_qs(request.data.decode())['data'][0]
            self.queries.append(query)
            if 'out bb;' in query:
                state=next(name for name in GERMAN_STATES if f'name"="{name}"' in query)
                return Response({'elements':[{'bounds':{'minlat':48,'minlon':8,'maxlat':48.5,'maxlon':8.5},'tags':{'name':state}}]})
            state=next(name for name in GERMAN_STATES if f'name"="{name}"' in query)
            completed.append(state)
            from urllib.parse import urlsplit
            return Response({'elements':[{'type':'node','id':len(completed),'lat':48.25,'lon':8.25,
                'tags':{'name':state+' Employer','office':'company','employees':'300',
                        'website':f'https://state{len(completed)}-employer-test.de'}}]})
        steps=0
        while True:
            with self.factory.begin() as session:
                job=session.get(DiscoveryJob,self.job_id)
                if job.map_cursor.get('done'):
                    break
                job.scheduled_for=utcnow()-timedelta(seconds=1)
            with patch('hiring_scraper.location_discovery.urlopen',side_effect=opener):
                worker.process_once(location_jobs_only=True)
            steps+=1
            self.assertLessEqual(steps,33)
        with self.factory() as session:
            job=session.get(DiscoveryJob,self.job_id)
            companies=session.scalars(select(Company)).all()
            self.assertEqual(set(completed),set(GERMAN_STATES))
            self.assertEqual({c.state for c in companies},set(GERMAN_STATES))
            self.assertTrue(all(c.country_code=='DE' and c.crawl_interval_days==1 for c in companies))
            self.assertEqual(job.stage,'career_page_discovery')
            self.assertEqual(job.candidate_total,16)
            self.assertEqual(job.companies_found,16)
            self.assertEqual(job.map_cursor['state_index'],16)

    def test_repeated_sections_count_missing_homepage_companies_once_and_queue_late_homepage(self):
        candidate={'source_id':'way/2','name':'Mapped company','website_url':None,'employee_count':None}
        # Seed a company-only company from a completed map section.
        attempt=worker._claim_location_company_search()
        cursor={'state_index':1,'completed_sections':1,'pending':[[48.5,8.5,49,9,0]]}
        worker._persist_location_companies(self.job_id,attempt[1],[candidate],map_cursor=cursor,country_state='Baden-Württemberg')
        with self.factory.begin() as session:
            job=session.get(DiscoveryJob,self.job_id)
            self.assertEqual(job.companies_found,1)
            self.assertEqual(job.candidate_total,0)
            item=session.scalar(select(DiscoveryJobCompany))
            self.assertEqual(item.status,'skipped')
            job.scheduled_for=utcnow()-timedelta(seconds=1)
        attempt=worker._claim_location_company_search()
        candidate['website_url']='https://mapped-employer-test.de/'
        worker._persist_location_companies(self.job_id,attempt[1],[candidate],map_cursor={**cursor,'completed_sections':2},country_state='Baden-Württemberg')
        with self.factory() as session:
            job=session.get(DiscoveryJob,self.job_id)
            self.assertEqual(job.companies_found,1)
            self.assertEqual(job.candidate_total,1)
            self.assertEqual(session.scalar(select(DiscoveryJobCompany)).status,'queued')

    def test_country_campaign_preserves_existing_company_schedule_and_parked_rank(self):
        now=utcnow()
        with self.factory.begin() as session:
            company=Company(source='openstreetmap',source_id='way/parked',name='Parked employer',
                website_url='https://parked-employer-test.de/',crawl_paused=True,irrelevant_checks=2,
                crawl_priority=0,crawl_interval_days=None,next_discovery_at=None)
            known_due=now+timedelta(days=2)
            known=Company(source='openstreetmap',source_id='way/known',name='Known employer',
                website_url='https://known-employer-test.de/',crawl_priority=60,crawl_interval_days=3,
                next_discovery_at=known_due)
            session.add_all([company,known]);session.flush();company_id=company.id;known_id=known.id
            job=session.get(DiscoveryJob,self.job_id)
            job.map_cursor={'state_index':0,'completed_sections':1,'pending':[[48.5,8.5,49,9,0]]}
        attempt=worker._claim_location_company_search(now=now)
        candidates=[
            {'source_id':'way/parked','name':'Parked employer','website_url':'https://parked-employer-test.de/'},
            {'source_id':'way/known','name':'Known employer','website_url':'https://known-employer-test.de/'},
        ]
        worker._persist_location_companies(self.job_id,attempt[1],candidates,
            map_cursor={'state_index':0,'completed_sections':2,'pending':[[48.5,8.5,49,9,0]]},
            country_state='Baden-Württemberg')
        with self.factory() as session:
            company=session.get(Company,company_id)
            known=session.get(Company,known_id)
            job=session.get(DiscoveryJob,self.job_id)
            items={session.get(Company,item.company_id).source_id:item.status
                   for item in session.scalars(select(DiscoveryJobCompany)).all()}
            self.assertTrue(company.crawl_paused)
            self.assertIsNone(company.next_discovery_at)
            self.assertEqual(known.next_discovery_at.replace(tzinfo=now.tzinfo),known_due)
            self.assertEqual(job.candidate_total,1)
            self.assertEqual(job.homepages_found,1)
            self.assertEqual(items['way/parked'],'skipped')
            self.assertEqual(items['way/known'],'queued')

    def test_country_campaign_claims_due_companies_by_rank_and_respects_future_schedule(self):
        now=utcnow()
        with self.factory.begin() as session:
            job=session.get(DiscoveryJob,self.job_id)
            job.status='queued';job.stage='career_page_discovery';job.candidate_total=3
            companies=[
                Company(source='openstreetmap',source_id='way/low',name='Low rank',website_url='https://low-rank-test.de/',
                    crawl_priority=10,crawl_interval_days=30,next_discovery_at=now-timedelta(days=5)),
                Company(source='openstreetmap',source_id='way/high',name='High rank',website_url='https://high-rank-test.de/',
                    crawl_priority=100,crawl_interval_days=1,next_discovery_at=now-timedelta(days=1)),
                Company(source='openstreetmap',source_id='way/future',name='Not due yet',website_url='https://future-test.de/',
                    crawl_priority=100,crawl_interval_days=1,next_discovery_at=now+timedelta(days=1)),
            ]
            session.add_all(companies);session.flush()
            session.add_all(DiscoveryJobCompany(discovery_job_id=job.id,company_id=company.id,status='queued')
                for company in companies)
        claim=worker._claim_campaign_company(now=now)
        self.assertIsNotNone(claim)
        self.assertEqual(claim[0],companies[1].id)
        second=worker._claim_campaign_company(now=now)
        self.assertEqual(second[0],companies[0].id)
        self.assertIsNone(worker._claim_campaign_company(now=now),'A company whose ranked revisit is in the future must remain queued')

    def test_national_inventory_crawls_shared_employer_domain_once(self):
        attempt=worker._claim_location_company_search()
        with self.factory.begin() as session:
            root=Company(source='legacy',source_id='hamburg-office',name='Shared Employer Hamburg',
                         website_url='https://shared-employer-test.de/about',
                         domain='shared-employer-test.de',state='Hamburg',country_code='DE',
                         next_discovery_at=utcnow()-timedelta(seconds=1))
            session.add(root)
            session.flush()
            root_id=root.id
        candidates=[
            {'source_id':'way/office-a','name':'Employer Office A','website_url':'https://shared-employer-test.de/jobs',
             'domain':'shared-employer-test.de'},
            {'source_id':'way/office-b','name':'Employer Office B','website_url':'https://www.shared-employer-test.de/contact',
             'domain':'www.shared-employer-test.de'},
        ]
        worker._persist_location_companies(self.job_id,attempt[1],candidates,
            map_cursor={'state_index':0,'completed_sections':1,'pending':[[48.5,8.5,49,9,0]]},
            country_state='Baden-Württemberg')
        with self.factory.begin() as session:
            companies=session.scalars(select(Company).order_by(Company.id)).all()
            items=session.scalars(select(DiscoveryJobCompany).order_by(DiscoveryJobCompany.id)).all()
            self.assertEqual(len(companies),3,'Distinct OSM offices remain searchable')
            self.assertEqual(session.get(Company,root_id).state,'Hamburg',
                             'A shared page must not move the existing office into this map tile state')
            self.assertEqual(sum(item.status=='queued' for item in items),1,
                             'Shared employer websites get one initial page check')
            self.assertEqual(sum(item.status=='skipped' for item in items),2)
            self.assertEqual(session.get(DiscoveryJob,self.job_id).candidate_total,1)
            for company in companies:
                company.next_discovery_at=utcnow()-timedelta(seconds=1)
        claim=worker._claim_campaign_company()
        self.assertIsNotNone(claim)
        self.assertIsNone(worker._claim_campaign_company(),'The second office must not cause another career-page crawl')

    def test_missing_national_homepage_is_resolved_as_separately_paced_work(self):
        attempt=worker._claim_location_company_search()
        candidate={'source_id':'way/contacted','name':'Contacted Employer GmbH','website_url':None,
                   'employee_count':300,'tags':{'addr:city':'Stuttgart','email':'jobs@contacted-employer.de',
                                                'addr:street':'Werkstrasse','addr:postcode':'70173'}}
        with patch.object(worker,'configured_website_search',return_value=None):
            worker._persist_location_companies(self.job_id,attempt[1],[candidate],
                map_cursor={'state_index':0,'completed_sections':1,'pending':[[48.5,8.5,49,9,0]]},
                country_state='Baden-Württemberg')
        now=utcnow()
        with self.factory() as session:
            company=session.scalar(select(Company))
            self.assertEqual(company.homepage_source_tags['email'],'jobs@contacted-employer.de')
            self.assertIsNotNone(company.homepage_resolution_next_at)
            self.assertEqual(company.crawl_interval_days,1)
            company_id=company.id
        claim=worker._claim_due_homepage_resolution(now=now+timedelta(days=2))
        self.assertEqual(claim[:2],(company_id,1))
        def resolve(candidates,**_):
            candidates[0].update(website_url='https://contacted-employer.de/',domain='contacted-employer.de',
                                domain_match_method='osm_email_verified_page',
                                domain_evidence_url='https://contacted-employer.de/impressum/')
        with patch.object(worker,'resolve_missing_company_websites',side_effect=resolve):
            self.assertTrue(worker._process_homepage_resolution(claim))
        with self.factory() as session:
            company=session.get(Company,company_id)
            self.assertEqual(company.website_url,'https://contacted-employer.de/')
            self.assertIsNone(company.homepage_source_tags)
            self.assertIsNone(company.homepage_resolution_next_at)
            self.assertEqual(company.homepage_resolution_attempts,0)
            self.assertLessEqual(company.next_discovery_at.replace(tzinfo=now.tzinfo),utcnow())

    def test_stale_homepage_resolution_cannot_overwrite_a_newer_claim(self):
        attempt=worker._claim_location_company_search()
        candidate={'source_id':'way/fenced','name':'Fenced Employer GmbH','website_url':None,
                   'tags':{'email':'jobs@fenced-employer.de'}}
        with patch.object(worker,'configured_website_search',return_value=None):
            worker._persist_location_companies(self.job_id,attempt[1],[candidate],
                map_cursor={'state_index':0,'completed_sections':1,'pending':[[48.5,8.5,49,9,0]]},
                country_state='Baden-Württemberg')
        due=utcnow()+timedelta(days=10)
        old=worker._claim_due_homepage_resolution(now=due)
        self.assertIsNotNone(old)
        newer=[]
        def stale_lookup(candidates,**_):
            with self.factory.begin() as session:
                company=session.get(Company,old[0])
                company.homepage_resolution_lease_until=utcnow()-timedelta(seconds=1)
            newer.append(worker._claim_due_homepage_resolution(now=due+timedelta(minutes=20)))
            candidates[0].update(website_url='https://stale.fenced-employer.de/',domain='stale.fenced-employer.de')
        with patch.object(worker,'resolve_missing_company_websites',side_effect=stale_lookup):
            worker._process_homepage_resolution(old)
        self.assertIsNotNone(newer[0])
        with self.factory() as session:
            company=session.get(Company,old[0])
            self.assertIsNone(company.website_url)
            self.assertEqual(company.homepage_resolution_token,newer[0][2])
            self.assertEqual(company.homepage_resolution_attempts,2)

    def test_failure_keeps_previous_section_and_retry_does_not_recreate_its_tasks(self):
        self.step();self.step()
        from urllib.error import HTTPError
        with self.factory.begin() as session:
            session.get(DiscoveryJob,self.job_id).scheduled_for=utcnow()-timedelta(seconds=1)
        with patch('hiring_scraper.location_discovery.urlopen',side_effect=HTTPError('https://overpass-api.de',504,'Unavailable',{},None)),patch('hiring_scraper.location_discovery.time.sleep'):
            worker.process_once(location_jobs_only=True)
        with self.factory() as session:
            job=session.get(DiscoveryJob,self.job_id)
            self.assertEqual(job.status,'scheduled')
            self.assertEqual(len(session.scalars(select(Company)).all()),1)
            self.assertEqual(len(session.scalars(select(DiscoveryJobCompany)).all()),1)
            self.assertEqual(job.map_cursor['completed_sections'],1)
        self.step()
        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(DiscoveryJobCompany)).all()),1)
            self.assertEqual(session.get(DiscoveryJob,self.job_id).map_cursor['completed_sections'],2)

    def test_country_inventory_remains_resumable_after_the_normal_retry_limit(self):
        attempt=worker._claim_location_company_search()
        with self.factory.begin() as session:
            job=session.get(DiscoveryJob,self.job_id)
            job.location_scan_attempt_count=worker.LOCATION_SCAN_MAX_ATTEMPTS
            job.map_cursor={'state_index':3,'completed_sections':17,'pending':[[48,8,48.5,8.5,0]]}
        from urllib.error import HTTPError
        retry_at=worker._fail_location_company_search(self.job_id,attempt[1],
            HTTPError('https://overpass-api.de',504,'Gateway Timeout',{},None))
        self.assertIsNotNone(retry_at)
        with self.factory() as session:
            job=session.get(DiscoveryJob,self.job_id)
            self.assertEqual(job.status,'scheduled')
            self.assertEqual(job.map_cursor['completed_sections'],17)
            self.assertEqual(job.map_cursor['pending'],[[48,8,48.5,8.5,0]])
