"""Regressions for contracts observed in the Bonn live benchmark."""
import copy
import json
import unittest
from hiring_scraper.ats import identify, parse_feed, fetch_feed
from hiring_scraper.discovery import discover
from hiring_scraper.html_jobs import extract_html_jobs, html_job_key
from hiring_scraper.pages import inspect_page
from tests.test_discovery import FixtureClient


def phenom(rows, total=126, base='https://careers.example/de/'):
    config = {'baseUrl': base, 'siteType': 'external'}
    ddo = {'eagerLoadRefineSearch': {'status': 200, 'hits': len(rows), 'totalHits': total, 'data': {'jobs': rows}}}
    return '<script>var phApp = phApp || '+json.dumps(config)+'; phApp.ddo = '+json.dumps(ddo)+'; phApp.urlMap = '+json.dumps({'job': 'job/:jobSeqNo/:title'})+';</script>'


class BonnDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.row = {'title': 'Softwareentwickler Steuerungstechnik (m/w/d)', 'jobSeqNo': 'ABC123',
                    'siteType': 'external', 'visibilityType': 'External', 'location': 'Bonn, Germany',
                    'country': 'Germany', 'city': 'Bonn', 'workHours': 'Vollzeit',
                    'postedDate': '2026-10-08T10:53:09Z', 'descriptionTeaser': 'Work on control systems.'}

    def test_phenom_search_records_become_jobs_without_executing_javascript(self):
        result = extract_html_jobs(phenom([self.row]), 'https://careers.example/de/search-results')
        self.assertEqual(len(result['jobs']), 1)
        job = result['jobs'][0]
        self.assertEqual(job['title'], 'Softwareentwickler Steuerungstechnik (m/w/d)')
        self.assertEqual(job['id'], 'ABC123')
        self.assertEqual(job['locations'], [{'label': 'Bonn', 'country_code': 'DE'}])
        self.assertFalse(result['complete'])
        self.assertNotIn('skills', job)
        self.assertNotIn('is_remote', {k:v for k,v in job.items() if v is not None})

    def test_phenom_url_map_may_end_at_script_boundary_without_semicolon(self):
        source = phenom([self.row]).replace(';</script>', '</script>')
        result = extract_html_jobs(source, 'https://careers.example/de/search-results')
        self.assertEqual(len(result['jobs']), 1)

    def test_phenom_identity_keeps_same_title_distinct_jobs(self):
        row2 = {**self.row, 'jobSeqNo': 'ABC456'}
        jobs = extract_html_jobs(phenom([self.row, row2]), 'https://careers.example/de/search-results')['jobs']
        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(html_job_key(jobs[0]), html_job_key(jobs[1]))

    def test_phenom_rejects_private_inactive_initiative_and_cross_origin_config(self):
        for patch in [{'siteType':'internal'}, {'visibilityType':'Internal'}, {'closed':True},
                      {'title':'Initiativbewerbung (m/w/d)'}, {'jobSeqNo':'../evil'}, {'isPrivate':True}]:
            with self.subTest(patch=patch):
                jobs = extract_html_jobs(phenom([{**self.row, **patch}]), 'https://careers.example/de/search-results')['jobs']
                self.assertEqual(jobs, [])
        self.assertEqual(extract_html_jobs(phenom([self.row],base='https://other.example/de/'), 'https://careers.example/de/search-results')['jobs'], [])

    def test_searchaction_promotes_same_origin_listing_over_city_marketing_links(self):
        data={'@type':'WebSite','potentialAction':{'@type':'SearchAction','target':'https://careers.example/de/search-results?keywords={search_term_string}'}}
        source='<script type="application/ld+json">'+json.dumps(data)+'</script><a href="/de/city">Jobs in Trier</a>'
        candidates=inspect_page('https://careers.example/de/',source)['candidates']
        self.assertEqual(candidates[0]['url'], 'https://careers.example/de/search-results?keywords=')
        self.assertGreater(candidates[0]['score'], candidates[1]['score'])

    def test_unexpanded_template_links_do_not_consume_career_page_budget(self):
        source='<a href="{{oneTrust}}">Career site cookie settings</a><a href="%7B%7BoneTrust%7D%7D">Career site cookie settings</a>'
        self.assertEqual(inspect_page('https://careers.example/de/',source)['candidates'],[])

    def test_searchaction_rejects_external_or_non_job_targets(self):
        for target in ['https://evil.example/search-results?q={search_term_string}', 'https://careers.example/delete?q={search_term_string}']:
            data={'@type':'WebSite','potentialAction':{'@type':'SearchAction','target':target}}
            self.assertEqual(inspect_page('https://careers.example/de/', '<script type="application/ld+json">'+json.dumps(data)+'</script>')['candidates'], [])

    def test_location_campaign_prioritizes_matching_career_landing_page(self):
        home='https://www.dhl.de/'
        career='https://careers.dhl.com/eu/de'
        search='https://careers.dhl.com/eu/de/search-results?keywords='
        bonn='https://careers.dhl.com/eu/de/jobs-in-bonn'
        cities=('Aachen','Berlin','Bremen','Dresden','Essen','Hannover','Kassel',
                'Koeln','Leipzig','Mainz','Muenchen','Nuernberg','Potsdam','Stuttgart',
                'Trier','Ulm','Wiesbaden','Wuppertal','Wuerzburg','Zwickau','Erfurt',
                'Freiburg','Hamburg','Jena')
        city_links=''.join(
            f'<a href="/eu/de/{city.casefold()}">Jobs in {city}</a>' for city in cities
        )
        pages={
            home:f'<a href="{career}">Karriere</a>',
            career:('<script type="application/ld+json">'+json.dumps({
                '@type':'WebSite','potentialAction':{'@type':'SearchAction',
                'target':search.replace('keywords=', 'keywords={search_term_string}')}})
                +'</script>'+city_links+f'<a href="{bonn}">Jobs in Bonn</a>'),
            search:'<h1>Stellenangebote</h1>',
            bonn:'<h1>Jobs in Bonn</h1>',
        }
        result=discover({'name':'DHL','website':home,'preferred_locations':['Bonn']},
                        FixtureClient(pages),max_pages=4,max_depth=3)
        self.assertIn(bonn,{page['url'] for page in result['pages']})

    def test_career_discovery_follows_three_site_path_to_employer_verified_jobs(self):
        home='https://www.acme.de/'
        career='https://careers.acme.com/de/'
        vendor='https://acme.talent-platform.example/jobs'
        detail='https://acme.talent-platform.example/job/123/software-engineer'
        posting={'@context':'https://schema.org','@type':'JobPosting',
                 'title':'Software Engineer','url':detail,
                 'hiringOrganization':{'@type':'Organization','name':'Acme GmbH'}}
        pages={home:f'<a href="{career}">Karriere</a>',
               career:f'<title>Acme Careers</title><h1>Jobs bei Acme</h1><a href="{vendor}">Alle Jobs</a>',
               vendor:f'<h1>Acme vacancies</h1><a href="{detail}">Software Engineer</a>',
               detail:'<script type="application/ld+json">'+json.dumps(posting)+'</script>'}
        result=discover({'name':'Acme GmbH','website':home},FixtureClient(pages),max_pages=8,max_depth=4)
        self.assertEqual(result['status'],'jobs_extracted')
        self.assertEqual(sum(board['job_count'] for board in result['boards']),1)
        self.assertIn(detail,{page['url'] for page in result['pages']})

    def test_softgarden_auth_routes_are_not_boards(self):
        for path in ['/sign-in?l=de', '/sign-up?l=de', '/login', '/de/sign-in']:
            self.assertIsNone(identify('https://acme.softgarden.io'+path))

    def test_softgarden_server_rendered_job_links_support_numeric_detail_routes(self):
        body='<h1>Jobs</h1><a href="/job/123/Software-Engineer?jobDbPVId=4">Software Engineer</a><a href="/job/456/Software-Engineer">Software Engineer</a><a hidden href="/job/789/Software-Engineer">Software Engineer</a>'
        result=extract_html_jobs(body,'https://acme.softgarden.io/de/vacancies')
        self.assertEqual({j['id'] for j in result['jobs']},{'123','456'})
        self.assertEqual(len({html_job_key(j) for j in result['jobs']}),2)
        self.assertEqual(extract_html_jobs(body,'https://shared.example/de/vacancies')['jobs'],[])

    def test_softgarden_job_contract_does_not_need_english_role_keywords(self):
        body='<a href="/job/123/Buchhalter">Buchhalter (m/w/d)</a><a href="/job/456/Initiativ">Initiativbewerbung (m/w/d)</a>'
        result=extract_html_jobs(body,'https://acme.softgarden.io/de/vacancies')
        self.assertEqual([j['title'] for j in result['jobs']],['Buchhalter (m/w/d)'])
        self.assertFalse(result['complete'])

    def test_unsupported_provider_details_survive_board_limit(self):
        home='https://acme.example/'
        board='https://acme.softgarden.io/de/vacancies'
        urls=[f'https://acme.softgarden.io/job/{i}/Software-Engineer' for i in range(1,5)]
        pages={home:f'<a href="{board}">Jobs</a>', board:'<h1>Acme Jobs</h1>'+''.join(f'<a href="{u}">Software Engineer</a>' for u in urls)}
        for i,u in enumerate(urls):
            pages[u]='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':f'Software Engineer {i}', 'url':u, 'hiringOrganization':{'name':'Acme GmbH'}})+'</script>'
        result=discover({'name':'Acme GmbH','website':home},FixtureClient(pages),max_pages=6)
        self.assertEqual(sum(len(b['jobs']) for b in result['boards']),4)

    def test_nested_branded_career_pages_keep_verified_gateway_provenance(self):
        home='https://www.acme.de/'
        gateway='https://careers.acme.com/de/'
        listing='https://careers.acme.com/de/search-results'
        client=FixtureClient({home:f'<a href="{gateway}">Careers</a>',
            gateway:f'<title>Acme Careers</title><h1>Acme Jobs</h1><a href="{listing}">Jobsuche</a>',
            listing:'<h1>Jobs</h1><p>Apply now</p>'+phenom([self.row],base=gateway)})
        result=discover({'name':'Acme','website':home},client,max_pages=4)
        self.assertEqual(sum(len(b['jobs']) for b in result['boards']),1)

    def test_unbranded_gateway_does_not_trust_nested_employerless_rows(self):
        home='https://www.acme.de/'
        gateway='https://careers.shared.com/de/'
        listing='https://careers.shared.com/de/search-results'
        client=FixtureClient({home:f'<a href="{gateway}">Careers</a>',
            gateway:f'<title>Global Jobs</title><h1>Jobs</h1><a href="{listing}">Jobsuche</a>',
            listing:'<h1>Jobs</h1><p>Apply now</p>'+phenom([self.row],base=gateway)})
        result=discover({'name':'Acme','website':home},client,max_pages=4)
        self.assertEqual(sum(len(b['jobs']) for b in result['boards']),0)

    def test_shared_host_path_tenant_does_not_trust_its_global_listing(self):
        home='https://www.acme.de/'
        gateway='https://jobs.shared.com/acme/'
        listing='https://jobs.shared.com/jobs/'
        client=FixtureClient({home:f'<a href="{gateway}">Careers</a>',
            gateway:f'<title>Acme Jobs</title><h1>Acme Careers</h1><a href="{listing}">Jobsuche</a>',
            listing:'<h1>Jobs</h1><p>Apply now</p>'+phenom([self.row],base='https://jobs.shared.com/')})
        result=discover({'name':'Acme','website':home},client,max_pages=4)
        self.assertEqual(sum(len(b['jobs']) for b in result['boards']),0)

    def test_telekom_public_search_contract_parses_with_employer_and_partial_scope(self):
        data={'status_code':200,'message':'Results found','data':{'234492':{'company':'Deutsche Telekom AG','job_title':'Rechtsreferendar*in (m/w/d)','location':'Bonn, Deutschland','city':'Bonn','job_type':'Regulär (Teilzeit möglich)','category':'Zentralfunktionen','skills':['Handelsrecht']}}}
        parsed=parse_feed('telekom',json.dumps(data),'https://careers.telekom.com/de/jobs')
        self.assertFalse(parsed['complete'])
        job=parsed['jobs'][0]
        self.assertEqual(job['url'],'https://careers.telekom.com/de/jobs/rechtsreferendarin-m-w-d-234492')
        self.assertEqual(job['raw_metadata']['hiring_organization'],{'name':'Deutsche Telekom AG'})
        self.assertNotIn('skills',job)
        self.assertNotIn('description',job)

    def test_telekom_discovery_filters_other_group_employers(self):
        url='https://careers.telekom.com/api/jobs-proxy/keyword_search?locale=de'
        row={'company':'Deutsche Telekom AG','job_title':'Engineer','city':'Bonn','location':'Bonn, Deutschland'}
        data={'status_code':200,'data':{'1':row,'2':{**row,'company':'Unrelated GmbH'}}}
        client=FixtureClient({'https://www.telekom.com/':'<a href="https://careers.telekom.com/de/jobs">Jobs</a>',url:json.dumps(data)})
        result=discover({'name':'Deutsche Telekom','website':'https://www.telekom.com/'},client,max_pages=3)
        self.assertEqual([j['id'] for b in result['boards'] for j in b['jobs']],['1'])
        self.assertFalse(result['boards'][0]['complete'])

    def test_telekom_only_canonical_jobs_routes_are_detected(self):
        provider=identify('https://careers.telekom.com/de/jobs')
        self.assertIsNotNone(provider)
        self.assertEqual(provider['feed_url'],'https://careers.telekom.com/api/jobs-proxy/keyword_search?locale=de')
        for u in ['https://careers.telekom.com.evil.example/de/jobs','https://careers.telekom.com/de/jobs/foo-1','https://careers.telekom.com/api/private']:
            self.assertIsNone(identify(u))

    def test_telekom_explicit_location_filter_is_preserved_in_public_feed(self):
        provider=identify('https://careers.telekom.com/de/jobs?location=Bonn')
        self.assertIsNotNone(provider)
        self.assertEqual(provider['feed_url'],'https://careers.telekom.com/api/jobs-proxy/keyword_search?locale=de&location=Bonn')
        self.assertEqual(provider['board_url'],'https://careers.telekom.com/de/jobs')
        for query in ['location=Bonn&location=Berlin', 'location=', 'location=Bonn&private=true']:
            self.assertIsNone(identify('https://careers.telekom.com/de/jobs?'+query))

    def test_telekom_prefixed_native_id_is_valid_without_losing_the_entire_batch(self):
        row={'company':'BUYIN GmbH','job_title':'Dual Master Procurement','city':'Bonn','location':'Bonn, Deutschland'}
        parsed=parse_feed('telekom',json.dumps({'status_code':200,'data':{'teamtailor_8078885':row}}),'https://careers.telekom.com/de/jobs')
        self.assertEqual(parsed['jobs'][0]['id'],'teamtailor_8078885')
        self.assertTrue(parsed['jobs'][0]['url'].endswith('-teamtailor_8078885'))

    def test_telekom_language_aliases_do_not_create_duplicate_group_feeds(self):
        row={'company':'Deutsche Telekom AG','job_title':'Engineer','city':'Bonn','location':'Bonn, Deutschland'}
        body=json.dumps({'status_code':200,'data':{'1':row}})
        client=FixtureClient({'https://www.telekom.com/':'<a href="https://careers.telekom.com/de/jobs">Jobs</a><a href="https://careers.telekom.com/en/jobs">Jobs</a>',
            'https://careers.telekom.com/api/jobs-proxy/keyword_search?locale=de':body,
            'https://careers.telekom.com/api/jobs-proxy/keyword_search?locale=en':body})
        result=discover({'name':'Deutsche Telekom','website':'https://www.telekom.com/'},client,max_pages=3)
        self.assertEqual(sum(len(b['jobs']) for b in result['boards']),1)

    def test_telekom_invalid_rows_fail_closed(self):
        for data in [{'status_code':500,'data':{}},{'status_code':200,'data':[]},{'status_code':200,'data':{'1':{'company':'Acme'}}}]:
            with self.assertRaises(ValueError):parse_feed('telekom',json.dumps(data),'https://careers.telekom.com/de/jobs')

