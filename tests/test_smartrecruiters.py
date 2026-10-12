import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hiring_scraper.ats import fetch_feed, identify, parse_feed
from hiring_scraper.app import worker
from hiring_scraper.http import Client


TENANT = 'RheindataGmbH'
BOARD = f'https://careers.smartrecruiters.com/{TENANT}'
FEED = f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings?limit=100&offset=0&destination=PUBLIC'


def posting(job_id='744000113808485', **changes):
    row = {
        'id': job_id,
        'name': 'Data Engineer mit Schwerpunkt Microsoft Fabric',
        'refNumber': 'REF50C',
        'company': {'identifier': TENANT, 'name': 'rheindata GmbH'},
        'releasedDate': '2026-03-10T20:36:04.429Z',
        'location': {
            'city': 'Cologne', 'region': 'NRW', 'country': 'de', 'remote': False,
            'hybrid': True, 'fullLocation': 'Cologne, NRW, Germany',
        },
        'department': {'id': 'engineering', 'label': 'Engineering'},
        'typeOfEmployment': {'id': 'permanent', 'label': 'Full-time'},
        'experienceLevel': {'id': 'associate', 'label': 'Associate'},
        'visibility': 'PUBLIC',
        'ref': f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings/{job_id}',
    }
    row.update(changes)
    return row


def page(rows, *, offset=0, total=None, limit=100):
    return json.dumps({'offset': offset, 'limit': limit,
                       'totalFound': len(rows) if total is None else total,
                       'content': rows}).encode()


