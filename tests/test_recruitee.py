import tempfile
import unittest
from xml.sax.saxutils import escape

from hiring_scraper.ats import identify, parse_feed
from hiring_scraper.http import Client


BOARD = 'https://example.recruitee.com'


def offer(**changes):
    fields = {
        'id': '2732449', 'slug': 'system-engineer', 'title': 'System Engineer',
        'description': '<h3>Your mission</h3><ul><li>Maintain servers</li></ul><p>Nice to have:</p><ul><li>Cloud experience</li></ul>',
        'requirements': '<p>Experience or equivalent training</p>',
        'location': 'Homeoffice', 'country': 'Deutschland', 'city': 'Karlsruhe',
        'country_code': 'DE', 'remote': 'true', 'hybrid': 'true', 'on_site': 'false',
        'department': 'Development', 'employment_type_code': 'fulltime_permanent',
        'company_name': 'Example GmbH', 'careers_url': BOARD + '/o/system-engineer',
        'apply_url': BOARD + '/o/system-engineer/c/new',
        'published_at': '2026-09-30 09:45:56 UTC', 'close_at': '',
    }
    fields.update(changes)
    return '<offer>' + ''.join('<' + key + '>' + escape(value) + '</' + key + '>' for key, value in fields.items() if value is not None) + '</offer>'


def feed(*rows):
    return '<offers>' + ''.join(rows) + '</offers>'