if __name__=='__main__':unittest.main()


class BonnRefreshTests(unittest.TestCase):
    def test_telekom_refresh_preserves_employer_scope_and_does_not_close_unseen_jobs(self):
        from datetime import timedelta
        from unittest.mock import patch
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from hiring_scraper.app import worker
        from hiring_scraper.app.models import Base, Company, Job, JobFeed, ScanRun, utcnow
        engine=create_engine('sqlite://')
        Base.metadata.create_all(engine)
        factory=sessionmaker(bind=engine,expire_on_commit=False)
        try:
            with factory.begin() as session:
                company=Company(source='test',source_id='telekom',name='Deutsche Telekom',website_url='https://www.telekom.com/')
                feed=JobFeed(company=company,provider='telekom',board_url='https://careers.telekom.com/de/jobs',
                    feed_url='https://careers.telekom.com/api/jobs-proxy/keyword_search?locale=de',status='incomplete')
                old=Job(feed=feed,external_id='old',title='Old Engineer',url='https://careers.telekom.com/de/jobs/old',
                    last_seen_at=utcnow()-timedelta(days=15),missing_complete_scans=1)
                session.add(old);session.flush()
                scan=ScanRun(feed_id=feed.id,status='running')
                session.add(scan);session.flush();feed_id,scan_id=feed.id,scan.id
            row={'company':'Deutsche Telekom AG','job_title':'Engineer','city':'Bonn','location':'Bonn, Deutschland'}
            body=json.dumps({'status_code':200,'data':{'1':row,'2':{**row,'company':'Unrelated GmbH'}}}).encode()
            with patch.object(worker,'SessionLocal',factory):
                worker._persist_result(feed_id,scan_id,{'state':'ok','status':200},body)
            with factory() as session:
                jobs=session.scalars(select(Job)).all()
                self.assertEqual({j.external_id for j in jobs},{'old','1'})
                old=next(j for j in jobs if j.external_id=='old')
                self.assertTrue(old.is_active)
                self.assertEqual(old.missing_complete_scans,1)
                self.assertEqual(session.get(JobFeed,feed_id).status,'incomplete')
        finally:engine.dispose()


