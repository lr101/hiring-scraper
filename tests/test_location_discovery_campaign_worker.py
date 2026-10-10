import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import discovery_worker as worker
from hiring_scraper.app.discovery_jobs import discovery_job_json
from hiring_scraper.app import models
from hiring_scraper.app.models import Base, Company, DiscoveryJobCompany, DiscoveryRun, JobFeed, utcnow


class LocationDiscoveryCampaignWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'campaign.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.patches = [patch.object(worker, "SessionLocal", self.factory),
                        patch.object(worker, "engine", self.engine)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def test_location_campaign_runs_a_company_and_persists_terminal_progress(self):
        with self.factory.begin() as session:
            self.assertIsNotNone(getattr(models, "DiscoveryJob", None),
                                 "The database should persist location discovery jobs")
            DiscoveryJob = models.DiscoveryJob
            DiscoveryJobCompany = models.DiscoveryJobCompany
            company = Company(source="openstreetmap", source_id="node/1", name="Acme GmbH",
                              website_url="https://acme.example/", latitude=49.0, longitude=8.4,
                              career_status="not_checked", next_discovery_at=utcnow() + timedelta(days=10))
            session.add(company)
            session.flush()
            job = DiscoveryJob(label="Karlsruhe", latitude=49.0, longitude=8.4, radius_km=15,
                               status="queued", candidate_total=1, created_at=utcnow())
            session.add(job)
            session.flush()
            session.add(DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                                            status="queued"))
            job_id = job.id

        discovered = {"status": "jobs_feed_found", "pages": [
            {"url": "https://acme.example/careers", "classification": "career_content"}],
            "boards": [{"provider": "greenhouse", "tenant": "acme",
                        "board_url": "https://job-boards.greenhouse.io/acme",
                        "feed_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
                        "feed_state": "parsed", "complete": True,
                        "jobs": [{"id": "123", "title": "Engineer",
                                  "url": "https://acme.example/jobs/123"}]}]}
        with patch.object(worker, "discover", return_value=discovered), patch.object(worker, "Client"):
            self.assertTrue(worker.process_once())

        with self.factory() as session:
            job = session.get(DiscoveryJob, job_id)
            item = session.scalar(select(DiscoveryJobCompany).where(
                DiscoveryJobCompany.discovery_job_id == job_id))
            run = session.get(DiscoveryRun, item.discovery_run_id)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.processed_count, 1)
            self.assertEqual(job.succeeded_count, 1)
            self.assertEqual(job.failed_count, 0)
            self.assertEqual(job.jobs_found, 1)
            self.assertEqual(discovery_job_json(job)["progress_percent"], 100)
            self.assertEqual(item.status, "completed")
            self.assertEqual(item.jobs_found, 1)
            self.assertEqual(run.status, "jobs_feed_found")

    def test_campaign_batch_crawls_distinct_companies_concurrently(self):
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Parallel area", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="career_page_discovery",
                candidate_total=2, created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id
            companies = [
                Company(source="openstreetmap", source_id=f"node/parallel-{index}",
                    name=f"Parallel Company {index}", website_url=f"https://company-{index}.example/",
                    career_status="not_checked")
                for index in range(2)
            ]
            session.add_all(companies)
            session.flush()
            session.add_all(DiscoveryJobCompany(discovery_job_id=job_id, company_id=company.id,
                status="queued") for company in companies)

        together = Barrier(2)

        def discover_together(*_args, **_kwargs):
            together.wait(timeout=2)
            return {"status": "career_content_found", "pages": [], "boards": []}

        with patch.object(worker, "discover", side_effect=discover_together), patch.object(worker, "Client"):
            self.assertTrue(worker.process_batch(location_jobs_only=True, max_workers=2))

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            items = session.scalars(select(DiscoveryJobCompany).where(
                DiscoveryJobCompany.discovery_job_id == job_id)).all()
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.processed_count, 2)
            self.assertEqual(job.succeeded_count, 2)
            self.assertEqual({item.status for item in items}, {"completed"})

    def test_campaign_crawl_receives_requested_city_for_local_page_prioritization(self):
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/dhl-bonn", name="DHL",
                website_url="https://www.dhl.de/", latitude=50.7374, longitude=7.0982,
                career_status="not_checked")
            job = models.DiscoveryJob(label="Bonn", city="Bonn", latitude=50.7374,
                longitude=7.0982, radius_km=15, status="queued",
                stage="career_page_discovery", candidate_total=1, created_at=utcnow())
            session.add_all([company, job])
            session.flush()
            session.add(DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                status="queued"))

        with patch.object(worker, "discover", return_value={
                "status": "career_content_found", "pages": [], "boards": []}) as discover, \
             patch.object(worker, "Client"):
            self.assertTrue(worker.process_once(location_jobs_only=True))

        seed = discover.call_args.args[0]
        self.assertIn("Bonn", seed["preferred_locations"])

    def test_campaign_batch_supports_32_concurrent_discovery_workers(self):
        together = Barrier(32)
        claims = [(index, index, index, index) for index in range(32)]

        def process_together(_claim):
            together.wait(timeout=3)
            return True

        with patch.object(worker, "_prepare_cycle", return_value=None), \
             patch.object(worker, "_claim_campaign_company", side_effect=claims), \
             patch.object(worker, "_process_claimed_company", side_effect=process_together):
            self.assertTrue(worker.process_batch(location_jobs_only=True, max_workers=32))

    def test_claimed_campaign_does_not_start_a_crawl_after_cancellation(self):
        now = utcnow()
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/cancel-before-fetch",
                name="Cancelled Company", website_url="https://cancelled.example/",
                discovery_lease_until=now + timedelta(minutes=10))
            job = models.DiscoveryJob(label="Cancelled area", latitude=49.0, longitude=8.4,
                radius_km=5, status="cancelled", stage="career_page_discovery",
                candidate_total=1, created_at=now)
            session.add_all([company, job])
            session.flush()
            run = DiscoveryRun(company_id=company.id, started_at=now, status="running")
            session.add(run)
            session.flush()
            item = DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                discovery_run_id=run.id, status="running", started_at=now)
            session.add(item)
            session.flush()
            claim = (company.id, run.id, item.id, job.id)

        with patch.object(worker, "discover") as discover, patch.object(worker, "Client"):
            worker._process_claimed_company(claim)
            discover.assert_not_called()

        with self.factory() as session:
            company = session.get(Company, claim[0])
            run = session.get(DiscoveryRun, claim[1])
            item = session.get(DiscoveryJobCompany, claim[2])
            self.assertIsNone(company.discovery_lease_until)
            self.assertEqual(run.status, "cancelled")
            self.assertEqual(item.status, "cancelled")

    def test_location_first_search_imports_companies_and_homepages_before_career_checks(self):
        with self.factory.begin() as session:
            location = models.ConfiguredLocation(label="Karlsruhe", city="Karlsruhe",
                latitude=49.0, longitude=8.4, radius_km=15, interval_days=7,
                enabled=True, next_run_at=utcnow() + timedelta(days=7))
            session.add(location)
            session.flush()
            job = models.DiscoveryJob(configured_location_id=location.id, kind="initial",
                label="Karlsruhe", latitude=49.0, longitude=8.4, radius_km=15,
                status="queued", stage="company_homepage_discovery", candidate_total=0,
                created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id

        companies = [{"source_id": "node/nearby", "name": "Nearby GmbH",
                      "website_url": "https://nearby.example/", "domain_match_method": "osm_website_tag",
                      "domain_evidence_url": "https://www.openstreetmap.org/node/nearby",
                      "category": "office", "source_url": "https://www.openstreetmap.org/node/nearby",
                      "latitude": 49.0, "longitude": 8.4, "location_precision": "point",
                      "location_label": "office"},
                     {"source_id": "node/no-site", "name": "No Website GmbH",
                      "website_url": None, "category": "craft", "source_url": "https://www.openstreetmap.org/node/no-site",
                      "latitude": 49.01, "longitude": 8.41, "location_precision": "point",
                      "location_label": "craft"}]
        with patch.object(worker, "fetch_location_companies", return_value=companies):
            self.assertTrue(worker.process_once())

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            rows = session.scalars(select(Company).order_by(Company.source_id)).all()
            tasks = session.scalars(select(models.DiscoveryJobCompany).where(
                models.DiscoveryJobCompany.discovery_job_id == job_id)).all()
            self.assertEqual(job.stage, "career_page_discovery")
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.companies_found, 2)
            self.assertEqual(job.homepages_found, 1)
            self.assertEqual(job.candidate_total, 1)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row.name for row in rows}, {"Nearby GmbH", "No Website GmbH"})
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].company_id, rows[0].id)

        discovered = {"status": "career_content_found", "pages": [], "boards": []}
        with patch.object(worker, "discover", return_value=discovered), patch.object(worker, "Client"):
            self.assertTrue(worker.process_once())
        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.stage, "complete")
            self.assertEqual(job.processed_count, 1)

    def test_transient_location_source_failure_is_scheduled_for_retry(self):
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Broken source", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="company_homepage_discovery",
                candidate_total=0, created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id

        with self.assertLogs(worker.LOG, level="WARNING") as captured:
            with patch.object(worker, "fetch_location_companies", side_effect=TimeoutError("Overpass timed out")):
                self.assertTrue(worker.process_once())
        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "scheduled")
            self.assertEqual(job.stage, "company_homepage_discovery")
            self.assertIn("Overpass timed out", job.error)
            self.assertIsNotNone(job.scheduled_for)
            self.assertIsNone(job.finished_at)
            self.assertEqual(job.location_scan_attempt_count, 1)
            self.assertIn("retry scheduled", job.progress_message)
            self.assertEqual(discovery_job_json(job)["progress_percent"], 0)
        self.assertIn("retry scheduled", captured.output[0])

    def test_exhausted_location_source_retries_fail_visibly(self):
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Broken source", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="company_homepage_discovery",
                candidate_total=0,
                location_scan_attempt_count=worker.LOCATION_SCAN_MAX_ATTEMPTS - 1,
                created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id

        with self.assertLogs(worker.LOG, level="ERROR"):
            with patch.object(worker, "fetch_location_companies", side_effect=TimeoutError("Overpass timed out")):
                self.assertTrue(worker.process_once())
        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.stage, "company_homepage_discovery")
            self.assertIn("Overpass timed out", job.error)
            self.assertIsNotNone(job.finished_at)
            self.assertEqual(job.location_scan_attempt_count, worker.LOCATION_SCAN_MAX_ATTEMPTS)
            self.assertEqual(discovery_job_json(job)["progress_percent"], 0)

    def test_preview_worker_scope_skips_unrelated_due_company_backlog(self):
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/unrelated", name="Unrelated GmbH",
                              website_url="https://unrelated.example", career_status="not_checked")
            session.add(company)
            session.flush()
            company_id = company.id

        with patch.object(worker, "discover") as discover:
            self.assertFalse(worker.process_once(location_jobs_only=True))
            discover.assert_not_called()

        with self.factory() as session:
            company = session.get(Company, company_id)
            self.assertEqual(company.discovery_attempt_count, 0)
            self.assertIsNone(company.discovery_lease_until)

    def test_location_search_persists_live_phase_and_counts_before_source_request_finishes(self):
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Live progress", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="company_homepage_discovery",
                candidate_total=0, created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id

        def fetch_with_progress(*_args, progress_callback):
            progress_callback("Checking company website information",
                              companies_found=12, homepages_found=3)
            with self.factory() as session:
                job = session.get(models.DiscoveryJob, job_id)
                self.assertEqual(job.status, "running")
                self.assertEqual(job.progress_message, "Checking company website information")
                self.assertEqual(job.companies_found, 12)
                self.assertEqual(job.homepages_found, 3)
                self.assertIsNotNone(job.progress_updated_at)
            return []

        with patch.object(worker, "fetch_location_companies", side_effect=fetch_with_progress):
            self.assertTrue(worker.process_once())

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            payload = discovery_job_json(job)
            self.assertEqual(job.status, "completed")
            self.assertEqual(payload["progress_message"], "Search complete")
            self.assertIsNotNone(payload["progress_updated_at"])

    def test_expired_location_search_ignores_results_and_failures_from_previous_attempt(self):
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Lease area", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="company_homepage_discovery",
                candidate_total=0, created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id

        first_attempt = worker._claim_location_company_search()
        self.assertEqual(first_attempt[0], job_id)
        with self.factory.begin() as session:
            job = session.get(models.DiscoveryJob, job_id)
            job.location_scan_lease_until = utcnow() - timedelta(seconds=1)

        replacement_attempt = worker._claim_location_company_search()
        self.assertEqual(replacement_attempt[0], job_id)
        self.assertNotEqual(replacement_attempt[1], first_attempt[1])
        candidate = {"source_id": "node/late", "name": "Late GmbH",
                     "website_url": "https://late.example/", "latitude": 49.0,
                     "longitude": 8.4, "location_precision": "point"}

        worker._fail_location_company_search(job_id, first_attempt[1], TimeoutError("old timeout"))
        worker._persist_location_companies(job_id, first_attempt[1], [candidate])

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "running")
            self.assertEqual(job.stage, "company_homepage_discovery")
            self.assertEqual(job.location_scan_token, replacement_attempt[1])
            self.assertIsNone(job.error)
            self.assertEqual(session.scalars(select(Company)).all(), [])

        worker._persist_location_companies(job_id, replacement_attempt[1], [candidate])
        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.stage, "career_page_discovery")
            self.assertEqual(job.status, "queued")
            self.assertIsNone(job.location_scan_token)
            self.assertEqual(session.scalar(select(Company.name)), "Late GmbH")

    def test_cancelled_location_search_discards_late_results(self):
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Cancelled location", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="company_homepage_discovery",
                candidate_total=0, created_at=utcnow())
            session.add(job)
            session.flush()
            job_id = job.id

        attempt = worker._claim_location_company_search()
        with self.factory.begin() as session:
            job = session.get(models.DiscoveryJob, job_id)
            job.status = "cancelled"
            job.finished_at = utcnow()
            job.location_scan_lease_until = None
            job.location_scan_token = None
        candidate = {"source_id": "node/late-cancelled", "name": "Late GmbH",
                     "website_url": "https://late.example/", "latitude": 49.0,
                     "longitude": 8.4, "location_precision": "point"}

        worker._persist_location_companies(job_id, attempt[1], [candidate])
        worker._fail_location_company_search(job_id, attempt[1], TimeoutError("late timeout"))

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "cancelled")
            self.assertEqual(job.stage, "company_homepage_discovery")
            self.assertEqual(session.scalars(select(Company)).all(), [])

    def test_expired_company_attempt_cannot_overwrite_a_newer_worker_result(self):
        lease = utcnow() + timedelta(minutes=10)
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/fenced", name="Fenced GmbH",
                              website_url="https://fenced.example/", career_status="not_checked",
                              discovery_lease_until=lease)
            session.add(company)
            session.flush()
            old_run = DiscoveryRun(company_id=company.id, status="failed", started_at=utcnow(),
                                   error="Worker lease expired")
            new_run = DiscoveryRun(company_id=company.id, status="running", started_at=utcnow())
            job = models.DiscoveryJob(label="Fencing area", latitude=49.0, longitude=8.4,
                radius_km=5, status="running", stage="career_page_discovery", candidate_total=1,
                created_at=utcnow())
            session.add_all([old_run, new_run, job])
            session.flush()
            item = models.DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                discovery_run_id=new_run.id, status="running", started_at=utcnow())
            session.add(item)
            company_id, old_run_id, new_run_id = company.id, old_run.id, new_run.id

        worker._persist_discovery(company_id, old_run_id,
                                  {"status": "jobs_extracted", "pages": [], "boards": []})
        worker._record_failure(company_id, old_run_id, TimeoutError("old attempt failed late"))

        with self.factory() as session:
            company = session.get(Company, company_id)
            old_run = session.get(DiscoveryRun, old_run_id)
            new_run = session.get(DiscoveryRun, new_run_id)
            self.assertEqual(company.career_status, "not_checked")
            saved_lease = company.discovery_lease_until
            if saved_lease.tzinfo is None:
                saved_lease = saved_lease.replace(tzinfo=lease.tzinfo)
            self.assertEqual(saved_lease, lease)
            self.assertIsNone(company.discovery_error)
            self.assertEqual(old_run.status, "failed")
            self.assertEqual(new_run.status, "running")
            self.assertEqual(session.scalars(select(JobFeed).where(JobFeed.company_id == company_id)).all(), [])

    def test_failed_company_is_counted_and_closes_campaign_as_failed(self):
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/fail", name="Broken GmbH",
                              website_url="https://broken.example/", latitude=49.0, longitude=8.4,
                              career_status="not_checked")
            session.add(company)
            session.flush()
            job = models.DiscoveryJob(label="Failure area", latitude=49.0, longitude=8.4,
                                      radius_km=5, status="queued", candidate_total=1,
                                      created_at=utcnow())
            session.add(job)
            session.flush()
            item = models.DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                                              status="queued")
            session.add(item)
            job_id = job.id

        with self.assertLogs(worker.LOG, level="ERROR"):
            with patch.object(worker, "discover", side_effect=TimeoutError("source timed out")), \
                    patch.object(worker, "Client"):
                self.assertTrue(worker.process_once())

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.processed_count, 1)
            self.assertEqual(job.failed_count, 1)
            self.assertEqual(job.succeeded_count, 0)
            self.assertEqual(discovery_job_json(job)["progress_percent"], 100)

    def test_cancelled_campaign_drops_a_late_company_result(self):
        lease = utcnow() + timedelta(minutes=15)
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/cancel-success",
                name="Cancelled GmbH", website_url="https://cancelled.example/",
                career_status="not_checked", discovery_lease_until=lease)
            session.add(company)
            session.flush()
            job = models.DiscoveryJob(label="Cancelled area", latitude=49.0, longitude=8.4,
                radius_km=5, status="running", stage="career_page_discovery",
                candidate_total=1, created_at=utcnow())
            run = DiscoveryRun(company_id=company.id, started_at=utcnow(), status="running")
            session.add_all([job, run])
            session.flush()
            item = models.DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                discovery_run_id=run.id, status="running", started_at=utcnow())
            session.add(item)
            session.flush()
            job.status = "cancelled"
            job.finished_at = utcnow()
            company_id, run_id, item_id, job_id = company.id, run.id, item.id, job.id

        result = {"status": "jobs_feed_found", "pages": [], "boards": [{
            "provider": "greenhouse", "feed_url": "https://boards-api.greenhouse.io/v1/boards/cancelled/jobs",
            "feed_state": "parsed", "complete": True,
            "jobs": [{"id": "1", "title": "Engineer", "url": "https://cancelled.example/jobs/1"}],
        }]}
        worker._persist_discovery(company_id, run_id, result, campaign_item_id=item_id)
        worker._finish_campaign_company(item_id, run_id, jobs_found=1)

        with self.factory() as session:
            company = session.get(Company, company_id)
            job = session.get(models.DiscoveryJob, job_id)
            item = session.get(models.DiscoveryJobCompany, item_id)
            run = session.get(DiscoveryRun, run_id)
            self.assertEqual(job.status, "cancelled")
            self.assertEqual(job.processed_count, 0)
            self.assertEqual(item.status, "cancelled")
            self.assertEqual(run.status, "cancelled")
            self.assertEqual(company.career_status, "not_checked")
            self.assertIsNone(company.discovery_lease_until)
            self.assertEqual(session.scalars(select(JobFeed).where(JobFeed.company_id == company_id)).all(), [])

    def test_cancelled_campaign_does_not_record_a_late_worker_failure(self):
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/cancel-failure",
                name="Cancelled GmbH", website_url="https://cancelled.example/",
                career_status="not_checked", discovery_lease_until=utcnow() + timedelta(minutes=15))
            session.add(company)
            session.flush()
            job = models.DiscoveryJob(label="Cancelled area", latitude=49.0, longitude=8.4,
                radius_km=5, status="cancelled", stage="career_page_discovery",
                candidate_total=1, created_at=utcnow(), finished_at=utcnow())
            run = DiscoveryRun(company_id=company.id, started_at=utcnow(), status="running")
            session.add_all([job, run])
            session.flush()
            item = models.DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                discovery_run_id=run.id, status="running", started_at=utcnow())
            session.add(item)
            session.flush()
            company_id, run_id, item_id, job_id = company.id, run.id, item.id, job.id

        worker._record_failure(company_id, run_id, TimeoutError("late timeout"),
                               campaign_item_id=item_id)

        with self.factory() as session:
            company = session.get(Company, company_id)
            job = session.get(models.DiscoveryJob, job_id)
            item = session.get(models.DiscoveryJobCompany, item_id)
            run = session.get(DiscoveryRun, run_id)
            self.assertEqual(job.status, "cancelled")
            self.assertEqual(item.status, "cancelled")
            self.assertEqual(run.status, "cancelled")
            self.assertIsNone(company.discovery_error)
            self.assertIsNone(company.discovery_lease_until)

    def test_recurring_location_schedules_known_sources_without_discovery(self):
        now = utcnow()
        with self.factory.begin() as session:
            companies = [Company(source="test", source_id=str(index), name=f"Company {index}",
                                 website_url=f"https://company-{index}.example/",
                                 latitude=49.0 if index < 3 else 52.0, longitude=8.4,
                                 career_status="not_checked") for index in range(4)]
            companies[1].career_status = "career_page_found"
            companies[1].career_url = "https://company-1.example/careers"
            session.add_all(companies)
            session.flush()
            feeds = [JobFeed(company_id=companies[index].id, provider="greenhouse",
                            feed_url=f"https://boards-api.greenhouse.io/v1/boards/company-{index}/jobs",
                            next_scan_at=now + timedelta(days=1), status="parsed") for index in (0, 3)]
            session.add_all(feeds)
            location = models.ConfiguredLocation(label="Karlsruhe", latitude=49.0, longitude=8.4,
                radius_km=15, interval_days=7, enabled=True, next_run_at=now - timedelta(minutes=1))
            session.add(location)
            session.flush()
            location_id = location.id
            nearby_feed_id, distant_feed_id = feeds[0].id, feeds[1].id
            career_company_id = companies[1].id
        with patch.object(worker, "fetch_location_companies", side_effect=AssertionError("Location rediscovery")), \
                patch.object(worker, "discover", side_effect=AssertionError("Homepage rediscovery")):
            self.assertEqual(worker.schedule_due_locations(now=now), 1)
            self.assertEqual(worker.schedule_due_locations(now=now), 0)
            self.assertFalse(worker.process_once(location_jobs_only=True))
        with self.factory() as session:
            jobs = session.scalars(select(models.DiscoveryJob).where(
                models.DiscoveryJob.configured_location_id == location_id)).all()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].status, "completed")
            self.assertEqual(jobs[0].stage, "complete")
            self.assertEqual(session.scalars(select(DiscoveryJobCompany)).all(), [])
            self.assertLessEqual(session.get(JobFeed, nearby_feed_id).next_scan_at.replace(tzinfo=now.tzinfo), now)
            self.assertGreater(session.get(JobFeed, distant_feed_id).next_scan_at.replace(tzinfo=now.tzinfo), now)
            page_feed = session.scalar(select(JobFeed).where(JobFeed.company_id == career_company_id))
            self.assertIsNotNone(page_feed)
            self.assertEqual(page_feed.feed_url, "https://company-1.example/careers")
            self.assertGreater(session.get(models.ConfiguredLocation, location_id).next_run_at.replace(tzinfo=now.tzinfo), now)

    def test_recurring_jobs_cannot_be_claimed_for_discovery(self):
        with self.factory.begin() as session:
            company = Company(source="test", source_id="recurring", name="Known company",
                              website_url="https://known.example/", latitude=49.0, longitude=8.4)
            session.add(company)
            session.flush()
            for stage in ("company_homepage_discovery", "career_page_discovery"):
                job = models.DiscoveryJob(kind="recurring", label="Area", latitude=49.0,
                    longitude=8.4, radius_km=15, status="queued", stage=stage, candidate_total=1)
                session.add(job)
                session.flush()
                if stage == "career_page_discovery":
                    session.add(DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                                                   status="queued"))
        self.assertIsNone(worker._claim_location_company_search())
        self.assertIsNone(worker._claim_campaign_company())

    def test_expired_recurring_campaign_refreshes_sources_instead_of_retrying_discovery(self):
        now = utcnow()
        with self.factory.begin() as session:
            company = Company(source="test", source_id="expired-recurring", name="Known company",
                website_url="https://known.example/", latitude=49.0, longitude=8.4,
                discovery_lease_until=now - timedelta(minutes=1))
            session.add(company)
            session.flush()
            run = DiscoveryRun(company_id=company.id, status="running", started_at=now - timedelta(hours=1))
            job = models.DiscoveryJob(kind="recurring", label="Area", latitude=49.0,
                longitude=8.4, radius_km=15, status="running", stage="career_page_discovery",
                candidate_total=1, started_at=now - timedelta(hours=1))
            session.add_all([run, job])
            session.flush()
            session.add(DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                discovery_run_id=run.id, status="running", started_at=now - timedelta(hours=1)))
            session.add(JobFeed(company_id=company.id, provider="greenhouse", status="parsed",
                feed_url="https://boards-api.greenhouse.io/v1/boards/known/jobs", next_scan_at=now + timedelta(days=1)))
            job_id, company_id, run_id = job.id, company.id, run.id
        with patch.object(worker, "discover", side_effect=AssertionError("Discovery restarted")):
            self.assertFalse(worker.process_once(location_jobs_only=True))
        with self.factory() as session:
            self.assertEqual(session.get(models.DiscoveryJob, job_id).status, "completed")
            self.assertIsNone(session.get(Company, company_id).discovery_lease_until)
            self.assertEqual(session.get(DiscoveryRun, run_id).status, "cancelled")
            self.assertEqual(session.scalar(select(DiscoveryJobCompany)).status, "cancelled")
            self.assertLess(session.scalar(select(JobFeed)).next_scan_at.replace(tzinfo=now.tzinfo), utcnow())

    def test_live_recurring_location_lease_is_not_cancelled_during_upgrade(self):
        now = utcnow()
        with self.factory.begin() as session:
            job = models.DiscoveryJob(kind="recurring", label="Area", latitude=49.0,
                longitude=8.4, radius_km=15, status="running", stage="company_homepage_discovery",
                started_at=now, location_scan_lease_until=now + timedelta(minutes=10),
                location_scan_token="live-attempt")
            session.add(job)
            session.flush()
            job_id = job.id
        worker._refresh_queued_recurring_jobs(now)
        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "running")
            self.assertEqual(job.location_scan_token, "live-attempt")
            self.assertIsNone(job.finished_at)

    def test_recurring_refresh_preserves_failed_feed_backoff(self):
        now = utcnow()
        retry_at = now + timedelta(hours=2)
        with self.factory.begin() as session:
            company = Company(source="test", source_id="retry", name="Known company",
                              website_url="https://known.example/", latitude=49.0, longitude=8.4)
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider="greenhouse",
                feed_url="https://boards-api.greenhouse.io/v1/boards/known/jobs", status="throttled",
                attempt_count=2, next_scan_at=retry_at)
            location = models.ConfiguredLocation(label="Area", latitude=49.0, longitude=8.4,
                radius_km=15, enabled=True, next_run_at=now - timedelta(minutes=1))
            session.add_all([feed, location])
            session.flush()
            feed_id = feed.id
        worker.schedule_due_locations(now=now)
        with self.factory() as session:
            feed = session.get(JobFeed, feed_id)
            self.assertEqual(feed.next_scan_at.replace(tzinfo=now.tzinfo), retry_at)
            self.assertEqual(feed.attempt_count, 2)
            self.assertEqual(feed.status, "throttled")

    def test_due_empty_scheduled_job_finishes_without_waiting_for_a_company_task(self):
        now = utcnow()
        with self.factory.begin() as session:
            job = models.DiscoveryJob(label="Empty area", latitude=49.0, longitude=8.4,
                                      radius_km=15, status="scheduled",
                                      scheduled_for=now - timedelta(minutes=1), candidate_total=0,
                                      created_at=now - timedelta(days=1))
            session.add(job)
            session.flush()
            job_id = job.id

        self.assertEqual(worker._complete_due_empty_jobs(now=now), 1)

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.processed_count, 0)
            self.assertIsNotNone(job.finished_at)

    def test_expired_worker_cannot_finish_a_campaign_item_reclaimed_by_a_new_run(self):
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/reclaimed", name="Reclaimed GmbH",
                              website_url="https://reclaimed.example/", latitude=49.0, longitude=8.4,
                              career_status="not_checked")
            session.add(company)
            session.flush()
            job = models.DiscoveryJob(label="Reclaimed area", latitude=49.0, longitude=8.4,
                                      radius_km=5, status="running", candidate_total=1,
                                      created_at=utcnow())
            session.add(job)
            session.flush()
            old_run = DiscoveryRun(company_id=company.id, status="failed", started_at=utcnow())
            current_run = DiscoveryRun(company_id=company.id, status="running", started_at=utcnow())
            session.add_all([old_run, current_run])
            session.flush()
            item = models.DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                                              discovery_run_id=current_run.id, status="running",
                                              started_at=utcnow())
            session.add(item)
            session.flush()
            item_id, stale_run_id, job_id = item.id, old_run.id, job.id

        worker._finish_campaign_company(item_id, stale_run_id, jobs_found=7)

        with self.factory() as session:
            job = session.get(models.DiscoveryJob, job_id)
            item = session.get(models.DiscoveryJobCompany, item_id)
            self.assertEqual(job.status, "running")
            self.assertEqual(job.processed_count, 0)
            self.assertEqual(job.jobs_found, 0)
            self.assertEqual(item.status, "running")

    def test_expired_campaign_item_is_locked_requeued_and_claimed_under_a_new_run(self):
        now = utcnow()
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id="node/stale", name="Stale GmbH",
                              website_url="https://stale.example/", latitude=49.0, longitude=8.4,
                              career_status="not_checked", discovery_lease_until=now - timedelta(minutes=1))
            job = models.DiscoveryJob(label="Stale area", latitude=49.0, longitude=8.4,
                radius_km=5, status="running", stage="career_page_discovery", candidate_total=1,
                created_at=now - timedelta(hours=1))
            session.add_all([company, job])
            session.flush()
            old_run = DiscoveryRun(company_id=company.id, status="running",
                                   started_at=now - timedelta(minutes=worker.LEASE_MINUTES + 1))
            session.add(old_run)
            session.flush()
            item = models.DiscoveryJobCompany(discovery_job_id=job.id, company_id=company.id,
                discovery_run_id=old_run.id, status="running",
                started_at=now - timedelta(minutes=worker.LEASE_MINUTES + 1))
            session.add(item)
            session.flush()
            company_id, item_id, job_id, old_run_id = company.id, item.id, job.id, old_run.id

        claimed = worker._claim_campaign_company(now=now)

        self.assertIsNotNone(claimed)
        self.assertEqual((claimed[0], claimed[2], claimed[3]), (company_id, item_id, job_id))
        with self.factory() as session:
            old_run = session.get(DiscoveryRun, old_run_id)
            item = session.get(models.DiscoveryJobCompany, item_id)
            self.assertEqual(old_run.status, "failed")
            self.assertEqual(item.status, "running")
            self.assertEqual(item.discovery_run_id, claimed[1])
            self.assertNotEqual(item.discovery_run_id, old_run_id)


if __name__ == "__main__":
    unittest.main()
