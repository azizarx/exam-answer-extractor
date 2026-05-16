"""
Per-extraction-run file logging.

Attaches a FileHandler to the relevant backend.* loggers for the duration of
one extraction run so every log line — existing and new — is captured to a
dedicated file under storage/pipeline_runs/. Loggers are bumped to DEBUG
inside the context to capture step-level detail.

Usage (recommended, non-CM so it fits the existing try/finally):

    handle = attach_run_log(submission_id, filename, template_id)
    try:
        ...do work...
    finally:
        detach_run_log(handle)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, Optional, Tuple

_RUN_LOG_DIR = Path("storage/pipeline_runs")

# We attach the FileHandler to the parent "backend" logger only and rely on
# Python's child-to-parent propagation to deliver every backend.* log line to
# the handler exactly once. Earlier versions attached the handler to "backend"
# AND each child logger separately, which caused every line to be written
# twice (once at the child, once at the parent it propagates to).
_RUN_LOG_PARENT = "backend"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80]


class RunLogHandle:
    """Opaque handle returned from attach_run_log; pass to detach_run_log."""

    def __init__(self, path: Path, handler: logging.Handler, prior_level: int):
        self.path = path
        self.handler = handler
        self.prior_level = prior_level
        self.started = time.perf_counter()


def attach_run_log(
    submission_id: int,
    filename: str,
    template_id: Optional[str] = None,
) -> RunLogHandle:
    """Attach a per-run FileHandler to the 'backend' parent logger.

    All backend.* loggers propagate up to this parent, so the handler fires
    exactly once per log line regardless of which submodule emitted it.
    """
    _RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = _RUN_LOG_DIR / f"sub{submission_id}_{ts}_{_safe(filename)}.log"

    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s :: %(message)s",
        "%H:%M:%S",
    ))

    parent = logging.getLogger(_RUN_LOG_PARENT)
    prior_level = parent.level
    parent.setLevel(logging.DEBUG)
    parent.addHandler(handler)

    log = logging.getLogger("backend.run")
    log.info(
        "=== RUN START sub=%s file=%r template=%r log=%s",
        submission_id, filename, template_id, path,
    )
    return RunLogHandle(path=path, handler=handler, prior_level=prior_level)


def detach_run_log(handle: Optional[RunLogHandle]) -> None:
    """Remove the per-run FileHandler and restore the parent logger's level."""
    if handle is None:
        return
    elapsed = time.perf_counter() - handle.started
    logging.getLogger("backend.run").info("=== RUN END elapsed=%.2fs", elapsed)
    parent = logging.getLogger(_RUN_LOG_PARENT)
    try:
        parent.removeHandler(handle.handler)
    except Exception:
        pass
    parent.setLevel(handle.prior_level)
    try:
        handle.handler.close()
    except Exception:
        pass


def step_timer(stage: str, logger: logging.Logger):
    """
    Return a context-manager-like object that logs STEP START / STEP DONE
    with elapsed time. Use as:

        with step_timer("pdf_to_images", logger):
            ...
    """
    return _StepTimer(stage, logger)


class _StepTimer:
    def __init__(self, stage: str, logger: logging.Logger):
        self.stage = stage
        self.logger = logger
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.logger.info("STEP[%s] START", self.stage)
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.perf_counter() - self.t0
        if exc is None:
            self.logger.info("STEP[%s] DONE t=%.2fs", self.stage, dt)
        else:
            self.logger.error("STEP[%s] FAIL t=%.2fs err=%s: %s",
                              self.stage, dt, type(exc).__name__, exc)
        return False  # don't suppress


def log_llm_response(
    stage: str,
    t0: float,
    response,
    logger: logging.Logger,
    image_size=None,
    prompt_chars: Optional[int] = None,
) -> None:
    """Emit a structured one-line log for a completed Gemini call."""
    dt = time.perf_counter() - t0
    finish = None
    text = ""
    try:
        if getattr(response, "candidates", None):
            finish = int(response.candidates[0].finish_reason)
    except Exception:
        pass
    try:
        text = (response.text or "").strip()
    except Exception as e:
        text = f"<.text raised: {type(e).__name__}: {e}>"

    usage = getattr(response, "usage_metadata", None)
    parts = [f"t={dt:.2f}s", f"finish={finish}", f"chars={len(text)}"]
    if usage is not None:
        parts.append(
            f"tok(p={usage.prompt_token_count},c={usage.candidates_token_count},total={usage.total_token_count})"
        )
    if prompt_chars is not None:
        parts.append(f"prompt={prompt_chars}c")
    if image_size is not None:
        parts.append(f"img={image_size}")

    logger.info("LLM[%s] OK %s preview=%r", stage, " ".join(parts), text[:300])