class BonnBenchmarkReplayTests(unittest.TestCase):
    def test_concurrent_offline_replay_preserves_capture_evidence(self):
        import hashlib
        from argparse import Namespace
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from experiments import bonn_discovery_benchmark as benchmark
        real_client = benchmark.Client
        with TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / 'http'
            cache.mkdir()
            body = b'<html>Fixture</html>'
            record = {'url': 'https://example.com/', 'capture': 'fixture',
                      'state': 'ok', 'status': 200,
                      'sha256': hashlib.sha256(body).hexdigest()}
            (cache / 'fixture.body').write_bytes(body)
            (cache / 'fixture.json').write_text(json.dumps(record))
            original = {p.name: p.read_bytes() for p in cache.iterdir()}
            benchmark.write(root / 'cohort.json', [{'name': str(i), 'website': record['url']} for i in range(32)])
            def make_client(out, **kwargs):
                self.assertNotEqual(Path(out).resolve(), cache.resolve())
                return real_client(out, **kwargs)
            def discover(seed, client, **kwargs):
                meta, captured = client._raw(seed['website'])
                self.assertEqual(captured, body)
                return {'name': seed['name'], 'status': 'career_found',
                        'pages': [{'fetch_state': meta['state']}], 'boards': []}
            args = Namespace(root=root, variant='candidate', baseline='unused',
                             workers=8, requests=2500, live=False)
            with patch.object(benchmark, 'Client', side_effect=make_client), patch.object(benchmark.discovery, 'discover', side_effect=discover), patch('builtins.print'):
                benchmark.crawl(args)
            self.assertEqual(len(benchmark.read(root / 'raw' / 'candidate.json')), 32)
            self.assertEqual({p.name: p.read_bytes() for p in cache.iterdir()}, original)


