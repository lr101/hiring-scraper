import json
import unittest
from hiring_scraper.discovery import discover, trusted_html_jobs, trusted_html_source

class FixtureClient:
    def __init__(self,pages): self.pages=pages
    def get(self,url):
        body=self.pages.get(url)
        return ({'state':'ok','status':200,'final_url':url},body.encode()) if body is not None else ({'state':'http_error','status':404,'final_url':url},b'')

    get_feed = get

class DiscoveryTests(unittest.TestCase):
    def test_company_root_redirect_establishes_canonical_first_party_host(self):
        seed = {'name': 'USU GmbH', 'website': 'https://www.usu.de/'}
        home = {'url': 'https://www.usu.com/', 'requested_url': 'https://www.usu.de/',
                'redirect_chain': ['https://www.usu.de/', 'https://www.usu.com/'],
                'parent': None, 'depth': 0}
        career = {'url': 'https://www.usu.com/jobs', 'parent': 'https://www.usu.com/',
                  'title': 'Jobs bei USU', 'headings': 'Karriere bei USU'}
        self.assertTrue(trusted_html_source(seed, career, [home, career]))

    def test_root_redirect_to_sibling_subdomain_inside_state_domain_is_first_party(self):
        seed = {'name': 'Regierungspräsidium Karlsruhe',
                'website': 'https://rp.baden-wuerttemberg.de/rpk'}
        home = {'url': 'https://rpk.baden-wuerttemberg.de/',
                'requested_url': 'https://rp.baden-wuerttemberg.de/rpk',
                'redirect_chain': ['https://rp.baden-wuerttemberg.de/rpk',
                                   'https://rpk.baden-wuerttemberg.de/'],
                'parent': None, 'depth': 0}
        career = {'url': 'https://rpk.baden-wuerttemberg.de/service/seiten/stellenangebote/',
                  'parent': home['url'], 'title': 'Stellenangebote', 'headings': 'Karriere'}
        self.assertTrue(trusted_html_source(seed, career, [home, career]))

    def test_german_umlaut_and_domain_transliteration_recognizes_company_alias(self):
        seed = {'name': 'Röser Media', 'website': 'https://www.roeser-media.de/'}
        home = {'url': 'https://www.roeser-medienhaus.de/',
                'requested_url': 'https://www.roeser-medienhaus.de/',
                'parent': None, 'depth': 0}
        career = {'url': 'https://www.roeser-medienhaus.de/karriere/stellenangebote/',
                  'parent': home['url'], 'title': 'Röser Medienhaus Karriere',
                  'headings': 'Stellenangebote'}
        self.assertTrue(trusted_html_source(seed, career, [home, career]))

    def test_direct_external_job_board_accepts_only_postings_matching_the_employer(self):
        seed = {'name': 'Verkehrsbetriebe Karlsruhe GmbH', 'website': 'https://www.vbk.info/'}
        home = {'url': 'https://www.vbk.info/', 'parent': None, 'depth': 0}
        linked_board = {'url': 'https://jobs.shared.example/jobs', 'parent': home['url'],
                        'title': 'Jobs', 'headings': 'Offene Stellen'}
        rows = [
            {'title': 'Instandhaltungsplaner', 'raw_metadata': {'hiring_organization': {
                'name': 'VBK Verkehrsbetriebe Karlsruhe', 'url': 'https://www.vbk.info/'}}},
            {'title': 'Triebfahrzeugführerin', 'raw_metadata': {'hiring_organization': {
                'name': 'Albtal-Verkehrs-Gesellschaft mbH', 'url': 'https://www.avg.info/'}}},
            {'title': 'Generic role', 'raw_metadata': {}},
        ]
        accepted, trust = trusted_html_jobs(seed, linked_board, [home, linked_board], rows)
        self.assertEqual(trust, 'linked_external_verified')
        self.assertEqual([job['title'] for job in accepted], ['Instandhaltungsplaner'])

    def test_nested_external_job_detail_can_use_employer_identity_after_company_link(self):
        seed = {'name': 'Verkehrsbetriebe Karlsruhe GmbH', 'website': 'https://www.vbk.info/'}
        home = {'url': 'https://www.vbk.info/', 'parent': None, 'depth': 0}
        portal = {'url': 'https://jobs.shared.example/jobs', 'parent': home['url'], 'depth': 1}
        detail = {'url': 'https://jobs.shared.example/job/vbk', 'parent': portal['url'], 'depth': 2,
                  'title': 'Job details', 'headings': 'Role'}
        rows = [{'title': 'Instandhaltungsplaner', 'raw_metadata': {'hiring_organization': {
            'name': 'Verkehrsbetriebe Karlsruhe GmbH'}}}]
        accepted, trust = trusted_html_jobs(seed, detail, [home, portal, detail], rows)
        self.assertEqual([job['title'] for job in accepted], ['Instandhaltungsplaner'])
        self.assertEqual(trust, 'linked_external_verified')

    def test_external_source_without_company_provenance_or_employer_identity_is_rejected(self):
        seed = {'name': 'Acme GmbH', 'website': 'https://acme.example/'}
        home = {'url': 'https://acme.example/', 'parent': None, 'depth': 0}
        unrelated = {'url': 'https://jobs.example.org/jobs', 'parent': 'https://jobs.other.org/',
                     'title': 'Acme Jobs', 'headings': 'Acme'}
        rows = [{'title': 'Engineer', 'raw_metadata': {}}]
        accepted, trust = trusted_html_jobs(seed, unrelated, [home, unrelated], rows)
        self.assertEqual((accepted, trust), ([], 'unverified_external_source'))

    def test_discovery_recovers_matching_structured_job_from_directly_linked_shared_board(self):
        home = 'https://www.vbk.info/'
        shared = 'https://jobs.shared.example/jobs'
        postings = [
            {'@context': 'https://schema.org', '@type': 'JobPosting',
             'title': 'Instandhaltungsplaner', 'url': 'https://jobs.shared.example/job/vbk',
             'hiringOrganization': {'@type': 'Organization', 'name': 'VBK Verkehrsbetriebe Karlsruhe'}},
            {'@context': 'https://schema.org', '@type': 'JobPosting',
             'title': 'Triebfahrzeugführerin', 'url': 'https://jobs.shared.example/job/avg',
             'hiringOrganization': {'@type': 'Organization', 'name': 'Albtal-Verkehrs-Gesellschaft mbH'}},
        ]
        body = ''.join('<script type="application/ld+json">' + json.dumps(posting) + '</script>'
                       for posting in postings)
        client = FixtureClient({
            home: f'<a href="{shared}">Jobs</a>',
            shared: '<title>Offene Stellen</title><h1>Jobs</h1>' + body,
        })
        result = discover({'name': 'Verkehrsbetriebe Karlsruhe GmbH', 'website': home}, client, max_pages=4)
        board = next(board for board in result['boards'] if board.get('provider') == 'html_jobs')
        self.assertEqual([job['title'] for job in board['jobs']], ['Instandhaltungsplaner'])
        self.assertEqual(board['jobs'][0]['raw_metadata']['hiring_organization']['name'],
                         'VBK Verkehrsbetriebe Karlsruhe')
        shared_page = next(page for page in result['pages'] if page.get('url') == shared)
        self.assertEqual(shared_page['html_extraction_trust'], 'linked_external_verified')

    def test_rejects_generic_external_job_portal(self):
        company_page={'url':'https://gaul-catering.de/karriere','parent':'https://gaul-catering.de/',
                      'title':'Karriere','headings':'Stellenangebote','classification':'career_content'}
        portal_page={'url':'https://www.hogapage.de/jobs/job/','parent':'https://gaul-catering.de/karriere',
                     'title':'Gastronomie Jobs | HOGAPAGE','headings':'Alle Jobs','classification':'career_content'}
        self.assertFalse(trusted_html_source(
            {'name':"Gaul’s Catering GmbH",'website':'https://gaul-catering.de/'}, portal_page,
            [company_page, portal_page]))

    def test_accepts_external_branded_career_site_linked_from_company(self):
        company_page={'url':'https://acme.example/','parent':None,'title':'Acme','headings':'Karriere',
                      'classification':'career_content'}
        career_page={'url':'https://jobs.acme-talent.example/positions','parent':'https://acme.example/',
                     'title':'Acme | Jobs','headings':'Join Acme','classification':'career_content'}
        self.assertTrue(trusted_html_source(
            {'name':'Acme GmbH','website':'https://acme.example/'}, career_page,
            [company_page, career_page]))

    def test_does_not_accept_unlinked_external_page_even_if_branded(self):
        page={'url':'https://jobs.acme-talent.example/positions','parent':None,
              'title':'Acme | Jobs','headings':'Join Acme','classification':'career_content'}
        self.assertFalse(trusted_html_source(
            {'name':'Acme GmbH','website':'https://acme.example/'}, page, [page]))

    def test_external_generic_job_page_is_not_reported_as_employer_career_page(self):
        home='https://gaul-catering.de/'
        portal='https://jobs.example.org/jobs/'
        detail='https://jobs.example.org/jobs/account-manager'
        client=FixtureClient({
            home:f'<a href="{portal}">Offene Stellen</a>',
            portal:('<title>Hospitality Jobs | Example</title><h1>Jobs</h1><h2>Open Positions</h2>'
                    '<article><a href="/jobs/account-manager">Account Manager</a><p>Apply now.</p></article>'),
            detail:'<h1>Account Manager</h1><p>Apply for this position.</p>',
        })
        result=discover({'name':"Gaul's Catering GmbH",'website':home},client,max_pages=3)
        self.assertEqual(result['status'],'unresolved')
        self.assertFalse(any(board.get('provider')=='html_jobs' for board in result['boards']))
        self.assertEqual(result['unverified_external_html_pages'][0]['reason'],'unverified_external_source')

    def test_extracts_jobs_from_ordinary_career_page_html(self):
        home='https://qwertiko.example/'
        careers='https://qwertiko.example/jobs/'
        client=FixtureClient({
            home:'<a href="/jobs/">Jobs</a>',
            careers:('<h1>IT-Jobs in Karlsruhe</h1><h2>Aktuelle Jobs</h2>'
                     '<article><a href="/jobs/senior-linux-administrator">Senior Linux Administrator</a>'
                     '<p>Karlsruhe (Vor-Ort), Vollzeit. Run Linux systems.</p></article>'),
            'https://qwertiko.example/jobs/senior-linux-administrator':
                '<h1>Senior Linux Administrator</h1><p>Manage servers and Kubernetes.</p>',
        })
        result=discover({'name':'Qwertiko','website':home},client,max_pages=3)
        source=next(board for board in result['boards'] if board['provider']=='html_jobs')
        self.assertEqual(result['status'],'jobs_extracted')
        self.assertEqual(source['feed_url'],careers)
        self.assertEqual(source['job_count'],1)
        self.assertEqual(source['jobs'][0]['title'],'Senior Linux Administrator')
        self.assertEqual(source['jobs'][0]['location'],'Karlsruhe')

    def test_follows_company_page_to_greenhouse_embed_and_feed(self):
        client=FixtureClient({
            'https://acme.de/':'<a href="/karriere">Karriere</a>',
            'https://acme.de/karriere':'<title>Karriere</title><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>',
            'https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true':'{"jobs":[{"id":1,"title":"Dev","absolute_url":"https://acme.de/job/1","location":{"name":"Karlsruhe"}}],"meta":{"total":1}}'
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

    def test_low_confidence_about_link_does_not_spend_career_page_budget(self):
        client=FixtureClient({
            'https://acme.de/':'<a href="/about">About</a><a href="/careers">Careers</a>',
            'https://acme.de/careers':'<title>Careers</title><h1>Careers</h1><p>Apply now.</p>'
        })
        result=discover({'name':'Acme','website':'https://acme.de/'},client,max_pages=3)
        self.assertIn('https://acme.de/careers',[p['url'] for p in result['pages']])
        self.assertNotIn('https://acme.de/about',[p['url'] for p in result['pages']])

    def test_redirect_aliases_do_not_count_the_same_final_page_twice(self):
        class RedirectFixtureClient(FixtureClient):
            def get(self,url):
                replies={
                    'https://acme.de/':('https://acme.de/','<a href="/careers">Careers</a><a href="/jobs">Jobs</a>'),
                    'https://acme.de/careers':('https://acme.de/jobs/','<title>Jobs</title><h1>Jobs</h1><p>Apply now.</p>'),
                    'https://acme.de/jobs':('https://acme.de/jobs/','<title>Jobs</title><h1>Jobs</h1><p>Apply now.</p>')
                }
                if url not in replies:
                    return {'state':'http_error','status':404,'final_url':url},b''
                final,body=replies[url]
                return {'state':'ok','status':200,'final_url':final},body.encode()

        result=discover({'name':'Acme','website':'https://acme.de/'},RedirectFixtureClient({}),max_pages=10)
        successful=[p['url'] for p in result['pages'] if p['fetch_state']=='ok']
        self.assertEqual(successful.count('https://acme.de/jobs/'),1)

    def test_company_greenhouse_application_link_is_followed_for_board_evidence(self):
        apply_url='https://acme.de/apply?gh_jid=1234567'
        board='https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true'
        client=FixtureClient({
            'https://acme.de/':'<a href="/apply?gh_jid=1234567">Apply</a>',
            apply_url:'<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>',
            board:'{"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://acme.de/job/1","location":{"name":"Karlsruhe"}}],"meta":{"total":1}}'
        })
        result=discover({'name':'Acme','website':'https://acme.de/'},client,max_pages=4)
        self.assertEqual(result['status'],'jobs_feed_found')
        greenhouse=next(b for b in result['boards'] if b['feed_state']=='parsed')
        self.assertEqual(greenhouse['tenant'],'acme')
        self.assertEqual(greenhouse['job_count'],1)
        self.assertEqual(greenhouse['discovered_on'],apply_url)

    def test_mail_and_media_german_apply_route_reveals_eu_greenhouse_feed(self):
        home='https://www.mail-and-media.com/jobs/'
        job='https://www.mail-and-media.com/job/de-363/'
        apply='https://www.mail-and-media.com/jobs/bewerbung/?gh_jid=4928092101'
        board_script='https://boards.eu.greenhouse.io/embed/job_board/js?for=mailmediaportal'
        feed='https://boards-api.greenhouse.io/v1/boards/mailmediaportal/jobs?content=true'
        client=FixtureClient({
            home:'<a href="/job/de-363/">Kubernetes Platform Engineer</a>',
            job:f'<a href="{apply}">jetzt bewerben</a>',
            apply:f'<script src="{board_script}"></script>',
            feed:'{"jobs":[{"id":4928092101,"requisition_id":"363","title":"Kubernetes Platform Engineer (w/m/d)","absolute_url":"https://www.mail-and-media.com/jobs/bewerbung?gh_jid=4928092101","location":{"name":"Karlsruhe"},"offices":[{"name":"Karlsruhe"}]}],"meta":{"total":1}}',
        })
        result=discover({'name':'1&1 Mail & Media GmbH','website':home},client,max_pages=6)
        board=next(b for b in result['boards'] if b.get('tenant')=='mailmediaportal')
        self.assertEqual(result['status'],'jobs_feed_found')
        self.assertEqual(board['feed_url'],feed)
        self.assertEqual(board['job_count'],1)
        self.assertEqual(board['jobs'][0]['raw_metadata']['requisition_id'],'363')

    def test_job_detail_paths_outrank_career_brand_pages(self):
        home='https://acme.de/'
        careers='https://acme.de/jobs/'
        job='https://acme.de/job/de-363/'
        apply='https://acme.de/jobs/bewerbung/?gh_jid=4928092101'
        board='https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true'
        client=FixtureClient({
            home:'<a href="/jobs/">Jobs</a><a href="/jobs/arbeitswelt/">Arbeitswelt</a>',
            careers:'<h1>Jobs</h1><a href="/jobs/arbeitswelt/">Arbeitswelt</a><a href="/job/de-363/">Kubernetes Platform Engineer (w/m/d)</a>',
            'https://acme.de/jobs/arbeitswelt/':'<h1>Arbeitswelt</h1><p>Learn about our culture and benefits.</p>',
            job:f'<a href="{apply}">Jetzt bewerben</a>',
            apply:'<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=acme"></script>',
            board:'{"jobs":[{"id":4928092101,"requisition_id":"363","title":"Kubernetes Platform Engineer","absolute_url":"https://acme.de/jobs/bewerbung?gh_jid=4928092101","location":{"name":"Karlsruhe"}}]}',
        })
        result=discover({'name':'Acme','website':home},client,max_pages=4)
        self.assertEqual(result['status'],'jobs_feed_found')
        greenhouse=next(b for b in result['boards'] if b.get('tenant')=='acme')
        self.assertEqual(greenhouse['feed_state'],'parsed')

    def test_http_osm_seed_prefers_same_host_https_when_accessible(self):
        class TrackingFixtureClient(FixtureClient):
            def __init__(self,pages):
                super().__init__(pages)
                self.requests=[]
            def get(self,url):
                self.requests.append(url)
                return super().get(url)

        home='https://acme.de/'
        feed='https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true'
        client=TrackingFixtureClient({
            home:'<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>',
            feed:'{"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://acme.de/job/1","location":{"name":"Karlsruhe"}}]}',
        })
        result=discover({'name':'Acme','website':'http://acme.de/'},client,max_pages=2)
        self.assertEqual(result['status'],'jobs_feed_found')
        self.assertEqual(result['pages'][0]['url'],home)
        self.assertNotIn('http://acme.de/',client.requests)

    def test_http_seed_checks_www_robots_when_apex_robots_is_unavailable(self):
        class CanonicalFixtureClient(FixtureClient):
            def __init__(self,pages):
                super().__init__(pages)
                self.requests=[]
            def get(self,url):
                self.requests.append(url)
                if url=='https://acme.de/':
                    return {'state':'robots_unavailable','final_url':url},b''
                return super().get(url)

        home='https://www.acme.de/'
        feed='https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true'
        client=CanonicalFixtureClient({
            home:'<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>',
            feed:'{"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://www.acme.de/job/1","location":{"name":"Karlsruhe"}}]}',
        })
        result=discover({'name':'Acme','website':'http://acme.de/'},client,max_pages=2)
        self.assertEqual(result['status'],'jobs_feed_found')
        self.assertIn(home,client.requests)
        self.assertEqual(result['pages'][0]['url'],home)

    def test_http_seed_does_not_try_www_after_an_explicit_robots_block(self):
        class BlockedFixtureClient(FixtureClient):
            def __init__(self):
                super().__init__({})
                self.requests=[]
            def get(self,url):
                self.requests.append(url)
                return {'state':'robots_disallowed','final_url':url},b''

        client=BlockedFixtureClient()
        result=discover({'name':'Acme','website':'http://acme.de/'},client,max_pages=2)
        self.assertEqual(result['pages'][0]['fetch_state'],'robots_disallowed')
        self.assertFalse(any(url.startswith('https://www.acme.de/') for url in client.requests))

    def test_external_official_job_portal_outranks_employer_brand_pages(self):
        home='https://company.de/'
        careers='https://company.de/karriere/'
        portal='https://group.example/jobs/jobsuche.html'
        feed='https://boards-api.greenhouse.io/v1/boards/groupco/jobs?content=true'
        client=FixtureClient({
            home:'<a href="/karriere/">Karriere</a>',
            careers:(f'<a href="/karriere/benefits/">Unsere Arbeitswelt</a>'
                     f'<a href="{portal}">Hier findest Du die aktuellen Stellenausschreibungen</a>'),
            portal:'<iframe src="https://boards.greenhouse.io/embed/job_board?for=groupco"></iframe>',
            feed:'{"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://company.de/job/1","location":{"name":"Karlsruhe"}}]}',
        })
        result=discover({'name':'Company','website':home},client,max_pages=3)
        self.assertEqual(result['status'],'jobs_feed_found')
        self.assertEqual(result['pages'][-1]['url'],portal)

    def test_company_jobs_portal_outranks_external_group_jobs(self):
        home='https://www.ionos.de/'
        career='https://jobs.ionos.de/karriere/wer-wir-sind'
        company_jobs='https://jobs.ionos.de/karriere/jobs/alle-jobs'
        group_jobs='https://www.united-internet.de/jobs-karriere/stellenangebote.html'
        ionos_job='https://jobs.ionos.de/karriere/jobangebot/4979230101'
        greenhouse='https://job-boards.eu.greenhouse.io/ionos/jobs/4979230101'
        feed='https://boards-api.greenhouse.io/v1/boards/ionos/jobs?content=true'
        client=FixtureClient({
            home:f'<a href="{career}">Jobs</a>',
            career:(f'<a href="{group_jobs}">Stellenangebote der United Internet AG anzeigen</a>'
                    '<a href="https://jobs.ionos.de/karriere/jobs/faq">FAQ</a>'
                    '<a href="https://jobs.ionos.de/karriere/ihre-karriere">Ihre Karriere</a>'
                    f'<a href="{company_jobs}">Jobs</a>'),
            group_jobs:'<h1>United Internet jobs</h1><p>Group careers listing.</p>',
            company_jobs:f'<a href="{ionos_job}">Cyber Security Engineer Karlsruhe</a>',
            ionos_job:f'<a href="{greenhouse}">Jetzt online bewerben</a>',
            feed:'{"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://job-boards.eu.greenhouse.io/ionos/jobs/1","location":{"name":"Karlsruhe"}}]}',
        })
        result=discover({'name':'IONOS','website':home},client,max_pages=4)
        self.assertEqual(result['status'],'jobs_feed_found')
        self.assertEqual(result['pages'][2]['url'],company_jobs)
        self.assertEqual(result['boards'][0]['tenant'],'ionos')

    def test_explicit_ats_application_evidence_can_extend_one_level_past_site_depth(self):
        home='https://uim.de/'
        careers='https://uim.de/karriere/'
        portal='https://group.example/jobs/jobsuche.html'
        job='https://group.example/job/de-1/'
        apply='https://group.example/jobs/bewerbung/?gh_jid=1234567'
        feed='https://boards-api.greenhouse.io/v1/boards/groupco/jobs?content=true'
        client=FixtureClient({
            home:f'<a href="{careers}">Karriere</a>',
            careers:f'<a href="{portal}">Aktuelle Stellenausschreibungen</a>',
            portal:f'<a href="{job}">Engineer</a>',
            job:f'<a href="{apply}">Jetzt bewerben</a>',
            apply:'<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=groupco"></script>',
            feed:'{"jobs":[{"id":1,"title":"Engineer","absolute_url":"https://group.example/job/1","location":{"name":"Karlsruhe"}}]}',
        })
        result=discover({'name':'UIM','website':home},client,max_pages=5,max_depth=3)
        self.assertEqual(result['status'],'jobs_feed_found')

    def test_linked_schema_org_jobs_feed_is_fetched_and_parsed(self):
        feed='https://careers.acme.de/jobs.feed.json'
        body='{"@context":"https://schema.org","@type":"DataFeed","numberOfItems":1,"dataFeedElement":[{"@type":"DataFeedItem","item":{"@type":"JobPosting","title":"Engineer","url":"https://careers.acme.de/jobs/1","identifier":{"value":1},"jobLocation":{"address":{"addressLocality":"Karlsruhe"}}}}]}'
        client=FixtureClient({
            'https://acme.de/':'<a href="/karriere">Karriere</a>',
            'https://acme.de/karriere':f'<a href="{feed}">Machine-readable job feed (JSON)</a>',
            feed:body
        })
        result=discover({'name':'Acme','website':'https://acme.de/'},client,max_pages=4)
        self.assertEqual(result['status'],'jobs_feed_found')
        board=next(b for b in result['boards'] if b['provider']=='schema_org')
        self.assertEqual(board['job_count'],1)
        self.assertEqual(board['jobs'][0]['location'],'Karlsruhe')
        self.assertEqual(board['evidence_url'],feed)
