"""Backfill stale Journey matches without holding up HTTP requests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import threading
from uuid import uuid4
import time

from hiring_scraper.app.job_search import ProfileIndexChanged, refresh_profile_match_indexes_once, refresh_dirty_job_matches_once
from hiring_scraper.app.profile_semantics import refresh_semantic_documents_once
from hiring_scraper.app.profile_llm import refresh_llm_scores_once, sync_matching_queue, matching_worker_heartbeat
from hiring_scraper.app import codex_runtime
from hiring_scraper.app.auto_preparation import prepare_once


LOG = logging.getLogger(__name__)
POLL_SECONDS = 2


def matching_loop(stop):
    """Keep inference independent of slow local indexing and document preparation."""
    worker_id = uuid4().hex
    next_sync = 0.0
    next_quota = 0.0
    allowed = False
    with ThreadPoolExecutor(max_workers=codex_runtime.inference_concurrency(), thread_name_prefix='ai-match') as pool:
        active = set()
        while not stop.is_set():
            try:
                matching_worker_heartbeat(worker_id)
                if time.monotonic() >= next_sync:
                    sync_matching_queue()
                    next_sync = time.monotonic() + 60
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
            except Exception:
                LOG.exception('AI matching scheduler will retry')
            stop.wait(POLL_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOG.info("profile match worker started")
    next_semantic_attempt = 0.0
    stop = threading.Event()
    threading.Thread(target=matching_loop, args=(stop,), daemon=True, name="ai-matching").start()
    next_preparation_attempt = 0.0
    try:
        while True:
            for refresh in (refresh_profile_match_indexes_once, refresh_dirty_job_matches_once):
                try:
                    refresh()
                except ProfileIndexChanged:
                    LOG.warning("Matching inputs changed; retrying the affected page next cycle")
                except Exception:
                    LOG.exception("Could not refresh profile matches; retrying next cycle")
            # Each stage gets a turn even during backfill or a failed index page.
            # AI selection reads current source facts and validates them again on
            # publication; it does not depend on the materialized local index.
            if time.monotonic() >= next_semantic_attempt:
                try:
                    refresh_semantic_documents_once()
                except OSError:
                    LOG.warning('Local embedding service unavailable; rule matching continues; retrying in 30 seconds')
                    next_semantic_attempt = time.monotonic() + 30
                except Exception:
                    LOG.exception('Could not refresh local semantic profile evidence; retrying in 30 seconds')
                    next_semantic_attempt = time.monotonic() + 30
            if time.monotonic() >= next_preparation_attempt:
                try:
                    prepare_once()
                except Exception:
                    LOG.exception('Could not schedule automatic application preparation')
                next_preparation_attempt = time.monotonic() + 30
            time.sleep(POLL_SECONDS)
    finally:
        stop.set()


if __name__ == '__main__':
    main()
