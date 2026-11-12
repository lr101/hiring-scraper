"""Shared helpers for materialized job search results and profile match state."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from tempfile import SpooledTemporaryFile
from typing import Callable

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.board_scope import geographic_scope
from hiring_scraper.app.application_deduplication import (
    consolidate_application_duplicates, index_vacancy_identities, reserve_vacancy_identity_writer,
)
from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.models import (
    Company, Job, JobApplication, JobFeed, JobLocation, JobSearchState, MatchDirtyJob,
    MatchSemanticDocument, ProfileLLMScore, ProfileMatchProgress, ProfileJobMatch, UserProfile, utcnow,
    reserve_job_source_rows,
)
from hiring_scraper.matching import VERSION, match_job
from hiring_scraper.app.profile_semantics import cached_semantic_evidence
from hiring_scraper.app.profile_llm import (VERSION as LLM_VERSION, apply_cached_assessment,
    load_matching_documents as load_semantic_documents, profile_data)
from hiring_scraper.semantic_matching import VERSION as SEMANTIC_VERSION
from hiring_scraper.app.requirement_evidence import SCORING_VERSION as REQUIREMENT_SCORING_VERSION

_PENDING_MATCH_MAX_BYTES = 1_048_576
_MATCH_JOB_BATCH_SIZE = 500
_MATCH_PROFILE_BATCH_SIZE = 100
_MATCH_WORK_PAGE_SIZE = 100


class ProfileIndexChanged(RuntimeError):
    """Raised when profile preferences changed while their match index was rebuilt."""


_JOB_MATCH_FIELDS = {
    "feed_id", "employer_company_id", "title", "url", "description", "location_text",
    "is_remote", "work_arrangement", "employment_type", "schedule", "department",
    "seniority", "date_posted", "salary", "raw_metadata", "enrichment", "first_seen_at",
    "is_active",
}


def job_search_signature(job: Job) -> str:
    """Stable signature of values used by matching and search scope."""
    fields = {name: getattr(job, name) for name in _JOB_MATCH_FIELDS}
    locations = [(
        location.label, location.latitude, location.longitude,
        location.precision, location.country_code,
    ) for location in job.locations]
    fields["locations"] = sorted(locations, key=lambda row: json.dumps(row, default=str))
    return json.dumps(fields, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def replace_job_locations(job: Job, rows: list[dict]) -> None:
    """Reuse equal locations so unchanged feed scans do not dirty search state."""
    def key(value):
        return tuple(value.get(name) for name in
                     ("label", "latitude", "longitude", "precision", "country_code"))

    previous: dict[tuple, list[JobLocation]] = {}
    for location in job.locations:
        previous.setdefault(key({
            "label": location.label,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "precision": location.precision,
            "country_code": location.country_code,
        }), []).append(location)
    updated = []
    for row in rows:
        identity = key(row)
        matches = previous.get(identity)
        if matches:
            updated.append(matches.pop())
        else:
            updated.append(JobLocation(**{name: row.get(name) for name in
                ("label", "latitude", "longitude", "precision", "country_code")}))
    job.locations[:] = updated


def _insert_for(session: Session, model):
    if session.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    return insert(model)


def _profile_index_version(profile: UserProfile) -> str:
    payload = json.dumps({
        "matcher": VERSION,
        "semantic": SEMANTIC_VERSION,
        "llm": LLM_VERSION,
        "requirement_scoring": REQUIREMENT_SCORING_VERSION,
        "llm_preferences": profile_data(profile),
        "preferences": profile.matching_preferences,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def profile_match_index_stale(profile: UserProfile) -> bool:
    return profile.match_index_version != _profile_index_version(profile)


def _match_worker_owner(session: Session) -> bool:
    return (session.get_bind().dialect.name != 'postgresql' or
            bool(session.scalar(text('SELECT pg_try_advisory_xact_lock(85793011, 4)'))))


def refresh_profile_match_indexes_once() -> bool:
    """Commit one inventory page, rotating stale profiles between steps."""
    with SessionLocal() as session:
        if not _match_worker_owner(session):
            return False
        profiles = session.scalars(select(UserProfile).order_by(UserProfile.id)).all()
        progress = {row.profile_id: row for row in session.scalars(select(ProfileMatchProgress))}
        stale = [p for p in profiles if profile_match_index_stale(p)]
        if not stale:
            return False
        # Unstarted profiles first, then least recently advanced. No profile can
        # monopolize the worker while new jobs continue arriving.
        profile = min(stale, key=lambda p: (p.id in progress,
            progress[p.id].advanced_at.isoformat() if p.id in progress else '', p.id))
        version = _profile_index_version(profile)
        state = progress.get(profile.id)
        checkpoint = ((state.version, state.cursor, state.high_water, state.advanced_at)
                      if state is not None else None)
        if state is None or state.version != version:
            state = ProfileMatchProgress(profile_id=profile.id, version=version, cursor=0,
                high_water=int(session.scalar(select(func.max(Job.id))) or 0))
            # Persist only after the publication verifies the profile below.
        ids = session.scalars(select(Job.id).where(
            Job.id > state.cursor, Job.id <= state.high_water).order_by(Job.id)
            .limit(_MATCH_WORK_PAGE_SIZE)).all()
        _refresh_application_matches_for_jobs(session, ids, profile_ids=[profile.id], verify_sources=True)
        locked = session.scalar(select(UserProfile).where(UserProfile.id == profile.id)
            .with_for_update().execution_options(populate_existing=True))
        if locked is None or _profile_index_version(locked) != version:
            raise ProfileIndexChanged('Profile changed during inventory page')
        persisted = session.execute(select(ProfileMatchProgress.version, ProfileMatchProgress.cursor,
            ProfileMatchProgress.high_water, ProfileMatchProgress.advanced_at).where(
                ProfileMatchProgress.profile_id == profile.id)).one_or_none()
        if (tuple(persisted) if persisted is not None else None) != checkpoint:
            raise ProfileIndexChanged('Backfill checkpoint was reset during matching')
        # Explicit rematch may keep the same preference hash. Verify checkpoint
        # identity as well so an in-flight page cannot restore a deleted cursor.
        state.cursor = ids[-1] if ids else state.high_water
        state.advanced_at = utcnow()
        if len(ids) < _MATCH_WORK_PAGE_SIZE or state.cursor >= state.high_water:
            locked.match_index_version = version
            session.execute(delete(ProfileMatchProgress).where(ProfileMatchProgress.profile_id == profile.id))
        else:
            session.merge(state)
        session.commit()
        return True


def refresh_dirty_job_matches_once() -> bool:
    """Compute without row locks, then acknowledge exactly the verified revisions."""
    with SessionLocal() as session:
        if not _match_worker_owner(session):
            return False
        pending = session.execute(select(MatchDirtyJob.job_id, MatchDirtyJob.revision)
            .order_by(MatchDirtyJob.queued_at, MatchDirtyJob.job_id).limit(_MATCH_WORK_PAGE_SIZE)).all()
        if not pending:
            return False
        _refresh_application_matches_for_jobs(session, [row.job_id for row in pending], verify_sources=True)
        for job_id, revision in pending:
            session.execute(delete(MatchDirtyJob).where(MatchDirtyJob.job_id == job_id,
                                                       MatchDirtyJob.revision == revision))
        session.commit()
        return True


def _batch_source_snapshot(session: Session, ids, profile_ids) -> str:
    """Read source/evidence values directly, bypassing the ORM identity cache."""
    job_feeds = select(Job.feed_id).where(Job.id.in_(ids))
    feed_companies = select(JobFeed.company_id).where(JobFeed.id.in_(job_feeds))
    employers = select(Job.employer_company_id).where(Job.id.in_(ids))
    statements = [
        select(Job.id, *(getattr(Job, name) for name in sorted(_JOB_MATCH_FIELDS)))
            .where(Job.id.in_(ids)).order_by(Job.id),
        select(JobLocation.__table__).where(JobLocation.job_id.in_(ids)).order_by(JobLocation.id),
        select(JobFeed.id, JobFeed.company_id, JobFeed.provider, JobFeed.tenant, JobFeed.source_revision)
            .where(JobFeed.id.in_(job_feeds)).order_by(JobFeed.id),
        select(Company.id, Company.name, Company.domain, Company.state, Company.country_code,
               Company.category, Company.source)
            .where(Company.id.in_(employers) | Company.id.in_(feed_companies)).order_by(Company.id),
        select(MatchSemanticDocument.__table__).where(MatchSemanticDocument.job_id.in_(ids) |
            MatchSemanticDocument.profile_id.in_(profile_ids)).order_by(MatchSemanticDocument.id),
        select(ProfileLLMScore.__table__).where(ProfileLLMScore.job_id.in_(ids),
            ProfileLLMScore.profile_id.in_(profile_ids)).order_by(ProfileLLMScore.profile_id, ProfileLLMScore.job_id),
    ]
    return json.dumps([[list(row) for row in session.execute(query)] for query in statements], default=str,
                      sort_keys=True, separators=(',', ':'))


def refresh_application_matches_for_jobs(session: Session, job_ids, *, before_publish=None) -> None:
    """Production callers enqueue; SQLite/manual workflows stay self contained."""
    if session.get_bind().dialect.name != 'postgresql':
        _refresh_application_matches_for_jobs(session, job_ids, before_publish=before_publish)
        return
    ids = sorted({int(job_id) for job_id in job_ids if job_id is not None})
    if not ids:
        return
    session.flush()
    reserve_job_source_rows(session, ids)
    if before_publish is not None:
        # Only owners of affected saved stages participate in short retirement.
        owners = select(JobApplication.profile_id).where(JobApplication.job_id.in_(ids))
        session.scalars(select(UserProfile).where(UserProfile.id.in_(owners))
                        .order_by(UserProfile.id).with_for_update()).all()
        before_publish(session)
    from uuid import uuid4
    statement = _insert_for(session, MatchDirtyJob).values([
        {'job_id': job_id, 'revision': uuid4().hex, 'queued_at': utcnow()} for job_id in ids])
    session.execute(statement.on_conflict_do_update(index_elements=['job_id'],
        set_={'revision': statement.excluded.revision}))


def application_match_allowed(job: Job, profile: UserProfile, result: dict) -> bool:
    """Apply the profile's default area and match threshold before creating user state."""
    preferences = profile.matching_preferences
    area = preferences.get("search_area") or {}
    try:
        latitude = float(area.get("latitude", 49.0068705))
        longitude = float(area.get("longitude", 8.4034195))
        radius_km = float(area.get("radius_km", 15))
    except (TypeError, ValueError):
        latitude, longitude, radius_km = 49.0068705, 8.4034195, 15
    scope = geographic_scope(
        job, latitude, longitude, radius_km,
        area.get("city"), area.get("country_code") or "DE",
    )
    defaults = preferences.get("matching_defaults") or {}
    min_score = defaults.get("min_match_score", 30)
    try:
        min_score = int(min_score)
    except (TypeError, ValueError):
        min_score = 30
    include_unknown = defaults.get("include_unknown", True) is not False
    uncertain = bool(result.get("uncertain") or scope.get("unknowns"))
    return bool(scope["eligible"] and result.get("eligible") and
                result.get("score", 0) >= min_score and
                (include_unknown or not uncertain))


