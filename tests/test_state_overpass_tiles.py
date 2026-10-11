import json
import re
import unittest
from urllib.parse import parse_qs
from unittest.mock import patch

from hiring_scraper.location_discovery import fetch_location_companies, is_retryable_overpass_error, resolve_location_candidates


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, _limit): return json.dumps(self.payload).encode()


class StateOverpassTilesTests(unittest.TestCase):
    def fetch(self, opener):
        with patch('hiring_scraper.location_discovery._wait_for_overpass_slot'), \
             patch('hiring_scraper.location_discovery.time.sleep'), \
             patch('hiring_scraper.location_discovery.fetch_wikidata_entities', return_value={}):
            return fetch_location_companies(0, 0, 15000, state='Baden-Württemberg', opener=opener)

    def test_timeout_remark_is_retryable_and_never_returns_partial_companies(self):
        try:
            resolve_location_candidates({'elements': [], 'remark': 'runtime error: Query timed out'}, 0, 0, None)
        except RuntimeError as error:
            self.assertTrue(is_retryable_overpass_error(error))
        else:
            self.fail('Incomplete map results must not be accepted')

    def test_state_tiles_cover_boundary_bounds_and_deduplicate_shared_objects(self):
        boxes = []
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements': [{'bounds': {'minlat': 48, 'minlon': 8, 'maxlat': 49, 'maxlon': 9}}]})
            match = re.search(r'nwr\(([\d.,]+)\)', query)
            self.assertIsNotNone(match, 'State query must have a bounded tile instead of querying the entire area')
            box = tuple(map(float, match[1].split(',')))
            self.assertIn('(area.region)', query)
            self.assertIn('[timeout:30]', query)
            boxes.append(box)
            return Response({'elements': [{'type': 'node', 'id': 1, 'lat': 48.5, 'lon': 8.5,
                'tags': {'name': 'Shared employer', 'office': 'company', 'website': 'https://company.example'}}]})
        rows = self.fetch(opener)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(boxes), 4)
        self.assertEqual(sum((n-s)*(e-w) for s,w,n,e in boxes), 1)
        self.assertEqual(set(boxes), {(48,8,48.5,8.5), (48,8.5,48.5,9), (48.5,8,49,8.5), (48.5,8.5,49,9)})

    def test_timed_out_tile_is_subdivided_and_its_partial_results_discarded(self):
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements': [{'bounds': {'minlat': 48, 'minlon': 8, 'maxlat': 48.5, 'maxlon': 8.5}}]})
            match = re.search(r'nwr\(([\d.,]+)\)', query)
            self.assertIsNotNone(match, 'State query needs a bounding box')
            box = tuple(map(float, match[1].split(',')))
            if box[2] - box[0] > .25:
                return Response({'remark': 'runtime error: Query timed out', 'elements': [
                    {'type': 'node', 'id': 999, 'lat': 48.1, 'lon': 8.1, 'tags': {'name': 'Partial', 'website': 'https://partial.example'}}]})
            return Response({'elements': [{'type': 'node', 'id': int(box[0]*1000 + box[1]*100),
                'lat': box[0], 'lon': box[1], 'tags': {'name': str(box), 'website': 'https://company.example'}}]})
        rows = self.fetch(opener)
        self.assertEqual(len(rows), 4)
        self.assertNotIn('node/999', {r['source_id'] for r in rows})

    def test_missing_boundary_cannot_succeed_as_an_empty_state(self):
        with self.assertRaisesRegex(ValueError, 'boundary'):
            self.fetch(lambda *_args, **_kwargs: Response({'elements': []}))

    def test_persistent_timeout_exhausts_subdivision_and_remains_retryable(self):
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements': [{'bounds': {'minlat': 48, 'minlon': 8, 'maxlat': 48.5, 'maxlon': 8.5}}]})
            return Response({'elements': [], 'remark': 'runtime error: Query timed out'})
        try:
            self.fetch(opener)
        except RuntimeError as error:
            self.assertTrue(is_retryable_overpass_error(error))
        else:
            self.fail('A missing tile must not produce a successful incomplete state scan')

    def test_smallest_timed_out_section_retries_before_abandoning_state(self):
        attempts = {}
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements': [{'bounds': {'minlat': 48, 'minlon': 8, 'maxlat': 48.5, 'maxlon': 8.5}}]})
            match = re.search(r'nwr\(([\d.,]+)\)', query)
            self.assertIsNotNone(match)
            box = tuple(map(float, match[1].split(',')))
            attempts[box] = attempts.get(box, 0) + 1
            if box[2]-box[0] > .125 or attempts[box] == 1:
                return Response({'elements': [], 'remark': 'runtime error: Query timed out'})
            return Response({'elements': []})
        try:
            rows = self.fetch(opener)
        except RuntimeError as error:
            self.fail(f'Smallest sections must retry transient timeouts: {error}')
        self.assertEqual(rows, [])
        self.assertEqual(sum(n == 2 for n in attempts.values()), 16)

    def test_cancelled_search_stops_before_another_section_or_enrichment(self):
        opened = []
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            opened.append(query)
            if 'out bb;' in query:
                return Response({'elements': [{'bounds': {'minlat': 48, 'minlon': 8, 'maxlat': 49, 'maxlon': 9}}]})
            return Response({'elements': []})
        def progress(*_args, **_kwargs):
            return False if len(opened) >= 2 else None
        with patch('hiring_scraper.location_discovery._wait_for_overpass_slot'), \
             patch('hiring_scraper.location_discovery.fetch_wikidata_entities') as enrich:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                fetch_location_companies(0,0,15000,state='Baden-Württemberg',opener=opener,progress_callback=progress)
        self.assertEqual(len(opened),2)
        enrich.assert_not_called()

    def test_rate_limit_without_retry_after_waits_at_least_thirty_seconds(self):
        from urllib.error import HTTPError
        attempts = []
        def opener(request, **_):
            attempts.append(request)
            if len(attempts) == 1:
                raise HTTPError(request.full_url,429,'Too Many Requests',{},None)
            return Response({'elements': []})
        with patch('hiring_scraper.location_discovery._wait_for_overpass_slot'), \
             patch('hiring_scraper.location_discovery.time.sleep') as sleep:
            self.assertEqual(fetch_location_companies(49,8.4,15000,opener=opener),[])
        self.assertGreaterEqual(sleep.call_args.args[0],30)

    def test_transient_gateway_error_retries_same_section_without_subdivision(self):
        from urllib.error import HTTPError
        sections = []
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements':[{'bounds':{'minlat':48,'minlon':8,'maxlat':48.5,'maxlon':8.5}}]})
            sections.append(query)
            if len(sections) == 1:
                raise HTTPError(request.full_url,504,'Gateway Timeout',{},None)
            return Response({'elements':[]})
        self.assertEqual(self.fetch(opener),[])
        self.assertEqual(len(sections),2)
        self.assertEqual(sections[0],sections[1])

    def test_persistent_gateway_overload_does_not_multiply_section_requests(self):
        from urllib.error import HTTPError
        sections = []
        def opener(request, **_):
            query = parse_qs(request.data.decode())['data'][0]
            if 'out bb;' in query:
                return Response({'elements':[{'bounds':{'minlat':48,'minlon':8,'maxlat':48.5,'maxlon':8.5}}]})
            sections.append(query)
            raise HTTPError(request.full_url,504,'Gateway Timeout',{},None)
        with self.assertRaises(HTTPError) as caught:
            self.fetch(opener)
        self.assertTrue(is_retryable_overpass_error(caught.exception))
        self.assertEqual(len(sections),3)
        self.assertEqual(len(set(sections)),1)
