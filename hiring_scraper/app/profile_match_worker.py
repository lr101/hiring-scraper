"""Backfill stale Journey matches without holding up HTTP requests."""
from __future__ import annotations

import logging
import time

from hiring_scraper.app.job_search import ProfileIndexChanged, refresh_profile_match_indexes_once


LOG = logging.getLogger(__name__)
POLL_SECONDS = 2


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOG.info("profile match worker started")
    while True:
        try:
            refresh_profile_match_indexes_once()
        except ProfileIndexChanged:
            LOG.warning("Profile or inventory changed during matching; retrying next cycle")
        except Exception:
            LOG.exception("Could not refresh profile matches; retrying next cycle")
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
