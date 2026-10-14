"""Refresh queued job search snapshots outside API requests."""
from __future__ import annotations

import logging
import os
import time

from hiring_scraper.app.api import process_search_snapshot_refresh_once


POLL_SECONDS = max(0.25, float(os.getenv("SEARCH_SNAPSHOT_POLL_SECONDS", "1")))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger(__name__).info("search snapshot worker started poll_seconds=%s", POLL_SECONDS)
    while True:
        if not process_search_snapshot_refresh_once():
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
