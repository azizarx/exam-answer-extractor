"""Exercise the OCR call boundary under concurrent submissions."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from types import SimpleNamespace

import numpy as np

from backend.services import page_layout_classifier as classifier


def test_concurrent_ocr_calls_share_a_process_budget(monkeypatch):
    from backend.services import cpu_limits

    cpu_limits.configure_cpu_limits.cache_clear()
    cpu_limits.ocr_semaphore.cache_clear()
    monkeypatch.setattr(cpu_limits, 'get_settings', lambda: SimpleNamespace(
        max_ocr_workers=2, opencv_threads=1,
    ))
    active = peak = 0
    lock = threading.Lock()

    def ocr(_image, **kwargs):
        nonlocal active, peak
        assert kwargs['timeout'] > 0
        assert os.environ['OMP_THREAD_LIMIT'] == '1'
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return 'SEAMO 2025 Paper A'

    monkeypatch.setattr(classifier.pytesseract, 'image_to_string', ocr)
    image = np.full((100, 80, 3), 255, dtype=np.uint8)
    try:
        with ThreadPoolExecutor(max_workers=8) as callers:
            results = list(callers.map(lambda _: classifier._ocr_band(image, band='footer'), range(12)))
        assert peak == 2
        assert results == ['SEAMO 2025 Paper A'] * 12
    finally:
        cpu_limits.configure_cpu_limits.cache_clear()
        cpu_limits.ocr_semaphore.cache_clear()


def test_timed_out_ocr_releases_capacity_and_preserves_fallback(monkeypatch):
    from backend.services import cpu_limits

    cpu_limits.ocr_semaphore.cache_clear()
    monkeypatch.setattr(cpu_limits, 'get_settings', lambda: SimpleNamespace(
        max_ocr_workers=1, opencv_threads=1,
    ))
    calls = []

    def ocr(_image, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) == 1:
            raise RuntimeError('Tesseract process timeout')
        return 'SEAMO 2025 Paper A'

    monkeypatch.setattr(classifier.pytesseract, 'image_to_string', ocr)
    image = np.full((100, 80, 3), 255, dtype=np.uint8)
    try:
        assert classifier._ocr_band(image, band='footer') == ''
        assert classifier._ocr_band(image, band='header') == 'SEAMO 2025 Paper A'
        assert len(calls) == 2
    finally:
        cpu_limits.ocr_semaphore.cache_clear()


def test_cancelled_submission_does_not_wait_for_an_ocr_slot(monkeypatch):
    from backend.services import cpu_limits
    from backend.services.cancellation import (
        CancellationExecutor, ExtractionCancelled, current_cancellation,
    )
    import pytest

    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr(cpu_limits, 'ocr_semaphore', lambda: semaphore)
    cancelled = threading.Event()
    entered = threading.Event()

    class Token:
        def check(self):
            entered.set()
            if cancelled.is_set():
                raise ExtractionCancelled()

    context = current_cancellation.set(Token())
    image = np.full((100, 80, 3), 255, dtype=np.uint8)
    try:
        with CancellationExecutor(max_workers=1) as pool:
            future = pool.submit(classifier._ocr_band, image, band='footer')
            assert entered.wait(2)
            cancelled.set()
            with pytest.raises(ExtractionCancelled):
                future.result(timeout=2)
    finally:
        semaphore.release()
        current_cancellation.reset(context)