def upsert_new_match_states(session: Session, matches: list[dict]) -> None:
    """Create New for newly visible matches without replacing user decisions."""
    if not matches:
        return
    insert = _insert_for(session, JobApplication)
    matches = [{**match, "auto_created": True} for match in matches]
    for start in range(0, len(matches), 80):
        statement = insert.values(matches[start:start + 80])
        session.execute(statement.on_conflict_do_nothing(
            index_elements=["profile_id", "job_id"],
        ))


def _upsert_profile_matches(session: Session, matches: list[dict]) -> None:
    if not matches:
        return
    insert = _insert_for(session, ProfileJobMatch)
    for start in range(0, len(matches), 100):
        statement = insert.values(matches[start:start + 100])
        session.execute(statement.on_conflict_do_update(
            index_elements=["profile_id", "job_id"],
            set_={
                "match_score": statement.excluded.match_score,
                "match_tier": statement.excluded.match_tier,
                "match_uncertain": statement.excluded.match_uncertain,
            },
        ))


def _encode_pending_datetime(item):
    if isinstance(item, datetime):
        return {"__datetime__": item.isoformat()}
    raise TypeError(f"Cannot spool {type(item).__name__}")


def _decode_pending_datetime(item):
    if set(item) == {"__datetime__"}:
        return datetime.fromisoformat(item["__datetime__"])
    return item


