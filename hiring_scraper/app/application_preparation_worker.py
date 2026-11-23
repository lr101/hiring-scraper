"""Run queued application preparation outside the HTTP process.

The inference lane (prepare/revise, legacy v1 preparations) runs on the main
thread; the render lane runs on its own thread so PDF exports never wait
behind a model call. Each lane uses short database sessions.
"""
import logging
import threading
import time

from hiring_scraper.app import application_preparation_queue as queue
from hiring_scraper.app.database import SessionLocal, log_worker_failure

LOG = logging.getLogger(__name__)


def _run_legacy(profile_id, payload, revision, progress):
    from hiring_scraper.app.application_generation import PrepareApplicationRequest, _prepare_application
    with SessionLocal() as session:
        return _prepare_application(
            profile_id, PrepareApplicationRequest.model_validate(payload), session,
            progress=progress, expected_revision=revision,
        )


def run(profile_id, payload, revision, progress):
    from hiring_scraper.app import application_documents_v2 as documents
    kind = getattr(progress, 'kind', None)
    if kind is None:
        return _run_legacy(profile_id, payload, revision, progress)
    if kind == 'prepare':
        if documents.pipeline_version() != 'v2':
            raise queue.TaskError('pipeline_disabled', 'Structured application documents are turned off on this server.')
        return documents.run_prepare(profile_id, payload, progress)
    if kind == 'revise':
        return documents.run_revise(profile_id, payload, progress)
    raise queue.TaskError('unknown_kind', 'This task type is not supported by this server version.')


def render(profile_id, kind, body):
    from hiring_scraper.app import application_documents_v2 as documents
    return documents.render_for_worker(profile_id, kind, body)


def _render_loop(stop: threading.Event):
    while not stop.is_set():
        try:
            busy = queue.process_render_once(render)
        except Exception as error:  # noqa: BLE001 - keep the lane alive
            log_worker_failure(LOG, 'Render lane cycle failed', error)
            busy = False
        if not busy:
            stop.wait(1)


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    threading.Thread(target=_render_loop, args=(stop,), name='render-lane', daemon=True).start()
    next_cleanup = time.monotonic()
    while True:
        try:
            if time.monotonic() >= next_cleanup:
                queue.prune_finished()
                next_cleanup = time.monotonic() + 3600
            busy = queue.process_once(run)
        except Exception as error:  # noqa: BLE001 - contention must not stop the worker
            log_worker_failure(LOG, 'Preparation lane cycle failed', error)
            busy = False
        if not busy:
            time.sleep(1)


if __name__ == '__main__':
    main()
