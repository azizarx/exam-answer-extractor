"""Second-opinion reader for MCQ questions CV would not commit to.

The acceptance rules are the whole point of this stage: a wrong confident
answer costs a candidate real marks, while a refusal only leaves the question
where it already was. Most of these tests are therefore about refusal.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.services.review_vision import (
    BLANK,
    MULTIPLE,
    UNSURE,
    build_prompt,
    mcq_options,
    mcq_question_crop,
    parse_response,
    recheck_flagged_mcq,
)
from backend.services.template_service import TemplateRegistry


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def registry():
    return TemplateRegistry(ROOT / "backend" / "templates")


@pytest.fixture(scope="module")
def single_column(registry):
    """20 questions, A-E, one visual column."""
    return registry.get_or_raise("seamo_2026_a_fb")


@pytest.fixture(scope="module")
def two_column(registry):
    """15 questions, A-C, two visual columns of 10 and 5."""
    return registry.get_or_raise("seamo_2026_k_fb")


class _Reader:
    """Canned reader; records what it was asked."""

    def __init__(self, results, explode=False):
        self.results = results
        self.explode = explode
        self.asked = None

    def read(self, items):
        self.asked = items
        if self.explode:
            raise RuntimeError("model unavailable")
        return self.results


def _page(h=3510, w=2482):
    return np.full((h, w, 3), 255, dtype=np.uint8)


_SAME = object()  # distinct from None, which is itself a value under test


def _answer(q, marked, *, seen=_SAME, confident=True):
    return {
        "question_number": q,
        "question_number_seen": q if seen is _SAME else seen,
        "marked": marked,
        "confident": confident,
    }


# --- geometry ---------------------------------------------------------------


def test_crop_covers_the_row_and_the_printed_question_number(single_column):
    grid = [s for s in single_column.sections if s.type == "mcq_grid"][0].grid
    crop = mcq_question_crop(single_column, 1, image_shape=(3510, 2482))

    assert crop is not None
    row_y = grid.row_positions[0]
    # The scored band is row_y + cell_height down bubble_height.
    assert crop.y1 < row_y
    assert crop.y2 > row_y + grid.cell_height + grid.bubble_height
    # Extends left of the first option box to catch the printed number.
    assert crop.x1 < min(grid.col_positions[0]) - grid.cell_width // 2
    assert crop.x2 > max(grid.col_positions[0])


def test_crop_follows_the_second_visual_column(two_column):
    grid = [s for s in two_column.sections if s.type == "mcq_grid"][0].grid
    first = mcq_question_crop(two_column, 1, image_shape=(3510, 2482))
    eleventh = mcq_question_crop(two_column, 11, image_shape=(3510, 2482))

    assert first is not None and eleventh is not None
    # Q11 starts the right-hand column: further right, and back at the top.
    assert eleventh.x1 > first.x2
    assert abs(eleventh.y1 - first.y1) < 5
    assert eleventh.x1 < min(grid.col_positions[1])


def test_crop_rows_advance_down_a_column(two_column):
    ys = [mcq_question_crop(two_column, q, image_shape=(3510, 2482)).y1 for q in range(1, 11)]
    assert ys == sorted(ys)
    assert ys[-1] > ys[0]


def test_crop_is_clamped_to_the_image(single_column):
    crop = mcq_question_crop(single_column, 1, image_shape=(400, 400))
    assert crop is None or (crop.x1 >= 0 and crop.y1 >= 0 and crop.x2 <= 400 and crop.y2 <= 400)


def test_crop_declines_a_question_outside_the_grid(single_column):
    assert mcq_question_crop(single_column, 99, image_shape=(3510, 2482)) is None


def test_options_come_from_the_template(single_column, two_column):
    assert mcq_options(single_column, 1) == ["A", "B", "C", "D", "E"]
    assert mcq_options(two_column, 1) == ["A", "B", "C"]


# --- acceptance -------------------------------------------------------------


def test_confident_in_domain_answer_is_accepted(single_column):
    reader = _Reader([_answer(3, "C")])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, ["3"], reader)

    assert accepted == {"3": "C"}
    assert audit[0]["outcome"] == "accepted"


def test_blank_is_never_accepted(single_column):
    """A crop that missed its row is indistinguishable from an empty one.

    Blank scores zero either way, so accepting it cannot gain the candidate a
    mark and can only remove the human who might have awarded one. Observed
    live on page 66 (crop cut off the row) and page 113 (half-erased mark).
    """
    reader = _Reader([_answer(4, BLANK)])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [4], reader)

    assert accepted == {}
    assert audit[0]["outcome"] == "blank_not_accepted"


def test_reader_is_asked_one_image_per_flagged_question(single_column):
    reader = _Reader([_answer(2, "A"), _answer(5, "B")])

    recheck_flagged_mcq(_page(), single_column, [2, 5], reader)

    assert [i["question"] for i in reader.asked] == [2, 5]
    assert all(i["image"].size[0] > 0 for i in reader.asked)
    assert all(i["options"] == ["A", "B", "C", "D", "E"] for i in reader.asked)


# --- refusal ----------------------------------------------------------------


def test_unconfident_answer_is_refused(single_column):
    reader = _Reader([_answer(3, "C", confident=False)])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [3], reader)

    assert accepted == {}
    assert audit[0]["outcome"] == "not_confident"


def test_question_number_mismatch_is_refused(single_column):
    """A mis-registered crop shows a different printed number — refuse it."""
    reader = _Reader([_answer(3, "C", seen=4)])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [3], reader)

    assert accepted == {}
    assert audit[0]["outcome"] == "question_number_mismatch:saw_4"


def test_unreadable_question_number_is_refused(single_column):
    reader = _Reader([_answer(3, "C", seen=None)])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [3], reader)

    assert accepted == {}
    assert audit[0]["outcome"] == "no_question_number_visible"


def test_option_outside_the_template_domain_is_refused(two_column):
    """Paper K offers A-C; a confident "D" means the model is not reading it."""
    reader = _Reader([_answer(2, "D")])

    accepted, audit = recheck_flagged_mcq(_page(), two_column, [2], reader)

    assert accepted == {}
    assert audit[0]["outcome"] == "unusable:D"


def test_unsure_is_refused(single_column):
    reader = _Reader([_answer(3, UNSURE)])

    accepted, _ = recheck_flagged_mcq(_page(), single_column, [3], reader)

    assert accepted == {}


def test_multiple_marks_stays_flagged_for_a_human(single_column):
    """Scores zero either way, so leave it where a person will see it."""
    reader = _Reader([_answer(3, MULTIPLE)])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [3], reader)

    assert accepted == {}
    assert audit[0]["outcome"] == "multiple_marks"


def test_missing_response_for_a_question_is_refused(single_column):
    reader = _Reader([_answer(2, "A")])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [2, 7], reader)

    assert accepted == {"2": "A"}
    assert {a["question"]: a["outcome"] for a in audit}[7] == "no_response"


def test_reader_failure_leaves_every_question_flagged(single_column):
    reader = _Reader([], explode=True)

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [1, 2], reader)

    assert accepted == {}
    assert audit == []


def test_whole_page_flag_is_skipped_as_geometry_distrust(two_column):
    """All 15 flagged means the geometry is suspect; crops would be wrong."""
    reader = _Reader([_answer(q, "A") for q in range(1, 16)])

    accepted, audit = recheck_flagged_mcq(
        _page(), two_column, list(range(1, 16)), reader, max_questions=10,
    )

    assert accepted == {}
    assert audit == []
    assert reader.asked is None  # never even asked


def test_free_response_questions_are_not_rechecked(single_column):
    """Scope is MCQ: an FR flag came from the model's own uncertainty."""
    reader = _Reader([_answer(22, "A")])

    accepted, audit = recheck_flagged_mcq(_page(), single_column, [22], reader)

    assert accepted == {}
    assert audit == []
    assert reader.asked is None


