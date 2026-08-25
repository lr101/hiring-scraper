from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from django.db import transaction
from django.utils import timezone

from jobs.collectors import BotProtectionDetected, CollectorRegistry, RawJob
from jobs.matching import normalize_text, update_job_match
from jobs.models import CareerSource, CrawlRun, Job, SearchProfile, UserJobState

collector_registry = CollectorRegistry()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass(frozen=True, slots=True)
class NormalizedJob:
    raw_job: RawJob
    normalized_title: str
    description_text: str
    country_code: str
    content_hash: str
    fingerprint: str


def normalize_raw_job(*, raw_job: RawJob, company_domain: str) -> NormalizedJob:
    """Fill derived fields consistently before the job is persisted."""
    description_text = raw_job.description_text or _text_from_html(raw_job.description_html)
    country_code = raw_job.country_code.upper()
    normalized_title = normalize_text(raw_job.title)
    content_hash = _hash(
        {
            "application_url": raw_job.application_url,
            "canonical_url": raw_job.canonical_url,
            "city": raw_job.city,
            "country_code": country_code,
            "department": raw_job.department,
            "description": description_text,
            "employment_type": raw_job.employment_type,
            "external_id": raw_job.external_id,
            "industry": raw_job.industry,
            "language_requirement": raw_job.language_requirement,
            "latitude": raw_job.latitude,
            "locations": raw_job.locations,
            "longitude": raw_job.longitude,
            "posted_at": raw_job.posted_at,
            "remote_type": raw_job.remote_type,
            "salary_currency": raw_job.salary_currency,
            "salary_max": raw_job.salary_max,
            "salary_min": raw_job.salary_min,
            "seniority": raw_job.seniority,
            "skills": raw_job.skills,
            "state": raw_job.state,
            "title": normalized_title,
            "expires_at": raw_job.expires_at,
        }
    )
    fingerprint = _hash(
        {
            "company_domain": company_domain.casefold(),
            "city": normalize_text(raw_job.city),
            "country_code": country_code,
            "department": normalize_text(raw_job.department),
            "employment_type": normalize_text(raw_job.employment_type),
            "title": normalized_title,
        }
    )
    return NormalizedJob(
        raw_job=raw_job,
        normalized_title=normalized_title,
        description_text=description_text,
        country_code=country_code,
        content_hash=content_hash,
        fingerprint=fingerprint,
    )


def collect_source(*, source: CareerSource, registry: CollectorRegistry) -> CrawlRun:
    """Collect one source and record an auditable successful crawl run."""
    run = CrawlRun.objects.create(source=source)
    try:
        result = registry.create(source).collect()
    except BotProtectionDetected as error:
        return _finish_blocked_run(run=run, source=source, error=error)
    except Exception as error:
        return _finish_failed_run(run=run, source=source, error=error)
    created = 0
    updated = 0
    seen_external_ids: set[str] = set()
    for raw_job in result.raw_jobs:
        seen_external_ids.add(raw_job.external_id)
        normalized = normalize_raw_job(raw_job=raw_job, company_domain=source.company.domain)
        existing_hash = (
            Job.objects.filter(source=source, external_id=raw_job.external_id)
            .values_list("content_hash", flat=True)
            .first()
        )
        job, was_created = Job.objects.update_or_create(
            source=source,
            external_id=raw_job.external_id,
            defaults=_job_defaults(normalized),
        )
        created += int(was_created)
        updated += int(existing_hash is not None and existing_hash != normalized.content_hash)
        if was_created:
            _propagate_ignored_state(job=job)
        _refresh_matches(job=job)

    now = timezone.now()
    with transaction.atomic():
        closed = _record_successful_misses(
            source=source, seen_external_ids=seen_external_ids, closed_at=now
        )
        source.last_success_at = now
        source.consecutive_failures = 0
        source.last_job_count = len(result.raw_jobs)
        source.save(update_fields=["last_success_at", "consecutive_failures", "last_job_count"])
        run.status = CrawlRun.Status.SUCCESS
        run.finished_at = now
        run.jobs_seen = len(result.raw_jobs)
        run.jobs_created = created
        run.jobs_updated = updated
        run.jobs_closed = closed
        run.requests_made = result.requests_made
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "jobs_seen",
                "jobs_created",
                "jobs_updated",
                "jobs_closed",
                "requests_made",
            ]
        )
    return run


