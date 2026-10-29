"""Backfill stale Journey matches without holding up HTTP requests."""
from __future__ import annotations

import logging
import time

from hiring_scraper.app.job_search import ProfileIndexChanged, refresh_profile_match_indexes_once
from hiring_scraper.app.profile_semantics import refresh_semantic_documents_once
from hiring_scraper.app.profile_llm import refresh_llm_scores_once


LOG = logging.getLogger(__name__)
POLL_SECONDS = 2


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOG.info("profile match worker started")
    next_semantic_attempt = 0.0
    next_llm_attempt = 0.0
    while True:
        try:
            refresh_profile_match_indexes_once()
        except ProfileIndexChanged:
            LOG.warning("Profile or inventory changed during matching; retrying next cycle")
        except Exception:
            LOG.exception("Could not refresh profile matches; retrying next cycle")
        if time.monotonic() >= next_semantic_attempt:
            try:
                refresh_semantic_documents_once()
            except OSError:
                LOG.warning('Local embedding service unavailable; rule matching continues; retrying in 30 seconds')
                next_semantic_attempt = time.monotonic() + 30
            except Exception:
                LOG.exception('Could not refresh local semantic profile evidence; retrying in 30 seconds')
                next_semantic_attempt = time.monotonic() + 30
        if time.monotonic() >= next_llm_attempt:
            try:
                if not refresh_llm_scores_once():
                    next_llm_attempt = time.monotonic() + 30
            except Exception:
                LOG.exception('Could not refresh model assessments; local matching continues; retrying in 30 seconds')
                next_llm_attempt = time.monotonic() + 30
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
