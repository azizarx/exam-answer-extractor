"""Human correction of a candidate: answers, identity, audit trail, re-mark."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.api import routes  # noqa: F401  (registers routes)
from backend.db.database import Base, get_db
from backend.db.models import (
    AnswerKey,
    CandidateMarking,
    CandidateResult,
    ExamSubmission,
    MarkingRun,
)
from main import app


def _answer_key(db: Session, template_id: str = "seamo_2025_a") -> AnswerKey:
    key = AnswerKey(
        name=f"{template_id} v1",
        paper_type="A",
        answers={"1": "C", "2": "D"},
        total_questions=2,
        template_id=template_id,
        version=1,
        source_filename="Paper A key.pdf",
        source_sha256="a" * 64,
        total_marks=5,
        question_spec=[
            {"number": 1, "type": "mcq", "accepted_answers": ["C"], "marks": 2,
             "normalizer": "uppercase"},
            {"number": 2, "type": "mcq", "accepted_answers": ["D"], "marks": 3,
             "normalizer": "uppercase"},
        ],
        is_active=True,
    )
    db.add(key)
    db.commit()
    return key


@pytest.fixture
def api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'edit.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with factory() as db:
        _answer_key(db)
        submission = ExamSubmission(
            filename="students.pdf",
            original_pdf_key="uploads/students.pdf",
            template_id="seamo_2025_a",
            status="completed",
            pages_count=3,
            processed_at=datetime.utcnow(),
        )
        db.add(submission)
        db.flush()
        ids = []
        for page, (number, a1, a2) in enumerate(
            [("001", "C", "D"), ("002", "A", "D"), ("003", "C", "B")], start=1
        ):
            candidate = CandidateResult(
                submission_id=submission.id,
                page_number=page,
                candidate_name=f"Student {page}",
                candidate_number=number,
                country="MY",
                paper_type="A",
                template_id="seamo_2025_a",
                answers={"1": a1, "2": a2},
                extra_fields={"needs_review_questions": ["1"], "answer_trust": {"1": "needs_review"}}
                if page == 1 else None,
            )
            db.add(candidate)
            db.flush()
            ids.append(candidate.id)
        db.commit()
        submission_id = submission.id

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), submission_id, ids, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _edit(client, submission_id, candidate_id, **payload):
    return client.post(
        f"/submission/{submission_id}/candidates/{candidate_id}/confirm-review",
        json=payload,
    )


# --- editing ---------------------------------------------------------------

def test_editing_an_answer_records_what_the_extractor_originally_read(api):
    client, sid, ids, factory = api
    response = _edit(client, sid, ids[0], answers={"1": "B"})
    assert response.status_code == 200
    body = response.json()
    assert body["answers"]["1"] == "B"
    assert body["edits_recorded"] == 1

    with factory() as db:
        extra = db.get(CandidateResult, ids[0]).extra_fields
    edit = extra["manual_edits"][0]
    assert edit["target"] == "answers.1"
    assert edit["from"] == "C"
    assert edit["to"] == "B"
    assert edit["at"].endswith("Z")


def test_identity_fields_are_editable_and_audited(api):
    client, sid, ids, factory = api
    response = _edit(
        client, sid, ids[1],
        candidate_number="CAN-999", candidate_name="Corrected Name",
        edited_by="marker@example.com",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_number"] == "CAN-999"
    assert body["candidate_name"] == "Corrected Name"
    assert body["edits_recorded"] == 2

    with factory() as db:
        candidate = db.get(CandidateResult, ids[1])
        assert candidate.candidate_number == "CAN-999"
        targets = {e["target"]: e for e in candidate.extra_fields["manual_edits"]}
    assert targets["candidate_number"]["from"] == "002"
    assert targets["candidate_name"]["by"] == "marker@example.com"


def test_omitted_fields_mean_unchanged_not_cleared(api):
    """A payload that only touches one answer must not blank the identity."""
    client, sid, ids, factory = api
    _edit(client, sid, ids[1], answers={"2": "D"})
    with factory() as db:
        candidate = db.get(CandidateResult, ids[1])
    assert candidate.candidate_number == "002"
    assert candidate.candidate_name == "Student 2"
    assert candidate.country == "MY"


def test_setting_a_field_to_its_current_value_records_nothing(api):
    client, sid, ids, _ = api
    body = _edit(client, sid, ids[1], candidate_number="002", answers={"1": "A"}).json()
    assert body["edits_recorded"] == 0


def test_edits_accumulate_rather_than_overwrite_history(api):
    client, sid, ids, factory = api
    _edit(client, sid, ids[0], answers={"1": "B"})
    _edit(client, sid, ids[0], answers={"1": "D"})
    with factory() as db:
        history = db.get(CandidateResult, ids[0]).extra_fields["manual_edits"]
    assert [e["from"] for e in history] == ["C", "B"]
    assert history[-1]["to"] == "D"


def test_confirming_a_flagged_answer_still_clears_its_review_flag(api):
    client, sid, ids, factory = api
    body = _edit(client, sid, ids[0], answers={"1": "C"}).json()
    assert body["needs_review_questions"] == []
    with factory() as db:
        extra = db.get(CandidateResult, ids[0]).extra_fields
    assert extra["answer_trust"]["1"] == "trusted"


# --- re-marking ------------------------------------------------------------

def test_remark_is_opt_in(api):
    client, sid, ids, factory = api
    body = _edit(client, sid, ids[0], answers={"1": "C"}).json()
    assert body["remarked"] is False
    with factory() as db:
        assert db.query(MarkingRun).count() == 0


def test_remark_scores_only_the_edited_candidate_but_keeps_the_run_complete(api):
    """A scoped re-mark must not blank everyone else.

    Reads resolve a submission's marks from the newest run alone, so the
    untouched candidates' marks are carried into the new run.
    """
    client, sid, ids, factory = api
    # Establish a baseline run over all three candidates.
    assert client.post(f"/submission/{sid}/mark").status_code == 200
    with factory() as db:
        first_run = db.query(MarkingRun).order_by(MarkingRun.id.desc()).first()
        baseline = {
            m.candidate_result_id: m.awarded_marks
            for m in db.query(CandidateMarking).filter(
                CandidateMarking.marking_run_id == first_run.id
            )
        }
    assert baseline[ids[1]] == 3  # Q1 wrong, Q2 right

    # Candidate 2's Q1 was misread; correcting it should raise only their score.
    body = _edit(client, sid, ids[1], answers={"1": "C"}, remark=True).json()
    assert body["remarked"] is True
    assert body["remark_error"] is None

    with factory() as db:
        new_run = db.query(MarkingRun).order_by(MarkingRun.id.desc()).first()
        assert new_run.id != first_run.id
        rows = {
            m.candidate_result_id: m.awarded_marks
            for m in db.query(CandidateMarking).filter(
                CandidateMarking.marking_run_id == new_run.id
            )
        }
    # Every candidate is present in the newest run.
    assert set(rows) == set(ids)
    assert rows[ids[1]] == 5           # re-marked
    assert rows[ids[0]] == baseline[ids[0]]  # carried forward
    assert rows[ids[2]] == baseline[ids[2]]  # carried forward


def test_the_ui_read_path_sees_every_candidate_after_a_scoped_remark(api):
    client, sid, ids, _ = api
    client.post(f"/submission/{sid}/mark")
    _edit(client, sid, ids[1], answers={"1": "C"}, remark=True)

    detail = client.get(f"/submission/{sid}").json()
    marked = [c for c in detail["candidates"] if c.get("marking")]
    assert len(marked) == 3, "a scoped re-mark blanked other candidates in the UI payload"


def test_a_scoped_remark_without_a_previous_run_marks_everyone(api):
    """Nothing to carry forward, so fall back rather than leave 2 of 3 unmarked."""
    client, sid, ids, factory = api
    body = _edit(client, sid, ids[1], answers={"1": "C"}, remark=True).json()
    assert body["remarked"] is True
    with factory() as db:
        run = db.query(MarkingRun).order_by(MarkingRun.id.desc()).first()
        count = db.query(CandidateMarking).filter(
            CandidateMarking.marking_run_id == run.id
        ).count()
    assert count == 3


def test_the_edit_survives_a_failing_remark(api, monkeypatch):
    """The correction is committed before marking; a marking failure must not lose it."""
    client, sid, ids, factory = api

    def boom(*args, **kwargs):
        raise RuntimeError("marking exploded")

    monkeypatch.setattr("backend.api.marking_routes.mark_submission_answers", boom)
    response = _edit(client, sid, ids[0], answers={"1": "B"}, remark=True)

    assert response.status_code == 200
    body = response.json()
    assert body["remarked"] is False
    assert "RuntimeError" in body["remark_error"]
    with factory() as db:
        assert db.get(CandidateResult, ids[0]).answers["1"] == "B"


def test_unknown_candidate_and_cross_submission_access_are_rejected(api):
    client, sid, ids, _ = api
    assert _edit(client, sid, 999999, answers={"1": "B"}).status_code == 404
    assert _edit(client, sid + 1, ids[0], answers={"1": "B"}).status_code == 404
