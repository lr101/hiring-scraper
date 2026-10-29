"""Background local inference and read-only reuse of profile/job section vectors."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from sqlalchemy import delete, exists, or_, select

from hiring_scraper.app.database import SessionLocal
from hiring_scraper.app.models import Job, MatchSemanticDocument, UserProfile, utcnow
from hiring_scraper.semantic_matching import (
    MODEL_KEY, VERSION, job_sections, pack_vectors, profile_sections, semantic_evidence, source_hash,
)

BATCH_JOBS = 16
BATCH_PROFILES = 4


class SemanticDocuments(dict):
    """Vectors and hashes prepared from one matching batch's source snapshot."""
    def __init__(self):
        super().__init__()
        self.source_hashes = {}


def load_semantic_documents(session, jobs, profiles) -> dict:
    jobs, profiles = list(jobs), list(profiles)
    owners = [('job', row.id) for row in jobs] + [('profile', row.id) for row in profiles]
    documents = SemanticDocuments()
    if session is None or not owners:
        return documents
    # Include the version parameter while staying below SQLite's standard 999
    # bind limit, even when the incremental matcher receives many profiles.
    for start in range(0, len(owners), 800):
        batch = owners[start:start + 800]
        rows = session.scalars(select(MatchSemanticDocument).where(or_(
            MatchSemanticDocument.job_id.in_([key for kind, key in batch if kind == 'job']),
            MatchSemanticDocument.profile_id.in_([key for kind, key in batch if kind == 'profile'])),
            MatchSemanticDocument.version == VERSION)).all()
        documents.update({('job', row.job_id) if row.job_id is not None else ('profile', row.profile_id): row
                          for row in rows})
    for job in jobs:
        key = ('job', job.id)
        if key in documents and key not in documents.source_hashes:
            documents.source_hashes[key] = source_hash(job_sections(
                {'description': job.description, 'raw_metadata': job.raw_metadata}))
    for profile in profiles:
        key = ('profile', profile.id)
        if key in documents and key not in documents.source_hashes:
            documents.source_hashes[key] = source_hash(profile_sections(profile.matching_preferences))
    return documents


def cached_semantic_evidence(job: dict, profile: dict, job_id: int, profile_id: int, documents: dict) -> dict | None:
    job_doc = documents.get(('job', job_id))
    profile_doc = documents.get(('profile', profile_id))
    if job_doc is None or profile_doc is None:
        return None
    # Hashes are computed once from the matching batch's source snapshot, never
    # by reparsing both documents for every job x profile comparison.
    hashes = documents.source_hashes if isinstance(documents, SemanticDocuments) else {
        ('job', job_id): source_hash(job_sections(job)),
        ('profile', profile_id): source_hash(profile_sections(profile))}
    if (job_doc.version != VERSION or profile_doc.version != VERSION or
            job_doc.source_hash != hashes.get(('job', job_id)) or
            profile_doc.source_hash != hashes.get(('profile', profile_id))):
        return None
    return semantic_evidence(profile_doc.sections, profile_doc.vectors, job_doc.sections, job_doc.vectors)