class RecruiteeTests(unittest.TestCase):
    def test_malformed_and_disallowed_ports_do_not_establish_a_tenant(self):
        for port in ('bad', '65536', '8443', '80'):
            with self.subTest(port=port):
                self.assertIsNone(identify('https://example.recruitee.com:' + port + '/'))

    def test_default_and_explicit_https_ports_preserve_the_public_feed(self):
        for authority in ('example.recruitee.com', 'example.recruitee.com:443'):
            with self.subTest(authority=authority):
                self.assertEqual(identify('https://' + authority + '/'), {
                    'provider': 'recruitee', 'tenant': 'example', 'board_url': BOARD,
                    'feed_url': BOARD + '/api/feeds/offers.xml',
                })

    def test_public_board_and_localized_job_routes_construct_documented_feed(self):
        for path in ('/', '/l/de/', '/o/system-engineer', '/l/en/o/system-engineer?lang=de', '/api/feeds/offers.xml'):
            with self.subTest(path=path):
                result = identify(BOARD + path)
                self.assertEqual(result, {'provider': 'recruitee', 'tenant': 'example', 'board_url': BOARD,
                                          'feed_url': BOARD + '/api/feeds/offers.xml'})

    def test_assets_private_application_policy_and_utility_routes_do_not_establish_tenants(self):
        for path in ('/assets/logo', '/login', '/sign-in', '/privacy', '/imprint', '/robots.txt',
                     '/o/system-engineer/c/new', '/api/offers', '/api/feeds/offers.xml?limit=1', '/l/en/privacy'):
            with self.subTest(path=path):
                self.assertIsNone(identify(BOARD + path))
        for host in ('api.recruitee.com', 'app.recruitee.com', 'example.recruitee.com.evil.test', 'x.y.recruitee.com'):
            self.assertIsNone(identify('https://' + host + '/'))

    def test_xml_normalizes_fields_and_preserves_description_section_and_bullet_scope(self):
        result = parse_feed('recruitee', feed(offer()), BOARD)
        self.assertTrue(result['complete'])
        job = result['jobs'][0]
        self.assertEqual((job['id'], job['title'], job['url']), ('2732449', 'System Engineer', BOARD + '/o/system-engineer'))
        self.assertEqual(job['locations'], [{'label': 'Karlsruhe', 'country_code': 'DE'}])
        self.assertEqual(job['department'], 'Development')
        self.assertEqual(job['employment_type'], 'fulltime_permanent')
        self.assertEqual(job['date_posted'], '2026-09-30')
        self.assertEqual(job['work_arrangement'], 'hybrid')
        self.assertNotIn('remote_country_codes', job.get('raw_metadata', {}))
        self.assertIn('Your mission\n- Maintain servers\nNice to have:\n- Cloud experience', job['description'])
        self.assertIn('Experience or equivalent training', job['description'])
        self.assertNotIn('skills', job)

    def test_custom_domain_links_with_aligned_slugs_normalize_to_same_tenant(self):
        job = parse_feed('recruitee', feed(offer(careers_url='https://careers.example.org/o/system-engineer',
                 apply_url='https://careers.example.org/o/system-engineer/c/new')), BOARD)['jobs'][0]
        self.assertEqual(job['url'], BOARD + '/o/system-engineer')
        self.assertEqual(job['raw_metadata']['careers_url'], 'https://careers.example.org/o/system-engineer')

    def test_no_remote_or_office_inference_from_description_or_uncertain_location(self):
        job = parse_feed('recruitee', feed(offer(remote=None, hybrid=None, on_site=None, city=None,
                     country_code=None, location='Homeoffice', description='Remote worldwide available')), BOARD)['jobs'][0]
        self.assertNotIn('work_arrangement', job)
        self.assertNotIn('is_remote', job)
        self.assertNotIn('office', job.get('raw_metadata', {}))
        self.assertNotIn('remote_country_codes', job.get('raw_metadata', {}))

    def test_initiative_applications_and_talent_pools_are_not_active_jobs(self):
        for title in ('Initiativbewerbungen – keine Zuordnung zu aktueller Ausschreibung', 'Initiativbewerbung', 'Talent pool', 'Join our talent community'):
            with self.subTest(title=title):
                self.assertEqual(parse_feed('recruitee', feed(offer(title=title)), BOARD), {'jobs': [], 'complete': True})

    def test_valid_empty_feed_is_complete(self):
        self.assertEqual(parse_feed('recruitee', '<offers/>', BOARD), {'jobs': [], 'complete': True})

    def test_unexpected_truncated_or_paginated_xml_fails_closed(self):
        for body in ('<html/>', '<offers><offer>', '<offers><next>page2</next></offers>',
                     '<offers total="2">' + offer() + '</offers>', '<offers>' + offer() + '<pagination/></offers>',
                     '<offers>' + offer() + offer() + '</offers>', '<offers>' + offer().replace('</offer>', '<next_page>2</next_page></offer>') + '</offers>'):
            with self.subTest(body=body[:70]), self.assertRaises(ValueError):
                parse_feed('recruitee', body, BOARD)

    def test_missing_required_or_unsafe_job_urls_fail_closed(self):
        changes = [{'id': None}, {'id': '  '}, {'title': ' '}, {'slug': '../other'}, {'careers_url': None},
                   {'careers_url': BOARD + '/o/other'}, {'careers_url': BOARD + '/o/system-engineer/c/new'},
                   {'careers_url': 'https://other.recruitee.com/o/system-engineer'},
                   {'careers_url': 'https://user@careers.example.org/o/system-engineer'},
                   {'careers_url': 'https:///o/system-engineer'},
                   {'careers_url': 'https://careers.example.org:8443/o/system-engineer'},
                   {'careers_url': 'https://careers.example.org/o/system-engineer#frag'},
                   {'apply_url': 'https://other.example.org/o/system-engineer/c/new'},
                   {'apply_url': BOARD + '/o/other/c/new'}, {'remote': 'perhaps'}, {'close_at': '2020-01-01'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_feed('recruitee', feed(offer(**change)), BOARD)
        with self.assertRaises(ValueError):
            parse_feed('recruitee', feed(offer()), 'https://other.example.org')

    def test_nested_pagination_or_ambiguous_locations_cannot_claim_completeness(self):
        for extra in ('<locations><next_page>2</next_page></locations>',
                      '<locations total="2"><location><city>Karlsruhe</city></location></locations>',
                      '<locations><location><city>Karlsruhe</city><city>Berlin</city></location></locations>',
                      '<tags><next_page>2</next_page></tags>', '<salary><next_page>2</next_page></salary>'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_feed('recruitee', feed(offer().replace('</offer>', extra + '</offer>')), BOARD)

    def test_duplicate_job_url_and_nonpublic_custom_host_fail_closed(self):
        with self.subTest(duplicate=True), self.assertRaises(ValueError):
            parse_feed('recruitee', feed(offer(), offer(id='2732450')), BOARD)
        for host in ('127.0.0.1', 'localhost', '.example.org', 'careers..example.org'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                parse_feed('recruitee', feed(offer(careers_url='https://' + host + '/o/system-engineer',
                     apply_url='https://' + host + '/o/system-engineer/c/new')), BOARD)

    def test_smartrecruiters_utility_urls_use_explicit_employer_or_are_rejected(self):
        for name in ('cdn-cgi', 'external-referrals', 'oneclick-ui'):
            for host in ('jobs', 'careers'):
                self.assertIsNone(identify('https://' + host + '.smartrecruiters.com/' + name))
        self.assertIsNone(identify('https://jobs.smartrecruiters.com/cdn-cgi/l/email-protection'))
        for route in ('oneclick-ui', 'external-referrals'):
            result = identify('https://jobs.smartrecruiters.com/' + route + '/company/Tipico/publication/42cd661f-db60-4d99-817e-0495b47ea69d')
            self.assertEqual(result['tenant'], 'Tipico')
            self.assertEqual(result['board_url'], 'https://careers.smartrecruiters.com/Tipico')
        self.assertEqual(identify('https://jobs.smartrecruiters.com/Tipico/744000153411220-backend-developer')['tenant'], 'Tipico')

    def test_shared_softgarden_infrastructure_cannot_establish_employer_board(self):
        for url in ('https://jhfiles.s3.softgarden.de/publicFiles/4/8/file',
                    'https://jobdb.softgarden.de/jobdb/public/jobposting/applyonline/click?jp=42'):
            self.assertIsNone(identify(url))
        for url in ('https://andrena.softgarden.io/job/42/Engineer', 'https://actemium.career.softgarden.de/'):
            self.assertEqual(identify(url)['provider'], 'softgarden')


class RecruiteeHttpTests(unittest.TestCase):
    def test_only_exact_documented_https_tenant_feed_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client(folder, offline_only=True)
            meta, body = client.get_feed(BOARD + '/api/feeds/offers.xml')
            self.assertEqual(meta['state'], 'cache_miss')
            self.assertEqual(body, b'')
            for url in (BOARD + '/api/feeds/offers.xml?limit=1', BOARD + '/api/feeds/offers.xml#frag',
                        BOARD + '/api/offers', BOARD + '/api/candidates', BOARD + '/o/system-engineer/c/new',
                        'https://api.recruitee.com/api/feeds/offers.xml', 'https://x.y.recruitee.com/api/feeds/offers.xml',
                        'http://example.recruitee.com/api/feeds/offers.xml', 'https://example.recruitee.com.evil.test/api/feeds/offers.xml'):
                with self.subTest(url=url):
                    self.assertEqual(client.get_feed(url)[0]['state'], 'unsupported_api')
