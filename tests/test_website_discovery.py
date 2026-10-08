"""Verify real identity decisions; only network boundaries use local doubles."""
import unittest
from hiring_scraper import website_discovery as websites


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        value = self.pages.get(url)
        if value is None:
            return {'state': 'robots_disallowed', 'url': url}, b''
        return {'state': 'ok', 'status': 200, 'content_type': 'text/html',
                'final_url': url}, value.encode()


def candidate(name='Northstar Robotics GmbH', **tags):
    return {'source_id': 'node/1', 'name': name, 'website_url': None,
            'source_url': 'https://www.openstreetmap.org/node/1',
            'tags': {'addr:city': 'Karlsruhe', 'addr:postcode': '76133', **tags}}


class WebsiteDiscoveryTests(unittest.TestCase):
    def test_email_domain_requires_public_page_identity_and_preserves_evidence(self):
        row = candidate(**{'contact:email': 'info@northstar-robotics.de'})
        pages = Pages({'https://northstar-robotics.de/':
                       '<title>Northstar Robotics</title><h1>Northstar Robotics GmbH</h1>'})
        decisions = websites.discover_missing_websites([row], pages)
        self.assertEqual(row['website_url'], 'https://northstar-robotics.de/')
        self.assertEqual(row['domain_match_method'], 'osm_email_verified_page')
        self.assertEqual(row['domain_evidence_url'], 'https://northstar-robotics.de/')
        self.assertTrue(decisions[0]['accepted'])

    def test_email_does_not_inherit_identity_from_a_partial_brand_match(self):
        for name, body in [
                ('Northstar Robotics GmbH', '<title>Northstar Inc</title>Northstar Robotics'),
                ('Northstar Robotics', '<title>Northstar Inc</title>Northstar Robotics'),
                ('Northstar Robotics GmbH', '<title>Northstar Inc</title>Client: Northstar Robotics GmbH')]:
            with self.subTest(name=name, body=body):
                row = candidate(name, email='info@northstar.com')
                websites.discover_missing_websites([row], Pages({'https://northstar.com/': body}))
                self.assertIsNone(row['website_url'])

    def test_search_verifies_name_and_location_and_skips_directories(self):
        row = candidate()
        pages = Pages({'https://northstar-robotics.de/contact/':
                       '<title>Northstar Robotics GmbH</title>76133 Karlsruhe'})
        decisions = websites.discover_missing_websites([row], pages, search=lambda query: [
            {'url': 'https://www.xing.com/pages/northstar-robotics'},
            {'url': 'https://northstar-robotics.de/contact/'}])
        self.assertEqual(row['website_url'], 'https://northstar-robotics.de/')
        self.assertEqual(row['domain_match_method'], 'web_search_verified_page')
        self.assertEqual(row['domain_evidence_url'], 'https://northstar-robotics.de/contact/')
        self.assertEqual(sum(d['accepted'] for d in decisions), 1)

    def test_rejects_same_name_wrong_city_parked_pages_and_partial_names(self):
        for html in ['<title>Northstar Robotics</title>Berlin 10115',
                     '<title>Domain for sale</title>Northstar Robotics 76133 Karlsruhe',
                     '<title>Northstar Logistics</title>76133 Karlsruhe']:
            with self.subTest(html=html):
                row = candidate()
                websites.discover_missing_websites([row], Pages({'https://northstar.de/': html}),
                                                   search=lambda q: [{'url': 'https://northstar.de/'}])
                self.assertIsNone(row['website_url'])

    def test_search_rejects_unlisted_directories_and_investor_articles(self):
        for name, url, html in [
                ('Mosaik Digital Network GmbH', 'https://agentur.de/karlsruhe/mosaik',
                 '<title>Mosaik Digital Network GmbH</title>76133 Karlsruhe'),
                ('R3DT GmbH', 'https://htgf.de/investment-in-r3dt/',
                 '<title>Investment in R3DT GmbH</title>76133 Karlsruhe')]:
            with self.subTest(url=url):
                row = candidate(name)
                websites.discover_missing_websites([row], Pages({url: html}),
                                                  search=lambda q: [{'url': url}])
                self.assertIsNone(row['website_url'])

    def test_does_not_resolve_ambiguous_search_domains(self):
        row = candidate()
        pages = Pages({url: '<title>Northstar Robotics GmbH</title>76133 Karlsruhe'
                       for url in ['https://northstar.de/', 'https://northstar.com/']})
        decisions = websites.discover_missing_websites([row], pages, search=lambda q: [
            {'url': 'https://northstar.de/'}, {'url': 'https://northstar.com/'}])
        self.assertIsNone(row['website_url'])
        self.assertTrue(any(d['reason'] == 'ambiguous_domains' for d in decisions))

    def test_search_does_not_assign_a_related_but_different_legal_entity(self):
        row = candidate('Vi2vi GmbH', **{'addr:city': 'Malsch', 'addr:postcode': '76316',
                                         'addr:street': 'Daimlerstraße'})
        pages = Pages({'https://vi2vi.com/':
                       '<title>vi2vi Gruppe</title>vi2vi Retail Solution GmbH Dieselstraße 11 76316 Malsch'})
        websites.discover_missing_websites([row], pages,
                                          search=lambda q: [{'url': 'https://vi2vi.com/'}])
        self.assertIsNone(row['website_url'])

    def test_free_email_private_links_blocked_pages_and_failures_remain_unresolved(self):
        for email in ['info@gmail.com', 'info@127.1', 'info@northstar.de']:
            row = candidate(email=email)
            websites.discover_missing_websites([row], Pages({}))
            self.assertIsNone(row['website_url'])
        row = candidate()
        def failed_search(q):
            raise OSError('provider unavailable')
        websites.discover_missing_websites([row], Pages({}), search=failed_search)
        self.assertIsNone(row['website_url'])

    def test_preserves_existing_homepage_and_bounds_searches(self):
        existing = candidate(); existing['website_url'] = 'https://existing.de/'
        rows = [existing, candidate(), {**candidate(), 'source_id': 'node/2'}]
        queries = []
        def search(q):
            queries.append(q)
            return []
        websites.discover_missing_websites(rows, Pages({}), search=search, max_searches=1)
        self.assertEqual(existing['website_url'], 'https://existing.de/')
        self.assertEqual(len(queries), 1)
        self.assertIn('Northstar Robotics', queries[0])
        self.assertIn('Karlsruhe', queries[0])

    def test_contact_query_fallback_keeps_legal_name_for_verification(self):
        row = candidate()
        queries = []
        def search(q):
            queries.append(q)
            return [{'url': 'https://northstar.de/contact/'}] if 'Kontakt Impressum' in q else []
        pages = Pages({'https://northstar.de/contact/':
                       '<title>Northstar Robotics GmbH</title>76133 Karlsruhe'})
        websites.discover_missing_websites([row], pages, search=search)
        self.assertEqual(row['website_url'], 'https://northstar.de/')
        self.assertNotIn('GmbH', queries[1])
        self.assertNotIn('76133', queries[1])
        self.assertIn('Kontakt Impressum', queries[1])
        self.assertEqual(row['domain_match_method'], 'web_search_verified_contact_fallback')

    def test_official_query_is_used_after_contact_fallback_fails(self):
        row = candidate()
        queries = []
        def search(q):
            queries.append(q)
            return [{'url': 'https://northstar.de/'}] if 'offizielle Website' in q else []
        websites.discover_missing_websites([row], Pages({'https://northstar.de/':
            '<title>Northstar Robotics GmbH</title>76133 Karlsruhe'}), search=search)
        self.assertEqual(row['website_url'], 'https://northstar.de/')
        self.assertEqual(len(queries), 3)
        self.assertEqual(row['domain_match_method'], 'web_search_verified_official_fallback')

    def test_first_queries_cover_companies_before_fallback_spends_budget(self):
        rows = [candidate(), {**candidate('Southstar GmbH'), 'source_id': 'node/2'}]
        queries = []
        def search(q):
            queries.append(q)
            return [{'url': 'https://southstar.de/'}] if 'Southstar' in q else []
        websites.discover_missing_websites(rows, Pages({'https://southstar.de/':
            '<title>Southstar GmbH</title>76133 Karlsruhe'}), search=search, max_searches=3)
        self.assertEqual(rows[1]['website_url'], 'https://southstar.de/')
        self.assertEqual(len(queries), 3)
        self.assertIn('Southstar', queries[1])

    def test_large_batch_reserves_search_budget_for_both_fallbacks(self):
        rows=[{**candidate(f'Northstar{index} Robotics GmbH'),'source_id':f'node/{index+1}'}
              for index in range(10)]
        def search(q):
            return [{'url':'https://northstar0.de/'}] if 'Northstar0' in q and 'offizielle Website' in q else []
        pages=Pages({'https://northstar0.de/':'<title>Northstar0 Robotics GmbH</title>76133 Karlsruhe'})
        websites.discover_missing_websites(rows,pages,search=search,max_searches=6)
        self.assertEqual(rows[0]['website_url'],'https://northstar0.de/')
        self.assertEqual(rows[0]['domain_match_method'],'web_search_verified_official_fallback')

    def test_compact_brand_domain_requires_same_page_contact_identity(self):
        row = candidate('Falc Immobilien', **{'addr:street': 'Pfinztalstraße', 'addr:housenumber': '59',
                                            'addr:postcode': '76227'})
        pages = Pages({'https://falcimmo.de/karlsruhe/':
            '<title>FALC Immobilien Karlsruhe</title><h1>FALC Immobilien</h1>'
            '<p>Kontakt: FALC Immobilien, Pfinztalstraße 59, 76227 Karlsruhe</p>'})
        websites.discover_missing_websites([row], pages,
            search=lambda q: [{'url': 'https://falcimmo.de/karlsruhe/'}])
        self.assertEqual(row['website_url'], 'https://falcimmo.de/')
        self.assertEqual(row['domain_evidence_url'], 'https://falcimmo.de/karlsruhe/')

    def test_three_letter_acronym_brand_requires_its_own_domain_token(self):
        for domain,accepted in [('bkv-logistik.de',True),('logistik.de',False)]:
            with self.subTest(domain=domain):
                row=candidate('BKV-Logistik GmbH & Co. KG', **{'addr:city':'Rheinstetten',
                                                            'addr:postcode':'76287'})
                html='<title>BKV Logistik GmbH &amp; Co. KG</title>BKV-Logistik GmbH &amp; Co. KG, 76287 Rheinstetten'
                websites.discover_missing_websites([row],Pages({f'https://{domain}/':html}),
                    search=lambda q:[{'url':f'https://{domain}/'}])
                self.assertEqual(row['website_url'],f'https://{domain}/' if accepted else None)

    def test_ranked_imprint_supplies_owner_evidence_and_generic_page_title(self):
        row = candidate(**{'addr:street': 'Marktstraße', 'addr:housenumber': '7'})
        pages = Pages({'https://northstar.de/':
            '<title>Northstar Robotics</title><a href="/about">About</a>'
            '<a href="/impressum">Impressum</a>',
            'https://northstar.de/about': '<h1>About</h1>Innovative technology',
            'https://northstar.de/impressum': '<title>Impressum</title><main>'
            '<h1>Impressum</h1>Angaben gemäß § 5 DDG: Northstar Robotics GmbH '
            'Marktstraße 7, 76133 Karlsruhe</main>'})
        websites.discover_missing_websites([row], pages, search=lambda q: [{'url': 'https://northstar.de/'}])
        self.assertEqual(row['website_url'], 'https://northstar.de/')
        self.assertEqual(row['domain_evidence_url'], 'https://northstar.de/impressum')

    def test_current_imprint_overrides_historical_identity_and_footer(self):
        row = candidate('Vi2vi GmbH', **{'addr:city': 'Malsch', 'addr:postcode': '76316',
                                        'addr:street': 'Daimlerstraße'})
        pages = Pages({'https://vi2vi.com/fakten': '<title>Vi2vi Gruppe</title>'
            '<p>Vi2vi GmbH was founded in Malsch 76316.</p><a href="/impressum">Impressum</a>',
            'https://vi2vi.com/': '<title>Vi2vi Gruppe</title>',
            'https://vi2vi.com/impressum': '<title>Impressum</title><main>'
            'vi2vi Retail Solution GmbH Dieselstraße 11 76316 Malsch</main>'
            '<footer>Vi2vi GmbH, Wagnerstraße 31, Durmersheim</footer>'})
        websites.discover_missing_websites([row], pages,
            search=lambda q: [{'url': 'https://vi2vi.com/fakten'}])
        self.assertIsNone(row['website_url'])

    def test_historical_footer_without_imprint_does_not_verify_identity(self):
        for email in (None, 'info@vi2vi.com'):
            with self.subTest(email=email):
                row=candidate('Vi2vi GmbH', **{'addr:city':'Malsch','addr:postcode':'76316',
                                              'email':email})
                pages=Pages({'https://vi2vi.com/': '<title>Vi2vi Gruppe</title>'
                    '<h1>Vi2vi Group</h1><p>Welcome</p>'
                    '<footer>Vi2vi GmbH, Daimlerstraße 27, 76316 Malsch</footer>'})
                websites.discover_missing_websites([row],pages,
                                                  search=lambda q:[{'url':'https://vi2vi.com/'}])
                self.assertIsNone(row['website_url'])

    def test_old_entity_after_imprint_disclaimer_is_not_the_current_owner(self):
        row = candidate('Vi2vi GmbH', **{'addr:city':'Malsch','addr:postcode':'76316'})
        pages = Pages({'https://vi2vi.com/': '<title>Vi2vi Gruppe</title>'
            '<p>Vi2vi GmbH founded in 76316 Malsch</p><a href="/impressum">Impressum</a>',
            'https://vi2vi.com/impressum': '<title>Impressum</title>'
            '<main>Vi2vi Retail Solution GmbH, 76316 Malsch</main>'
            '<h2>Haftungsausschluss</h2><p>Legal disclaimers</p>'
            '<div class="site-info">Vi2vi GmbH, Durmersheim</div>'})
        websites.discover_missing_websites([row],pages,search=lambda q:[{'url':'https://vi2vi.com/'}])
        self.assertIsNone(row['website_url'])

    def test_imprint_branding_title_cannot_override_declared_operator(self):
        row=candidate('Vi2vi GmbH', **{'addr:city':'Malsch','addr:postcode':'76316'})
        pages=Pages({'https://vi2vi.com/impressum': '<title>Vi2vi GmbH Impressum</title>'
            '<h1>Impressum</h1><main>Vi2vi Retail Solution GmbH, 76316 Malsch</main>'})
        websites.discover_missing_websites([row],pages,
            search=lambda q:[{'url':'https://vi2vi.com/impressum'}])
        self.assertIsNone(row['website_url'])

    def test_imprint_branding_heading_cannot_override_declared_operator(self):
        for tag in ('h1','h2','h3','h4','h5','h6'):
            with self.subTest(tag=tag):
                row=candidate('Vi2vi GmbH', **{'addr:city':'Malsch','addr:postcode':'76316'})
                pages=Pages({'https://vi2vi.com/impressum': '<title>Vi2vi Gruppe</title>'
                    f'<{tag}>Vi2vi GmbH Impressum</{tag}>'
                    '<main>Vi2vi Retail Solution GmbH, Dieselstraße 11, 76316 Malsch</main>'})
                websites.discover_missing_websites([row],pages,
                    search=lambda q:[{'url':'https://vi2vi.com/impressum'}])
                self.assertIsNone(row['website_url'])

    def test_legal_owner_name_can_have_a_trailing_partner_label_in_source(self):
        row = candidate('STUMPF OHG Allianz', **{'addr:street':'Grünhutstraße','addr:postcode':'76187'})
        pages = Pages({'https://stumpf-ohg.de/': '<title>Stumpf OHG</title>'
            '<p>STUMPF OHG Allianz Grünhutstraße 6 76187 Karlsruhe</p>'
            '<a href="/impressum">Impressum</a>',
            'https://stumpf-ohg.de/impressum': '<title>Impressum</title><main>'
            '<h1>Impressum</h1>Angaben gemäß § 5 TMG: STUMPF OHG, '
            'Grünhutstraße 6, 76187 Karlsruhe</main>'})
        websites.discover_missing_websites([row],pages,search=lambda q:[{'url':'https://stumpf-ohg.de/'}])
        self.assertEqual(row['website_url'],'https://stumpf-ohg.de/')
        self.assertEqual(row['domain_evidence_url'],'https://stumpf-ohg.de/impressum')

    def test_generic_industry_domain_cannot_establish_ownership(self):
        row = candidate('Stober Metallbau', **{'addr:city': 'Linkenheim-Hochstetten',
                                              'addr:postcode': '76351'})
        pages = Pages({'https://metallbauer.io/stober': '<title>Stober Metallbau</title>'
            '<p>Kontakt Stober Metallbau, 76351 Linkenheim-Hochstetten</p>'})
        websites.discover_missing_websites([row], pages,
            search=lambda q: [{'url': 'https://metallbauer.io/stober'}])
        self.assertIsNone(row['website_url'])

    def test_missing_locality_uses_region_hint_but_verifies_exact_source_phone(self):
        row = candidate('Gastronomie-Service Thomas Glaser', **{
            'addr:city': None, 'addr:postcode': None, 'contact:phone': '+49 721 9200815'})
        queries = []
        def search(q):
            queries.append(q)
            return [{'url': 'https://gastronomie-service-glaser.de/kontakt/'}]
        pages = Pages({'https://gastronomie-service-glaser.de/kontakt/':
            '<title>Gastronomie Service Glaser</title><h1>Kontakt</h1>'
            '<p>Gastronomie Service Thomas Glaser, Karlsruhe. Telefon: 0721/9200815</p>'})
        websites.discover_missing_websites([row], pages, search=search, search_area_hint='Karlsruhe Region')
        self.assertEqual(row['website_url'], 'https://gastronomie-service-glaser.de/')
        self.assertIn('Karlsruhe Region', queries[0])
        self.assertIsNone(row['tags']['addr:city'])

    def test_region_hint_is_not_locality_evidence(self):
        row = candidate(**{'addr:city': None, 'addr:postcode': None})
        websites.discover_missing_websites([row], Pages({'https://northstar.de/':
            '<title>Northstar Robotics GmbH</title><p>76133 Karlsruhe</p>'}),
            search=lambda q: [{'url': 'https://northstar.de/'}], search_area_hint='Karlsruhe Region')
        self.assertIsNone(row['website_url'])

    def test_phone_digits_cannot_be_assembled_from_unrelated_numbers(self):
        row = candidate(**{'addr:city': None, 'addr:postcode': None, 'phone': '+49 721 9200815'})
        websites.discover_missing_websites([row], Pages({'https://northstar.de/kontakt/':
            '<title>Northstar Robotics GmbH</title><h1>Kontakt</h1>'
            '<p>Product 0721</p><p>Founded 9200</p><p>Employees 815</p>'}),
            search=lambda q: [{'url': 'https://northstar.de/kontakt/'}], search_area_hint='Karlsruhe')
        self.assertIsNone(row['website_url'])

    def test_linked_unavailable_imprint_cannot_confirm_legal_owner(self):
        row = candidate()
        pages = Pages({'https://northstar.de/': '<title>Northstar Robotics GmbH</title>'
            '<p>76133 Karlsruhe</p><a href="/impressum">Impressum</a>'})
        websites.discover_missing_websites([row], pages, search=lambda q: [{'url': 'https://northstar.de/'}])
        self.assertIsNone(row['website_url'])

    def test_exhausted_crawl_budget_does_not_buy_search_leads(self):
        from tempfile import TemporaryDirectory
        from hiring_scraper.http import Client
        queries = []
        with TemporaryDirectory() as capture:
            websites.discover_missing_websites([candidate()], Client(capture, max_requests=0),
                                              search=lambda q: queries.append(q) or [])
        self.assertEqual(queries, [])

    def test_search_api_parses_results_and_never_follows_credential_redirects(self):
        import json
        from urllib.error import HTTPError
        from urllib.parse import parse_qs, urlsplit
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, limit):
                return json.dumps({'web': {'results': [{'url': 'https://northstar.de/',
                                                        'title': 'Northstar'}, {'title': 'missing URL'}]}}).encode()
        requests = []
        def opener(request, timeout):
            requests.append(request)
            return Response()
        provider = websites.BraveSearch('test-token', opener=opener)
        results = provider('Northstar Karlsruhe')
        self.assertEqual(results, [{'url': 'https://northstar.de/', 'title': 'Northstar'}])
        self.assertEqual(parse_qs(urlsplit(requests[0].full_url).query)['country'], ['DE'])
        self.assertEqual(requests[0].get_header('X-subscription-token'), 'test-token')
        # The default transport must refuse redirects, so auth cannot leak to another host.
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread
        from urllib.request import Request
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/target')
                else:
                    self.send_response(200)
                self.end_headers()
            def log_message(self, *_): pass
        server = HTTPServer(('127.0.0.1', 0), RedirectHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for provider_class in (websites.BraveSearch, websites.TavilySearch):
                default = provider_class('test-token')
                with self.assertRaises(HTTPError) as error:
                    default.opener(Request(f'http://127.0.0.1:{server.server_port}/redirect',
                                           headers={'Authorization': 'Bearer test-token'}), timeout=2)
                self.assertEqual(error.exception.code, 302)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        def failed(request, timeout):
            raise HTTPError(request.full_url, 429, 'rate limit', {}, None)
        limited = websites.BraveSearch('test-token', opener=failed)
        self.assertEqual(limited('first'), [])
        self.assertTrue(limited.disabled)
        self.assertEqual(limited('second'), [])

    def test_unrelated_redirect_cannot_inherit_email_trust(self):
        row = candidate(email='info@northstar-robotics.de')
        class Redirect:
            def get(self, url):
                return {'state': 'ok', 'content_type': 'text/html',
                        'final_url': 'https://directory.de/'}, b'<title>Directory</title>Northstar Robotics'
        websites.discover_missing_websites([row], Redirect())
        self.assertIsNone(row['website_url'])


if __name__ == '__main__':
    unittest.main()
