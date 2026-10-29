"""Prepare the highest ranked new applications in the background."""
from __future__ import annotations

import fcntl
import logging
import os
import re
import time

from fastapi import HTTPException
from sqlalchemy import select

from hiring_scraper.app import application_generation as generation, application_generation_jobs as jobs, codex_runtime
from hiring_scraper.app.applications import canonical_application_states
from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.job_search import profile_match_index_stale
from hiring_scraper.app.models import Job, UserProfile
from hiring_scraper.app.profiles import cv_metadata

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


def _active_job(root):
    for path in jobs._directory(root).glob('*.json'):
        match = re.fullmatch(r'(\d+)-(\d+)\.json', path.name)
        if match:
            with jobs._lock(root, int(match[1]), int(match[2])):
                current = jobs._read(root, int(match[1]), int(match[2]))
            if current and current['state'] in ('running', 'cancelling'):
                return True
    return False


def prepare_once():
    root = codex_runtime._root()
    jobs._directory(root)
    fd = os.open(root / 'automatic-preparation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if _active_job(root) or not codex_runtime.status()['enabled']:
            return False
        quota = codex_runtime.subscription_usage()
        if not quota.get('allowed') or quota.get('used_percent') is None or quota['used_percent'] >= 50:
            return False
        with SessionLocal() as session:
            profiles = session.scalars(select(UserProfile).order_by(UserProfile.id)).all()
            for profile in profiles:
                if not profile.cv or profile_match_index_stale(profile):
                    continue
                rows = canonical_application_states(session, [profile.id])
                rows = sorted((row for row in rows if row.status == 'new'),
                              key=lambda row: (row.match_rank, row.discovered_rank, row.posted_rank, row.id), reverse=True)[:25]
                for row in rows:
                    if jobs.load_draft(root, profile.id, row.job_id):
                        continue
                    current = jobs.status(root, profile.id, row.job_id)
                    if current and (current['state'] in ('running', 'cancelling', 'cancelled') or
                                    (current['state'] == 'completed' and not current.get('automatic')) or
                                    (current['state'] == 'failed' and time.time() - current.get('updated_at', 0) < RETRY_SECONDS)):
                        continue
                    job = session.get(Job, row.job_id)
                    if not job:
                        continue
                    request = generation.PrepareApplicationRequest(job_id=job.id,
                        cv_updated_at=cv_metadata(profile.cv)['updated_at'], profile_updated_at=profile.updated_at.isoformat(),
                        language=document_language(job.description or job.title, profile.cv.variants or {}),
                        model=codex_runtime.selected_model())
                    try:
                        with jobs.profile_lock(root, profile.id):
                            # A manual draft or job may have appeared since the scan.
                            if jobs.load_draft(root, profile.id, job.id):
                                continue
                            current = jobs.status(root, profile.id, job.id)
                            if current and (current['state'] in ('running', 'cancelling', 'cancelled') or
                                            (current['state'] == 'completed' and not current.get('automatic'))):
                                continue
                            _, revision = generation._snapshot(profile.id, request, session)
                            if current and current['state'] == 'completed' and current.get('revision') == revision and current.get('request') == request.model_dump():
                                continue
                            profile_id = profile.id
                            session.rollback()
                            def run(progress, profile_id=profile_id, request=request, revision=revision):
                                with SessionLocal() as worker_session:
                                    return generation._prepare_application(profile_id, request, worker_session,
                                                                           progress=progress, expected_revision=revision)
                            jobs.start(root, profile_id, request.job_id, run, request.model_dump(), revision=revision, automatic=True)
                            return True
                    except HTTPException as exc:
                        LOG.info('Automatic preparation skipped profile=%s job=%s: %s', profile.id, row.job_id, exc.status_code)
                        continue
        return False
    finally:
        os.close(fd)
