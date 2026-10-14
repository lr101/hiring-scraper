"""Refresh queued job search snapshots outside API requests."""
from __future__ import annotations

import logging
import os
import time

from hiring_scraper.app.api import (
    prewarm_profile_location_snapshots_once, process_search_snapshot_refresh_once,
)


POLL_SECONDS = max(0.25, float(os.getenv("SEARCH_SNAPSHOT_POLL_SECONDS", "1")))
PREWARM_SECONDS = max(30, float(os.getenv("SEARCH_SNAPSHOT_PREWARM_SECONDS", "60")))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger(__name__).info(
        "search snapshot worker started poll_seconds=%s prewarm_seconds=%s",
        POLL_SECONDS, PREWARM_SECONDS,
    )
    last_prewarm = 0.0
    while True:
        now = time.monotonic()
        if now - last_prewarm >= PREWARM_SECONDS:
            try:
                queued = prewarm_profile_location_snapshots_once()
                logging.getLogger(__name__).info("queued %s profile/location search snapshots", queued)
            except Exception:
                logging.getLogger(__name__).exception("Could not prewarm profile/location search snapshots")
            last_prewarm = now
        if not process_search_snapshot_refresh_once():
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
