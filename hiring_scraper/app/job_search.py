"""Shared helpers for materialized job search results and profile match state."""
from __future__ import annotations

import hashlib
import json
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from hiring_scraper.app.board_scope import geographic_scope
from hiring_scraper.app.enrichment import current_enrichment, job_input
from hiring_scraper.app.models import (
    Job, JobApplication, JobFeed, JobLocation, JobSearchState, ProfileJobMatch, UserProfile, utcnow,
)
from hiring_scraper.matching import VERSION, match_job


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
        "preferences": profile.matching_preferences,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


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
    pending_match_batches: list[list[dict]] = []
    pending_state_batches: list[list[dict]] = []
    pending_matches: list[dict] = []
    pending_states: list[dict] = []
    now = utcnow()
    for start in range(0, len(active_job_ids), 500):
        jobs = session.scalars(
            select(Job).where(Job.id.in_(active_job_ids[start:start + 500])).options(
                joinedload(Job.feed).joinedload(JobFeed.company),
                joinedload(Job.employer_company),
                selectinload(Job.locations),
            )
        ).unique().all()
        for job in jobs:
            source = job_input(job)
            enrichment = current_enrichment(job)
            for profile in stale_profiles:
                result = match_job(source, profile.matching_preferences, enrichment)
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
                    pending_match_batches.append(pending_matches)
                    pending_matches = []
                if len(pending_states) >= 500:
                    pending_state_batches.append(pending_states)
                    pending_states = []
    if pending_matches:
        pending_match_batches.append(pending_matches)
    if pending_states:
        pending_state_batches.append(pending_states)

    # Do not publish a backfill if preferences changed during the scan. If a
    # competing request already rebuilt the same version, leave its rows alone.
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
        # The pending rows were built from the versions now protected by row
        # locks. Replace only those profiles; another rebuild may have finished
        # while this one was scanning.
        for batch in pending_match_batches:
            _upsert_profile_matches(session, [row for row in batch if row["profile_id"] in stable_ids])
        for batch in pending_state_batches:
            upsert_new_match_states(session, [row for row in batch if row["profile_id"] in stable_ids])
        for profile in stable_profiles:
            profile.match_index_version = expected_versions[profile.id]

    profiles[:] = [current_by_id.get(profile_id) for profile_id in profile_ids]
    session.flush()
    return bool(stable_ids)


def refresh_application_matches_for_jobs(session: Session, job_ids: set[int] | list[int]) -> None:
    """Refresh derived matches and create New only for newly visible default-area matches."""
    ids = sorted({int(job_id) for job_id in job_ids if job_id is not None})
    if not ids:
        return
    # Make pending source updates visible before computing incremental matches.
    session.flush()
    profiles = session.scalars(
        select(UserProfile).order_by(UserProfile.id).execution_options(populate_existing=True)
    ).all()
    if not profiles:
        return
    profile_versions = {profile.id: _profile_index_version(profile) for profile in profiles}
    now = utcnow()
    pending_batches: list[tuple[list[int], list[dict], list[dict], dict[int, list[int]]]] = []
    for start in range(0, len(ids), 500):
        job_batch = ids[start:start + 500]
        jobs = session.scalars(
            select(Job).where(Job.id.in_(job_batch)).options(
                joinedload(Job.feed).joinedload(JobFeed.company),
                joinedload(Job.employer_company),
                selectinload(Job.locations),
            ).execution_options(populate_existing=True)
        ).unique().all()
        matches: list[dict] = []
        states: list[dict] = []
        qualifying_job_ids: dict[int, list[int]] = {profile.id: [] for profile in profiles}
        for job in jobs:
            if not job.is_active:
                continue
            source = job_input(job)
            enrichment = current_enrichment(job)
            for profile in profiles:
                result = match_job(source, profile.matching_preferences, enrichment)
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
        pending_batches.append((job_batch, matches, states, qualifying_job_ids))

    # One invocation can span many batches. Keep the profile rows unlocked
    # through every matching pass, then serialize the derived writes together.
    current_profiles = session.scalars(select(UserProfile).where(
        UserProfile.id.in_(list(profile_versions))
    ).order_by(UserProfile.id).with_for_update().execution_options(populate_existing=True)).all()
    stable_profiles = [profile for profile in current_profiles
                       if _profile_index_version(profile) == profile_versions[profile.id]]
    stable_ids = {profile.id for profile in stable_profiles}
    if not stable_ids:
        return

    for job_batch, matches, states, qualifying_job_ids in pending_batches:
        matches = [match for match in matches if match["profile_id"] in stable_ids]
        states = [state for state in states if state["profile_id"] in stable_ids]
        for profile_start in range(0, len(stable_profiles), 300):
            stable_batch = stable_profiles[profile_start:profile_start + 300]
            stable_batch_ids = [profile.id for profile in stable_batch]
            session.execute(delete(ProfileJobMatch).where(
                ProfileJobMatch.job_id.in_(job_batch),
                ProfileJobMatch.profile_id.in_(stable_batch_ids),
            ))
        # Retract only untouched automatic New entries that are no longer a
        # qualifying match. User-created New rows and every acted stage remain.
        for profile in stable_profiles:
            qualifying = qualifying_job_ids[profile.id]
            stale = delete(JobApplication).where(
                JobApplication.profile_id == profile.id,
                JobApplication.job_id.in_(job_batch),
                JobApplication.status == "new",
                JobApplication.auto_created.is_(True),
            )
            if qualifying:
                stale = stale.where(JobApplication.job_id.not_in(qualifying))
            session.execute(stale)
        _upsert_profile_matches(session, matches)
        upsert_new_match_states(session, states)


def clear_profile_match_scores(session: Session, profile_id: int) -> None:
    """Remove derived matches and untouched New rows after profile edits."""
    session.execute(delete(ProfileJobMatch).where(ProfileJobMatch.profile_id == profile_id))
    session.execute(delete(JobApplication).where(
        JobApplication.profile_id == profile_id,
        JobApplication.status == "new",
        JobApplication.auto_created.is_(True),
    ))
