"""Batched LLM judge: is an extracted FR response equivalent to accepted answers?"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import google.generativeai as genai

from backend.services.gemini_client import create_gemini_model
from backend.services.judge_json import loads_lenient
from backend.services.run_logger import llm_call

logger = logging.getLogger(__name__)


class FrEquivalenceJudge(Protocol):
    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return list of {question_number, verdict, reason}."""


def build_judge_prompt(items: list[dict[str, Any]]) -> str:
    payload = json.dumps({"items": items}, ensure_ascii=False)
    return (
        "You are an unbiased exam marking assistant.\n"
        "For each item, decide if the candidate's extracted response is equivalent "
        "in meaning to ANY of the accepted_answers.\n"
        "\n"
        "Rules:\n"
        "- Compare only the provided strings. Do not invent context.\n"
        "- Allow format/spelling variants that preserve the same value "
        "(e.g. nine≡9, 5:00vaqt≡5:00 PM, 0=19≡19 when 19 is the value).\n"
        "- Units must not conflict (5m is NOT equivalent to 5km).\n"
        "- If the key has no unit and the response adds a non-conflicting word/gloss "
        "that does not change the quantity, that may be equivalent.\n"
        "- Mathematical expressions may use algebraically equivalent expanded, "
        "factored, fractional, decimal, Unicode, or LaTeX forms.\n"
        "- For type=diagram, compare only the candidate-variable content: labels, "
        "drawn marks/hands, and shading. Geometry or connections described only by "
        "the key may be a preprinted scaffold and need not be repeated by the response.\n"
        "- A concise diagram description is sufficient when it fixes the same answer "
        "(for example, 'Clock: 7:45' conveys the keyed hand positions). Do not demand "
        "details already implied by that concise answer.\n"
        "- In compact 2x2-grid descriptions, TL/TR/BL/BR before '=' mean positions; "
        "O means a circle, X or 'crossed O' means a crossed circle, and BL after '=' "
        "means that cell was blank.\n"
        "- Different values are not_equivalent. Do NOT give benefit of the doubt.\n"
        "- If unsure, verdict must be uncertain.\n"
        "- Verdict must be exactly one of: equivalent, not_equivalent, uncertain.\n"
        "- Always include a short plain-text reason. Do not use LaTeX backslash commands.\n"
        "\n"
        "Return ONLY valid JSON (no markdown fences) with schema:\n"
        '{"items":[{"question_number":<int>,"verdict":"<str>","reason":"<str>"},...]}\n'
        "\n"
        f"INPUT:\n{payload}\n"
    )


def parse_judge_response(text: str) -> list[dict[str, Any]]:
    data = loads_lenient(text)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("judge response missing items list")
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "question_number" not in item:
            continue
        cleaned.append(
            {
                "question_number": int(item["question_number"]),
                "verdict": str(item.get("verdict") or "uncertain"),
                "reason": str(item.get("reason") or ""),
            }
        )
    if not cleaned:
        raise ValueError("judge response had no usable items")
    return cleaned


class GeminiFrEquivalenceJudge:
    def __init__(self, model=None, model_name: str | None = None):
        if model is None:
            self.model, self.model_name = create_gemini_model()
        else:
            self.model = model
            self.model_name = model_name or "injected"

    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            return []
        prompt = build_judge_prompt(items)
        response = llm_call(
            "fr_equivalence_judge",
            self.model,
            [prompt],
            genai.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
            logger,
        )
        text = getattr(response, "text", None) or ""
        return parse_judge_response(text)