def test_non_numeric_flags_are_ignored(single_column):
    reader = _Reader([])

    accepted, _ = recheck_flagged_mcq(_page(), single_column, ["header", None], reader)

    assert accepted == {}


# --- protocol ---------------------------------------------------------------


def test_prompt_states_the_domain_and_never_names_an_answer(single_column):
    prompt = build_prompt(
        [{"question": 3, "options": ["A", "B", "C", "D", "E"]}]
    )

    assert "question_number_seen" in prompt
    assert BLANK in prompt and UNSURE in prompt
    # The reported number must be tied to the row actually read, and a
    # cut-off row must come back UNSURE rather than BLANK.
    assert "whose option boxes you are reading" in prompt
    assert "cut off" in prompt
    assert "correct answer" in prompt.lower()
    # The model must not be told, or asked to infer, what is right.
    assert "accepted_answers" not in prompt


def test_parse_response_normalizes_and_keeps_the_seen_number():
    items = parse_response(
        '{"items":[{"question_number":"3","question_number_seen":"3",'
        '"marked":"c","confident":true}]}'
    )

    assert items == [
        {
            "question_number": 3,
            "question_number_seen": 3,
            "marked": "C",
            "confident": True,
        }
    ]


def test_parse_response_defaults_a_missing_verdict_to_unsure():
    items = parse_response('{"items":[{"question_number":1}]}')

    assert items[0]["marked"] == UNSURE
    assert items[0]["confident"] is False
    assert items[0]["question_number_seen"] is None


def test_parse_response_rejects_a_payload_with_no_items():
    with pytest.raises(ValueError):
        parse_response('{"items":[]}')
    with pytest.raises(ValueError):
        parse_response('{"nope":1}')


# --- enumerated box list ----------------------------------------------------