def _request_vectors(texts: list[str], kind: str) -> list[list[float]]:
    base = os.getenv('SEMANTIC_EMBEDDING_URL', 'http://127.0.0.1:8001/embed').rstrip('/')
    url = base.removesuffix('/embed') + '/embed/batch'
    request = Request(url, data=json.dumps({'texts': texts, 'kind': kind}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read(2_000_000))
    if payload.get('model') != MODEL_KEY or not isinstance(payload.get('embeddings'), list):
        raise OSError('Local embedding service returned an incompatible model or payload')
    vectors = payload['embeddings']
    if len(vectors) != len(texts):
        raise OSError('Local embedding service returned an incomplete batch')
    try:
        pack_vectors(vectors)
    except (ValueError, TypeError) as error:
        raise OSError('Local embedding service returned invalid vectors') from error
    return vectors


def refresh_semantic_documents_once(*, encode=None) -> bool:
    """Compute a bounded batch outside a DB transaction, then verify before publishing.

    Job vectors are shared across profiles. Empty documents are saved too, so
    missing descriptions or role-only profiles never cause an inference loop.
    """
    work = []
    with SessionLocal() as session:
        if not session.scalar(select(exists(select(UserProfile.id)))):
            return False
        profiles = session.scalars(select(UserProfile).where(~exists(select(1).where(
            MatchSemanticDocument.profile_id == UserProfile.id,
            MatchSemanticDocument.version == VERSION))).order_by(UserProfile.id).limit(BATCH_PROFILES)).all()
        for profile in profiles:
            work.append(('profile', profile.id, profile_sections(profile.matching_preferences)))
        has_facts = any(parts for _, _, parts in work) or session.scalar(select(exists(select(1).where(
            MatchSemanticDocument.profile_id.is_not(None), MatchSemanticDocument.version == VERSION,
            MatchSemanticDocument.vectors != b''))))
        if has_facts:
            jobs = session.scalars(select(Job).where(Job.is_active.is_(True), ~exists(select(1).where(
                MatchSemanticDocument.job_id == Job.id,
                MatchSemanticDocument.version == VERSION))).order_by(Job.first_seen_at.desc(), Job.id.desc())
                .limit(BATCH_JOBS)).all()
            for job in jobs:
                work.append(('job', job.id, job_sections({'description': job.description, 'raw_metadata': job.raw_metadata})))
    if not work:
        return False
    encode = encode or _request_vectors
    encoded = {}
    for kind, prefix in [('profile', 'query'), ('job', 'passage')]:
        owners, texts = [], []
        for entity_type, entity_id, parts in work:
            if entity_type == kind:
                for part in parts:
                    owners.append((entity_type, entity_id))
                    texts.append(part['text'])
        for start in range(0, len(texts), 32):
            vectors = encode(texts[start:start + 32], prefix)
            if len(vectors) != len(texts[start:start + 32]):
                raise ValueError('Incomplete section embedding batch')
            for owner, vector in zip(owners[start:start + 32], vectors):
                encoded.setdefault(owner, []).append(vector)
    for entity_type, entity_id, parts in work:
        owner = MatchSemanticDocument.job_id if entity_type == 'job' else MatchSemanticDocument.profile_id
        model = Job if entity_type == 'job' else UserProfile
        with SessionLocal() as session:
            # Serialize publication with source edits. Holding these locks only
            # during local DB work also prevents saving a vanished source.
            if session.get_bind().dialect.name == 'sqlite':
                # SQLite ignores FOR UPDATE. Reserve its writer before reading
                # the source so an edit cannot commit between verification and
                # publishing the corresponding vectors.
                session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            source_query = select(model).where(model.id == entity_id)
            if entity_type == 'job':
                # Keep job writers serialized without blocking FK KEY SHARE
                # checks while an index rebuild owns the profile row.
                source_query = source_query.with_for_update(key_share=True)
            else:
                source_query = source_query.with_for_update()
            current = session.scalar(source_query)
            if current is None:
                continue
            fresh = (job_sections({'description': current.description, 'raw_metadata': current.raw_metadata})
                     if entity_type == 'job' else profile_sections(current.matching_preferences))
            document = session.scalar(select(MatchSemanticDocument).where(owner == entity_id))
            if source_hash(fresh) != source_hash(parts):
                # A previous inference may have left a same-version row with an
                # obsolete source hash. Remove it so the queue can rebuild it.
                if document is not None:
                    session.delete(document)
                    session.commit()
                continue
            if document is None:
                document = MatchSemanticDocument(**{entity_type + '_id': entity_id})
                session.add(document)
            document.version = VERSION
            document.source_hash = source_hash(parts)
            document.sections = parts
            document.vectors = pack_vectors(encoded.get((entity_type, entity_id), []))
            document.indexed_at = utcnow()
            if entity_type == 'profile':
                current.match_index_version = None
            else:
                from hiring_scraper.app.job_search import refresh_application_matches_for_jobs
                session.flush()
                refresh_application_matches_for_jobs(session, [entity_id])
            session.commit()
    return True
