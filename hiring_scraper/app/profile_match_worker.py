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
    next_preparation_attempt = 0.0
    while True:
        # Model inference runs in the job analysis worker; this worker only
        # scores from stored analyses and evidence. Each stage gets a turn even
        # if another fails.
        _run_match_stage(refresh_profile_match_indexes_once)
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
