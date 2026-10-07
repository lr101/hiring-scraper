import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hiring_scraper import ats
from hiring_scraper.app import worker
from hiring_scraper.app.models import Base, Company, Job, JobFeed, ScanRun, utcnow
from hiring_scraper.discovery import discover


FEED = 'https://api.lever.co/v0/postings/acme?mode=json&limit=100'
BOARD = 'https://jobs.lever.co/acme'


def page(start, count):
    return json.dumps([{'id': str(i), 'text': 'Engineer',
                        'hostedUrl': f'{BOARD}/{i}', 'categories': {'location': 'Berlin'}}
                       for i in range(start, start + count)]).encode()


class FeedClient:
    """Replace only external HTTP; parsing, pagination and persistence remain real."""
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def get_feed(self, url, headers=None):
        self.requests.append((url, headers))
        response = self.responses.get(url, ({'state': 'http_error', 'status': 404}, b''))
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url):
        return ({'state': 'ok', 'status': 200, 'final_url': url},
                f'<a href="{BOARD}">Careers</a>'.encode())


def ok(body, **metadata):
    return {'state': 'ok', 'status': 200, **metadata}, body


class LeverFeedTests(unittest.TestCase):
    def read(self, client, url=FEED, **kwargs):
        self.assertTrue(callable(getattr(ats, 'fetch_feed', None)), 'Shared bounded feed reader is missing')
        return ats.fetch_feed(client, 'lever', url, BOARD, **kwargs)

    def test_short_final_page_enumerates_unique_jobs_and_preserves_first_metadata(self):
        client = FeedClient({FEED: ok(page(0, 100), etag='first', capture='first-capture'),
                             FEED + '&skip=100': ok(page(100, 3), etag='second')})
        paced = []
        metadata, body = self.read(client, conditional_headers={'If-None-Match': 'old'}, pace=paced.append)
        self.assertEqual(body, page(0, 100))
        self.assertEqual(metadata['etag'], 'first')
        self.assertEqual(metadata['capture'], 'first-capture')
        self.assertEqual([job['id'] for job in metadata['parsed_feed']['jobs']], [str(i) for i in range(103)])
        self.assertTrue(metadata['parsed_feed']['complete'])
        self.assertTrue(metadata['pagination']['complete'])
        self.assertEqual(paced, [FEED, FEED + '&skip=100'])
        self.assertEqual(client.requests, [(FEED, {'If-None-Match': 'old'}), (FEED + '&skip=100', None)])

    def test_exact_default_bound_keeps_all_1000_jobs_incomplete_without_eleventh_request(self):
        urls = [FEED] + [FEED + f'&skip={i}' for i in range(100, 1000, 100)]
        client = FeedClient({url: ok(page(i * 100, 100)) for i, url in enumerate(urls)})
        metadata, _ = self.read(client)
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 1000)
        self.assertFalse(metadata['parsed_feed']['complete'])
        self.assertIn('cap', metadata['pagination']['error'])
        self.assertEqual(len(client.requests), 10)

    def test_continuation_failure_retains_first_page_and_records_incompleteness(self):
        failures = [({'state': 'http_error', 'status': 503}, b''),
                    ({'state': 'request_budget_exhausted'}, b''),
                    ({'state': 'not_modified', 'status': 304}, b''),
                    ok(b'{}'), ok(b'not json'), ok(b'[null]'),
                    ({'state': 'body_too_large', 'status': 200}, page(100, 1)),
                    ok(page(100, 1).replace(b'"id": "100"', b'"id": null')),
                    OSError('connection interrupted')]
        for response in failures:
            with self.subTest(response=response):
                client = FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': response})
                metadata, _ = self.read(client)
                self.assertEqual(len(metadata['parsed_feed']['jobs']), 100)
                self.assertFalse(metadata['parsed_feed']['complete'])
                self.assertTrue(metadata['pagination']['error'])
                self.assertEqual(len(client.requests), 2)

    def test_malformed_consumer_fields_exclude_entire_continuation_page(self):
        mutations = [
            ('location', {'unexpected': 'Berlin'}), ('location', ['Berlin']),
            ('location', 7), ('location', 0), ('location', False), ('location', []),
            ('text', {'unexpected': 'Engineer'}), ('text', ['Engineer']), ('text', 7), ('text', '   '),
            ('hostedUrl', {'unexpected': BOARD}), ('hostedUrl', [BOARD]), ('hostedUrl', 7),
            ('hostedUrl', 'https://'), ('hostedUrl', 'https:///jobs/100'),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                rows = json.loads(page(100, 2))
                if field == 'location':
                    rows[1]['categories']['location'] = value
                else:
                    rows[1][field] = value
                client = FeedClient({FEED: ok(page(0, 100)),
                                     FEED + '&skip=100': ok(json.dumps(rows).encode())})
                metadata, _ = self.read(client)
                jobs = metadata['parsed_feed']['jobs']
                self.assertEqual(len(jobs), 100)
                self.assertEqual(len({job['id'] for job in jobs}), 100)
                self.assertFalse(metadata['parsed_feed']['complete'])
                self.assertFalse(metadata['pagination']['complete'])
                self.assertIn('schema', metadata['pagination']['error'].casefold())

    def test_absent_and_null_locations_normalize_to_empty_strings(self):
        rows = json.loads(page(100, 3))
        del rows[0]['categories']['location']
        rows[1]['categories']['location'] = None
        rows[2]['categories']['location'] = ''
        metadata, _ = self.read(FeedClient({FEED: ok(page(0, 100)),
                                          FEED + '&skip=100': ok(json.dumps(rows).encode())}))
        self.assertEqual([row['location'] for row in metadata['parsed_feed']['jobs'][-3:]], ['', '', ''])
        self.assertTrue(metadata['parsed_feed']['complete'])

    def test_duplicate_or_repeated_page_stops_incomplete_without_duplicate_rows(self):
        for final in [page(0, 100), page(99, 3), page(100, 1)[:-1] + b',' + page(100, 1)[1:]]:
            with self.subTest(final=final):
                client = FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': ok(final)})
                metadata, _ = self.read(client)
                jobs = metadata['parsed_feed']['jobs']
                self.assertEqual(len({job['id'] for job in jobs}), len(jobs))
                self.assertFalse(metadata['parsed_feed']['complete'])
                self.assertTrue(metadata['pagination']['error'])
                self.assertEqual(len(client.requests), 2)

    def test_duplicate_first_short_page_cannot_close_missing_jobs(self):
        body = page(0, 1)[:-1] + b',' + page(0, 1)[1:]
        metadata, _ = self.read(FeedClient({FEED: ok(body)}))
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 1)
        self.assertFalse(metadata['parsed_feed']['complete'])

    def test_partial_query_semantics_are_preserved_even_for_short_results(self):
        for query in ['mode=json&limit=100&skip=100', 'mode=json&limit=25',
                      'mode=json&limit=100&location=Berlin', 'mode=json&limit=100&page=2',
                      'mode=json', 'limit=100']:
            with self.subTest(query=query):
                url = FEED.split('?')[0] + '?' + query
                client = FeedClient({url: ok(page(100, 2))})
                metadata, _ = self.read(client, url)
                self.assertEqual(len(metadata['parsed_feed']['jobs']), 2)
                self.assertFalse(metadata['parsed_feed']['complete'])
                self.assertEqual(client.requests, [(url, None)])

    def test_malformed_or_ambiguous_url_is_rejected_before_any_request(self):
        urls = [FEED + '&skip=0&skip=100', FEED + '&limit=100', FEED + '&skip=',
                FEED + '&skip=-1', FEED + '&skip=abc', FEED + '&skip=1.0',
                FEED + '&skip=%ZZ', FEED + '&skip=0;skip=100', FEED + '&broken',
                FEED.replace('limit=100', 'limit=0'), FEED.replace('limit=100', 'limit=x'),
                FEED.replace('mode=json', 'mode=xml'), FEED + '#fragment',
                FEED.replace('api.lever.co', 'api.lever.co.evil.test'),
                FEED.replace('https://', 'http://'), FEED.replace('api.lever.co', 'user@api.lever.co'),
                FEED.replace('api.lever.co', 'api.lever.co:444'), FEED.replace('api.lever.co', 'api.lever.co:bad'),
                FEED.replace('/v0/postings/acme', '/v0/postings/acme/job-id'),
                FEED.replace('api.lever.co', 'api.lev\ner.co'), ' ' + FEED]
        for url in urls:
            with self.subTest(url=url):
                client = FeedClient({url: ok(page(0, 1))})
                metadata, _ = self.read(client, url)
                self.assertEqual(metadata['state'], 'unsupported_api')
                self.assertTrue(metadata.get('error'))
                self.assertEqual(client.requests, [])

    def test_explicit_zero_skip_and_eu_route_can_complete(self):
        url = FEED.replace('api.lever.co', 'api.eu.lever.co') + '&skip=0'
        metadata, _ = self.read(FeedClient({url: ok(page(0, 1))}), url)
        self.assertTrue(metadata['parsed_feed']['complete'])

    def test_empty_final_page_completes_an_exact_multiple(self):
        client = FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': ok(b'[]')})
        metadata, _ = self.read(client)
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 100)
        self.assertTrue(metadata['parsed_feed']['complete'])

    def test_short_tenth_page_can_complete_without_exceeding_cap(self):
        urls = [FEED] + [FEED + f'&skip={i}' for i in range(100, 1000, 100)]
        client = FeedClient({url: ok(page(i * 100, 3 if i == 9 else 100)) for i, url in enumerate(urls)})
        metadata, _ = self.read(client)
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 903)
        self.assertTrue(metadata['parsed_feed']['complete'])
        self.assertEqual(len(client.requests), 10)

    def test_oversized_response_is_not_accepted_as_complete(self):
        metadata, _ = self.read(FeedClient({FEED: ok(page(0, 101))}))
        self.assertEqual(len(metadata['parsed_feed']['jobs']), 101)
        self.assertFalse(metadata['parsed_feed']['complete'])
        self.assertTrue(metadata['pagination']['error'])

    def test_first_304_does_not_parse_or_request_continuations(self):
        client = FeedClient({FEED: ({'state': 'not_modified', 'status': 304, 'etag': 'first'}, b'')})
        metadata, body = self.read(client)
        self.assertEqual(metadata['state'], 'not_modified')
        self.assertEqual(body, b'')
        self.assertNotIn('parsed_feed', metadata)
        self.assertEqual(client.requests, [(FEED, None)])

    def test_other_providers_keep_single_request_and_conditional_headers(self):
        client = FeedClient({FEED: ok(b'{"jobs":[]}')})
        self.assertTrue(callable(getattr(ats, 'fetch_feed', None)), 'Shared bounded feed reader is missing')
        metadata, body = ats.fetch_feed(client, 'greenhouse', FEED, BOARD,
                                        conditional_headers={'If-None-Match': 'old'})
        self.assertEqual(metadata['state'], 'ok')
        self.assertEqual(body, b'{"jobs":[]}')
        self.assertEqual(client.requests, [(FEED, {'If-None-Match': 'old'})])

    def test_discovery_uses_complete_shared_enumeration(self):
        client = FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': ok(page(100, 2))})
        result = discover({'name': 'Acme', 'website': 'https://acme.example/'}, client, max_pages=2)
        board = result['boards'][0]
        self.assertEqual(board['job_count'], 102)
        self.assertTrue(board['complete'])
        self.assertTrue(board['pagination']['complete'])

    def test_discovery_reports_recovered_rows_and_continuation_error(self):
        client = FeedClient({FEED: ok(page(0, 100))})
        board = discover({'name': 'Acme', 'website': 'https://acme.example/'}, client, max_pages=2)['boards'][0]
        self.assertEqual(board['job_count'], 100)
        self.assertFalse(board['complete'])
        self.assertIn('pagination', board)
        self.assertTrue(board['pagination']['error'])


class LeverWorkerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.patches = [patch.object(worker, 'SessionLocal', self.factory), patch.object(worker, 'engine', self.engine),
                        patch.object(worker, '_pace_origin')]
        for item in self.patches:
            item.start()
        with self.factory.begin() as session:
            company = Company(source='test', source_id='1', name='Acme')
            feed = JobFeed(company=company, provider='lever', feed_url=FEED, board_url=BOARD,
                           status='incomplete', job_count=1, etag='old', last_error='Previous partial scan',
                           next_scan_at=utcnow() - timedelta(minutes=1))
            job = Job(feed=feed, external_id='missing', title='Existing job', url=BOARD + '/missing',
                      last_seen_at=utcnow() - timedelta(days=10), missing_complete_scans=1, is_active=True)
            session.add(job)
            session.flush()
            self.feed_id, self.job_id = feed.id, job.id

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def scan(self, client):
        with patch.object(worker, 'Client', return_value=client):
            self.assertTrue(worker.scan_once())

    def test_worker_ingests_all_pages_and_closes_only_after_complete_scan(self):
        self.scan(FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': ok(page(100, 2))}))
        with self.factory() as session:
            feed = session.get(JobFeed, self.feed_id)
            self.assertEqual(feed.job_count, 102)
            self.assertEqual(feed.status, 'parsed')
            self.assertEqual(len(session.scalars(select(Job)).all()), 103)
            self.assertFalse(session.get(Job, self.job_id).is_active)

    def test_failed_continuation_never_advances_missing_or_close_counters(self):
        self.scan(FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': ok(b'{}')}))
        with self.factory() as session:
            job = session.get(Job, self.job_id)
            self.assertTrue(job.is_active)
            self.assertEqual(job.missing_complete_scans, 1)
            self.assertIsNone(job.closed_at)
            feed = session.get(JobFeed, self.feed_id)
            self.assertEqual(feed.job_count, 100)
            self.assertEqual(feed.status, 'incomplete')
            self.assertIn('schema', feed.last_error.casefold())

    def assert_malformed_continuation_preserves_rows_and_closure_counters(self, field, value):
        rows = json.loads(page(100, 1))
        if field == 'location':
            rows[0]['categories']['location'] = value
        else:
            rows[0][field] = value
        self.scan(FeedClient({FEED: ok(page(0, 100)),
                              FEED + '&skip=100': ok(json.dumps(rows).encode())}))
        with self.factory() as session:
            feed = session.get(JobFeed, self.feed_id)
            self.assertEqual(feed.status, 'incomplete')
            self.assertEqual(feed.job_count, 100)
            self.assertIn('schema', feed.last_error.casefold())
            stored = session.scalars(select(Job)).all()
            self.assertEqual(len(stored), 101)
            recovered = {job.external_id for job in stored if job.external_id != 'missing'}
            self.assertEqual(recovered, {str(i) for i in range(100)})
            missing = session.get(Job, self.job_id)
            self.assertEqual(missing.missing_complete_scans, 1)
            self.assertTrue(missing.is_active)
            self.assertIsNone(missing.closed_at)
            run = session.scalar(select(ScanRun))
            self.assertEqual(run.status, 'incomplete')
            self.assertEqual(run.item_count, 100)
            self.assertIn('schema', run.error.casefold())

    def test_malformed_continuation_location_preserves_rows_and_closure_counters(self):
        self.assert_malformed_continuation_preserves_rows_and_closure_counters('location', {'unexpected': 'Berlin'})

    def test_malformed_continuation_title_preserves_rows_and_closure_counters(self):
        self.assert_malformed_continuation_preserves_rows_and_closure_counters('text', {'unexpected': 'Engineer'})

    def test_null_location_persists_as_missing_location(self):
        rows = json.loads(page(0, 1))
        rows[0]['categories']['location'] = None
        self.scan(FeedClient({FEED: ok(json.dumps(rows).encode())}))
        with self.factory() as session:
            feed = session.get(JobFeed, self.feed_id)
            self.assertEqual(feed.status, 'parsed')
            incoming = session.scalar(select(Job).where(Job.external_id == '0'))
            self.assertIsNone(incoming.location_text)
            self.assertEqual(incoming.locations, [])

    def test_304_keeps_previous_incomplete_data_and_diagnostic(self):
        self.scan(FeedClient({FEED: ({'state': 'not_modified', 'status': 304}, b'')}))
        with self.factory() as session:
            feed = session.get(JobFeed, self.feed_id)
            self.assertEqual(feed.status, 'incomplete')
            self.assertEqual(feed.job_count, 1)
            self.assertEqual(feed.last_error, 'Previous partial scan')
            self.assertEqual(session.get(Job, self.job_id).missing_complete_scans, 1)
            self.assertEqual(session.scalar(select(ScanRun)).status, 'not_modified')

    def test_malformed_first_page_id_is_schema_error_and_preserves_existing_jobs(self):
        body = page(0, 1).replace(b'"id": "0"', b'"id": null')
        self.scan(FeedClient({FEED: ok(body)}))
        with self.factory() as session:
            feed = session.get(JobFeed, self.feed_id)
            self.assertEqual(feed.status, 'schema_error')
            self.assertEqual(feed.job_count, 1)
            self.assertEqual(session.get(Job, self.job_id).missing_complete_scans, 1)
            self.assertTrue(session.get(Job, self.job_id).is_active)
            self.assertEqual(len(session.scalars(select(Job)).all()), 1)

    def test_worker_does_not_use_first_page_validators_to_skip_later_pages(self):
        client = FeedClient({FEED: ok(page(0, 100)), FEED + '&skip=100': ok(page(100, 1))})
        self.scan(client)
        self.assertEqual(client.requests, [(FEED, None), (FEED + '&skip=100', None)])
        self.assertEqual(worker._pace_origin.call_args_list[0].args, (FEED,))
        self.assertEqual(worker._pace_origin.call_args_list[1].args, (FEED + '&skip=100',))


if __name__ == '__main__':
    unittest.main()
