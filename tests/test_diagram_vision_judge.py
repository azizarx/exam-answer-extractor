"""The vision judge and the marking stage that consumes its verdicts."""

import json
from pathlib import Path

import pytest
from PIL import Image

from backend.services.diagram_vision_judge import (
    build_contents,
    build_judge_prompt,
    parse_judge_response,
    reference_diagram_path,
)
from backend.services.marking_service import (
    AnswerKeyManifest,
    ManifestQuestion,
    MarkingService,
    apply_diagram_vision_judge,
    apply_extraction_trust,
)


def _manifest() -> AnswerKeyManifest:
    return AnswerKeyManifest(
        template_id="seamo_x_2026_a",
        version=2,
        source_filename="x.pdf",
        source_sha256="abc",
        total_marks=12,
        questions=(
            ManifestQuestion(4, "diagram", ("Clock: 7:45",), 4, "text"),
            ManifestQuestion(6, "diagram", ("2x2 grid: BL=crossed O",), 4, "text"),
            ManifestQuestion(9, "diagram", ("Pie chart: shaded sector 4-5 o'clock",), 4, "text"),
        ),
    )


def _crops(tmp_path: Path, questions=(4, 6, 9), page=3) -> dict:
    crops = {}
    for question in questions:
        name = f"p{page}_q{question}.png"
        Image.new("RGB", (40, 40), "white").save(tmp_path / name)
        crops[str(question)] = {"file": name, "source": "template_region"}
    return crops


class FakeVisionJudge:
    def __init__(self, verdicts: dict, raises: Exception | None = None):
        self.verdicts = verdicts
        self.raises = raises
        self.calls = []

    def judge(self, items):
        self.calls.append(items)
        if self.raises:
            raise self.raises
        return [
            {
                "question_number": item["question_number"],
                **self.verdicts[item["question_number"]],
            }
            for item in items
            if item["question_number"] in self.verdicts
        ]


# --- the judge itself ------------------------------------------------------

def test_every_committed_diagram_question_has_a_reference_drawing():
    for template_id, questions in (("seamo_x_2026_a", (4, 6, 9)), ("seamo_x_2026_b", (5,))):
        for question in questions:
            assert reference_diagram_path(template_id, question) is not None


@pytest.mark.parametrize(
    "template_id",
    ["../../etc/passwd", "..%2f..%2fpasswd", "seamo_x_2026_a/../../secret"],
)
def test_reference_lookup_refuses_paths_outside_its_directory(template_id):
    assert reference_diagram_path(template_id, 4) is None


def test_contents_pair_each_student_image_with_its_key_image_in_order(tmp_path):
    student = tmp_path / "student.png"
    reference = tmp_path / "reference.png"
    Image.new("RGB", (10, 10), "white").save(student)
    Image.new("RGB", (10, 10), "black").save(reference)
    items = [
        {"question_number": 4, "accepted_answers": ["Clock: 7:45"],
         "student_path": student, "reference_path": reference},
        {"question_number": 6, "accepted_answers": ["2x2 grid"],
         "student_path": student, "reference_path": reference},
    ]

    contents = build_contents(items)

    labels = [part for part in contents[1:] if isinstance(part, str)]
    assert labels == [
        "IMAGE 1: STUDENT Q4",
        "IMAGE 2: CORRECT Q4",
        "IMAGE 3: STUDENT Q6",
        "IMAGE 4: CORRECT Q6",
    ]
    assert sum(1 for part in contents if isinstance(part, Image.Image)) == 4


def test_prompt_carries_the_key_wording_and_forbids_pixel_comparison():
    prompt = build_judge_prompt(
        [{"question_number": 4, "accepted_answers": ["Clock: 7:45"]}]
    )
    assert "Clock: 7:45" in prompt
    assert "NOT pixel-comparable" in prompt
    assert "preprinted scaffold" in prompt


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [("match", "match"), ("MISMATCH", "mismatch"), ("uncertain", "uncertain"),
     ("equivalent", "uncertain"), ("", "uncertain")],
)
def test_unknown_verdicts_degrade_to_uncertain(verdict, expected):
    text = json.dumps({"items": [
        {"question_number": 4, "verdict": verdict, "observed": "x", "reason": "y"}
    ]})
    assert parse_judge_response(text)[0]["verdict"] == expected


def test_parses_a_fenced_response_with_latex_backslashes():
    text = '```json\n{"items":[{"question_number":9,"verdict":"mismatch",' \
           '"observed":"\\sqrt sector","reason":"shaded \\(4-5\\)"}]}\n```'
    parsed = parse_judge_response(text)
    assert parsed[0]["question_number"] == 9
    assert parsed[0]["verdict"] == "mismatch"


@pytest.mark.parametrize("text", ["", "   ", "no json here", '{"items": []}'])
def test_unusable_responses_raise_rather_than_guess(text):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_judge_response(text)


# --- the marking stage -----------------------------------------------------

def test_verdicts_map_to_marks_and_the_observed_drawing_becomes_the_response(tmp_path):
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "Clock: 7:45", "6": "x", "9": "y"})
    judge = FakeVisionJudge({
        4: {"verdict": "match", "observed": "Clock: 7:45", "reason": "same hands"},
        6: {"verdict": "mismatch", "observed": "2x2 grid: all blank", "reason": "nothing drawn"},
        9: {"verdict": "uncertain", "observed": "", "reason": "smudged"},
    })

    merged = apply_diagram_vision_judge(
        result, manifest, judge, diagram_crops=_crops(tmp_path), crop_dir=tmp_path,
    )

    by_q = {o.question_number: o for o in merged.outcomes}
    assert (by_q[4].status, by_q[4].awarded_marks) == ("correct", 4)
    assert (by_q[6].status, by_q[6].awarded_marks) == ("incorrect", 0)
    assert (by_q[9].status, by_q[9].awarded_marks) == ("needs_review", 0)
    assert by_q[6].response == "2x2 grid: all blank"
    # An empty observation keeps whatever the extractor recorded.
    assert by_q[9].response == "y"
    assert all(o.judge_source == "diagram_vision" for o in merged.outcomes)
    assert merged.awarded_marks == 4


