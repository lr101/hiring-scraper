import json
import unittest
from unittest.mock import Mock, patch

from hiring_scraper.ats import parse_feed
from hiring_scraper.app.models import JobFeed
from hiring_scraper.app.worker import _request
from hiring_scraper.html_jobs import _merge, extract_html_jobs


class HtmlJobExtractionTests(unittest.TestCase):
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

    def test_embedded_framework_job_array_is_parsed_without_running_javascript(self):
        payload = {'props': {'pageProps': {'jobs': [{
            'id': 'job-9', 'title': 'Cloud Engineer', 'url': '/careers/cloud-engineer',
            'location': 'Berlin', 'employmentType': 'FULL_TIME',
        }]}}}
        body = ('<html><body><h1>Careers</h1><script id="__NEXT_DATA__" type="application/json">' +
                json.dumps(payload) + '</script></body></html>').encode()
        result = parse_feed('html_jobs', body, 'https://example.test/careers/')
        self.assertEqual(result['jobs'][0]['title'], 'Cloud Engineer')
        self.assertEqual(result['jobs'][0]['url'], 'https://example.test/careers/cloud-engineer')
        self.assertEqual(result['jobs'][0]['location'], 'Berlin')
        self.assertEqual(result['jobs'][0]['employment_type'], 'full-time')
        self.assertEqual(result['jobs'][0]['raw_metadata']['extraction_method'], 'embedded_json')

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

    def test_explicit_job_card_keeps_an_active_initiative_posting(self):
        body = b'''<html><body><a data-guide-id="joblist-card" href="/en?id=41247d"><div
          data-guide-id="joblist-card-title">Initiativbewerbung (m/w/d)</div><div
          data-guide-id="joblist-card-location">Karlsruhe</div><div
          data-guide-id="joblist-card-tags">Full time</div><p>Ausbildung is one option.</p></a></body></html>'''
        result = extract_html_jobs(body, 'https://jobs.example/en')
        self.assertEqual([job['title'] for job in result['jobs']], ['Initiativbewerbung (m/w/d)'])
        self.assertEqual(result['jobs'][0]['employment_type'], 'full-time')

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

    def test_html_job_refresh_uses_robots_aware_page_fetch(self):
        client = Mock()
        client.get.return_value = ({'state': 'ok'}, b'<html></html>')
        feed = JobFeed(provider='html_jobs', feed_url='https://example.test/careers/')
        with patch('hiring_scraper.app.worker._pace_origin'), \
             patch('hiring_scraper.app.worker.Client', return_value=client):
            metadata, body = _request(feed)
        self.assertEqual(metadata['state'], 'ok')
        self.assertTrue(body.startswith(b'<html'))
        client.get.assert_called_once_with(feed.feed_url)
        client.get_feed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
