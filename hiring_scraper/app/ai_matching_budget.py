"""Durable global rolling five-hour reservation for every matching provider attempt."""
from __future__ import annotations

import os
from datetime import timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from hiring_scraper.app.models import AIMatchingCall, utcnow


def call_limit():
    try:
        value = int(os.getenv('RADIUS_MATCH_AI_CALLS_PER_5H', '50'))
    except ValueError:
        return 0
    return max(0, min(value, 1000))


def reserve(factory, *, limit=None, now=None):
    """Commit a slot before inference; failures and restarts do not refund it.

    The composite primary key prevents replicas reserving the same slot. No
    applicant facts, prompts or credentials are stored in this accounting table.
    """
    limit = call_limit() if limit is None else limit
    if limit <= 0:
        return False
    instant = (now or utcnow()).astimezone(timezone.utc)
    cutoff = instant - timedelta(hours=5)
    for _ in range(limit + 1):
        try:
            with factory() as session:
                # Delete only expired attempts in the same transaction as the
                # reservation. Active slots retain their uniqueness under races.
                session.execute(delete(AIMatchingCall).where(AIMatchingCall.reserved_at <= cutoff))
                last_slot = session.scalar(select(func.max(AIMatchingCall.slot)).where(AIMatchingCall.day == 'rolling')) or 0
                used = session.scalar(select(func.count()).select_from(AIMatchingCall).where(AIMatchingCall.reserved_at > cutoff)) or 0
                if used >= limit:
                    return False
                session.add(AIMatchingCall(day='rolling', slot=last_slot + 1, reserved_at=instant))
                session.commit()
                return True
        except IntegrityError:
            continue  # A replica claimed that slot; re-read the durable count.
    return False


def status(factory, *, now=None):
    instant = (now or utcnow()).astimezone(timezone.utc)
    cutoff = instant - timedelta(hours=5)
    with factory() as session:
        used = session.scalar(select(func.count()).select_from(AIMatchingCall).where(AIMatchingCall.reserved_at > cutoff)) or 0
    limit = call_limit()
    return {'limit': limit, 'used': used, 'remaining': max(0, limit - used), 'window_hours': 5}