class PublicMicrodataTests(unittest.TestCase):
    def test_html_microdata_recovers_source_posting_and_nested_employer_location(self):
        html = '''<main itemscope itemtype="https://schema.org/JobPosting">
        <h1 itemprop="title">Wissenschaftlicher Mitarbeiter KI (all genders)</h1>
        <meta itemprop="identifier" content="85561">
        <meta itemprop="datePosted" content="2026-10-08">
        <div itemprop="hiringOrganization" itemscope itemtype="https://schema.org/Organization">
        <meta itemprop="name" content="Fraunhofer-Gesellschaft"></div>
        <div itemprop="jobLocation" itemscope itemtype="https://schema.org/Place">
        <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
        <meta itemprop="addressLocality" content="Sankt Augustin">
        <meta itemprop="addressCountry" content="DE"></div></div>
        <div itemprop="description">Wir suchen Forschende für vertrauenswürdige KI.</div>
        </main><footer>Other employer</footer>'''
        parsed=extract_html_jobs(html,'https://jobs.example/job/researcher/85561/')
        self.assertEqual(len(parsed['jobs']),1)
        job=parsed['jobs'][0]
        self.assertEqual(job['id'],'85561')
        self.assertEqual(job['locations'],[{'label':'Sankt Augustin','country_code':'DE'}])
        self.assertEqual(job['raw_metadata']['hiring_organization']['name'],'Fraunhofer-Gesellschaft')
        self.assertNotIn('Other employer',job['description'])
        self.assertFalse(parsed['complete'])

    def test_hidden_ambiguous_and_initiative_microdata_are_not_vacancies(self):
        for attrs,title in [('hidden','Engineer'),('', 'Initiativbewerbung')]:
            html=f'<div {attrs} itemscope itemtype="https://schema.org/JobPosting"><span itemprop="title">{title}</span><p itemprop="description">We seek people for this role.</p></div>'
            self.assertEqual(extract_html_jobs(html,'https://jobs.example/careers')['jobs'],[])
        html='<div itemscope itemtype="https://schema.org/JobPosting"><meta itemprop="title" content="Engineer"><meta itemprop="title" content="Different Engineer"><p itemprop="description">We seek people for this role.</p></div>'
        self.assertEqual(extract_html_jobs(html,'https://jobs.example/careers')['jobs'],[])

