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

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.template_extractor import (
    TemplateExtractor,
    _apply_diagram_urls,
    _diagram_questions_for,
    _build_header_fr_crop_prompt,
    _fr_model_crop_specs,
    _fr_question_crop_specs,
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


def test_header_fr_prompt_includes_per_question_diagram_hints():
    template = get_template_registry().get_or_raise("seamo_x_2026_a")
    prompt = _build_header_fr_crop_prompt(
        template,
        _fr_model_crop_specs(template.sections),
    )

    assert "Q4: Read the drawn hands" in prompt
    assert "Q6: Return exactly '2x2 grid" in prompt
    assert "Q9: The circle has 12 fixed sectors" in prompt
    assert "never use a vague direction" in prompt


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


@pytest.mark.parametrize(
    ("template_id", "column_questions"),
    [
        ("seamo_x_2026_a", ((1, 6), (7, 13), (14, 20))),
        ("seamo_x_2026_b", ((1, 5), (6, 10), (11, 15))),
        ("seamo_x_2026_c", ((1, 5), (6, 10), (11, 15))),
        ("seamo_x_2026_d", ((1, 5), (6, 10), (11, 15))),
    ],
)
def test_fr_crop_specs_map_seamo_x_questions_to_column_slabs(
    template_id, column_questions,
):
    template = get_template_registry().get_or_raise(template_id)
    section = template.sections[0]
    specs = {q_start: region for q_start, _q_end, _kind, region in
             _fr_question_crop_specs([section])}
    section_left = section.region.x
    section_right = section.region.x + section.region.w
    column_count = len(column_questions)

    for column_index, (q_start, q_end) in enumerate(column_questions):
        slab_left = section_left + section.region.w * column_index // column_count
        slab_right = section_left + section.region.w * (column_index + 1) // column_count
        for question in range(q_start, q_end + 1):
            override = section.question_overrides.get(question)
            if override and override.region:
                assert specs[question] == override.region
            else:
                assert specs[question].x == slab_left
                assert specs[question].w == slab_right - slab_left
                assert specs[question] != section.region
            assert section_left <= specs[question].x
            assert specs[question].x + specs[question].w <= section_right


@pytest.mark.parametrize(
    ("template_id", "same_row_questions", "last_row_questions"),
    [
        ("seamo_x_2026_a", (1, 7, 14), (13, 20)),
        ("seamo_x_2026_b", (1, 6, 11), (10, 15)),
        ("seamo_x_2026_c", (1, 6, 11), (5, 10, 15)),
        ("seamo_x_2026_d", (1, 6, 11), (5, 10, 15)),
    ],
)
def test_fr_crop_specs_use_next_row_only_in_same_seamo_x_column(
    template_id, same_row_questions, last_row_questions,
):
    template = get_template_registry().get_or_raise(template_id)
    section = template.sections[0]
    specs = {q_start: region for q_start, _q_end, _kind, region in
             _fr_question_crop_specs([section])}

    first_row_regions = [specs[question] for question in same_row_questions]
    assert len({region.y for region in first_row_regions}) == 1
    assert len({region.h for region in first_row_regions}) == 1

    last_row_regions = [specs[question] for question in last_row_questions]
    assert len({region.y for region in last_row_regions}) == 1
    assert len({region.h for region in last_row_regions}) == 1
    assert all(region.y > section.region.y for region in last_row_regions)
    assert all(region.h < section.region.h for region in last_row_regions)


def test_fr_crop_specs_preserve_one_column_uz_geometry():
    template = get_template_registry().get_or_raise("seamo_2025_a")
    section = next(s for s in template.sections if s.type == "numeric_grid")

    specs = _fr_question_crop_specs([section])

    assert [
        (q_start, (region.x, region.y, region.w, region.h))
        for q_start, _q_end, _kind, region in specs
    ] == [
        (21, (800, 1148, 900, 212)),
        (22, (800, 1365, 900, 212)),
        (23, (800, 1582, 900, 212)),
        (24, (800, 1799, 900, 212)),
        (25, (800, 2016, 900, 147)),
    ]


@pytest.mark.parametrize(
    ("template_id", "expected_ranges"),
    [
        ("seamo_x_2026_a", [(1, 6), (7, 13), (14, 20)]),
        ("seamo_x_2026_b", [(1, 5), (6, 10), (11, 15)]),
        ("seamo_x_2026_c", [(1, 5), (6, 10), (11, 15)]),
        ("seamo_x_2026_d", [(1, 5), (6, 10), (11, 15)]),
    ],
)
def test_fr_model_crop_specs_keep_seamo_x_visual_columns_intact(
    template_id, expected_ranges,
):
    template = get_template_registry().get_or_raise(template_id)
    section = template.sections[0]

    specs = _fr_model_crop_specs([section])

    assert [(q_start, q_end) for q_start, q_end, _kind, _region in specs] == expected_ranges
    assert len(specs) == 3
    assert all(region.y == section.region.y for *_prefix, region in specs)
    assert all(region.h == section.region.h for *_prefix, region in specs)
    assert sum(region.w for *_prefix, region in specs) == section.region.w
    assert specs[0][3].x == section.region.x
    assert specs[-1][3].x + specs[-1][3].w == section.region.x + section.region.w


def test_fr_model_crop_specs_preserve_one_column_focused_crops():
    template = get_template_registry().get_or_raise("seamo_2025_a")
    section = next(s for s in template.sections if s.type == "numeric_grid")

    assert _fr_model_crop_specs([section]) == _fr_question_crop_specs([section])


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


def test_diagram_overlay_uses_mathpix_source_page_suffix_without_drift():
    candidates = _make_candidates(3)
    for candidate in candidates:
        candidate["diagram_qs"] = [5]
        candidate["template_id"] = "seamo_x_2026_b"
    markdown = "\n".join([
        "Question 5",
        # Page 1 has no figure URL. The first URL belongs to page 2 and must
        # never be written into page 1 just because it appears first.
        "Question 5",
        "![](https://cdn.mathpix.com/cropped/job-02.jpg?height=270&width=330&top_left_y=2030&top_left_x=100)",
        "Question 5",
        "![](https://cdn.mathpix.com/cropped/job-03.jpg?height=270&width=330&top_left_y=2030&top_left_x=100)",
    ])

    _apply_diagram_urls(candidates, markdown)

    assert candidates[0]["answers"]["5"] == ""
    assert "job-02.jpg" in candidates[1]["answers"]["5"]
    assert "job-03.jpg" in candidates[2]["answers"]["5"]


def test_diagram_overlay_rejects_same_page_non_diagram_grid_crop():
    candidates = _make_candidates(1)
    candidates[0]["diagram_qs"] = [5]
    candidates[0]["template_id"] = "seamo_x_2026_b"
    markdown = "\n".join([
        "Question 5",
        # Right-column, one-row crop: this is an ordinary answer grid, not Q5.
        "![](https://cdn.mathpix.com/cropped/job-01.jpg?height=85&width=584&top_left_y=2032&top_left_x=726)",
    ])

    _apply_diagram_urls(candidates, markdown)

    assert candidates[0]["answers"]["5"] == ""


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


def _complete_format_b_payload(**answer_overrides):
    answers = {str(q): str(q) for q in range(21, 26)}
    answers.update({str(k): v for k, v in answer_overrides.items()})
    return json.dumps({
        "header": {
            "candidate_name": "Alice",
            "candidate_number": "CAN123",
            "school": "Example School",
            "country": "Tunisia",
            "level": "Grade 3",
            "date": "2026-07-31",
        },
        "answers": answers,
    })


def test_header_fr_primary_is_one_labeled_call(monkeypatch):
    template = get_template_registry().get_or_raise("seamo_2025_b_fb")
    calls = []

    class FakeModel:
        def generate_content(self, contents, **kwargs):
            calls.append((contents, kwargs))
            return _StubGeminiResponse(_complete_format_b_payload())

    extractor = TemplateExtractor(
        template.id,
        shared_model=FakeModel(),
        shared_model_name="fake",
    )
    bgr = np.full((template.page_size[1], template.page_size[0], 3), 255, np.uint8)
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    header, answers, header_ok, fr_ok, review = extractor._llm_extract_header_fr(
        pil, bgr, 1, template,
    )

    assert len(calls) == 1
    contents, kwargs = calls[0]
    labels = [part for part in contents if isinstance(part, str)][1:]
    assert labels == [
        "IMAGE 1: HEADER",
        "IMAGE 2: FREE RESPONSE Q21",
        "IMAGE 3: FREE RESPONSE Q22",
        "IMAGE 4: FREE RESPONSE Q23",
        "IMAGE 5: FREE RESPONSE Q24",
        "IMAGE 6: FREE RESPONSE Q25",
    ]
    assert kwargs["request_options"]["timeout"] == 60.0
    assert header["candidate_number"] == "CAN123"
    assert answers["25"] == "25"
    assert header_ok and fr_ok and not review


def test_header_fr_seamo_x_primary_sends_three_labeled_visual_columns():
    template = get_template_registry().get_or_raise("seamo_x_2026_a")
    calls = []
    payload = json.dumps({
        "header": {
            "candidate_name": "Alice",
            "candidate_number": "CAN123",
        },
        "answers": {str(q): str(q) for q in range(1, 21)},
    })

    class FakeModel:
        def generate_content(self, contents, **kwargs):
            calls.append(contents)
            return _StubGeminiResponse(payload)

    extractor = TemplateExtractor(
        template.id,
        shared_model=FakeModel(),
        shared_model_name="fake",
    )
    bgr = np.full((template.page_size[1], template.page_size[0], 3), 255, np.uint8)
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    header, answers, header_ok, fr_ok, review = extractor._llm_extract_header_fr(
        pil, bgr, 1, template,
    )

    assert len(calls) == 1
    labels = [part for part in calls[0] if isinstance(part, str)][1:]
    assert labels == [
        "IMAGE 1: HEADER",
        "IMAGE 2: FREE RESPONSES Q1-Q6",
        "IMAGE 3: FREE RESPONSES Q7-Q13",
        "IMAGE 4: FREE RESPONSES Q14-Q20",
    ]
    assert header["candidate_number"] == "CAN123"
    assert answers["20"] == "20"
    assert header_ok and fr_ok and not review


def test_header_fr_retries_only_incomplete_fr_component():
    primary = json.loads(_complete_format_b_payload())
    primary["answers"].pop("25")
    responses = [json.dumps(primary), _complete_format_b_payload(**{"25": "filled"})]
    calls = []

    class FakeModel:
        def generate_content(self, contents, **kwargs):
            calls.append(contents)
            return _StubGeminiResponse(responses.pop(0))

    template = get_template_registry().get_or_raise("seamo_2025_b_fb")
    extractor = TemplateExtractor(
        template.id,
        shared_model=FakeModel(),
        shared_model_name="fake",
    )
    bgr = np.full((template.page_size[1], template.page_size[0], 3), 255, np.uint8)
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    header, answers, header_ok, fr_ok, review = extractor._llm_extract_header_fr(
        pil, bgr, 1, template,
    )

    assert len(calls) == 2
    assert "HEADER FIELDS" in calls[0][0]
    assert "FREE-RESPONSE answers" in calls[1][0]
    assert header["candidate_name"] == "Alice"
    assert answers["25"] == "filled"
    assert header_ok and fr_ok and not review


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
