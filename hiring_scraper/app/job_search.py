"""Shared helpers for materialized job search results and profile match state."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from tempfile import SpooledTemporaryFile
from typing import Callable

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from hiring_scraper.app import source_changes
from hiring_scraper.app.database import dialect_insert, SessionLocal
from hiring_scraper.app.board_scope import geographic_scope
from hiring_scraper.app.application_deduplication import (
    consolidate_application_duplicates, index_vacancy_identities, reserve_vacancy_identity_writer,
)
from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.match_compute import (
    active_matcher_version,
    compute_match,
    current_score_version,
    load_stored_match_snapshots,
    match_row_payload,
    snapshot_explanation_is_current,
)
from hiring_scraper.app.models import (
    ProfileIndexChanged, hold_profiles,
    Company, Job, JobApplication, JobFeed, JobLocation, JobSearchState, MatchDirtyJob,
    JobAnalysis, MatchEvidence, ProfileMatchProgress, ProfileJobMatch, UserProfile, utcnow,
    reserve_job_source_rows,
)
from hiring_scraper.app.match_documents import load_match_documents
from hiring_scraper.matching_v3 import match_job_v3

match_job = match_job_v3
_DEFAULT_MATCH_JOB = match_job
PRESENTATION_MATCHING_DEFAULT_KEYS = frozenset({"min_match_score", "include_unknown"})

_PENDING_MATCH_MAX_BYTES = 1_048_576
_MATCH_JOB_BATCH_SIZE = 500
_MATCH_PROFILE_BATCH_SIZE = 100
_MATCH_WORK_PAGE_SIZE = 100


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
    if len(updated) != len(job.locations) or any(previous.values()):
        job.locations[:] = updated


def scoring_preferences(matching_preferences: dict) -> dict:
    """Return matching preferences stripped of presentation-only defaults."""
    prefs = dict(matching_preferences or {})
    defaults = prefs.get("matching_defaults")
    if isinstance(defaults, dict):
        scoring_defaults = {
            key: value for key, value in defaults.items()
            if key not in PRESENTATION_MATCHING_DEFAULT_KEYS
        }
        if scoring_defaults:
            prefs["matching_defaults"] = scoring_defaults
        else:
            prefs.pop("matching_defaults", None)
    return prefs


def _profile_index_version(profile: UserProfile) -> str:
    payload = json.dumps({
        "matcher": active_matcher_version(),
        "preferences": scoring_preferences(profile.matching_preferences),
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def profile_match_index_stale(profile: UserProfile) -> bool:
    return profile.match_index_version != _profile_index_version(profile)


def _match_worker_owner(session: Session) -> bool:
    return bool(session.scalar(text('SELECT pg_try_advisory_xact_lock(85793011, 4)')))


def refresh_profile_match_indexes_once() -> bool:
    """Commit one inventory page, rotating stale profiles between steps."""
    with SessionLocal() as session:
        if not _match_worker_owner(session):
            return False
        profiles = session.scalars(select(UserProfile).order_by(UserProfile.id)).all()
        progress = {row.profile_id: row for row in session.scalars(select(ProfileMatchProgress))}
        stale = [p for p in profiles if profile_match_index_stale(p)]
        pending = [p for p in profiles if p.presentation_refresh_pending and not profile_match_index_stale(p)]
        if pending:
            # Self-heal a flag set against an index that has since become current.
            return _apply_pending_presentation_refresh(session, pending[0])
        if not stale:
            return False
        # Unstarted profiles first, then least recently advanced. No profile can
        # monopolize the worker while new jobs continue arriving.
        profile = min(stale, key=lambda p: (p.id in progress,
            progress[p.id].advanced_at.isoformat() if p.id in progress else '', p.id))
        version = _profile_index_version(profile)
        expected_source = {profile.id: profile.source_version}
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
        _refresh_application_matches_for_jobs(session, ids, profile_ids=[profile.id], verify_sources=True,
                                              exclusive_profiles=True)
        # Exclusive: this page may also mark the profile index current below.
        hold_profiles(session, expected_source, wait=False, exclusive=True)
        locked = session.get(UserProfile, profile.id, populate_existing=True)
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
            # SessionLocal disables autoflush: persist the version first, or the
            # snapshot queries below still see the old index and defer every row.
            session.flush()
            if locked.presentation_refresh_pending:
                # A threshold/unknown-policy edit arrived mid-backfill: pages committed
                # before it still hold the old presentation. Re-evaluate New rows now,
                # in the transaction that publishes the index as current.
                reevaluate_profile_new_applications(session, locked)
            session.execute(delete(ProfileMatchProgress).where(ProfileMatchProgress.profile_id == profile.id))
        else:
            session.merge(state)
        session.commit()
        return True


def _apply_pending_presentation_refresh(session: Session, profile: UserProfile) -> bool:
    expected_source = {profile.id: profile.source_version}
    hold_profiles(session, expected_source, wait=False, exclusive=True)
    locked = session.get(UserProfile, profile.id, populate_existing=True)
    if locked is None or not locked.presentation_refresh_pending:
        session.rollback()
        return False
    # reevaluate_profile_new_applications defers again if the index went stale.
    reevaluate_profile_new_applications(session, locked)
    session.commit()
    return True


def refresh_dirty_job_matches_once(session_factory=None) -> bool:
    """Compute without row locks, then acknowledge exactly the verified revisions."""
    with (session_factory or SessionLocal)() as session:
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
        select(JobAnalysis.job_id, JobAnalysis.version, JobAnalysis.status, JobAnalysis.result_hash)
            .where(JobAnalysis.job_id.in_(ids)).order_by(JobAnalysis.job_id),
        select(MatchEvidence.profile_id, MatchEvidence.job_id, MatchEvidence.version,
               MatchEvidence.analysis_hash, MatchEvidence.units_hash)
            .where(MatchEvidence.job_id.in_(ids), MatchEvidence.profile_id.in_(profile_ids))
            .order_by(MatchEvidence.profile_id, MatchEvidence.job_id),
    ]
    return json.dumps([[list(row) for row in session.execute(query)] for query in statements], default=str,
                      sort_keys=True, separators=(',', ':'))


def refresh_application_matches_for_jobs(session: Session, job_ids, *, before_publish=None) -> None:
    """Queue jobs for the match worker; run ``before_publish`` (job retirement) now."""
    ids = sorted({int(job_id) for job_id in job_ids if job_id is not None})
    if not ids:
        return
    session.flush()
    reserve_job_source_rows(session, ids)
    # Declares the jobs: drops stale vectors and queues the rematch.
    source_changes.jobs_changed(session, ids)
    if before_publish is not None:
        # Only owners of affected saved stages participate in short retirement.
        hold_profiles(session, session.scalars(select(JobApplication.profile_id).distinct()
                                               .where(JobApplication.job_id.in_(ids))).all())
        before_publish(session)


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
    min_score = defaults.get("min_match_score", 0)
    try:
        min_score = int(min_score)
    except (TypeError, ValueError):
        min_score = 0
    include_unknown = defaults.get("include_unknown", True) is not False
    uncertain = bool(result.get("uncertain") or scope.get("unknowns"))
    # Jobs the local models have not analysed yet stay hidden until they are.
    return bool(not result.get("pending") and scope["eligible"] and result.get("eligible") and
                result.get("score", 0) >= min_score and
                (include_unknown or not uncertain))


def evaluate_stored_new_application(
    job: Job,
    profile: UserProfile,
    match_row,
    *,
    is_current: bool,
) -> str:
    """Pure decision for presentation-only New-row re-evaluation.

    Returns ``'defer'`` when the stored match is dirty, stale, legacy, or unfaithful
    (leaving existing applications untouched for rematching), ``'qualifies'`` when the
    current stored row passes ``application_match_allowed``, or ``'retract'`` otherwise.
    """
    if not is_current or match_row is None:
        return "defer"
    explanation = getattr(match_row, "explanation", None)
    if not isinstance(explanation, dict):
        return "defer"
    stored_result = {
        "eligible": bool(explanation.get("eligible", True)),
        "pending": bool(explanation.get("pending", False)),
        "score": match_row.match_score,
        "uncertain": bool(match_row.match_uncertain),
    }
    if application_match_allowed(job, profile, stored_result):
        return "qualifies"
    return "retract"


def upsert_new_match_states(session: Session, matches: list[dict]) -> None:
    """Create New for newly visible matches without replacing user decisions."""
    if not matches:
        return
    insert = dialect_insert(session)(JobApplication)
    matches = [{**match, "auto_created": True} for match in matches]
    for start in range(0, len(matches), 80):
        statement = insert.values(matches[start:start + 80])
        session.execute(statement.on_conflict_do_nothing(
            index_elements=["profile_id", "job_id"],
        ))


def _upsert_profile_matches(session: Session, matches: list[dict]) -> None:
    if not matches:
        return
    insert = dialect_insert(session)(ProfileJobMatch)
    for start in range(0, len(matches), 100):
        statement = insert.values(matches[start:start + 100])
        session.execute(statement.on_conflict_do_update(
            index_elements=["profile_id", "job_id"],
            set_={
                "match_score": statement.excluded.match_score,
                "match_tier": statement.excluded.match_tier,
                "match_uncertain": statement.excluded.match_uncertain,
                "explanation": statement.excluded.explanation,
                "score_version": statement.excluded.score_version,
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
    expected_sources = {profile.id: profile.source_version for profile in stale_profiles}
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
            match_documents = load_match_documents(session, jobs, stale_profiles)
            score_version = current_score_version()
            matcher = None if match_job is _DEFAULT_MATCH_JOB else match_job
            for job in jobs:
                source = job_input(job)
                enrichment = current_enrichment(job)
                for profile in stale_profiles:
                    result = compute_match(
                        job, profile, match_documents,
                        source=source, enrichment=enrichment, matcher=matcher,
                    )
                    if not result.get("eligible"):
                        continue
                    pending_matches.append(
                        match_row_payload(
                            profile.id, job.id, result, score_version,
                            job=job, documents=match_documents, source=source,
                        )
                    )
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

        # Serialize identity unions before row locks; never hold the generation
        # commit fence through derived publication. New inventory is durably
        # queued, while existing source/evidence writers share these row locks.
        if not reserve_vacancy_identity_writer(session, wait=False):
            raise ProfileIndexChanged('Vacancy identities are being indexed')
        index_vacancy_identities(session, active_job_ids)
        reserve_job_source_rows(session, active_job_ids)
        # Do not publish if preferences or inventory changed during computation.
        hold_profiles(session, expected_sources, exclusive=True)
        locked_profiles = session.scalars(
            select(UserProfile).where(UserProfile.id.in_(list(expected_versions))).order_by(UserProfile.id)
            .execution_options(populate_existing=True)
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
                profile.presentation_refresh_pending = False

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
    exclusive_profiles: bool = False,
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
    profile_sources = {profile.id: profile.source_version for profile in profiles}
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
            match_documents = load_match_documents(session, jobs, profiles)
            score_version = current_score_version()
            matcher = None if match_job is _DEFAULT_MATCH_JOB else match_job
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
                        result = compute_match(
                            job, profile, match_documents,
                            source=source, enrichment=enrichment, matcher=matcher,
                        )
                        if not result.get("eligible"):
                            continue
                        matches.append(
                            match_row_payload(
                                profile.id, job.id, result, score_version,
                                job=job, documents=match_documents, source=source,
                            )
                        )
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
        # Workers verify versions and defer behind an in-flight profile edit;
        # other callers publish only the profiles that did not change.
        held = hold_profiles(session, profile_sources if verify_sources else list(profile_sources),
                             wait=not verify_sources, exclusive=exclusive_profiles)
        current_profiles = session.scalars(select(UserProfile).where(
            UserProfile.id.in_(list(held))
        ).order_by(UserProfile.id).execution_options(populate_existing=True)).all()
        if before_publish is not None:
            # Source retirement must update application rows only after profile
            # locks are held, and before stale automatic New rows are retracted.
            before_publish(session)
        if verify_sources:
            if _batch_source_snapshot(session, ids, list(profile_versions)) != source_snapshot:
                raise ProfileIndexChanged("Batch sources or evidence changed during matching")
            if any(_profile_index_version(profile) != profile_versions[profile.id] for profile in current_profiles):
                raise ProfileIndexChanged("Profile changed during matching")
        elif int(session.scalar(select(func.max(JobSearchState.id))) or 0) != search_generation:
            raise ProfileIndexChanged("Search evidence changed during incremental matching")
        stable_profiles = [profile for profile in current_profiles
                           if held[profile.id] == profile_sources[profile.id]
                           and _profile_index_version(profile) == profile_versions[profile.id]]
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


def reevaluate_profile_new_applications(
    session: Session,
    profile: UserProfile,
    *,
    batch_size: int = 200,
) -> dict[str, int]:
    """Re-evaluate automatic Journey New rows from current stored ProfileJobMatch rows.

    Used when only presentation preferences (``min_match_score``, ``include_unknown``)
    change: shares the exact ``application_match_allowed`` predicate with workers,
    never alters user-decided rows, and defers dirty/stale/legacy rows to rematching
    instead of trusting or retracting them.
    """
    expected_index_version = _profile_index_version(profile)
    # The caller holds the profile row lock. Read the committed index state under
    # it: a backfill page may have published the index after this profile was loaded.
    session.flush()
    session.refresh(profile, ['match_index_version'])
    if profile.match_index_version != expected_index_version:
        # Backfill in progress: pages committed before this edit keep the old
        # presentation. Remember it; the worker re-evaluates once the index is current.
        profile.presentation_refresh_pending = True
        return {"evaluated": 0, "added": 0, "retracted": 0, "deferred": 0}
    now = utcnow()
    cursor = 0
    evaluated = 0
    added = 0
    retracted = 0
    deferred = 0
    while True:
        # Page by id only; explanations are loaded once, by the snapshot query below.
        batch_job_ids = list(session.scalars(
            select(ProfileJobMatch.job_id)
            .join(Job, Job.id == ProfileJobMatch.job_id)
            .where(
                ProfileJobMatch.profile_id == profile.id,
                Job.is_active.is_(True),
                ProfileJobMatch.job_id > cursor,
            )
            .order_by(ProfileJobMatch.job_id)
            .limit(batch_size)
        ))
        if not batch_job_ids:
            break
        cursor = batch_job_ids[-1]
        jobs_by_id = {job.id: job for job in session.scalars(
            select(Job).where(Job.id.in_(batch_job_ids)).options(selectinload(Job.locations)))}
        snapshots = load_stored_match_snapshots(
            session, [(profile.id, job_id) for job_id in batch_job_ids],
        )
        retract_ids: list[int] = []
        stale_to_queue: list[int] = []
        states: list[dict] = []
        for job in (jobs_by_id[job_id] for job_id in batch_job_ids if job_id in jobs_by_id):
            snapshot = snapshots.get((profile.id, job.id))
            is_current = snapshot_explanation_is_current(
                snapshot,
                job,
                profile,
                expected_profile_index_version=expected_index_version,
            )
            decision = evaluate_stored_new_application(
                job,
                profile,
                snapshot.match if snapshot is not None else None,
                is_current=is_current,
            )
            if decision == "defer":
                deferred += 1
                if snapshot is None or not snapshot.dirty:
                    stale_to_queue.append(job.id)
                continue
            evaluated += 1
            if decision == "qualifies":
                states.append({
                    "profile_id": profile.id,
                    "job_id": job.id,
                    "status": "new",
                    "previous_status": None,
                    "created_at": now,
                    "updated_at": now,
                })
            else:
                retract_ids.append(job.id)
        if retract_ids:
            stale = delete(JobApplication).where(
                JobApplication.profile_id == profile.id,
                JobApplication.job_id.in_(retract_ids),
                JobApplication.status == "new",
                JobApplication.auto_created.is_(True),
            )
            deleted = session.execute(stale)
            retracted += int(deleted.rowcount or 0)
        if states:
            upsert_new_match_states(session, states)
            added += len(states)
        if stale_to_queue:
            source_changes.queue_stale_rematch(session, stale_to_queue)
    if added:
        consolidate_application_duplicates(session, {profile.id})
    profile.presentation_refresh_pending = False
    return {
        "evaluated": evaluated,
        "added": added,
        "retracted": retracted,
        "deferred": deferred,
    }
