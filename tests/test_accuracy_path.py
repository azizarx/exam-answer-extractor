"""Unit tests for gold eval, trust merge helpers, and integer soft-normalize."""

from __future__ import annotations

from backend.services.gold_eval import evaluate_page, summarize
from backend.services.marking_service import apply_extraction_trust, normalize_integer
from backend.services.marking_service import CandidateMarkingResult, QuestionOutcome
from backend.services.mcq_extractor import _classify_row
from backend.services.template_service import ScoringParams
from backend.services.template_extractor import _assemble_candidate
from backend.services.template_service import get_template_registry


def test_gold_eval_flags_silent_wrong_vs_review():
    gold = {"page_id": "p1", "answers": {"1": "A", "2": "B", "3": "C"}}
    page = evaluate_page(
        gold,
        {"1": "A", "2": "Z", "3": "C"},
        needs_review_questions=["2"],
    )
    buckets = {q.question: q.bucket for q in page.questions}
    assert buckets["1"] == "exact_match"
    assert buckets["2"] == "flagged_review"
    assert buckets["3"] == "exact_match"
    assert page.silent_wrong == 0

    page2 = evaluate_page(gold, {"1": "A", "2": "Z", "3": "C"})
    assert page2.silent_wrong == 1
    assert summarize([page2])["meets_zero_silent_wrong"] is False


def test_ambiguous_mcq_flags_in_and_thin_ratio():
    from backend.services.mcq_extractor import (
        PageResult,
        RowResult,
        SectionResult,
        ambiguous_mcq_questions,
    )

    page = PageResult(
        page_number=1,
        template_id="t",
        status="ok",
        sections=[
            SectionResult(
                section_type="mcq_grid",
                question_start=1,
                question_end=3,
                rows=[
                    RowResult(1, "A", 400, 100, 4.0, {"A": 400}),
                    RowResult(2, "IN", 300, 290, 1.03, {"A": 300, "B": 290}),
                    RowResult(3, "E", 260, 216, 1.20, {"E": 260, "D": 216}),
                ],
            )
        ],
    )
    flagged = ambiguous_mcq_questions(page, min_ratio=1.2, min_ink_pixels=180)
    assert "2" in flagged and "3" in flagged
    assert "1" not in flagged
    # Light fill classified BL should also be reviewed.
    page.sections[0].rows.append(RowResult(4, "BL", 146, 99, 1.47, {"C": 146}))
    flagged2 = ambiguous_mcq_questions(page, min_ratio=1.2, min_ink_pixels=180)
    assert "4" in flagged2

    # A synchronized printed-label lattice proves the blank ROI geometry;
    # scan noise in a true blank must not force manual review.
    page.overlay_mcq_sections = [object()]
    flagged3 = ambiguous_mcq_questions(page, min_ratio=1.2, min_ink_pixels=180)
    assert "4" not in flagged3
    assert "2" in flagged3 and "3" in flagged3


def test_assemble_candidate_cv_owns_mcq_and_trust():
    template = get_template_registry().get_or_raise("seamo_2025_k")
    cand = _assemble_candidate(
        page_num=1,
        header={"candidate_name": "Ada", "level": "K"},
        mcq_answers={"1": "A", "2": "BL"},
        fr_answers={},
        review_qs={"2"},
        mcq_warning="low_coverage",
        extraction_flags=["mcq_low_coverage"],
        template=template,
    )
    assert cand["candidate_name"] == "Ada"
    assert cand["answers"]["1"] == "A"
    assert cand["extra_fields"]["needs_review_questions"] == ["2"]
    assert cand["extra_fields"]["answer_trust"]["2"] == "needs_review"
    assert cand["extra_fields"]["answer_trust"]["1"] == "trusted"
    assert cand["extra_fields"]["level"] == "K"


def test_adapted_to_image_scales_low_dpi_pages():
    import numpy as np
    from backend.services.template_service import get_template_registry

    t = get_template_registry().get_or_raise("seamo_2025_d_fb")
    # ~200 DPI page (width ~2/3 of reference 2481)
    img = np.zeros((2330, 1650, 3), dtype=np.uint8)
    adapted, factor = t.adapted_to_image(img)
    assert abs(factor - (1650 / t.page_size[0])) < 0.02 or abs(factor - 1650 / 2481) < 0.05
    assert adapted.sections[0].grid.col_positions[0][0] < t.sections[0].grid.col_positions[0][0]
    assert adapted.sections[0].scoring.min_ink_pixels < t.sections[0].scoring.min_ink_pixels

    # Near-reference width must not rescale (scanner margin noise).
    img300 = np.zeros((3508, 2500, 3), dtype=np.uint8)
    adapted2, factor2 = t.adapted_to_image(img300)
    assert factor2 == 1.0
    assert adapted2 is t


def test_apply_extraction_trust_zeros_marks():
    result = CandidateMarkingResult(
        outcomes=(
            QuestionOutcome(1, "correct", "A", 4, 4, "uppercase"),
            QuestionOutcome(2, "incorrect", "Z", 0, 4, "uppercase"),
        ),
        awarded_marks=4,
        max_marks=8,
        percentage=50.0,
    )
    gated = apply_extraction_trust(result, [1])
    assert gated.outcomes[0].status == "needs_review"
    assert gated.outcomes[0].awarded_marks == 0
    assert gated.awarded_marks == 0
