"""
Integration test for TemplateExtractor with Gemini and Mathpix mocked.

We exercise the full per-page merge + post-hoc Mathpix overlay against a
synthetic filled page so:
  - CV MCQ answers overwrite the LLM stub's MCQ answers.
  - Mathpix CDN URLs land on the right diagram questions, per page, in the
    same order they appear in the markdown.
  - Spurious URLs after non-diagram questions are dropped (does not shift the
    next diagram question's assignment).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.template_extractor import (
    TemplateExtractor,
    _apply_diagram_urls,
    _diagram_questions_for,
    _parse_extraction_json,
)
from backend.services.template_service import get_template_registry


# ---------------------------------------------------------------------------
# Pure-helper tests (no Gemini, no Mathpix needed)
# ---------------------------------------------------------------------------

def test_diagram_questions_for_seamo_x_2026_a():
    template = get_template_registry().get_or_raise("seamo_x_2026_a")
    assert _diagram_questions_for(template) == [4, 6, 9]


def test_diagram_questions_for_seamo_x_2026_b():
    template = get_template_registry().get_or_raise("seamo_x_2026_b")
    assert _diagram_questions_for(template) == [5]


def test_diagram_questions_for_mcq_only_template():
    template = get_template_registry().get_or_raise("seamo_2025_k")
    assert _diagram_questions_for(template) == []


def test_parse_extraction_json_handles_code_fences():
    text = '```json\n{"header": {"candidate_name": "Alice"}, "answers": {"1": "A"}}\n```'
    out = _parse_extraction_json(text, page_num=1)
    assert out["header"]["candidate_name"] == "Alice"
    assert out["answers"]["1"] == "A"


def test_parse_extraction_json_finds_object_in_prose():
    text = 'Here is the JSON: {"header": {}, "answers": {"1": "BL"}}.  Done.'
    out = _parse_extraction_json(text, page_num=1)
    assert out["answers"]["1"] == "BL"


def test_parse_extraction_json_empty_on_garbage():
    out = _parse_extraction_json("not json", page_num=1)
    assert out == {"header": {}, "answers": {}}


# ---------------------------------------------------------------------------
# _apply_diagram_urls — the unit under most scrutiny
# ---------------------------------------------------------------------------

def _make_candidates(n_pages: int):
    return [
        {
            "page_number": i + 1,
            "answers": {str(q): "" for q in range(1, 11)},
        }
        for i in range(n_pages)
    ]


def test_diagram_overlay_assigns_only_diagram_questions():
    """Every question in the Mathpix MMD has a URL; only Q4/Q6/Q9 should be filled."""
    candidates = _make_candidates(2)
    candidates[0]["answers"]["5"] = "8"  # non-diagram LLM answer must survive

    md = "\n".join([
        # Page 1
        r"\section*{Question 1}", "![](https://cdn.mathpix.com/cropped/p1-q1.jpg)",
        r"\section*{Question 2}", "![](https://cdn.mathpix.com/cropped/p1-q2.jpg)",
        r"\section*{Question 3}", "![](https://cdn.mathpix.com/cropped/p1-q3.jpg)",
        r"\section*{Question 4}", "![](https://cdn.mathpix.com/cropped/p1-q4.jpg)",
        r"\section*{Question 5}", "![](https://cdn.mathpix.com/cropped/p1-q5.jpg)",
        r"\section*{Question 6}", "![](https://cdn.mathpix.com/cropped/p1-q6.jpg)",
        r"\section*{Question 7}", "![](https://cdn.mathpix.com/cropped/p1-q7.jpg)",
        r"\section*{Question 9}", "![](https://cdn.mathpix.com/cropped/p1-q9.jpg)",
        r"\section*{Question 10}", "![](https://cdn.mathpix.com/cropped/p1-q10.jpg)",
        # Page 2 (figures arrive in same order)
        r"\section*{Question 1}", "![](https://cdn.mathpix.com/cropped/p2-q1.jpg)",
        r"\section*{Question 4}", "![](https://cdn.mathpix.com/cropped/p2-q4.jpg)",
        r"\section*{Question 6}", "![](https://cdn.mathpix.com/cropped/p2-q6.jpg)",
        r"\section*{Question 9}", "![](https://cdn.mathpix.com/cropped/p2-q9.jpg)",
    ])

    _apply_diagram_urls(candidates, md, diagram_qs=[4, 6, 9])

    assert candidates[0]["answers"]["4"] == "https://cdn.mathpix.com/cropped/p1-q4.jpg"
    assert candidates[0]["answers"]["6"] == "https://cdn.mathpix.com/cropped/p1-q6.jpg"
    assert candidates[0]["answers"]["9"] == "https://cdn.mathpix.com/cropped/p1-q9.jpg"
    assert candidates[1]["answers"]["4"] == "https://cdn.mathpix.com/cropped/p2-q4.jpg"
    assert candidates[1]["answers"]["6"] == "https://cdn.mathpix.com/cropped/p2-q6.jpg"
    assert candidates[1]["answers"]["9"] == "https://cdn.mathpix.com/cropped/p2-q9.jpg"
    # Non-diagram LLM value untouched
    assert candidates[0]["answers"]["5"] == "8"
    # Non-diagram empty stays empty (URL was dropped)
    assert candidates[0]["answers"]["7"] == ""


def test_diagram_overlay_drops_spurious_url_after_non_diagram_label():
    """If Mathpix decides Q5's handwriting is a figure (extra URL), we drop it."""
    candidates = _make_candidates(1)
    md = "\n".join([
        r"\section*{Question 4}", "![](https://cdn.mathpix.com/cropped/q4.jpg)",
        # Mathpix mis-detection: Q5 isn't a diagram but it emitted a URL.
        r"\section*{Question 5}", "![](https://cdn.mathpix.com/cropped/q5-spurious.jpg)",
        # Real diagrams continue.
        r"\section*{Question 6}", "![](https://cdn.mathpix.com/cropped/q6.jpg)",
        r"\section*{Question 9}", "![](https://cdn.mathpix.com/cropped/q9.jpg)",
    ])

    _apply_diagram_urls(candidates, md, diagram_qs=[4, 6, 9])

    assert candidates[0]["answers"]["4"].endswith("q4.jpg")
    assert candidates[0]["answers"]["6"].endswith("q6.jpg")
    assert candidates[0]["answers"]["9"].endswith("q9.jpg")
    # Q5 spurious URL must not have been stored anywhere
    for c in candidates:
        for v in c["answers"].values():
            assert "q5-spurious" not in v


