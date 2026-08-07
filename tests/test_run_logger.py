import logging
from types import SimpleNamespace

import pytest

from backend.services import run_logger

logger = logging.getLogger(__name__)


class DeadlineExceeded(Exception):
    pass


def test_llm_call_has_one_bounded_retry_owner(monkeypatch):
    attempts = []
    acquired = []
    sleeps = []

    class Model:
        def generate_content(self, contents, **kwargs):
            attempts.append(kwargs)
            if len(attempts) < 3:
                raise DeadlineExceeded("deadline")
            return SimpleNamespace(text='{"ok":true}', candidates=[])

    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: SimpleNamespace(
            gemini_transient_retries=2,
            gemini_request_timeout_seconds=60.0,
            gemini_max_rpm=0,
        ),
    )
    monkeypatch.setattr(
        run_logger,
        "_acquire_gemini_token",
        lambda stage, logger: acquired.append(stage),
    )
    monkeypatch.setattr(run_logger.time, "sleep", lambda value: sleeps.append(value))
    generation = SimpleNamespace(temperature=0.0, max_output_tokens=None)

    response = run_logger.llm_call(
        "bounded",
        Model(),
        ["prompt"],
        generation,
        logger,
    )

    assert response.text == '{"ok":true}'
    assert len(attempts) == 3
    assert acquired == ["bounded", "bounded", "bounded"]
    assert sleeps == [2.0, 5.0]
    assert all(a["request_options"] == {"timeout": 60.0} for a in attempts)


def test_llm_call_stops_after_configured_attempts(monkeypatch):
    attempts = 0

    class Model:
        def generate_content(self, contents, **kwargs):
            nonlocal attempts
            attempts += 1
            raise DeadlineExceeded("deadline")

    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: SimpleNamespace(
            gemini_transient_retries=2,
            gemini_request_timeout_seconds=1.0,
            gemini_max_rpm=0,
        ),
    )
    monkeypatch.setattr(run_logger, "_acquire_gemini_token", lambda *args: None)
    monkeypatch.setattr(run_logger.time, "sleep", lambda _value: None)

    with pytest.raises(DeadlineExceeded):
        run_logger.llm_call(
            "bounded",
            Model(),
            ["prompt"],
            SimpleNamespace(temperature=0.0, max_output_tokens=None),
            logger,
        )

    assert attempts == 3
