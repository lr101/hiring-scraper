import json
import tempfile
import threading
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from hiring_scraper.app import discovery_worker as worker
from hiring_scraper.app import worker as feed_worker
from hiring_scraper.browser import BROWSER_CLIENT_REQUEST_BUDGET
from hiring_scraper.app.models import Base, Company, DiscoveryRun, Job, JobFeed, ScanRun, utcnow


class DiscoveryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'worker.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.patches = [
            patch.object(worker, "SessionLocal", self.factory),
            patch.object(worker, "engine", self.engine),
            patch.object(feed_worker, "SessionLocal", self.factory),
            patch.object(feed_worker, "engine", self.engine),
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

    def test_shared_website_domain_has_one_global_career_page_schedule(self):
        due = utcnow() - timedelta(minutes=1)
        first = self._company(source_id="node/first-office", domain="shared.example",
                              next_discovery_at=due, crawl_priority=30)
        second = self._company(source_id="node/second-office", domain="www.shared.example",
                               next_discovery_at=due, crawl_priority=100)
        claim = worker._claim_due_company()
        self.assertEqual(claim[0], first,
                         "A more relevant branch must not create a second page crawl for the same domain")
        self.assertIsNone(worker._claim_due_company())

    def test_due_company_uses_german_employer_instead_of_an_older_foreign_job_board(self):
        due = utcnow() - timedelta(minutes=1)
        board = self._company(source="imported", source_id="old-austrian-board", name="Austrian job board",
                              country_code="AT", category="job_board_source", domain="shared-board.example",
                              crawl_interval_days=1, next_discovery_at=due)
        german = self._company(source_id="german-employer", name="German employer", country_code="DE",
                               domain="www.shared-board.example", crawl_interval_days=7,
                               next_discovery_at=due)

        claim = worker._claim_due_company()

        self.assertIsNotNone(claim)
        self.assertEqual(claim[0], german)
        self.assertNotEqual(claim[0], board)

    def test_active_german_alias_is_claimed_when_an_older_alias_is_paused(self):
        due = utcnow() - timedelta(minutes=1)
        self._company(source_id="paused-german-alias", name="Paused alias", country_code="DE",
                      domain="shared-paused.example", crawl_paused=True, next_discovery_at=due)
        active = self._company(source_id="active-german-alias", name="Active alias", country_code="DE",
                               domain="www.shared-paused.example", next_discovery_at=due)

        claim = worker._claim_due_company()

        self.assertIsNotNone(claim)
        self.assertEqual(claim[0], active)

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

    def test_known_feed_and_homepage_have_independent_ranked_refreshes(self):
        company_id = self._company(career_status="jobs_feed_found")
        with self.factory.begin() as session:
            session.add(JobFeed(company_id=company_id, provider="greenhouse",
                                feed_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                                status="failed", next_scan_at=utcnow()))
        claimed=worker._claim_due_company()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0],company_id)

    def test_existing_career_page_is_registered_for_direct_refresh(self):
        company_id = self._company(career_status="career_page_found",
                                   career_url="https://acme.example/careers",
                                   next_discovery_at=utcnow()+timedelta(days=1))
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
                                   career_url="https://acme.example/careers",
                                   next_discovery_at=utcnow()+timedelta(days=1))
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

    def test_shared_scoped_personio_feed_persists_aggregate_job_count(self):
        company_id = self._company()
        with self.factory.begin() as session:
            run = DiscoveryRun(company_id=company_id, status="running", started_at=utcnow())
            session.add(run)
            session.flush()
            run_id = run.id

        feed_url = "https://group.jobs.personio.de/xml"
        boards = []
        for job_id in ("42", "43", "44"):
            boards.append({
                "provider": "personio", "tenant": "group",
                "board_url": f"https://group.jobs.personio.de/job/{job_id}",
                "feed_url": feed_url, "feed_state": "parsed", "complete": False,
                "jobs": [{"id": job_id, "title": f"Engineer {job_id}",
                          "url": f"https://group.jobs.personio.de/job/{job_id}",
                          "raw_metadata": {"personio_posting_scope": job_id}}],
            })

        self.assertEqual(worker._persist_discovery(
            company_id, run_id, {"status": "jobs_feed_found", "pages": [], "boards": boards}), 3)

        with self.factory() as session:
            feed = session.scalar(select(JobFeed).where(JobFeed.company_id == company_id))
            jobs = session.scalars(select(Job).where(Job.feed_id == feed.id)).all()
            self.assertEqual(feed.job_count, 3)
            self.assertEqual({job.external_id for job in jobs}, {"42", "43", "44"})
            self.assertEqual(feed.status, "incomplete")

    def test_overlapping_boards_count_each_persisted_source_posting_once(self):
        company_id = self._company()
        _, run_id = worker._claim_due_company()
        feed_url = "https://group.jobs.personio.de/xml"
        def board(ids):
            return {"provider": "personio", "tenant": "group", "feed_url": feed_url,
                    "feed_state": "parsed", "complete": False,
                    "jobs": [{"id": key, "title": "Engineer " + key,
                              "url": "https://group.jobs.personio.de/job/" + key}
                             for key in ids]}
        result = {"status": "jobs_feed_found", "pages": [],
                  "boards": [board(["42", "43"]), board(["43", "44"])]}

        count = worker._persist_discovery(company_id, run_id, result)

        with self.factory() as session:
            self.assertEqual(len(session.scalars(select(Job)).all()), 3)
            self.assertEqual(session.scalar(select(JobFeed)).job_count, 3)
            self.assertEqual(count, 3)
            self.assertEqual(session.get(DiscoveryRun, run_id).jobs_found, 3)

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


class FeedWorkerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'feed-worker.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.patches = [patch.object(feed_worker, "SessionLocal", self.factory),
                        patch.object(feed_worker, "engine", self.engine)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def _feed_and_run(self, *, revision=1):
        with self.factory.begin() as session:
            company = Company(source="managed_feed", source_id="source-1", name="Acme GmbH",
                              website_url="https://acme.example", domain="acme.example")
            session.add(company)
            session.flush()
            feed = JobFeed(company_id=company.id, provider="rss", feed_url="https://acme.example/jobs.xml",
                           board_url="https://acme.example/careers", status="pending",
                           source_revision=revision, next_scan_at=utcnow(), lease_until=utcnow())
            session.add(feed)
            session.flush()
            run = ScanRun(feed_id=feed.id, started_at=utcnow(), status="running")
            session.add(run)
            session.flush()
            return feed.id, run.id

    def test_browser_feed_client_budget_covers_the_rendered_request_limit(self):
        feed = JobFeed(provider="browser_html", feed_url="https://acme.example/careers")
        with patch.object(feed_worker, "Client") as client_factory, \
                patch.object(feed_worker, "fetch_feed", return_value=({}, b"")):
            feed_worker._request(feed)
        self.assertEqual(client_factory.call_args.kwargs["max_requests"], BROWSER_CLIENT_REQUEST_BUDGET)
        self.assertGreater(BROWSER_CLIENT_REQUEST_BUDGET, 8)

    def test_source_revision_discards_a_stale_response(self):
        feed_id, run_id = self._feed_and_run(revision=2)
        complete_result = {"state": "ok", "status": 200, "parsed_feed": {
            "complete": True, "jobs": [{"id": "new", "title": "New role",
                                         "url": "https://acme.example/jobs/new"}]}}

        feed_worker._persist_result(feed_id, run_id, complete_result, b"", source_revision=1)

        with self.factory() as session:
            feed = session.get(JobFeed, feed_id)
            run = session.get(ScanRun, run_id)
            self.assertEqual(feed.source_revision, 2)
            self.assertEqual(feed.status, "pending")
            self.assertIsNone(feed.lease_until)
            self.assertEqual(session.scalars(select(Job)).all(), [])
            self.assertEqual(run.status, "stale_source")

    def test_feed_edit_lock_serializes_with_in_flight_result_persistence(self):
        feed_id, run_id = self._feed_and_run(revision=1)
        api_session = self.factory()
        api_session.execute(update(JobFeed).where(JobFeed.id == feed_id).values(
            source_revision=JobFeed.source_revision).execution_options(synchronize_session=False))
        worker_update_started = threading.Event()
        worker_errors = []

        def observe_worker_update(_connection, _cursor, statement, _parameters, _context, _executemany):
            if (threading.current_thread().name == "feed-result-persist" and
                    statement.lstrip().upper().startswith("UPDATE JOB_FEEDS")):
                worker_update_started.set()

        event.listen(self.engine, "before_cursor_execute", observe_worker_update)
        metadata = {"state": "ok", "status": 200, "parsed_feed": {
            "complete": True, "jobs": [{"id": "stale", "title": "Stale role",
                                         "url": "https://acme.example/jobs/stale"}]}}

        def persist_old_response():
            try:
                feed_worker._persist_result(feed_id, run_id, metadata, b"", source_revision=1)
            except Exception as error:  # surfaced in the owning test thread below
                worker_errors.append(error)

        thread = threading.Thread(target=persist_old_response, name="feed-result-persist")
        thread.start()
        try:
            self.assertTrue(worker_update_started.wait(timeout=2))
            self.assertTrue(thread.is_alive(), "the result writer should wait for the feed edit lock")
            feed = api_session.get(JobFeed, feed_id)
            feed.source_revision = 2
            feed.feed_url = "https://acme.example/new-jobs.xml"
            api_session.commit()
        finally:
            api_session.close()
            event.remove(self.engine, "before_cursor_execute", observe_worker_update)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "the result writer should finish after the edit commits")
        self.assertEqual(worker_errors, [])
        with self.factory() as session:
            feed = session.get(JobFeed, feed_id)
            run = session.get(ScanRun, run_id)
            self.assertEqual(feed.source_revision, 2)
            self.assertEqual(feed.feed_url, "https://acme.example/new-jobs.xml")
            self.assertEqual(run.status, "stale_source")
            self.assertEqual(session.scalars(select(Job).where(Job.external_id == "stale")).all(), [])

    def test_rss_window_scan_succeeds_without_closing_missing_jobs(self):
        feed_id, run_id = self._feed_and_run()
        old_seen = utcnow() - timedelta(days=30)
        with self.factory.begin() as session:
            old_job = Job(feed_id=feed_id, external_id="old", title="Old role",
                          url="https://acme.example/jobs/old", first_seen_at=old_seen,
                          last_seen_at=old_seen, missing_complete_scans=2, is_active=True)
            session.add(old_job)

        metadata = {"state": "ok", "status": 200, "parsed_feed": {
            "complete": False, "windowed": True,
            "jobs": [{"id": "recent", "title": "Recent role",
                      "url": "https://acme.example/jobs/recent"}]}}
        feed_worker._persist_result(feed_id, run_id, metadata, b"", source_revision=1)

        with self.factory() as session:
            feed = session.get(JobFeed, feed_id)
            old_job = session.scalar(select(Job).where(Job.external_id == "old"))
            run = session.get(ScanRun, run_id)
            self.assertEqual(feed.status, "windowed")
            self.assertIsNone(feed.last_error)
            next_scan = feed.next_scan_at
            if next_scan.tzinfo is None:
                next_scan = next_scan.replace(tzinfo=timezone.utc)
            self.assertGreater(next_scan, utcnow())
            self.assertTrue(old_job.is_active)
            self.assertEqual(old_job.missing_complete_scans, 2)
            self.assertEqual(run.status, "windowed")

    def test_journey_save_racing_with_closure_cannot_restore_new_stage(self):
        from hiring_scraper.app import api
        from hiring_scraper.app.models import JobApplication, UserProfile
        feed_id, run_id = self._feed_and_run()
        with self.factory.begin() as session:
            profile = UserProfile(name='Concurrent closure', preferences={})
            job = Job(feed_id=feed_id, external_id='closing', title='Closing role', url='https://acme.example/jobs/closing')
            session.add_all([profile, job])
            session.flush()
            profile_id, job_id = profile.id, job.id
        reconciled = threading.Event()
        tracking_write = threading.Event()
        release = threading.Event()
        errors = []

        def before_sql(connection, cursor, statement, parameters, context, executemany):
            if threading.current_thread().name == 'journey-save' and (
                    statement.startswith('UPDATE jobs SET') or statement.startswith('INSERT INTO job_applications')):
                tracking_write.set()

        def after_sql(connection, cursor, statement, parameters, context, executemany):
            if threading.current_thread().name == 'source-close' and statement.startswith('UPDATE job_applications SET'):
                reconciled.set()
                if not release.wait(10):
                    raise TimeoutError('closure test was not released')

        def close_source():
            try:
                feed_worker._persist_result(feed_id, run_id, {'state': 'ok', 'status': 200,
                    'parsed_feed': {'jobs': [], 'complete': True}}, b'')
            except Exception as error:
                errors.append(error)

        def track():
            try:
                with self.factory() as session:
                    api.set_job_application(profile_id, job_id, api.ApplicationRequest(status='new'), session)
            except Exception as error:
                errors.append(error)

        event.listen(self.engine, 'before_cursor_execute', before_sql)
        event.listen(self.engine, 'after_cursor_execute', after_sql)
        closer = threading.Thread(target=close_source, name='source-close')
        tracker = threading.Thread(target=track, name='journey-save')
        try:
            closer.start()
            self.assertTrue(reconciled.wait(10))
            tracker.start()
            self.assertTrue(tracking_write.wait(10))
        finally:
            release.set()
            closer.join(10)
            if tracker.ident:
                tracker.join(10)
            event.remove(self.engine, 'before_cursor_execute', before_sql)
            event.remove(self.engine, 'after_cursor_execute', after_sql)
        self.assertFalse(closer.is_alive())
        self.assertFalse(tracker.is_alive())
        self.assertEqual(errors, [])
        with self.factory() as session:
            self.assertFalse(session.get(Job, job_id).is_active)
            self.assertEqual(session.scalar(select(JobApplication.status).where(JobApplication.job_id == job_id)), 'closed')


if __name__ == "__main__":
    unittest.main()
