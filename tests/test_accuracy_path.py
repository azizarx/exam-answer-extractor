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


def test_classify_row_emits_in_for_multi_fill():
    scoring = ScoringParams(min_ink_pixels=10, min_ratio=1.5)
    ans, best, second, ratio = _classify_row({"A": 40, "B": 38, "C": 2}, scoring)
    assert ans == "IN"
    assert best >= second


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


def test_normalize_integer_accepts_units():
    assert normalize_integer("12 cm").value == "12"
    assert normalize_integer("12.0").value == "12"
    assert normalize_integer("12").value == "12"


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
