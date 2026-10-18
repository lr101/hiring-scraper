"""The extension catalog is stable, bounded, and independent of search location."""
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from hiring_scraper.app import models


class CompanionCatalogTests(unittest.TestCase):
    def test_lookup_finds_jobs_after_the_catalog_limit_without_matching_other_requisitions(self):
        from hiring_scraper.app.companion import lookup_companion_jobs
        engine = create_engine('sqlite://')
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company = models.Company(source='test', source_id='lookup', name='Employer')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://employer.example/jobs', status='parsed')
            session.add_all([models.Job(feed=feed, external_id=str(i), title='Old role', url=f'https://ats.example/apply?jobId={i+1000}', is_active=False) for i in range(2001)])
            target = models.Job(feed=feed, external_id='target', title='Current role', url='https://ats.example/apply?jobId=42&utm_source=feed')
            other = models.Job(feed=feed, external_id='other', title='Other role', url='https://ats.example/apply?jobId=99')
            session.add_all([target, other]);session.commit()
            found = lookup_companion_jobs(url='https://ats.example/apply?utm_campaign=mail&jobId=42#apply', session=session)
            self.assertEqual([row['id'] for row in found['items']], [target.id])
            self.assertEqual(set(found['items'][0]), {'id','title','company_name','url','is_active'})
            self.assertEqual(lookup_companion_jobs(url='https://ats.example/apply?jobId=43',session=session)['items'], [])
            self.assertEqual(lookup_companion_jobs(url='https://ats.example/apply?jobId=42&step=other',session=session)['items'], [])
            self.assertEqual(lookup_companion_jobs(url='https://other.example/apply?jobId=42',session=session)['items'], [])
        engine.dispose()

    def test_server_search_finds_uncached_jobs_and_treats_wildcards_literally(self):
        from hiring_scraper.app.companion import search_companion_jobs
        engine = create_engine('sqlite://')
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company = models.Company(source='test', source_id='search', name='Research Labs')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://employer.example/jobs', status='parsed')
            session.add_all([models.Job(feed=feed, external_id=str(i), title='Research coordinator', url=f'https://employer.example/jobs/{i}') for i in range(15)])
            session.commit()
            result = search_companion_jobs(q='coordinator labs',session=session)
            self.assertEqual(len(result['items']),10)
            self.assertEqual(result['items'][0]['company_name'],'Research Labs')
            self.assertEqual(search_companion_jobs(q='%%',session=session)['items'],[])
        engine.dispose()

    def test_lookup_narrows_nonstandard_meaningful_query_keys_before_the_candidate_limit(self):
        from hiring_scraper.app.companion import lookup_companion_jobs
        engine=create_engine('sqlite://');models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company=models.Company(source='test',source_id='query',name='Employer')
            feed=models.JobFeed(company=company,provider='html_jobs',feed_url='https://ats.example/',status='parsed')
            jobs=[models.Job(feed=feed,external_id=str(i),title='Role',url=f'https://ats.example/position?vacancy={i}&language=de') for i in range(150)]
            session.add_all(jobs);session.commit()
            result=lookup_companion_jobs(url='https://ats.example/position?language=de&vacancy=149',session=session)
            self.assertEqual([row['id'] for row in result['items']],[jobs[149].id])
        engine.dispose()

    def test_lookup_preserves_spa_fragments_and_rejects_session_urls(self):
        from fastapi import HTTPException
        from hiring_scraper.app.companion import lookup_companion_jobs
        engine = create_engine('sqlite://')
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company=models.Company(source='test',source_id='spa',name='Employer')
            feed=models.JobFeed(company=company,provider='html_jobs',feed_url='https://ats.example/',status='parsed')
            job=models.Job(feed=feed,external_id='42',title='Role',url='https://ats.example/#/job/42')
            session.add_all([models.Job(feed=feed,external_id=str(i+100),title='Other role',url=f'https://ats.example/#/job/{i+100}') for i in range(150)])
            session.add(job);session.commit()
            self.assertEqual(lookup_companion_jobs(url=job.url,session=session)['items'][0]['id'],job.id)
            self.assertEqual(lookup_companion_jobs(url='https://ats.example/#/job/99',session=session)['items'],[])
            for url in ['https://ats.example/login?jobId=42','https://ats.example/apply?token=private','http://127.0.0.1/jobs/42']:
                with self.assertRaises(HTTPException):lookup_companion_jobs(url=url,session=session)
            with self.assertRaises(HTTPException):lookup_companion_jobs(url='https://[invalid/jobs/42',session=session)
        engine.dispose()

    def test_keyset_catalog_includes_local_and_remote_jobs_without_cv_or_descriptions(self):
        from hiring_scraper.app.companion import list_companion_jobs
        engine = create_engine('sqlite://')
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company = models.Company(source='test', source_id='companion', name='Employer')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://example.test/jobs', status='parsed')
            jobs = [models.Job(feed=feed, external_id=str(i), title=f'Role {i}', url=f'https://example.test/jobs/{i}', description='Never included', is_remote=i == 1, is_active=i != 2) for i in range(4)]
            session.add_all(jobs)
            session.commit()
            first = list_companion_jobs(after_id=0, limit=2, session=session)
            self.assertEqual([job['id'] for job in first['items']], [jobs[0].id, jobs[1].id])
            self.assertEqual(first['next_after_id'], jobs[1].id)
            self.assertEqual(set(first['items'][0]), {'id', 'title', 'company_name', 'url', 'is_active'})
            self.assertEqual(first['items'][0]['company_name'], 'Employer')
            second = list_companion_jobs(after_id=first['next_after_id'], limit=2, session=session)
            self.assertEqual([job['id'] for job in second['items']], [jobs[2].id, jobs[3].id])
            self.assertFalse(second['items'][0]['is_active'])
            self.assertIsNone(second['next_after_id'])
        engine.dispose()
