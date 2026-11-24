"""Job search ranking, materialized snapshots, and refresh queue operations."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, case, delete, func, or_, select, text, true, update
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from hiring_scraper.app.application_deduplication import index_vacancy_identities
from hiring_scraper.app.database import dialect_insert, SessionLocal
from hiring_scraper.app.models import (
    Company, ConfiguredLocation, Job, JobApplication, JobFeed, JobLocation, JobSearchRefresh,
    JobSearchResult, JobSearchSnapshot, JobSearchState, JobVacancyGroup, MatchDirtyJob,
    ProfileJobMatch, SemanticSearchDocument, UserProfile, utcnow,
)
from hiring_scraper.app.discovery_jobs import german_company_filter
from hiring_scraper.election_data import municipality_vote_result
from hiring_scraper.geography import GERMAN_STATES, normalize_german_state
from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.applications import (
    APPLICATION_STATUSES, application_json, canonical_application_states_for_jobs, vacancy_group_ids_for_jobs,
)
from hiring_scraper.app.match_compute import (
    apply_profile_match_scope,
    compute_match,
    current_score_version,
    indexed_missing_match_action,
    load_stored_match_snapshots,
    stored_match_explanation,
)
from hiring_scraper.matching import VERSION as MATCHING_VERSION
from hiring_scraper.semantic_matching import MODEL_KEY as SEMANTIC_MODEL_KEY
from hiring_scraper.app.board_scope import (
    deduplicate_jobs, expiry, fully_remote, geographic_scope, remote_countries,
    german_location_text_names, german_state_bounds, location_text_names,
    vacancy_keys, vacancy_text_scope,
)


_LOG = logging.getLogger(__name__)
_SEARCH_SNAPSHOT_REFRESH_DELAY_SECONDS = 2.0
_SEARCH_SNAPSHOT_REFRESH_LEASE_SECONDS = 15 * 60
SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS = 15 * 60
WORK_LOCATION_GEOGRAPHY_VERSION = 'work-location-text-v2'




def _location_text_patterns(value: str) -> set[str]:
    source = value.strip().casefold().replace('ß', 'ss')
    normalized = unicodedata.normalize('NFKD', source)
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    transliterated = source.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue'}))
    patterns = set()
    for raw in {value.strip().casefold(), value.strip().upper()}:
        raw_words = re.sub(r'[^A-Za-z0-9äöüÄÖÜ]+', '%', raw).strip('%')
        if raw_words:
            patterns.add(f'%{raw_words}%')
    for variant in {normalized, transliterated}:
        words = re.sub(r'[^a-z0-9]+', '%', variant).strip('%')
        if words:
            patterns.add(f'%{words}%')
    return patterns


def _job_location_text_candidate(value: str):
    patterns = {pattern for name in location_text_names(value)
                for pattern in _location_text_patterns(name)}
    return or_(*(Job.location_text.ilike(pattern) for pattern in patterns),
               *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns))


def _remote_job_candidate():
    return or_(Job.work_arrangement == 'remote',
               and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None)))


def _job_area_candidates(latitude: float, longitude: float, radius_km: float,
                         place: str | None, location_scope: str):
    """Broadly narrow jobs before Python performs exact workplace and remote checks."""
    expiry_clause = or_(Job.raw_metadata["validThrough"].as_string().is_not(None),
                        Job.raw_metadata["valid_through"].as_string().is_not(None))
    if location_scope == 'remote':
        return or_(_remote_job_candidate(), expiry_clause)
    location_ids = select(JobLocation.job_id).where(text(
        "ST_DWithin(job_locations.location_geog, "
        "ST_SetSRID(ST_MakePoint(:longitude, :latitude),4326)::geography, :radius_m, false)"
    ).bindparams(longitude=longitude, latitude=latitude, radius_m=radius_km * 1000))
    spatial_clause = Job.id.in_(location_ids)
    clauses = [spatial_clause, expiry_clause, _remote_job_candidate()]
    if place:
        clauses.append(_job_location_text_candidate(place))
    return or_(*clauses)


def _job_state_text_candidates(state: str):
    patterns = {pattern for value in german_location_text_names(state)
                for pattern in _location_text_patterns(value)}
    candidates = [*(Job.location_text.ilike(pattern) for pattern in patterns),
                  *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns)]
    if bounds := german_state_bounds().get(state):
        west, south, east, north = bounds
        candidates.append(Job.locations.any(and_(
            JobLocation.latitude.between(south, north),
            JobLocation.longitude.between(west, east),
        )))
    return or_(*candidates)


def _job_germany_text_candidates():
    patterns = {pattern for value in ('Germany', 'Deutschland', *german_location_text_names())
                for pattern in _location_text_patterns(value)}
    country_code_patterns = (
        'DE', 'DE %', 'DE,%', 'DE/%', 'DE-%', 'DE)%',
        '%, DE', '%, DE %', '%, DE,%', '%,DE', '%,DE %',
        '% DE', '% DE %', '% DE,%', '%/DE', '%/DE %', '%/DE,%',
        '%(DE)%', '% (DE)%', '%(DE %', '% (DE %',
        '%;DE%', '%; DE%', '%|DE%', '%| DE%', '%\nDE%',
    )
    candidates = [*(Job.location_text.ilike(pattern) for pattern in patterns),
                  *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in patterns),
                  *(Job.location_text.ilike(pattern) for pattern in country_code_patterns),
                  *(Job.locations.any(JobLocation.label.ilike(pattern)) for pattern in country_code_patterns)]
    bounds = tuple(german_state_bounds().values())
    if bounds:
        west = min(bound[0] for bound in bounds)
        south = min(bound[1] for bound in bounds)
        east = max(bound[2] for bound in bounds)
        north = max(bound[3] for bound in bounds)
        candidates.append(Job.locations.any(and_(
            JobLocation.latitude.between(south, north),
            JobLocation.longitude.between(west, east),
        )))
    return or_(*candidates)


def _sort_jobs(jobs: list[Job], sort: str) -> list[Job]:
    if sort == "newest":
        dated = [job for job in jobs if job.date_posted]
        undated = [job for job in jobs if not job.date_posted]
        dated.sort(key=lambda job: (job.date_posted or "", job.id), reverse=True)
        undated.sort(key=lambda job: (job.title.casefold(), job.id))
        return dated + undated
    if sort == "title":
        return sorted(jobs, key=lambda job: (job.title.casefold(), job_employer_name(job).casefold(), job.id))
    if sort == "company":
        return sorted(jobs, key=lambda job: (job_employer_name(job).casefold(), job.title.casefold(), job.id))
    return sorted(jobs, key=lambda job: (not job.is_remote, job.title.casefold(), job.id))


def _job_date_rank(value: Any) -> float:
    if isinstance(value, datetime):
        parsed = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return parsed.timestamp()
    if not isinstance(value, str) or not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        return parsed.timestamp()
    except ValueError:
        return float("-inf")


def job_employer_name(job: Job) -> str:
    if job.employer_company is not None:
        return job.employer_company.name
    metadata = job.raw_metadata if isinstance(job.raw_metadata, dict) else {}
    organization = metadata.get("hiring_organization")
    if isinstance(organization, dict) and isinstance(organization.get("name"), str) and organization["name"].strip():
        return organization["name"].strip()
    return job.feed.company.name if job.feed is not None and job.feed.company is not None else ''


def job_json(job: Job, *, include_description: bool = False) -> dict[str, Any]:
    feed, source_company = job.feed, job.feed.company
    company = job.employer_company or source_company
    raw_metadata = job.raw_metadata if isinstance(job.raw_metadata, dict) else {}
    organization = raw_metadata.get("hiring_organization")
    organization = organization if isinstance(organization, dict) else {}
    employer_name = organization.get("name") if isinstance(organization.get("name"), str) else None
    employer_url = organization.get("url") if isinstance(organization.get("url"), str) else None
    employer_host = urlsplit(employer_url).hostname if employer_url else None
    source_company_name = source_company.name
    resolved_employer_name = job_employer_name(job)
    locations = [{"label": location.label, "latitude": location.latitude,
                  "longitude": location.longitude, "precision": location.precision,
                  "country_code": location.country_code,
                  "city_election_result": municipality_vote_result(location.label, location.country_code)}
                 for location in job.locations]
    data = {"id": job.id, "external_id": job.external_id, "title": job.title,
            "url": job.url, "company_id": company.id,
            "employer_company_id": job.employer_company_id,
            "company_name": resolved_employer_name,
            "company_name_is_employer": bool(job.employer_company is not None or
                                                (employer_name and employer_name.strip() and
                                                 employer_name.strip().casefold() != source_company_name.casefold())),
            "company_domain": company.domain or (employer_host if job.employer_company is None else None) or source_company.domain,
            "company_website": company.website_url or (employer_url if job.employer_company is None else None) or source_company.website_url,
            "company_source_url": company.source_url or source_company.source_url,
            "source_company_id": source_company.id, "source_company_name": source_company.name,
            "provider": feed.provider,
            "board_url": feed.board_url, "feed_status": feed.status,
            "locations": locations, "location_text": job.location_text,
            "is_remote": job.is_remote, "work_arrangement": job.work_arrangement,
            "employment_type": job.employment_type,
            "schedule": job.schedule, "department": job.department,
            "seniority": job.seniority, "date_posted": job.date_posted,
            "salary": job.salary, "first_seen_at": job.first_seen_at.isoformat(),
            "last_seen_at": job.last_seen_at.isoformat(), "is_active": job.is_active,
            "posting_status": "open" if job.is_active else "closed",
            "closed_at": job.closed_at.isoformat() if job.closed_at else None,
            "raw_metadata": job.raw_metadata}
    data.update(expiry(job))
    data['remote_country_codes'] = sorted(remote_countries(job))
    data["enrichment"] = current_enrichment(job)
    if include_description:
        data["description"] = job.description
    else:
        data["description_preview"] = (job.description or "")[:280]
    return data


def canonical_state(state: str | None) -> str | None:
    if state is None:
        return None
    try:
        return normalize_german_state(state)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def profile_match(job, profile, scope, semantic_documents=None):
    from sqlalchemy.orm import object_session
    from hiring_scraper.app.job_search import profile_match_index_stale
    from hiring_scraper.app.match_documents import load_match_documents

    session = None
    for obj in (job, profile):
        try:
            session = object_session(obj)
        except Exception:
            session = None
        if session is not None:
            break
    if semantic_documents is None:
        if session is not None and getattr(profile, 'id', None) is not None and getattr(job, 'id', None) is not None:
            snapshots = load_stored_match_snapshots(session, [(profile.id, job.id)])
            stored = stored_match_explanation(
                None,
                job,
                profile,
                snapshot=snapshots.get((profile.id, job.id)),
                scope=scope,
            )
            if stored is not None:
                return stored
        semantic_documents = load_match_documents(session, [job], [profile])
        return compute_match(job, profile, semantic_documents, scope=scope)

    stored = None
    if hasattr(semantic_documents, 'match_snapshots'):
        stored = stored_match_explanation(
            None,
            job,
            profile,
            snapshot=semantic_documents.match_snapshots.get((profile.id, job.id)),
            scope=scope,
        )
    elif hasattr(semantic_documents, 'indexed_matches'):
        indexed = semantic_documents.indexed_matches.get((profile.id, job.id))
        if indexed is not None:
            stored = stored_match_explanation(
                indexed,
                job,
                profile,
                semantic_documents,
                expected_version=current_score_version(),
                dirty_job_ids=getattr(semantic_documents, 'dirty_job_ids', None),
                profile_stale=profile_match_index_stale(profile),
                scope=scope,
            )
    if stored is not None:
        return stored
    return compute_match(job, profile, semantic_documents, scope=scope)


def _apply_profile_match_scope(match, scope):
    """External geography remains authoritative after local scoring."""
    return apply_profile_match_scope(match, scope)


def result_scope(job, latitude, longitude, radius_km, place, country, state=None, employer_country=None,
                  *, include_expired=False):
    if employer_country and (include_expired or not expiry(job)['expired']):
        if fully_remote(job):
            return geographic_scope(job, latitude, longitude, radius_km, place, employer_country,
                                    include_expired=include_expired)
        text_scope = vacancy_text_scope(job, country=employer_country)
        if text_scope is not None:
            return {'eligible': text_scope, 'reason': None if text_scope else 'outside_area',
                    'unknowns': [], 'match_kind': 'work_location' if text_scope else None}
        company = job.employer_company or job.feed.company
        source_company = job.feed.company
        has_german_workplace = any(
            str(location.country_code or "").casefold() == "de" for location in job.locations)
        eligible = (company.country_code == 'DE' or company.state in GERMAN_STATES or
                    source_company.source == "default_job_feed" or has_german_workplace)
        return {'eligible': eligible, 'reason': None if eligible else 'outside_area',
                'unknowns': [], 'match_kind': ('remote' if fully_remote(job) else 'employer_in_country') if eligible else None}
    if state and (include_expired or not expiry(job)['expired']):
        if fully_remote(job):
            return geographic_scope(job, latitude, longitude, radius_km, place, 'DE',
                                    include_expired=include_expired)
        text_scope = vacancy_text_scope(job, state=state)
        if text_scope is not None:
            return {'eligible': text_scope, 'reason': None if text_scope else 'outside_area',
                    'unknowns': [], 'match_kind': 'work_location' if text_scope else None}
        company = job.employer_company or job.feed.company
        eligible = company.state == state or job.feed.company.state == state
        return {'eligible': eligible, 'reason': None if eligible else 'outside_area',
                'unknowns': [], 'match_kind': ('remote' if fully_remote(job) else 'employer_in_state') if eligible else None}
    return geographic_scope(job, latitude, longitude, radius_km, place, country,
                            include_expired=include_expired)


def _search_scope(job, latitude, longitude, radius_km, place, country,
                  location_scope, state, employer_country, posting_status='open'):
    scope = result_scope(job, latitude, longitude, radius_km, place, country, state, employer_country,
                          include_expired=posting_status != 'open' and not job.is_active)
    if scope['reason'] != 'expired' and location_scope == 'remote' and not fully_remote(job):
        return {'eligible': False, 'reason': 'not_remote', 'unknowns': [], 'match_kind': None}
    if scope['eligible'] and location_scope == 'area' and fully_remote(job):
        return {'eligible': False, 'reason': 'remote_not_selected', 'unknowns': [], 'match_kind': 'remote'}
    return scope


def _scoped_candidate_winners(session, candidate_ids, latitude, longitude, radius_km,
                              place, country, location_scope, state, employer_country, posting_status='open'):
    """Stream scope checks and retain only the canonical job from each duplicate group."""
    groups: dict[int, dict[str, Any]] = {}
    seen: dict[tuple, int] = {}
    filtered = {'expired': 0, 'remote_country': 0, 'outside_area': 0,
                'remote_not_selected': 0, 'not_remote': 0}
    scoped_count = 0
    for start in range(0, len(candidate_ids), 200):
        batch_ids = candidate_ids[start:start + 200]
        jobs = session.scalars(select(Job).where(Job.id.in_(batch_ids)).options(
            joinedload(Job.feed).joinedload(JobFeed.company),
            joinedload(Job.employer_company), selectinload(Job.locations),
        )).unique().all()
        for job in jobs:
            scope = _search_scope(job, latitude, longitude, radius_km, place, country,
                                  location_scope, state, employer_country, posting_status)
            if not scope['eligible']:
                filtered[scope['reason']] += 1
                continue
            scoped_count += 1
            keys = vacancy_keys(job)
            existing = {seen[key] for key in keys if key in seen}
            group_id = min(existing) if existing else job.id
            quality = (job.is_active and not expiry(job)['expired'],
                       len(job.description or ''), len(job.locations), -job.id)
            merged_keys = set(keys)
            winner = (job.id, quality, scope)
            for old_group in existing:
                previous = groups.pop(old_group)
                merged_keys.update(previous['keys'])
                if previous['winner'][1] > winner[1]:
                    winner = previous['winner']
            groups[group_id] = {'keys': merged_keys, 'winner': winner}
            for key in merged_keys:
                seen[key] = group_id
    winners = sorted((group['winner'][0], group['winner'][2]) for group in groups.values())
    return winners, scoped_count, filtered


def _jobs_page(jobs, sort, offset, limit, latitude, longitude, radius_km,
               profile, min_match_score, include_unknown, place=None, country=None,
               location_scope='area', state=None, employer_country=None,
               include_internal=False, indexed_matches=None, precomputed_scopes=None,
               match_snapshots=None, dirty_job_ids=None):
    filtered = {'expired': 0, 'remote_country': 0, 'outside_area': 0,
                'remote_not_selected': 0, 'not_remote': 0,
                'profile_conflict': 0, 'below_score': 0, 'uncertain': 0}
    if precomputed_scopes is not None:
        scoped = jobs
        scopes = precomputed_scopes
        unique = jobs
    else:
        scoped, scopes = [], {}
        for job in jobs:
            scope = _search_scope(job, latitude, longitude, radius_km, place, country,
                                  location_scope, state, employer_country)
            if scope['eligible']:
                scoped.append(job)
                scopes[job.id] = scope
            else:
                filtered[scope['reason']] += 1
        unique = deduplicate_jobs(scoped)
    matching = _sort_jobs(unique, sort)
    matches = {}
    if profile is not None:
        selected = []
        from sqlalchemy.orm import object_session
        from hiring_scraper.app.job_search import _profile_index_version, profile_match_index_stale
        from hiring_scraper.app.match_documents import load_match_documents
        profile_session = None
        try:
            profile_session = object_session(profile)
        except Exception:
            profile_session = None
        expected_index_version = _profile_index_version(profile)
        if match_snapshots is not None or (indexed_matches is not None and profile_session is not None):
            snapshots = (
                match_snapshots
                if match_snapshots is not None
                else load_stored_match_snapshots(
                    profile_session,
                    [(profile.id, job.id) for job in matching if getattr(job, 'id', None) is not None],
                )
            )
            fallback_jobs = []
            for job in matching:
                snapshot = snapshots.get((profile.id, job.id))
                if snapshot is not None and snapshot.match is not None:
                    stored = stored_match_explanation(
                        None,
                        job,
                        profile,
                        snapshot=snapshot,
                        expected_profile_index_version=expected_index_version,
                        scope=scopes[job.id],
                    )
                    if stored is not None:
                        matches[job.id] = stored
                        continue
                    fallback_jobs.append(job)
                else:
                    profile_is_stale = (
                        snapshot.profile_index_version != expected_index_version
                        if snapshot is not None
                        else profile_match_index_stale(profile)
                    )
                    job_dirty = snapshot.dirty if snapshot is not None else False
                    if indexed_missing_match_action(profile_stale=profile_is_stale, job_dirty=job_dirty) == 'profile_conflict':
                        matches[job.id] = {
                            'eligible': False, 'score': 0, 'fit_tier': 'unlikely',
                            'uncertain': False, 'unknowns': [],
                        }
                    else:
                        fallback_jobs.append(job)
            if fallback_jobs:
                semantic_documents = load_match_documents(profile_session, fallback_jobs, [profile])
                for job in fallback_jobs:
                    matches[job.id] = compute_match(job, profile, semantic_documents, scope=scopes[job.id])
        elif indexed_matches is not None:
            profile_is_stale = profile_match_index_stale(profile)
            dirty_ids = set(dirty_job_ids or ())
            semantic_documents = load_match_documents(None, matching, [profile]) if indexed_matches else None
            expected_version = current_score_version()
            fallback_jobs = []
            for job in matching:
                indexed = indexed_matches.get(job.id)
                if indexed is None:
                    if indexed_missing_match_action(profile_stale=profile_is_stale, job_dirty=job.id in dirty_ids) == 'profile_conflict':
                        matches[job.id] = {
                            'eligible': False, 'score': 0, 'fit_tier': 'unlikely',
                            'uncertain': False, 'unknowns': [],
                        }
                    else:
                        fallback_jobs.append(job)
                else:
                    stored = stored_match_explanation(
                        indexed,
                        job,
                        profile,
                        semantic_documents,
                        expected_version=expected_version,
                        dirty_job_ids=dirty_ids,
                        profile_stale=profile_is_stale,
                        scope=scopes[job.id],
                    )
                    if stored is not None:
                        matches[job.id] = stored
                    else:
                        fallback_jobs.append(job)
            if fallback_jobs:
                if semantic_documents is None:
                    semantic_documents = load_match_documents(None, fallback_jobs, [profile])
                for job in fallback_jobs:
                    matches[job.id] = compute_match(job, profile, semantic_documents, scope=scopes[job.id])
        else:
            semantic_documents = load_match_documents(profile_session, matching, [profile])
            for job in matching:
                matches[job.id] = compute_match(job, profile, semantic_documents, scope=scopes[job.id])
        for job in matching:
            match = matches[job.id]
            if not match['eligible']:
                filtered['profile_conflict'] += 1
            elif match['score'] < min_match_score:
                filtered['below_score'] += 1
            elif not include_unknown and match['uncertain']:
                filtered['uncertain'] += 1
            else:
                selected.append(job)
        matching = selected
    if sort in {'relevance', 'match', 'posted', 'discovered'}:
        def match_order(job):
            match = matches.get(job.id) or {}
            score = match.get('score', -1)
            discovered = _job_date_rank(job.first_seen_at)
            posted = _job_date_rank(job.date_posted)
            if sort == 'posted':
                return (-posted, -score, -discovered, job.title.casefold(), job.id)
            if sort == 'discovered':
                return (-discovered, -score, -posted, job.title.casefold(), job.id)
            return (-score, -discovered, -posted, job.title.casefold(), job.id)
        matching.sort(key=match_order)
    items = [] if include_internal else [job_json(job) for job in matching[offset:offset + limit]]
    for item in items:
        item['match_kind'] = scopes[item['id']]['match_kind']
        item['geography'] = scopes[item['id']]
        if profile is not None:
            item['profile_match'] = matches[item['id']]
    tiers = [matches[job.id].get('fit_tier', 'possible') for job in matching] if profile else []
    result = {'items': items, 'total': len(matching), 'offset': offset, 'limit': limit,
            'location': {'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
                         'city': place, 'country_code': country},
            'location_scope': location_scope,
            'profile_id': profile.id if profile is not None else None,
            'counts': {'source': len(jobs), 'scoped': len(unique), 'filtered': filtered,
                       'duplicates_removed': len(scoped) - len(unique),
                       'recommended': tiers.count('recommended'), 'possible': tiers.count('possible'),
                       'unlikely': tiers.count('unlikely')},
            'coverage': {'source_count': len({job.feed_id for job in jobs}),
                         'note': 'Observed active source records; selected sources are not exhaustive market coverage.'},
            'rule': ('work location in Germany, with a German-employer fallback when location is missing or unspecified' if employer_country else 'work location in ' + state + ', with an employer-state fallback when location is missing or unspecified' if state else ('explicitly remote within country scope' if location_scope == 'remote' else
                     'mapped or parsed work location within radius' if location_scope == 'area' else
                     'mapped or parsed work location within radius OR explicitly remote within country scope')),
            'state': state, 'country': employer_country}
    if profile is not None:
        result['_matching_job_ids'] = [job.id for job in matching]
    if include_internal:
        result['_search_entries'] = [{
            'job_id': job.id,
            'scope': scopes[job.id],
            'match_score': matches.get(job.id, {}).get('score'),
            'match_tier': matches.get(job.id, {}).get('fit_tier'),
            'match_uncertain': matches.get(job.id, {}).get('uncertain'),
        } for job in matching]
    return result


def search_generation(session: Session) -> int:
    generation = session.scalar(select(func.max(JobSearchState.id)))
    return int(generation or 0)


def search_key(generation: int, criteria: dict[str, Any]) -> str:
    from hiring_scraper.matching import VERSION
    payload = json.dumps({'generation': generation, 'matching_version': VERSION, **criteria},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _search_criteria_hash(criteria: dict[str, Any]) -> str:
    stable = _stable_search_criteria(criteria)
    payload = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _search_page(session: Session, candidate_ids: list[int], profiles: list[Any], key: str,
                 generation: int, source_count: int, source_feed_count: int,
                 prefilter_filtered: dict[str, int],
                 sort: str, latitude: float, longitude: float,
                 radius_km: float, place: str | None, country: str | None,
                 location_scope: str, state: str | None, employer_country: str | None,
                 thresholds: dict[int | None, tuple[int, bool]],
                 criteria: dict[str, Any],
                 preserve_snapshot_key: str | None = None,
                 is_prewarmed: bool = False) -> JobSearchSnapshot:
    """Fetch a cached ordered result or materialize it once for later page reads."""
    snapshot = session.get(JobSearchSnapshot, key)
    if snapshot is not None:
        if is_prewarmed:
            snapshot.criteria_hash = _search_criteria_hash(criteria)
            snapshot.is_prewarmed = True
            session.commit()
        return snapshot

    insert = dialect_insert(session)
    now = utcnow()
    insert_statement = insert(JobSearchSnapshot).values(
        key=key, generation=generation, payload={},
        criteria_hash=_search_criteria_hash(criteria), created_at=now,
        is_prewarmed=is_prewarmed,
    ).on_conflict_do_nothing(index_elements=['key']).returning(JobSearchSnapshot.key)
    inserted_key = session.scalar(insert_statement)
    if inserted_key is None:
        # A concurrent request owns this key and has committed the complete row set.
        snapshot = session.get(JobSearchSnapshot, key)
        if snapshot is not None:
            return snapshot
        # The competing transaction rolled back; this request can safely retry.
        return _search_page(session, candidate_ids, profiles, key, generation, source_count, source_feed_count,
                            prefilter_filtered,
                            sort, latitude, longitude,
                            radius_km, place, country, location_scope, state, employer_country,
                            thresholds, criteria)

    result_count = 0
    combined_counts: dict[str, Any] = {
        'source': 0, 'scoped': 0, 'duplicates_removed': 0,
        'recommended': 0, 'possible': 0, 'unlikely': 0,
        'filtered': dict.fromkeys(('expired', 'remote_country', 'outside_area',
                                   'remote_not_selected', 'not_remote',
                                   'profile_conflict', 'below_score', 'uncertain'), 0),
    }
    application_profile_ids = session.scalars(select(JobApplication.profile_id).distinct()).all()
    # Attach every retained source copy before grouping the candidate set. If
    # indexing happens after winner selection, aliases bridged through a saved
    # vacancy can survive together in the first snapshot built after migration.
    if application_profile_ids:
        for start in range(0, len(candidate_ids), 200):
            batch_ids = candidate_ids[start:start + 200]
            jobs = session.scalars(select(Job).where(Job.id.in_(batch_ids)).options(
                joinedload(Job.feed).joinedload(JobFeed.company),
                joinedload(Job.employer_company), selectinload(Job.locations),
            )).unique().all()
            _index_unlinked_application_copies(session, application_profile_ids, jobs)
    winners, scoped_count, geo_filtered = _scoped_candidate_winners(
        session, candidate_ids, latitude, longitude, radius_km, place, country,
        location_scope, state, employer_country, criteria.get('posting_status', 'open'),
    )
    winner_ids = [job_id for job_id, _ in winners]
    scopes_by_id = dict(winners)
    duplicates_removed = scoped_count - len(winners)
    search_profiles = profiles or [None]
    for _profile in search_profiles:
        combined_counts['source'] += source_count
        combined_counts['scoped'] += len(winners)
        combined_counts['duplicates_removed'] += duplicates_removed
        for name, count in geo_filtered.items():
            combined_counts['filtered'][name] += count
        for name, count in prefilter_filtered.items():
            combined_counts['filtered'][name] += count

    # The job graph and profile matches stay bounded to one batch. Only the
    # compact canonical candidate IDs and exact scope results survive the scan.
    for start in range(0, len(winner_ids), 200):
        batch_ids = winner_ids[start:start + 200]
        jobs = session.scalars(select(Job).where(Job.id.in_(batch_ids)).options(
            joinedload(Job.feed).joinedload(JobFeed.company),
            joinedload(Job.employer_company), selectinload(Job.locations),
        )).unique().all()
        batch_scopes = {job_id: scopes_by_id[job_id] for job_id in batch_ids}
        batch_snapshots = (
            load_stored_match_snapshots(
                session,
                [(item.id, job_id) for item in profiles for job_id in batch_ids],
            )
            if profiles and batch_ids
            else {}
        )

        result_rows = [{
            'snapshot_key': key,
            'job_id': job.id,
            'scope': batch_scopes[job.id],
            'scope_uncertain': bool(batch_scopes[job.id].get('unknowns')),
            'posted_rank': _job_date_rank(job.date_posted),
            'discovered_rank': _job_date_rank(job.first_seen_at),
            'title_sort': job.title.casefold(),
            'company_sort': job_employer_name(job).casefold(),
            'remote_sort': not job.is_remote,
        } for job in jobs]
        for profile in search_profiles:
            min_score, include_unknown = thresholds.get(profile.id if profile else None, (0, True))
            page = _jobs_page(
                jobs, sort, 0, 1, latitude, longitude, radius_km, profile,
                min_score, include_unknown, place, country, location_scope,
                state, employer_country, include_internal=True,
                precomputed_scopes=batch_scopes,
                match_snapshots=batch_snapshots if profile else None,
            )
            entries = page.pop('_search_entries', [])
            page.pop('_matching_job_ids', None)
            for name in ('profile_conflict', 'below_score', 'uncertain'):
                combined_counts['filtered'][name] += page['counts']['filtered'][name]
            for name in ('recommended', 'possible', 'unlikely'):
                combined_counts[name] += page['counts'].get(name, 0)
            result_count += len(entries)
        if result_rows:
            session.execute(insert(JobSearchResult), result_rows)
    snapshot = session.get(JobSearchSnapshot, key)
    snapshot.payload = {
        'counts': combined_counts,
        'coverage': {'source_count': source_feed_count,
                     'note': 'Observed active source records; selected sources are not exhaustive market coverage.'},
        'location': {'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
                     'city': place, 'country_code': country},
        'location_scope': location_scope,
        'profile_ids': [profile.id for profile in profiles],
        'profile_id': profiles[0].id if len(profiles) == 1 else None,
        'total': result_count,
        'rule': ('work location in Germany, with a German-employer fallback when location is missing or unspecified' if employer_country else
                 'work location in ' + state + ', with an employer-state fallback when location is missing or unspecified' if state else
                 ('explicitly remote within country scope' if location_scope == 'remote' else
                  'mapped or parsed work location within radius' if location_scope == 'area' else
                  'mapped or parsed work location within radius OR explicitly remote within country scope')),
        'state': state,
        'country': employer_country,
        'criteria': criteria,
    }
    # A snapshot stores one scoped row per canonical job; bound both age and count.
    stale_filter = or_(
        and_(JobSearchSnapshot.is_prewarmed.is_(True),
             JobSearchSnapshot.created_at < now - timedelta(seconds=SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)),
        and_(JobSearchSnapshot.is_prewarmed.is_(False),
             JobSearchSnapshot.created_at < now - timedelta(days=1)),
    )
    excess_keys = select(JobSearchSnapshot.key).order_by(
        JobSearchSnapshot.created_at.desc(), JobSearchSnapshot.key).where(
            JobSearchSnapshot.is_prewarmed.is_(False))
    if preserve_snapshot_key is not None:
        stale_filter = and_(stale_filter, JobSearchSnapshot.key != preserve_snapshot_key)
        excess_keys = excess_keys.where(JobSearchSnapshot.key != preserve_snapshot_key)
    excess_keys = excess_keys.offset(32)
    stale_keys = select(JobSearchSnapshot.key).where(or_(
        stale_filter, JobSearchSnapshot.key.in_(excess_keys)))
    session.execute(delete(JobSearchResult).where(JobSearchResult.snapshot_key.in_(stale_keys)))
    session.execute(delete(JobSearchSnapshot).where(JobSearchSnapshot.key.in_(stale_keys)))
    session.commit()
    return snapshot


def _index_unlinked_application_copies(session: Session, profile_ids, jobs) -> None:
    """Retain a shared group ID for untracked source copies used by search."""
    jobs = list(jobs)
    if not jobs:
        return
    unlinked_ids = set()
    groups_by_job = vacancy_group_ids_for_jobs(session, [job.id for job in jobs])
    for profile_id in profile_ids:
        states_by_job = canonical_application_states_for_jobs(session, profile_id, jobs)
        unlinked_ids.update(job.id for job in jobs
                            if (application := states_by_job.get(job.id)) is not None
                            and application.vacancy_group_id is not None
                            and groups_by_job.get(job.id) != application.vacancy_group_id)
    if unlinked_ids:
        index_vacancy_identities(session, unlinked_ids)


def _stable_search_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in criteria.items() if name != 'freshness_bucket'}


def search_criteria_match(snapshot_criteria: Any, criteria: dict[str, Any]) -> bool:
    return (isinstance(snapshot_criteria, dict) and
            _stable_search_criteria(snapshot_criteria) == _stable_search_criteria(criteria))


def latest_stale_snapshot(session: Session, generation: int,
                           criteria: dict[str, Any]) -> JobSearchSnapshot | None:
    # Freshness buckets are cache versions, not user-visible search criteria.
    # Reuse the prior bucket while the worker builds the current one, but keep
    # the stale window no longer than one bucket so expiry checks stay bounded.
    stale_before = utcnow() - timedelta(seconds=SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)
    snapshot = session.scalars(select(JobSearchSnapshot).where(
        JobSearchSnapshot.criteria_hash == _search_criteria_hash(criteria),
        JobSearchSnapshot.generation <= generation,
        JobSearchSnapshot.created_at >= stale_before,
    ).order_by(JobSearchSnapshot.created_at.desc(), JobSearchSnapshot.key).limit(1)).first()
    if snapshot is None or not search_criteria_match((snapshot.payload or {}).get('criteria'), criteria):
        return None
    return snapshot


def posting_status_filter(posting_status: str):
    return true() if posting_status == 'all' else Job.is_active.is_(posting_status == 'open')


def materialize_search_snapshot(session: Session, profiles: list[UserProfile], key: str,
                                 generation: int, criteria: dict[str, Any],
                                 thresholds: dict[int | None, tuple[int, bool]],
                                 *, preserve_snapshot_key: str | None = None,
                                 is_prewarmed: bool = False) -> JobSearchSnapshot:
    """Build the full scoped result set for a request, outside the response path when possible."""
    latitude = criteria['latitude']
    longitude = criteria['longitude']
    radius_km = criteria['radius_km']
    place = criteria['place']
    scope_country = criteria['country']
    location_scope = criteria['location_scope']
    state = criteria['state']
    employer_country = criteria['employer_country']
    company_id = criteria['company_id']
    normalized_query = criteria['query']
    work_style = criteria['work_style']
    posting_status = criteria.get('posting_status', 'open')

    employer_company = aliased(Company)
    candidate_statement = (select(Job.id).join(Job.feed).join(JobFeed.company)
                           .outerjoin(employer_company, Job.employer_company_id == employer_company.id))
    filters = [posting_status_filter(posting_status)]
    if employer_country:
        scope_candidates = [german_company_filter(employer_company), german_company_filter(Company),
                            Company.source == "default_job_feed",
                            Job.locations.any(func.lower(JobLocation.country_code) == "de"),
                            _job_germany_text_candidates()]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    elif state:
        scope_candidates = [Company.state == state, employer_company.state == state,
                            _job_state_text_candidates(state)]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    if company_id is not None:
        filters.append(or_(JobFeed.company_id == company_id, Job.employer_company_id == company_id))
    if normalized_query:
        pattern = f"%{normalized_query}%"
        filters.append(or_(Job.title.ilike(pattern), Company.name.ilike(pattern), Company.domain.ilike(pattern),
                           employer_company.name.ilike(pattern), employer_company.domain.ilike(pattern),
                           Job.raw_metadata["hiring_organization"]["name"].as_string().ilike(pattern)))
    if work_style == 'remote':
        remote_clause = or_(Job.work_arrangement == 'remote',
                            and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None)))
        filters.append(remote_clause)
    elif work_style in {'hybrid', 'onsite'}:
        filters.append(Job.work_arrangement == work_style)
    source_filters = list(filters)
    if not state and not employer_country:
        filters.append(_job_area_candidates(latitude, longitude, radius_km, place, location_scope))

    candidate_cache_key = (generation, WORK_LOCATION_GEOGRAPHY_VERSION, company_id, normalized_query, state, employer_country,
                           work_style, latitude, longitude, radius_km, place,
                           scope_country, location_scope, posting_status)
    reusable = (not normalized_query and company_id is None and state is None and
                employer_country is None and work_style == 'all')
    cached = session.info.get('job_search_candidate_ids') if reusable else None
    source_rows = select(Job.id.label('job_id'), Job.feed_id.label('feed_id')).join(
        Job.feed).join(JobFeed.company).outerjoin(
            employer_company, Job.employer_company_id == employer_company.id
        ).where(*source_filters).subquery()
    source_count, source_feed_count = session.execute(select(
        func.count(source_rows.c.job_id), func.count(func.distinct(source_rows.c.feed_id))
    )).one()
    if cached and cached[0] == candidate_cache_key:
        candidate_ids = cached[1]
    else:
        candidate_ids = session.scalars(
            candidate_statement.where(*filters).order_by(Job.id.asc())
        ).all()
        if reusable:
            session.info['job_search_candidate_ids'] = (candidate_cache_key, candidate_ids)
    prefilter_excluded = max(0, int(source_count) - len(candidate_ids))
    prefilter_filtered = {}
    if prefilter_excluded:
        prefilter_reason = 'not_remote' if location_scope == 'remote' else 'outside_area'
        prefilter_filtered[prefilter_reason] = prefilter_excluded
    return _search_page(session, candidate_ids, profiles, key, generation,
                        source_count, source_feed_count, prefilter_filtered, 'match',
                        latitude, longitude, radius_km, place, scope_country,
                        location_scope, state, employer_country, thresholds, criteria,
                        preserve_snapshot_key=preserve_snapshot_key,
                        is_prewarmed=is_prewarmed)


def enqueue_search_snapshot_refresh(session: Session, criteria: dict[str, Any], *, prewarm: bool = False) -> None:
    """Coalesce refresh requests with the same search criteria in a durable queue."""
    signature = hashlib.sha256(json.dumps(
        criteria, sort_keys=True, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')).hexdigest()
    insert = dialect_insert(session)
    requested_at = utcnow()
    refresh_table = JobSearchRefresh.__table__
    unleased = or_(refresh_table.c.lease_token.is_(None),
                   refresh_table.c.lease_until.is_(None),
                   refresh_table.c.lease_until <= requested_at)
    coalesced_request_time = case(
        (unleased, refresh_table.c.requested_at),
        else_=requested_at,
    )
    statement = insert(JobSearchRefresh).values(
        key=signature, criteria=criteria, requested_at=requested_at,
        lease_token=None, lease_until=None, prewarm=prewarm,
    ).on_conflict_do_update(index_elements=['key'], set_={
        'criteria': criteria,
        'prewarm': or_(refresh_table.c.prewarm, prewarm),
        # Keep the original debounce start for an idle item so steady search
        # traffic cannot postpone it forever. During a live lease, move this
        # timestamp to mark that a follow-up pass is needed after the lease.
        'requested_at': coalesced_request_time,
    })
    session.execute(statement)
    session.commit()


def prewarm_profile_location_snapshots_once() -> int:
    """Queue profileless browse snapshots for every saved profile/configured area."""
    now = utcnow()
    current_bucket = int(now.timestamp() // SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)
    locations: dict[str, dict[str, Any]] = {}

    def add_area_variants(base: dict[str, Any], *, broad_scope: bool) -> None:
        variants = [("area_remote", "all"), ("remote", "remote")]
        if broad_scope:
            variants.insert(1, ("area", "all"))
        for location_scope, work_style in variants:
            criteria = {**base, "location_scope": location_scope, "work_style": work_style}
            locations[json.dumps(criteria, sort_keys=True, separators=(",", ":"))] = criteria

    with SessionLocal() as session:
        for profile in session.scalars(select(UserProfile).order_by(UserProfile.id)):
            area = (profile.preferences or {}).get("search_area") or {}
            if area.get("latitude") is None or area.get("longitude") is None:
                continue
            base = {
                "matching_version": MATCHING_VERSION,
                "geography_version": WORK_LOCATION_GEOGRAPHY_VERSION,
                "freshness_bucket": current_bucket,
                "company_id": None, "query": None,
                "latitude": float(area["latitude"]), "longitude": float(area["longitude"]),
                "radius_km": float(area.get("radius_km") or 15),
                "place": area.get("city") or "",
                "country": str(area.get("country_code") or "DE").upper(), "sort": "match",
                "state": None, "employer_country": None, "profiles": [],
            }
            base = {name: value for name, value in base.items() if name != "sort"}
            add_area_variants(base, broad_scope=True)
        for location in session.scalars(select(ConfiguredLocation).where(
            ConfiguredLocation.enabled.is_(True)).order_by(ConfiguredLocation.id)):
            state = canonical_state(location.state) if location.search_scope == "state" else None
            employer_country = "DE" if location.search_scope == "country" else None
            for scope_country in (None, "DE"):
                base = {
                    "matching_version": MATCHING_VERSION,
                    "geography_version": WORK_LOCATION_GEOGRAPHY_VERSION,
                    "freshness_bucket": current_bucket,
                    "company_id": None, "query": None,
                    "latitude": float(location.latitude), "longitude": float(location.longitude),
                    "radius_km": float(location.radius_km),
                    "place": location.city or "",
                    "country": scope_country,
                    "state": state, "employer_country": employer_country, "profiles": [],
                }
                # The frontend's boardLocationParams omits coordinates for state and
                # country selections, so use the same safe defaults as list_jobs.
                if state or employer_country:
                    base.update(latitude=49.0068705, longitude=8.4034195, radius_km=15, place=None)
                add_area_variants(base, broad_scope=not (state or employer_country))
        queued = 0
        for criteria in locations.values():
            generation = search_generation(session)
            current_criteria = {**criteria, "freshness_bucket": current_bucket}
            current_key = search_key(generation, current_criteria)
            snapshot = session.get(JobSearchSnapshot, current_key)
            if snapshot is None:
                enqueue_search_snapshot_refresh(session, current_criteria, prewarm=True)
                queued += 1
                continue
            snapshot.is_prewarmed = True
            snapshot.criteria_hash = _search_criteria_hash(current_criteria)
            if snapshot.created_at >= now - timedelta(minutes=5):
                continue
            next_criteria = {**criteria, "freshness_bucket": current_bucket + 1}
            next_key = search_key(generation, next_criteria)
            next_snapshot = session.get(JobSearchSnapshot, next_key)
            if next_snapshot is None:
                enqueue_search_snapshot_refresh(session, next_criteria, prewarm=True)
                queued += 1
            else:
                next_snapshot.is_prewarmed = True
                next_snapshot.criteria_hash = _search_criteria_hash(next_criteria)
        session.commit()
        return queued


def _claim_search_snapshot_refresh() -> tuple[str, dict[str, Any], str, datetime, bool] | None:
    token = str(uuid4())
    now = utcnow()
    with SessionLocal.begin() as session:
        refresh = session.scalars(select(JobSearchRefresh).where(
            JobSearchRefresh.requested_at <= now - timedelta(seconds=_SEARCH_SNAPSHOT_REFRESH_DELAY_SECONDS),
            or_(JobSearchRefresh.lease_until.is_(None), JobSearchRefresh.lease_until <= now),
        ).order_by(JobSearchRefresh.requested_at, JobSearchRefresh.key)
            .with_for_update(skip_locked=True).limit(1)).first()
        if refresh is None:
            return None
        refresh.lease_token = token
        refresh.lease_until = now + timedelta(seconds=_SEARCH_SNAPSHOT_REFRESH_LEASE_SECONDS)
        return refresh.key, dict(refresh.criteria), token, refresh.requested_at, refresh.prewarm


def _finish_search_snapshot_refresh(key: str, token: str, requested_at: datetime,
                                    *, retry: bool = False) -> None:
    with SessionLocal.begin() as session:
        owned = (JobSearchRefresh.key == key, JobSearchRefresh.lease_token == token)
        unchanged = (*owned, JobSearchRefresh.requested_at == requested_at)
        if not retry:
            removed = session.execute(delete(JobSearchRefresh).where(*unchanged))
            if removed.rowcount:
                return
        # Conditional DML rechecks ownership after any concurrent enqueue or
        # reclaim. A follow-up request keeps its timestamp and stays queued.
        session.execute(update(JobSearchRefresh).where(*owned).values(
            lease_token=None, lease_until=None,
            requested_at=case((JobSearchRefresh.requested_at == requested_at, utcnow()),
                              else_=JobSearchRefresh.requested_at) if retry
                         else JobSearchRefresh.requested_at,
        ))


def process_search_snapshot_refresh_once() -> bool:
    """Process one queued refresh; called by the dedicated search worker."""
    try:
        claimed = _claim_search_snapshot_refresh()
    except Exception:
        _LOG.exception("Could not claim a job search snapshot refresh")
        return False
    if claimed is None:
        return False
    key, criteria, token, requested_at, prewarm = claimed
    try:
        # On-demand work can cross a boundary; proactive work can target the
        # next bucket so it is ready before the current snapshot expires.
        criteria = dict(criteria)
        current_bucket = int(utcnow().timestamp() // SEARCH_SNAPSHOT_FRESHNESS_BUCKET_SECONDS)
        try:
            requested_bucket = int(criteria.get('freshness_bucket', current_bucket))
        except (TypeError, ValueError):
            requested_bucket = current_bucket
        criteria['freshness_bucket'] = min(max(requested_bucket, current_bucket), current_bucket + 1)
        profile_criteria = {int(item['id']): item for item in criteria.get('profiles', [])}
        with SessionLocal() as session:
            profile_ids = list(profile_criteria)
            profiles = session.scalars(select(UserProfile).where(
                UserProfile.id.in_(profile_ids)).order_by(UserProfile.id)
            ).all() if profile_ids else []
            current_profiles = (len(profiles) == len(profile_ids) and all(
                profile.match_index_version == profile_criteria[profile.id].get('match_index_version')
                for profile in profiles
            ))
            if current_profiles:
                generation = search_generation(session)
                current_key = search_key(generation, criteria)
                current_snapshot = session.get(JobSearchSnapshot, current_key)
                if current_snapshot is None:
                    thresholds = {
                        int(item['id']): (int(item['min_match_score']), bool(item['include_unknown']))
                        for item in criteria.get('profiles', [])
                    }
                    if not profiles:
                        thresholds[None] = (0, True)
                    stale = latest_stale_snapshot(session, generation, criteria)
                    materialize_search_snapshot(
                        session, profiles, current_key, generation, criteria, thresholds,
                        preserve_snapshot_key=stale.key if stale is not None else None,
                        is_prewarmed=prewarm,
                    )
                elif prewarm and not current_snapshot.is_prewarmed:
                    current_snapshot.is_prewarmed = True
                    current_snapshot.criteria_hash = _search_criteria_hash(criteria)
                    session.commit()
        _finish_search_snapshot_refresh(key, token, requested_at)
    except Exception:
        _LOG.exception("Could not refresh a materialized job search snapshot")
        try:
            _finish_search_snapshot_refresh(key, token, requested_at, retry=True)
        except Exception:
            _LOG.exception("Could not release a failed search snapshot refresh lease")
    return True


def semantic_jobs_page(session: Session, vector: list[float], *,
                        latitude: float, longitude: float, radius_km: float,
                        place: str | None, country: str | None,
                        location_scope: str, work_style: str,
                        company_id: int | None, state: str | None,
                        employer_country: str | None, profile: UserProfile | None,
                        offset: int, limit: int, job_status: str | None,
                        posting_status: str = 'open') -> dict[str, Any]:
    """Return nearby jobs in local-model relevance order without profile-match data."""
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    session.execute(text("SET LOCAL hnsw.max_scan_tuples = 100000"))
    employer_company = aliased(Company)
    distance = SemanticSearchDocument.embedding.cosine_distance(vector)
    statement = (select(Job.id, distance.label("semantic_distance"))
        .select_from(Job)
        .join(SemanticSearchDocument, and_(
            SemanticSearchDocument.entity_type == "job",
            SemanticSearchDocument.entity_id == Job.id,
            SemanticSearchDocument.model_revision == SEMANTIC_MODEL_KEY,
        ))
        .join(JobFeed, JobFeed.id == Job.feed_id)
        .join(Company, Company.id == JobFeed.company_id)
        .outerjoin(employer_company, Job.employer_company_id == employer_company.id))
    filters = [posting_status_filter(posting_status)]
    if employer_country:
        scope_candidates = [german_company_filter(employer_company), german_company_filter(Company),
                            Company.source == "default_job_feed",
                            Job.locations.any(func.lower(JobLocation.country_code) == "de"),
                            _job_germany_text_candidates()]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    elif state:
        scope_candidates = [Company.state == state, employer_company.state == state,
                            _job_state_text_candidates(state)]
        if location_scope in {"remote", "area_remote"} or work_style == "remote":
            scope_candidates.append(_remote_job_candidate())
        filters.append(or_(*scope_candidates))
    if company_id is not None:
        filters.append(or_(JobFeed.company_id == company_id, Job.employer_company_id == company_id))
    if work_style == "remote":
        filters.append(or_(Job.work_arrangement == "remote",
                           and_(Job.is_remote.is_(True), Job.work_arrangement.is_(None))))
    elif work_style in {"hybrid", "onsite"}:
        filters.append(Job.work_arrangement == work_style)
    if not state and not employer_country:
        filters.append(_job_area_candidates(latitude, longitude, radius_km, place, location_scope))
    candidate_filters = list(filters)
    filtered_statement = statement.where(*candidate_filters)
    candidate_count = int(session.scalar(select(func.count()).select_from(
        filtered_statement.order_by(None).with_only_columns(Job.id).subquery())) or 0)
    if profile is not None and candidate_count and session.scalar(select(JobApplication.id).where(
            JobApplication.profile_id == profile.id).limit(1)) is not None:
        candidate_id_rows = filtered_statement.order_by(None).with_only_columns(Job.id).subquery()
        for start in range(0, candidate_count, 400):
            candidate_job_ids = session.scalars(select(candidate_id_rows.c.id).order_by(candidate_id_rows.c.id)
                                                .offset(start).limit(400)).all()
            if not candidate_job_ids:
                break
            candidate_jobs = session.scalars(select(Job).where(Job.id.in_(candidate_job_ids)).options(
                joinedload(Job.feed).joinedload(JobFeed.company),
                joinedload(Job.employer_company), selectinload(Job.locations),
            )).unique().all()
            _index_unlinked_application_copies(session, [profile.id], candidate_jobs)
    batch_size = max(1_000, limit * 10)
    candidate_ids: list[int] = []
    score_by_id: dict[int, float] = {}
    fetch_offset = 0
    winners = []
    scoped_count = 0
    geo_filtered: dict[str, int] = {}
    ordered_ids: list[int] = []
    status_by_job = {}
    status_filtered_ids: list[int] = []
    scopes: dict[int, dict[str, Any]] = {}
    while fetch_offset < candidate_count:
        candidates = session.execute(filtered_statement.order_by(
            distance, Job.id).offset(fetch_offset).limit(batch_size)).all()
        if not candidates:
            break
        candidate_ids.extend(int(row.id) for row in candidates)
        score_by_id.update({int(row.id): float(row.semantic_distance) for row in candidates})
        fetch_offset += len(candidates)
        winners, scoped_count, geo_filtered = _scoped_candidate_winners(
            session, candidate_ids, latitude, longitude, radius_km, place, country,
            location_scope, state, employer_country, posting_status,
        )
        scopes = dict(winners)
        ordered_ids = sorted((job_id for job_id, _scope in winners),
                             key=lambda job_id: (score_by_id.get(job_id, 1.0), job_id))
        if job_status is not None:
            missing_status_ids = [job_id for job_id in ordered_ids if job_id not in status_by_job]
            if missing_status_ids:
                for start in range(0, len(missing_status_ids), 400):
                    id_batch = missing_status_ids[start:start + 400]
                    status_jobs = session.scalars(select(Job).where(Job.id.in_(id_batch)).options(
                        joinedload(Job.feed).joinedload(JobFeed.company),
                        joinedload(Job.employer_company), selectinload(Job.locations),
                    )).unique().all()
                    states = canonical_application_states_for_jobs(session, profile.id, status_jobs)
                    status_by_job.update({job_id: states.get(job_id) for job_id in id_batch})
            seen_status_groups = set()
            status_filtered_ids = []
            for job_id in ordered_ids:
                application = status_by_job.get(job_id)
                if application is None or application.status != job_status:
                    continue
                group_key = application.vacancy_group_id or application.job_id
                if group_key in seen_status_groups:
                    continue
                seen_status_groups.add(group_key)
                status_filtered_ids.append(job_id)
            enough_results = len(status_filtered_ids) >= offset + limit
        else:
            enough_results = len(ordered_ids) >= offset + limit
        if enough_results or len(candidates) < batch_size:
            break
    application_counts = dict.fromkeys(APPLICATION_STATUSES, 0)
    if profile is not None:
        application_job = aliased(Job)
        application_group = aliased(JobVacancyGroup)
        application_rows = session.execute(select(
            Job.id, JobApplication.status,
        ).select_from(JobApplication).join(application_job, application_job.id == JobApplication.job_id)
            .outerjoin(application_group, application_group.job_id == application_job.id)
            .join(Job, or_(Job.id == application_job.id,
                           and_(application_group.vacancy_group_id.is_not(None),
                                Job.id.in_(select(JobVacancyGroup.job_id).where(
                                    JobVacancyGroup.vacancy_group_id == application_group.vacancy_group_id)))))
            .join(JobFeed, JobFeed.id == Job.feed_id)
            .join(Company, Company.id == JobFeed.company_id)
            .outerjoin(employer_company, Job.employer_company_id == employer_company.id)
            .where(
                JobApplication.profile_id == profile.id, *filters,
                Job.id.in_(select(SemanticSearchDocument.entity_id).where(
                    SemanticSearchDocument.entity_type == "job",
                    SemanticSearchDocument.model_revision == SEMANTIC_MODEL_KEY,
                )),
            )).all()
        application_status_by_job = {int(job_id): status for job_id, status in application_rows}
        if application_status_by_job:
            application_winners, _, _ = _scoped_candidate_winners(
                session, list(application_status_by_job), latitude, longitude, radius_km,
                place, country, location_scope, state, employer_country, posting_status,
            )
            for job_id, _scope in application_winners:
                status = application_status_by_job.get(job_id)
                if status in application_counts:
                    application_counts[status] += 1
    complete_scan = fetch_offset >= candidate_count
    if job_status is None:
        total = len(ordered_ids) if complete_scan else candidate_count
        page_ids = ordered_ids[offset:offset + limit]
    else:
        total = len(status_filtered_ids) if complete_scan else candidate_count
        page_ids = status_filtered_ids[offset:offset + limit]
    jobs = session.scalars(select(Job).where(Job.id.in_(page_ids)).options(
        joinedload(Job.feed).joinedload(JobFeed.company), joinedload(Job.employer_company),
        selectinload(Job.locations),
    )).unique().all() if page_ids else []
    applications_by_job = (canonical_application_states_for_jobs(session, profile.id, jobs)
                           if profile is not None else {})
    jobs_by_id = {job.id: job for job in jobs}
    items = []
    for job_id in page_ids:
        job = jobs_by_id.get(job_id)
        if job is None:
            continue
        item = job_json(job)
        item["match_kind"] = scopes[job_id].get("match_kind")
        item["geography"] = scopes[job_id]
        item["semantic_score"] = round(1 - score_by_id.get(job_id, 1.0), 4)
        if profile is not None:
            item["profile_id"] = profile.id
            item["profile_name"] = profile.name
            application = applications_by_job.get(job_id)
            item["application"] = application_json(application) if application else None
        items.append(item)
    return {
        "items": items, "total": total, "total_is_upper_bound": not complete_scan,
        "offset": offset, "limit": limit,
        "profile_id": profile.id if profile else None,
        "profile_ids": [profile.id] if profile else [],
        "application_counts": application_counts,
        "counts": {"source": candidate_count, "scoped": len(winners),
                   "filtered": {**dict.fromkeys(("profile_conflict", "below_score", "uncertain"), 0),
                                **geo_filtered},
                   "duplicates_removed": max(0, scoped_count - len(winners)),
                   "recommended": 0, "possible": 0, "unlikely": 0},
        "coverage": {"source_count": candidate_count,
                     "note": "Observed indexed postings; semantic results are ranked by text similarity."},
        "location": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km,
                     "city": place, "country_code": country},
        "location_scope": location_scope,
        "rule": ("work location in Germany, with a German-employer fallback when location is missing or unspecified" if employer_country else
                 "work location in " + state + ", with an employer-state fallback when location is missing or unspecified" if state else
                 ("explicitly remote within country scope" if location_scope == "remote" else
                  "mapped or parsed work location within radius" if location_scope == "area" else
                  "mapped or parsed work location within radius OR explicitly remote within country scope")),
        "state": state, "country": employer_country, "search_mode": "semantic",
        "semantic_window": fetch_offset, "semantic_candidates": len(candidate_ids),
        "snapshot_stale": False,
    }
