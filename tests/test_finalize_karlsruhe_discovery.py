import unittest
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from experiments.finalize_karlsruhe_discovery import build_enrichment, filter_unverified_html, finalize


class FinalizeDiscoveryTests(unittest.TestCase):
    def test_refreshes_cached_html_jobs_with_current_parser_metadata(self):
        home = {"url": "https://acme.example/", "parent": None, "depth": 0,
                "classification": "ordinary_page"}
        source = {"url": "https://acme.example/careers", "parent": home["url"],
                  "depth": 1, "capture": "career", "classification": "career_content"}
        posting = {"@context": "https://schema.org", "@type": "JobPosting",
                   "title": "Platform Engineer", "url": "https://acme.example/jobs/platform",
                   "hiringOrganization": {"name": "Acme GmbH"}}
        body = ('<h1>Careers</h1><script type="application/ld+json">' +
                json.dumps(posting) + '</script>').encode()
        result = {"name": "Acme GmbH", "website": "https://acme.example/",
                  "status": "jobs_extracted", "pages": [home, source], "boards": [{
                      "provider": "html_jobs", "discovered_on": source["url"], "board_url": source["url"],
                      "feed_url": source["url"], "feed_state": "parsed", "jobs": [
                          {"id": "old", "title": "Legacy parser title", "url": "https://acme.example/jobs/platform"}
                      ]
                  }]}
        captures = {"career": ({"capture": "career"}, body, Path("career.body"))}
        filter_unverified_html(result, captures)
        job = result["boards"][0]["jobs"][0]
        self.assertEqual(job["title"], "Platform Engineer")
        self.assertEqual(job["raw_metadata"]["hiring_organization"]["name"], "Acme GmbH")

    def test_reprocesses_cached_external_html_and_recovers_only_matching_employer_jobs(self):
        home = {"url": "https://www.vbk.info/", "parent": None, "depth": 0,
                "classification": "ordinary_page"}
        source = {"url": "https://jobs.shared.example/jobs", "parent": home["url"],
                  "depth": 1, "capture": "external", "classification": "career_content"}
        postings = [
            {"@context": "https://schema.org", "@type": "JobPosting",
             "title": "VBK Planner", "url": "https://jobs.shared.example/job/vbk",
             "hiringOrganization": {"name": "Verkehrsbetriebe Karlsruhe GmbH"}},
            {"@context": "https://schema.org", "@type": "JobPosting",
             "title": "AVG Driver", "url": "https://jobs.shared.example/job/avg",
             "hiringOrganization": {"name": "Albtal-Verkehrs-Gesellschaft mbH"}},
        ]
        body = ("<title>Jobs</title><h1>Offene Stellen</h1>" + "".join(
            '<script type="application/ld+json">' + json.dumps(item) + '</script>' for item in postings
        )).encode()
        result = {"name": "Verkehrsbetriebe Karlsruhe GmbH", "website": "https://www.vbk.info/",
                  "status": "career_content_found", "pages": [home, source], "boards": [],
                  "unverified_external_html_pages": [{"url": source["url"], "job_count": 2,
                                                       "reason": "unverified_external_source"}]}
        captures = {"external": ({"capture": "external"}, body, Path("external.body"))}
        raw, accepted, rejected = filter_unverified_html(result, captures)
        self.assertEqual((raw, accepted, rejected), (2, 1, 1))
        self.assertEqual(result["boards"][0]["jobs"][0]["title"], "VBK Planner")
        self.assertEqual(source["html_extraction_trust"], "linked_external_verified")
        self.assertEqual(result["unverified_external_html_pages"][0]["job_count"], 1)

    def test_shared_detail_capture_is_reused_only_for_the_employer_named_in_jobposting(self):
        home = {"url": "https://www.vbk.info/", "parent": None, "depth": 0}
        listing = {"url": "https://www.wir-bewegen-alle.de/bewerben/jobs",
                   "parent": home["url"], "depth": 1, "capture": "listing",
                   "classification": "career_content"}
        detail = {"url": "https://www.wir-bewegen-alle.de/bewerben/jobs/job/instandhaltungsplanerin-baugewerke",
                  "parent": listing["url"], "depth": 2, "capture": "detail",
                  "classification": "jobposting"}
        posting = {"@context": "https://schema.org", "@type": "JobPosting",
                   "title": "Instandhaltungsplaner Baugewerke", "url": detail["url"],
                   "hiringOrganization": {"name": "VBK"}}
        detail_body = ('<h1>Instandhaltungsplaner Baugewerke</h1><script type="application/ld+json">' +
                       json.dumps(posting) + '</script>').encode()
        result = {"name": "Verkehrsbetriebe Karlsruhe GmbH", "website": "https://www.vbk.info/",
                  "status": "career_content_found", "pages": [home, listing], "boards": [],
                  "unverified_external_html_pages": [{"url": listing["url"], "job_count": 4,
                                                       "reason": "unverified_external_source"}]}
        captures = {"listing": ({}, b"<h1>Jobs</h1>", Path("listing.body")),
                    "detail": ({}, detail_body, Path("detail.body"))}
        shared_by_parent = {listing["url"]: [detail]}
        raw, accepted, rejected = filter_unverified_html(result, captures, shared_by_parent)
        self.assertEqual((raw, accepted, rejected), (5, 1, 4))
        self.assertEqual(result["boards"][0]["jobs"][0]["title"], "Instandhaltungsplaner Baugewerke")
        self.assertEqual(result["boards"][0]["evidence_kind"], "shared_linked_html_job_detail")
        recovered_detail = next(page for page in result["pages"] if page.get("url") == detail["url"])
        self.assertEqual(recovered_detail["html_extraction_trust"], "linked_external_verified")

    def test_generic_external_jobs_are_excluded_from_importable_results(self):
        home = {"url": "https://gaul-catering.de/", "parent": None, "title": "Gaul's Catering",
                "headings": "Welcome", "classification": "ordinary_page"}
        listing = {"url": "https://www.hogapage.de/jobs/job/", "parent": home["url"],
                   "title": "Hospitality Jobs | HOGAPAGE", "headings": "All jobs",
                   "classification": "career_content"}
        result = {"name": "Gaul's Catering GmbH", "website": "https://gaul-catering.de/",
                  "status": "jobs_extracted", "pages": [home, listing],
                  "boards": [{"provider": "html_jobs", "discovered_on": listing["url"],
                              "feed_state": "parsed", "jobs": [{"id": str(i)} for i in range(142)]}]}
        raw, accepted, rejected = filter_unverified_html(result)
        self.assertEqual((raw, accepted, rejected), (142, 0, 142))
        self.assertEqual(result["boards"], [])
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(listing["html_extraction_trust"], "unverified_external_source")

    def test_fixture_keeps_no_website_status_and_old_verified_feeds(self):
        candidates = [
            {"osm_type": "node", "osm_id": "1", "name": "Acme", "website": "https://acme.example/"},
            {"osm_type": "way", "osm_id": "2", "name": "No Site", "website": ""},
        ]
        result = {"osm_source_id": "node/1", "status": "jobs_feed_found",
                  "pages": [{"url": "https://acme.example/careers", "classification": "career_content",
                             "capture": "cap"}],
                  "boards": [{"provider": "personio", "tenant": "acme", "board_url": "https://acme.jobs.personio.de",
                              "feed_url": "https://acme.jobs.personio.de/xml", "feed_state": "parsed",
                              "complete": True, "jobs": [{"id": "1", "title": "Engineer",
                                  "url": "https://acme.jobs.personio.de/job/1"}]}]}
        prior = {"website_enrichment": {"source": "OSM Wikidata P856", "applied_count": 13},
                 "companies": [{"source_id": "node/1", "career_status": "career_page_found",
                                "feeds": [{"provider": "greenhouse", "feed_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                                           "status": "parsed", "jobs": []}]}]}
        captures = {"cap": ({"capture": "cap", "checked_at": "2026-10-05T12:00:00+00:00"}, b"", Path("capture"))}
        output = build_enrichment(candidates, [result], prior, captures, "2026-10-05T12:00:00+00:00")
        by_id = {item["source_id"]: item for item in output["companies"]}
        self.assertEqual(by_id["way/2"]["career_status"], "not_checked")
        self.assertEqual(by_id["node/1"]["career_status"], "jobs_feed_found")
        self.assertEqual(output["website_enrichment"], prior["website_enrichment"])
        self.assertEqual({feed["provider"] for feed in by_id["node/1"]["feeds"]}, {"personio", "greenhouse"})
        self.assertEqual(by_id["node/1"]["last_checked_at"], "2026-10-05T12:00:00+00:00")

    def test_finalizer_checks_scope_and_writes_importable_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates_path, seeds_path, scope_path = root / "candidates.csv", root / "seeds.json", root / "scope.json"
            run_dir, out, enrichment_path = root / "run-input", root / "run-final", root / "enrichment.json"
            run_dir.mkdir()
            fields = ["name", "website", "category", "lat", "lon", "osm_type", "osm_id"]
            with candidates_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerow({"name": "Acme", "website": "https://acme.example/", "lat": "49",
                                 "lon": "8", "osm_type": "node", "osm_id": "1"})
            seeds = [{"name": "Acme", "website": "https://acme.example/", "osm_source_id": "node/1"}]
            seeds_path.write_text(json.dumps(seeds), encoding="utf-8")
            scope_path.write_text(json.dumps({"candidate_count": 1, "with_website_seed_count": 1,
                                              "no_website_count": 0, "invalid_website_count": 0,
                                              "unique_website_urls": 1, "unique_hosts": 1}), encoding="utf-8")
            body = b'<h1>Jobs</h1>'
            metadata = {"capture": "cap1", "url": "https://acme.example/careers",
                        "checked_at": "2026-10-05T12:00:00+00:00", "sha256": hashlib.sha256(body).hexdigest(),
                        "state": "ok", "status": 200}
            (run_dir / "http").mkdir()
            (run_dir / "http/cap1.json").write_text(json.dumps(metadata), encoding="utf-8")
            (run_dir / "http/cap1.body").write_bytes(body)
            result = {**seeds[0], "status": "career_content_found", "pages": [
                {"url": "https://acme.example/careers", "classification": "career_content", "capture": "cap1"}],
                "boards": []}
            (run_dir / "results.json").write_text(json.dumps([result]), encoding="utf-8")
            enrichment_path.write_text(json.dumps({"companies": []}), encoding="utf-8")
            with patch.dict(os.environ, {'PATH': directory}):
                summary = finalize(candidates_path, seeds_path, scope_path, [run_dir], out, enrichment_path)
            self.assertEqual(summary["crawled_candidates"], 1)
            self.assertTrue((out / "run.json").exists())
            metadata = json.loads((out / 'run.json').read_text())
            self.assertIsNone(metadata['git_revision'])
            self.assertIsNone(metadata['git_dirty'])
            self.assertTrue(metadata['source_sha256'])
            self.assertTrue((out / "http/cap1.body").exists())
            enrichment = json.loads(enrichment_path.read_text(encoding="utf-8"))
            self.assertEqual(enrichment["companies"][0]["career_url"], "https://acme.example/careers")


if __name__ == "__main__":
    unittest.main()
