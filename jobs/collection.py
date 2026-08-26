from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from jobs.collectors import BotProtectionDetected, Collector, CollectorRegistry, RawJob
from jobs.collectors.employers import register_employer_collectors
from jobs.matching import normalize_text, update_job_match
from jobs.models import CareerSource, CrawlRun, Job, JobMatch, SearchProfile, UserJobState

collector_registry = CollectorRegistry()
register_employer_collectors(collector_registry)


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
    """Collect one source while a database-backed run lock is held."""
    run, acquired = _start_run(source=source)
    if not acquired:
        return run

    collector: Collector | None = None
    try:
        collector = registry.create(source)
        result = collector.collect()
    except BotProtectionDetected as error:
        return _finish_blocked_run(
            run=run,
            source_id=source.id,
            error=error,
            requests_made=_collector_requests(collector),
        )
    except Exception as error:
        return _finish_failed_run(
            run=run,
            source_id=source.id,
            error=error,
            requests_made=_collector_requests(collector),
        )

    try:
        with transaction.atomic():
            locked_source = (
                CareerSource.objects.select_for_update().select_related("company").get(pk=source.pk)
            )
            owned_run = (
                CrawlRun.objects.select_for_update()
                .filter(
                    pk=run.pk,
                    status=CrawlRun.Status.RUNNING,
                )
                .first()
            )
            if owned_run is None:
                return CrawlRun.objects.get(pk=run.pk)
            created, updated, closed = _apply_successful_collection(
                source=locked_source,
                raw_jobs=result.raw_jobs,
            )
            now = timezone.now()
            locked_source.last_success_at = now
            locked_source.consecutive_failures = 0
            locked_source.last_job_count = len(result.raw_jobs)
            locked_source.save(
                update_fields=["last_success_at", "consecutive_failures", "last_job_count"]
            )
            owned_run.status = CrawlRun.Status.SUCCESS
            owned_run.finished_at = now
            owned_run.jobs_seen = len(result.raw_jobs)
            owned_run.jobs_created = created
            owned_run.jobs_updated = updated
            owned_run.jobs_closed = closed
            owned_run.requests_made = result.requests_made
            owned_run.save(
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
    except Exception as error:
        return _finish_failed_run(
            run=run,
            source_id=source.id,
            error=error,
            requests_made=result.requests_made,
        )
    return owned_run


def _start_run(*, source: CareerSource) -> tuple[CrawlRun, bool]:
    while True:
        try:
            with transaction.atomic():
                return CrawlRun.objects.create(source=source), True
        except IntegrityError:
            recovered = False
            with transaction.atomic():
                locked_source = CareerSource.objects.select_for_update().get(pk=source.pk)
                running = (
                    CrawlRun.objects.select_for_update()
                    .filter(source=locked_source, status=CrawlRun.Status.RUNNING)
                    .first()
                )
                if running is not None:
                    now = timezone.now()
                    threshold = timedelta(seconds=settings.COLLECTION_STALE_RUN_SECONDS)
                    if running.started_at <= now - threshold:
                        locked_source.last_failure_at = now
                        locked_source.consecutive_failures += 1
                        locked_source.save(
                            update_fields=["last_failure_at", "consecutive_failures"]
                        )
                        running.status = CrawlRun.Status.FAILED
                        running.finished_at = now
                        running.error = (
                            "Recovered stale run after "
                            f"{settings.COLLECTION_STALE_RUN_SECONDS} seconds."
                        )
                        running.save(update_fields=["status", "finished_at", "error"])
                        recovered = True
                    else:
                        return running, False
            if recovered:
                continue


def _collector_requests(collector: Collector | None) -> int:
    return collector.requests_made if collector is not None else 0


def _apply_successful_collection(
    *, source: CareerSource, raw_jobs: list[RawJob]
) -> tuple[int, int, int]:
    created = 0
    updated = 0
    seen_external_ids: set[str] = set()
    for raw_job in raw_jobs:
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

    closed = _record_successful_misses(
        source=source,
        seen_external_ids=seen_external_ids,
        closed_at=timezone.now(),
    )
    return created, updated, closed


def collect_enabled_sources(*, registry: CollectorRegistry = collector_registry) -> list[CrawlRun]:
    """Run every source that is enabled and not awaiting an unblock decision."""
    sources = CareerSource.objects.filter(is_enabled=True, blocked_at__isnull=True).order_by("id")
    return [collect_source(source=source, registry=registry) for source in sources]


def _finish_blocked_run(
    *, run: CrawlRun, source_id: int, error: BotProtectionDetected, requests_made: int
) -> CrawlRun:
    now = timezone.now()
    with transaction.atomic():
        source = CareerSource.objects.select_for_update().get(pk=source_id)
        owned_run = (
            CrawlRun.objects.select_for_update()
            .filter(
                pk=run.pk,
                status=CrawlRun.Status.RUNNING,
            )
            .first()
        )
        if owned_run is None:
            return CrawlRun.objects.get(pk=run.pk)
        source.blocked_at = now
        source.save(update_fields=["blocked_at"])
        owned_run.status = CrawlRun.Status.BLOCKED
        owned_run.finished_at = now
        owned_run.requests_made = requests_made
        owned_run.error = str(error)
        owned_run.save(update_fields=["status", "finished_at", "requests_made", "error"])
    return owned_run


def _finish_failed_run(
    *, run: CrawlRun, source_id: int, error: Exception, requests_made: int
) -> CrawlRun:
    now = timezone.now()
    with transaction.atomic():
        source = CareerSource.objects.select_for_update().get(pk=source_id)
        owned_run = (
            CrawlRun.objects.select_for_update()
            .filter(
                pk=run.pk,
                status=CrawlRun.Status.RUNNING,
            )
            .first()
        )
        if owned_run is None:
            return CrawlRun.objects.get(pk=run.pk)
        source.last_failure_at = now
        source.consecutive_failures += 1
        source.save(update_fields=["last_failure_at", "consecutive_failures"])
        owned_run.status = CrawlRun.Status.FAILED
        owned_run.finished_at = now
        owned_run.requests_made = requests_made
        owned_run.error = str(error)
        owned_run.save(update_fields=["status", "finished_at", "requests_made", "error"])
    return owned_run


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
            JobMatch.objects.filter(job=job).delete()
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
