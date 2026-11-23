"""Deterministic offline semantic fixture embeddings for evaluation.

Computes lightweight 384-d L2-normalized vectors on the fly from hashed
lowercase word stems and character trigrams, then builds the ``semantic`` dict
consumed by ``hiring_scraper.matching.match_job`` via
``hiring_scraper.semantic_matching``. No precomputed vectors are committed.
"""
from __future__ import annotations

import functools
import hashlib
import math
import re
from collections.abc import Sequence

from hiring_scraper.semantic_matching import (
    DIMENSIONS,
    job_sections,
    pack_vectors,
    profile_sections,
    semantic_evidence,
)

_TOKEN_RE = re.compile(r"[a-z0-9äöüß]+")
_STEM_MAX_LEN = 6
_STEM_WEIGHT = 2.0
_TRIGRAM_WEIGHT = 1.0


def _feature_hash(feature: str, dimensions: int) -> int:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % dimensions


@functools.lru_cache(maxsize=4096)
def _embed_text_tuple(text: str, dimensions: int) -> tuple[float, ...]:
    if dimensions <= 0:
        raise ValueError(f"dimensions must be positive, got {dimensions}")
    vec = [0.0] * dimensions
    tokens = _TOKEN_RE.findall((text or "").lower())
    has_feature = False
    for token in tokens:
        if len(token) >= 2:
            vec[_feature_hash("w:" + token[:_STEM_MAX_LEN], dimensions)] += _STEM_WEIGHT
            has_feature = True
        padded = f"#{token}#"
        for idx in range(len(padded) - 2):
            vec[_feature_hash("g:" + padded[idx : idx + 3], dimensions)] += _TRIGRAM_WEIGHT
            has_feature = True
    if not has_feature:
        vec[_feature_hash("raw:" + (text or ""), dimensions)] = 1.0
    norm = math.sqrt(sum(val * val for val in vec))
    if norm < 1e-12:
        vec[0] = 1.0
        norm = 1.0
    return tuple(val / norm for val in vec)


def embed_text(text: str, *, dimensions: int = DIMENSIONS) -> list[float]:
    """Return a deterministic L2-normalized embedding vector for ``text``."""
    return list(_embed_text_tuple(text, dimensions))


def embed_sections(sections: Sequence[dict], *, dimensions: int = DIMENSIONS) -> bytes:
    """Embed extracted job/profile sections and pack them with ``pack_vectors``."""
    if not sections:
        return b""
    vectors = [embed_text(str(section.get("text") or ""), dimensions=dimensions) for section in sections]
    return pack_vectors(vectors)


def build_semantic_evidence(job: dict, profile: dict) -> dict:
    """Compute the ``semantic`` evidence dict accepted by ``match_job``."""
    p_parts = profile_sections(profile)
    j_parts = job_sections(job)
    p_vectors = embed_sections(p_parts)
    j_vectors = embed_sections(j_parts)
    return semantic_evidence(p_parts, p_vectors, j_parts, j_vectors)