class PublicApplicationFormTests(unittest.TestCase):
    def test_german_job_detail_with_cv_form_is_confirmed_without_english_role_word(self):
        html='<main><h1>Immobilienberater Wohnimmobilien (m/w/d)</h1><p>Beraten Sie unsere Kunden in Bonn und Köln.</p><h2>Jetzt bewerben!</h2><form><label>Lebenslauf</label><div class="ginput_container_fileupload"><button>Datei auswählen</button></div></form></main>'
        jobs=extract_html_jobs(html,'https://company.example/jobs/immobilienberater')['jobs']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['title'],'Immobilienberater Wohnimmobilien (m/w/d)')

    def test_newsletter_and_initiative_forms_do_not_confirm_job(self):
        for title,form in [('Immobilienberater','<form>E-Mail<input type="email"></form>'),('Initiativbewerbung','<form>Lebenslauf<input type="file"></form>'),('Immobilienberater','<form hidden>Lebenslauf<input type="file"></form>')]:
            html=f'<main><h1>{title}</h1><h2>Jetzt bewerben!</h2>{form}</main>'
            self.assertEqual(extract_html_jobs(html,'https://company.example/jobs/example')['jobs'],[])

class ListingHeadingPrecisionTests(unittest.TestCase):
    def test_student_listing_heading_is_not_a_specific_vacancy(self):
        html='<main><h1>Karriere</h1><h2>Stellenangebote</h2><h3>Stellenangebote für Studentische Hilfskräfte</h3><p>Alle Angebote im Stellenportal.</p></main>'
        self.assertEqual(extract_html_jobs(html,'https://company.example/karriere')['jobs'],[])

