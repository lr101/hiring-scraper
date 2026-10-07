import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import seed
from hiring_scraper.app.models import Base, Company
from hiring_scraper.ats import parse_feed


class FixtureImportTests(unittest.TestCase):
    def test_greenhouse_office_name_with_employer_suffix_gets_pilot_city_coordinates(self):
        locations=seed._job_locations({'locations':[{'label':'Karlsruhe - Mail & Media'}]})
        self.assertEqual(locations[0]['latitude'],49.0068705)
        self.assertEqual(locations[0]['longitude'],8.4034195)
        self.assertEqual(locations[0]['precision'],'city_centroid')

    def test_greenhouse_city_only_location_survives_legal_entity_office_and_gets_coordinates(self):
        body=json.dumps({'jobs':[{
            'id':4789623101,'title':'AI Developer',
            'absolute_url':'https://job-boards.eu.greenhouse.io/ionos/jobs/4789623101',
            'location':{'name':'Karlsruhe'},
            'offices':[{'name':'IONOS SE'}],
        }]}).encode()
        parsed=parse_feed('greenhouse',body,'https://job-boards.eu.greenhouse.io/ionos')['jobs'][0]
        locations=seed._job_locations(parsed)
        karlsruhe=next((location for location in locations if location['label']=='Karlsruhe'),None)
        self.assertIsNotNone(karlsruhe)
        self.assertEqual(karlsruhe['latitude'],49.0068705)
        self.assertEqual(karlsruhe['longitude'],8.4034195)

    def test_osm_website_remains_authoritative_with_same_domain_canonicalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            companies=root/'companies.csv'
            with companies.open('w',newline='',encoding='utf-8') as file:
                writer=csv.DictWriter(file,fieldnames=['name','website','email','category','lat','lon','distance_m','osm_type','osm_id','source_url'])
                writer.writeheader()
                writer.writerow({'name':'Direct Site GmbH','website':'https://direct.example/','category':'company','lat':'49','lon':'8.4','osm_type':'node','osm_id':'123','source_url':'https://www.openstreetmap.org/node/123'})
                writer.writerow({'name':'Canonical Site GmbH','website':'http://canonical.example/','category':'company','lat':'49','lon':'8.4','osm_type':'node','osm_id':'124','source_url':'https://www.openstreetmap.org/node/124'})
            careers=root/'careers.json'
            careers.write_text(json.dumps({'companies':[
                {'source_id':'node/123','website_url':'https://unrelated.example/','website_resolution':{'method':'manual_group_match','evidence_urls':['https://evidence.example/']}},
                {'source_id':'node/124','website_url':'https://www.canonical.example/home/','website_resolution':{'method':'osm_website_tag','evidence_urls':['https://www.openstreetmap.org/node/124']}},
            ]}),encoding='utf-8')
            engine=create_engine('sqlite://')
            Base.metadata.create_all(engine)
            factory=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
            try:
                with patch.object(seed,'engine',engine),patch.object(seed,'SessionLocal',factory),patch.object(seed,'IS_SQLITE',True):
                    seed.import_fixture(companies,careers)
                    with factory() as session:
                        company=session.scalar(select(Company).where(Company.source_id=='node/123'))
                        self.assertEqual(company.website_url,'https://direct.example/')
                        self.assertEqual(company.domain_match_method,'osm_website_tag')
                        self.assertEqual(company.domain_evidence_url,'https://www.openstreetmap.org/node/123')
                        self.assertIsNotNone(company.next_discovery_at)
                        scheduled = company.next_discovery_at
                    with factory.begin() as session:
                        company=session.scalar(select(Company).where(Company.source_id=='node/123'))
                        company.next_discovery_at = scheduled
                    seed.import_fixture(companies,careers)
                    with factory() as session:
                        company=session.scalar(select(Company).where(Company.source_id=='node/123'))
                        self.assertEqual(company.next_discovery_at,scheduled)
                        canonical=session.scalar(select(Company).where(Company.source_id=='node/124'))
                        self.assertEqual(canonical.website_url,'https://www.canonical.example/home/')
                        self.assertEqual(canonical.domain_match_method,'osm_website_tag')
                        self.assertEqual(canonical.domain_evidence_url,'https://www.openstreetmap.org/node/124')
            finally:
                engine.dispose()

    def test_verified_website_override_fills_missing_osm_website_and_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            companies=root/'companies.csv'
            with companies.open('w',newline='',encoding='utf-8') as file:
                writer=csv.DictWriter(file,fieldnames=[
                    'name','website','email','category','lat','lon','distance_m',
                    'osm_type','osm_id','source_url',
                ])
                writer.writeheader()
                writer.writerow({
                    'name':'1&1 Mail & Media GmbH','website':'','category':'company',
                    'lat':'48.9995737','lon':'8.3854306','distance_m':'1561.9',
                    'osm_type':'node','osm_id':'3705530440',
                    'source_url':'https://www.openstreetmap.org/node/3705530440',
                })
                writer.writerow({
                    'name':'United Internet Media','website':'http://united-internet-media.de',
                    'category':'company','lat':'48.9995617','lon':'8.3851922','distance_m':'1577.5',
                    'osm_type':'node','osm_id':'3718877125',
                    'source_url':'https://www.openstreetmap.org/node/3718877125',
                })
            careers=root/'careers.json'
            careers.write_text(json.dumps({
                'observed_at':'2026-10-05T00:00:00+00:00',
                'companies':[{
                    'source_id':'node/3705530440',
                    'website_url':'https://www.mail-and-media.com/',
                    'career_url':'https://www.mail-and-media.com/jobs/',
                    'website_resolution':{
                        'method':'manual_group_match',
                        'evidence_urls':['https://home.1und1.de/impressum/'],
                    },
                    'feeds':[],
                }],
            }),encoding='utf-8')
            engine=create_engine('sqlite://')
            Base.metadata.create_all(engine)
            factory=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
            try:
                with patch.object(seed,'engine',engine), patch.object(seed,'SessionLocal',factory), patch.object(seed,'IS_SQLITE',True):
                    seed.import_fixture(companies,careers)
                    with factory() as session:
                        company=session.scalar(select(Company).where(Company.source_id=='node/3705530440'))
                        self.assertEqual(company.website_url,'https://www.mail-and-media.com/')
                        self.assertEqual(company.domain,'mail-and-media.com')
                        self.assertEqual(company.domain_match_method,'manual_group_match')
                        self.assertEqual(company.domain_evidence_url,'https://home.1und1.de/impressum/')
                        self.assertEqual(company.career_url,'https://www.mail-and-media.com/jobs/')
                        due_at=company.next_discovery_at
                        if due_at.tzinfo is None:
                            due_at=due_at.replace(tzinfo=timezone.utc)
                        self.assertLessEqual(due_at,datetime.now(timezone.utc))
                        sibling=session.scalar(select(Company).where(Company.source_id=='node/3718877125'))
                        self.assertEqual(sibling.domain,'united-internet-media.de')
                        self.assertEqual(sibling.domain_match_method,'osm_website_tag')
                        self.assertEqual(sibling.domain_evidence_url,'https://www.openstreetmap.org/node/3718877125')
            finally:
                engine.dispose()

    def test_reimport_does_not_rewind_newer_discovery_or_feed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            companies=root/'companies.csv'
            with companies.open('w',newline='',encoding='utf-8') as file:
                writer=csv.DictWriter(file,fieldnames=[
                    'name','website','email','category','lat','lon','distance_m','osm_type','osm_id','source_url',
                ])
                writer.writeheader()
                writer.writerow({'name':'Acme GmbH','website':'https://acme.example/','category':'company',
                                 'lat':'49','lon':'8.4','distance_m':'0','osm_type':'node','osm_id':'1',
                                 'source_url':'https://www.openstreetmap.org/node/1'})
            careers=root/'careers.json'
            careers.write_text(json.dumps({
                'observed_at':'2026-10-05T00:00:00+00:00',
                'companies':[{'source_id':'node/1','career_url':'https://acme.example/careers',
                              'career_status':'jobs_feed_found','feeds':[{
                                  'provider':'greenhouse','tenant':'acme','board_url':'https://job-boards.greenhouse.io/acme',
                                  'feed_url':'https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true',
                                  'status':'parsed','job_count':1,'jobs':[{
                                      'id':'123','title':'Old snapshot title','url':'https://acme.example/job/123',
                                  }],
                              }]}],
            }),encoding='utf-8')
            engine=create_engine('sqlite://')
            Base.metadata.create_all(engine)
            factory=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
            try:
                with patch.object(seed,'engine',engine),patch.object(seed,'SessionLocal',factory),patch.object(seed,'IS_SQLITE',True):
                    seed.import_fixture(companies,careers)
                    fresh=datetime(2026,10,6,tzinfo=timezone.utc)
                    with factory.begin() as session:
                        company=session.scalar(select(Company).where(Company.source_id=='node/1'))
                        feed=company.feeds[0]
                        job=feed.jobs[0]
                        company.career_url='https://jobs.acme.example/live'
                        company.career_status='jobs_feed_found'
                        company.last_checked_at=fresh
                        feed.status='incomplete'
                        feed.job_count=8
                        feed.last_checked_at=fresh
                        job.title='Live title'
                        job.is_active=False
                        job.last_seen_at=fresh
                    seed.import_fixture(companies,careers)
                    with factory() as session:
                        company=session.scalar(select(Company).where(Company.source_id=='node/1'))
                        feed=company.feeds[0]
                        job=feed.jobs[0]
                        self.assertEqual(company.career_url,'https://jobs.acme.example/live')
                        self.assertEqual(company.career_status,'jobs_feed_found')
                        self.assertEqual(seed._as_utc(company.last_checked_at),fresh)
                        self.assertEqual(feed.status,'incomplete')
                        self.assertEqual(feed.job_count,8)
                        self.assertEqual(job.title,'Live title')
                        self.assertFalse(job.is_active)
            finally:
                engine.dispose()


if __name__=='__main__':
    unittest.main()
