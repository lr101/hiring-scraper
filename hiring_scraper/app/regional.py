"""Import selected BA captures and explicitly refresh a saved regional search.

Run ``python -m hiring_scraper.app.regional --help`` for the opt-in commands.
Imports never interpret absence from a selected search as job closure.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hiring_scraper.app.models import Company, Job, JobFeed, JobLocation, UserProfile
from hiring_scraper.regional import BASE_URL, DEFAULT_TERMS, acquire, normalize_job


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _rows(snapshot):
    if isinstance(snapshot, (str, Path)):
        snapshot = json.loads(Path(snapshot).read_text())
    if isinstance(snapshot, list):
        return snapshot
    if isinstance(snapshot, dict):
        rows = snapshot.get('jobs', snapshot.get('ergebnisliste', snapshot.get('stellenangebote')))
        if isinstance(rows, list):
            return rows
    raise ValueError('Snapshot must contain a list of source rows or normalized jobs')


def _employer_name(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).strip().casefold())


def _provenance(metadata):
    return {key: metadata.get(key) for key in ('source_observed_at', 'source_api_url', 'source_url')}


def _location_matches(old, new):
    return (_employer_name(old['label']) == _employer_name(new['label'])
            and (not old.get('country_code') or not new.get('country_code')
                 or old['country_code'] == new['country_code']))


def import_snapshot(session: Session, snapshot, observed_at: str | None = None) -> dict:
    """Idempotent employer/reference import; caller owns commit or rollback.

    Both raw captured detail lists and ``{'jobs': normalized_jobs}`` are accepted.
    Regional feeds are deliberately excluded from the ordinary ATS scan worker.
    """
    counts = {'companies_created': 0, 'feeds_created': 0, 'jobs_created': 0, 'jobs_updated': 0, 'skipped': 0}
    feed_ids = set()
    for row in _rows(snapshot):
        try:
            if isinstance(row, dict) and 'external_id' in row and 'raw_metadata' in row:
                job_data = row
                if not all(isinstance(row.get(key), str) and row[key].strip()
                           for key in ('external_id', 'title', 'company_name', 'company_source_id', 'url')):
                    raise ValueError('Invalid normalized source identity')
                if not isinstance(row.get('raw_metadata'), dict):
                    raise ValueError('Invalid normalized metadata')
                if row['raw_metadata'].get('source') != 'arbeitsagentur' or row['raw_metadata'].get('source_reference') != row['external_id']:
                    raise ValueError('Normalized source/reference mismatch')
            else:
                job_data = normalize_job(row, observed_at)
        except ValueError:
            counts['skipped'] += 1
            continue
        company = session.scalar(select(Company).where(Company.source == 'arbeitsagentur',
                                                       Company.source_id == job_data['company_source_id']))
        if company is None:
            # A sparse refresh can omit the public hash. Recover identity only
            # from an already observed reference for the same named employer;
            # conflicting hashes never merge different employers or agencies.
            candidates = session.scalars(select(Company).join(JobFeed).join(Job).where(
                Company.source == 'arbeitsagentur', JobFeed.provider == 'arbeitsagentur',
                Job.external_id == job_data['external_id'])).unique().all()
            matches = [candidate for candidate in candidates
                       if _employer_name(candidate.name) == _employer_name(job_data['company_name'])
                       and (job_data['company_source_id'].startswith('name:') or candidate.source_id.startswith('name:'))]
            if len(matches) == 1:
                company = matches[0]
                if job_data['company_source_id'].startswith('hash:'):
                    company.source_id = job_data['company_source_id']
        if company is None:
            company = Company(source='arbeitsagentur', source_id=job_data['company_source_id'],
                              name=job_data['company_name'], source_url=job_data['url'],
                              category=job_data['raw_metadata'].get('employer_type'), career_status='regional_source')
            session.add(company)
            session.flush()
            counts['companies_created'] += 1
        else:
            company.name = job_data['company_name']
            if job_data['raw_metadata'].get('employer_type') == 'agency':
                company.category = 'agency'
        feed = session.scalar(select(JobFeed).where(JobFeed.company_id == company.id, JobFeed.provider == 'arbeitsagentur'))
        if feed is None:
            feed = JobFeed(company_id=company.id, provider='arbeitsagentur', tenant=company.source_id,
                           board_url='https://www.arbeitsagentur.de/jobsuche/',
                           feed_url=BASE_URL+'/pc/v6/jobs', status='selected_search', next_scan_at=None)
            session.add(feed)
            session.flush()
            counts['feeds_created'] += 1
        feed.next_scan_at = None
        feed.status = 'selected_search'
        feed.last_checked_at = _timestamp(observed_at or job_data['raw_metadata'].get('source_observed_at'))
        feed_ids.add(feed.id)
        job = session.scalar(select(Job).where(Job.feed_id == feed.id, Job.external_id == job_data['external_id']))
        created = job is None
        if created:
            job = Job(feed_id=feed.id, external_id=job_data['external_id'], title=job_data['title'], url=job_data['url'])
            session.add(job)
            counts['jobs_created'] += 1
        else:
            counts['jobs_updated'] += 1
        metadata = job_data['raw_metadata']
        prior = dict(job.raw_metadata or {})
        description = job_data.get('description')
        detail_ok = metadata.get('detail_state') == 'ok'
        keep_description = bool(job.description) and (not description or (not detail_ok and len(description) < len(job.description)))
        if not keep_description:
            job.description = description
        raw_old = prior.get('source_record', {})
        raw_new = metadata.get('source_record', {})
        source_record = dict(raw_old) if isinstance(raw_old, dict) else {}
        if isinstance(raw_new, dict):
            source_record.update({key: value for key, value in raw_new.items() if value is not None and value != '' and value != []})
        if keep_description:
            source_record['stellenangebotsBeschreibung'] = job.description
        # Preserve earlier detailed evidence when summary-only refreshes omit it.
        merged = {**prior, **{key: value for key, value in metadata.items() if value is not None and value != [] and value != {}}}
        merged.setdefault('remote_country_codes', [])
        merged.setdefault('remote_scope_source', None)
        if prior.get('employer_type') and not any(key in raw_new for key in
                                                   ('istPrivateArbeitsvermittlung', 'istArbeitnehmerUeberlassung')):
            merged['employer_type'] = prior['employer_type']
        merged['source_record'] = source_record
        if keep_description:
            merged['description_preserved_from_previous_capture'] = True
        prior_evidence = prior.get('field_evidence') or {}
        evidence = dict(prior_evidence)
        job.title = job_data['title']
        job.url = job_data['url']
        for key in ('location_text', 'employment_type', 'date_posted', 'salary'):
            if job_data.get(key):
                setattr(job, key, job_data[key])
        # "Home office possible" cannot revoke an earlier explicit percentage.
        # A new explicit negative, percentage or negotiated type supplies the
        # evidence needed to replace the previously observed arrangement.
        explicit_negative = raw_new.get('homeofficemoeglich') is False
        explicit_percentage = (raw_new.get('homeofficetyp') == 'ANGABE_IN_PROZENT'
                               and metadata.get('homeoffice_percentage') is not None)
        explicit_negotiated = raw_new.get('homeofficetyp') == 'NACH_VEREINBARUNG'
        replace_arrangement = (created or explicit_negative or explicit_percentage or explicit_negotiated
                               or (prior.get('homeoffice_percentage') is None and prior.get('homeoffice_type') is None
                                   and 'homeofficemoeglich' in raw_new))
        if replace_arrangement:
            job.is_remote = bool(job_data.get('is_remote'))
            job.work_arrangement = job_data.get('work_arrangement')
            if explicit_negative:
                job.is_remote = False
                job.work_arrangement = None
            for key in ('homeoffice_possible', 'homeoffice_type', 'homeoffice_percentage'):
                merged[key] = metadata.get(key)
            if explicit_negative or explicit_negotiated:
                merged['homeoffice_percentage'] = None
                source_record.pop('homeofficeprozent', None)
            if explicit_negative:
                merged['homeoffice_type'] = None
                source_record.pop('homeofficetyp', None)
            evidence['work_arrangement'] = {**_provenance(metadata), 'is_remote': job.is_remote,
                                            'work_arrangement': job.work_arrangement,
                                            **{key: merged.get(key) for key in
                                               ('homeoffice_possible', 'homeoffice_type', 'homeoffice_percentage')}}
        else:
            for key in ('homeoffice_possible', 'homeoffice_type', 'homeoffice_percentage'):
                merged[key] = prior.get(key)
            if 'work_arrangement' not in evidence:
                evidence['work_arrangement'] = {**_provenance(prior), 'is_remote': job.is_remote,
                                                'work_arrangement': job.work_arrangement}
        seen = _timestamp(observed_at or metadata.get('source_observed_at'))
        if created:
            job.first_seen_at = seen
        job.last_seen_at = seen
        # Seeing a job explicitly can reopen it; selected-search absence never closes.
        job.is_active = True
        job.closed_at = None
        locations = job_data.get('locations')
        if isinstance(locations, list) and locations:
            old_locations = [{'label': location.label, 'latitude': location.latitude,
                              'longitude': location.longitude, 'precision': location.precision,
                              'country_code': location.country_code} for location in job.locations]
            merged_locations, location_evidence = [], []
            for location in locations:
                if not isinstance(location, dict) or not location.get('label'):
                    continue
                current = {key: location.get(key) for key in
                           ('label', 'latitude', 'longitude', 'precision', 'country_code')}
                matching = [old for old in old_locations if _location_matches(old, current)]
                retained = False
                if len(matching) == 1 and (current['latitude'] is None or current['longitude'] is None):
                    old = matching[0]
                    if old['latitude'] is not None and old['longitude'] is not None:
                        current.update(latitude=old['latitude'], longitude=old['longitude'], precision=old['precision'])
                        retained = True
                    if not current['country_code']:
                        current['country_code'] = old['country_code']
                origin = _provenance(metadata)
                if retained:
                    previous = [item for item in prior_evidence.get('locations', []) if _location_matches(item, current)]
                    origin = _provenance(previous[0]) if len(previous) == 1 else _provenance(prior)
                merged_locations.append(current)
                location_evidence.append({**origin, **current})
            job.locations.clear()
            job.locations.extend(JobLocation(**location) for location in merged_locations)
            evidence['locations'] = location_evidence
        merged['field_evidence'] = evidence
        job.raw_metadata = merged
        session.flush()
    for feed_id in feed_ids:
        feed = session.get(JobFeed, feed_id)
        feed.job_count = session.scalar(select(func.count(Job.id)).where(Job.feed_id == feed_id, Job.is_active.is_(True)))
    session.flush()
    return counts


def load_search_area(session: Session, *, profile_id=None, profile_json=None) -> dict:
    """Read the saved profile's search_area without requiring a particular CV schema."""
    if profile_id is not None:
        profile = session.get(UserProfile, profile_id)
        if profile is None:
            raise ValueError('Saved profile does not exist')
        payload = profile.preferences
    elif profile_json is not None:
        payload = json.loads(Path(profile_json).read_text())
        if isinstance(payload, dict) and isinstance(payload.get('preferences'), dict):
            payload = payload['preferences']
    else:
        return {}
    if not isinstance(payload, dict):
        raise ValueError('Saved profile or area config must be an object')
    area = payload.get('search_area', payload)
    if not isinstance(area, dict):
        raise ValueError('search_area must be an object')
    return dict(area)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Opt-in, bounded observed BA Jobsuche acquisition/import')
    sub = parser.add_subparsers(dest='command', required=True)
    importer = sub.add_parser('import', help='Import raw detail rows or a normalized snapshot idempotently')
    importer.add_argument('snapshot', type=Path)
    search = sub.add_parser('acquire', help='Capture a fresh selected regional search; does not claim market completeness')
    search.add_argument('--out', type=Path, required=True, help='Fresh directory (must not exist)')
    search.add_argument('--city')
    search.add_argument('--radius-km', type=float)
    search.add_argument('--profile-id', type=int)
    search.add_argument('--profile-json', type=Path, help='Saved profile JSON or search-area config')
    search.add_argument('--term', action='append', dest='terms', help='Repeat for each role term; defaults to broad English/German families')
    search.add_argument('--max-pages', type=int, default=3)
    search.add_argument('--page-size', type=int, default=100)
    search.add_argument('--max-details', type=int, default=100)
    search.add_argument('--max-requests', type=int, default=300)
    search.add_argument('--delay', type=float, default=1)
    search.add_argument('--retries', type=int, default=2)
    search.add_argument('--import', action='store_true', dest='import_after', help='Import resulting snapshot into the application')
    args = parser.parse_args(argv)
    from hiring_scraper.app.database import SessionLocal, engine, initialize_sqlite_schema
    initialize_sqlite_schema(engine)
    with SessionLocal() as session:
        try:
            if args.command == 'import':
                result = import_snapshot(session, args.snapshot)
            else:
                area = load_search_area(session, profile_id=args.profile_id, profile_json=args.profile_json)
                city = args.city or area.get('city') or area.get('label')
                if not city:
                    parser.error('Provide --city or a saved search_area with a city')
                radius = args.radius_km if args.radius_km is not None else area.get('radius_km', 35)
                terms = args.terms or area.get('terms') or DEFAULT_TERMS
                result = acquire(args.out, city=city, radius_km=float(radius), terms=terms,
                                 max_pages=args.max_pages, page_size=args.page_size, max_details=args.max_details,
                                 max_requests=args.max_requests, delay=args.delay, retries=args.retries)
                if args.import_after:
                    result['import_counts'] = import_snapshot(session, args.out/'snapshot.json')
            session.commit()
        except (ValueError, OSError, json.JSONDecodeError) as error:
            session.rollback()
            parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