def test_a_missing_crop_needs_review_instead_of_scoring_zero(tmp_path):
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "a", "6": "b", "9": "c"})
    judge = FakeVisionJudge({4: {"verdict": "match", "observed": "", "reason": ""}})

    merged = apply_diagram_vision_judge(
        result, manifest, judge,
        diagram_crops=_crops(tmp_path, questions=(4,)), crop_dir=tmp_path,
    )

    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[4].status == "correct"
    for question in (6, 9):
        assert by_q[question].status == "needs_review"
        assert by_q[question].judge_source == "diagram_crop_missing"
    # Only the question with a crop was ever sent.
    assert [item["question_number"] for item in judge.calls[0]] == [4]


def test_a_crop_name_the_service_did_not_generate_is_not_opened(tmp_path):
    """extra_fields is JSON we read back; a path in it must never be trusted."""
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "a"})
    judge = FakeVisionJudge({})

    merged = apply_diagram_vision_judge(
        result, manifest, judge,
        diagram_crops={"4": {"file": "../../../etc/passwd", "source": "x"}},
        crop_dir=tmp_path,
    )

    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[4].judge_source == "diagram_crop_missing"
    assert judge.calls == []


def test_a_judge_failure_needs_review_rather_than_failing_the_run(tmp_path):
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "a", "6": "b", "9": "c"})
    judge = FakeVisionJudge({}, raises=RuntimeError("gemini exploded"))

    merged = apply_diagram_vision_judge(
        result, manifest, judge, diagram_crops=_crops(tmp_path), crop_dir=tmp_path,
    )

    assert all(o.status == "needs_review" for o in merged.outcomes)
    assert all(o.judge_source == "diagram_vision_error" for o in merged.outcomes)
    assert "gemini exploded" in merged.outcomes[0].judge_reason
    assert merged.awarded_marks == 0


def test_a_verdict_the_judge_omitted_needs_review(tmp_path):
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "a", "6": "b", "9": "c"})
    judge = FakeVisionJudge({4: {"verdict": "match", "observed": "", "reason": ""}})

    merged = apply_diagram_vision_judge(
        result, manifest, judge, diagram_crops=_crops(tmp_path), crop_dir=tmp_path,
    )

    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[4].status == "correct"
    assert by_q[6].status == "needs_review"
    assert by_q[6].judge_reason == "missing verdict from judge response"


def test_blank_answers_are_never_sent_to_the_judge(tmp_path):
    """Nothing was drawn, so there is nothing to compare and no call to spend."""
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "BL", "6": "", "9": None})
    assert {o.status for o in result.outcomes} == {"blank"}
    judge = FakeVisionJudge({})

    merged = apply_diagram_vision_judge(
        result, manifest, judge, diagram_crops=_crops(tmp_path), crop_dir=tmp_path,
    )

    assert judge.calls == []
    assert {o.status for o in merged.outcomes} == {"blank"}


def test_extraction_trust_still_wins_over_the_vision_judge(tmp_path):
    manifest = _manifest()
    result = MarkingService(manifest).mark({"4": "a", "6": "b", "9": "c"})
    result = apply_extraction_trust(result, [6])
    judge = FakeVisionJudge({
        4: {"verdict": "match", "observed": "", "reason": ""},
        9: {"verdict": "match", "observed": "", "reason": ""},
    })

    merged = apply_diagram_vision_judge(
        result, manifest, judge, diagram_crops=_crops(tmp_path), crop_dir=tmp_path,
    )

    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[6].judge_source == "extraction_trust"
    assert [item["question_number"] for item in judge.calls[0]] == [4, 9]


def test_a_question_measured_by_deterministic_cv_keeps_that_answer(tmp_path):
    """Sector density measures the wedge; the judge reads it a sector off."""
    manifest = _manifest()
    result = MarkingService(manifest).mark(
        {"4": "a", "6": "b", "9": "Pie chart: shaded sector 4-5 o'clock"}
    )
    assert {o.question_number: o.status for o in result.outcomes}[9] == "correct"
    judge = FakeVisionJudge({
        4: {"verdict": "match", "observed": "", "reason": ""},
        6: {"verdict": "match", "observed": "", "reason": ""},
    })

    merged = apply_diagram_vision_judge(
        result, manifest, judge,
        diagram_crops=_crops(tmp_path), crop_dir=tmp_path,
        cv_owned_questions=[9],
    )

    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[9].status == "correct"
    assert by_q[9].awarded_marks == 4
    # Tagged as a diagram so the UI still shows the drawing, but not judged.
    assert by_q[9].judge_source == "diagram_cv"
    assert [item["question_number"] for item in judge.calls[0]] == [4, 6]
    # Q4 and Q6 matched (4 each) and Q9's CV mark survived intact.
    assert merged.awarded_marks == 12


def test_a_manifest_without_diagrams_is_left_completely_alone(tmp_path):
    manifest = AnswerKeyManifest(
        template_id="test_numeric", version=1, source_filename="x.pdf",
        source_sha256="abc", total_marks=4,
        questions=(ManifestQuestion(1, "numeric", ("19",), 4, "integer"),),
    )
    result = MarkingService(manifest).mark({"1": "20"})
    judge = FakeVisionJudge({})

    merged = apply_diagram_vision_judge(
        result, manifest, judge, diagram_crops={}, crop_dir=tmp_path,
    )

    assert merged is result
    assert judge.calls == []
