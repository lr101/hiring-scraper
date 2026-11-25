"""Clients for the local llama.cpp model servers (job analysis LLM and reranker).

Calls go to the homelab containers, or to the laptop GPU while it is enabled in
Settings and online (see ``laptop_gpu``).

Both servers are started with ``--alias <model key>``; every response is checked
against the pinned key, so a swapped model can never write mislabeled analyses.
Network or server failures raise ``OSError`` so workers back off and retry; a
rejected input raises ``ModelRequestRejected``.
"""
from __future__ import annotations

import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hiring_scraper.app import laptop_gpu
from hiring_scraper.job_analysis import GRAMMAR, LLM_MODEL_KEY, MAX_OUTPUT_TOKENS, prompt_messages
from hiring_scraper.matching_v3.evidence import RERANK_MODEL_KEY

LLM_TIMEOUT_SECONDS = float(os.getenv('JOB_ANALYSIS_LLM_TIMEOUT_SECONDS', '900'))
RERANK_TIMEOUT_SECONDS = float(os.getenv('JOB_ANALYSIS_RERANK_TIMEOUT_SECONDS', '300'))


def llm_url() -> str:
    return laptop_gpu.endpoint('llm')[0]


def rerank_url() -> str:
    return laptop_gpu.endpoint('reranker')[0]


def llm_parallel() -> int:
    """Postings to analyse at once: the laptop's slots, else one."""
    return laptop_gpu.endpoint('llm')[1]


def rerank_parallel() -> int:
    """Profile/posting pairs to check at once: the laptop's slots, else one."""
    return laptop_gpu.endpoint('reranker')[1]


class ModelRequestRejected(ValueError):
    """The server is up but refused this input (for example, longer than its context).

    Item-specific, unlike ``OSError``: the worker retries that item later or with a
    shorter prompt instead of pausing the whole stage.
    """


def _post(base: str, path: str, payload: dict, timeout: float) -> dict:
    try:
        return _send(f'{base}{path}', payload, timeout)
    except OSError:
        laptop_gpu.mark_unreachable(base)  # The next call goes to the homelab server.
        raise


def _send(url: str, payload: dict, timeout: float) -> dict:
    request = Request(url, data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(8_000_000))
    except HTTPError as error:
        if error.code in (400, 413, 422):
            raise ModelRequestRejected(f'Local model server rejected the request (HTTP {error.code})') from error
        raise OSError(f'Local model server returned HTTP {error.code}') from error
    except (URLError, TimeoutError, ValueError, HTTPException) as error:
        # HTTPException: the server restarted or died mid-response (an outage, not bad input).
        raise OSError(f'Local model server unavailable: {error}') from error


def analyse_lines(lines: list[str]) -> str:
    """Run the grammar-constrained job analysis and return the raw line format."""
    payload = _post(llm_url(), '/v1/chat/completions', {
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
    payload = _post(rerank_url(), '/v1/rerank', {
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
