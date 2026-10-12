"""Correcting a misread candidate number by the number the results show."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

# page -> (candidate_number, template_id). Pages 4 and 5 share a misread number
# on two layout variants of Paper A; page 3 is the same student set on Paper C.
PAGES = {
    1: ("CAN100", "seamo_2025_a"),
    2: ("CAN200", "seamo_2025_a"),
    3: ("CAN300", "seamo_2025_c"),
    4: ("CAN777", "seamo_2025_a"),
    5: ("CAN777", "seamo_2025_a_fb"),
    6: (None, "seamo_2025_a"),
    7: (" 000123 ", "seamo_2025_a"),
}


def _submission(db, status="completed", **kwargs):
    submission = ExamSubmission(
        filename="students.pdf",
        original_pdf_key="uploads/students.pdf",
        status=status,
        pages_count=len(PAGES),
        processed_at=datetime.utcnow(),
        **kwargs,
    )
    db.add(submission)
    db.flush()
    return submission


def _candidate(db, submission_id, page, number, template_id, answers=None):
    candidate = CandidateResult(
        submission_id=submission_id,
        page_number=page,
        candidate_name=f"Student {page}",
        candidate_number=number,
        paper_type=template_id.split("_")[2].upper(),
        template_id=template_id,
        answers=answers or {"1": "C", "2": "D"},
    )
    db.add(candidate)
    db.flush()
    return candidate.id


@pytest.fixture
def api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'corrections.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with factory() as db:
        main = _submission(db)
        ids = {
            page: _candidate(db, main.id, page, number, template_id)
            for page, (number, template_id) in PAGES.items()
        }
        other = _submission(db)
        other_id = _candidate(db, other.id, 1, "CAN100", "seamo_2025_a")
        processing = _submission(db, status="processing")
        _candidate(db, processing.id, 1, "CAN100", "seamo_2025_a")
        db.commit()
        sids = SimpleNamespace(main=main.id, other=other.id, processing=processing.id)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield SimpleNamespace(
            client=TestClient(app), sid=sids, ids=ids, other_id=other_id, db=factory
        )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _correct(api, *corrections, submission_id=None, **body):
    return api.client.post(
        f"/submission/{submission_id or api.sid.main}/candidate-number-corrections",
        json={"corrections": list(corrections), **body},
    )


def _fix(wrong, right, **extra):
    return {"candidate_number": wrong, "corrected_candidate_number": right, **extra}


def _numbers(api):
    with api.db() as db:
        return {
            row.page_number: row.candidate_number
            for row in db.query(CandidateResult).filter_by(submission_id=api.sid.main)
        }


def _errors(response):
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "candidate_number_corrections_rejected"
    return detail["errors"]


# --- applying a correction ---------------------------------------------------

def test_the_misread_number_finds_the_candidate_and_is_audited(api):
    response = _correct(api, _fix("CAN100", "CAN101"), edited_by="import-bot")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["corrected"] == 1
    assert body["results"] == [{
        "candidate_id": api.ids[1],
        "page_number": 1,
        "template_id": "seamo_2025_a",
        "previous_candidate_number": "CAN100",
        "candidate_number": "CAN101",
        "outcome": "corrected",
    }]
    with api.db() as db:
        candidate = db.get(CandidateResult, api.ids[1])
        assert candidate.candidate_number == "CAN101"
        edit = candidate.extra_fields["manual_edits"][-1]
    assert edit["target"] == "candidate_number"
    assert (edit["from"], edit["to"], edit["by"]) == ("CAN100", "CAN101", "import-bot")
    assert edit["at"].endswith("Z")


def test_only_the_named_submission_is_touched(api):
    _correct(api, _fix("CAN100", "CAN101"))
    with api.db() as db:
        assert db.get(CandidateResult, api.other_id).candidate_number == "CAN100"
    # A candidate id from another submission cannot be reached through this one.
    errors = _errors(_correct(api, _fix("CAN100", "CAN102", candidate_id=api.other_id)))
    assert errors[0]["code"] == "candidate_not_found"


def test_surrounding_whitespace_is_ignored_and_leading_zeros_survive(api):
    response = _correct(api, _fix("000123", "  000124\t"))
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["previous_candidate_number"] == " 000123 "
    assert _numbers(api)[7] == "000124"


def test_a_number_that_was_never_read_can_be_supplied(api):
    response = _correct(api, _fix("", "CAN600", page_number=6))
    assert response.status_code == 200, response.text
    assert _numbers(api)[6] == "CAN600"
    with api.db() as db:
        edit = db.get(CandidateResult, api.ids[6]).extra_fields["manual_edits"][-1]
    assert edit["from"] == ""


def test_setting_the_current_number_changes_nothing(api):
    body = _correct(api, _fix("CAN100", "CAN100")).json()
    assert body["corrected"] == 0
    assert body["results"][0]["outcome"] == "unchanged"
    with api.db() as db:
        assert db.get(CandidateResult, api.ids[1]).extra_fields is None


def test_two_numbers_can_be_swapped_in_one_request(api):
    response = _correct(api, _fix("CAN100", "CAN200"), _fix("CAN200", "CAN100"))
    assert response.status_code == 200, response.text
    assert response.json()["corrected"] == 2
    numbers = _numbers(api)
    assert (numbers[1], numbers[2]) == ("CAN200", "CAN100")


# --- retries -----------------------------------------------------------------

def test_repeating_an_applied_correction_is_a_safe_retry(api):
    first = _correct(api, _fix("CAN100", "CAN101")).json()
    retry = _correct(api, _fix("CAN100", "CAN101"))
    assert retry.status_code == 200, retry.text
    assert first["results"][0]["outcome"] == "corrected"
    assert retry.json()["corrected"] == 0
    assert retry.json()["results"][0] | {"outcome": "corrected"} == first["results"][0]
    assert retry.json()["results"][0]["outcome"] == "already_applied"
    with api.db() as db:
        assert len(db.get(CandidateResult, api.ids[1]).extra_fields["manual_edits"]) == 1


def test_a_correction_made_in_the_review_ui_also_counts_as_applied(api):
    api.client.post(
        f"/submission/{api.sid.main}/candidates/{api.ids[1]}/confirm-review",
        json={"candidate_number": "CAN101"},
    )
    body = _correct(api, _fix("CAN100", "CAN101")).json()
    assert body["results"][0]["outcome"] == "already_applied"


def test_a_number_changed_since_is_not_mistaken_for_a_retry(api):
    _correct(api, _fix("CAN100", "CAN101"))
    _correct(api, _fix("CAN101", "CAN102"))
    errors = _errors(_correct(api, _fix("CAN100", "CAN101")))
    assert errors[0]["code"] == "correction_superseded"
    assert errors[0]["candidates"][0]["candidate_number"] == "CAN102"
    assert _numbers(api)[1] == "CAN102"


# --- refusals ----------------------------------------------------------------

def test_an_unknown_number_rejects_the_whole_batch(api):
    errors = _errors(_correct(api, _fix("CAN100", "CAN101"), _fix("CAN999", "CAN998")))
    assert [(e["index"], e["code"]) for e in errors] == [(1, "candidate_not_found")]
    assert _numbers(api)[1] == "CAN100"


def test_a_shared_misread_number_is_never_guessed(api):
    errors = _errors(_correct(api, _fix("CAN777", "CAN701")))
    assert errors[0]["code"] == "ambiguous_candidate_number"
    assert [(m["candidate_id"], m["page_number"]) for m in errors[0]["matches"]] == [
        (api.ids[4], 4), (api.ids[5], 5),
    ]
    assert errors[0]["truncated"] is False

    by_page = _correct(api, _fix("CAN777", "CAN705", page_number=5))
    by_id = _correct(api, _fix("CAN777", "CAN704", candidate_id=api.ids[4]))
    assert by_page.status_code == by_id.status_code == 200
    numbers = _numbers(api)
    assert (numbers[4], numbers[5]) == ("CAN704", "CAN705")


def test_a_selected_candidate_showing_another_number_is_reported(api):
    errors = _errors(_correct(api, _fix("CAN999", "CAN101", candidate_id=api.ids[1])))
    assert errors[0]["code"] == "candidate_number_mismatch"
    assert errors[0]["candidates"][0]["candidate_number"] == "CAN100"


def test_two_corrections_for_one_candidate_are_refused(api):
    errors = _errors(_correct(api, _fix("CAN100", "CAN101"), _fix("CAN100", "CAN102")))
    assert [(e["index"], e["code"], e["first_index"]) for e in errors] == [
        (1, "duplicate_target", 0),
    ]


def test_a_number_already_on_the_same_paper_is_refused(api):
    errors = _errors(_correct(api, _fix("CAN200", "CAN100")))
    assert errors[0]["code"] == "candidate_number_in_use"
    assert [c["page_number"] for c in errors[0]["conflicts"]] == [1]
    assert _numbers(api)[2] == "CAN200"


def test_layout_variants_of_one_paper_count_as_the_same_paper(api):
    errors = _errors(_correct(api, _fix("CAN777", "CAN100", page_number=5)))
    assert errors[0]["code"] == "candidate_number_in_use"


def test_the_same_student_on_another_paper_is_accepted(api):
    """Paper C's page 3 really is CAN100, who also sat Paper A on page 1."""
    response = _correct(api, _fix("CAN300", "CAN100"))
    assert response.status_code == 200, response.text
    assert _numbers(api)[3] == "CAN100"


