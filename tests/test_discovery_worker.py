import json
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import discovery_worker as worker
from hiring_scraper.app import worker as feed_worker
from hiring_scraper.app.models import Base, Company, DiscoveryRun, Job, JobFeed, utcnow


class DiscoveryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'worker.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.patches = [
            patch.object(worker, "SessionLocal", self.factory),
            patch.object(worker, "engine", self.engine),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def _company(self, **overrides):
        values = {"source": "openstreetmap", "source_id": "node/1", "name": "Acme GmbH",
                  "website_url": "https://acme.example/", "domain": "acme.example",
                  "career_status": "not_checked", "next_discovery_at": utcnow() - timedelta(minutes=1)}
        values.update(overrides)
        with self.factory.begin() as session:
            company = Company(**values)
            session.add(company)
            session.flush()
            return company.id

    def test_due_company_is_leased_and_not_claimed_twice(self):
        company_id = self._company()
        first = worker._claim_due_company()
        self.assertIsNotNone(first)
        self.assertEqual(first[0], company_id)
        self.assertIsNone(worker._claim_due_company())
        with self.factory() as session:
            company = session.get(Company, company_id)
            run = session.get(DiscoveryRun, first[1])
            self.assertEqual(run.status, "running")
            self.assertIsNotNone(company.discovery_lease_until)

    def test_expired_company_lease_marks_the_old_attempt_failed_before_requeue(self):
        company_id = self._company(discovery_lease_until=utcnow() - timedelta(minutes=1))
        with self.factory.begin() as session:
            old_run = DiscoveryRun(company_id=company_id, status="running", started_at=utcnow() - timedelta(hours=1))
            session.add(old_run)
            session.flush()
            old_run_id = old_run.id

        claimed = worker._claim_due_company()

        self.assertIsNotNone(claimed)
        with self.factory() as session:
            old_run = session.get(DiscoveryRun, old_run_id)
            new_run = session.get(DiscoveryRun, claimed[1])
            self.assertEqual(old_run.status, "failed")
            self.assertEqual(new_run.status, "running")

    def test_empty_discovered_career_pages_are_saved_for_direct_refresh(self):
        company_id = self._company()
        claimed = worker._claim_due_company()
        result = {"status": "career_content_found", "pages": [
            {"url": "https://acme.example/careers", "classification": "career_content"},
            {"url": "https://acme.example/jobs", "classification": "career_content"},
            {"url": "https://untrusted.example/jobs", "classification": "career_content",
             "html_extraction_trust": "unverified_external_source"},
            {"url": "https://generic.example/jobs", "classification": "career_content"}], "boards": []}
        worker._persist_discovery(company_id, claimed[1], result)
        with self.factory() as session:
            feeds = session.scalars(select(JobFeed).where(JobFeed.company_id == company_id)).all()
            self.assertEqual({feed.feed_url for feed in feeds},
                             {"https://acme.example/careers", "https://acme.example/jobs"})
            self.assertTrue(all(feed.provider == "html_jobs" and feed.next_scan_at for feed in feeds))
        self.assertIsNone(worker._claim_due_company())

    def test_known_feed_is_not_claimed_for_homepage_rediscovery(self):
        company_id = self._company(career_status="jobs_feed_found")
        with self.factory.begin() as session:
            session.add(JobFeed(company_id=company_id, provider="greenhouse",
                                feed_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                                status="failed", next_scan_at=utcnow()))
        self.assertIsNone(worker._claim_due_company())
        with self.factory() as session:
            self.assertEqual(session.scalars(select(DiscoveryRun)).all(), [])

    def test_existing_career_page_is_registered_for_direct_refresh(self):
        company_id = self._company(career_status="career_page_found",
                                   career_url="https://acme.example/careers")
        with patch.object(worker, "discover", side_effect=AssertionError("Discovery restarted")):
            self.assertFalse(worker.process_once())
        with self.factory() as session:
            feed = session.scalar(select(JobFeed).where(JobFeed.company_id == company_id))
            self.assertIsNotNone(feed)
            self.assertEqual(feed.provider, "html_jobs")
            self.assertEqual(feed.feed_url, "https://acme.example/careers")
            self.assertIsNotNone(feed.next_scan_at)
            self.assertEqual(session.scalars(select(DiscoveryRun)).all(), [])

    def test_direct_career_refresh_finds_new_jobs_after_a_failed_attempt(self):
        company_id = self._company(career_status="career_page_found",
                                   career_url="https://acme.example/careers")
        with patch.object(worker, "discover", side_effect=AssertionError("Discovery restarted")):
            self.assertFalse(worker.process_once())
        requested = []
        responses = iter([({"state": "failed", "status": 503}, b""),
                          ({"state": "ok", "status": 200}, b'<script type="application/ld+json">' +
                           json.dumps({"@type": "JobPosting", "title": "Python Developer",
                                       "url": "https://acme.example/jobs/new"}).encode() + b'</script>')])

        class OfflineClient:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url):
                requested.append(url)
                return next(responses)

        with patch.object(feed_worker, "SessionLocal", self.factory), \
                patch.object(feed_worker, "engine", self.engine), \
                patch.object(feed_worker, "Client", OfflineClient), \
                patch.object(feed_worker, "_pace_origin"):
            self.assertTrue(feed_worker.scan_once())
            with self.factory.begin() as session:
                feed = session.scalar(select(JobFeed).where(JobFeed.company_id == company_id))
                self.assertEqual(feed.status, "failed")
                self.assertEqual(feed.attempt_count, 1)
                feed.next_scan_at = utcnow() - timedelta(seconds=1)
            self.assertTrue(feed_worker.scan_once())
        with self.factory() as session:
            jobs = session.scalars(select(Job)).all()
            self.assertEqual([job.title for job in jobs], ["Python Developer"])
            self.assertTrue(jobs[0].is_active)
            self.assertEqual(session.scalars(select(DiscoveryRun)).all(), [])
        self.assertEqual(requested, ["https://acme.example/careers", "https://acme.example/careers"])

    def test_job_location_alias_gets_pilot_city_point(self):
        locations = worker._job_locations({"locations": [{"label": "Karlsruhe - Mail & Media"}]}, [])
        self.assertEqual(locations[0]["latitude"], 49.0068705)
        self.assertEqual(locations[0]["longitude"], 8.4034195)

    def test_parsed_feed_and_jobs_persist_and_status_stays_stable_on_later_miss(self):
        company_id = self._company(career_status="jobs_feed_found",
                                   career_url="https://acme.example/careers")
        with self.factory.begin() as session:
            run = DiscoveryRun(company_id=company_id, status="running", started_at=utcnow())
            session.add(run)
            session.flush()
            run_id = run.id
        result = {
            "status": "jobs_feed_found",
            "pages": [{"url": "https://acme.example/careers", "classification": "career_content"}],
            "boards": [{"provider": "greenhouse", "tenant": "acme", "board_url": "https://job-boards.greenhouse.io/acme",
                        "feed_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
                        "feed_state": "parsed", "complete": True,
                        "jobs": [{"id": "123", "title": "Engineer", "url": "https://acme.example/job/123",
                                  "location": "Karlsruhe", "employment_type": "Full-time"}]}],
        }
        self.assertEqual(worker._persist_discovery(company_id, run_id, result), 1)
        with self.factory() as session:
            company = session.get(Company, company_id)
            feed = session.scalar(select(JobFeed).where(JobFeed.company_id == company_id))
            job = session.scalar(select(Job).where(Job.feed_id == feed.id))
            run = session.get(DiscoveryRun, run_id)
            self.assertEqual(company.career_status, "jobs_feed_found")
            self.assertEqual(company.career_url, "https://acme.example/careers")
            self.assertIsNotNone(company.next_discovery_at)
            self.assertIsNone(company.discovery_lease_until)
            self.assertEqual(feed.status, "parsed")
            self.assertEqual(feed.job_count, 1)
            self.assertEqual(job.title, "Engineer")
            self.assertTrue(job.is_active)
            self.assertEqual(run.status, "jobs_feed_found")
            self.assertEqual(run.pages_checked, 1)
            self.assertEqual(run.evidence["career_pages"][0]["url"], "https://acme.example/careers")

        with self.factory.begin() as session:
            next_run = DiscoveryRun(company_id=company_id, status="running", started_at=utcnow())
            session.add(next_run)
            session.flush()
            next_run_id = next_run.id
        worker._persist_discovery(company_id, next_run_id, {"status": "unresolved", "pages": [], "boards": []})
        with self.factory() as session:
            company = session.get(Company, company_id)
            self.assertEqual(company.career_status, "jobs_feed_found")
            self.assertEqual(session.scalar(select(Job).where(Job.feed_id == feed.id)).title, "Engineer")

    def test_unverified_external_html_board_is_not_imported(self):
        company_id = self._company()
        with self.factory.begin() as session:
            run = DiscoveryRun(company_id=company_id, status="running", started_at=utcnow())
            session.add(run)
            session.flush()
            run_id = run.id
        pages = [
            {"url": "https://acme.example/", "parent": None, "title": "Acme", "classification": "ordinary_page"},
            {"url": "https://www.hogapage.de/jobs/job/", "parent": "https://acme.example/",
             "title": "Gastronomie Jobs | HOGAPAGE", "headings": "Alle Jobs", "classification": "career_content"},
        ]
        result = {"status": "jobs_extracted", "pages": pages,
                  "boards": [{"provider": "html_jobs", "tenant": "www.hogapage.de",
                              "board_url": "https://www.hogapage.de/jobs/job/",
                              "feed_url": "https://www.hogapage.de/jobs/job/", "discovered_on": pages[1]["url"],
                              "feed_state": "parsed", "complete": True,
                              "jobs": [{"id": "fake", "title": "Generic Role", "url": "https://www.hogapage.de/jobs/job/generic"}]}]}
        worker._persist_discovery(company_id, run_id, result)
        with self.factory() as session:
            self.assertEqual(session.scalars(select(JobFeed).where(JobFeed.company_id == company_id)).all(), [])

    def test_shared_external_html_feed_persists_only_matching_hiring_organization(self):
        company_id = self._company(name="Verkehrsbetriebe Karlsruhe GmbH",
                                   website_url="https://www.vbk.info/", domain="vbk.info")
        with self.factory.begin() as session:
            run = DiscoveryRun(company_id=company_id, status="running", started_at=utcnow())
            session.add(run)
            session.flush()
            run_id = run.id
        pages = [
            {"url": "https://www.vbk.info/", "parent": None, "depth": 0},
            {"url": "https://jobs.shared.example/jobs", "parent": "https://www.vbk.info/",
             "depth": 1, "classification": "career_content"},
        ]
        jobs = [
            {"id": "vbk", "title": "VBK Planner", "url": "https://jobs.shared.example/job/vbk",
             "raw_metadata": {"hiring_organization": {"name": "Verkehrsbetriebe Karlsruhe GmbH"}}},
            {"id": "avg", "title": "AVG Driver", "url": "https://jobs.shared.example/job/avg",
             "raw_metadata": {"hiring_organization": {"name": "Albtal-Verkehrs-Gesellschaft mbH"}}},
        ]
        result = {"status": "jobs_extracted", "pages": pages, "boards": [{
            "provider": "html_jobs", "tenant": "jobs.shared.example",
            "board_url": pages[1]["url"], "feed_url": pages[1]["url"],
            "discovered_on": pages[1]["url"], "feed_state": "parsed", "complete": True,
            "jobs": jobs,
        }]}
        self.assertEqual(worker._persist_discovery(company_id, run_id, result), 1)
        with self.factory() as session:
            stored = session.scalars(select(Job).where(Job.feed.has(company_id=company_id))).all()
            self.assertEqual([job.title for job in stored], ["VBK Planner"])

    def test_worker_runs_discovery_and_schedules_next_check(self):
        company_id = self._company()
        discovered = {"status": "career_content_found", "pages": [
            {"url": "https://acme.example/careers", "classification": "career_content"}], "boards": []}
        with patch.object(worker, "discover", return_value=discovered), \
                patch.object(worker, "Client"):
            self.assertTrue(worker.process_once())
        with self.factory() as session:
            company = session.get(Company, company_id)
            runs = session.scalars(select(DiscoveryRun).where(DiscoveryRun.company_id == company_id)).all()
            self.assertEqual(company.career_status, "career_page_found")
            self.assertEqual(company.career_url, "https://acme.example/careers")
            next_discovery = company.next_discovery_at
            if next_discovery.tzinfo is None:
                next_discovery = next_discovery.replace(tzinfo=timezone.utc)
            self.assertGreater(next_discovery, utcnow())
            self.assertIsNone(company.discovery_lease_until)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].status, "career_content_found")
            self.assertEqual(runs[0].pages_checked, 1)


if __name__ == "__main__":
    unittest.main()
