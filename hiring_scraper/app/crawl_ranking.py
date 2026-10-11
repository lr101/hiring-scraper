"""Evidence-based crawl tiers and deterministic schedules bounded by each cadence."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from hiring_scraper.app.models import Job, JobFeed


def parse_employee_count(value):
    """Read explicit counts or conservative range lower bounds; reject estimates."""
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    number = r'(?:[1-9]\d{0,2}(?:[., ]\d{3})+|[1-9]\d*)'
    match = re.fullmatch(r'(' + number + r')(?:\s*[-–]\s*(' + number + r'))?', text)
    if not match:
        return None
    count = int(re.sub(r'[., ]', '', match[1]))
    if match[2] and int(re.sub(r'[., ]', '', match[2])) < count:
        return None
    return count


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _date(value, *, end_of_day=False):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if end_of_day and len(value) == 10:
            parsed = datetime.combine(parsed.date(), time.max)
        return _aware(parsed)
    except ValueError:
        return None


def _successful_complete(result):
    if not result or result.get('error'):
        return False
    pages = result.get('pages') or []
    boards = result.get('boards') or []
    if any(page.get('fetch_state') != 'ok' for page in pages):
        return False
    if any(board.get('complete') is False or board.get('feed_state') in {'failed','incomplete','schema_error','blocked','throttled'} for board in boards):
        return False
    if result.get('complete') is not None:
        return result['complete'] is True
    limits = result.get('limits') or {}
    return bool(pages and not limits.get('page_budget_reached') and not limits.get('remaining_queue'))


def refresh_company_rank(session, company, now, discovery_result=None, *, fresh_employer_evidence=False):
    """Persist rank from jobs and reliable evidence without treating errors as absence.

    ``irrelevant=True`` is reserved for a completed, evidenced nonemployer check.
    Empty results only reduce cadence. Fresh employer evidence reactivates a pause.
    """
    now = _aware(now)
    result = discovery_result or {}
    evidence = dict(company.size_evidence or {})
    size = result.get('company_size') or {}
    count = parse_employee_count(size.get('employee_count'))
    if count and size.get('source') == 'employer_website' and size.get('source_url'):
        company.employee_count = count
        evidence.update(size)
    jobs = session.scalars(select(Job).join(JobFeed).where(
        JobFeed.company_id == company.id, Job.is_active.is_(True))).all()
    active_jobs = []
    recent = False
    for job in jobs:
        expiry = _date((job.raw_metadata or {}).get('validThrough') or
                       (job.raw_metadata or {}).get('valid_through'), end_of_day=True)
        if expiry and expiry < now:
            continue
        active_jobs.append(job)
        # A malformed explicit posting date is unknown, not newly posted.
        posted = _date(job.date_posted) if job.date_posted else _aware(job.first_seen_at)
        if posted and now - timedelta(days=30) <= posted <= now:
            recent = True
    positive = bool(active_jobs or count or fresh_employer_evidence or
                    result.get('status') in {'jobs_feed_found','jobs_extracted','career_content_found','ats_identified'})
    complete = _successful_complete(result)
    if positive:
        company.crawl_paused = False
        company.irrelevant_checks = 0
        evidence['successful_empty_checks'] = 0
    elif complete:
        evidence['successful_empty_checks'] = int(evidence.get('successful_empty_checks', 0)) + 1
        if result.get('irrelevant') is True:
            company.irrelevant_checks = (company.irrelevant_checks or 0) + 1
            if company.irrelevant_checks >= 2:
                company.crawl_paused = True
        else:
            company.irrelevant_checks = 0
    if company.crawl_paused:
        priority, interval, reason = 0, None, 'Repeated successful checks proved this source is not an employer'
    elif (company.employee_count or 0) >= 250:
        priority, interval, reason = 100, 1, 'Large employer: at least 250 employees'
    elif recent:
        priority, interval, reason = 90, 1, 'Active vacancy posted or first discovered within 30 days'
    elif positive:
        priority, interval, reason = 60, 3, 'Employer or hiring evidence; no recent vacancy'
    elif int(evidence.get('successful_empty_checks', 0)) >= 2:
        priority, interval, reason = 10, 30, 'Repeated complete successful checks found no hiring evidence'
    else:
        priority, interval, reason = 30, 7, 'Employer relevance is still unknown'
    company.size_evidence = evidence
    company.crawl_priority = priority
    company.crawl_interval_days = interval
    company.crawl_reason = reason
    company.ranking_updated_at = now


def _next_due(company, key, now):
    if company.crawl_paused:
        return None
    interval = max(1, company.crawl_interval_days or 7)
    # Spreading within the final fifth preserves a minimum delay and never misses
    # the promised maximum daily / three-day / weekly / monthly revisit cadence.
    fraction = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big') / (2 ** 64 - 1)
    return _aware(now) + timedelta(days=interval * (0.8 + 0.2 * fraction))


def next_company_due(company, now):
    return _next_due(company, f'company:{company.id}:{company.source}:{company.source_id}', now)


def next_feed_due(company, feed, now):
    return _next_due(company, f'feed:{company.id}:{feed.id}:{feed.feed_url}', now)
