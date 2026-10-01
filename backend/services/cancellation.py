"""Cooperative submission cancellation, shared by nested page workers.

The database is authoritative so cancellation also works across API processes.
Already-running native/network calls finish before their next checkpoint.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
import threading
import time


class ExtractionCancelled(BaseException):
    """Control flow: must bypass the pipeline's recoverable-error fallbacks."""


class CancellationToken:
    def __init__(self, submission_id, session_factory):
        self.submission_id = submission_id
        self.session_factory = session_factory
        self.lock = threading.Lock()
        self.cancelled = False
        self.next_check = 0.0

    def check(self):
        from backend.db.models import ExamSubmission

        with self.lock:
            if not self.cancelled and time.monotonic() >= self.next_check:
                with self.session_factory() as db:
                    status = db.query(ExamSubmission.status).filter(
                        ExamSubmission.id == self.submission_id
                    ).scalar()
                self.cancelled = status is None or status == "cancelled"
                self.next_check = time.monotonic() + 0.5
            if self.cancelled:
                raise ExtractionCancelled()


current_cancellation = ContextVar("submission_cancellation", default=None)


def check_cancelled():
    from backend.queue.runtime import current_job
    execution = current_job.get()
    if execution is not None:
        execution.check()
    token = current_cancellation.get()
    if token is not None:
        token.check()


def cancellation_sleep(seconds):
    from backend.queue.runtime import current_job
    if current_cancellation.get() is None and current_job.get() is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


class CancellationExecutor(ThreadPoolExecutor):
    """Propagate the submission context and skip cancelled queued work."""

    def submit(self, fn, /, *args, **kwargs):
        context = copy_context()

        def run():
            check_cancelled()
            result = fn(*args, **kwargs)
            check_cancelled()
            return result

        return super().submit(context.run, run)
