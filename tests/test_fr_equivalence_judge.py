import json

import pytest

from backend.services.fr_equivalence_judge import (
    build_judge_prompt,
    parse_judge_response,
)
from backend.services.marking_service import (
    AnswerKeyManifest,
    ManifestQuestion,
    MarkingService,
    apply_fr_equivalence_judge,
)


def _tiny_manifest() -> AnswerKeyManifest:
    return AnswerKeyManifest(
        template_id="test_fr",
        version=1,
        source_filename="x.pdf",
        source_sha256="abc",
        total_marks=12,
        questions=(
            ManifestQuestion(1, "mcq", ("A",), 3, "uppercase"),
            ManifestQuestion(21, "numeric", ("19",), 6, "integer"),
            ManifestQuestion(22, "time", ("5:00 PM",), 3, "time_12_24"),
        ),
    )


class FakeJudge:
    def __init__(self, verdicts: dict[int, tuple[str, str]]):
        self.verdicts = verdicts
        self.calls = []

    def judge(self, items: list[dict]) -> list[dict]:
        self.calls.append(items)
        out = []
        for item in items:
            q = item["question_number"]
            verdict, reason = self.verdicts[q]
            out.append({"question_number": q, "verdict": verdict, "reason": reason})
        return out


def test_deterministic_correct_and_blank_skip_judge():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark({"1": "A", "21": "19", "22": ""})
    judge = FakeJudge({})
    merged = apply_fr_equivalence_judge(result, manifest, judge)
    assert judge.calls == []
    assert merged.awarded_marks == result.awarded_marks


def test_llm_equivalent_promotes_invalid_time_to_correct():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark({"1": "A", "21": "19", "22": "5:00vaqt"})
    assert {o.question_number: o.status for o in result.outcomes}[22] == "invalid"
    judge = FakeJudge({22: ("equivalent", "same time; non-conflicting word")})
    merged = apply_fr_equivalence_judge(result, manifest, judge)
    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[22].status == "correct"
    assert by_q[22].awarded_marks == 3
    assert by_q[22].response == "5:00vaqt"  # unchanged
    assert by_q[22].judge_source == "llm"
    assert by_q[22].judge_verdict == "equivalent"
    assert "time" in by_q[22].judge_reason
    assert merged.awarded_marks == 3 + 6 + 3


def test_not_equivalent_and_uncertain_and_mcq_excluded():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark(
        {"1": "B", "21": "0=19", "22": "4:40"}
    )
    judge = FakeJudge(
        {
            21: ("equivalent", "value 19"),
            22: ("not_equivalent", "different time"),
        }
    )
    merged = apply_fr_equivalence_judge(result, manifest, judge)
    assert len(judge.calls) == 1
    queued = {i["question_number"] for i in judge.calls[0]}
    assert queued == {21, 22}  # MCQ incorrect excluded
    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[1].status == "incorrect"
    assert by_q[1].judge_source == "deterministic"
    assert by_q[21].status == "correct"
    assert by_q[22].status == "incorrect"
    assert by_q[22].judge_verdict == "not_equivalent"


def test_uncertain_and_judge_error_become_needs_review():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark({"1": "A", "21": "weird", "22": "5:00vaqt"})

    class UncertainJudge:
        def judge(self, items):
            return [
                {"question_number": 21, "verdict": "uncertain", "reason": "illegible digits"},
                {"question_number": 22, "verdict": "equivalent", "reason": "ok"},
            ]

    merged = apply_fr_equivalence_judge(result, manifest, UncertainJudge())
    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[21].status == "needs_review"
    assert by_q[21].awarded_marks == 0
    assert by_q[22].status == "correct"

    class BoomJudge:
        def judge(self, items):
            raise RuntimeError("gemini down")

    merged_err = apply_fr_equivalence_judge(result, manifest, BoomJudge())
    by_q = {o.question_number: o for o in merged_err.outcomes}
    assert by_q[21].status == "needs_review"
    assert by_q[21].judge_source == "llm_fallback_error"
    assert by_q[22].status == "needs_review"
    assert by_q[22].judge_source == "llm_fallback_error"


def test_build_judge_prompt_includes_unbiased_rules_and_items():
    items = [
        {
            "question_number": 22,
            "type": "time",
            "accepted_answers": ["5:00 PM"],
            "response": "5:00vaqt",
        }
    ]
    prompt = build_judge_prompt(items)
    assert "not_equivalent" in prompt
    assert "uncertain" in prompt
    assert "conflicting units" in prompt.lower() or "units must not conflict" in prompt.lower()
    assert "5:00vaqt" in prompt
    assert "5:00 PM" in prompt
    assert "benefit of the doubt" not in prompt.lower() or "Do NOT give benefit of the doubt" in prompt


def test_parse_judge_response_extracts_items():
    text = json.dumps(
        {
            "items": [
                {
                    "question_number": 22,
                    "verdict": "equivalent",
                    "reason": "same time",
                }
            ]
        }
    )
    parsed = parse_judge_response(text)
    assert parsed == [
        {"question_number": 22, "verdict": "equivalent", "reason": "same time"}
    ]


def test_parse_judge_response_rejects_garbage():
    with pytest.raises(ValueError):
        parse_judge_response("not json")
