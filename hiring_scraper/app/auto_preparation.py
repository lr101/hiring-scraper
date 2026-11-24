"""Prepare the highest ranked new applications in the background."""
from __future__ import annotations

import fcntl
import logging
import os
import re
import time

from fastapi import HTTPException
from sqlalchemy import select

from hiring_scraper.app import (
    application_documents_v2 as documents,
    application_preparation_queue, codex_runtime,
)
from hiring_scraper.app.applications import canonical_application_states
from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.job_search import profile_match_index_stale
from hiring_scraper.app.models import Job, UserProfile

LOG = logging.getLogger(__name__)
RETRY_SECONDS = 3600


def document_language(posting, variants):
    """Follow the same posting-language heuristic as the preparation page."""
    text = posting or ''
    if re.search(r'(?:apply|application|cv|resume|cover letter)[^.\n]{0,90}\bin (?:german|deutsch)\b|bewerbung[^.\n]{0,90}auf deutsch', text, re.I):
        language = 'de'
    elif re.search(r'(?:apply|application|cv|resume|cover letter)[^.\n]{0,90}\bin english\b|bewerbung[^.\n]{0,90}auf englisch', text, re.I):
        language = 'en'
    else:
        german = re.findall(r'(?<!\w)(?:wir|suchen|einen|eine|für|und|sie|ihre|aufgaben|kenntnisse|bewerbung|unser)(?!\w)', text, re.I)
        english = re.findall(r'(?<!\w)(?:we|are|looking|for|and|you|your|the|experience|application|our)(?!\w)', text, re.I)
        language = 'de' if len(german) > len(english) else 'en'
    if variants and language not in variants:
        return 'de' if 'de' in variants else 'en'
    return language


def _queue_structured(session, profile, job, language, current):
    if not (profile.cv.variants or {}).get(language, {}).get('document'):
        return False  # Let the applicant review the imported base CV first.
    if current and not current.get('automatic'):
        return False
    from hiring_scraper.app.application_pipeline import PreparationInput, input_digest

    fields, _ = documents.snapshot_inputs(session, profile.id, job.id, language)
    digest = input_digest(PreparationInput(**fields))
    latest = {kind: documents.latest(session, profile.id, job.id, kind) for kind in documents.KINDS}
    if any(row and row.origin != 'ai' for row in latest.values()):
        return False
    if all(row and row.input_digest == digest and not row.stale_inputs for row in latest.values()):
        return False
    profile_id, job_id = profile.id, job.id
    versions = {kind: row.version if row else 0 for kind, row in latest.items()}
    session.rollback()
    application_preparation_queue.enqueue_task(
        profile_id, job_id, 'prepare',
        {'job_id': job_id, 'language': language, 'mode': 'full', 'model': codex_runtime.selected_model() or ''},
        automatic=True, extra={'base_versions': versions},
    )
    return True


def prepare_once():
    root = codex_runtime._root()
    codex_runtime._prepare(root)
    fd = os.open(root / 'automatic-preparation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if not codex_runtime.status()['enabled']:
            return False
        quota = codex_runtime.subscription_usage()
        if not quota.get('allowed'):
            return False
        with SessionLocal() as session:
            if application_preparation_queue.has_active(session):
                return False

            def status(profile_id, job_id):
                return application_preparation_queue.status(session, profile_id, job_id)

            profiles = session.scalars(select(UserProfile).order_by(UserProfile.id)).all()
            for profile in profiles:
                if not profile.cv or profile_match_index_stale(profile):
                    continue
                rows = canonical_application_states(session, [profile.id])
                rows = sorted((row for row in rows if row.status == 'new'),
                              key=lambda row: (row.match_rank, row.discovered_rank, row.posted_rank, row.id), reverse=True)[:25]
                for row in rows:
                    current = status(profile.id, row.job_id)
                    if current and current.get('kind') not in application_preparation_queue.V2_KINDS:
                        current = None
                    if current and (current['state'] in ('running', 'cancelling', 'cancelled') or
                                    (current['state'] == 'completed' and not current.get('automatic')) or
                                    (current['state'] == 'failed' and time.time() - current.get('updated_at', 0) < RETRY_SECONDS)):
                        continue
                    job = session.get(Job, row.job_id)
                    if not job:
                        continue
                    language = document_language(job.description or job.title, profile.cv.variants or {})
                    try:
                        if _queue_structured(session, profile, job, language, current):
                            return True
                    except (HTTPException, application_preparation_queue.TaskError) as error:
                        LOG.info('Automatic preparation skipped profile=%s job=%s (%s)',
                                 profile.id, row.job_id, type(error).__name__)
        return False
    finally:
        os.close(fd)
