import unittest
import json
from pathlib import Path

from experiments.enrich_osm_websites import build_enrichment, merge_career_poc_results


class OSMWebsiteEnrichmentTests(unittest.TestCase):
    def test_reapplying_same_suggestions_preserves_the_original_applied_count(self):
        suggestions = [{"source_id": "node/1", "eligible_for_enrichment": True, "relation": "entity",
                        "identity_score": 1.0, "website_url": "https://acme.example/",
                        "method": "osm_entity_wikidata_p856", "entity_id": "Q1"}]
        first = build_enrichment({"companies": []}, suggestions, "first")
        second = build_enrichment(first, suggestions, "second")
        self.assertEqual(first["website_enrichment"]["applied_count"], 1)
        self.assertEqual(second["website_enrichment"]["applied_count"], 1)

    def test_merges_career_poc_only_for_selected_matching_website_hosts(self):
        candidates = [
            {"osm_type": "node", "osm_id": "1", "name": "Acme GmbH", "website": ""},
            {"osm_type": "node", "osm_id": "2", "name": "Other GmbH", "website": ""},
        ]
        existing = {"observed_at": "old", "website_enrichment": {"source": "OSM Wikidata P856", "applied_count": 2},
                    "companies": [
                        {"source_id": "node/1", "website_url": "https://acme.example/",
                         "website_resolution": {"method": "osm_entity_wikidata_p856"},
                         "career_status": "not_checked", "feeds": []},
                        {"source_id": "node/2", "website_url": "https://other.example/",
                         "career_status": "not_checked", "feeds": []},
                    ]}
        suggestions = [
            {"source_id": "node/1", "eligible_for_enrichment": True, "relation": "entity",
             "identity_score": 1.0, "website_url": "https://acme.example/"},
            {"source_id": "node/2", "eligible_for_enrichment": True, "relation": "brand",
             "identity_score": 1.0, "website_url": "https://other.example/"},
        ]
        posting = {"@context": "https://schema.org", "@type": "JobPosting",
                   "title": "Platform Engineer", "url": "https://acme.example/jobs/platform",
                   "hiringOrganization": {"name": "Acme GmbH"}}
        body = ('<h1>Careers</h1><script type="application/ld+json">' + json.dumps(posting) + '</script>').encode()
        results = [
            {"osm_source_id": "node/1", "website": "https://www.acme.example/", "name": "Acme GmbH",
             "status": "jobs_extracted", "pages": [
                 {"url": "https://www.acme.example/", "parent": None, "depth": 0},
                 {"url": "https://www.acme.example/careers", "parent": "https://www.acme.example/",
                  "depth": 1, "capture": "acme", "classification": "career_content"}],
             "boards": [{"provider": "html_jobs", "feed_state": "parsed",
                         "discovered_on": "https://www.acme.example/careers",
                         "board_url": "https://www.acme.example/careers",
                         "feed_url": "https://www.acme.example/careers", "jobs": []}]},
            {"osm_source_id": "node/2", "website": "https://wrong.example/", "name": "Other GmbH",
             "status": "jobs_extracted", "pages": [], "boards": []},
        ]
        captures = {"acme": ({"capture": "acme", "checked_at": "2026-10-06T01:00:00+00:00"},
                              body, Path("acme.body"))}

        updated, counts = merge_career_poc_results(existing, candidates, suggestions, results, captures,
                                                    "2026-10-06T01:00:00+00:00")
        by_id = {item["source_id"]: item for item in updated["companies"]}
        self.assertEqual(counts["career_results_matched"], 1)
        self.assertEqual(counts["career_results_domain_mismatch_skipped"], 1)
        self.assertEqual(counts["career_html_rows_accepted"], 1)
        self.assertEqual(by_id["node/1"]["feeds"][0]["jobs"][0]["title"], "Platform Engineer")
        self.assertEqual(by_id["node/1"]["feeds"][0]["jobs"][0]["raw_metadata"]["hiring_organization"]["name"],
                         "Acme GmbH")
        self.assertEqual(by_id["node/2"]["career_status"], "not_checked")
        self.assertEqual(updated["website_enrichment"], existing["website_enrichment"])

    def test_applies_only_the_strongest_eligible_suggestion_and_preserves_discovery_state(self):
        existing = {'observed_at': 'old', 'companies': [
            {'source_id': 'node/1', 'website_url': None, 'career_status': 'jobs_extracted',
             'feeds': [{'provider': 'html_jobs', 'feed_url': 'https://acme.example/jobs'}]},
            {'source_id': 'node/2', 'website_url': 'https://manual.example/', 'career_status': 'unresolved'},
        ]}
        suggestions = [
            {'source_id': 'node/1', 'eligible_for_enrichment': True, 'relation': 'operator',
             'identity_score': 1.0, 'website_url': 'https://operator.example/',
             'method': 'osm_operator_wikidata_p856', 'entity_id': 'Q2', 'evidence_url': 'https://www.wikidata.org/wiki/Q2'},
            {'source_id': 'node/1', 'eligible_for_enrichment': True, 'relation': 'entity',
             'identity_score': 0.0, 'website_url': 'https://acme.example/',
             'method': 'osm_entity_wikidata_p856', 'entity_id': 'Q1', 'evidence_url': 'https://www.wikidata.org/wiki/Q1'},
            {'source_id': 'node/2', 'eligible_for_enrichment': True, 'relation': 'entity',
             'identity_score': 1.0, 'website_url': 'https://should-not-overwrite.example/',
             'method': 'osm_entity_wikidata_p856', 'entity_id': 'Q3', 'evidence_url': 'https://www.wikidata.org/wiki/Q3'},
            {'source_id': 'node/3', 'eligible_for_enrichment': False, 'relation': 'brand',
             'identity_score': 0.0, 'website_url': 'https://wrong-brand.example/',
             'method': 'osm_brand_wikidata_p856', 'entity_id': 'Q4', 'evidence_url': 'https://www.wikidata.org/wiki/Q4'},
        ]
        result = build_enrichment(existing, suggestions, 'new')
        by_id = {item['source_id']: item for item in result['companies']}
        self.assertEqual(result['observed_at'], 'new')
        self.assertEqual(by_id['node/1']['website_url'], 'https://acme.example/')
        self.assertEqual(by_id['node/1']['career_status'], 'jobs_extracted')
        self.assertEqual(by_id['node/1']['feeds'], existing['companies'][0]['feeds'])
        self.assertEqual(by_id['node/2']['website_url'], 'https://manual.example/')
        self.assertNotIn('node/3', by_id)
        self.assertEqual(result['website_enrichment']['applied_count'], 1)


if __name__ == '__main__':
    unittest.main()
