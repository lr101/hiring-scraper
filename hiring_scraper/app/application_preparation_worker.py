"""Run queued application preparation outside the HTTP process."""
import logging
import time

from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.application_generation import PrepareApplicationRequest, _prepare_application
from hiring_scraper.app.database import SessionLocal


def run(profile_id, payload, revision, progress):
    with SessionLocal() as session:
        return _prepare_application(
            profile_id, PrepareApplicationRequest.model_validate(payload), session,
            progress=progress, expected_revision=revision,
        )


def main():
    logging.basicConfig(level=logging.INFO)
    next_cleanup = time.monotonic()
    while True:
        if time.monotonic() >= next_cleanup:
            queue.prune_finished()
            next_cleanup = time.monotonic() + 3600
        if not queue.process_once(run):
            time.sleep(1)


if __name__ == '__main__':
    main()
