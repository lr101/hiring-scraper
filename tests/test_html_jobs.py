import json
import unittest

from hiring_scraper.ats import parse_feed
from hiring_scraper.html_jobs import _merge, extract_html_jobs, html_job_key


class HtmlJobExtractionTests(unittest.TestCase):
    def test_department_navigation_is_not_a_job_and_keeps_real_sibling(self):
        departments = ''.join('<div cc-gh-id="department-item" role="listitem"><a cc-t-item="careers_deparment"><h3>' + name + '</h3></a></div>' for name in ('Data &amp; Analytics', 'Engineering', 'Executive Leadership', 'General &amp; Administration'))
        body = '<h2>Open positions</h2>' + departments
        self.assertEqual(extract_html_jobs(body + '<h2>Our culture</h2>', 'https://example.test/careers/')['jobs'], [])
        result = extract_html_jobs(body + '<section><h3>Data Analyst</h3><p>Build analytics workflows.</p></section>', 'https://example.test/careers/')
        self.assertEqual([job['title'] for job in result['jobs']], ['Data Analyst'])

    def test_confirmed_single_vacancy_scopes_description_and_deadline(self):
        body = '<p>Previous staff biography and software engineer.</p><article class="vacancy"><a href="https://apply.example/job/7">Apply</a><h3>Application deadline</h3><p>6 October 2026</p><h1>Programme Assistant – absence cover</h1><p>EMBO is located on the international EMBL life sciences research campus in Heidelberg, Germany.</p><h2>Your role</h2><ul><li>Manage programme selection rounds.</li></ul><h2>You have</h2><p>Two years administrative experience.</p><h2>Why join us</h2><p>Employer branding unrelated to role.</p><h2>Meet staff members</h2><p>Meet Rosy, Programme Officer.</p></article>'
        result = extract_html_jobs(body, 'https://example.test/vacancy/programme-assistant/')
        self.assertEqual(len(result['jobs']), 1)
        job = result['jobs'][0]
        self.assertIn('Manage programme selection rounds.', job['description'])
        self.assertIn('Two years administrative experience.', job['description'])
        self.assertNotIn('biography', job['description'])
        self.assertNotIn('Meet Rosy', job['description'])
        self.assertNotIn('Employer branding', job['description'])
        self.assertEqual(job['location'], 'Heidelberg')
        self.assertEqual(job['raw_metadata']['validThrough'], '2026-10-06')
        self.assertEqual(extract_html_jobs('<article><h1>Careers</h1><p>Meet our staff.</p></article>', 'https://example.test/vacancy/careers/')['jobs'], [])
        self.assertEqual(extract_html_jobs('<h1>Personal Assistant</h1><p>Staff biography.</p>', 'https://example.test/vacancy/staff/')['jobs'], [])
        self.assertEqual(extract_html_jobs('<article class="vacancy"><h1>About us</h1><a href="/apply">Apply</a><p>Our employer story.</p></article>', 'https://example.test/vacancy/about/')['jobs'], [])

    def test_schema_preserves_applicant_scope_expiry_country_and_coordinates(self):
        posting = {'@type': 'JobPosting', 'title': 'Engineer', 'url': 'https://example.test/jobs/1', 'jobLocationType': 'TELECOMMUTE', 'validThrough': '2026-10-06', 'applicantLocationRequirements': [{'@type': 'Country', 'name': 'Germany'}], 'jobLocation': {'address': {'addressLocality': 'Heidelberg', 'addressCountry': {'name': 'Germany'}}, 'geo': {'latitude': 49.4, 'longitude': 8.7}}}
        for provider in ('html_jobs', 'schema_org'):
            with self.subTest(provider=provider):
                payload = ('<script type="application/ld+json">' + json.dumps(posting) + '</script>') if provider == 'html_jobs' else json.dumps({'@type': 'DataFeed', 'dataFeedElement': [posting]})
                job = parse_feed(provider, payload, 'https://example.test/jobs/')['jobs'][0]
                self.assertEqual(job['raw_metadata']['applicantLocationRequirements'], [{'@type': 'Country', 'name': 'Germany'}])
                self.assertEqual(job['raw_metadata']['validThrough'], '2026-10-06')
                self.assertEqual(job['locations'], [{'label': 'Heidelberg', 'country_code': 'DE', 'latitude': 49.4, 'longitude': 8.7, 'precision': 'source_coordinates'}])
        posting['jobLocation'] = [{'address': 'malformed', 'geo': {'latitude': 'nan', 'longitude': 8}}, {'address': {'addressLocality': 'Berlin', 'addressCountry': ['bad']}, 'geo': {'latitude': True, 'longitude': 8}}]
        posting['datePosted'] = {'bad': 'value'}
        posting['@type'] = ['JobPosting', {'bad': 'type'}]
        job = extract_html_jobs('<script type="application/ld+json">' + json.dumps(posting) + '</script>', 'https://example.test/jobs/')['jobs'][0]
        self.assertEqual(job['locations'], [{'label': 'Berlin'}])
        posting['@type'] = 'JobPosting'
        posting['employmentType'] = ['FULL_TIME', {'bad': 'nested'}]
        job = parse_feed('schema_org', json.dumps({'@type': 'DataFeed', 'dataFeedElement': [posting]}), 'https://example.test/jobs/')['jobs'][0]
        self.assertNotIn('date_posted', job)
        self.assertEqual(job['locations'], [{'label': 'Berlin'}])

    def test_extracts_job_links_and_card_metadata_from_static_career_html(self):
        body = b'''<!doctype html><html><head><title>Jobs</title></head><body>
          <h1>IT-Jobs in Karlsruhe</h1><h2>Aktuelle Jobs</h2>
          <article><a href="/jobs/senior-linux-administrator">Senior Linux Administrator</a>
            <p>Karlsruhe (Vor-Ort) Vollzeit Linux Betrieb und Kubernetes.</p></article>
          <article><a href="/jobs/junior-linux-administrator">Junior Linux Administrator</a>
            <p>Karlsruhe Vollzeit Einstieg ins Hosting.</p></article>
          <nav><a href="/jobs/">Jobs</a></nav>
        </body></html>'''
        result = parse_feed('html_jobs', body, 'https://qwertiko.example/jobs/')
        self.assertEqual([job['title'] for job in result['jobs']], [
            'Senior Linux Administrator', 'Junior Linux Administrator',
        ])
        self.assertEqual(result['jobs'][0]['location'], 'Karlsruhe')
        self.assertEqual(result['jobs'][0]['employment_type'], 'full-time')
        self.assertIn('extraction_method', result['jobs'][0]['raw_metadata'])

    def test_extracts_roles_from_explicit_open_positions_cards(self):
        body = '''<html><body><h1>Karriere</h1><h2>Offene Stellen</h2>
          <section><h3>IT-Systemadministrator / IT-Techniker (m/w/d)</h3>
            <p>Karlsruhe (Vor-Ort) Vollzeit Start: 01.07.2027</p>
            <a href="mailto:jobs@example.test">Jetzt bewerben</a></section>
          <section><h3>Ausbildung: Fachinformatiker Systemintegration (m/w/d)</h3>
            <p>Karlsruhe (Vor-Ort) Ausbildungsstart: 01.09.2027</p></section>
          <h2>Unser Büro</h2><h3>Unser Team</h3></body></html>'''.encode()
        result = parse_feed('html_jobs', body, 'https://tooltec.example/karriere/')
        self.assertEqual(len(result['jobs']), 2)
        self.assertEqual(result['jobs'][0]['location'], 'Karlsruhe')
        self.assertEqual(result['jobs'][0]['raw_metadata']['start_date'], '2027-07-01')
        self.assertEqual(result['jobs'][1]['employment_type'], 'apprenticeship')

    def test_detail_page_uses_role_section_instead_of_site_footer(self):
        body = b'''<html><body><main><h1>Senior Linux Administrator</h1>
          <p>Operate Linux and Kubernetes systems for customers.</p></main>
          <footer><p>Ausbildung zum Fachinformatiker. Karlsruhe address.</p></footer></body></html>'''
        result = parse_feed('html_jobs', body, 'https://qwertiko.example/jobs/senior-linux-administrator/')
        self.assertEqual(result['jobs'][0]['title'], 'Senior Linux Administrator')
        self.assertIsNone(result['jobs'][0]['employment_type'])
        self.assertEqual(result['jobs'][0]['description'], 'Operate Linux and Kubernetes systems for customers.')

    def test_reads_schema_org_jobposting_from_regular_career_page(self):
        posting = {
            '@context': 'https://schema.org', '@type': 'JobPosting',
            'identifier': {'value': 'role-77'}, 'title': 'Platform Engineer',
            'url': 'https://example.test/careers/platform-engineer',
            'datePosted': '2026-09-30', 'employmentType': 'FULL_TIME',
            'description': '<p>Build platform services.</p>',
            'jobLocation': {'address': {'addressLocality': 'Karlsruhe'}},
        }
        body = ('<html><head><script type="application/ld+json">' + json.dumps(posting) +
                '</script></head><body><h1>Careers</h1></body></html>').encode()
        result = parse_feed('html_jobs', body, 'https://example.test/careers/')
        self.assertEqual(result['jobs'][0]['id'], 'role-77')
        self.assertEqual(result['jobs'][0]['location'], 'Karlsruhe')
        self.assertEqual(result['jobs'][0]['description'], 'Build platform services.')

    def test_preserves_schema_org_hiring_organization_for_source_validation(self):
        posting = {
            '@context': 'https://schema.org', '@type': 'JobPosting',
            'title': 'Platform Engineer', 'url': 'https://board.example/jobs/77',
            'hiringOrganization': {
                '@type': 'Organization', 'name': 'Acme Systems GmbH',
                'url': 'https://acme.example/', 'sameAs': 'https://www.wikidata.org/entity/Q123',
            },
        }
        body = ('<script type="application/ld+json">' + json.dumps(posting) + '</script>').encode()
        result = parse_feed('html_jobs', body, 'https://board.example/jobs/')
        self.assertEqual(result['jobs'][0]['raw_metadata']['hiring_organization'], {
            'name': 'Acme Systems GmbH', 'url': 'https://acme.example/',
        })

    def test_malformed_and_non_http_schema_job_urls_are_ignored_safely(self):
        postings = [
            {'@context': 'https://schema.org', '@type': 'JobPosting',
             'title': 'Malformed', 'url': 'https://[invalid'},
            {'@context': 'https://schema.org', '@type': 'JobPosting',
             'title': 'Script URL', 'url': 'javascript:alert(1)'},
        ]
        body = ''.join('<script type="application/ld+json">' + json.dumps(item) + '</script>'
                       for item in postings).encode()
        result = extract_html_jobs(body, 'https://board.example/jobs/')
        self.assertEqual(result['jobs'], [])

    def test_job_cards_use_specific_title_location_and_do_not_mix_sibling_rows(self):
        body = '''<html><body><h1>Jobbörse</h1>
          <ul><li><a href="/stellenangebote/Teamleitung-Cloud-Platform-TK1">
            <div class="stellenmarkt-anzeige__taetigkeitsbereich">Informationstechnologie (IT)</div>
            <h4 class="stellenmarkt-anzeige__titel">Team­lei­tung Cloud Platform (m/w/d)</h4>
            <div class="stellenmarkt-anzeige__ort">in Hamburg</div><span>Details</span>
          </a></li><li><a href="/stellenangebote/Teamleitung-Data-Platform-TK2">
            <h4 class="stellenmarkt-anzeige__titel">Teamleitung Data Platform (m/w/d)</h4>
            <div class="stellenmarkt-anzeige__ort">in Fulda</div><span>Details</span>
          </a></li></ul></body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/stellenangebote/')
        self.assertEqual([job['title'] for job in result['jobs']], [
            'Teamleitung Cloud Platform (m/w/d)', 'Teamleitung Data Platform (m/w/d)',
        ])
        self.assertEqual([job['location'] for job in result['jobs']], ['Hamburg', 'Fulda'])
        self.assertTrue(all(job.get('description') is None for job in result['jobs']))
        self.assertTrue(all(job['employment_type'] is None for job in result['jobs']))

    def test_job_slug_city_is_kept_when_card_title_omits_location(self):
        body = b'<html><body><h1>Stellenangebote</h1><a href="/jobs/vertrieb-halle-15595">Mitarbeiter (m/w/d) Spezialvertrieb</a></body></html>'
        result = extract_html_jobs(body, 'https://acme.example/jobs/')
        self.assertEqual(result['jobs'][0]['location'], 'Halle')

    def test_faq_question_about_internships_is_not_misread_as_an_open_job(self):
        body = '<html><body><h1>Karriere FAQ</h1><h2>Spezialfragen: Praktikum &amp; Abschlussarbeit</h2></body></html>'
        result = extract_html_jobs(body, 'https://huk.example/karriere/faq.html')
        self.assertEqual(result['jobs'], [])

    def test_hidden_cookie_controls_are_not_misread_as_a_vacancy(self):
        body = '''<html><body><h1>Careers</h1><h2>Open positions</h2>
          <div style="display: none"><h3>Manager für Cookie-Einwilligungen</h3></div>
          <div class="modal fade"><h3>Cookie Consent Manager</h3></div>
        </body></html>'''
        result = extract_html_jobs(body, 'https://acme.example/careers/')
        self.assertEqual(result['jobs'], [])

    def test_list_and_detail_versions_of_one_job_merge_by_canonical_job_url(self):
        listing = {'id': 'list', 'title': 'Cloud Engineer in Hamburg Details',
                   'url': 'https://tk.example/stellenangebote/cloud-engineer-TK1',
                   'raw_metadata': {'extraction_method': 'html_job_link'}, 'location': 'Hamburg'}
        detail = {'id': 'detail', 'title': 'Cloud Engineer',
                  'url': 'https://tk.example/stellenangebote/cloud-engineer-TK1',
                  'raw_metadata': {'extraction_method': 'html_job_detail'}, 'description': 'Build the cloud platform.'}
        merged = _merge([listing, detail])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['title'], 'Cloud Engineer')
        self.assertEqual(merged[0]['description'], 'Build the cloud platform.')
        self.assertEqual(merged[0]['location'], 'Hamburg')

    def test_role_heading_does_not_duplicate_a_linked_job_card(self):
        body = b'''<html><body><h1>Careers</h1><h2>Open positions</h2>
          <h3>Cloud Engineer</h3><a href="/jobs/cloud-engineer-1">Cloud Engineer</a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://acme.example/careers/')
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual(result['jobs'][0]['raw_metadata']['extraction_method'], 'html_job_link')

    def test_evergreen_student_program_heading_is_retained_as_unconfirmed_role_only(self):
        body = '''<html><body><h1>Career paths for students</h1>
          <h2>Praktika, Abschlussarbeiten &amp; Werkstudententätigkeit</h2>
          <p>Find out how you can get to know us while studying.</p></body></html>'''.encode()
        result = extract_html_jobs(body, 'https://contargo.example/careers/students/')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['role_candidates'][0]['title'],
                         'Praktika, Abschlussarbeiten & Werkstudententätigkeit')
        self.assertEqual(result['role_candidates'][0]['confidence'], 'unconfirmed_role')

    def test_embedded_framework_job_array_is_kept_as_an_unconfirmed_role(self):
        payload = {'props': {'pageProps': {'jobs': [{
            'id': 'job-9', 'title': 'Cloud Engineer', 'url': '/careers/cloud-engineer',
            'location': 'Berlin', 'employmentType': 'FULL_TIME',
        }]}}}
        body = ('<html><body><h1>Careers</h1><script id="__NEXT_DATA__" type="application/json">' +
                json.dumps(payload) + '</script></body></html>').encode()
        result = extract_html_jobs(body, 'https://example.test/careers/')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['role_candidates'], [{
            'title': 'Cloud Engineer', 'url': 'https://example.test/careers/cloud-engineer',
            'method': 'embedded_json', 'confidence': 'unconfirmed_role',
        }])

    def test_generic_closed_or_template_framework_rows_are_rejected(self):
        payload = {'props': {'pageProps': {'jobs': [
            {'title': 'Closed Cloud Engineer', 'url': '/careers/closed', 'status': 'closed'},
            {'title': 'Template Developer', 'url': '/careers/template', 'template': True},
        ]}}}
        body = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + '</script>'
        result = extract_html_jobs(body, 'https://example.test/careers/')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['role_candidates'], [])

    def test_extracts_explicit_server_rendered_job_cards_with_query_detail_urls(self):
        body = b'''<html><body><h1>Open jobs</h1>
          <a data-guide-id="joblist-card" href="/en?id=297547"><div
            data-guide-id="joblist-card-title"><h2>Technical Lead - Cloud Operations (m/w/d)</h2></div>
            <div data-guide-id="joblist-card-location">Karlsruhe</div><span>Full time</span></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual(len(result['jobs']), 1)
        job = result['jobs'][0]
        self.assertEqual(job['title'], 'Technical Lead - Cloud Operations (m/w/d)')
        self.assertEqual(job['url'], 'https://jobs.example/en?id=297547')
        self.assertEqual(job['location'], 'Karlsruhe')
        self.assertEqual(job['employment_type'], 'full-time')
        self.assertEqual(job['raw_metadata']['extraction_method'], 'structured_job_card')

    def test_explicit_job_card_keeps_an_initiative_posting_unconfirmed(self):
        body = b'''<html><body><a data-guide-id="joblist-card" href="/en?id=41247d"><div
          data-guide-id="joblist-card-title">Initiativbewerbung (m/w/d)</div><div
          data-guide-id="joblist-card-location">Karlsruhe</div><div
          data-guide-id="joblist-card-tags">Full time</div><p>Ausbildung is one option.</p></a></body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['role_candidates'], [{
            'title': 'Initiativbewerbung (m/w/d)', 'url': 'https://jobs.example/en?id=41247d',
            'method': 'structured_job_card', 'confidence': 'unconfirmed_role',
        }])

    def test_structured_generic_application_labels_are_unconfirmed(self):
        body = b'''<html><body>
          <a data-guide-id="joblist-card" href="/en?id=initiative"><div data-guide-id="joblist-card-title">Initiative Application</div></a>
          <a data-guide-id="joblist-card" href="/en?id=unsolicited"><div data-guide-id="joblist-card-title">Unsolicited Application</div></a>
          <a data-guide-id="joblist-card" href="/en?id=general"><div data-guide-id="joblist-card-title">General Application (m/f/d)</div></a>
          <a data-guide-id="joblist-card" href="/en?id=speculative"><div data-guide-id="joblist-card-title">Speculative Application (m/f/d)</div></a>
          <a data-guide-id="joblist-card" href="/en?id=open"><div data-guide-id="joblist-card-title">Open Application</div></a>
          <a data-guide-id="joblist-card" href="/en?id=specific"><div data-guide-id="joblist-card-title">General Application Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=open-specific"><div data-guide-id="joblist-card-title">Open Application Engineer</div></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual([job['title'] for job in result['jobs']], [
            'General Application Engineer', 'Open Application Engineer',
        ])
        self.assertEqual([candidate['title'] for candidate in result['role_candidates']], [
            'Initiative Application', 'Unsolicited Application', 'General Application (m/f/d)',
            'Speculative Application (m/f/d)', 'Open Application',
        ])

    def test_structured_talent_pools_are_unconfirmed_but_specific_talent_roles_are_jobs(self):
        body = b'''<html><body>
          <a data-guide-id="joblist-card" href="/en?id=pool"><div data-guide-id="joblist-card-title">Talent Pool (m/f/d)</div></a>
          <a data-guide-id="joblist-card" href="/en?id=community"><div data-guide-id="joblist-card-title">Join our talent community (m/w/d)</div></a>
          <a data-guide-id="joblist-card" href="/en?id=manager"><div data-guide-id="joblist-card-title">Talent Acquisition Manager</div></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual([job['title'] for job in result['jobs']], ['Talent Acquisition Manager'])
        self.assertEqual([candidate['title'] for candidate in result['role_candidates']], [
            'Talent Pool (m/f/d)', 'Join our talent community (m/w/d)',
        ])

    def test_structured_cards_reject_hidden_disabled_closed_and_template_rows(self):
        body = b'''<html><body>
          <a data-guide-id="joblist-card" href="/en?id=hidden" hidden><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=disabled" aria-disabled="true"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=closed" data-status="closed"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=filled"><div data-guide-id="joblist-card-title">Cloud Engineer</div><span>Position closed</span></a>
          <a data-guide-id="joblist-card" href="/en?id=template" class="template"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(result['role_candidates'], [])

    def test_structured_cards_reject_inactive_class_tokens_and_true_data_flags(self):
        body = b'''<html><body>
          <a data-guide-id="joblist-card" href="/en?id=closed" class="job-card--closed"><div data-guide-id="joblist-card-title">Closed Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=archived" class="state-archived"><div data-guide-id="joblist-card-title">Archived Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=template" data-template="true"><div data-guide-id="joblist-card-title">Template Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=disabled" data-disabled="TRUE"><div data-guide-id="joblist-card-title">Disabled Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=active" class="not-closed" data-template="false" data-disabled="0"><div data-guide-id="joblist-card-title">Live Engineer</div></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual([job['title'] for job in result['jobs']], ['Live Engineer'])
        self.assertEqual(result['role_candidates'], [])

    def test_structured_cards_require_a_same_origin_destination(self):
        body = b'''<html><body>
          <a data-guide-id="joblist-card" href="https://other.example/en?id=one"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="https://user:pass@jobs.example/en?id=two"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="javascript:alert(1)"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual(result['jobs'], [])

    def test_structured_cards_use_query_identity_and_deduplicate_repeated_destination(self):
        body = b'''<html><body>
          <a data-guide-id="joblist-card" href="/en?id=one"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=two"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
          <a data-guide-id="joblist-card" href="/en?id=one"><div data-guide-id="joblist-card-title">Cloud Engineer</div></a>
        </body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual([job['url'] for job in result['jobs']], [
            'https://jobs.example/en?id=one', 'https://jobs.example/en?id=two',
        ])
        self.assertNotEqual(result['jobs'][0]['id'], result['jobs'][1]['id'])
        self.assertNotEqual(html_job_key(result['jobs'][0]), html_job_key(result['jobs'][1]))

    def test_schema_org_role_itemlist_is_kept_as_unconfirmed_not_active_job(self):
        body = b'''<html><body><h1>Join the team</h1><h2>Which roles suit you?</h2>
          <script type="application/ld+json">{"@type":"ItemList","name":"Join the team","itemListElement":[
            {"@type":"ListItem","name":"Backend Development","url":"https://example.test/blog/backend"}]}
          </script></body></html>'''
        extracted = extract_html_jobs(body, 'https://example.test/careers/')
        self.assertEqual(extracted['jobs'], [])
        self.assertEqual(extracted['role_candidates'][0]['title'], 'Backend Development')
        self.assertEqual(extracted['role_candidates'][0]['confidence'], 'unconfirmed_role')

    def test_no_open_positions_is_not_misread_as_a_job(self):
        body = b'<html><body><h1>Careers</h1><p>We currently have no open positions.</p><h2>Our Team</h2></body></html>'
        result = parse_feed('html_jobs', body, 'https://emmtrix.example/company/jobs')
        self.assertEqual(result['jobs'], [])
        self.assertTrue(result['complete'])

    def test_job_article_linked_from_career_sitemap_is_not_treated_as_current_vacancy(self):
        body = b'<html><body><h1>Software Jobs in Karlsruhe</h1><h2>Working students and thesis topics</h2><a href="/company/jobs">Visit our job site</a></body></html>'
        result = parse_feed('html_jobs', body, 'https://emmtrix.example/news/software-jobs.html')
        self.assertEqual(result['jobs'], [])

if __name__ == '__main__':
    unittest.main()
