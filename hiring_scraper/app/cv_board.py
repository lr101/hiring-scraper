"""Explicitly import an anonymized profile and selected regional job captures.

No CV profile is included in the ordinary global fixture seed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from sqlalchemy import select
from hiring_scraper.app.database import SessionLocal, engine, initialize_sqlite_schema
from hiring_scraper.app.models import UserProfile, utcnow
from hiring_scraper.app.profiles import ProfileRequest
from hiring_scraper.app.regional import import_snapshot
from hiring_scraper.matching import normalize_skills


def import_board(session, profile_path, regional_snapshot, employer_snapshot=None):
    """Validate and import explicitly supplied inputs; caller owns transaction."""
    payload = ProfileRequest.model_validate(json.loads(Path(profile_path).read_text(encoding='utf-8')))
    data = payload.model_dump()
    name = data.pop('name').strip()
    if not name:
        raise ValueError('Enter a profile name')
    data['skills'] = normalize_skills(data['skills'])
    profile = session.scalar(select(UserProfile).where(UserProfile.name == name).order_by(UserProfile.id))
    if profile is None:
        profile = UserProfile(name=name)
        session.add(profile)
    profile.preferences = data
    profile.updated_at = utcnow()
    session.flush()
    result = {'profile_id': profile.id, 'regional': import_snapshot(session, regional_snapshot)}
    if employer_snapshot is not None:
        result['employers'] = import_employer_snapshot(session, employer_snapshot)
    return result


def import_employer_snapshot(session, snapshot_path):
    """Import a compact employer supplement using existing feed/job contracts."""
    from hiring_scraper.app.models import Company, JobFeed, Job, JobLocation
    from hiring_scraper.app.enrichment import refresh_enrichment, preserve_verified_detail
    from hiring_scraper.app.seed import _datetime, _job_locations
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    observed = _datetime(snapshot.get('observed_at')) or utcnow()
    count = 0
    for row in snapshot['companies']:
        source = row.get('source', 'employer_supplement')
        company = session.scalar(select(Company).where(Company.source == source, Company.source_id == row['source_id']))
        if company is None:
            company = Company(source=source, source_id=row['source_id'], name=row['name'])
            session.add(company)
            session.flush()
        for key in ('name', 'website_url', 'domain', 'source_url', 'career_url', 'latitude', 'longitude', 'location_label', 'location_precision'):
            if key in row:
                setattr(company, key, row[key])
        company.career_status = row.get('career_status', 'jobs_feed_found')
        for feed_row in row.get('feeds', []):
            feed = session.scalar(select(JobFeed).where(JobFeed.company_id == company.id, JobFeed.provider == feed_row['provider'], JobFeed.feed_url == feed_row['feed_url']))
            if feed is None:
                feed = JobFeed(company_id=company.id, provider=feed_row['provider'], feed_url=feed_row['feed_url'], status=feed_row.get('status', 'parsed'))
                session.add(feed)
                session.flush()
            feed.board_url = feed_row.get('board_url')
            feed.status = feed_row.get('status', 'parsed')
            feed.next_scan_at = None
            feed.last_checked_at = _datetime(feed_row.get('last_checked_at')) or observed
            for item in feed_row.get('jobs', []):
                external_id = str(item.get('id', item.get('external_id')))
                job = session.scalar(select(Job).where(Job.feed_id == feed.id, Job.external_id == external_id))
                if job is None:
                    job = Job(feed_id=feed.id, external_id=external_id, title=item['title'], url=item['url'], first_seen_at=observed, last_seen_at=observed)
                    session.add(job)
                item = preserve_verified_detail(job, item)
                for key in ('title', 'url', 'description', 'work_arrangement', 'employment_type', 'schedule', 'department', 'seniority', 'date_posted', 'salary'):
                    setattr(job, key, item.get(key))
                job.location_text = item.get('location', item.get('location_text'))
                job.is_remote = bool(item.get('is_remote'))
                job.raw_metadata = item.get('raw_metadata', {})
                job.last_seen_at = feed.last_checked_at
                job.is_active = True
                refresh_enrichment(job)
                session.flush()
                for previous in list(job.locations):
                    session.delete(previous)
                for location in _job_locations(item):
                    session.add(JobLocation(job_id=job.id, **{key: location.get(key) for key in ('label', 'latitude', 'longitude', 'precision', 'country_code')}))
                count += 1
            feed.job_count = len(feed_row.get('jobs', []))
    session.flush()
    return {'jobs_imported': count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--regional-data', required=True, type=Path)
    parser.add_argument('--employer-data', type=Path)
    args = parser.parse_args()
    initialize_sqlite_schema(engine)
    with SessionLocal.begin() as session:
        result = import_board(session, args.profile, args.regional_data, args.employer_data)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