class MicrodataIdentityTests(unittest.TestCase):
    def test_distinct_posting_ids_on_one_listing_do_not_merge(self):
        html=''.join(f'<div itemscope itemtype="https://schema.org/JobPosting"><meta itemprop="identifier" content="{id}"><meta itemprop="title" content="{title}"><p itemprop="description">We seek people for this role.</p></div>' for id,title in [('A1','Engineer'),('B2','Researcher')])
        self.assertEqual({job['id'] for job in extract_html_jobs(html,'https://example.org/careers')['jobs']},{'A1','B2'})

    def test_jsonld_and_microdata_for_same_posting_merge(self):
        posting={'@type':'JobPosting','title':'Engineer','url':'https://example.org/jobs/engineer','identifier':'A1','description':'We seek people for this role.'}
        html='<script type="application/ld+json">'+json.dumps(posting)+'</script><div itemscope itemtype="https://schema.org/JobPosting"><meta itemprop="identifier" content="A1"><meta itemprop="title" content="Engineer"><link itemprop="url" href="https://example.org/jobs/engineer"><p itemprop="description">We seek people for this role.</p></div>'
        jobs=extract_html_jobs(html,'https://example.org/jobs/engineer')['jobs']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['id'],'A1')

    def test_relative_microdata_url_resolves_to_source_origin(self):
        html='<div itemscope itemtype="https://schema.org/JobPosting"><meta itemprop="title" content="Engineer"><link itemprop="url" href="/jobs/engineer"><p itemprop="description">We seek people for this role.</p></div>'
        jobs=extract_html_jobs(html,'https://example.org/careers')['jobs']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['url'],'https://example.org/jobs/engineer')

class DetailCompletenessTests(unittest.TestCase):
    def test_single_detail_cannot_close_other_jobs_merged_from_neighbor_pages(self):
        body='<main><h1>Immobilienberater (m/w/d)</h1><p>Beraten Sie unsere Kunden.</p><h2>Jetzt bewerben!</h2><form>Lebenslauf<input type="file"></form></main>'
        parsed=parse_feed('html_jobs',body,'https://company.example/jobs/immobilienberater/')
        self.assertEqual(len(parsed['jobs']),1)
        self.assertFalse(parsed['complete'])

