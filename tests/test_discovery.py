import unittest
from hiring_scraper.discovery import discover

class FixtureClient:
    def __init__(self,pages): self.pages=pages
    def get(self,url):
        body=self.pages.get(url)
        return ({'state':'ok','status':200,'final_url':url},body.encode()) if body is not None else ({'state':'http_error','status':404,'final_url':url},b'')

    get_feed = get

class DiscoveryTests(unittest.TestCase):
    def test_follows_company_page_to_greenhouse_embed_and_feed(self):
        client=FixtureClient({
            'https://acme.de/':'<a href="/karriere">Karriere</a>',
            'https://acme.de/karriere':'<title>Karriere</title><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>',
            'https://boards-api.greenhouse.io/v1/boards/acme/jobs':'{"jobs":[{"id":1,"title":"Dev","absolute_url":"https://acme.de/job/1","location":{"name":"Karlsruhe"}}],"meta":{"total":1}}'
        })
        result=discover({'name':'Acme','website':'https://acme.de/'},client,max_pages=3)
        self.assertEqual(result['status'],'jobs_feed_found')
        self.assertEqual(len(result['boards']),1)
        self.assertEqual(result['boards'][0]['discovered_on'],'https://acme.de/karriere')
        self.assertEqual(result['boards'][0]['job_count'],1)

    def test_blocked_feed_keeps_evidence_but_not_job_count(self):
        client=FixtureClient({'https://acme.de/':'<a href="https://jobs.lever.co/acme">Careers</a>'})
        result=discover({'name':'Acme','website':'https://acme.de/'},client,max_pages=2)
        self.assertEqual(result['status'],'ats_identified')
        self.assertIsNone(result['boards'][0]['job_count'])

    def test_page_budget_bounds_fallback_probing(self):
        result=discover({'name':'Acme','website':'https://acme.de/'},FixtureClient({'https://acme.de/':'<h1>Welcome</h1>'}),max_pages=2)
        self.assertEqual(len(result['pages']),2)
        self.assertEqual(result['status'],'unresolved')
