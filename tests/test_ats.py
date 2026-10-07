import json
import unittest
from hiring_scraper.ats import identify, parse_feed

class ProviderTests(unittest.TestCase):
    def test_remote_office_scope_is_preserved_without_hybrid_or_homeoffice_conflation(self):
        for office, expected in [('Germany (Remote)', 'remote'), ('Germany (Hybrid, remote options)', 'hybrid'), ('Germany (home office available)', None)]:
            with self.subTest(office=office):
                row = {'id': 1, 'title': 'Engineer', 'absolute_url': 'https://example.test/jobs/1', 'location': {'name': office}, 'offices': [{'name': office, 'country': 'Germany'}]}
                job = parse_feed('greenhouse', json.dumps({'jobs': [row]}), 'https://example.test')['jobs'][0]
                self.assertEqual(job.get('work_arrangement'), expected)
                self.assertEqual(job['raw_metadata']['offices'], [{'name': office, 'country': 'Germany'}])
                self.assertEqual(job['locations'][0]['country_code'], 'DE')

    def test_greenhouse_city_primary_is_not_upgraded_by_other_remote_office(self):
        row = {'id': 1, 'title': 'Engineer', 'absolute_url': 'https://example.test/jobs/1', 'location': {'name': 'Berlin'}, 'offices': [{'name': 'United States (Remote)', 'country': 'US'}]}
        job = parse_feed('greenhouse', json.dumps({'jobs': [row]}), 'https://example.test')['jobs'][0]
        self.assertIsNone(job.get('work_arrangement'))
        self.assertIsNone(job.get('is_remote'))
        self.assertNotIn('remote_country_codes', job['raw_metadata'])
        self.assertEqual(job['raw_metadata']['offices'], row['offices'])
        self.assertIn({'label': 'Berlin'}, job['locations'])
        row['location']['name'] = 'Germany (Remote)'
        job = parse_feed('greenhouse', json.dumps({'jobs': [row]}), 'https://example.test')['jobs'][0]
        self.assertEqual(job['work_arrangement'], 'remote')
        self.assertTrue(job['is_remote'])
        self.assertEqual(job['raw_metadata']['remote_country_codes'], ['DE'])

    def test_greenhouse_explicit_remote_location_preserves_country_scope(self):
        row = {'id': 1, 'title': 'Engineer', 'absolute_url': 'https://example.test/jobs/1', 'location': {'name': 'Germany (Remote); Netherlands (Remote)'}, 'offices': [{'name': 'United Kingdom (Remote)'}]}
        job = parse_feed('greenhouse', json.dumps({'jobs': [row]}), 'https://example.test')['jobs'][0]
        self.assertEqual(job.get('raw_metadata', {}).get('remote_country_codes'), ['DE', 'NL'])
        self.assertEqual(job['raw_metadata']['remote_scope_source'], 'greenhouse_location')
        row['location']['name'] = 'Heidelberg (Hybrid)'
        job = parse_feed('greenhouse', json.dumps({'jobs': [row]}), 'https://example.test')['jobs'][0]
        self.assertEqual(job['work_arrangement'], 'hybrid')
        self.assertNotIn('remote_country_codes', job['raw_metadata'])

    def test_personio_preserves_explicit_remote_office_country(self):
        body = '<workzag-jobs><position><id>7</id><name>Engineer</name><office>Germany (Remote)</office><country>DE</country></position></workzag-jobs>'
        job = parse_feed('personio', body, 'https://example.jobs.personio.com')['jobs'][0]
        self.assertEqual(job.get('raw_metadata', {}).get('office'), 'Germany (Remote)')
        self.assertEqual(job['raw_metadata']['country'], 'DE')
        self.assertEqual(job['locations'], [{'label': 'Germany (Remote)', 'country_code': 'DE'}])
        self.assertTrue(job['is_remote'])

    def test_greenhouse_board_and_company_embedded_widget(self):
        for url in ['https://job-boards.greenhouse.io/acme/jobs/123',
                    'https://boards.greenhouse.io/embed/job_board?for=acme']:
            with self.subTest(url=url):
                result=identify(url)
                self.assertIsNotNone(result)
                self.assertEqual(result['feed_url'], 'https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true')
                self.assertEqual(result['provider'], 'greenhouse')

    def test_lever_europe_and_personio_keep_region(self):
        for url,expected in [
            ('https://jobs.eu.lever.co/acme/123','https://api.eu.lever.co/v0/postings/acme?mode=json&limit=100'),
            ('https://acme.jobs.personio.de/job/123','https://acme.jobs.personio.de/xml'),
            ('https://jobs.ashbyhq.com/acme/123','https://api.ashbyhq.com/posting-api/job-board/acme')]:
            with self.subTest(url=url):
                self.assertIsNotNone(identify(url))
                self.assertEqual(identify(url)['feed_url'], expected)

    def test_lookalike_hosts_and_vendor_marketing_are_not_employer_boards(self):
        for url in ['https://greenhouse.io.evil.example/acme',
                    'https://www.greenhouse.com/', 'https://boards.greenhouse.io/',
                    'https://jobs.lever.co/', 'https://jobs.ashbyhq.com/',
                    'https://jobs.personio.de/', 'https://boards.greenhouse.io/embed/job_board']:
            with self.subTest(url=url): self.assertIsNone(identify(url))

    def test_detection_only_provider_has_no_invented_feed(self):
        result=identify('https://acme.wd3.myworkdayjobs.com/en-US/External')
        self.assertIsNotNone(result)
        self.assertEqual(result['provider'],'workday')
        self.assertIsNone(result['feed_url'])

    def test_greenhouse_jobs_normalize_id_title_and_location(self):
        body=json.dumps({'jobs':[{'id':123,'title':'Developer','absolute_url':'https://acme.test/job/123','location':{'name':'Karlsruhe'}}],'meta':{'total':1}}).encode()
        result=parse_feed('greenhouse',body,'https://job-boards.greenhouse.io/acme')
        self.assertIsNotNone(result)
        self.assertEqual(result['jobs'],[{
            'id':'123','title':'Developer','url':'https://acme.test/job/123',
            'location':'Karlsruhe','locations':[{'label':'Karlsruhe'}],
        }])
        self.assertTrue(result['complete'])

    def test_greenhouse_eu_embed_script_identifies_board_tenant(self):
        result=identify('https://boards.eu.greenhouse.io/embed/job_board/js?for=mailmediaportal')
        self.assertEqual(result,{
            'provider':'greenhouse',
            'tenant':'mailmediaportal',
            'board_url':'https://job-boards.greenhouse.io/mailmediaportal',
            'feed_url':'https://boards-api.greenhouse.io/v1/boards/mailmediaportal/jobs?content=true',
        })

    def test_greenhouse_eu_job_board_link_identifies_board_tenant(self):
        result=identify('https://job-boards.eu.greenhouse.io/ionos/jobs/4979230101')
        self.assertEqual(result,{
            'provider':'greenhouse',
            'tenant':'ionos',
            'board_url':'https://job-boards.eu.greenhouse.io/ionos',
            'feed_url':'https://boards-api.greenhouse.io/v1/boards/ionos/jobs?content=true',
        })

    def test_german_company_hosted_greenhouse_application_is_recognized(self):
        result=identify('https://www.mail-and-media.com/jobs/bewerbung/?gh_jid=4928092101')
        self.assertEqual(result['provider'],'greenhouse')
        self.assertIsNone(result['tenant'])
        self.assertIsNone(result['feed_url'])

    def test_greenhouse_parser_preserves_requisition_and_office_locations(self):
        body=json.dumps({'jobs':[{
            'id':4928092101,'requisition_id':'363','title':'Kubernetes Platform Engineer (w/m/d)',
            'absolute_url':'https://www.mail-and-media.com/jobs/bewerbung?gh_jid=4928092101',
            'location':{'name':'Karlsruhe, Munich, Berlin'},
            'offices':[{'name':'Karlsruhe'},{'name':'Munich'},{'name':'Berlin'}],
        }]}).encode()
        result=parse_feed('greenhouse',body,'https://job-boards.greenhouse.io/mailmediaportal')
        job=result['jobs'][0]
        self.assertEqual(job['raw_metadata']['requisition_id'],'363')
        self.assertEqual(job['locations'],[
            {'label':'Karlsruhe'},{'label':'Munich'},{'label':'Berlin'},
        ])

    def test_greenhouse_german_postcode_address_exposes_city_for_location_filter(self):
        body=json.dumps({'jobs':[{
            'id':4789623101,'requisition_id':'1434','title':'AI Developer',
            'absolute_url':'https://job-boards.eu.greenhouse.io/ionos/jobs/4789623101',
            'location':{'name':'Hinterm Hauptbahnhof 3-5, 76137 Karlsruhe'},
            'offices':[{'id':4014500101,'name':'IONOS S.R.L.','location':None}],
        }]}).encode()
        job=parse_feed('greenhouse',body,'https://job-boards.eu.greenhouse.io/ionos')['jobs'][0]
        self.assertEqual(job['location'],'Hinterm Hauptbahnhof 3-5, 76137 Karlsruhe')
        self.assertIn({'label':'Karlsruhe'},job['locations'])

    def test_lever_page_at_limit_is_explicitly_incomplete(self):
        body=json.dumps([{'id':str(i),'text':'Engineer','hostedUrl':f'https://jobs.lever.co/acme/{i}','categories':{'location':'Berlin'}} for i in range(100)]).encode()
        result=parse_feed('lever',body,'https://jobs.lever.co/acme')
        self.assertIsNotNone(result)
        self.assertEqual(len(result['jobs']),100)
        self.assertFalse(result['complete'])

    def test_personio_and_ashby_jobs(self):
        xml=b'<workzag-jobs><position><id>42</id><name>Engineer</name><office>Karlsruhe</office></position></workzag-jobs>'
        result=parse_feed('personio',xml,'https://acme.jobs.personio.de/')
        self.assertIsNotNone(result)
        self.assertEqual(result['jobs'][0]['url'],'https://acme.jobs.personio.de/job/42')
        result=parse_feed('ashby',b'{"jobs":[{"id":"a","title":"Dev","jobUrl":"https://jobs.ashbyhq.com/acme/a","location":"Berlin","isListed":true}]}','https://jobs.ashbyhq.com/acme')
        self.assertIsNotNone(result)
        self.assertEqual(result['jobs'][0]['location'],'Berlin')

    def test_schema_org_datafeed_normalizes_nested_job_postings(self):
        body=json.dumps({'@context':'https://schema.org','@type':'DataFeed','numberOfItems':1,'dataFeedElement':[
            {'@type':'DataFeedItem','item':{'@type':'JobPosting','title':'SAP Administrator','url':'https://careers.acme.de/jobs/42',
                'datePosted':'2026-09-10','identifier':{'@type':'PropertyValue','value':42},
                'jobLocation':{'@type':'Place','address':{'@type':'PostalAddress','addressLocality':'Karlsruhe','addressCountry':'Deutschland'}}}}
        ]}).encode()
        result=parse_feed('schema_org',body,'https://careers.acme.de/jobs.feed.json')
        self.assertEqual(result['jobs'],[{'id':'42','title':'SAP Administrator','url':'https://careers.acme.de/jobs/42','location':'Karlsruhe','date_posted':'2026-09-10','locations':[{'label':'Karlsruhe','country_code':'DE'}]}])
        self.assertTrue(result['complete'])

    def test_schema_org_datafeed_accepts_a_single_feed_element(self):
        body=json.dumps({'@context':'https://schema.org','@type':'DataFeed','numberOfItems':1,
            'dataFeedElement':{'@type':'JobPosting','title':'Engineer','url':'https://careers.acme.de/jobs/1',
                'identifier':'job-1','jobLocation':{'address':{'addressLocality':'Karlsruhe'}}}}).encode()
        result=parse_feed('schema_org',body,'https://careers.acme.de/jobs.feed.json')
        self.assertEqual(result['jobs'][0]['id'],'job-1')
        self.assertTrue(result['complete'])

    def test_bad_schema_is_not_reported_as_zero_jobs(self):
        for provider,body in [('greenhouse',b'{"error":"bad"}'),('personio',b'<html></html>'),('lever',b'{}'),('ashby',b'{"success":false}')]:
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError): parse_feed(provider,body,'https://example.org')

    def test_vendor_assets_are_not_employer_boards(self):
        for url in ['https://performancemanager5.successfactors.eu/verp/vmod_v1/ui/extlib/jquery.js',
                    'https://career5.successfactors.eu/',
                    'https://acme.softgarden.io/assets/app.js',
                    'https://certificate.softgarden.io/acme',
                    'https://acme.softgarden.io/imprint',
                    'https://acme.softgarden.io/data-security']:
            with self.subTest(url=url): self.assertIsNone(identify(url))

    def test_company_hosted_greenhouse_api_reference_identifies_board(self):
        result=identify('https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true')
        self.assertIsNotNone(result)
        self.assertEqual(result['tenant'],'acme')
        self.assertEqual(result['feed_url'],'https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true')

    def test_ashby_tenant_can_contain_a_dot(self):
        result=identify('https://jobs.ashbyhq.com/ecosia.org')
        self.assertIsNotNone(result)
        self.assertEqual(result['feed_url'],'https://api.ashbyhq.com/posting-api/job-board/ecosia.org')

    def test_vendor_infrastructure_is_not_a_tenant(self):
        for url in ['https://api.recruitee.com/c/acme/offers','https://wd5.myworkdayjobs.com/wday/cxs/a/b',
                    'https://career5.successfactors.eu/?company=acme']:
            with self.subTest(url=url): self.assertIsNone(identify(url))
