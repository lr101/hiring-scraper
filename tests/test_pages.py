import unittest
from hiring_scraper.pages import inspect_page

class PageTests(unittest.TestCase):
    def test_rank_career_link_above_about_and_ignore_empty_fragment_social(self):
        result=inspect_page('https://acme.de/', '<a href="">Karriere</a><a href="#jobs">Jobs</a><a href="https://instagram.com/acme">Career</a><a href="/unternehmen">Unternehmen</a><a href="/karriere?utm_source=x">Karriere</a>')
        self.assertEqual([x['url'] for x in result['candidates']],['https://acme.de/karriere','https://acme.de/unternehmen'])

    def test_malformed_link_without_href_value_is_ignored(self):
        result=inspect_page('https://acme.de/','<title>Acme</title><h1>Products</h1><a href>Career</a>')
        self.assertEqual(result['candidates'],[])

    def test_url_like_regex_in_script_is_ignored(self):
        result=inspect_page('https://acme.de/',
            '<title>Acme</title><script>const pattern = "https://[a-zA-Z0-9-]+";</script>')
        self.assertEqual(result['ats'],[])

    def test_career_word_in_hostname_does_not_rank_privacy_links(self):
        result=inspect_page('https://careers.acme.de/','<a href="https://careers.acme.de/privacy-policy/">Privacy Policy</a><a href="https://careers.acme.de/jobs/">Open roles</a>')
        self.assertEqual([x['url'] for x in result['candidates']],['https://careers.acme.de/jobs/'])

    def test_privacy_page_on_career_subdomain_is_not_career_content(self):
        result=inspect_page('https://careers.acme.de/privacy-policy/','<title>Careers at Acme | Privacy Policy</title><h1>Privacy Policy</h1><nav><a href="/jobs/">Apply</a></nav><p>How we use personal data.</p>')
        self.assertEqual(result['classification'],'ordinary_page')

    def test_news_article_about_employers_is_not_a_career_page(self):
        result=inspect_page('https://acme.de/aktuelles-publikationen/work-location-risks-for-employers/',
            '<title>Legal risks for employers – Acme</title><h1>Legal risks for employers</h1><p>Advice for employers about Mitarbeiter, Bewerbungen, work location and Benefits.</p>')
        self.assertEqual(result['classification'],'ordinary_page')

    def test_german_employer_page_with_job_offers_is_career_content(self):
        result=inspect_page('https://acme.de/de/arbeitgeber','<title>Arbeitgeber</title><h1>Durchstarten bei Acme</h1><p>Entdecke unsere aktuellen Jobangebote. Bewirb dich jetzt.</p>')
        self.assertEqual(result['classification'],'career_content')

    def test_english_talent_wording_and_student_role_are_career_content(self):
        result=inspect_page('https://easysg.de/about-us/careers/',
            '<title>Careers | Easy Smart Grid</title><h1>Careers</h1>'
            '<h2>Easy Smart Grid looks for talents!</h2>'
            '<p>Working student | Master Thesis - Embedded Systems Engineering (m/w)</p>')
        self.assertEqual(result['classification'],'career_content')

    def test_employer_link_is_a_high_priority_career_candidate(self):
        result=inspect_page('https://acme.de/de','<a href="/de/arbeitgeber">Arbeitgeber</a>')
        self.assertEqual([x['url'] for x in result['candidates']],['https://acme.de/de/arbeitgeber'])

    def test_german_jobangebot_detail_path_is_ranked_as_a_job(self):
        result=inspect_page('https://jobs.acme.de/karriere/jobs/alle-jobs',
            '<a href="/karriere/jobangebot/4979230101-cyber-security">Cyber Security Engineer Karlsruhe</a>')
        self.assertEqual(result['candidates'][0]['score'],92)

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