def _write_pending(spool, value) -> None:
    spool.write(json.dumps(value, default=_encode_pending_datetime, separators=(",", ":")))
    spool.write("\n")


def _iter_pending(spool):
    spool.seek(0)
    for line in spool:
        yield json.loads(line, object_hook=_decode_pending_datetime)


def _iter_pending_batches(spool, batch_size: int = 500):
    batch = []
    for row in _iter_pending(spool):
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def ensure_profile_match_indexes(session: Session, profiles: list[UserProfile]) -> bool:
    """Backfill stale profile indexes once; preserve user stages as matches change."""
    profile_ids = sorted({profile.id for profile in profiles})
    if not profile_ids:
        return False
    current_profiles = session.scalars(
        select(UserProfile).where(UserProfile.id.in_(profile_ids)).order_by(UserProfile.id)
        .execution_options(populate_existing=True)
    ).all()
    stale_profiles = [profile for profile in current_profiles
                      if profile.match_index_version != _profile_index_version(profile)]
    if not stale_profiles:
        profiles_by_id = {profile.id: profile for profile in current_profiles}
        profiles[:] = [profiles_by_id[profile_id] for profile_id in profile_ids]
        return False

    expected_versions = {profile.id: _profile_index_version(profile) for profile in stale_profiles}
    search_generation = int(session.scalar(select(func.max(JobSearchState.id))) or 0)
    # Matching every active job can take much longer than a database lock
    # timeout. Compute from the snapshot first, then lock and verify the
    # profiles before replacing their derived rows.
    active_job_ids = session.scalars(
        select(Job.id).where(Job.is_active.is_(True)).order_by(Job.id)
    ).all()
    pending_matches: list[dict] = []
    pending_states: list[dict] = []
    now = utcnow()
    with (SpooledTemporaryFile(max_size=_PENDING_MATCH_MAX_BYTES, mode="w+t", encoding="utf-8") as match_spool,
          SpooledTemporaryFile(max_size=_PENDING_MATCH_MAX_BYTES, mode="w+t", encoding="utf-8") as state_spool):
        for start in range(0, len(active_job_ids), _MATCH_JOB_BATCH_SIZE):
            jobs = session.scalars(
                select(Job).where(Job.id.in_(active_job_ids[start:start + _MATCH_JOB_BATCH_SIZE])).options(
                    joinedload(Job.feed).joinedload(JobFeed.company),
                    joinedload(Job.employer_company),
                    selectinload(Job.locations),
                )
            ).unique().all()
            semantic_documents = load_semantic_documents(session, jobs, stale_profiles)
            for job in jobs:
                source = job_input(job)
                enrichment = current_enrichment(job)
                for profile in stale_profiles:
                    semantic = cached_semantic_evidence(source, profile.matching_preferences,
                                                        job.id, profile.id, semantic_documents)
                    result = match_job(source, profile.matching_preferences, enrichment,
                                       **({'semantic': semantic} if semantic else {}))
                    result = apply_cached_assessment(result, job, profile, semantic_documents)
                    if not result.get("eligible"):
                        continue
                    pending_matches.append({
                        "profile_id": profile.id,
                        "job_id": job.id,
                        "match_score": result.get("score", 0),
                        "match_tier": result.get("fit_tier", "possible"),
                        "match_uncertain": bool(result.get("uncertain")),
                    })
                    if application_match_allowed(job, profile, result):
                        pending_states.append({
                            "profile_id": profile.id,
                            "job_id": job.id,
                            "status": "new",
                            "previous_status": None,
                            "created_at": now,
                            "updated_at": now,
                        })
                    if len(pending_matches) >= 500:
                        for row in pending_matches:
                            _write_pending(match_spool, row)
                        pending_matches.clear()
                    if len(pending_states) >= 500:
                        for row in pending_states:
                            _write_pending(state_spool, row)
                        pending_states.clear()
        for row in pending_matches:
            _write_pending(match_spool, row)
        for row in pending_states:
            _write_pending(state_spool, row)

        # Keep the legacy full rebuild safe for explicit PostgreSQL callers too.
        # Serialize identity unions before row locks; never hold the generation
        # commit fence through derived publication. New inventory is durably
        # queued, while existing source/evidence writers share these row locks.
        if session.get_bind().dialect.name == 'postgresql':
            if not reserve_vacancy_identity_writer(session, wait=False):
                raise ProfileIndexChanged('Vacancy identities are being indexed')
            index_vacancy_identities(session, active_job_ids)
            reserve_job_source_rows(session, active_job_ids)
        # Do not publish if preferences or inventory changed during computation.
        locked_profiles = session.scalars(
            select(UserProfile).where(UserProfile.id.in_(list(expected_versions))).order_by(UserProfile.id)
            .with_for_update().execution_options(populate_existing=True)
        ).all()
        current_by_id = {profile.id: profile for profile in current_profiles}
        locked_by_id = {profile.id: profile for profile in locked_profiles}
        for profile_id in expected_versions:
            if profile_id in locked_by_id:
                current_by_id[profile_id] = locked_by_id[profile_id]
            else:
                current_by_id.pop(profile_id, None)
        for profile_id, expected_version in expected_versions.items():
            profile = current_by_id.get(profile_id)
            if profile is None or _profile_index_version(profile) != expected_version:
                raise ProfileIndexChanged("Profile changed while its matches were being rebuilt")

        current_generation = int(session.scalar(select(func.max(JobSearchState.id))) or 0)
        if current_generation != search_generation:
            raise ProfileIndexChanged("Job inventory changed while profile matches were being rebuilt")

        # The locked query refreshes ORM identities in place, but construct from
        # the merged map so this remains correct if a caller passed detached rows.
        stable_profiles = [
            current_by_id[profile_id]
            for profile_id in expected_versions
            if profile_id in current_by_id
            and current_by_id[profile_id].match_index_version != expected_versions[profile_id]
        ]
        stable_ids = {profile.id for profile in stable_profiles}
        if stable_ids:
            session.execute(delete(ProfileJobMatch).where(ProfileJobMatch.profile_id.in_(stable_ids)))
            session.execute(delete(JobApplication).where(
                JobApplication.profile_id.in_(stable_ids),
                JobApplication.status == "new",
                JobApplication.auto_created.is_(True),
            ))
            # Replace rows from the verified snapshot in bounded batches.
            for batch in _iter_pending_batches(match_spool):
                _upsert_profile_matches(session, [row for row in batch if row["profile_id"] in stable_ids])
            for batch in _iter_pending_batches(state_spool):
                upsert_new_match_states(session, [row for row in batch if row["profile_id"] in stable_ids])
            consolidate_application_duplicates(session, stable_ids)
            for profile in stable_profiles:
                profile.match_index_version = expected_versions[profile.id]

        profiles[:] = [current_by_id.get(profile_id) for profile_id in profile_ids]
        session.flush()
        return bool(stable_ids)


