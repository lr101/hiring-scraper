"""Read-only reuse of local job analyses and pair evidence during matching.

The job analysis worker writes ``job_analyses`` (one per posting) and
``match_evidence`` (one per profile/job pair). Matching only reads them: HTTP
requests and the match worker never run model inference. A stale row (job or
CV edited since) is still used until its replacement is published, so edits do
not hide existing matches; a job that was never analysed stays pending.
"""
from __future__ import annotations

import hashlib

from typing import Any

from sqlalchemy import select

from hiring_scraper.app.models import JobAnalysis, MatchEvidence


class MatchDocuments(dict):
    """Analyses keyed by ('job', job_id) and evidence keyed by ('pair', profile_id, job_id)."""


USABLE_ANALYSIS_STATUSES = ('ok', 'stale')


def _usable_analysis(row) -> bool:
    # Status decides usability so the SQL snapshot query can apply the same rule.
    return getattr(row, 'status', None) in USABLE_ANALYSIS_STATUSES and isinstance(getattr(row, 'payload', None), dict)


def load_match_documents(session, jobs, profiles) -> MatchDocuments:
    jobs, profiles = list(jobs), list(profiles)
    documents = MatchDocuments()
    if session is None or not jobs:
        return documents
    job_ids = sorted({job.id for job in jobs if job.id is not None})
    profile_ids = sorted({profile.id for profile in profiles if profile.id is not None})
    for start in range(0, len(job_ids), 800):
        batch = job_ids[start:start + 800]
        for row in session.scalars(select(JobAnalysis).where(JobAnalysis.job_id.in_(batch))):
            if _usable_analysis(row):
                documents[('job', row.job_id)] = row
        if profile_ids:
            for row in session.scalars(select(MatchEvidence).where(
                    MatchEvidence.job_id.in_(batch), MatchEvidence.profile_id.in_(profile_ids))):
                documents[('pair', row.profile_id, row.job_id)] = row
    return documents


def evidence_stamp(analysis_hash: str | None, evidence_analysis_hash: str | None,
                   evidence_units_hash: str | None, evidence_version: str | None = None) -> str:
    """Identity of the model outputs behind a stored match (empty parts when absent)."""
    # The full evidence version (reranker revision, E5 model) is hashed, not truncated,
    # so any model change marks stored matches outdated.
    version = hashlib.sha256((evidence_version or '').encode('utf-8')).hexdigest()[:16] if evidence_version else ''
    return ':'.join([analysis_hash or '', (evidence_analysis_hash or '')[:16], (evidence_units_hash or '')[:16],
                     version])


def pair_rows(documents, job_id: int | None, profile_id: int | None) -> tuple[Any, Any]:
    if not documents or job_id is None or profile_id is None:
        return None, None
    analysis = documents.get(('job', job_id))
    evidence = documents.get(('pair', profile_id, job_id))
    if analysis is None or not _usable_analysis(analysis):
        return None, None
    # Evidence refers to duty/requirement indexes of one exact analysis.
    if evidence is None or evidence.analysis_hash != analysis.result_hash or not isinstance(evidence.payload, dict):
        return analysis, None
    return analysis, evidence


def documents_stamp(documents, job_id: int | None, profile_id: int | None) -> str:
    analysis, evidence = pair_rows(documents, job_id, profile_id)
    return evidence_stamp(
        getattr(analysis, "result_hash", None) if analysis is not None else None,
        getattr(evidence, 'analysis_hash', None), getattr(evidence, 'units_hash', None),
        getattr(evidence, 'version', None),
    )


def pair_inputs(documents, job_id: int | None, profile_id: int | None) -> tuple[dict | None, dict | None]:
    analysis, evidence = pair_rows(documents, job_id, profile_id)
    if analysis is None or evidence is None:
        return None, None
    return analysis.payload, evidence.payload
