"""Backfill job signals and optionally hydrate sparse postings from verified JSON-LD.

Runs in the server image: python -m hiring_scraper.app.enrichment [--fetch-details]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from sqlalchemy import select

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.models import Job, utcnow
from hiring_scraper.http import Client, OriginPacer
from hiring_scraper.matching import VERSION, detail_updates, enrich_job

SOURCE_FIELDS = ('title','url','description','raw_metadata','seniority','employment_type','schedule',
                 'work_arrangement','is_remote','salary')


def job_input(job: Job) -> dict:
    return {field: getattr(job, field) for field in SOURCE_FIELDS}


def current_enrichment(job: Job) -> dict:
    source = job_input(job)
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()
    stored = job.enrichment or {}
    if stored.get('version') == VERSION and stored.get('source_hash') == digest:
        return stored
    return enrich_job(source)


def refresh_enrichment(job: Job) -> None:
    job.enrichment = current_enrichment(job)



def preserve_verified_detail(job: Job, incoming: dict) -> dict:
    """Keep details across unchanged summaries; a newly supplied description wins."""
    if job.title != incoming.get('title') or job.url != incoming.get('url'):
        return incoming
    old = job.raw_metadata or {}
    data = dict(incoming)
    metadata = dict(incoming.get('raw_metadata') or {})
    description = incoming.get('description')
    same_summary = ('detail_listing_description' in old and description == old['detail_listing_description'])
    if old.get('detail_enrichment_attempt') and (same_summary or description == job.description):
        metadata['detail_enrichment_attempt'] = old['detail_enrichment_attempt']
    verified = old.get('description_method') in {'verified_detail_jsonld','verified_detail_html'}
    if verified and (not description or same_summary):
        data['description'] = job.description
        for key in ('detail_requirements', 'detail_listing_description', 'description_method',
                    'description_evidence_url', 'detail_enrichment_attempt'):
            if key in old:
                metadata[key] = old[key]
    data['raw_metadata'] = metadata
    return data


def backfill(limit: int = 200, *, fetch_details: bool = False, client=None) -> dict:
    if not 1 <= limit <= 200:
        raise ValueError('limit must be between 1 and 200')
    counts = {'checked': 0, 'updated': 0, 'details_attempted': 0, 'descriptions_improved': 0}
    candidates = []
    with SessionLocal() as session:
        jobs = session.scalars(select(Job).where(Job.is_active.is_(True)).order_by(Job.id)).all()
        for job in jobs:
            signals = current_enrichment(job)
            if signals != job.enrichment:
                job.enrichment = signals
                counts['updated'] += 1
            counts['checked'] += 1
            detail_source = (job.raw_metadata or {}).get('description_method') in {'verified_detail_jsonld','verified_detail_html'}
            if not fetch_details or len(candidates) >= limit or signals['quality'] != 'limited' and not detail_source:
                continue
            attempt = (job.raw_metadata or {}).get('detail_enrichment_attempt') or {}
            try:
                checked_at = datetime.fromisoformat(attempt['checked_at'])
                if checked_at > utcnow() - timedelta(days=7) and attempt.get('url') == job.url:
                    continue
            except (KeyError, ValueError, TypeError):
                pass
            candidates.append(job.id)
        session.commit()
    if not candidates:
        return counts
    client = client or Client(Path('/tmp/hiring-scraper-enrichment'), timeout=12, delay=1,
                              max_requests=limit*8, origin_pacer=OriginPacer(),
                              user_agent=os.getenv("HIRING_USER_AGENT", "HiringScraper/0.3 (public job-detail enrichment)"))
    for job_id in candidates:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None or not job.is_active:
                continue
            source = job_input(job)
        try:
            metadata, body = client.get(source['url'])
            updates = detail_updates(source, body, metadata.get('url') or source['url']) if metadata.get('state') == 'ok' else {}
        except Exception as error:
            metadata, updates = {'state': 'detail_error', 'error_type': type(error).__name__}, {}
        counts['details_attempted'] += 1
        with SessionLocal.begin() as session:
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or not job.is_active or job_input(job) != source:
                continue
            raw = dict(job.raw_metadata or {})
            if updates:
                job.description = updates['description']
                raw.update(updates['raw_metadata'])
                counts['descriptions_improved'] += 1
            raw['detail_enrichment_attempt'] = {'url': job.url, 'checked_at': utcnow().isoformat(),
                'status': 'improved' if updates else 'no_verified_description', 'http_state': metadata.get('state')}
            job.raw_metadata = raw
            refresh_enrichment(job)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=20, help='Maximum detail pages to try per pass (1–100)')
    parser.add_argument('--fetch-details', action='store_true')
    parser.add_argument('--watch', action='store_true', help='Repeat every hour; attempted URLs wait seven days')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit must be between 1 and 100')
    while True:
        print(json.dumps(backfill(args.limit, fetch_details=args.fetch_details)), flush=True)
        if not args.watch:
            break
        time.sleep(3600)


if __name__ == '__main__':
    main()
