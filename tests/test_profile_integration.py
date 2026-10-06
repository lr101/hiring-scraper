import importlib
import importlib.util
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from hiring_scraper.app import api, models


class ProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models.Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def profile_api(self):
        self.assertIsNotNone(importlib.util.find_spec('hiring_scraper.app.profiles'),
                             'Saved profiles are not implemented')
        return importlib.import_module('hiring_scraper.app.profiles')

    def test_saved_profile_filters_before_pagination_and_preserves_location_scope(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            profile = profiles.create_profile(profiles.ProfileRequest(name='Developer', skills=['python', 'postgres'],
                desired_roles=['Software developer'], work_styles=['remote']), session)
            self.assertEqual(profile['skills'], ['Python', 'PostgreSQL'])
            company = models.Company(source='test', source_id='1', name='Example')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.org/jobs', status='parsed')
            for key,title,remote,style,location in [
                ('remote','Python Developer',True,'remote','Berlin'),
                ('local','Python Developer',False,'onsite','Karlsruhe'),
                ('unknown','Software Engineer',False,None,'Karlsruhe'),
                ('outside','Python Developer',False,None,'Hamburg')]:
                feed.jobs.append(models.Job(external_id=key,title=title,url=f'https://example.org/jobs/{key}',
                    description='Python and PostgreSQL development. '*20 if key != 'unknown' else None,
                    is_remote=remote,work_arrangement=style,locations=[models.JobLocation(label=location)]))
            session.add(company); session.commit()
            page = api.list_jobs(49.0068705,8.4034195,15,None,None,'Karlsruhe',0,1,
                session=session, profile_id=profile['id'], min_match_score=0, include_unknown=True)
            self.assertEqual(page['total'],2)
            self.assertEqual(page['items'][0]['external_id'],'remote')
            self.assertTrue(page['items'][0]['profile_match']['eligible'])
            self.assertIn('enrichment',page['items'][0])
            strict = api.list_jobs(49.0068705,8.4034195,15,None,None,'Karlsruhe',0,10,
                session=session, profile_id=profile['id'], include_unknown=False)
            self.assertEqual([item['external_id'] for item in strict['items']], ['remote'])

    def test_unknown_profile_does_not_silently_return_unfiltered_jobs(self):
        self.profile_api()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as raised:
                api.list_jobs(49.0,8.4,15,None,None,None,0,10,session=session,profile_id=999)
            self.assertEqual(raised.exception.status_code,404)

    def test_profile_delete_and_cv_preview_do_not_store_cv_text(self):
        profiles = self.profile_api()
        with Session(self.engine) as session:
            preview = profiles.preview_cv(profiles.CVPreview(text='My email is private@example.org. Python and PostgreSQL.'))
            self.assertEqual(set(preview['skills']), {'Python','PostgreSQL'})
            profile = profiles.create_profile(profiles.ProfileRequest(name='Test',skills=preview['skills']),session)
            self.assertNotIn('text',profile)
            profiles.delete_profile(profile['id'],session)
            self.assertEqual(profiles.list_profiles(session)['items'],[])

    def test_profile_edit_is_allowed_from_the_frontend_origin(self):
        import asyncio
        messages=[]
        async def send(message): messages.append(message)
        async def receive(): return {'type':'http.request','body':b''}
        scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'OPTIONS',
               'scheme':'http','path':'/api/v1/profiles/1','raw_path':b'/api/v1/profiles/1',
               'query_string':b'', 'headers':[(b'origin',b'http://localhost:5173'),
                   (b'access-control-request-method',b'PUT')], 'server':('test',80),'client':('test',1)}
        asyncio.run(api.app(scope,receive,send))
        start=next(item for item in messages if item['type']=='http.response.start')
        self.assertEqual(start['status'],200)

    def test_existing_sqlite_jobs_are_preserved_when_initializing_the_new_schema(self):
        from hiring_scraper.app import database
        self.assertTrue(callable(getattr(database,'initialize_sqlite_schema',None)),
                        'Existing SQLite databases need the enrichment column added')
        with Session(self.engine) as session:
            company=models.Company(source='test',source_id='legacy',name='Legacy Co')
            feed=models.JobFeed(company=company,provider='html_jobs',feed_url='https://example.org',status='parsed')
            feed.jobs.append(models.Job(external_id='1',title='Legacy Developer',url='https://example.org/jobs/1',description='Python'))
            session.add(company);session.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE jobs DROP COLUMN enrichment')
            connection.exec_driver_sql('DROP TABLE user_profiles')
        database.initialize_sqlite_schema(self.engine)
        database.initialize_sqlite_schema(self.engine)
        with Session(self.engine) as session:
            job=session.scalar(select(models.Job))
            self.assertEqual(job.description,'Python')
            self.assertEqual(job.enrichment,{})
            self.assertEqual(session.scalars(select(models.UserProfile)).all(),[])
