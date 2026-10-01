"""Bound native parallelism across concurrent submissions.

Page-level workers already supply parallelism. Tesseract's default OpenMP
team per page oversubscribes the host and severely reduces OCR throughput.
"""
from contextlib import contextmanager
from functools import lru_cache
import os
import threading

import cv2

from backend.config import get_settings
from backend.services.cancellation import check_cancelled


@lru_cache(maxsize=1)
def configure_cpu_limits():
    # Inherited by every Tesseract subprocess, including non-layout callers.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    cv2.setNumThreads(get_settings().opencv_threads)


@lru_cache(maxsize=1)
def ocr_semaphore():
    return threading.BoundedSemaphore(get_settings().max_ocr_workers)


@contextmanager
def ocr_slot():
    configure_cpu_limits()
    semaphore = ocr_semaphore()
    check_cancelled()
    while not semaphore.acquire(timeout=0.1):
        check_cancelled()
    try:
        check_cancelled()
        yield
    finally:
        semaphore.release()
