import unittest
from urllib.parse import parse_qs

from hiring_scraper.location_discovery import fetch_location_companies, resolve_location_candidates


class LocationCompanyDiscoveryTests(unittest.TestCase):
    def test_uses_direct_homepage_and_matching_wikidata_website_evidence(self):
        payload = {"elements": [
            {"type": "node", "id": 7, "lat": 49.0, "lon": 8.4,
             "tags": {"name": "Direct GmbH", "office": "company", "website": "direct-web.de"}},
            {"type": "way", "id": 8, "center": {"lat": 49.001, "lon": 8.401},
             "tags": {"name": "Wikidata Works", "industrial": "factory", "wikidata": "Q42"}},
            {"type": "node", "id": 9, "lat": 49.5, "lon": 8.4,
             "tags": {"name": "Outside GmbH", "office": "company", "website": "outside.example"}},
        ]}
        entities = {"Q42": {"labels": {"de": {"value": "Wikidata Works"}},
                           "claims": {"P856": [{"rank": "normal", "mainsnak": {
                               "datavalue": {"value": "https://wikidata-works.de/"}}}]}}}

        rows = resolve_location_candidates(payload, 49.0, 8.4, 5000, entities=entities)

        self.assertEqual([row["name"] for row in rows], ["Direct GmbH", "Wikidata Works"])
        direct, matched = rows
        self.assertEqual(direct["website_url"], "https://direct-web.de")
        self.assertEqual(direct["domain_match_method"], "osm_website_tag")
        self.assertEqual(matched["website_url"], "https://wikidata-works.de/")
        self.assertEqual(matched["domain_match_method"], "osm_entity_wikidata_p856")
        self.assertIsNone(matched["website"] if "website" in matched else None)

    def test_posts_the_bounded_radius_query_to_overpass(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                return b'{"elements": []}'

        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return Response()

        self.assertEqual(fetch_location_companies(49.0, 8.4, 5000, opener=opener), [])
        request, timeout = calls[0]
        self.assertEqual(request.method, "POST")
        self.assertIn("around:5000,49,8.4", parse_qs(request.data.decode())["data"][0])
        self.assertEqual(timeout, 110)

    def test_reports_company_and_homepage_counts_as_location_results_are_enriched(self):
        payload = {"elements": [
            {"type": "node", "id": 7, "lat": 49.0, "lon": 8.4,
             "tags": {"name": "Direct GmbH", "office": "company", "website": "direct-web.de"}},
            {"type": "node", "id": 8, "lat": 49.001, "lon": 8.401,
             "tags": {"name": "Needs Match GmbH", "wikidata": "Q42"}},
        ]}
        calls = []

        def report(message, **counts):
            calls.append((message, counts))

        rows = resolve_location_candidates(payload, 49.0, 8.4, 5000, entities={},
                                           progress_callback=report)

        self.assertEqual(len(rows), 2)
        self.assertEqual(calls, [
            ("Checking company website information", {"companies_found": 2, "homepages_found": 1}),
            ("Saving company and website results", {"companies_found": 2, "homepages_found": 1}),
        ])

    def test_rejects_malformed_and_private_homepage_addresses_from_map_data(self):
        hosts = ["http://127.0.0.1/admin", "http://169.254.169.254/latest/",
                 "http://[::1]/", "not a url", "https://single-label", "http://bad host.example/"]
        payload = {"elements": [
            {"type": "node", "id": index + 1, "lat": 49.0, "lon": 8.4,
             "tags": {"name": f"Company {index}", "office": "company", "website": host}}
            for index, host in enumerate(hosts)
        ]}

        rows = resolve_location_candidates(payload, 49.0, 8.4, 1000, entities={})

        self.assertEqual(len(rows), len(hosts))
        self.assertTrue(all(row["website_url"] is None for row in rows))
        self.assertTrue(all(row["domain"] is None for row in rows))


if __name__ == "__main__":
    unittest.main()
