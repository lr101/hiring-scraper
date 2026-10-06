"""Idempotently import the compact, dated Karlsruhe research snapshot."""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select

from hiring_scraper.app.database import IS_SQLITE, SessionLocal, engine
from hiring_scraper.app.models import Base, Company, Job, JobFeed, JobLocation, LocationCache


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
KARLSRUHE = {"label": "Karlsruhe, Baden-Württemberg, Deutschland", "city": "Karlsruhe",
             "state": "Baden-Württemberg", "latitude": 49.0068705, "longitude": 8.4034195,
             "precision": "city_centroid", "country_code": "de", "type": "city"}
DISCOVERY_INTERVAL_DAYS = max(1, int(os.getenv("CAREER_DISCOVERY_INTERVAL_DAYS", "30")))


def _host(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").casefold().strip(".")
    return host.removeprefix("www.") or None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _job_locations(job: dict) -> list[dict]:
    locations = job.get("locations")
    if not locations:
        label = (job.get("location") or "").strip()
        locations = [{"label": label}] if label else []
    normalized = []
    for location in locations:
        if isinstance(location, str):
            location = {"label": location}
        if not isinstance(location, dict) or not location.get("label"):
            continue
        label = str(location["label"]).strip()
        lat, lon, precision, country = location.get("latitude"), location.get("longitude"), location.get("precision"), location.get("country_code")
        locality = label.casefold().strip()
        city_key = locality.split(" - ", 1)[0].split(",", 1)[0].strip()
        if city_key == "karlsruhe":
            lat, lon, precision, country = 49.0068705, 8.4034195, "city_centroid", "de"
        elif city_key == "berlin":
            lat, lon, precision, country = 52.5200, 13.4050, "city_centroid", "de"
        if locality == "remote":
            precision = "remote_scope_unspecified"
        normalized.append({"label": label, "latitude": lat, "longitude": lon,
                           "precision": precision, "country_code": country})
    return normalized


def import_fixture(company_path: Path, career_path: Path) -> dict[str, int]:
    if IS_SQLITE:
        Base.metadata.create_all(engine)
    with company_path.open(newline="", encoding="utf-8") as file:
        candidates = list(csv.DictReader(file))
    enrichment = json.loads(career_path.read_text(encoding="utf-8"))
    observed_at = _datetime(enrichment.get("observed_at")) or datetime(2026, 10, 5, tzinfo=timezone.utc)
    career_by_key = {item["source_id"]: item for item in enrichment.get("companies", [])}

    with SessionLocal.begin() as session:
        companies = {(item.source, item.source_id): item for item in session.scalars(select(Company)).all()}
        by_source_id = {item.source_id: item for item in companies.values()}
        scheduled_now = datetime.now(timezone.utc)
        for candidate in candidates:
            source_id = f"{candidate['osm_type']}/{candidate['osm_id']}"
            company = companies.get(("openstreetmap", source_id))
            extra = career_by_key.get(source_id, {})
            resolution = extra.get("website_resolution") or {}
            osm_website = (candidate.get("website") or "").strip() or None
            curated_website = extra.get("website_url")
            osm_domain, curated_domain = _host(osm_website), _host(curated_website)
            same_domain_canonical = bool(osm_domain and curated_domain and osm_domain == curated_domain)
            website_url = curated_website if not osm_website or same_domain_canonical else osm_website
            evidence_urls = resolution.get("evidence_urls") or []
            domain_match_method = "osm_website_tag" if osm_website else resolution.get("method")
            domain_evidence_url = ((candidate.get("source_url") or None) if osm_website else
                                   evidence_urls[0] if evidence_urls else None)
            checked_at = _datetime(extra.get("last_checked_at"))
            if checked_at is None and extra and extra.get("career_status", "not_checked") != "not_checked":
                checked_at = observed_at
            fields = {
                "name": candidate["name"], "website_url": website_url,
                "domain": _host(website_url),
                "domain_match_method": domain_match_method,
                "domain_evidence_url": domain_evidence_url,
                "category": candidate.get("category") or None,
                "source_url": candidate.get("source_url") or None,
                "latitude": float(candidate["lat"]), "longitude": float(candidate["lon"]),
                "location_precision": "point" if candidate["osm_type"] == "node" else "feature_center",
                "location_label": candidate.get("category") or "Mapped establishment",
                "career_url": extra.get("career_url"), "career_status": extra.get("career_status", "not_checked"),
                "last_checked_at": checked_at,
            }
            if company is None:
                newly_discovered_website = bool(website_url and not osm_website)
                fields["next_discovery_at"] = (scheduled_now if newly_discovered_website else
                                                max(scheduled_now, observed_at + timedelta(days=DISCOVERY_INTERVAL_DAYS))
                                                if extra else scheduled_now)
                company = Company(source="openstreetmap", source_id=source_id, **fields)
                session.add(company)
                companies[("openstreetmap", source_id)] = company
            else:
                newly_discovered_website = not company.website_url and bool(website_url)
                if newly_discovered_website:
                    fields["next_discovery_at"] = scheduled_now
                elif company.next_discovery_at is None:
                    fields["next_discovery_at"] = (
                        max(scheduled_now, observed_at + timedelta(days=DISCOVERY_INTERVAL_DAYS))
                        if extra else scheduled_now)
                snapshot_is_newer = (checked_at is not None and
                                     (_as_utc(company.last_checked_at) is None or
                                      _as_utc(company.last_checked_at) <= checked_at))
                for key, value in fields.items():
                    if key in {"career_url", "career_status", "last_checked_at"} and not snapshot_is_newer:
                        continue
                    setattr(company, key, value)
            by_source_id[source_id] = company
        session.flush()

        feeds = {(feed.company_id, feed.provider, feed.feed_url): feed
                 for feed in session.scalars(select(JobFeed)).all()}
        for source_id, extra in career_by_key.items():
            company = by_source_id.get(source_id)
            if not company:
                continue
            for feed_data in extra.get("feeds", []):
                key = (company.id, feed_data["provider"], feed_data["feed_url"])
                feed_observed_at = _datetime(feed_data.get("last_checked_at")) or observed_at
                feed = feeds.get(key)
                if feed is None:
                    feed = JobFeed(company_id=company.id, provider=feed_data["provider"],
                                   tenant=feed_data.get("tenant"), board_url=feed_data.get("board_url"),
                                   feed_url=feed_data["feed_url"], status=feed_data["status"],
                                   job_count=feed_data.get("job_count"), last_checked_at=feed_observed_at,
                                   next_scan_at=datetime.now(timezone.utc))
                    session.add(feed)
                    feeds[key] = feed
                else:
                    if (_as_utc(feed.last_checked_at) is not None and
                            _as_utc(feed.last_checked_at) > feed_observed_at):
                        continue
                    feed.tenant = feed_data.get("tenant")
                    feed.board_url = feed_data.get("board_url")
                    feed.status = feed_data["status"]
                    feed.job_count = feed_data.get("job_count")
                    feed.last_checked_at = feed_observed_at
                    if feed.next_scan_at is None:
                        feed.next_scan_at = datetime.now(timezone.utc)
                session.flush()
                jobs = {(job.feed_id, job.external_id): job for job in session.scalars(
                    select(Job).where(Job.feed_id == feed.id)).all()}
                jobs_observed_at = feed_observed_at
                for job_data in feed_data.get("jobs", []):
                    external_id = str(job_data["id"])
                    job = jobs.get((feed.id, external_id))
                    if (job is not None and _as_utc(job.last_seen_at) is not None and
                            _as_utc(job.last_seen_at) > feed_observed_at):
                        continue
                    if job is None:
                        job = Job(feed_id=feed.id, external_id=external_id,
                                  title=job_data["title"], url=job_data["url"],
                                  first_seen_at=jobs_observed_at, last_seen_at=jobs_observed_at)
                        session.add(job)
                    job.title = job_data["title"]
                    job.url = job_data["url"]
                    job.description = job_data.get("description")
                    job.location_text = job_data.get("location") or None
                    job.is_remote = bool(job_data.get("is_remote")) or (job.location_text or "").casefold().strip() == "remote"
                    job.work_arrangement = job_data.get("work_arrangement") or ("remote" if job.is_remote else None)
                    job.employment_type = job_data.get("employment_type")
                    job.schedule = job_data.get("schedule")
                    job.department = job_data.get("department")
                    job.seniority = job_data.get("seniority")
                    job.date_posted = job_data.get("date_posted")
                    job.salary = job_data.get("salary")
                    job.raw_metadata = job_data.get("raw_metadata", {})
                    job.last_seen_at = jobs_observed_at
                    job.is_active = True
                    session.flush()
                    for old_location in list(job.locations):
                        session.delete(old_location)
                    for location in _job_locations(job_data):
                        session.add(JobLocation(job_id=job.id, label=location["label"],
                                                latitude=location.get("latitude"),
                                                longitude=location.get("longitude"),
                                                precision=location.get("precision"),
                                                country_code=location.get("country_code")))

        if session.scalar(select(LocationCache).where(LocationCache.query_key == "karlsruhe")) is None:
            session.add(LocationCache(query_key="karlsruhe", results=[KARLSRUHE], cached_at=observed_at))
        session.flush()
        company_count = session.scalar(select(func.count()).select_from(Company))
        feed_count = session.scalar(select(func.count()).select_from(JobFeed))
        job_count = session.scalar(select(func.count()).select_from(Job))
    return {"companies": company_count or 0, "feeds": feed_count or 0, "jobs": job_count or 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", type=Path, default=FIXTURES / "karlsruhe-osm-candidates.csv")
    parser.add_argument("--careers", type=Path, default=FIXTURES / "karlsruhe-career-enrichment.json")
    args = parser.parse_args()
    print(json.dumps(import_fixture(args.companies, args.careers), ensure_ascii=False))


if __name__ == "__main__":
    main()
