"""Load maintained public RSS sources into the default feed scanner."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from hiring_scraper.app.database import IS_SQLITE, SessionLocal, engine, initialize_sqlite_schema
from hiring_scraper.app.models import Company, JobFeed, utcnow


CONFIG_PATH = Path(__file__).with_name("default_job_feeds.json")


def _configured_feeds() -> list[dict]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    feeds = config.get("feeds") if isinstance(config, dict) else None
    if not isinstance(feeds, list):
        raise ValueError("Default job feed config must contain a feeds list")
    seen_ids, seen_urls = set(), set()
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ValueError("Each default job feed must be an object")
        feed_id, feed_url = feed.get("id"), feed.get("feed_url")
        page_url, website_url = feed.get("source_page_url"), feed.get("website_url")
        if (not isinstance(feed_id, str) or not feed_id or len(feed_id) > 100 or feed_id in seen_ids
                or not isinstance(feed.get("name"), str) or not feed["name"].strip() or len(feed["name"]) > 500
                or feed.get("provider") != "rss" or not isinstance(feed_url, str)
                or not isinstance(page_url, str) or not isinstance(website_url, str)):
            raise ValueError("Default job feed entries need unique IDs, RSS provider and source URLs")
        parsed_feed, parsed_page, parsed_site = map(urlsplit, (feed_url, page_url, website_url))
        if any(parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
               for parsed in (parsed_feed, parsed_page, parsed_site)):
            raise ValueError(f"Default job feed {feed_id} must use public HTTPS URLs")
        if feed_url in seen_urls:
            raise ValueError(f"Duplicate default job feed URL: {feed_url}")
        latitude, longitude = feed.get("latitude"), feed.get("longitude")
        if (latitude is None) != (longitude is None):
            raise ValueError(f"Default job feed {feed_id} must provide both location coordinates or neither")
        seen_ids.add(feed_id)
        seen_urls.add(feed_url)
    return feeds


def _ensure_default_job_feeds(session: Session, now) -> int:
    created = 0
    for item in _configured_feeds():
        company = session.scalar(select(Company).where(
            Company.source == "default_job_feed", Company.source_id == item["id"]))
        website_host = (urlsplit(item["website_url"]).hostname or "").casefold()
        company_fields = {
            "name": item["name"], "website_url": item["website_url"], "domain": website_host,
            "domain_match_method": "configured_job_source",
            "domain_evidence_url": item["source_page_url"],
            "category": item.get("category"), "source_url": item["source_page_url"],
            "latitude": item.get("latitude"), "longitude": item.get("longitude"),
            "location_precision": item.get("location_precision"),
            "location_label": item.get("location_label"),
            "career_url": item["source_page_url"], "career_status": "jobs_feed_found",
        }
        if item.get("category") == "public_employer":
            company_fields["country_code"] = "DE"
        if company is None:
            company = Company(source="default_job_feed", source_id=item["id"], **company_fields)
            session.add(company)
            session.flush()
        else:
            if company.domain_match_method == "manual":
                company_fields.pop("domain", None)
                company_fields.pop("domain_match_method", None)
                company_fields.pop("domain_evidence_url", None)
            if company.career_url_manual:
                company_fields.pop("career_url", None)
                company_fields.pop("career_status", None)
            for key, value in company_fields.items():
                setattr(company, key, value)

        feed = session.scalar(select(JobFeed).where(
            JobFeed.company_id == company.id, JobFeed.tenant == item["id"]))
        if feed is None:
            session.add(JobFeed(
                company_id=company.id, provider=item["provider"], tenant=item["id"],
                board_url=item["source_page_url"], feed_url=item["feed_url"],
                status="pending", enabled=True, job_count=0, next_scan_at=now,
            ))
            created += 1
    return created


def ensure_default_job_feeds(session: Session | None = None) -> int:
    """Idempotently add newly configured defaults; existing pause state is retained."""
    if session is None and IS_SQLITE:
        initialize_sqlite_schema(engine)
    now = utcnow()
    if session is not None:
        return _ensure_default_job_feeds(session, now)
    with SessionLocal.begin() as session:
        return _ensure_default_job_feeds(session, now)