def log_llm_error(stage: str, t0: float, exc: BaseException, logger: logging.Logger) -> None:
    dt = time.perf_counter() - t0
    logger.error("LLM[%s] FAIL t=%.2fs err=%s: %s",
                 stage, dt, type(exc).__name__, str(exc)[:300])


# Per-attempt sleeps for HTTP 429 (ResourceExhausted). With the rate limiter
# below, 429s should be rare — these are a safety net for whatever the limiter
# undershoots (e.g. Mathpix or other concurrent users on the same key).
_RATE_LIMIT_BACKOFF_SECONDS = (5.0, 15.0, 30.0, 60.0, 60.0, 60.0, 60.0, 60.0)


# Process-wide token bucket for Gemini calls (sliding 60s window). Cap comes
# from Settings.gemini_max_rpm (read lazily so .env / env var changes apply
# without restarting the importer). Earlier versions read os.environ directly
# at import time, which silently ignored `.env` values because pydantic-
# settings doesn't export `.env` back into os.environ.
_GEMINI_WINDOW_SECONDS = 60.0
_gemini_lock = threading.Lock()
_gemini_calls: Deque[float] = deque()


def _gemini_rpm_cap() -> float:
    """Read the current RPM cap. Imported lazily to break a circular import:
    backend.config → backend.db.* → … → backend.services.run_logger.
    """
    from backend.config import get_settings
    return float(get_settings().gemini_max_rpm)


def _acquire_gemini_token(stage: str, logger: logging.Logger) -> None:
    """Block until we're allowed to make another Gemini call under the RPM cap.

    Sliding-window: keep a deque of recent call timestamps; if we've made
    `gemini_max_rpm` calls in the last `_GEMINI_WINDOW_SECONDS` seconds,
    sleep until the oldest one falls out of the window. Then record this call.
    """
    cap = _gemini_rpm_cap()
    if cap <= 0:
        return
    while True:
        with _gemini_lock:
            now = time.monotonic()
            # Drop calls outside the window
            while _gemini_calls and now - _gemini_calls[0] >= _GEMINI_WINDOW_SECONDS:
                _gemini_calls.popleft()
            if len(_gemini_calls) < cap:
                _gemini_calls.append(now)
                return
            # Need to wait for the oldest call to age out
            wait = _GEMINI_WINDOW_SECONDS - (now - _gemini_calls[0])
        if wait > 0:
            logger.info(
                "LLM[%s] RATE_PACED waiting %.1fs (window has %d calls, cap %s)",
                stage, wait, len(_gemini_calls), cap,
            )
            time.sleep(min(wait, 5.0))  # cap individual sleeps so logs stay live


def llm_call(
    stage: str,
    model,
    contents,
    generation_config,
    logger: logging.Logger,
    image=None,
):
    """Wrap a Gemini generate_content call with structured logging.

    Logs prompt size, image dimensions, latency, finish_reason, token usage,
    and a truncated preview of the response text. Transparently retries on
    429 ResourceExhausted (Gemini rate limit) with exponential backoff so
    parallel workers can recover instead of returning empty headers.
    """
    prompt_text = next((p for p in contents if isinstance(p, str)), "")
    prompt_chars = len(prompt_text)
    image_size = None
    if image is not None and hasattr(image, "size"):
        image_size = image.size
    logger.info(
        "LLM[%s] CALL prompt=%dc image=%s temp=%s max_out=%s",
        stage, prompt_chars, image_size,
        getattr(generation_config, "temperature", "?"),
        getattr(generation_config, "max_output_tokens", "?"),
    )
    _acquire_gemini_token(stage, logger)
    t0 = time.perf_counter()
    attempt = 0
    while True:
        try:
            response = model.generate_content(contents, generation_config=generation_config)
            break
        except Exception as exc:
            # google.api_core.exceptions.ResourceExhausted is the 429 path.
            # We can't import it directly here without dragging in the SDK,
            # so we sniff for the class name + "429" in the str.
            err_name = type(exc).__name__
            msg = str(exc)
            is_429 = (err_name == "ResourceExhausted") or msg.startswith("429") or "RESOURCE_EXHAUSTED" in msg
            if is_429 and attempt < len(_RATE_LIMIT_BACKOFF_SECONDS):
                delay = _RATE_LIMIT_BACKOFF_SECONDS[attempt]
                attempt += 1
                logger.warning(
                    "LLM[%s] 429 rate-limited; sleeping %.0fs (attempt %d/%d)",
                    stage, delay, attempt, len(_RATE_LIMIT_BACKOFF_SECONDS),
                )
                time.sleep(delay)
                continue
            log_llm_error(stage, t0, exc, logger)
            raise
    log_llm_response(stage, t0, response, logger, image_size=image_size, prompt_chars=prompt_chars)
    return response