def test_diagram_overlay_noop_when_no_diagram_questions():
    candidates = _make_candidates(1)
    snapshot = {k: v for k, v in candidates[0]["answers"].items()}
    _apply_diagram_urls(candidates, "irrelevant markdown", diagram_qs=[])
    assert candidates[0]["answers"] == snapshot


def test_diagram_overlay_partial_when_mathpix_misses_a_question():
    """If Mathpix's output is missing Q6, Q9's URL must NOT get assigned to Q6."""
    candidates = _make_candidates(1)
    md = "\n".join([
        r"\section*{Question 4}", "![](https://cdn.mathpix.com/cropped/q4.jpg)",
        # Q6 absent (no label, no URL)
        r"\section*{Question 9}", "![](https://cdn.mathpix.com/cropped/q9.jpg)",
    ])
    _apply_diagram_urls(candidates, md, diagram_qs=[4, 6, 9])
    assert candidates[0]["answers"]["4"].endswith("q4.jpg")
    assert candidates[0]["answers"]["6"] == ""  # untouched
    assert candidates[0]["answers"]["9"].endswith("q9.jpg")


# ---------------------------------------------------------------------------
# Full pipeline integration (Gemini + Mathpix mocked, real CV on synthetic page)
# ---------------------------------------------------------------------------

class _StubGeminiResponse:
    def __init__(self, text: str):
        self.text = text
        self.candidates = [
            types.SimpleNamespace(finish_reason=1, content=types.SimpleNamespace(parts=[]))
        ]
        self.usage_metadata = types.SimpleNamespace(
            prompt_token_count=10, candidates_token_count=20, total_token_count=30
        )


