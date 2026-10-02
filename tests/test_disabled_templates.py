"""Layouts withdrawn from automatic processing.

A layout whose geometry is known to be wrong must not be read or scored. The
failure mode being prevented is specific: extracting it yields empty answers,
and marking empty answers produces a clean-looking zero that is
indistinguishable from a candidate who answered nothing.
"""
from __future__ import annotations

import pytest

from backend.services.template_service import (
    disabled_template_ids,
    is_template_disabled,
)


@pytest.fixture
def withdraw(monkeypatch):
    def apply(value):
        from backend.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("DISABLED_TEMPLATE_IDS", value)
        return get_settings()

    yield apply
    from backend.config import get_settings

    get_settings.cache_clear()


def test_only_the_named_layouts_are_withdrawn(withdraw):
    withdraw("seamo_2026_k")

    assert is_template_disabled("seamo_2026_k")
    # The format-B sibling is mapped from a clean vector sheet and works.
    assert not is_template_disabled("seamo_2026_k_fb")
    assert not is_template_disabled("seamo_2025_k")
    assert not is_template_disabled("seamo_x_2026_k")


def test_several_layouts_can_be_withdrawn(withdraw):
    withdraw("seamo_2026_k, seamo_2026_a")

    assert disabled_template_ids() == frozenset({"seamo_2026_k", "seamo_2026_a"})


def test_empty_setting_withdraws_nothing(withdraw):
    withdraw("")

    assert disabled_template_ids() == frozenset()
    assert not is_template_disabled("seamo_2026_k")


def test_none_is_never_disabled(withdraw):
    withdraw("seamo_2026_k")

    assert not is_template_disabled(None)
    assert not is_template_disabled("")


# --- marking ----------------------------------------------------------------


def test_withdrawn_paper_is_reported_unmarked_not_scored_zero(withdraw, tmp_path):
    """The whole point: an all-blank zero reads as a real candidate result."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from backend.db.database import Base
    from backend.db.models import (
        AnswerKey,
        CandidateMarking,
        CandidateResult,
        ExamSubmission,
    )
    from backend.services.marking_workflow import mark_submission_answers

    withdraw("seamo_2026_k")
    engine = create_engine(f"sqlite:///{tmp_path / 'm.sqlite'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        submission = ExamSubmission(
            filename="k.pdf", original_pdf_key="k.pdf", status="completed",
        )
        db.add(submission)
        db.flush()
        withdrawn = CandidateResult(
            submission_id=submission.id, candidate_number="001",
            template_id="seamo_2026_k", answers={},
        )
        ordinary = CandidateResult(
            submission_id=submission.id, candidate_number="002",
            template_id="seamo_2026_k_fb", answers={"1": "B"},
        )
        key = AnswerKey(
            name="k", paper_type="K", answers={"1": "B"}, total_questions=1,
            template_id="seamo_2026_k", version=1, source_filename="k.pdf",
            source_sha256="a" * 64, total_marks=3,
            question_spec=[{
                "number": 1, "type": "mcq", "accepted_answers": ["B"],
                "marks": 3, "normalizer": "uppercase",
            }],
            is_active=True,
        )
        db.add_all([withdrawn, ordinary, key])
        db.commit()
        withdrawn_id, ordinary_id = withdrawn.id, ordinary.id

        run = mark_submission_answers(db, submission.id)

        markings = {
            m.candidate_result_id: m
            for m in db.scalars(
                select(CandidateMarking).where(
                    CandidateMarking.marking_run_id == run.id
                )
            ).all()
        }

    # The withdrawn paper gets no marking row at all — not a zero.
    assert withdrawn_id not in markings
    # Its format-B sibling shares the key and still marks normally.
    assert markings[ordinary_id].awarded_marks == 3
    assert "seamo_2026_k (withdrawn)" in run.key_provenance["missing_templates"]
