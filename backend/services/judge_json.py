"""Lenient JSON decoding for LLM judge responses.

Gemini returns JSON under ``response_mime_type="application/json"``, but not
reliably clean: responses arrive wrapped in markdown fences, surrounded by
prose, or containing LaTeX-like text whose backslashes are not valid JSON
escapes.  Every judge needs the same tolerance, so it lives here once.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_INVALID_JSON_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def loads_lenient(text: str) -> Any:
    """Parse a judge response, tolerating fences, prose, and bad escapes.

    Raises ``ValueError`` on empty input and ``json.JSONDecodeError`` when the
    payload is not recoverable — callers treat both as "needs review" rather
    than guessing at a verdict.
    """
    if not text or not str(text).strip():
        raise ValueError("empty judge response")
    raw = str(text).strip()

    fence = _FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Repair only backslashes that cannot begin a valid JSON escape (``\sqrt``,
        # ``\(``); every other syntax error stays a failure and is reviewed.
        repaired = _INVALID_JSON_ESCAPE_RE.sub(r"\\\\", raw)
        if repaired == raw:
            raise
        return json.loads(repaired)
