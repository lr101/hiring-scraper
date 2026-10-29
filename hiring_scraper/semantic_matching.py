"""Bounded section extraction and local vector comparison, without model loading.

Similarity is a retrieval signal, never proof of qualifications. Model inference
belongs to the existing local embedding service, outside request handlers.
"""
from __future__ import annotations

import hashlib
import json
import numpy as np

from hiring_scraper.matching import NON_REQUIREMENT, _UNSUPPORTED_PROFILE_CLAIM, profile_content_sources
from hiring_scraper.requirements import scoped_sentences, structured_text

MODEL_KEY = 'intfloat/multilingual-e5-small@ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3'
VERSION = MODEL_KEY + ':profile-sections-v2'
DIMENSIONS = 384
CHUNK_CHARS = 900
MAX_JOB_CHUNKS = 12
MAX_PROFILE_CHUNKS = 8


def _chunks(text: str) -> list[str]:
    words = text.split()
    chunks, current, size = [], [], 0
    for word in words:
        # Extremely long source tokens must not bypass the inference bound.
        for start in range(0, len(word), CHUNK_CHARS):
            piece = word[start:start + CHUNK_CHARS]
            if current and size + len(piece) + 1 > CHUNK_CHARS:
                chunks.append(' '.join(current))
                current, size = [], 0
            current.append(piece)
            size += len(piece) + bool(size)
    if current:
        chunks.append(' '.join(current))
    return chunks


def _bounded_sections(groups: dict[tuple[str, str], list[str]], limit: int) -> list[dict]:
    # Round-robin sampling preserves late qualifications even with a long intro
    # or duties section. Include both ends rather than truncating each document.
    buckets = []
    for (source, scope), sentences in groups.items():
        chunks = _chunks(' '.join(dict.fromkeys(sentences)))
        order = [0, len(chunks) - 1, *range(1, len(chunks) - 1)] if chunks else []
        buckets.append([{'source': source, 'scope': scope, 'text': chunks[index]}
                        for index in dict.fromkeys(order)])
    result, seen = [], set()
    while any(buckets) and len(result) < limit:
        for bucket in buckets:
            if not bucket:
                continue
            row = bucket.pop(0)
            if row['text'] not in seen:
                seen.add(row['text'])
                result.append(row)
            if len(result) == limit:
                break
    return result


def job_sections(job: dict) -> list[dict]:
    texts = [('description', job.get('description') or '', False)]
    raw = job.get('raw_metadata') or {}
    for prefix, values in [('structured', raw), ('detail', raw.get('detail_requirements') or {})]:
        for field in ('skills', 'qualifications', 'experienceRequirements', 'educationRequirements'):
            value = values.get(field)
            if value:
                texts.append((prefix + '.' + field,
                              value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), True))
    groups = {}
    for source, text, explicit in texts:
        for sentence, scope in scoped_sentences(structured_text(text), structured=explicit):
            if scope in {'benefits', 'optional'} or NON_REQUIREMENT.search(sentence):
                continue
            # Headings alone carry no content and can otherwise inflate cosine similarity.
            if len(sentence.split()) < 4:
                continue
            groups.setdefault((source, scope), []).append(sentence)
    return _bounded_sections(groups, MAX_JOB_CHUNKS)


def profile_sections(profile: dict) -> list[dict]:
    groups = {}
    skills = profile.get('skills') or []
    if skills:
        groups[('skills', 'capabilities')] = ['Skills: ' + ', '.join(skills)]
    for source, text in profile_content_sources(profile):
        for sentence, _ in scoped_sentences(structured_text(text)):
            if not _UNSUPPORTED_PROFILE_CLAIM.search(sentence):
                groups.setdefault((source, 'experience'), []).append(sentence)
    return _bounded_sections(groups, MAX_PROFILE_CHUNKS)


def source_hash(sections: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sections, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def pack_vectors(vectors: list[list[float]]) -> bytes:
    if not vectors:
        return b''
    values = np.asarray(vectors, dtype='<f4')
    if values.ndim != 2 or values.shape[1] != DIMENSIONS or not np.isfinite(values).all():
        raise ValueError('Invalid local embedding shape or values')
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if (norms < 1e-9).any():
        raise ValueError('Empty local embedding')
    return (values / norms).astype('<f4').tobytes()


def semantic_evidence(profile_parts: list[dict], profile_vectors: bytes,
                      job_parts: list[dict], job_vectors: bytes) -> dict:
    empty = {'score': 0, 'similarity': None, 'model': MODEL_KEY}
    if not profile_parts or not job_parts:
        return empty
    try:
        p = np.frombuffer(profile_vectors, dtype='<f4').reshape(len(profile_parts), DIMENSIONS)
        j = np.frombuffer(job_vectors, dtype='<f4').reshape(len(job_parts), DIMENSIONS)
    except ValueError:
        return empty
    if not np.isfinite(p).all() or not np.isfinite(j).all():
        return empty
    similarities = j @ p.T
    job_index, profile_index = np.unravel_index(np.argmax(similarities), similarities.shape)
    similarity = max(-1.0, min(1.0, float(similarities[job_index, profile_index])))
    # E5 cosine values are concentrated well above zero. This conservative
    # retrieval threshold is not a probability and needs corpus calibration.
    score = min(59, 30 + round((similarity - .84) / .07 * 29)) if similarity >= .84 else 0
    return {'score': score, 'similarity': round(similarity, 4), 'model': MODEL_KEY,
            'job_evidence': job_parts[job_index]['text'],
            'job_source': job_parts[job_index]['source'],
            'profile_evidence': profile_parts[profile_index]['text'],
            'profile_source': profile_parts[profile_index]['source']}