def collect_enabled_sources(*, registry: CollectorRegistry = collector_registry) -> list[CrawlRun]:
    """Run every source that is enabled and not awaiting an unblock decision."""
    sources = CareerSource.objects.filter(is_enabled=True, blocked_at__isnull=True).order_by("id")
    return [collect_source(source=source, registry=registry) for source in sources]


def _finish_blocked_run(
    *, run: CrawlRun, source: CareerSource, error: BotProtectionDetected
) -> CrawlRun:
    now = timezone.now()
    source.blocked_at = now
    source.save(update_fields=["blocked_at"])
    run.status = CrawlRun.Status.BLOCKED
    run.finished_at = now
    run.error = str(error)
    run.save(update_fields=["status", "finished_at", "error"])
    return run


def _finish_failed_run(*, run: CrawlRun, source: CareerSource, error: Exception) -> CrawlRun:
    now = timezone.now()
    source.last_failure_at = now
    source.consecutive_failures += 1
    source.save(update_fields=["last_failure_at", "consecutive_failures"])
    run.status = CrawlRun.Status.FAILED
    run.finished_at = now
    run.error = str(error)
    run.save(update_fields=["status", "finished_at", "error"])
    return run


def _record_successful_misses(
    *, source: CareerSource, seen_external_ids: set[str], closed_at: datetime
) -> int:
    closed = 0
    jobs = source.jobs.filter(closed_at__isnull=True).exclude(external_id__in=seen_external_ids)
    for job in jobs:
        job.missed_runs += 1
        update_fields = ["missed_runs"]
        if job.missed_runs >= 2:
            job.closed_at = closed_at
            update_fields.append("closed_at")
            closed += 1
        job.save(update_fields=update_fields)
        if job.closed_at is not None:
            _refresh_matches(job=job)
    return closed


def _propagate_ignored_state(*, job: Job) -> None:
    user_ids = UserJobState.objects.filter(
        job__fingerprint=job.fingerprint,
        status=UserJobState.Status.IGNORED,
    ).values_list("user_id", flat=True)
    UserJobState.objects.bulk_create(
        [
            UserJobState(user_id=user_id, job=job, status=UserJobState.Status.IGNORED)
            for user_id in set(user_ids)
        ],
        ignore_conflicts=True,
    )


def _refresh_matches(*, job: Job) -> None:
    for profile in SearchProfile.objects.filter(is_enabled=True):
        update_job_match(job=job, profile=profile)


def _job_defaults(normalized: NormalizedJob) -> dict[str, Any]:
    raw_job = normalized.raw_job
    return {
        "canonical_url": raw_job.canonical_url,
        "application_url": raw_job.application_url,
        "title": raw_job.title,
        "normalized_title": normalized.normalized_title,
        "description_html": raw_job.description_html,
        "description_text": normalized.description_text,
        "city": raw_job.city,
        "state": raw_job.state,
        "country_code": normalized.country_code,
        "locations": raw_job.locations,
        "latitude": raw_job.latitude,
        "longitude": raw_job.longitude,
        "remote_type": raw_job.remote_type,
        "employment_type": raw_job.employment_type,
        "seniority": raw_job.seniority,
        "department": raw_job.department,
        "industry": raw_job.industry,
        "salary_min": raw_job.salary_min,
        "salary_max": raw_job.salary_max,
        "salary_currency": raw_job.salary_currency,
        "language_requirement": raw_job.language_requirement,
        "skills": raw_job.skills,
        "posted_at": raw_job.posted_at,
        "expires_at": raw_job.expires_at,
        "content_hash": normalized.content_hash,
        "fingerprint": normalized.fingerprint,
        "raw_payload": raw_job.raw_payload,
        "closed_at": None,
        "missed_runs": 0,
    }


def _text_from_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def _hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