class SmartRecruitersTests(unittest.TestCase):
    def test_company_and_job_pages_resolve_to_the_public_postings_feed(self):
        for url in (BOARD, BOARD + '/744000113808485-data-engineer',
                    'https://jobs.smartrecruiters.com/RheindataGmbH/744000113808485'):
            with self.subTest(url=url):
                self.assertEqual(identify(url), {
                    'provider': 'smartrecruiters', 'tenant': TENANT,
                    'board_url': BOARD, 'feed_url': FEED,
                })

    def test_public_posting_fields_normalize_to_a_hosted_german_vacancy(self):
        result = parse_feed('smartrecruiters', page([posting()]), BOARD)
        self.assertTrue(result['complete'])
        job = result['jobs'][0]
        self.assertEqual((job['id'], job['title'], job['url']), (
            '744000113808485', 'Data Engineer mit Schwerpunkt Microsoft Fabric',
            'https://jobs.smartrecruiters.com/RheindataGmbH/744000113808485'))
        self.assertEqual((job['location'], job['locations'], job['country_code']), (
            'Cologne, NRW, Germany', [{'label': 'Cologne, NRW, Germany', 'country_code': 'DE'}], 'DE'))
        self.assertEqual((job['department'], job['employment_type'], job['seniority'], job['date_posted'],
                          job['work_arrangement']),
                         ('Engineering', 'Full-time', 'Associate', '2026-03-10', 'hybrid'))
        self.assertEqual(job['raw_metadata'], {
            'hiring_organization': {'name': 'rheindata GmbH'},
            'country_code': 'DE',
            'smartrecruiters_ref': f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings/744000113808485',
            'requisition_id': 'REF50C',
        })

    def test_parser_rejects_wrong_tenant_and_inconsistent_public_listing(self):
        for body, board in (
            (page([posting(company={'identifier': 'OtherCo', 'name': 'Other'})]), BOARD),
            (page([posting(visibility='INTERNAL')]), BOARD),
            (page([posting()], offset=1, total=1), BOARD),
            (page([posting(), posting('744000113808486')], total=1), BOARD),
        ):
            with self.subTest(body=body, board=board), self.assertRaises(ValueError):
                parse_feed('smartrecruiters', body, board)
        partial = parse_feed('smartrecruiters', page([posting()], total=2), BOARD)
        self.assertFalse(partial['complete'])

    def test_nullable_location_fields_keep_the_german_country_code_and_readable_label(self):
        row = posting(location={'city': 'Berlin', 'region': None, 'country': 'de',
                                'fullLocation': None, 'remote': False, 'hybrid': False})
        job = parse_feed('smartrecruiters', page([row]), BOARD)['jobs'][0]
        self.assertEqual(job['location'], 'Berlin, Germany')
        self.assertEqual(job['locations'], [{'label': 'Berlin, Germany', 'country_code': 'DE'}])

    def test_reader_follows_public_offsets_and_marks_the_complete_result(self):
        rows = [posting(str(index)) for index in range(100)]
        final = [posting(str(index)) for index in range(100, 102)]
        next_url = FEED.replace('offset=0', 'offset=100')

        class FeedClient:
            def __init__(self):
                self.requests = []

            def get_feed(self, url, headers=None):
                self.requests.append((url, headers))
                if url == FEED:
                    return {'state': 'ok', 'status': 200, 'etag': 'first'}, page(rows, total=102)
                return {'state': 'ok', 'status': 200}, page(final, offset=100, total=102)

        client = FeedClient()
        metadata, raw_body = fetch_feed(client, 'smartrecruiters', FEED, BOARD)
        self.assertEqual(raw_body, page(rows, total=102))
        self.assertEqual(client.requests, [(FEED, None), (next_url, None)])
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 102)
        self.assertTrue(metadata['parsed_feed']['complete'])
        self.assertTrue(metadata['pagination']['complete'])
        self.assertEqual(metadata['etag'], 'first')

    def test_worker_does_not_reuse_first_page_validator_for_later_offsets(self):
        rows = [posting(str(index)) for index in range(100)]
        final = [posting(str(index)) for index in range(100, 102)]
        next_url = FEED.replace('offset=0', 'offset=100')

        class RefreshClient:
            instance = None

            def __init__(self, *args, **kwargs):
                self.requests = []
                RefreshClient.instance = self

            def get_feed(self, url, headers=None):
                self.requests.append((url, headers))
                if headers:
                    return {'state': 'not_modified', 'status': 304}, b''
                if url == FEED:
                    return {'state': 'ok', 'status': 200, 'etag': 'fresh'}, page(rows, total=102)
                return {'state': 'ok', 'status': 200}, page(final, offset=100, total=102)

        feed = SimpleNamespace(
            provider='smartrecruiters', feed_url=FEED, board_url=BOARD,
            etag='"offset-zero"', last_modified='Tue, 06 Oct 2026 10:00:00 GMT',
        )
        with patch.object(worker, 'Client', RefreshClient):
            metadata, _ = worker._request(feed)

        self.assertEqual(RefreshClient.instance.requests, [(FEED, None), (next_url, None)])
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 102)
        self.assertTrue(metadata['parsed_feed']['complete'])

    def test_client_allows_only_the_public_read_only_postings_route(self):
        accepted = FEED
        rejected = (
            f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings?limit=100&offset=0&destination=INTERNAL',
            f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings?limit=100&offset=-1&destination=PUBLIC',
            f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings?limit=101&offset=0&destination=PUBLIC',
            f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings?limit=100&offset=0&destination=PUBLIC&destination=INTERNAL',
            f'https://api.smartrecruiters.com/v1/companies/{TENANT}/postings/744000113808485?limit=100&offset=0&destination=PUBLIC',
            f'https://api.smartrecruiters.com.evil.test/v1/companies/{TENANT}/postings?limit=100&offset=0&destination=PUBLIC',
        )
        with tempfile.TemporaryDirectory() as folder:
            client = Client(folder, offline_only=True)
            meta, body = client.get_feed(accepted)
            self.assertEqual(meta['state'], 'cache_miss')
            self.assertEqual(body, b'')
            for url in rejected:
                with self.subTest(url=url):
                    self.assertEqual(client.get_feed(url)[0]['state'], 'unsupported_api')


if __name__ == '__main__':
    unittest.main()
