import unittest
from hiring_scraper.pages import inspect_page

class PageTests(unittest.TestCase):
    def test_rank_career_link_above_about_and_ignore_empty_fragment_social(self):
        result=inspect_page('https://acme.de/', '<a href="">Karriere</a><a href="#jobs">Jobs</a><a href="https://instagram.com/acme">Career</a><a href="/unternehmen">Unternehmen</a><a href="/karriere?utm_source=x">Karriere</a>')
        self.assertEqual([x['url'] for x in result['candidates']],['https://acme.de/karriere','https://acme.de/unternehmen'])

    def test_greenhouse_embedded_on_company_domain(self):
        result=inspect_page('https://acme.de/karriere','<title>Karriere bei Acme</title><h1>Offene Stellen</h1><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>')
        self.assertEqual(result['classification'],'career_content')
        self.assertEqual(len(result['ats']),1)
        self.assertEqual(result['ats'][0]['provider'],'greenhouse')
        self.assertEqual(result['ats'][0]['evidence_kind'],'iframe')

    def test_script_configuration_finds_personio_without_rendering(self):
        result=inspect_page('https://acme.de/jobs','<script>window.config={"url":"https:\\/\\/acme.jobs.personio.de\\/xml"};</script>')
        self.assertEqual(len(result['ats']),1)
        self.assertEqual(result['ats'][0]['provider'],'personio')
        self.assertEqual(result['ats'][0]['evidence_kind'],'script_reference')

    def test_navigation_alone_is_not_a_career_page(self):
        result=inspect_page('https://acme.de/','<title>Acme products</title><nav><a href="/jobs">Jobs</a></nav><h1>Buy our products</h1>')
        self.assertEqual(result['classification'],'ordinary_page')

    def test_placeholder_is_not_a_useful_career_page(self):
        result=inspect_page('https://acme.de/stellenangebote','<title>Stellenangebote</title><h1>Stellenangebote</h1><p>Your content goes here. Edit or remove this text.</p>')
        self.assertEqual(result['classification'],'placeholder')

    def test_structured_jobposting_graph_is_recognized(self):
        result=inspect_page('https://acme.de/position/12','<script type="application/ld+json">{"@graph":[{"@type":"Organization"},{"@type":"JobPosting","title":"Engineer"}]}</script>')
        self.assertEqual(result['jobposting_count'],1)
        self.assertEqual(result['classification'],'jobposting')

    def test_homepage_career_teaser_is_not_the_career_page(self):
        result=inspect_page('https://acme.de/','<title>Acme IT Services</title><h1>IT for your business</h1><h2>Karriere</h2><p>Unsere Mitarbeiter. Jetzt bewerben!</p><a href="/karriere">Karriere</a>')
        self.assertEqual(result['classification'],'ordinary_page')

    def test_company_csp_personio_hint_is_distinguished_from_a_link(self):
        result=inspect_page('https://careers.acme.de/','<meta http-equiv="Content-Security-Policy" content="connect-src https: acme.jobs.personio.de;"><title>Careers</title>')
        self.assertEqual(len(result['ats']),1)
        self.assertEqual(result['ats'][0]['evidence_kind'],'csp_hint')

    def test_script_mention_of_detection_only_vendor_is_not_a_board(self):
        result=inspect_page('https://acme.de/','<script>let analytics="https://api.recruitee.com/metrics";</script>')
        self.assertEqual(result['ats'],[])
