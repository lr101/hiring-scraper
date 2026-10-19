"""Relational model shared by SQLite development and PostgreSQL/PostGIS."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, LargeBinary,
    JSON, String, Text, UniqueConstraint, delete, event, false, insert, inspect, select, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from pgvector.sqlalchemy import Vector


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_company_source_record"),
        Index("ix_companies_latitude_longitude", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    website_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    domain_match_method: Mapped[str | None] = mapped_column(String(60))
    domain_evidence_url: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(120), index=True)
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    employee_count: Mapped[int | None] = mapped_column(Integer)
    size_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    crawl_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text('0'), index=True)
    crawl_interval_days: Mapped[int | None] = mapped_column(Integer, default=7, server_default=text('7'))
    crawl_reason: Mapped[str | None] = mapped_column(Text)
    crawl_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    irrelevant_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text('0'))
    ranking_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    homepage_source_tags: Mapped[dict | None] = mapped_column(JSON)
    homepage_resolution_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    homepage_resolution_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    homepage_resolution_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    homepage_resolution_token: Mapped[str | None] = mapped_column(String(36))
    category: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_precision: Mapped[str | None] = mapped_column(String(40))
    location_label: Mapped[str | None] = mapped_column(String(255))
    career_url: Mapped[str | None] = mapped_column(Text)
    career_url_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    career_status: Mapped[str] = mapped_column(String(40), nullable=False, default="not_checked")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_discovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovery_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovery_error: Mapped[str | None] = mapped_column(Text)

    feeds: Mapped[list[JobFeed]] = relationship(back_populates="company", cascade="all, delete-orphan")
    discovery_runs: Mapped[list[DiscoveryRun]] = relationship(back_populates="company", cascade="all, delete-orphan")
    employer_jobs: Mapped[list["Job"]] = relationship(
        back_populates="employer_company", foreign_keys="Job.employer_company_id")


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    pages_checked: Mapped[int | None] = mapped_column(Integer)
    jobs_found: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="discovery_runs")


class ConfiguredLocation(Base):
    __tablename__ = "configured_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    search_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="radius", server_default="radius")
    state: Mapped[str | None] = mapped_column(String(120))
    postcode: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False, default=15)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    discovery_jobs: Mapped[list[DiscoveryJob]] = relationship(back_populates="configured_location")


class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"
    __table_args__ = (
        Index(
            "uq_active_germany_inventory",
            "search_scope",
            unique=True,
            postgresql_where=text("search_scope = 'country' AND stage IN ('company_homepage_discovery', 'career_page_discovery') AND status IN ('scheduled', 'queued', 'running')"),
            sqlite_where=text("search_scope = 'country' AND stage IN ('company_homepage_discovery', 'career_page_discovery') AND status IN ('scheduled', 'queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    configured_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("configured_locations.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    search_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="radius", server_default="radius")
    state: Mapped[str | None] = mapped_column(String(120))
    postcode: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="career_page_discovery")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    companies_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    homepages_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(500))
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_scan_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    location_scan_token: Mapped[str | None] = mapped_column(String(36))
    map_cursor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    location_scan_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)

    configured_location: Mapped[ConfiguredLocation | None] = relationship(back_populates="discovery_jobs")
    companies: Mapped[list[DiscoveryJobCompany]] = relationship(back_populates="discovery_job",
                                                                 cascade="all, delete-orphan")


class DiscoveryJobCompany(Base):
    __tablename__ = "discovery_job_companies"
    __table_args__ = (
        UniqueConstraint("discovery_job_id", "company_id", name="uq_discovery_job_company"),
        Index("ix_discovery_job_companies_claim", "status", "discovery_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discovery_job_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_jobs.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    discovery_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    discovery_job: Mapped[DiscoveryJob] = relationship(back_populates="companies")
    company: Mapped[Company] = relationship()
    discovery_run: Mapped[DiscoveryRun | None] = relationship()


class JobFeed(Base):
    __tablename__ = "job_feeds"
    __table_args__ = (UniqueConstraint("company_id", "provider", "feed_url", name="uq_job_feed_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    tenant: Mapped[str | None] = mapped_column(String(255))
    source_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    pending_search_scopes: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'"))
    board_url: Mapped[str | None] = mapped_column(Text)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    job_count: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="feeds")
    jobs: Mapped[list[Job]] = relationship(back_populates="feed", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("feed_id", "external_id", name="uq_job_feed_external_id"),
        Index("ix_jobs_remote_active", "is_remote", "is_active"),
        Index("ix_jobs_active_id", "id", postgresql_where=text("is_active"),
              sqlite_where=text("is_active = 1")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("job_feeds.id", ondelete="CASCADE"), nullable=False, index=True)
    employer_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    is_remote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    work_arrangement: Mapped[str | None] = mapped_column(String(40))
    employment_type: Mapped[str | None] = mapped_column(String(120))
    schedule: Mapped[str | None] = mapped_column(String(120))
    department: Mapped[str | None] = mapped_column(String(255))
    seniority: Mapped[str | None] = mapped_column(String(120))
    date_posted: Mapped[str | None] = mapped_column(String(80))
    salary: Mapped[str | None] = mapped_column(String(500))
    raw_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enrichment: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    missing_complete_scans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    feed: Mapped[JobFeed] = relationship(back_populates="jobs")
    employer_company: Mapped[Company | None] = relationship(
        back_populates="employer_jobs", foreign_keys=[employer_company_id])
    locations: Mapped[list[JobLocation]] = relationship(back_populates="job", cascade="all, delete-orphan")
    applications: Mapped[list[JobApplication]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobLocation(Base):
    __tablename__ = "job_locations"
    __table_args__ = (
        Index("ix_job_locations_job_id", "job_id"),
        Index("ix_job_locations_latitude_longitude", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    precision: Mapped[str | None] = mapped_column(String(40))
    country_code: Mapped[str | None] = mapped_column(String(2))

    job: Mapped[Job] = relationship(back_populates="locations")


class LocationCache(Base):
    __tablename__ = "location_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    results: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    low_afd_vote_share_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    match_index_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    applications: Mapped[list[JobApplication]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    cv: Mapped[ProfileCV | None] = relationship(back_populates="profile", cascade="all, delete-orphan", uselist=False)

    @property
    def matching_preferences(self) -> dict:
        """Return preferences with versioned matcher settings merged in."""
        preferences = dict(self.preferences or {})
        defaults = dict(preferences.get("matching_defaults") or {})
        defaults["low_afd_vote_share_weight"] = self.low_afd_vote_share_weight
        preferences["matching_defaults"] = defaults
        return preferences


class ProfileCV(Base):
    __tablename__ = "profile_cvs"

    profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, deferred=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    profile: Mapped[UserProfile] = relationship(back_populates="cv")


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("profile_id", "job_id", name="uq_job_application_profile_job"),
        CheckConstraint("status IN ('new', 'open', 'in_gots_hand', 'not_interested', 'waiting_for_reply', 'automated_application', 'interview', 'rejected', 'accepted', 'closed')",
                        name="ck_job_application_status"),
        Index("ix_job_applications_profile_status", "profile_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new")
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    auto_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    profile: Mapped[UserProfile] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(back_populates="applications")


class ApplicationMomentumEvent(Base):
    __tablename__ = "application_momentum_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('applied', 'interview', 'accepted')",
                        name="ck_application_momentum_event_type"),
        Index("ix_application_momentum_events_type_date", "event_type", "occurred_at"),
        Index("ix_application_momentum_events_job", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))


class ProfileJobMatch(Base):
    """Derived, rebuildable profile/job match data, separate from user tracking state."""
    __tablename__ = "profile_job_matches"
    __table_args__ = (
        Index("ix_profile_job_matches_profile_score", "profile_id", "match_score", "job_id"),
        Index("ix_profile_job_matches_job_profile", "job_id", "profile_id"),
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    match_score: Mapped[int] = mapped_column(Integer, nullable=False)
    match_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    match_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False)


class JobSearchState(Base):
    """Append-only change marker; its generated ID is a cheap search generation."""
    __tablename__ = "job_search_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class JobSearchSnapshot(Base):
    """A materialized, ordered result set for one normalized search request."""
    __tablename__ = "job_search_snapshots"
    __table_args__ = (
        Index("ix_job_search_snapshots_criteria_created", "criteria_hash", "created_at"),
    )

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    criteria_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    is_prewarmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class JobSearchRefresh(Base):
    """Durable, debounced work queue for refreshing stale search snapshots."""
    __tablename__ = "job_search_snapshot_refreshes"
    __table_args__ = (
        Index("ix_job_search_snapshot_refresh_claim", "requested_at", "lease_until"),
    )

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    criteria: Mapped[dict] = mapped_column(JSON, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prewarm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class SemanticSearchDocument(Base):
    """Current local-model vector for a searchable company or job record."""
    __tablename__ = "semantic_search_documents"
    __table_args__ = (
        Index("ix_semantic_search_documents_entity_revision", "entity_type", "model_revision"),
    )

    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SemanticSearchRefresh(Base):
    """Durable queue for asynchronous vector indexing and record updates."""
    __tablename__ = "semantic_search_refreshes"
    __table_args__ = (
        Index("ix_semantic_search_refresh_claim", "queued_at", "lease_until"),
    )

    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SemanticSearchIndexState(Base):
    """Tracks when the initial vector backfill is complete for one model/type."""
    __tablename__ = "semantic_search_index_state"

    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    model_revision: Mapped[str] = mapped_column(String(128), primary_key=True)
    backfill_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class JobSearchResult(Base):
    """One scoped, canonical job in a materialized search candidate set."""
    __tablename__ = "job_search_results"
    __table_args__ = (
        Index("ix_job_search_results_snapshot_sort",
              "snapshot_key", "posted_rank", "discovered_rank", "job_id"),
    )

    snapshot_key: Mapped[str] = mapped_column(
        ForeignKey("job_search_snapshots.key", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scope_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    posted_rank: Mapped[float] = mapped_column(Float, nullable=False)
    discovered_rank: Mapped[float] = mapped_column(Float, nullable=False)
    title_sort: Mapped[str] = mapped_column(Text, nullable=False)
    company_sort: Mapped[str] = mapped_column(Text, nullable=False)
    remote_sort: Mapped[bool] = mapped_column(Boolean, nullable=False)


_SEARCH_FIELDS = {
    Company: {"name", "domain", "state", "country_code", "category", "source"},
    JobFeed: {"company_id", "provider", "tenant", "source_revision"},
    Job: {"feed_id", "external_id", "employer_company_id", "title", "url", "description",
          "location_text", "is_remote", "work_arrangement", "employment_type", "schedule",
          "department", "seniority", "date_posted", "salary", "raw_metadata", "enrichment",
          "first_seen_at", "is_active"},
    JobLocation: {"job_id", "label", "latitude", "longitude", "country_code"},
}


def _search_data_changed(session: Session) -> bool:
    for item in (*session.new, *session.deleted):
        if type(item) in _SEARCH_FIELDS:
            return True
    for item in session.dirty:
        fields = _SEARCH_FIELDS.get(type(item))
        if fields is None:
            continue
        state = inspect(item)
        if type(item) is Job and state.attrs.locations.history.has_changes():
            return True
        if not session.is_modified(item, include_collections=False):
            continue
        if any(state.attrs[name].history.has_changes() for name in fields):
            return True
    return False


@event.listens_for(Session, "before_flush")
def _invalidate_job_search_snapshots(session: Session, _flush_context, _instances) -> None:
    """Remember invalidation without taking a global lock during source work."""
    if _search_data_changed(session):
        session.info["job_search_generation_dirty"] = True


@event.listens_for(Session, "before_commit")
def _commit_job_search_generation(session: Session) -> None:
    # Finish domain writes first. Holding the shared marker during a feed/job
    # flush inverted its order with Company/Profile locks in other transactions.
    session.flush()
    if not session.info.get("job_search_generation_dirty"):
        return
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        # Serialize sequence allocation with commit, including an empty table.
        # Otherwise a lower sequence ID committing later can miss invalidation.
        connection.execute(text("SELECT pg_advisory_xact_lock(85793011, 2)"))
    connection.execute(insert(JobSearchState).values())
    generation = connection.execute(select(JobSearchState.id).order_by(JobSearchState.id.desc()).limit(1)).scalar_one()
    connection.execute(delete(JobSearchState).where(JobSearchState.id < generation))


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _reset_job_search_generation_flag(session: Session) -> None:
    session.info.pop("job_search_generation_dirty", None)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("job_feeds.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    search_scope: Mapped[dict | None] = mapped_column(JSON)
    http_status: Mapped[int | None] = mapped_column(Integer)
    item_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
