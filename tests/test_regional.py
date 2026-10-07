import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from hiring_scraper.regional import RegionalClient, acquire, normalize_job, parse_search


ROW = {'referenznummer': '123-A-S', 'stellenangebotsTitel': 'Project coordinator',
       'firma': 'Example GmbH', 'stellenangebotsBeschreibung': 'Tasks\nCoordinate delivery\n\nRequirements\nGerman B2',
       'arbeitgeberKundennummerHash': 'company-hash',
       'stellenlokationen': [{'adresse': {'ort': 'Heidelberg', 'land': 'DEUTSCHLAND'}, 'breite': 49.4, 'laenge': 8.7}]}


class RegionalParserTests(unittest.TestCase):
    def test_observed_and_legacy_shapes_skip_invalid_rows_and_keep_identity(self):
        for key in ['ergebnisliste', 'stellenangebote']:
            result = parse_search({key: [ROW, None, {'titel': 'missing reference'}], 'maxErgebnisse': 10})
            self.assertEqual(len(result['jobs']), 1)
            self.assertEqual(result['invalid_count'], 2)
            self.assertEqual(result['total'], 10)
            self.assertEqual(result['jobs'][0]['external_id'], '123-A-S')
        legacy = normalize_job({'refnr': 'legacy-1', 'titel': 'Legacy', 'arbeitgeber': 'Legacy AG',
                                'arbeitsort': {'ort': 'Mannheim', 'land': 'Deutschland', 'koordinaten': {'lat': '49.49', 'lon': '8.47'}}})
        self.assertEqual(legacy['locations'][0]['latitude'], 49.49)
        self.assertEqual(legacy['locations'][0]['country_code'], 'DE')

    def test_full_text_and_source_metadata_are_preserved(self):
        result = normalize_job({**ROW, '_checked_at': '2026-10-07T06:00:00Z', 'istPrivateArbeitsvermittlung': True,
                                'festgehalt': 55000, 'verguetungsangabe': 'JAHRESGEHALT',
                                'befristetBis': '2028-10-01', 'veroeffentlichungszeitraum': {'bis': '2026-11-01'}})
        self.assertEqual(result['description'], ROW['stellenangebotsBeschreibung'])
        self.assertEqual(result['company_source_id'], 'hash:company-hash')
        self.assertEqual(result['raw_metadata']['employer_type'], 'agency')
        self.assertEqual(result['raw_metadata']['publication_expires_at'], '2026-11-01')
        self.assertEqual(result['raw_metadata']['contract_end'], '2028-10-01')
        self.assertEqual(result['raw_metadata']['source_observed_at'], '2026-10-07T06:00:00Z')
        self.assertIn('55000', result['salary'])
        self.assertNotIn('EUR', result['salary'])

    def test_remote_percentage_is_explicit_and_office_country_is_not_remote_scope(self):
        hybrid = normalize_job({**ROW, 'homeofficemoeglich': True, 'homeofficetyp': 'NACH_VEREINBARUNG'})
        self.assertFalse(hybrid['is_remote'])
        self.assertEqual(hybrid['work_arrangement'], 'hybrid')
        remote = normalize_job({**ROW, 'homeofficemoeglich': True, 'homeofficetyp': 'ANGABE_IN_PROZENT', 'homeofficeprozent': 100})
        self.assertTrue(remote['is_remote'])
        self.assertEqual(remote['work_arrangement'], 'remote')
        self.assertEqual(remote['raw_metadata']['remote_country_codes'], [])
        self.assertIsNone(remote['raw_metadata']['remote_scope_source'])

    def test_malformed_coordinates_never_become_valid_locations(self):
        for latitude, longitude in [('bad', 8), (float('nan'), 8), (91, 8), (49, 181), (True, 8)]:
            result = normalize_job({**ROW, 'stellenlokationen': [{'adresse': {'ort': 'Heidelberg', 'land': 'DEUTSCHLAND'},
                                                                 'breite': latitude, 'laenge': longitude}]})
            self.assertIsNone(result['locations'][0]['latitude'])
            self.assertIsNone(result['locations'][0]['longitude'])


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []
    def _raw(self, url, headers):
        self.urls.append(url)
        return next(self.responses)


def ok(payload):
    return {'state': 'ok', 'status': 200, 'checked_at': '2026-10-07T06:00:00Z'}, json.dumps(payload).encode()


