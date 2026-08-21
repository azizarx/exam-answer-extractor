"""Vision judge: does a candidate's drawing mean the same as the key's drawing?

A diagram answer cannot survive a text round-trip.  Describing a drawing as a
canonical string and matching that string against the key's prose puts two
independent failure points in front of every mark.  This judge removes the
round-trip: it shows the model the candidate's scanned drawing next to the
answer key's own drawing for the same question and asks whether they mean the
same thing.

The two images are sent as separate labelled parts rather than stitched into
one canvas — that is how every other multi-image call in this codebase works,
and the reference drawings are small enough (~190px) that rescaling them into a
shared canvas would cost detail the comparison depends on.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Protocol

import google.generativeai as genai
from PIL import Image

from backend.config import get_settings
from backend.services.gemini_client import create_gemini_model
from backend.services.judge_json import loads_lenient
from backend.services.run_logger import llm_call

logger = logging.getLogger(__name__)

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "answer_keys" / "reference_diagrams"

VERDICTS = frozenset({"match", "mismatch", "uncertain"})


def reference_diagram_path(template_id: str, question: int) -> Optional[Path]:
    """The key's drawing for this question, or None when it is not committed.

    ``template_id`` reaches this from a request path, so the resolved file must
    be a direct child of the reference directory — a traversal attempt resolves
    elsewhere and returns None.
    """
    try:
        path = (REFERENCE_DIR / f"{template_id}_q{int(question)}.png").resolve()
    except (OSError, ValueError):
        return None
    if path.parent != REFERENCE_DIR.resolve():
        logger.warning("Refused reference path outside %s: %s", REFERENCE_DIR, path)
        return None
    return path if path.is_file() else None


class DiagramVisionJudge(Protocol):
    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return list of {question_number, verdict, observed, reason}."""


def build_judge_prompt(items: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {
            "questions": [
                {
                    "question_number": item["question_number"],
                    "accepted_answers": list(item.get("accepted_answers") or []),
                }
                for item in items
            ]
        },
        ensure_ascii=False,
    )
    return (
        "You are an unbiased exam marking assistant comparing hand-drawn diagram "
        "answers.\n"
        "\n"
        "For each question you are given two images, in order:\n"
        "  STUDENT — the candidate's answer sheet region, as scanned\n"
        "  CORRECT — the official answer key's drawing for that same question\n"
        "\n"
        "Both images show the same preprinted scaffold (a clock face, a grid, a "
        "sector circle, a node network). Only what has been DRAWN or WRITTEN onto "
        "that scaffold is the answer.\n"
        "\n"
        "Rules:\n"
        "- Compare only the candidate-variable content: drawn hands, shading, "
        "crosses, circles, and handwritten values. The preprinted scaffold is "
        "identical by construction and is never itself the answer.\n"
        "- The STUDENT image is a pencil scan: strokes are faint, may be smudged, "
        "and the page may be slightly rotated or scaled. Judge what was meant, "
        "not neatness.\n"
        "- The two images are NOT pixel-comparable — they differ in size, "
        "resolution, and print quality. Only the meaning of the marks matters.\n"
        "- Marks that have been erased or rubbed out do not count as an answer.\n"
        "- accepted_answers describes the key in words and may help you name what "
        "you see. The CORRECT image is the authority when they appear to differ.\n"
        "- Verdict is 'match' only when the candidate's marks convey the SAME "
        "answer as the key. Different values are 'mismatch'. Do NOT give benefit "
        "of the doubt.\n"
        "- An empty scaffold is a wrong answer, not an unreadable one: verdict "
        "'mismatch'. Reserve 'uncertain' for marks you genuinely cannot make out.\n"
        "- 'observed' states in a few words what the STUDENT drew, for example "
        "\"Clock: 8:45\" or \"2x2 grid: TL=O, BL=crossed O, others blank\".\n"
        "- Verdict must be exactly one of: match, mismatch, uncertain.\n"
        "- Always include a short plain-text reason. Do not use LaTeX backslash "
        "commands.\n"
        "\n"
        "Return ONLY valid JSON (no markdown fences) with schema:\n"
        '{"items":[{"question_number":<int>,"verdict":"<str>","observed":"<str>",'
        '"reason":"<str>"},...]}\n'
        "\n"
        f"QUESTIONS:\n{payload}\n"
    )


def parse_judge_response(text: str) -> list[dict[str, Any]]:
    data = loads_lenient(text)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("judge response missing items list")

    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or "question_number" not in item:
            continue
        verdict = str(item.get("verdict") or "uncertain").strip().lower()
        cleaned.append(
            {
                "question_number": int(item["question_number"]),
                "verdict": verdict if verdict in VERDICTS else "uncertain",
                "observed": str(item.get("observed") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    if not cleaned:
        raise ValueError("judge response had no usable items")
    return cleaned


def build_contents(items: list[dict[str, Any]]) -> list[Any]:
    """Prompt followed by a labelled STUDENT/CORRECT image pair per question."""
    contents: list[Any] = [build_judge_prompt(items)]
    index = 1
    for item in items:
        question = item["question_number"]
        for label, key in (("STUDENT", "student_path"), ("CORRECT", "reference_path")):
            with Image.open(item[key]) as handle:
                image = handle.convert("RGB")
            contents.append(f"IMAGE {index}: {label} Q{question}")
            contents.append(image)
            index += 1
    return contents


class GeminiDiagramVisionJudge:
    def __init__(self, model=None, model_name: str | None = None):
        if model is None:
            preferred = (get_settings().diagram_vision_model or "").strip()
            self.model, self.model_name = create_gemini_model(
                preferred_model=preferred or None,
            )
        else:
            self.model = model
            self.model_name = model_name or "injected"

    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            return []
        contents = build_contents(items)
        response = llm_call(
            "diagram_vision_judge",
            self.model,
            contents,
            genai.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
            logger,
        )
        text = getattr(response, "text", None) or ""
        return parse_judge_response(text)
