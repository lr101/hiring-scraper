"""Deterministic stand-ins for the local models in database tests.

Production runs a local LLM and reranker (``app.job_analysis_worker``). Tests run
the same worker code with these functions instead: every posting line becomes a
duty (lines that say required/erforderlich become requirements), and a CV line
supports an item when they share a meaningful word.
"""
from __future__ import annotations

import hashlib
import re

_WORD = re.compile(r'[^\W\d_][\w+#-]{3,}', re.U)
_STOP = {'with', 'your', 'have', 'this', 'that', 'from', 'will', 'oder', 'und', 'oder', 'eine', 'einer', 'sind',
         'role', 'tasks', 'requirements', 'experience', 'erfahrung', 'job', 'title', 'does', 'show', 'candidate',
         'field', 'this', 'evidence', 'task', 'perform', 'must', 'meet', 'requirement', 'comparable', 'test',
         'occupation', 'industry', 'kind', 'same', 'target', 'past', 'professional', 'background',
         'occupational', 'unspecified'}
_REQUIRED = re.compile(r'\b(?:required|requirement|erforderlich|must|bring|mitbringen|kenntnisse)\b', re.I)


def words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text or '')} - _STOP


def _content(index: int, line: str) -> bool:
    # Like the real model, skip the title and bare headings such as "Your tasks:".
    return bool(index) and len(line.split()) > 2 and not line.rstrip().endswith(':')


def analyse(lines: list[str]) -> str:
    rows = ['OCC test occupation', 'IND test industry', 'DUTIES']
    rows += [f'D {index} | {line}' for index, line in enumerate(lines)
             if _content(index, line) and not _REQUIRED.search(line)][:12]
    rows.append('REQUIREMENTS')
    rows += [f'R {index} must skill | {line}' for index, line in enumerate(lines)
             if _content(index, line) and _REQUIRED.search(line)][:12]
    rows.append('END')
    return '\n'.join(rows)


def embed(texts: list[str], kind: str) -> list[list[float]]:
    vectors = []
    for text in texts:
        vector = [0.0] * 384
        for word in words(text) or {'empty'}:
            vector[int(hashlib.blake2b(word.encode(), digest_size=4).hexdigest(), 16) % 384] += 1.0
        vectors.append(vector)
    return vectors


def rerank(query: str, documents: list[str]) -> list[float]:
    item = query.split(': ', 2)[-1] if ': ' in query else query
    wanted = words(item)
    return [0.98 if wanted & words(document) else 0.01 for document in documents]


def run_analysis_worker(max_rounds: int = 2000) -> bool:
    """Drain the job analysis and evidence queues once with the stand-in models."""
    from hiring_scraper.app import job_analysis_worker as worker

    # Test fixtures often insert jobs without the ingestion declaration that
    # queues them; the worker's startup reconciliation picks those up.
    worker.reconcile_queues()
    worked = False
    for _ in range(max_rounds):
        step = bool(worker.refresh_profile_units_once(embed_fn=embed))
        step = bool(worker.refresh_evidence_once(limit=50, embed_fn=embed, rerank_fn=rerank)) or step
        step = worker.analyse_next_job(analyse=analyse) is not None or step
        if not step:
            return worked
        worked = True
    raise AssertionError('Job analysis queues did not settle')


def analyse_all(engine, *, drain_rematch: bool = True) -> None:
    """Run the stand-in job analysis worker against ``engine`` until its queues are empty.

    Published analyses queue rematches; drain them too, as the profile match worker
    would, so later reads see clean stored matches.
    """
    from hiring_scraper.app import job_analysis_worker, job_search  # noqa: F401 (imported before patching)
    from tests import pg

    with pg.patched_session_local(engine):
        run_analysis_worker()
        while drain_rematch and job_search.refresh_dirty_job_matches_once():
            pass