class NativeFeedProvenanceTests(unittest.TestCase):
    def test_multihop_other_company_career_footer_does_not_assign_its_feed(self):
        pages={'https://leanix.example/':'<a href="https://sap.example/careers">Karriere</a>',
               'https://sap.example/careers':'<h1>Careers at SAP</h1><a href="https://reltio.example/careers">Careers at Reltio</a>',
               'https://reltio.example/careers':'<h1>Careers at Reltio</h1><a href="https://job-boards.greenhouse.io/reltio">Jobs</a>',
               'https://boards-api.greenhouse.io/v1/boards/reltio/jobs?content=true':json.dumps({'jobs':[{'id':1,'title':'Engineer','absolute_url':'https://job-boards.greenhouse.io/reltio/jobs/1','location':{'name':'Bonn'}}]})}
        result=discover({'name':'Leanix','website':'https://leanix.example/'},FixtureClient(pages),max_pages=12)
        self.assertEqual(sum(len(b.get('jobs',[])) for b in result['boards']),0)
        self.assertTrue(result.get('unverified_external_ats'))

    def test_direct_official_link_and_branded_gateway_keep_public_native_feed(self):
        body=json.dumps({'jobs':[{'id':1,'title':'Engineer','absolute_url':'https://job-boards.greenhouse.io/acme/jobs/1','location':{'name':'Bonn'}}]})
        for home in ['<a href="https://job-boards.greenhouse.io/acme">Jobs</a>', '<a href="https://careers.acme.example/jobs">Jobs</a>']:
            pages={'https://acme.example/':home,'https://careers.acme.example/jobs':'<h1>Acme careers</h1><a href="https://job-boards.greenhouse.io/acme">Jobs</a>',
                   'https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true':body}
            result=discover({'name':'Acme','website':'https://acme.example/'},FixtureClient(pages),max_pages=12)
            self.assertEqual(sum(len(b.get('jobs',[])) for b in result['boards']),1)

    def test_multihop_unrelated_json_feed_is_also_rejected(self):
        payload={'@type':'DataFeed','numberOfItems':1,'dataFeedElement':[{'@type':'JobPosting','identifier':'1','title':'Engineer','url':'https://reltio.example/jobs/1','hiringOrganization':{'name':'Reltio'}}]}
        pages={'https://leanix.example/':'<a href="https://sap.example/careers">Karriere</a>',
               'https://sap.example/careers':'<h1>Careers at SAP</h1><a href="https://reltio.example/careers">Careers at Reltio</a>',
               'https://reltio.example/careers':'<h1>Careers at Reltio</h1><a href="/jobs.feed.json">Job feed</a>',
               'https://reltio.example/jobs.feed.json':json.dumps(payload)}
        result=discover({'name':'Leanix','website':'https://leanix.example/'},FixtureClient(pages),max_pages=12)
        self.assertEqual(sum(len(b.get('jobs',[])) for b in result['boards']),0)
        self.assertTrue(result.get('unverified_external_ats'))

    def test_official_page_can_directly_link_an_unbranded_public_json_feed(self):
        payload={'@type':'DataFeed','numberOfItems':1,'dataFeedElement':[{'@type':'JobPosting','identifier':'1','title':'Engineer','url':'https://acme.example/jobs/1'}]}
        pages={'https://acme.example/':'<a href="https://shared.example/jobs.feed.json">Job feed</a>',
               'https://shared.example/jobs.feed.json':json.dumps(payload)}
        result=discover({'name':'Acme','website':'https://acme.example/'},FixtureClient(pages),max_pages=3)
        self.assertEqual(sum(len(b.get('jobs',[])) for b in result['boards']),1)

