import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from starlette.middleware.cors import CORSMiddleware

from hiring_scraper.app import api
from hiring_scraper.app.models import Base, Company, DiscoveryJob, DiscoveryJobCompany, utcnow


class DiscoveryJobsApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'api.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

        self.previous_sqlite = api.IS_SQLITE
        api.IS_SQLITE = True

    def tearDown(self):
        api.IS_SQLITE = self.previous_sqlite
        self.engine.dispose()
        self.directory.cleanup()

    def _company(self, source_id, name, latitude, longitude, website_url):
        with self.factory.begin() as session:
            company = Company(source="openstreetmap", source_id=source_id, name=name,
                              latitude=latitude, longitude=longitude, website_url=website_url,
                              career_status="not_checked")
            session.add(company)
            session.flush()
            return company.id

    def _call_endpoint(self, name, payload=None, **query):
        endpoint = getattr(api, name, None)
        self.assertIsNotNone(endpoint, f"The API should expose {name}")
        session = self.factory()
        try:
            request = SimpleNamespace(**(payload or {}))
            if name == "update_configured_location":
                return endpoint(query["location_id"], request, session)
            return endpoint(request, session, **query) if payload is not None else endpoint(session=session, **query)
        finally:
            session.close()

    def test_location_can_be_saved_with_a_recurring_schedule(self):
        location = self._call_endpoint("create_configured_location", {
            "label": "Karlsruhe, Baden-Württemberg, Deutschland",
            "city": "Karlsruhe", "state": "Baden-Württemberg", "postcode": None,
            "latitude": 49.0068705, "longitude": 8.4034195,
            "radius_km": 15, "interval_days": 7,
        })

        self.assertEqual(location["city"], "Karlsruhe")
        self.assertEqual(location["radius_km"], 15)
        self.assertTrue(location["enabled"])
        self.assertIsNotNone(location["next_run_at"])

        jobs = self._call_endpoint("list_discovery_jobs", status=None, limit=50)["items"]
        self.assertEqual(len(jobs), 1, "Saving a location should start its first company and homepage search")
        self.assertEqual(jobs[0]["stage"], "company_homepage_discovery")
        self.assertEqual(jobs[0]["status"], "queued")

        listed = self._call_endpoint("list_configured_locations")
        self.assertEqual([item["id"] for item in listed["items"]], [location["id"]])

    def test_new_location_response_includes_its_first_discovery_job(self):
        location = self._call_endpoint("create_configured_location", {
            "label": "Leipzig", "city": "Leipzig", "latitude": 51.3397,
            "longitude": 12.3731, "radius_km": 10, "interval_days": 30,
        })
        self.assertIn("first_discovery_job", location)
        self.assertEqual(location["first_discovery_job"]["stage"], "company_homepage_discovery")

    def test_queued_or_scheduled_discovery_search_can_be_cancelled(self):
        queued = self._call_endpoint("create_discovery_job", {
            "label": "Karlsruhe", "latitude": 49.0069, "longitude": 8.4034,
            "radius_km": 5,
        })
        cancelled = self._call_endpoint("cancel_discovery_job", discovery_job_id=queued["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["progress_message"], "Search cancelled")
        self.assertIsNotNone(cancelled["finished_at"])
        self.assertIsNotNone(cancelled["progress_updated_at"])
        repeated = self._call_endpoint("cancel_discovery_job", discovery_job_id=queued["id"])
        self.assertEqual(repeated["status"], "cancelled")

        scheduled = self._call_endpoint("create_discovery_job", {
            "label": "Leipzig", "latitude": 51.3397, "longitude": 12.3731,
            "radius_km": 10, "scheduled_for": (utcnow() + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(scheduled["status"], "scheduled")
        cancelled_scheduled = self._call_endpoint("cancel_discovery_job", discovery_job_id=scheduled["id"])
        self.assertEqual(cancelled_scheduled["status"], "cancelled")

        running = self._call_endpoint("create_discovery_job", {
            "label": "Heidelberg", "latitude": 49.3988, "longitude": 8.6724,
            "radius_km": 15,
        })
        session = self.factory()
        try:
            from hiring_scraper.app.models import DiscoveryJob
            stored = session.get(DiscoveryJob, running["id"])
            stored.status = "running"
            stored.location_scan_lease_until = utcnow() + timedelta(minutes=10)
            stored.location_scan_token = "active-attempt"
            session.commit()
        finally:
            session.close()
        cancelled_running = self._call_endpoint("cancel_discovery_job", discovery_job_id=running["id"])
        self.assertEqual(cancelled_running["status"], "cancelled")
        session = self.factory()
        try:
            stored = session.get(DiscoveryJob, running["id"])
            self.assertIsNone(stored.location_scan_lease_until)
            self.assertIsNone(stored.location_scan_token)
        finally:
            session.close()

    def test_finished_discovery_search_cannot_be_cancelled(self):
        job = self._call_endpoint("create_discovery_job", {
            "label": "Karlsruhe", "latitude": 49.0069, "longitude": 8.4034,
            "radius_km": 5,
        })
        session = self.factory()
        try:
            from hiring_scraper.app.models import DiscoveryJob
            stored = session.get(DiscoveryJob, job["id"])
            stored.status = "completed"
            session.commit()
        finally:
            session.close()
        with self.assertRaises(HTTPException) as caught:
            self._call_endpoint("cancel_discovery_job", discovery_job_id=job["id"])
        self.assertEqual(caught.exception.status_code, 409)

    def test_cancelling_search_releases_its_waiting_company_checks(self):
        company_id = self._company("node/cancel-queued", "Waiting GmbH", 49.0, 8.4,
                                   "https://waiting.example/")
        with self.factory.begin() as session:
            job = DiscoveryJob(label="Waiting area", latitude=49.0, longitude=8.4,
                radius_km=5, status="queued", stage="career_page_discovery",
                candidate_total=1, created_at=utcnow())
            session.add(job)
            session.flush()
            item = DiscoveryJobCompany(discovery_job_id=job.id, company_id=company_id, status="queued")
            session.add(item)
            session.flush()
            job_id, item_id = job.id, item.id

        cancelled = self._call_endpoint("cancel_discovery_job", discovery_job_id=job_id)
        self.assertEqual(cancelled["status"], "cancelled")
        with self.factory() as session:
            self.assertEqual(session.get(DiscoveryJobCompany, item_id).status, "cancelled")

    def test_location_schedule_can_be_paused_and_removed_without_deleting_crawl_history(self):
        location = self._call_endpoint("create_configured_location", {
            "label": "Mannheim", "city": "Mannheim", "state": "Baden-Württemberg",
            "latitude": 49.4891, "longitude": 8.4660, "radius_km": 10, "interval_days": 14,
        })
        job = self._call_endpoint("create_discovery_job", {"location_id": location["id"]})
        paused = self._call_endpoint("update_configured_location", {"enabled": False},
                                     location_id=location["id"])
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["next_run_at"], location["next_run_at"])

        removed = self._call_endpoint("delete_configured_location", location_id=location["id"])
        self.assertTrue(removed["deleted"])
        listed = self._call_endpoint("list_configured_locations")
        self.assertEqual(listed["items"], [])
        history = self._call_endpoint("get_discovery_job", discovery_job_id=job["id"])
        self.assertIsNone(history["configured_location_id"])
        self.assertEqual(history["label"], "Mannheim")

    def test_cross_origin_location_management_allows_patch_and_delete_preflights(self):
        cors = next(middleware for middleware in api.app.user_middleware
                    if middleware.cls is CORSMiddleware)
        self.assertIn("PATCH", cors.kwargs["allow_methods"])
        self.assertIn("DELETE", cors.kwargs["allow_methods"])

    def test_location_job_starts_with_company_and_homepage_search(self):
        self._company("node/near", "Near GmbH", 49.0069, 8.4034, "https://near.example/")
        self._company("node/no-site", "No Site GmbH", 49.0069, 8.4034, None)
        self._company("node/far", "Far GmbH", 49.2, 8.4034, "https://far.example/")

        job = self._call_endpoint("create_discovery_job", {
            "label": "Karlsruhe",
            "latitude": 49.0068705, "longitude": 8.4034195,
            "radius_km": 15,
        })

        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["candidate_total"], 0)
        self.assertEqual(job["processed_count"], 0)
        self.assertEqual(job["progress_percent"], 0)
        self.assertEqual(job["stage"], "company_homepage_discovery")

        listed = self._call_endpoint("list_discovery_jobs", status=None, limit=50)["items"]
        self.assertEqual([item["id"] for item in listed], [job["id"]])
        detail = self._call_endpoint("get_discovery_job", discovery_job_id=job["id"])
        self.assertEqual(detail["candidate_total"], 0)
        self.assertEqual(detail["stage"], "company_homepage_discovery")

    def test_future_discovery_job_is_scheduled_until_its_start_time(self):
        scheduled_for = (utcnow() + timedelta(days=2)).isoformat()
        job = self._call_endpoint("create_discovery_job", {
            "label": "Berlin", "latitude": 52.52, "longitude": 13.405,
            "radius_km": 25, "scheduled_for": scheduled_for,
        })

        self.assertEqual(job["status"], "scheduled")
        self.assertIsNotNone(job["scheduled_for"])


if __name__ == "__main__":
    unittest.main()