def test_two_inked_boxes_collapse_to_multiple():
    """Observed live: a struck-out box plus a fresh one on page 54 Q2.

    Asking for a category let the model pick a winner between them; asking it
    to enumerate and collapsing here keeps that judgement out of its hands.
    """
    items = parse_response(
        '{"items":[{"question_number":2,"question_number_seen":2,'
        '"marked_boxes":["A","E"],"confident":true}]}'
    )
    assert items[0]["marked"] == MULTIPLE


def test_one_inked_box_is_that_letter():
    items = parse_response(
        '{"items":[{"question_number":3,"question_number_seen":3,'
        '"marked_boxes":["d"],"confident":true}]}'
    )
    assert items[0]["marked"] == "D"


def test_empty_box_list_is_blank():
    items = parse_response(
        '{"items":[{"question_number":3,"question_number_seen":3,'
        '"marked_boxes":[],"confident":true}]}'
    )
    assert items[0]["marked"] == BLANK


def test_repeated_letter_is_not_treated_as_multiple():
    items = parse_response(
        '{"items":[{"question_number":3,"question_number_seen":3,'
        '"marked_boxes":["B","B"],"confident":true}]}'
    )
    assert items[0]["marked"] == "B"


def test_unsure_string_instead_of_a_list_is_honoured():
    items = parse_response(
        '{"items":[{"question_number":3,"question_number_seen":3,'
        '"marked_boxes":"UNSURE","confident":false}]}'
    )
    assert items[0]["marked"] == UNSURE


def test_legacy_marked_field_still_parses():
    items = parse_response(
        '{"items":[{"question_number":3,"question_number_seen":3,'
        '"marked":"c","confident":true}]}'
    )
    assert items[0]["marked"] == "C"


def test_prompt_asks_for_enumeration_and_counts_struck_out_boxes():
    prompt = build_prompt([{"question": 2, "options": ["A", "B", "C", "D", "E"]}])

    assert "marked_boxes" in prompt
    assert "do not choose between them" in prompt
    assert "crossed out" in prompt


# --- CV multi-ink detections are not overturned -----------------------------


def test_cv_invalid_question_is_never_asked_about(single_column):
    """IN means CV positively saw ink in more than one box.

    On real pages that is the struck-out-and-rechosen case. The model picks
    one of the two and cannot tell which was cancelled — live on page 54 it
    chose the crossed-out box twice, costing 6 marks and the review each time.
    """
    reader = _Reader([_answer(2, "A")])

    accepted, audit = recheck_flagged_mcq(
        _page(), single_column, [2], reader, cv_answers={"2": "IN"},
    )

    assert accepted == {}
    assert reader.asked is None
    assert audit == [{"question": 2, "outcome": "cv_detected_multiple_marks"}]


def test_a_weak_single_reading_is_still_rechecked(single_column):
    """Only IN is excluded; an ambiguous single mark is the thing to rescue."""
    reader = _Reader([_answer(2, "C"), _answer(5, "D")])

    accepted, audit = recheck_flagged_mcq(
        _page(), single_column, [2, 5], reader, cv_answers={"2": "B", "5": ""},
    )

    assert accepted == {"2": "C", "5": "D"}
    assert [i["question"] for i in reader.asked] == [2, 5]


def test_mixed_page_asks_only_about_the_non_invalid_questions(single_column):
    reader = _Reader([_answer(5, "D")])

    accepted, audit = recheck_flagged_mcq(
        _page(), single_column, [2, 5], reader, cv_answers={"2": "IN"},
    )

    assert accepted == {"5": "D"}
    assert [i["question"] for i in reader.asked] == [5]
    outcomes = {a["question"]: a["outcome"] for a in audit}
    assert outcomes[2] == "cv_detected_multiple_marks"
    assert outcomes[5] == "accepted"


def test_absent_cv_answers_recheck_everything(single_column):
    reader = _Reader([_answer(2, "C")])

    accepted, _ = recheck_flagged_mcq(_page(), single_column, [2], reader)

    assert accepted == {"2": "C"}


# --- untrusted page geometry ------------------------------------------------


def test_untrusted_page_geometry_is_not_rechecked(two_column):
    """CV distrusting its own grid means the crop cannot be placed.

    Observed on the real Paper K page: the crop showed Q9's boxes beside Q10's
    printed number, and the model reported seeing "9" — echoing the number it
    was given rather than the one on the page. The same crop was correctly
    refused on another run, so the printed-number guard holds only sometimes.
    """
    reader = _Reader([_answer(q, "B") for q in range(1, 6)])

    accepted, audit = recheck_flagged_mcq(
        _page(), two_column, [1, 2, 3, 4, 5], reader, geometry_trusted=False,
    )

    assert accepted == {}
    assert reader.asked is None
    assert {a["outcome"] for a in audit} == {"page_geometry_not_trusted"}
    assert len(audit) == 5


def test_trusted_geometry_is_the_default(single_column):
    reader = _Reader([_answer(3, "C")])

    accepted, _ = recheck_flagged_mcq(_page(), single_column, [3], reader)

    assert accepted == {"3": "C"}
