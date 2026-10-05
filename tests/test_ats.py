import json
import unittest
from hiring_scraper.ats import identify, parse_feed

class ProviderTests(unittest.TestCase):
    def test_greenhouse_board_and_company_embedded_widget(self):
        for url in ['https://job-boards.greenhouse.io/acme/jobs/123',
                    'https://boards.greenhouse.io/embed/job_board?for=acme']:
            with self.subTest(url=url):
                result=identify(url)
                self.assertIsNotNone(result)
                self.assertEqual(result['feed_url'], 'https://boards-api.greenhouse.io/v1/boards/acme/jobs')
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
        self.assertEqual(result['jobs'],[{'id':'123','title':'Developer','url':'https://acme.test/job/123','location':'Karlsruhe'}])
        self.assertTrue(result['complete'])

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

    def test_bad_schema_is_not_reported_as_zero_jobs(self):
        for provider,body in [('greenhouse',b'{"error":"bad"}'),('personio',b'<html></html>'),('lever',b'{}'),('ashby',b'{"success":false}')]:
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError): parse_feed(provider,body,'https://example.org')

    def test_vendor_assets_are_not_employer_boards(self):
        for url in ['https://performancemanager5.successfactors.eu/verp/vmod_v1/ui/extlib/jquery.js',
                    'https://career5.successfactors.eu/',
                    'https://acme.softgarden.io/assets/app.js']:
            with self.subTest(url=url): self.assertIsNone(identify(url))

    def test_company_hosted_greenhouse_api_reference_identifies_board(self):
        result=identify('https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true')
        self.assertIsNotNone(result)
        self.assertEqual(result['tenant'],'acme')
        self.assertEqual(result['feed_url'],'https://boards-api.greenhouse.io/v1/boards/acme/jobs')

    def test_ashby_tenant_can_contain_a_dot(self):
        result=identify('https://jobs.ashbyhq.com/ecosia.org')
        self.assertIsNotNone(result)
        self.assertEqual(result['feed_url'],'https://api.ashbyhq.com/posting-api/job-board/ecosia.org')

    def test_vendor_infrastructure_is_not_a_tenant(self):
        for url in ['https://api.recruitee.com/c/acme/offers','https://wd5.myworkdayjobs.com/wday/cxs/a/b',
                    'https://career5.successfactors.eu/?company=acme']:
            with self.subTest(url=url): self.assertIsNone(identify(url))