def test_allow_duplicate_accepts_a_sheet_scanned_twice(api):
    response = _correct(api, _fix("CAN200", "CAN100", allow_duplicate=True))
    assert response.status_code == 200, response.text
    assert _numbers(api)[2] == "CAN100"


def test_two_corrections_cannot_claim_one_number(api):
    errors = _errors(_correct(api, _fix("CAN100", "CAN555"), _fix("CAN200", "CAN555")))
    assert [(e["index"], e["code"]) for e in errors] == [
        (0, "candidate_number_in_use"), (1, "candidate_number_in_use"),
    ]


def test_submission_must_exist_and_be_complete(api):
    assert _correct(api, _fix("CAN100", "CAN101"), submission_id=999999).status_code == 404
    response = _correct(api, _fix("CAN100", "CAN101"), submission_id=api.sid.processing)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "submission_not_completed"


@pytest.mark.parametrize("body", [
    {"corrections": []},
    {"corrections": [_fix("CAN100", "   ")]},
    {"corrections": [_fix("CAN100", "CAN\n101")]},
    {"corrections": [_fix("CAN100", "CAN\x85101")]},
    {"corrections": [_fix("CAN100", "C" * 101)]},
    {"corrections": [_fix("CAN100", 101)]},
    {"corrections": [_fix(100, "CAN101")]},
    {"corrections": [_fix("CAN100", "CAN101", page_number=0)]},
])
def test_malformed_requests_are_rejected_before_any_lookup(api, body):
    response = api.client.post(
        f"/submission/{api.sid.main}/candidate-number-corrections", json=body
    )
    assert response.status_code == 422, response.text
    assert _numbers(api)[1] == "CAN100"


