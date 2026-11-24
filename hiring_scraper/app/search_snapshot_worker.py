"""Refresh queued job search snapshots outside API requests."""
from __future__ import annotations

import logging
import os
import time

from hiring_scraper.app.search_snapshots import (
    backfill_job_location_scopes_once,
    prewarm_profile_location_snapshots_once,
    process_search_snapshot_refresh_once,
)


POLL_SECONDS = max(0.25, float(os.getenv("SEARCH_SNAPSHOT_POLL_SECONDS", "1")))
PREWARM_SECONDS = max(30, float(os.getenv("SEARCH_SNAPSHOT_PREWARM_SECONDS", "60")))
BACKFILL_BATCH_SIZE = 500


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
        backfilled = 0
        try:
            backfilled = backfill_job_location_scopes_once(batch_size=BACKFILL_BATCH_SIZE)
        except Exception:
            logging.getLogger(__name__).exception("Could not backfill job location scopes")
        if backfilled >= BACKFILL_BATCH_SIZE:
            continue
        refreshed = process_search_snapshot_refresh_once()
        if not backfilled and not refreshed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
