"""Acceptance tests for answer-key-blind Paper K MCQ extraction.

Paper K is the only two-column SEAMO layout.  Its gold pages were chosen for
failure modes (85-97% content scale, blur, large offsets) and annotated by hand
with scripts/annotate_mcq_grid.py; see the fixture's SELECTION.md.
"""

import json
from pathlib import Path

import cv2
import pytest

from backend.services.gold_eval import load_gold_pages
from backend.services.mcq_extractor import ambiguous_mcq_questions, extract_page
from backend.services.page_deskew import deskew_if_enabled
from backend.services.template_extractor import MCQ_TRUST_WARNINGS
from backend.services.template_service import get_template_registry


GOLD_ROOT = Path("tests/fixtures/gold/seamo_2025_paper_k")
# Grid registration must land on the printed boxes, not merely near them.  A
# one-row slip is ~145 px on these sheets; the measured worst case is 7 px.
MAX_REGISTRATION_ERROR_PX = 10


def _extract(image_path: Path, template_id: str):
    registry = get_template_registry()
    image = cv2.imread(str(image_path))
    assert image is not None, image_path
    image, _ = deskew_if_enabled(image, enabled=True)
    # Production passes the unadapted template; extract_page adapts it itself.
    template = registry.get_or_raise(template_id)
    return extract_page(image, template, page_number=1, registry=registry, deskew=False)


def _gold_pages():
    pages = load_gold_pages(GOLD_ROOT)
    assert len(pages) == 10, [p["page_id"] for p in pages]
    return pages


def test_paper_k_cv_extracts_every_mcq_exactly():
    """The production CV path must reproduce all visible Q1-Q15 marks."""
    errors = []
    for gold in _gold_pages():
        result = _extract(Path(gold["_path"]).with_suffix(".png"), gold["template_id"])
        predicted = result.answers
        for question in range(1, 16):
            expected = str(gold["answers"][str(question)]).upper()
            actual = str(predicted.get(str(question), "BL")).upper()
            if actual != expected:
                errors.append(
                    (gold["page_id"], question, expected, actual, result.warning)
                )
    assert not errors, errors


def test_paper_k_registers_by_label_lattice_on_the_annotated_grid():
    """Every option centre and box top must sit on the hand-annotated grid."""
    errors = []
    for gold in _gold_pages():
        page = Path(gold["_path"])
        result = _extract(page.with_suffix(".png"), gold["template_id"])
        assert result.overlay_mcq_sections, (gold["page_id"], result.warning)
        grid = result.overlay_mcq_sections[0].grid
        truth = json.loads(
            (page.parent / f"grid_{page.stem}.json").read_text(encoding="utf-8")
        )["raw_clicks"]["blocks"]

        start = 0
        for visual, block in enumerate(truth):
            count = len(block["fill_top_ys"])
            box_tops = grid.row_positions[start : start + count]
            start += count
            for got, want in zip(box_tops, block["fill_top_ys"]):
                if abs(got - want) > MAX_REGISTRATION_ERROR_PX:
                    errors.append((gold["page_id"], "row", visual, got, round(want)))
            for got, want in zip(grid.col_positions[visual], block["col_xs"]):
                if abs(got - want) > MAX_REGISTRATION_ERROR_PX:
                    errors.append((gold["page_id"], "col", visual, got, round(want)))
    assert not errors, errors


def _trust_cases():
    data = json.loads(
        (GOLD_ROOT / "trust_cases" / "trust_cases.json").read_text(encoding="utf-8")
    )
    return data["pages"]


@pytest.mark.parametrize("case", _trust_cases(), ids=lambda c: c["image"])
def test_paper_k_never_trusts_a_double_or_faint_mark(case):
    """Rows that were once confidently wrong must now go to review.

    Trust is decided exactly as production decides it: a page-level MCQ trust
    warning, an ``IN`` answer, or a question from ``ambiguous_mcq_questions``.
    """
    template = get_template_registry().get_or_raise(case["template_id"])
    scoring = next(s for s in template.sections if s.type == "mcq_grid").scoring
    result = _extract(GOLD_ROOT / "trust_cases" / case["image"], case["template_id"])
    review = set(
        ambiguous_mcq_questions(
            result,
            min_ratio=float(scoring.min_ratio),
            min_ink_pixels=int(scoring.min_ink_pixels),
            blank_review_ink=int(scoring.blank_review_ink),
        )
    )
    page_untrusted = (result.warning or "") in MCQ_TRUST_WARNINGS

    errors = []
    for question, expected in enumerate(case["answers"], start=1):
        actual = str(result.answers.get(str(question), "BL")).upper()
        trusted = (
            not page_untrusted and str(question) not in review and actual != "IN"
        )
        if expected == "?":
            if trusted:
                errors.append((question, "trusted", actual))
        elif expected == ".":
            if actual != "BL" or not trusted:
                errors.append((question, "BL", actual, trusted))
        elif actual != expected or not trusted:
            errors.append((question, expected, actual, trusted))
    assert not errors, errors


@pytest.mark.parametrize("page", ["page_001.png", "page_002.png"])
def test_numeric_option_sheet_is_never_trusted(page):
    """A sheet printed with 1/2/3 options and written Q11-15 is not Paper K.

    It carries the same "SEAMO 2025 Paper K" footer and format-B marker, so it
    classifies as seamo_2025_k_fb.  The lattice must refuse it (there is no
    right-hand box column) and whatever the fallback reads must be flagged.
    """
    result = _extract(GOLD_ROOT / "numeric_option_sheet" / page, "seamo_2025_k_fb")
    assert not result.overlay_mcq_sections
    assert result.warning in MCQ_TRUST_WARNINGS, result.warning
