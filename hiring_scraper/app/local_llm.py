"""Clients for the local llama.cpp model containers (job analysis LLM and reranker).

Both servers are started with ``--alias <model key>``; every response is checked
against the pinned key, so a swapped model can never write mislabeled analyses.
Network or server failures raise ``OSError`` so workers back off and retry.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hiring_scraper.job_analysis import GRAMMAR, LLM_MODEL_KEY, MAX_OUTPUT_TOKENS, prompt_messages
from hiring_scraper.matching_v3.evidence import RERANK_MODEL_KEY

LLM_TIMEOUT_SECONDS = float(os.getenv('JOB_ANALYSIS_LLM_TIMEOUT_SECONDS', '900'))
RERANK_TIMEOUT_SECONDS = float(os.getenv('JOB_ANALYSIS_RERANK_TIMEOUT_SECONDS', '300'))


def llm_url() -> str:
    return os.getenv('JOB_ANALYSIS_LLM_URL', 'http://127.0.0.1:8091').rstrip('/')


def rerank_url() -> str:
    return os.getenv('JOB_ANALYSIS_RERANK_URL', 'http://127.0.0.1:8092').rstrip('/')


def _post(url: str, payload: dict, timeout: float) -> dict:
    request = Request(url, data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(8_000_000))
    except HTTPError as error:
        raise OSError(f'Local model server returned HTTP {error.code}') from error
    except (URLError, TimeoutError, ValueError) as error:
        raise OSError(f'Local model server unavailable: {error}') from error


def analyse_lines(lines: list[str]) -> str:
    """Run the grammar-constrained job analysis and return the raw line format."""
    payload = _post(f'{llm_url()}/v1/chat/completions', {
        'model': LLM_MODEL_KEY,
        'messages': prompt_messages(lines),
        'temperature': 0,
        'seed': 7,
        'max_tokens': MAX_OUTPUT_TOKENS,
        'grammar': GRAMMAR,
        'cache_prompt': True,
    }, LLM_TIMEOUT_SECONDS)
    if payload.get('model') != LLM_MODEL_KEY:
        raise OSError('Local LLM server reported an unexpected model; check --alias and the pinned GGUF')
    try:
        choice = payload['choices'][0]
        content = choice['message']['content']
    except (KeyError, IndexError, TypeError) as error:
        raise OSError('Local LLM server returned an incomplete response') from error
    if choice.get('finish_reason') == 'length':
        # The grammar requires a closing END; truncated output is still parsed
        # line by line, so keep the grounded items that did arrive.
        content = str(content or '')
    return str(content or '')


def rerank(query: str, documents: list[str]) -> list[float]:
    """Relevance probabilities in [0, 1] for each document, in input order."""
    if not documents:
        return []
    payload = _post(f'{rerank_url()}/v1/rerank', {
        'model': RERANK_MODEL_KEY, 'query': query, 'documents': documents, 'top_n': len(documents),
    }, RERANK_TIMEOUT_SECONDS)
    if payload.get('model') not in (None, RERANK_MODEL_KEY):
        raise OSError('Local reranker reported an unexpected model; check --alias and the pinned GGUF')
    scores = [0.0] * len(documents)
    try:
        for row in payload['results']:
            scores[int(row['index'])] = max(0.0, min(1.0, float(row['relevance_score'])))
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise OSError('Local reranker returned an invalid response') from error
    return scores
