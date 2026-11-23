"""Backfill stale Journey matches without holding up HTTP requests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import threading
from uuid import uuid4
import time

from hiring_scraper.app.job_search import ProfileIndexChanged, refresh_profile_match_indexes_once, refresh_dirty_job_matches_once
from hiring_scraper.app.profile_semantics import refresh_semantic_documents_once
from hiring_scraper.app.profile_llm import (
    matching_worker_heartbeat,
    reconcile_matching_config_change,
    refresh_llm_scores_once,
    sync_matching_queue,
)
from hiring_scraper.app import codex_runtime
from hiring_scraper.app.database import log_worker_failure
from hiring_scraper.app.auto_preparation import prepare_once


LOG = logging.getLogger(__name__)
POLL_SECONDS = 2


def matching_loop(stop):
    """Keep inference independent of slow local indexing and document preparation."""
    worker_id = uuid4().hex
    next_sync = 0.0
    next_quota = 0.0
    allowed = False
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='ai-queue-sync') as sync_pool, \
            ThreadPoolExecutor(max_workers=codex_runtime.inference_concurrency(), thread_name_prefix='ai-match') as pool:
        sync_future = None
        active = set()
        while not stop.is_set():
            try:
                matching_worker_heartbeat(worker_id)
                if sync_future is not None and sync_future.done():
                    finished_sync, sync_future = sync_future, None
                    next_sync = time.monotonic() + 60
                    finished_sync.result()
                if sync_future is None and time.monotonic() >= next_sync:
                    # Inventory reconciliation can scan hundreds of thousands of
                    # jobs. Never hold up dispatch or heartbeats while it runs.
                    sync_future = sync_pool.submit(sync_matching_queue)
                for future in list(active):
                    if future.done():
                        active.remove(future)
                        future.result()
                if time.monotonic() >= next_quota:
                    allowed = bool(codex_runtime.subscription_usage().get('allowed'))
                    next_quota = time.monotonic() + (2 if allowed else 30)
                if allowed:
                    while len(active) < codex_runtime.inference_concurrency():
                        active.add(pool.submit(refresh_llm_scores_once, sync=False))
            except Exception as error:
                log_worker_failure(LOG, 'AI matching scheduler will retry', error)
            stop.wait(POLL_SECONDS)


def _run_match_stage(refresh) -> None:
    try:
        refresh()
    except ProfileIndexChanged:
        LOG.warning("Matching inputs changed; retrying the affected page next cycle")
    except Exception as error:
        log_worker_failure(LOG, "Could not refresh profile matches; retrying next cycle", error)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOG.info("profile match worker started")
    config_reconciled = False
    try:
        queued = reconcile_matching_config_change()
        config_reconciled = True
        if queued:
            LOG.info("Queued %s AI-scored jobs after AI matching config change", queued)
    except Exception as error:
        log_worker_failure(LOG, "Could not reconcile AI matching config at startup; retrying next cycle", error)
    next_semantic_attempt = 0.0
    stop = threading.Event()
    threading.Thread(target=matching_loop, args=(stop,), daemon=True, name="ai-matching").start()
    next_preparation_attempt = 0.0
    try:
        while True:
            if not config_reconciled:
                try:
                    queued = reconcile_matching_config_change()
                    config_reconciled = True
                    if queued:
                        LOG.info("Queued %s AI-scored jobs after AI matching config change", queued)
                except Exception as error:
                    log_worker_failure(LOG, "Could not reconcile AI matching config; retrying next cycle", error)
            # Order: local rule backfill (cheap, never starved by the model), then
            # vectors, then the changed-job rematch so edited postings use fresh
            # semantic evidence. Each stage gets a turn even if another fails.
            _run_match_stage(refresh_profile_match_indexes_once)
            if time.monotonic() >= next_semantic_attempt:
                try:
                    refresh_semantic_documents_once()
                except OSError:
                    LOG.warning('Local embedding service unavailable; rule matching continues; retrying in 30 seconds')
                    next_semantic_attempt = time.monotonic() + 30
                except Exception as error:
                    log_worker_failure(LOG, 'Could not refresh local semantic profile evidence; retrying in 30 seconds', error)
                    next_semantic_attempt = time.monotonic() + 30
            _run_match_stage(refresh_dirty_job_matches_once)
            if time.monotonic() >= next_preparation_attempt:
                try:
                    prepare_once()
                except Exception as error:
                    log_worker_failure(LOG, 'Could not schedule automatic application preparation', error)
                next_preparation_attempt = time.monotonic() + 30
            time.sleep(POLL_SECONDS)
    finally:
        stop.set()


if __name__ == '__main__':
    main()