def _refresh_application_matches_for_jobs(
    session: Session,
    job_ids: set[int] | list[int],
    *,
    before_publish: Callable[[Session], None] | None = None,
    profile_ids: list[int] | None = None,
    verify_sources: bool = False,
) -> None:
    """Refresh derived matches and create New only for newly visible default-area matches."""
    ids = sorted({int(job_id) for job_id in job_ids if job_id is not None})
    if not ids:
        return
    # Make pending source updates visible before computing incremental matches.
    session.flush()
    profile_query = select(UserProfile).order_by(UserProfile.id).execution_options(populate_existing=True)
    if profile_ids is not None:
        profile_query = profile_query.where(UserProfile.id.in_(profile_ids))
    profiles = session.scalars(profile_query).all()
    if not profiles:
        if not reserve_vacancy_identity_writer(session, wait=False):
            raise ProfileIndexChanged('Vacancy identities are being indexed')
        index_vacancy_identities(session, ids)
        if before_publish is not None:
            before_publish(session)
        return
    profile_versions = {profile.id: _profile_index_version(profile) for profile in profiles}
    source_snapshot = _batch_source_snapshot(session, ids, list(profile_versions)) if verify_sources else None
    search_generation = int(session.scalar(select(func.max(JobSearchState.id))) or 0)
    now = utcnow()
    with SpooledTemporaryFile(max_size=_PENDING_MATCH_MAX_BYTES, mode="w+t", encoding="utf-8") as pending_spool:
        for start in range(0, len(ids), _MATCH_JOB_BATCH_SIZE):
            job_batch = ids[start:start + _MATCH_JOB_BATCH_SIZE]
            jobs = session.scalars(
                select(Job).where(Job.id.in_(job_batch)).options(
                    joinedload(Job.feed).joinedload(JobFeed.company),
                    joinedload(Job.employer_company),
                    selectinload(Job.locations),
                ).execution_options(populate_existing=True)
            ).unique().all()
            semantic_documents = load_semantic_documents(session, jobs, profiles)
            matching_jobs = [
                (job, job_input(job), current_enrichment(job))
                for job in jobs if job.is_active
            ]
            for profile_start in range(0, len(profiles), _MATCH_PROFILE_BATCH_SIZE):
                profile_batch = profiles[profile_start:profile_start + _MATCH_PROFILE_BATCH_SIZE]
                matches: list[dict] = []
                states: list[dict] = []
                qualifying_job_ids: dict[int, list[int]] = {
                    profile.id: [] for profile in profile_batch
                }
                for job, source, enrichment in matching_jobs:
                    for profile in profile_batch:
                        semantic = cached_semantic_evidence(source, profile.matching_preferences,
                                                            job.id, profile.id, semantic_documents)
                        result = match_job(source, profile.matching_preferences, enrichment,
                                           **({'semantic': semantic} if semantic else {}))
                        result = apply_cached_assessment(result, job, profile, semantic_documents)
                        if not result.get("eligible"):
                            continue
                        matches.append({
                            "profile_id": profile.id,
                            "job_id": job.id,
                            "match_score": result.get("score", 0),
                            "match_tier": result.get("fit_tier", "possible"),
                            "match_uncertain": bool(result.get("uncertain")),
                        })
                        if application_match_allowed(job, profile, result):
                            qualifying_job_ids[profile.id].append(job.id)
                            states.append({
                                "profile_id": profile.id,
                                "job_id": job.id,
                                "status": "new",
                                "previous_status": None,
                                "created_at": now,
                                "updated_at": now,
                            })
                _write_pending(pending_spool, {
                    "job_ids": job_batch,
                    "profile_ids": [profile.id for profile in profile_batch],
                    "matches": matches,
                    "states": states,
                    "qualifying_job_ids": qualifying_job_ids,
                })

        # One invocation can span many batches. Keep the profile rows unlocked
        # through computation and identity indexing. A busy union writer defers
        # this transaction before it can block source editors or saved stages.
        if not reserve_vacancy_identity_writer(session, wait=False):
            raise ProfileIndexChanged('Vacancy identities are being indexed')
        affected_vacancy_groups = index_vacancy_identities(session, ids)
        if verify_sources:
            reserve_job_source_rows(session, ids)
        current_profiles = session.scalars(select(UserProfile).where(
            UserProfile.id.in_(list(profile_versions))
        ).order_by(UserProfile.id).with_for_update().execution_options(populate_existing=True)).all()
        if before_publish is not None:
            # Source retirement must update application rows only after profile
            # locks are held, and before stale automatic New rows are retracted.
            before_publish(session)
        # Assessment publication also owns profile locks, so a committed change
        # observed here cannot race past the derived writes that follow.
        if verify_sources:
            if _batch_source_snapshot(session, ids, list(profile_versions)) != source_snapshot:
                raise ProfileIndexChanged("Batch sources or evidence changed during matching")
            if any(_profile_index_version(profile) != profile_versions[profile.id] for profile in current_profiles):
                raise ProfileIndexChanged("Profile changed during matching")
        elif int(session.scalar(select(func.max(JobSearchState.id))) or 0) != search_generation:
            raise ProfileIndexChanged("Search evidence changed during incremental matching")
        stable_profiles = [profile for profile in current_profiles
                           if _profile_index_version(profile) == profile_versions[profile.id]]
        stable_ids = {profile.id for profile in stable_profiles}
        if not stable_ids:
            return

        for pending in _iter_pending(pending_spool):
            job_batch = pending["job_ids"]
            profile_batch_ids = [profile_id for profile_id in pending["profile_ids"]
                                 if profile_id in stable_ids]
            if not profile_batch_ids:
                continue
            matches = [match for match in pending["matches"] if match["profile_id"] in stable_ids]
            states = [state for state in pending["states"] if state["profile_id"] in stable_ids]
            qualifying_job_ids = {
                int(profile_id): job_ids
                for profile_id, job_ids in pending["qualifying_job_ids"].items()
            }
            session.execute(delete(ProfileJobMatch).where(
                ProfileJobMatch.job_id.in_(job_batch),
                ProfileJobMatch.profile_id.in_(profile_batch_ids),
            ))
            # Retract only untouched automatic New entries that are no longer a
            # qualifying match. User-created New rows and every acted stage remain.
            for profile_id in profile_batch_ids:
                qualifying = qualifying_job_ids[profile_id]
                stale = delete(JobApplication).where(
                    JobApplication.profile_id == profile_id,
                    JobApplication.job_id.in_(job_batch),
                    JobApplication.status == "new",
                    JobApplication.auto_created.is_(True),
                )
                if qualifying:
                    stale = stale.where(JobApplication.job_id.not_in(qualifying))
                session.execute(stale)
            _upsert_profile_matches(session, matches)
            upsert_new_match_states(session, states)
        consolidate_application_duplicates(
            session, stable_ids, vacancy_group_ids=affected_vacancy_groups,
        )


def clear_profile_match_scores(session: Session, profile_id: int) -> None:
    """Remove derived matches and untouched New rows after profile edits."""
    session.execute(delete(ProfileMatchProgress).where(ProfileMatchProgress.profile_id == profile_id))
    session.execute(delete(ProfileJobMatch).where(ProfileJobMatch.profile_id == profile_id))
    session.execute(delete(JobApplication).where(
        JobApplication.profile_id == profile_id,
        JobApplication.status == "new",
        JobApplication.auto_created.is_(True),
    ))
