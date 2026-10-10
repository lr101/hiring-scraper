"""Relational model shared by SQLite development and PostgreSQL/PostGIS."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_company_source_record"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    website_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    domain_match_method: Mapped[str | None] = mapped_column(String(60))
    domain_evidence_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_precision: Mapped[str | None] = mapped_column(String(40))
    location_label: Mapped[str | None] = mapped_column(String(255))
    career_url: Mapped[str | None] = mapped_column(Text)
    career_status: Mapped[str] = mapped_column(String(40), nullable=False, default="not_checked")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_discovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovery_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovery_error: Mapped[str | None] = mapped_column(Text)

    feeds: Mapped[list[JobFeed]] = relationship(back_populates="company", cascade="all, delete-orphan")
    discovery_runs: Mapped[list[DiscoveryRun]] = relationship(back_populates="company", cascade="all, delete-orphan")


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    configured_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("configured_locations.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
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
    board_url: Mapped[str | None] = mapped_column(Text)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("job_feeds.id", ondelete="CASCADE"), nullable=False, index=True)
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
    locations: Mapped[list[JobLocation]] = relationship(back_populates="job", cascade="all, delete-orphan")
    applications: Mapped[list[JobApplication]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobLocation(Base):
    __tablename__ = "job_locations"
    __table_args__ = (Index("ix_job_locations_job_id", "job_id"),)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    applications: Mapped[list[JobApplication]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("profile_id", "job_id", name="uq_job_application_profile_job"),
        CheckConstraint("status IN ('new', 'open', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted')",
                        name="ck_job_application_status"),
        Index("ix_job_applications_profile_status", "profile_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    profile: Mapped[UserProfile] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(back_populates="applications")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("job_feeds.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    item_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
