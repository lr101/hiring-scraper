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

    def test_confirmed_detail_stops_at_explicit_team_heading_after_role_text(self):
        for boundary in ('Our team', 'Unser Team', 'Meet our team', 'Staff members'):
            with self.subTest(boundary=boundary):
                body = '<main><h1>Programme Assistant</h1><a href="/apply">Apply</a><p>Manage programme selection rounds with researchers.</p><h2>Team responsibilities</h2><p>Coordinate the programme calendar with colleagues.</p><h2>You have</h2><p>Two years administrative experience.</p><h2>' + boundary + '</h2><p>Meet Rosy, staff biography unrelated to vacancy.</p></main>'
                job = extract_html_jobs(body, 'https://example.test/vacancy/programme-assistant/')['jobs'][0]
                self.assertIn('Manage programme selection rounds with researchers.', job['description'])
                self.assertIn('Team responsibilities', job['description'])
                self.assertIn('Coordinate the programme calendar with colleagues.', job['description'])
                self.assertIn('Two years administrative experience.', job['description'])
                self.assertNotIn('Meet Rosy', job['description'])
                self.assertNotIn(boundary, job['description'])

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

    def test_card_location_outside_the_title_link_is_kept(self):
        def card(slug, req, title, place):
            return ('<li class="results-list__item"><h3><a class="results-list__item-title--link" '
                    f'href="/{slug}/job/{req}">{title}</a></h3><div class="results-list__item-street">'
                    '<span class="results-list__item-street--icon"><svg><title>Job Location</title></svg></span>'
                    f'<span class="results-list__item-street--label">{place}</span></div>'
                    f'<span class="results-list__req--label">{req}</span>'
                    f'<a aria-label="Apply Now, {title}" href="/{slug}/job/{req}">Apply Now</a></li>')
        body = '<ul>' + ''.join((
            card('product-manager-ai', 'R56037', 'Product Manager, AI Solutions', 'Brasov'),
            card('sales-operations-analyst', 'R58619', 'Sales Operations Analyst', 'Westminster, CO'),
            card('avp-corporate-revenue', 'R58500', 'AVP Corporate Revenue', 'Princeton, NJ + 42 More Locations'),
            card('bdm-enterprise', 'R58423', 'Business Development Manager', 'UK - Remote'),
        )) + '</ul>'
        jobs = extract_html_jobs(body, 'https://careers.example/jobs')['jobs']
        self.assertEqual([job['title'] for job in jobs], [
            'Product Manager, AI Solutions', 'Sales Operations Analyst', 'AVP Corporate Revenue',
            'Business Development Manager',
        ])
        self.assertEqual([job['locations'] for job in jobs], [
            [{'label': 'Brasov'}], [{'label': 'Westminster, CO'}], [{'label': 'Princeton, NJ'}],
            [{'label': 'UK - Remote', 'country_code': 'GB'}],
        ])
        self.assertTrue(jobs[3]['is_remote'])

    def test_detail_page_reads_explicit_location_line_or_term_pair(self):
        body = ('<main><h1>Product Manager, AI Solutions</h1><p>Lead our AI roadmap with customers.</p>'
                '<p><b>Location: </b>Brasov, Romania (4 days on site)</p><p>Travel Requirement: 0-10%</p>'
                '<a href="/apply">Apply now</a></main>')
        job = extract_html_jobs(body, 'https://careers.example/job/R1')['jobs'][0]
        self.assertEqual(job['locations'], [{'label': 'Brasov, Romania', 'country_code': 'RO'}])
        self.assertEqual(job['work_arrangement'], 'onsite')
        body = ('<main><h1>Werkstudent Data Engineering</h1><dl><dt>Standort</dt><dd>Leipzig-Plagwitz</dd>'
                '<dt>Start</dt><dd>sofort</dd></dl><p>Du unterstützt unser Datenteam beim Aufbau von Pipelines.</p>'
                '<a href="/apply">Jetzt bewerben</a></main>')
        job = extract_html_jobs(body, 'https://careers.example/jobs/werkstudent')['jobs'][0]
        self.assertEqual(job['locations'], [{'label': 'Leipzig-Plagwitz'}])
        body = ('<main><h1>Data Analyst</h1><p>Location: we offer flexible working across sites.</p>'
                '<p>Build dashboards for our operations team every day.</p><a href="/apply">Apply</a></main>')
        self.assertEqual(extract_html_jobs(body, 'https://careers.example/jobs/analyst')['jobs'][0]['locations'], [])

    def test_schema_and_html_detail_rows_for_one_posting_merge_without_losing_location(self):
        posting = {'@context': 'http://schema.org', '@type': 'JobPosting', 'title': 'KYC Business Analyst- Remote',
                   'url': 'https://careers.example/vacancies/3389/kyc-business-analyst-remote.html',
                   'jobLocation': {'@type': 'Place', 'address': {'addressLocality': 'London', 'addressCountry': 'United Kingdom'}}}
        body = ('<script type="application/ld+json">' + json.dumps([posting]) + '</script>'
                '<main><h1>KYC Business Analyst- Remote</h1><p>Support onboarding reviews for our banking clients.</p>'
                '<a href="/apply">Apply now</a></main>'
                '<a href="/vacancies/vacancy-search-handler.ashx?BusinessAreaID=5">Internal Vacancies</a>')
        jobs = extract_html_jobs(body, posting['url'])['jobs']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['locations'], [{'label': 'London', 'country_code': 'GB'}])
        self.assertIn('onboarding reviews', jobs[0]['description'])

    def test_schema_posting_with_own_id_absorbs_the_same_pages_html_reading(self):
        url = 'https://careers.example/technical-analyst/job/R0151640'
        posting = {'@context': 'https://schema.org', '@type': 'JobPosting', 'title': 'Technical Analyst', 'url': url,
                   'identifier': {'@type': 'PropertyValue', 'value': 'R0151640'},
                   'description': '<h3>What you will be doing</h3><ul><li>Resolve storage issues.</li></ul>',
                   'jobLocation': [{'@type': 'Place', 'address': {'addressRegion': 'CA', 'addressCountry': 'US'}},
                                   {'@type': 'Place', 'address': {'addressCountry': 'MX'}}]}
        body = ('<script type="application/ld+json">' + json.dumps(posting) + '</script>'
                '<main><h1>Technical Analyst</h1><p>Share Job Post</p><p>Resolve storage issues for customers worldwide.</p>'
                '<a href="/apply">Apply Now</a><h3>Other jobs</h3>'
                '<a href="/technical-analyst/job/R0138960">Technical Analyst</a> <span class="location">Mexico City</span></main>')
        jobs = extract_html_jobs(body, url)['jobs']
        self.assertEqual([job['id'] for job in jobs if job['url'] == url], ['R0151640'])
        job = next(job for job in jobs if job['url'] == url)
        self.assertEqual(job['locations'], [{'label': 'California, United States', 'country_code': 'US'},
                                            {'label': 'Mexico', 'country_code': 'MX'}])
        self.assertEqual(job['description'], '## What you will be doing\n- Resolve storage issues.')
        self.assertEqual(job['raw_metadata']['extraction_method'], 'schema_org_jobposting')

    def test_screen_reader_location_label_is_not_part_of_the_value(self):
        body = ('<div class="job"><h3><a href="/vacancies/3394/business-analyst-leeds.html">Business Analyst- Leeds</a></h3>'
                '<p>Gather requirements for e-commerce platforms.</p>'
                '<div class="label"><i class="icon icon-location-on"><span class="its-u-visually-hidden">All Locations</span></i></div>'
                '<div data-id="div_content_VacV_AllLocations" class="content">Leeds</div></div>')
        job = extract_html_jobs(body, 'https://careers.example/vacancies/')['jobs'][0]
        self.assertEqual(job['locations'], [{'label': 'Leeds'}])

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

    def test_detail_page_reads_icon_location_overview_facts_and_stops_at_contact(self):
        # Shape of jobs.check24.de detail pages: no structured data, the town sits
        # beside a map-marker icon, and "Im Überblick" is a row of icon labels.
        body = '''<html><body><nav><a href="/de/standorte/berlin/">Berlin</a></nav>
          <article data-job-id="000000-REF645K"><div>
            <div><h1>(Junior) Product Manager (m/w/d) AI &amp; Digital Innovation</h1>
              <button type="button"><svg></svg><span>Teilen</span></button></div>
            <div><div><svg class="icon-building"><path d="M0"></path></svg><span>Steuer</span></div>
              <div><svg class="icon-map-marker w-[13px]"><path d="M0"></path></svg><span> Dortmund </span></div></div>
            <a class="apply-button" href="/de/jobs/produkt/ref645k-pm/apply/?jobid=REF645K">jetzt bewerben</a>
          </div>
          <div class="job-overview"><h2>Im Überblick</h2><div>
            <div><svg><circle></circle></svg><span>Junior</span></div>
            <div><svg><circle></circle></svg><span>Vollzeit</span></div>
            <div><svg><circle></circle></svg><span>Festanstellung</span></div></div></div>
          <div class="job-section"><h2>Über diesen Job</h2><p>Mit KI die Steuererklärung neu denken, auch für Werkstudenten-Teams.</p></div>
          <div class="job-section"><h2>Was Du mitbringst</h2><ul><li>Analytische Stärke</li></ul></div>
          <h2>Kontakt</h2><div class="job-section contact-section"><div><img alt="Recruiter">
            <div><h3>Erika Mustermann</h3><span><a href="mailto:recruiter@check24.example">recruiter@check24.example</a></span></div></div>
            <span>Bitte sende uns Bewerbungen ausschließlich online.</span></div>
          </article></body></html>'''
        result = extract_html_jobs(body, 'https://jobs.check24.example/de/jobs/produkt/ref645k-pm/')
        self.assertEqual(len(result['jobs']), 1)
        job = result['jobs'][0]
        self.assertEqual(job['location'], 'Dortmund')
        self.assertEqual(job['employment_type'], 'full-time')
        self.assertEqual(job['description'].split('\n\n')[:2], ['## Im Überblick', '- Junior\n- Vollzeit\n- Festanstellung'])
        self.assertNotIn('Kontakt', job['description'])
        self.assertNotIn('Mustermann', job['description'])
        self.assertNotIn('application_email', job['raw_metadata'])

    def test_detail_page_application_email_in_contact_section_is_kept(self):
        body = '''<html><body><main><h1>Cloud Engineer (m/w/d)</h1>
          <h2>Deine Aufgaben</h2><ul><li>Plattform betreiben</li></ul>
          <h2>Kontakt</h2><p>Bewerbungen bitte an jobs@acme.example</p>
          <a href="/apply">Jetzt bewerben</a></main></body></html>'''
        job = extract_html_jobs(body, 'https://acme.example/jobs/cloud-engineer/')['jobs'][0]
        self.assertEqual(job['raw_metadata'].get('application_email'), 'jobs@acme.example')
        self.assertNotIn('Kontakt', job['description'])

    def test_gender_marked_titles_are_postings_without_job_routes_or_profession_words(self):
        body = '''<html><body><h1>Karriere</h1><h2>Offene Stellen</h2>
          <ul class="check">
            <li><strong>Kraftwerksschlosser</strong> (m/w/d)<br>... <a href="#" onclick="apply()">jetzt bewerben</a></li>
            <li><strong>Laminierer</strong> (m/w/d)<br>... <a href="#" onclick="apply()">jetzt bewerben</a></li>
          </ul>
          <div class="card"><h3 class="job-title">Koch (m/w/d) mit freien Wochenenden in Köln, 20€/Std.</h3>
            <span class="tag">Gastronomie (m/w/d)</span><a href="/stellen/koch-4711">Details ansehen</a></div>
          <div class="post"><span>Administrative Clerk</span><span>( m | w | d )</span></div>
          <h3>Benefits</h3><ul><li>Nette und hilfsbereite Kolleg*innen</li>
            <li>Idealerweise abgeschlossene Ausbildung als Maler (m/w/d) oder vergleichbare Qualifikation</li></ul>
          <select><option>Prophylaxe (w/m/d)</option></select>
          <a href="/karriere?jobType=13">Verwaltung (w/m/d)</a>
        </body></html>'''
        jobs = extract_html_jobs(body, 'https://handwerk.example/karriere')['jobs']
        titles = sorted(job['title'] for job in jobs)
        self.assertEqual(titles, ['Administrative Clerk ( m | w | d )', 'Koch (m/w/d) mit freien Wochenenden in Köln, 20€/Std',
                                  'Kraftwerksschlosser (m/w/d)', 'Laminierer (m/w/d)'])
        koch = next(job for job in jobs if job['title'].startswith('Koch'))
        # A heading never borrows a link it does not own; the card's tag is still not a posting.
        self.assertIn('#role-', koch['url'])
        self.assertEqual(koch['location'], 'Köln')

    def test_a_posting_page_does_not_repeat_its_own_title_from_prose(self):
        body = '''<html><body><h1>Schweißaufsichtsperson (m/w/d)</h1>
          <p>Wir suchen ab sofort eine Schweißaufsichtsperson (m/w/d) für unseren Standort.</p>
          <h2>Stellenangebot: Schweißaufsichtsperson (m/w/d)</h2><ul><li>Planung</li></ul>
          <a href="/jobs/apply">Jetzt bewerben</a>
          <aside><h3>Noch mehr Jobs</h3><a href="/stellen/schlosser-12">Schlosser (m/w/d)</a></aside>
        </body></html>'''
        jobs = extract_html_jobs(body, 'https://metall.example/jobs/schweissaufsicht/')['jobs']
        self.assertEqual(sorted(job['title'] for job in jobs), ['Schlosser (m/w/d)', 'Schweißaufsichtsperson (m/w/d)'])

    def test_review_regressions_for_marked_titles(self):
        # Two postings with one title and query identities stay two postings with distinct ids.
        body = '''<html><body><h1>Karriere</h1>
          <div class="card"><a href="/vacancy?id=123">Laminierer (m/w/d)</a><span>Bonn</span></div>
          <div class="card"><a href="/vacancy?id=124">Laminierer (m/w/d)</a><span>Berlin</span></div>
          <div class="card"><a href="/karriere?jobId=77">Schlosser (m/w/d)</a></div>
          <div class="card"><a href="/karriere?category=lager">Lager (m/w/d)</a></div>
          <a data-guide-id="joblist-card" data-status="closed" href="/stellen/alt">Maler (m/w/d)</a>
        </body></html>'''
        jobs = extract_html_jobs(body, 'https://acme.example/karriere')['jobs']
        laminierer = [job for job in jobs if job['title'] == 'Laminierer (m/w/d)']
        self.assertEqual(len(laminierer), 2)
        self.assertNotEqual(laminierer[0]['id'], laminierer[1]['id'])
        # Each card keeps its own town rather than its neighbour's.
        self.assertEqual(sorted(job['location'] for job in laminierer), ['Berlin', 'Bonn'])
        titles = {job['title'] for job in jobs}
        self.assertIn('Schlosser (m/w/d)', titles)  # ?jobId= names a posting
        self.assertNotIn('Lager (m/w/d)', titles)  # ?category= only filters this list
        self.assertNotIn('Maler (m/w/d)', titles)  # a closed card stays closed

    def test_linked_posting_keeps_its_heading_title_and_id(self):
        body = '''<html><body><h1>Jobs</h1><a href="/jobs/cloud-engineer-123">
          <h3>Cloud Engineer (123)</h3><p>Datacenter Operations</p></a></body></html>'''
        job = extract_html_jobs(body, 'https://acme.example/careers')['jobs'][0]
        self.assertEqual(job['title'], 'Cloud Engineer (123)')

    def test_pager_with_count_keeps_the_scan_partial(self):
        for link in ('<a href="?page=2">Nächste 20 Stellen ›</a>', '<a href="/jobs/seite/2" aria-label="Nächste Seite">2</a>',
                     '<a href="#">Mehr Jobs laden</a>'):
            with self.subTest(link=link):
                result = extract_html_jobs('<html><body><h1>Jobs</h1>' + link + '</body></html>', 'https://acme.example/jobs')
                self.assertFalse(result['complete'])
        # main's rule stays a floor: a page it reads as partial is never read as complete.
        for link in ('<a href="/weiterbildung">Weiterbildung</a>', '<a href="?page=2">Weitere Angebote</a>'):
            with self.subTest(link=link):
                result = extract_html_jobs('<html><body><h1>Karriere</h1>' + link + '</body></html>', 'https://acme.example/karriere')
                self.assertFalse(result['complete'])

    def test_training_role_names_need_the_position_itself(self):
        body = '''<html><body><main><h1>Koordinator Ausbildung (m/w/d)</h1><p>Vollzeit</p>
          <h2>Aufgaben</h2><ul><li>Betreuung unserer Auszubildenden und Ausbildungsplanung</li></ul>
          <a href="/apply">Jetzt bewerben</a></main></body></html>'''
        job = extract_html_jobs(body, 'https://acme.example/jobs/koordinator-ausbildung/')['jobs'][0]
        self.assertEqual(job['employment_type'], 'full-time')
        from hiring_scraper.html_facts import _title_employment
        for title in ('Personalreferent Ausbildung für den Standort Bonn', 'Leitung Werkstudentenprogramm (m/w/d)',
                      'Werkstudentenbetreuer (m/w/d)', 'Koordinator Pflichtpraktikantenprogramm'):
            with self.subTest(title=title):
                self.assertIsNone(_title_employment(title))

    def test_page_facts_fill_only_this_pages_structured_posting(self):
        rows = [{"@type": "JobPosting", "title": f"Engineer {n}", "url": f"https://acme.example/careers?jobId={n}",
                 "description": "Build things for customers and teams."} for n in (123, 124)]
        body = (f'<html><body><script type="application/ld+json">{json.dumps({"@graph": rows})}</script>'
                '<h1>Engineer 123</h1><p>Berlin</p><p>Teilzeit</p><a href="/apply">Jetzt bewerben</a></body></html>')
        jobs = {job['title']: job for job in extract_html_jobs(body, 'https://acme.example/careers?jobId=123')['jobs']}
        self.assertEqual((jobs['Engineer 123']['location'], jobs['Engineer 123']['employment_type']), ('Berlin', 'part-time'))
        self.assertEqual((jobs['Engineer 124']['location'], jobs['Engineer 124']['employment_type']), ('', None))

    def test_recheck_regressions(self):
        # A real title starting with "Job" keeps its reading (and id) on a detail link.
        body = '<html><body><h1>Jobs</h1><a href="/jobs/job-developer-7"><h3>Job Developer (m/w/d)</h3><p>Career Consultant</p></a></body></html>'
        self.assertEqual(extract_html_jobs(body, 'https://acme.example/careers')['jobs'][0]['title'], 'Job Developer (m/w/d)')
        # Explicit page wording or a rel token marks more pages, whatever the URL.
        for link in ('<a href="?cursor=abc">Weiter zu Seite 2</a>', '<a rel="nofollow next" href="/x"><svg></svg></a>'):
            with self.subTest(link=link):
                self.assertFalse(extract_html_jobs('<html><body><h1>Jobs</h1>' + link + '</body></html>',
                                                   'https://acme.example/jobs')['complete'])
        # Linked postings replace a bare heading another strategy read; "?ref=lager" only filters.
        body = '''<html><body><h2>Offene Stellen</h2><h3>Laminierer (m/w/d)</h3>
          <div class="card"><a href="/vacancy?id=123">Laminierer (m/w/d)</a><span>Bonn</span></div>
          <div class="card"><a href="/vacancy?id=124">Laminierer (m/w/d)</a><span>Berlin</span></div>
          <div class="card"><a href="/karriere?ref=lager">Lagerist (m/w/d)</a></div></body></html>'''
        jobs = extract_html_jobs(body, 'https://acme.example/karriere')['jobs']
        self.assertEqual(sorted(job['url'] for job in jobs if job['title'] == 'Laminierer (m/w/d)'),
                         ['https://acme.example/vacancy?id=123', 'https://acme.example/vacancy?id=124'])
        self.assertNotIn('Lagerist (m/w/d)', {job['title'] for job in jobs})
        # A heading and a separate unlinked title are two postings, not a title and its tag.
        body = '<html><body><h1>Karriere</h1><section><h3>Fliesenleger (m/w/d)</h3><div>Laminierer (m/w/d)</div></section></body></html>'
        self.assertEqual(sorted(job['title'] for job in extract_html_jobs(body, 'https://bau.example/karriere')['jobs']),
                         ['Fliesenleger (m/w/d)', 'Laminierer (m/w/d)'])
        # Closed wrappers, disabled detail links and closed ordinary job links stay closed.
        body = '''<html><body><h1>Karriere</h1>
          <div data-status="closed"><span>Maler (m/w/d)</span></div>
          <div class="card"><span>Schlosser (m/w/d)</span><a aria-disabled="true" href="/stellen/schlosser">Details</a></div>
          <a href="/jobs/elektriker-9" data-status="closed">Elektriker (m/w/d)</a></body></html>'''
        self.assertEqual(extract_html_jobs(body, 'https://bau.example/karriere')['jobs'], [])
        # The saved URL is the link as written; identity is normalized separately.
        body = '<html><body><h1>Karriere</h1><div><a href="/Stellen/Koch/?b=2&a=1">Koch (m/w/d)</a></div></body></html>'
        self.assertEqual(extract_html_jobs(body, 'https://acme.example/karriere')['jobs'][0]['url'],
                         'https://acme.example/Stellen/Koch/?b=2&a=1')

    def test_page_facts_never_fill_another_vacancy(self):
        row = {"@type": "JobPosting", "title": "Engineer 124", "url": "https://acme.example/careers?jobId=124",
               "description": "Build things for customers and teams."}
        body = (f'<html><body><script type="application/ld+json">{json.dumps(row)}</script>'
                '<h1>Engineer 123</h1><p>Standort: Berlin</p><p>Teilzeit</p><a href="/apply">Jetzt bewerben</a></body></html>')
        jobs = {job['title']: job for job in extract_html_jobs(body, 'https://acme.example/careers?jobId=123')['jobs']}
        self.assertEqual((jobs['Engineer 124']['location'], jobs['Engineer 124']['employment_type']), ('', None))

    def test_listing_link_and_detail_page_share_one_key(self):
        listing = {'url': 'https://bau.example/jobs/fliesenleger', 'title': 'Fliesenleger (m/w/d)',
                   'raw_metadata': {'extraction_method': 'html_marked_title'}}
        detail = {'url': 'https://bau.example/jobs/fliesenleger/', 'title': 'Fliesenleger (m/w/d)',
                  'raw_metadata': {'extraction_method': 'html_job_detail'}}
        self.assertEqual(html_job_key(listing), html_job_key(detail))

    def test_second_recheck_regressions(self):
        page = lambda inner: '<html><body><h1>Karriere</h1>' + inner + '</body></html>'
        # "Next 20 vacancies" keeps a first page partial, whatever its target.
        self.assertFalse(extract_html_jobs(page('<a href="/jobs/continued">Next 20 vacancies</a>'),
                                           'https://acme.example/jobs')['complete'])
        # A bare heading does not take a neighbouring card's link or replace it.
        body = page('''<section><h3>Cloud Engineer (m/w/d)</h3><p>Bonn</p></section>
          <div class="card"><a href="/stellen/senior-cloud">Senior Cloud Engineer (m/w/d)</a><span>Berlin</span></div>''')
        jobs = {job['title']: job for job in extract_html_jobs(body, 'https://acme.example/karriere')['jobs']}
        self.assertEqual(jobs['Senior Cloud Engineer (m/w/d)']['url'], 'https://acme.example/stellen/senior-cloud')
        self.assertIn('#role-', jobs['Cloud Engineer (m/w/d)']['url'])
        # A page naming one vacancy is that vacancy's page: its title under the page URL, partial.
        body = '''<html><body><h1>Elektriker (m/w/d)</h1><p>Wir suchen Verstärkung.</p>
          <a href="/apply?jobId=123">Apply</a></body></html>'''
        result = extract_html_jobs(body, 'https://acme.example/careers?jobId=123')
        self.assertFalse(result['complete'])
        self.assertEqual([job['url'] for job in result['jobs'] if job['title'] == 'Elektriker (m/w/d)'],
                         ['https://acme.example/careers?jobId=123'])
        # Hash routes name distinct postings.
        body = page('<div><a href="#/jobs/12">Koch (m/w/d)</a></div><div><a href="#/jobs/13">Koch (m/w/d)</a></div>')
        self.assertEqual(len(extract_html_jobs(body, 'https://acme.example/karriere')['jobs']), 2)
        # main's readings stay: "Job: …" links and numbered titles.
        body = page('<a href="/jobs/cloud-7"><h3>Job: Cloud Engineer (m/w/d)</h3></a>')
        self.assertEqual(extract_html_jobs(body, 'https://acme.example/karriere')['jobs'][0]['title'], 'Job: Cloud Engineer (m/w/d)')
        body = '<html><body><main><h1>Cloud Engineer (123)</h1><p>Wir suchen eine erfahrene Person für unser Team.</p><a href="/apply">Jetzt bewerben</a></main></body></html>'
        self.assertEqual(extract_html_jobs(body, 'https://acme.example/jobs/cloud-engineer-123/')['jobs'][0]['title'], 'Cloud Engineer (123)')
        # Closed wrappers hold for ordinary job links and section headings too; filter links are navigation.
        body = '''<html><body><h1>Karriere</h1><div data-status="closed"><a href="/jobs/maler-3">Maler (m/w/d)</a></div>
          <h2>Offene Stellen</h2><div class="job closed"><h3>Schreiner (m/w/d)</h3><p>Holzwerkstatt Bonn</p></div>
          <div><a href="/stellen?ref=lager">Lagerist (m/w/d)</a></div></body></html>'''
        self.assertEqual(extract_html_jobs(body, 'https://bau.example/karriere')['jobs'], [])

    def test_third_recheck_regressions(self):
        # A self-link with a real title keeps main's reading and id.
        body = '<html><body><h1>Jobs</h1><a href="/jobs/portal"><h3>Security Manager</h3></a></body></html>'
        self.assertEqual([job['title'] for job in extract_html_jobs(body, 'https://acme.example/jobs/portal')['jobs']
                          if job['raw_metadata']['extraction_method'] == 'html_job_link'], ['Security Manager'])
        # A skipped category heading does not end main's section of open positions.
        body = '''<html><body><h1>Karriere</h1><h2>Open positions</h2><h3>Cloud Engineer</h3><p>Bonn</p>
          <h3>Praktikum</h3><p>Infos zum Praktikum</p><h3>Software Engineer</h3><p>Berlin</p></body></html>'''
        titles = {job['title'] for job in extract_html_jobs(body, 'https://acme.example/careers')['jobs']}
        # (main also reads "Praktikum" there; its rows are kept as main reads them.)
        self.assertTrue({'Cloud Engineer', 'Software Engineer'} <= titles)
        # "unfilled" is not "filled".
        body = '<html><body><h1>Karriere</h1><div data-status="unfilled"><a href="/stellen/c">Cloud Engineer (m/w/d)</a></div></body></html>'
        self.assertEqual(len(extract_html_jobs(body, 'https://acme.example/karriere')['jobs']), 1)
        # A heading never takes a neighbouring card's only link.
        body = '''<html><body><h1>Karriere</h1><section><h2>Software Engineer (m/w/d)</h2>
          <div class="card"><span>QA Tester (m/w/d)</span><a href="/stellen/qa">Details</a></div></section></body></html>'''
        jobs = {job['title']: job['url'] for job in extract_html_jobs(body, 'https://acme.example/karriere')['jobs']}
        self.assertNotEqual(jobs.get('Software Engineer (m/w/d)'), 'https://acme.example/stellen/qa')
        # On a vacancy page, a wrapped <h1> title is the page's own posting.
        body = '''<html><body><h1><span>Cloud Engineer (m/w/d)</span></h1><h2>QA Engineer (m/w/d)</h2>
          <p>Wir suchen Verstärkung.</p><a href="/apply">Apply</a></body></html>'''
        jobs = {job['title']: job['url'] for job in extract_html_jobs(body, 'https://acme.example/careers?jobId=7')['jobs']}
        self.assertEqual(jobs.get('Cloud Engineer (m/w/d)'), 'https://acme.example/careers?jobId=7')
        # A hash route main already reads keeps its row; the next route is new.
        body = '<html><body><h1>Jobs</h1><div><a href="/jobs/list#/jobs/12">Koch (m/w/d)</a></div><div><a href="/jobs/list#/jobs/13">Koch (m/w/d)</a></div></body></html>'
        urls = sorted(job['url'] for job in extract_html_jobs(body, 'https://acme.example/karriere')['jobs'])
        self.assertEqual(urls, ['https://acme.example/jobs/list#/jobs/12', 'https://acme.example/jobs/list#/jobs/13'])

    def test_fourth_recheck_regressions(self):
        from hiring_scraper.ats import parse_feed
        # Postings only the marked-title reader finds never make an unknown page complete.
        body = b'<html><body><a href="/stellen/fliese">Fliesenleger (m/w/d)</a></body></html>'
        result = parse_feed('html_jobs', body, 'https://bau.example/careers')
        self.assertEqual([job['title'] for job in result['jobs']], ['Fliesenleger (m/w/d)'])
        self.assertFalse(result['complete'])
        # main's rows stay: numbered titles under open positions and on hash routes.
        body = '''<html><body><h1>Karriere</h1><h2>Open positions</h2><h3>Cloud Engineer 37</h3><p>Bonn</p></body></html>'''
        self.assertIn('Cloud Engineer 37', {job['title'] for job in extract_html_jobs(body, 'https://acme.example/careers')['jobs']})
        # A "not-filled" card stays open; "unfilled" wording is not closed wording.
        body = '''<html><body><h1>Karriere</h1><div class="card" data-status="not-filled"><a href="/s/1">Maurer (m/w/d)</a></div>
          <div class="card"><a href="/s/2">Dachdecker (m/w/d)</a><p>This position is unfilled</p></div></body></html>'''
        self.assertEqual(len(extract_html_jobs(body, 'https://bau.example/karriere')['jobs']), 2)
        # An added posting never takes a neighbouring card's link, location or the page-wide place.
        body = '''<html><body><h1>Karriere</h1><section><h2>Fliesenleger (m/w/d)</h2>
          <div class="card"><h3>Warehouse operative</h3><p>Berlin</p><a href="/stellen/warehouse">Details</a></div></section>
          <div class="card"><h3>Koch (m/w/d)</h3><p>Standort: Bonn</p></div></body></html>'''
        jobs = {job['title']: job for job in extract_html_jobs(body, 'https://bau.example/karriere')['jobs']}
        self.assertNotEqual(jobs['Fliesenleger (m/w/d)']['url'], 'https://bau.example/stellen/warehouse')
        self.assertEqual(jobs['Fliesenleger (m/w/d)']['location'], '')

    def test_search_engine_pattern_spares_real_titles(self):
        body = '''<html><body><h1>Karriere</h1><a href="/jobs/test">Software Engineer – Search Jobs</a>
          <a href="/jobs/placement">Job Placement Specialist in Berlin</a><a href="/jobs/kaelte">Kältetechniker Jobs</a></body></html>'''
        titles = {job['title'] for job in extract_html_jobs(body, 'https://acme.example/karriere')['jobs']}
        self.assertTrue({'Software Engineer – Search Jobs', 'Job Placement Specialist'} <= titles)
        self.assertNotIn('Kältetechniker Jobs', titles)

    def test_rows_main_already_reads_keep_their_ids(self):
        # main reads these postings as headings under "Offene Stellen"; a link to the same posting
        # must not replace that row (its id carries Journey history).
        body = '''<html><body><h1>Karriere</h1><h2>Offene Stellen</h2>
          <div class="job"><h3>Werkstudent KI &amp; Digitalisierung (m/w/d)</h3><p>Duisburg</p>
            <a href="/stellen/werkstudent-ki">Mehr erfahren</a></div></body></html>'''
        before = [job for job in extract_html_jobs(body, 'https://hgk.example/karriere')['jobs']
                  if job['raw_metadata']['extraction_method'] == 'open_positions_heading']
        self.assertEqual(len(before), 1)
        jobs = extract_html_jobs(body, 'https://hgk.example/karriere')['jobs']
        self.assertEqual([job['id'] for job in jobs], [before[0]['id']])

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
