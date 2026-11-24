"""Context-aware requirement and duty evidence for one profile/job pair.

Stage 1 (cheap): multilingual E5 vectors pick the few CV lines closest to each
extracted duty or requirement. Stage 2 (precise): a local cross-encoder reranker
reads each candidate together with the job's occupation and industry and scores
whether the CV line actually shows that duty or requirement. Inference functions
are injected, so this module stays pure and testable.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from hiring_scraper.job_analysis import VERSION as ANALYSIS_VERSION, clean_text, profile_units
from hiring_scraper.matching_v3.profile_text import profile_roles
from hiring_scraper.semantic_matching import MODEL_KEY as EMBEDDING_MODEL_KEY

RERANK_MODEL_REPO = 'ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF'
RERANK_MODEL_REVISION = 'a02f48bb4f057028298c21fa033da2b30d7742d5'
RERANK_MODEL_FILE = 'qwen3-reranker-0.6b-q8_0.gguf'
RERANK_MODEL_KEY = f'{RERANK_MODEL_REPO}@{RERANK_MODEL_REVISION[:12]}:{RERANK_MODEL_FILE}'
EVIDENCE_REVISION = 'evidence-v2'
VERSION = f'{EVIDENCE_REVISION}|{RERANK_MODEL_KEY}|{EMBEDDING_MODEL_KEY}'
UNITS_VERSION = f'units-v1|{EMBEDDING_MODEL_KEY}'

CANDIDATES_PER_ITEM = 3
ROLE_CANDIDATES = 4
DOMAIN_CANDIDATES = 6
MAX_SCORED_ITEMS = 28
# Language and work-permit clauses are handled by the eligibility gates.
GATED_KINDS = frozenset({'language', 'authorization'})
FORMAL_KINDS = frozenset({'education', 'certification'})

Embed = Callable[[list[str], str], list[list[float]]]
Rerank = Callable[[str, list[str]], list[float]]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def units_source(profile: dict) -> dict[str, Any]:
    """Profile evidence units and roles: the only profile inputs used for evidence."""
    return {'units': profile_units(profile), 'roles': profile_roles(profile)}


def units_hash(source: dict[str, Any]) -> str:
    return _hash(source)


def evidence_hash(analysis_hash: str, profile_units_hash: str) -> str:
    return _hash([VERSION, ANALYSIS_VERSION, analysis_hash, profile_units_hash])


def _job_context(analysis: dict, title: str) -> str:
    parts = [part for part in (analysis.get('occupation'), analysis.get('industry')) if part]
    context = f'{title.strip()}' if title else ''
    if parts:
        context = f'{context} ({"; ".join(parts)})' if context else '; '.join(parts)
    return context or 'unspecified role'


def scored_items(analysis: dict) -> list[dict[str, Any]]:
    """Duties and requirements that the reranker scores, with stable refs."""
    items: list[dict[str, Any]] = []
    for index, duty in enumerate(analysis.get('duties') or []):
        items.append({'ref': f'd{index}', 'type': 'duty', 'text': duty['text']})
    for index, requirement in enumerate(analysis.get('requirements') or []):
        if requirement.get('kind') in GATED_KINDS:
            continue
        items.append({'ref': f'r{index}', 'type': 'requirement', 'text': requirement['text'],
                      'need': requirement.get('need', 'must'), 'kind': requirement.get('kind', 'other')})
    # Bound the per-pair cost; requirements are kept before overflow duties.
    if len(items) > MAX_SCORED_ITEMS:
        duties = [item for item in items if item['type'] == 'duty']
        requirements = [item for item in items if item['type'] == 'requirement']
        budget = max(MAX_SCORED_ITEMS // 2, MAX_SCORED_ITEMS - len(duties))
        # Mandatory formal qualifications first, then other mandatory items, then preferred ones.
        ranked = sorted(requirements, key=lambda item: (item['need'] == 'nice', item['kind'] not in FORMAL_KINDS))
        kept = {item['ref'] for item in ranked[:budget]}
        keep_req = [item for item in requirements if item['ref'] in kept]
        items = duties[:MAX_SCORED_ITEMS - len(keep_req)] + keep_req
    return items


def rerank_query(item: dict[str, Any], context: str) -> str:
    if item['type'] == 'duty':
        return (f'Job: {context}. Task the candidate will perform in this job: {item["text"]}. '
                'Does this CV evidence show the candidate has done this kind of task in a comparable field?')
    return (f'Job: {context}. Requirement the candidate must meet: {item["text"]}. '
            'Does this CV evidence show the candidate meets this requirement in this field?')


def pack_unit_vectors(vectors: Sequence[Sequence[float]]) -> bytes:
    if not vectors:
        return b''
    values = np.asarray(vectors, dtype='<f4')
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return (values / np.maximum(norms, 1e-9)).astype('<f4').tobytes()


def unpack_unit_vectors(blob: bytes, count: int) -> np.ndarray:
    if not blob or not count:
        return np.zeros((0, 384), dtype='<f4')
    return np.frombuffer(blob, dtype='<f4').reshape(count, -1)


def embed_units(source: dict[str, Any], embed: Embed) -> bytes:
    texts = [unit['text'] for unit in source['units']]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 32):
        vectors.extend(embed(texts[start:start + 32], 'passage'))
    return pack_unit_vectors(vectors)


def _closest(query: str, texts: list[str], embed: Embed, limit: int) -> list[int]:
    """Indexes of the ``limit`` texts closest to ``query`` by E5 cosine (all when few)."""
    if len(texts) <= limit:
        return list(range(len(texts)))
    vectors = np.asarray(embed([query] + texts, 'query'), dtype='<f4')
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
    return [int(index) for index in np.argsort(-(vectors[1:] @ vectors[0]))[:limit]]


def compute_evidence(
    analysis: dict,
    title: str,
    source: dict[str, Any],
    unit_vectors: bytes,
    *,
    embed: Embed,
    rerank: Rerank,
) -> dict[str, Any]:
    """Score every extracted duty/requirement against the profile's best CV lines."""
    units = source['units']
    roles = source['roles']
    context = _job_context(analysis, clean_text(title))  # posting title is untrusted text
    items = scored_items(analysis)
    result: dict[str, Any] = {'items': [], 'role': None, 'domain': None, 'context': context}
    matrix = unpack_unit_vectors(unit_vectors, len(units))
    if items and len(units):
        queries = [f'{context}: {item["text"]}' for item in items]
        item_vectors: list[list[float]] = []
        for start in range(0, len(queries), 32):
            item_vectors.extend(embed(queries[start:start + 32], 'query'))
        item_matrix = np.asarray(item_vectors, dtype='<f4')
        item_matrix /= np.maximum(np.linalg.norm(item_matrix, axis=1, keepdims=True), 1e-9)
        similarities = item_matrix @ matrix.T
        for item, row in zip(items, similarities):
            candidates = [int(index) for index in np.argsort(-row)[:CANDIDATES_PER_ITEM]]
            scores = rerank(rerank_query(item, context), [units[index]['text'] for index in candidates])
            best = int(np.argmax(scores)) if scores else 0
            unit = units[candidates[best]] if scores else None
            result['items'].append({
                'ref': item['ref'],
                'support': round(float(scores[best]), 4) if scores else 0.0,
                'similarity': round(float(row[candidates[best]]), 4) if scores else None,
                'quote': unit['text'][:240] if unit else '',
                'source': unit['source'] if unit else '',
            })
    else:
        result['items'] = [{'ref': item['ref'], 'support': 0.0, 'similarity': None, 'quote': '', 'source': ''}
                           for item in items]
    if roles and title:
        candidates = _closest(f'{context}', roles, embed, ROLE_CANDIDATES)
        scores = rerank(f'Job title: {context}. Is this the same kind of role as the candidate\'s target or past role?',
                        [roles[index] for index in candidates])
        best = int(np.argmax(scores))
        result['role'] = {'support': round(float(scores[best]), 4), 'role': roles[candidates[best]]}
    work_units = [index for index, unit in enumerate(units) if unit['source'].startswith(('cv.', 'experience', 'summary'))]
    if work_units and (analysis.get('occupation') or analysis.get('industry')):
        field = '; '.join(part for part in (analysis.get('occupation'), analysis.get('industry')) if part)
        rows = matrix[work_units] if len(matrix) else None
        if rows is not None and len(rows):
            query = np.asarray(embed([f'{field}: {title}'], 'query')[0], dtype='<f4')
            order = np.argsort(-(rows @ (query / max(float(np.linalg.norm(query)), 1e-9))))[:DOMAIN_CANDIDATES]
            chosen = [work_units[int(index)] for index in order]
        else:
            chosen = work_units[:DOMAIN_CANDIDATES]
        scores = rerank(f'Occupational field and industry of the job: {field}. '
                        'Does this CV evidence show professional background in this field?',
                        [units[index]['text'] for index in chosen])
        best = int(np.argmax(scores))
        unit = units[chosen[best]]
        result['domain'] = {'support': round(float(scores[best]), 4), 'quote': unit['text'][:240],
                            'source': unit['source'], 'field': field}
    return result
