"""The extension catalog is stable, bounded, and independent of search location."""
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from hiring_scraper.app import models


class CompanionCatalogTests(unittest.TestCase):
    def test_site_status_matches_any_company_subdomain_without_crossing_host_boundaries(self):
        from hiring_scraper.app.companion import companion_site
        engine = create_engine('sqlite://'); models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company = models.Company(source='test', source_id='site', name='Employer', domain='employer.example', website_url='https://employer.example')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://careers.employer.example/jobs', status='parsed')
            session.add(models.Job(feed=feed, external_id='one', title='Engineer', url='https://careers.employer.example/jobs/1'))
            session.commit()
            found = companion_site(host='news.eu.employer.example', session=session)
            self.assertEqual(found['job_count'], 1)
            self.assertEqual(found['feeds'][0]['url'], feed.feed_url)
            self.assertEqual(found['items'][0]['title'], 'Engineer')
            self.assertEqual(companion_site(host='notemployer.example', session=session)['job_count'], 0)
            self.assertEqual(companion_site(host='employer.example.evil.example', session=session)['feeds'], [])
        engine.dispose()

    def test_site_status_uses_an_imported_root_feed_without_company_website_metadata(self):
        from hiring_scraper.app.companion import companion_site
        engine = create_engine('sqlite://'); models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            company = models.Company(source='test', source_id='legacy-site', name='Employer')
            feed = models.JobFeed(company=company, provider='html_jobs', feed_url='https://employer.example/jobs', status='parsed')
            session.add(models.Job(feed=feed, external_id='one', title='Engineer', url='https://employer.example/jobs/1'))
            session.commit()
            self.assertEqual(companion_site(host='news.employer.example', session=session)['job_count'], 1)
        engine.dispose()

    def test_site_status_keeps_shared_ats_tenants_separate(self):
        from hiring_scraper.app.companion import companion_site
        engine = create_engine('sqlite://'); models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            for tenant in ['acme', 'other']:
                company = models.Company(source='test', source_id=tenant, name=tenant)
                feed = models.JobFeed(company=company, provider='html_jobs', feed_url=f'https://{tenant}.personio.de/jobs', status='parsed')
                session.add(models.Job(feed=feed, external_id='1', title=tenant, url=f'https://{tenant}.personio.de/job/1'))
            session.commit()
            self.assertEqual(companion_site(host='acme.personio.de', session=session)['items'][0]['title'], 'acme')
            self.assertEqual(companion_site(host='personio.de', session=session)['job_count'], 0)
            self.assertEqual(companion_site(host='unknown.personio.de', session=session)['feeds'], [])
            for tenant in ['acme', 'other']:
                company = models.Company(source='test', source_id='gh-' + tenant, name=tenant)
                feed = models.JobFeed(company=company, provider='greenhouse', feed_url=f'https://boards.greenhouse.io/{tenant}', status='parsed')
                session.add(models.Job(feed=feed, external_id='1', title=tenant, url=f'https://boards.greenhouse.io/{tenant}/jobs/1'))
            session.commit()
            self.assertEqual(companion_site(host='boards.greenhouse.io', session=session)['job_count'], 0)
        engine.dispose()

    def test_site_status_recognizes_supported_hostname_tenants(self):
        from hiring_scraper.app.companion import companion_site
        engine = create_engine('sqlite://'); models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            for suffix in ['softgarden.io', 'softgarden.de', 'onlyfy.jobs', 'helixjobs.com']:
                for tenant in ['acme', 'other']:
                    company = models.Company(source='test', source_id=f'{tenant}.{suffix}', name=tenant)
                    feed = models.JobFeed(company=company, provider='html_jobs', feed_url=f'https://{tenant}.{suffix}/jobs', status='parsed')
                    session.add(models.Job(feed=feed, external_id='1', title=tenant, url=f'https://{tenant}.{suffix}/jobs/1'))
            session.commit()
            for suffix in ['softgarden.io', 'softgarden.de', 'onlyfy.jobs', 'helixjobs.com']:
                with self.subTest(suffix=suffix):
                    found = companion_site(host='acme.' + suffix, session=session)
                    self.assertEqual(found['job_count'], 1)
                    self.assertEqual([item['title'] for item in found['items']], ['acme'])
                    self.assertEqual(companion_site(host=suffix, session=session)['feeds'], [])
        engine.dispose()

    def test_application_details_reject_answers_the_extension_cannot_fill(self):
        from pydantic import ValidationError
        from hiring_scraper.app.profiles import ProfileRequest
        for details in [
            {'email': 'invalid'}, {'website': 'javascript:alert(1)'},
            {'preferences': {'salary_amount': '75,000'}},
            {'preferences': {'salary_currency': 'eur'}},
            {'preferences': {'salary_period': 'weekly'}},
            {'preferences': {'weekly_hours': '200'}},
            {'preferences': {'available_from': '2026-02-30'}},
        ]:
            with self.subTest(details=details), self.assertRaises(ValidationError):
                ProfileRequest(name='Developer', application_details=details)

    def test_profile_saves_application_details_without_confusing_profile_label_with_name(self):
        from hiring_scraper.app.profiles import ProfileRequest, create_profile, update_profile
        engine = create_engine('sqlite://'); models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            result = create_profile(ProfileRequest(name='Developer', application_details={
                'first_name': 'Alex', 'last_name': 'Example',
                'preferences': {'salary_amount': '75000', 'salary_currency': 'EUR', 'salary_period': 'year'}
            }), session=session)
            self.assertEqual(result['application_details']['first_name'], 'Alex')
            result = update_profile(result['id'], ProfileRequest(name='Senior developer'), session=session)
            self.assertEqual(result['application_details']['preferences']['salary_amount'], '75000')
        engine.dispose()

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