class PersonioPostingScopeTests(unittest.TestCase):
    def test_explicit_single_personio_link_keeps_scope_on_discovery_and_refresh(self):
        provider=identify('https://group.jobs.personio.de/job/42?language=de')
        self.assertEqual(provider['board_url'],'https://group.jobs.personio.de/job/42')
        body='<workzag-jobs><position><id>42</id><name>Engineer</name><office>Bonn</office></position><position><id>99</id><name>Other group job</name><office>Berlin</office></position></workzag-jobs>'
        parsed=parse_feed('personio',body,provider['board_url'])
        self.assertEqual([job['id'] for job in parsed['jobs']],['42'])
        self.assertEqual(parsed['jobs'][0]['url'],'https://group.jobs.personio.de/job/42')
        self.assertFalse(parsed['complete'])
        client=FixtureClient({'https://acme.example/':'<a href="https://group.jobs.personio.de/job/42">Jobs</a>',provider['feed_url']:body})
        result=discover({'name':'Acme','website':'https://acme.example/'},client,max_pages=12)
        self.assertEqual([job['id'] for board in result['boards'] for job in board['jobs']],['42'])

    def test_linked_personio_postings_share_one_native_feed_limit(self):
        class TrackingClient(FixtureClient):
            def __init__(self,pages):
                super().__init__(pages)
                self.feed_requests=[]
            def get_feed(self,url):
                self.feed_requests.append(url)
                return super().get_feed(url)

        home='https://acme.example/'
        links=''.join(f'<a href="https://group.jobs.personio.de/job/{job_id}">Job {job_id}</a>'
                      for job_id in range(1,6))
        xml='<workzag-jobs>'+''.join(
            f'<position><id>{job_id}</id><name>Engineer {job_id}</name></position>'
            for job_id in range(1,6))+'</workzag-jobs>'
        client=TrackingClient({home:links,'https://group.jobs.personio.de/xml':xml})

        result=discover({'name':'Acme','website':home},client,max_pages=1)

        jobs=[job for board in result['boards'] for job in board['jobs']]
        self.assertEqual({job['id'] for job in jobs},{'1','2','3','4','5'})
        self.assertEqual(len(result['boards']),1)
        self.assertEqual(result['boards'][0]['personio_posting_scopes'],['1','2','3','4','5'])
        self.assertEqual(client.feed_requests,['https://group.jobs.personio.de/xml'])
        self.assertEqual(result['limits']['max_boards'],3)

    def test_personio_scoped_routes_have_an_explicit_resource_cap(self):
        class TrackingClient(FixtureClient):
            def __init__(self,pages):
                super().__init__(pages)
                self.feed_requests=[]
            def get_feed(self,url):
                self.feed_requests.append(url)
                return super().get_feed(url)

        home='https://acme.example/'
        links=''.join(f'<a href="https://group.jobs.personio.de/job/{job_id}">Job {job_id}</a>'
                      for job_id in range(1,26))
        xml='<workzag-jobs>'+''.join(
            f'<position><id>{job_id}</id><name>Engineer {job_id}</name></position>'
            for job_id in range(1,26))+'</workzag-jobs>'
        client=TrackingClient({home:links,'https://group.jobs.personio.de/xml':xml})

        result=discover({'name':'Acme','website':home},client,max_pages=1)

        self.assertEqual(len(result['boards']),1)
        self.assertEqual(len(result['boards'][0]['personio_posting_scopes']),24)
        self.assertEqual(len(result['boards'][0]['jobs']),24)
        self.assertTrue(result['limits']['personio_scope_budget_reached'])
        self.assertEqual(client.feed_requests,['https://group.jobs.personio.de/xml'])

    def test_explicit_full_personio_board_authorization_survives_link_order(self):
        home='https://acme.example/'
        scoped='https://group.jobs.personio.de/job/1'
        full='https://group.jobs.personio.de'
        xml='<workzag-jobs><position><id>1</id><name>Engineer 1</name></position><position><id>2</id><name>Engineer 2</name></position></workzag-jobs>'
        for links in ([scoped,full],[full,scoped]):
            with self.subTest(links=links):
                page=''.join(f'<a href="{url}">Jobs</a>' for url in links)
                result=discover({'name':'Acme','website':home},FixtureClient(
                    {home:page,'https://group.jobs.personio.de/xml':xml}),max_pages=1)

                self.assertEqual(len(result['boards']),1)
                board=result['boards'][0]
                self.assertEqual(board['board_url'],full)
                self.assertEqual({job['id'] for job in board['jobs']},{'1','2'})
                self.assertTrue(board['complete'])

class PersonioScopedRefreshTests(unittest.TestCase):
    def test_one_xml_source_refreshes_all_explicitly_scoped_postings_without_group_rows(self):
        from unittest.mock import patch
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from hiring_scraper.app import worker
        from hiring_scraper.app.models import Base,Company,JobFeed,Job,ScanRun
        engine=create_engine('sqlite://');Base.metadata.create_all(engine)
        factory=sessionmaker(bind=engine,expire_on_commit=False)
        try:
            with factory.begin() as session:
                company=Company(source='test',source_id='personio',name='Acme',website_url='https://acme.example/')
                feed=JobFeed(company=company,provider='personio',board_url='https://group.jobs.personio.de/job/43',feed_url='https://group.jobs.personio.de/xml',status='incomplete')
                session.add_all([Job(feed=feed,external_id=id,title='Old '+id,url='https://group.jobs.personio.de/job/'+id,raw_metadata={'personio_posting_scope':id}) for id in ['42','43']]);session.flush()
                run=ScanRun(feed_id=feed.id,status='running');session.add(run);session.flush();feed_id,run_id=feed.id,run.id
            body=b'<workzag-jobs><position><id>42</id><name>Updated A</name></position><position><id>43</id><name>Updated B</name></position><position><id>99</id><name>Other group role</name></position></workzag-jobs>'
            with patch.object(worker,'SessionLocal',factory):worker._persist_result(feed_id,run_id,{'state':'ok','status':200},body)
            with factory() as session:
                jobs=session.scalars(select(Job)).all()
                self.assertEqual({job.external_id:job.title for job in jobs},{'42':'Updated A','43':'Updated B'})
                self.assertTrue(all(job.raw_metadata.get('personio_posting_scope')==job.external_id for job in jobs))
                self.assertEqual(session.get(JobFeed,feed_id).status,'incomplete')
        finally:engine.dispose()