class RegionalAcquisitionTests(unittest.TestCase):
    def test_fixed_host_and_path_reject_arbitrary_urls_without_transport(self):
        transport = ScriptedTransport([])
        client = RegionalClient(transport, max_requests=5, retries=0)
        for url in ['https://example.com/pc/v6/jobs', 'https://rest.arbeitsagentur.de/secret',
                    'http://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs',
                    'https://rest.arbeitsagentur.de:444/jobboerse/jobsuche-service/pc/v6/jobs']:
            with self.assertRaises(ValueError):
                client.request_json(url)
        self.assertEqual(transport.urls, [])

    def test_pagination_detail_failure_and_budget_produce_reusable_partial_snapshot(self):
        transport = ScriptedTransport([ok({'ergebnisliste': [ROW], 'maxErgebnisse': 2}),
                                       ok({'ergebnisliste': [{**ROW, 'referenznummer': '456-B-S'}], 'maxErgebnisse': 2}),
                                       ({'state': 'http_error', 'status': 503}, b'')])
        client = RegionalClient(transport, max_requests=3, retries=1)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'fresh'
            result = acquire(out, city='Heidelberg', radius_km=35, terms=['Project'], page_size=1,
                             max_pages=2, max_details=10, client=client)
            self.assertEqual(result['state'], 'partial')
            self.assertEqual(result['requests_used'], 3)
            self.assertEqual(len(json.loads((out/'snapshot.json').read_text())['jobs']), 2)
            self.assertEqual(len(result['searches']), 2)
            self.assertTrue(result['detail_errors'])
            self.assertEqual(parse_qs(urlsplit(transport.urls[1]).query)['page'], ['2'])
            with self.assertRaises(FileExistsError):
                acquire(out, city='Heidelberg', terms=['Project'], client=client)

    def test_retries_evict_cached_failure_and_identity_mismatch_is_not_merged(self):
        transport = ScriptedTransport([({'state': 'http_error', 'status': 503}, b''),
                                       ok({'ergebnisliste': [ROW], 'maxErgebnisse': 1}),
                                       ok({**ROW, 'referenznummer': 'different', 'stellenangebotsBeschreibung': 'wrong vacancy'})])
        client = RegionalClient(transport, max_requests=4, retries=1, retry_delay=0)
        with tempfile.TemporaryDirectory() as directory:
            result = acquire(Path(directory)/'run', city='Heidelberg', terms=['Project'], client=client)
            self.assertEqual(result['requests_used'], 3)
            self.assertEqual(result['detail_errors'][0]['state'], 'identity_mismatch')
            snapshot = json.loads((Path(directory)/'run'/'snapshot.json').read_text())
            self.assertEqual(snapshot['jobs'][0]['description'], ROW['stellenangebotsBeschreibung'])


if __name__ == '__main__':
    unittest.main()

class RegionalCountryAndCaptureTests(unittest.TestCase):
    def test_observed_austrian_country_code_is_preserved(self):
        result = normalize_job({**ROW, 'stellenlokationen': [{'adresse': {'ort': 'Vienna', 'land': 'OESTERREICH'}}]})
        self.assertEqual(result['locations'][0]['country_code'], 'AT')

    def test_real_transport_retries_keep_two_immutable_captures(self):
        from email.message import Message
        from hiring_scraper.http import Client
        class Response:
            def __init__(self, code, body):
                self.code, self.body = code, body
                self.headers = Message()
                self.headers['Content-Type'] = 'application/json'
            def read(self, limit):
                return self.body[:limit]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory:
            transport = Client(Path(directory)/'http', delay=0, max_requests=3)
            client = RegionalClient(transport, max_requests=3, retries=1, retry_delay=0)
            with patch('hiring_scraper.http.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('1.1.1.1', 443))]), \
                 patch.object(transport.opener, 'open', side_effect=[Response(503, b'busy'), Response(200, b'{"ergebnisliste": []}')]):
                record, data = client.request_json('https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?was=Project')
            self.assertEqual(record['state'], 'ok')
            self.assertEqual(data['ergebnisliste'], [])
            captures = sorted((Path(directory)/'http').glob('attempt-*/*.json'))
            self.assertEqual(len(captures), 2)
            self.assertEqual(json.loads(captures[0].read_text())['status'], 503)
            self.assertEqual(json.loads(captures[1].read_text())['status'], 200)
            self.assertEqual(captures[0].with_suffix('.body').read_bytes(), b'busy')

class RegionalBoundsTests(unittest.TestCase):
    def test_detail_path_rejects_extra_segments(self):
        client = RegionalClient(ScriptedTransport([]), max_requests=1)
        with self.assertRaises(ValueError):
            client.request_json('https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/a/../../other')

    def test_search_page_cap_marks_partial_even_without_http_errors(self):
        client = RegionalClient(ScriptedTransport([ok({'ergebnisliste': [ROW], 'maxErgebnisse': 999})]), max_requests=2, retries=0)
        with tempfile.TemporaryDirectory() as directory:
            result = acquire(Path(directory)/'run', city='Heidelberg', terms=['Project'], max_pages=1,
                             page_size=1, max_details=0, client=client)
        self.assertEqual(result['state'], 'partial')
        self.assertTrue(result['searches'][0]['truncated'])
        self.assertEqual(result['unique_summaries'], 1)

    def test_unknown_search_shape_is_partial_and_error_is_saved(self):
        client = RegionalClient(ScriptedTransport([ok({'unexpected': []})]), max_requests=2, retries=0)
        with tempfile.TemporaryDirectory() as directory:
            result = acquire(Path(directory)/'run', city='Heidelberg', terms=['Project'], client=client)
            snapshot = json.loads((Path(directory)/'run'/'snapshot.json').read_text())
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['searches'][0]['state'], 'invalid_response')
        self.assertEqual(snapshot['jobs'], [])
