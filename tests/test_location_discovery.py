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

    def test_enrichment_receives_raw_contact_tags_and_keeps_its_evidence(self):
        payload = {"elements": [{"type": "node", "id": 7, "lat": 49.0, "lon": 8.4,
                                "tags": {"name": "Northstar Robotics", "email": "info@northstar.de"}}]}
        def resolver(rows):
            self.assertEqual(rows[0]["tags"]["email"], "info@northstar.de")
            rows[0].update(website_url="https://northstar.de/", domain="northstar.de",
                           domain_match_method="osm_email_verified_page",
                           domain_evidence_url="https://northstar.de/impressum/")
        rows = resolve_location_candidates(payload, 49.0, 8.4, 5000, entities={},
                                           homepage_resolver=resolver)
        self.assertEqual(rows[0]["website_url"], "https://northstar.de/")
        self.assertEqual(rows[0]["domain_evidence_url"], "https://northstar.de/impressum/")
        self.assertNotIn("tags", rows[0])

    def test_live_fetch_stage_verifies_email_without_search_credentials(self):
        import json
        from unittest.mock import patch
        payload = {"elements": [{"type": "node", "id": 7, "lat": 49.0, "lon": 8.4,
                                "tags": {"name": "Northstar Robotics GmbH",
                                         "contact:email": "info@northstar-robotics.de"}}]}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _limit): return json.dumps(payload).encode()
        class Pages:
            records = []
            def get(self, url):
                return {"state": "ok", "content_type": "text/html", "final_url": url}, (
                    b"<title>Northstar Robotics GmbH</title><h1>Northstar Robotics</h1>")
        with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "", "TAVILY_API_KEY": ""}), \
                patch("hiring_scraper.location_discovery.Client", return_value=Pages()):
            rows = fetch_location_companies(49.0, 8.4, 5000, opener=lambda *a, **k: Response())
        self.assertEqual(rows[0]["website_url"], "https://northstar-robotics.de/")
        self.assertEqual(rows[0]["domain_match_method"], "osm_email_verified_page")

    def test_live_fetch_stage_uses_configured_tavily_search(self):
        import json
        from unittest.mock import patch
        payload = {"elements": [{"type": "node", "id": 7, "lat": 49.0, "lon": 8.4,
                                "tags": {"name": "Northstar Robotics GmbH", "addr:city": "Karlsruhe",
                                         "addr:postcode": "76133"}}]}
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _limit): return json.dumps(self.payload).encode()
        class Pages:
            records = []
            def get(self, url):
                return {"state": "ok", "content_type": "text/html", "final_url": url}, (
                    b"<title>Northstar Robotics GmbH</title>76133 Karlsruhe")
        with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": "", "TAVILY_API_KEY": "test-token"}), \
                patch("hiring_scraper.location_discovery.Client", return_value=Pages()), \
                patch("hiring_scraper.website_discovery.build_opener") as transport:
            transport.return_value.open.return_value = Response({"results": [
                {"url": "https://northstar-robotics.de/", "title": "Northstar Robotics"}],
                "usage": {"credits": 1}})
            rows = fetch_location_companies(49.0, 8.4, 5000, opener=lambda *a, **k: Response(payload))
        self.assertEqual(rows[0]["website_url"], "https://northstar-robotics.de/")
        self.assertEqual(rows[0]["domain_match_method"], "web_search_verified_page")

    def test_live_region_hint_is_derived_from_nearby_tagged_source_without_becoming_identity(self):
        import json
        from unittest.mock import patch
        payload = {'elements': [
            {'type':'node','id':7,'lat':49.0,'lon':8.4,
             'tags':{'name':'Northstar Robotics GmbH','phone':'+49 721 9200815'}},
            {'type':'node','id':8,'lat':49.001,'lon':8.4,
             'tags':{'name':'Mapped Peer','addr:city':'Karlsruhe','website':'https://peer.de/'}},
            {'type':'node','id':9,'lat':49.02,'lon':8.4,
             'tags':{'name':'Distant Peer','addr:city':'Stutensee','website':'https://distant.de/'}},
        ]}
        class Response:
            def __init__(self, value): self.value=value
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def read(self,_): return json.dumps(self.value).encode()
        class Pages:
            records=[]
            def get(self,url):
                return {'state':'ok','content_type':'text/html','final_url':url}, (
                    b'<title>Northstar Robotics GmbH</title>Contact: Northstar Robotics GmbH '
                    b'Telefon: 0721/9200815')
        queries=[]
        def provider(request,timeout):
            queries.append(json.loads(request.data)['query'])
            return Response({'results':[{'url':'https://northstar.de/'}], 'usage':{'credits':1}})
        with patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':'','TAVILY_API_KEY':'test-token'}), \
                patch('hiring_scraper.location_discovery.Client',return_value=Pages()), \
                patch('hiring_scraper.website_discovery.build_opener') as transport:
            transport.return_value.open.side_effect=provider
            rows=fetch_location_companies(49.0,8.4,5000,opener=lambda *a,**k: Response(payload))
        row=next(r for r in rows if r['name']=='Northstar Robotics GmbH')
        self.assertEqual(row['website_url'],'https://northstar.de/')
        self.assertIn('Karlsruhe Region',queries[0])
        self.assertNotIn('Stutensee',queries[0])
        self.assertNotIn('addr:city',payload['elements'][0]['tags'])

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
