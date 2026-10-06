import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hiring_scraper.app.api import get_company, list_companies, list_jobs
from hiring_scraper.app.models import Base, Company, Job, JobFeed, JobLocation


class JobDeduplicationTests(unittest.TestCase):
    def test_company_list_filters_discovered_domain_career_feed_and_jobs(self):
        engine=create_engine('sqlite://')
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                domain_only=Company(source='test',source_id='domain',name='Domain Co',
                    domain='domain.example',latitude=49.0068705,longitude=8.4034195)
                career_only=Company(source='test',source_id='career',name='Career Co',
                    career_url='https://career.example/jobs',latitude=49.0068705,longitude=8.4034195)
                feed_company=Company(source='test',source_id='feed',name='Feed Co',
                    domain='feed.example',latitude=49.0068705,longitude=8.4034195,
                    feeds=[JobFeed(provider='greenhouse',feed_url='https://feed.example/jobs',status='parsed',
                        jobs=[Job(external_id='feed-job',title='Feed role',url='https://feed.example/job',
                            locations=[JobLocation(label='Karlsruhe')])])])
                html_company=Company(source='test',source_id='html',name='HTML Co',
                    career_status='jobs_extracted',latitude=49.0068705,longitude=8.4034195,
                    feeds=[JobFeed(provider='html_jobs',feed_url='https://html.example/careers',status='parsed',
                        jobs=[Job(external_id='html-job',title='HTML role',url='https://html.example/job',
                            locations=[JobLocation(label='Karlsruhe')])])])
                session.add_all([domain_only,career_only,feed_company,html_company])
                session.commit()

                def listed(filter_name):
                    return list_companies(49.0068705,8.4034195,15,None,0,20,session,
                                          discovery=filter_name)

                self.assertEqual({x['name'] for x in listed('domain')['items']}, {'Domain Co','Feed Co'})
                self.assertEqual({x['name'] for x in listed('career')['items']}, {'Career Co','HTML Co'})
                self.assertEqual({x['name'] for x in listed('feed')['items']}, {'Feed Co'})
                self.assertEqual({x['name'] for x in listed('jobs')['items']}, {'Feed Co','HTML Co'})
        finally:
            engine.dispose()

    def test_greenhouse_language_variants_are_one_job_in_location_and_company_views(self):
        engine=create_engine('sqlite://')
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                company=Company(
                    source='openstreetmap',source_id='node/3705530440',
                    name='1&1 Mail & Media GmbH',website_url='https://www.mail-and-media.com/',
                    domain='mail-and-media.com',latitude=48.9995737,longitude=8.3854306,
                )
                german=JobFeed(company=company,provider='greenhouse',tenant='mailmediaportal',
                    feed_url='https://boards-api.greenhouse.io/v1/boards/mailmediaportal/jobs?content=true',
                    status='parsed',job_count=2)
                english=JobFeed(company=company,provider='greenhouse',tenant='mailmediaportal-eng',
                    feed_url='https://boards-api.greenhouse.io/v1/boards/mailmediaportal-eng/jobs?content=true',
                    status='parsed',job_count=1)
                other=JobFeed(company=company,provider='greenhouse',tenant='mailmediaportal',
                    feed_url='https://boards-api.greenhouse.io/v1/boards/mailmediaportal/jobs?content=true&other=1',
                    status='parsed',job_count=1)
                german.jobs.extend([
                    Job(external_id='4928092101',title='Kubernetes Platform Engineer (w/m/d)',
                        url='https://www.mail-and-media.com/jobs/bewerbung?gh_jid=4928092101',
                        raw_metadata={'requisition_id':'363'},
                        locations=[JobLocation(label='Karlsruhe')]),
                    Job(external_id='4399833101',title='Initiativbewerbung Festanstellung',
                        url='https://www.mail-and-media.com/jobs/bewerbung?gh_jid=4399833101',
                        raw_metadata={'requisition_id':'157'},
                        locations=[JobLocation(label='Karlsruhe')]),
                ])
                english.jobs.append(Job(external_id='4928091101',
                    title='Kubernetes Platform Engineer (f/m/d)',
                    url='https://www.mail-and-media.com/en/jobs/application?gh_jid=4928091101',
                    raw_metadata={'requisition_id':'363'},
                    locations=[JobLocation(label='Karlsruhe')]))
                other.jobs.append(Job(external_id='unique-1',title='Independent role',
                    url='https://jobs.example.test/unique-1',raw_metadata={},
                    locations=[JobLocation(label='Karlsruhe')]))
                session.add(company)
                session.commit()

                result=list_jobs(49.0068705,8.4034195,15,None,None,'Karlsruhe',0,20,
                                 work_style='all',sort='relevance',session=session)
                self.assertEqual(result['total'],3)
                self.assertEqual(len(result['items']),3)
                self.assertEqual(sum(item['raw_metadata'].get('requisition_id')=='363' for item in result['items']),1)

                company_list=list_companies(49.0068705,8.4034195,15,None,0,20,session)
                self.assertEqual(company_list['items'][0]['active_job_count'],3)

                detail=get_company(company.id,session)
                self.assertEqual(len(detail['jobs']),3)
                self.assertEqual(sum(job['raw_metadata'].get('requisition_id')=='363' for job in detail['jobs']),1)
        finally:
            engine.dispose()


if __name__=='__main__':
    unittest.main()
