"""Pinned local multilingual embedding model shared by search and job analysis.

Inference runs in the semantic search worker (``/embed`` and ``/embed/batch``).
"""
from __future__ import annotations

MODEL_ID = 'intfloat/multilingual-e5-small'
MODEL_REVISION = 'ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3'
MODEL_KEY = f'{MODEL_ID}@{MODEL_REVISION}'
DIMENSIONS = 384