# --- what the client re-imports ----------------------------------------------

def test_marked_export_shows_the_correction_without_re_marking(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'marked.sqlite'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        db.add(AnswerKey(
            name="Paper A v1", template_id="seamo_2025_a", paper_type="A", version=1,
            answers={"1": "C", "2": "D"}, total_questions=2, total_marks=5,
            source_filename="key.pdf", source_sha256="a" * 64, is_active=True,
            question_spec=[
                {"number": 1, "type": "mcq", "accepted_answers": ["C"], "marks": 2, "normalizer": "uppercase"},
                {"number": 2, "type": "mcq", "accepted_answers": ["D"], "marks": 3, "normalizer": "uppercase"},
            ],
        ))
        submission = _submission(db, template_id="seamo_2025_a")
        _candidate(db, submission.id, 1, "CAN100", "seamo_2025_a")
        _candidate(db, submission.id, 2, "CAN9815109755", "seamo_2025_a", answers={"1": "A", "2": "D"})
        db.commit()
        sid = submission.id

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        assert client.post(f"/submission/{sid}/mark").status_code == 200
        before = client.get(f"/submission/{sid}/marked-json").json()
        response = client.post(
            f"/submission/{sid}/candidate-number-corrections",
            json={"corrections": [_fix("CAN9815109755", "CAN9315109755")]},
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/submission/{sid}/marked-json").json()
        detail = client.get(f"/submission/{sid}").json()
    finally:
        app.dependency_overrides.clear()

    assert after["marking"]["id"] == before["marking"]["id"]
    corrected = after["candidates"][1]
    assert corrected["candidate_number"] == "CAN9315109755"
    assert corrected["marking"]["candidate_number"] == "CAN9315109755"
    assert corrected["marking"]["awarded_marks"] == before["candidates"][1]["marking"]["awarded_marks"]
    assert detail["candidates"][1]["candidate_number"] == "CAN9315109755"
    with factory() as db:
        assert db.query(MarkingRun).count() == 1
        assert db.query(CandidateMarking).count() == 2
    engine.dispose()


# --- retries must never apply a correction twice -----------------------------

def test_a_retried_swap_is_refused_rather_than_swapped_back(api):
    swap = [_fix("CAN100", "CAN200"), _fix("CAN200", "CAN100")]
    assert _correct(api, *swap).status_code == 200
    errors = _errors(_correct(api, *swap))
    assert [e["code"] for e in errors] == ["ambiguous_candidate_number"] * 2
    numbers = _numbers(api)
    assert (numbers[1], numbers[2]) == ("CAN200", "CAN100")
    # Naming the candidates makes the retry provable.
    named = [
        _fix("CAN100", "CAN200", candidate_id=api.ids[1]),
        _fix("CAN200", "CAN100", candidate_id=api.ids[2]),
    ]
    body = _correct(api, *named).json()
    assert [r["outcome"] for r in body["results"]] == ["already_applied"] * 2
    assert (_numbers(api)[1], _numbers(api)[2]) == ("CAN200", "CAN100")


def test_a_retried_rotation_is_refused_rather_than_rotated_again(api):
    rotation = [_fix("CAN100", "CAN200"), _fix("CAN200", "000123"), _fix("000123", "CAN100")]
    assert _correct(api, *rotation).status_code == 200
    after = _numbers(api)
    assert _correct(api, *rotation).status_code == 409
    assert _numbers(api) == after


def test_a_retried_chain_is_refused_rather_than_misapplied(api):
    chain = [_fix("CAN100", "CAN150"), _fix("CAN200", "CAN100")]
    assert _correct(api, *chain).status_code == 200
    after = _numbers(api)
    errors = _errors(_correct(api, *chain))
    assert [(e["index"], e["code"]) for e in errors] == [(0, "ambiguous_candidate_number")]
    assert {m["candidate_id"] for m in errors[0]["matches"]} == {api.ids[1], api.ids[2]}
    assert _numbers(api) == after


def test_a_retry_after_the_old_number_moved_elsewhere_is_refused(api):
    """Page 1 was fixed CAN100 -> CAN101; page 3 has since become CAN100."""
    assert _correct(api, _fix("CAN100", "CAN101")).status_code == 200
    assert _correct(api, _fix("CAN300", "CAN100")).status_code == 200
    errors = _errors(_correct(api, _fix("CAN100", "CAN101")))
    assert errors[0]["code"] == "ambiguous_candidate_number"
    assert _numbers(api)[3] == "CAN100"


def test_a_retry_reports_what_the_first_response_reported(api):
    first = _correct(api, _fix("000123", "000124")).json()["results"][0]
    retry = _correct(api, _fix("000123", "000124")).json()["results"][0]
    assert retry == first | {"outcome": "already_applied"}
    assert retry["previous_candidate_number"] == " 000123 "


def test_only_this_exact_number_correction_proves_a_retry(api):
    # Someone holds CAN100 with no history of being corrected to it.
    assert _errors(_correct(api, _fix("CAN999", "CAN100")))[0]["code"] == "candidate_not_found"
    _correct(api, _fix("CAN100", "CAN101"))
    assert _errors(_correct(api, _fix("CAN150", "CAN101")))[0]["code"] == "candidate_not_found"
    with api.db() as db:
        candidate = db.get(CandidateResult, api.ids[2])
        candidate.extra_fields = {"manual_edits": [
            {"target": "candidate_name", "from": "CAN999", "to": "CAN200"},
        ]}
        db.commit()
    assert _errors(_correct(api, _fix("CAN999", "CAN200")))[0]["code"] == "candidate_not_found"


# --- matching and selection --------------------------------------------------

def test_matching_is_case_sensitive(api):
    with api.db() as db:
        db.get(CandidateResult, api.ids[2]).candidate_number = "can200"
        db.commit()
    assert _errors(_correct(api, _fix("CAN200", "CAN201")))[0]["code"] == "candidate_not_found"
    assert _correct(api, _fix("can200", "CAN200")).status_code == 200


def test_candidate_id_and_page_number_must_agree(api):
    errors = _errors(_correct(api, _fix("CAN100", "CAN101", candidate_id=api.ids[1], page_number=2)))
    assert errors[0]["code"] == "candidate_not_found"
    assert "page 1, not page 2" in errors[0]["message"]
    assert _numbers(api)[1] == "CAN100"


def test_long_match_lists_are_truncated(api, monkeypatch):
    from backend.api import marking_routes

    monkeypatch.setattr(marking_routes, "_MATCH_LIMIT", 1)
    errors = _errors(_correct(api, _fix("CAN777", "CAN701")))
    assert len(errors[0]["matches"]) == 1
    assert errors[0]["truncated"] is True


def test_length_limits_apply_to_the_number_not_its_padding(api):
    response = _correct(api, _fix("CAN100", "  " + "9" * 100 + "  "))
    assert response.status_code == 200, response.text
    assert _numbers(api)[1] == "9" * 100
    assert _correct(api, _fix("C" * 1001, "CAN101")).status_code == 422


# --- which paper a candidate sat ---------------------------------------------

def _extra_submission(api, template_id, pages):
    with api.db() as db:
        submission = _submission(db, template_id=template_id)
        ids = {}
        for page, (number, page_template) in pages.items():
            candidate = CandidateResult(
                submission_id=submission.id, page_number=page,
                candidate_number=number, template_id=page_template, answers={},
            )
            db.add(candidate)
            db.flush()
            ids[page] = candidate.id
        db.commit()
        return submission.id


def test_a_page_with_no_layout_of_its_own_uses_the_submissions(api):
    sid = _extra_submission(api, "seamo_2025_c", {1: ("CAN100", None), 2: ("CAN200", "seamo_2025_c_fb")})
    errors = _errors(_correct(api, _fix("CAN200", "CAN100"), submission_id=sid))
    assert errors[0]["code"] == "candidate_number_in_use"


def test_a_page_whose_layout_was_never_identified_may_be_any_paper(api):
    sid = _extra_submission(api, None, {1: ("CAN100", "seamo_2025_c"), 2: ("", None), 3: ("CAN300", None)})
    for wrong, right in [("", "CAN100"), ("CAN300", "CAN100")]:
        errors = _errors(_correct(api, _fix(wrong, right), submission_id=sid))
        assert errors[0]["code"] == "candidate_number_in_use"
    response = _correct(api, _fix("", "CAN100", allow_duplicate=True), submission_id=sid)
    assert response.status_code == 200, response.text


def test_a_resend_after_another_client_moved_the_numbers_is_refused(api):
    assert _correct(api, _fix("CAN100", "CAN150")).status_code == 200  # response lost
    rotation = [_fix("CAN150", "CAN300"), _fix("CAN300", "CAN100")]
    assert _correct(api, *rotation).status_code == 200
    errors = _errors(_correct(api, _fix("CAN100", "CAN150")))
    assert errors[0]["code"] == "ambiguous_candidate_number"
    numbers = _numbers(api)
    assert (numbers[1], numbers[3]) == ("CAN300", "CAN100")


def test_a_correction_undone_since_is_never_redone(api):
    assert _correct(api, _fix("CAN100", "CAN101")).status_code == 200  # response lost
    api.client.post(
        f"/submission/{api.sid.main}/candidates/{api.ids[1]}/confirm-review",
        json={"candidate_number": "CAN100", "edited_by": "reviewer"},
    )
    for selector in ({}, {"candidate_id": api.ids[1]}):
        errors = _errors(_correct(api, _fix("CAN100", "CAN101", **selector)))
        assert errors[0]["code"] == "correction_superseded"
    assert _numbers(api)[1] == "CAN100"


def test_a_padded_number_can_be_trimmed(api):
    body = _correct(api, _fix("000123", "000123")).json()
    assert body["results"][0]["outcome"] == "corrected"
    assert body["results"][0]["previous_candidate_number"] == " 000123 "
    assert _numbers(api)[7] == "000123"
    assert _correct(api, _fix("000123", "000123")).json()["results"][0]["outcome"] == "unchanged"


def test_a_selector_that_selects_nothing_says_so(api):
    errors = _errors(_correct(api, _fix("CAN100", "CAN101", candidate_id=999999), _fix("CAN100", "CAN101", page_number=99)))
    assert [e["message"] for e in errors] == [
        "candidate_id 999999 is not in this submission",
        "No candidate is on page 99",
    ]
