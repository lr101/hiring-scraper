"""Backfill stale Journey matches without holding up HTTP requests."""
from __future__ import annotations

import logging
import time

from hiring_scraper.app.auto_preparation import prepare_once
from hiring_scraper.app.database import log_worker_failure
from hiring_scraper.app.job_search import (
    ProfileIndexChanged,
    refresh_dirty_job_matches_once,
    refresh_profile_match_indexes_once,
)
from hiring_scraper.app.profile_semantics import refresh_semantic_documents_once

LOG = logging.getLogger(__name__)
POLL_SECONDS = 2


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
    next_semantic_attempt = 0.0
    next_preparation_attempt = 0.0
    while True:
        # Order: local rule backfill, then semantic vectors, then the
        # changed-job rematch so edited postings use fresh semantic evidence.
        # Each stage gets a turn even if another fails.
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


if __name__ == '__main__':
    main()