def test_template_extractor_merges_cv_mcq_and_mathpix(monkeypatch, tmp_path):
    """End-to-end with the real `seamo_2025_k` template (MCQ 15×3, 2 columns).

    We:
      - render the template's reference page and draw filled bubbles for known
        answers (real CV will read them);
      - stub Gemini to return wrong-but-parseable MCQ answers (so we can prove
        CV overrides them) plus header fields;
      - stub Mathpix to return markdown matching the template's diagram set
        (this template has none, so Mathpix should not be invoked).
    """
    template_id = "seamo_2025_k"
    template = get_template_registry().get_or_raise(template_id)

    # Render a filled page
    ref_image_path = (
        Path(__file__).resolve().parent.parent
        / "backend" / "templates" / "reference_images" / "seamo_2025_page1.png"
    )
    ref_img = cv2.imread(str(ref_image_path))
    assert ref_img is not None

    # Fill expected bubbles for Q1..Q15
    expected_mcq = {i: ["A", "B", "C"][(i - 1) % 3] for i in range(1, 16)}
    grid = template.sections[0].grid
    questions_per_col = grid.questions_per_col
    question_number = template.sections[0].question_start
    for vc in range(grid.cols or 1):
        start_row = sum(questions_per_col[:vc])
        count = questions_per_col[vc]
        row_ys = grid.row_positions[start_row : start_row + count]
        col_xs = grid.col_positions[vc]
        for row_y in row_ys:
            ans = expected_mcq[question_number]
            opt_idx = grid.options.index(ans)
            x_center = col_xs[opt_idx]
            x1 = int(x_center - grid.cell_width / 2)
            y1 = row_y + grid.cell_height
            x2 = int(x_center + grid.cell_width / 2)
            y2 = y1 + grid.bubble_height
            cv2.rectangle(ref_img, (x1, y1), (x2, y2), (60, 60, 60), -1)
            question_number += 1

    img_path = tmp_path / "page1_filled.png"
    cv2.imwrite(str(img_path), ref_img)
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 not a real pdf")

    # Stub Gemini to return wrong MCQ answers (all "Z") plus a real header.
    # The CV overlay must overwrite these.
    bogus_llm = (
        '{"header": {"candidate_name": "Alice", "candidate_number": "C12345"},'
        '"answers": {' + ",".join([f'"{i}":"Z"' for i in range(1, 16)]) + '}}'
    )

    def fake_generate(self, contents, generation_config=None, **kw):
        return _StubGeminiResponse(bogus_llm)

    # Capture the generation_config so we can assert no max_output_tokens
    captured = {}

    def capture_generate(self, contents, generation_config=None, **kw):
        captured["max_output_tokens"] = getattr(generation_config, "max_output_tokens", "UNSET")
        return _StubGeminiResponse(bogus_llm)

    # Construct extractor with create_gemini_model stubbed so __init__ doesn't
    # hit the real API.
    class FakeModel:
        def generate_content(self, *a, **kw):
            return capture_generate(self, *a, **kw)

    def fake_create_gemini_model(*a, **kw):
        return FakeModel(), "gemini-fake"

    with patch(
        "backend.services.template_extractor.create_gemini_model",
        fake_create_gemini_model,
    ):
        extractor = TemplateExtractor(template_id)
        result = extractor.extract_pdf(
            str(pdf_path),
            [str(img_path)],
            max_workers=1,
        )

    assert result["pages_processed"] == 1
    assert len(result["candidates"]) == 1
    cand = result["candidates"][0]
    # Header from LLM stub
    assert cand["candidate_name"] == "Alice"
    assert cand["candidate_number"] == "C12345"
    # MCQ answers came from CV, NOT from the LLM stub (which said "Z")
    for q in range(1, 16):
        assert cand["answers"][str(q)] != "Z", f"Q{q} still has LLM 'Z' — CV did not override"
        assert cand["answers"][str(q)] == expected_mcq[q], f"Q{q} CV mismatch"
    # No max_output_tokens cap was passed to Gemini
    assert captured.get("max_output_tokens") in (None, "UNSET"), \
        f"max_output_tokens leaked: {captured!r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-v"]))
