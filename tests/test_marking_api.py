from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.database import Base, get_db
from backend.db.models import (
    AnswerKey,
    CandidateMarking,
    CandidateResult,
    ExamSubmission,
    MarkingRun,
)
from backend.api import routes
from backend.services.marking_workflow import MARKING_RUN_STALE_AFTER
from main import app


@pytest.fixture
def api(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'marking-api.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), factory, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _answer_key(
    db: Session,
    template_id: str = "seamo_2025_a",
    *,
    active: bool = True,
    version: int = 1,
) -> AnswerKey:
    key = AnswerKey(
        name=f"{template_id} v{version}",
        paper_type="A",
        answers={"1": "C", "2": "D"},
        drawing_key={"99": "secret"},
        total_questions=2,
        template_id=template_id,
        version=version,
        source_filename="Paper A key.pdf",
        source_sha256="a" * 64,
        total_marks=5,
        question_spec=[
            {
                "number": 1,
                "type": "mcq",
                "accepted_answers": ["C"],
                "marks": 2,
                "normalizer": "uppercase",
            },
            {
                "number": 2,
                "type": "mcq",
                "accepted_answers": ["D"],
                "marks": 3,
                "normalizer": "uppercase",
            },
        ],
        is_active=active,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


def _submission(
    db: Session,
    *,
    template_id: str = "seamo_2025_a",
    answers: dict[str, str] | None = None,
) -> tuple[ExamSubmission, CandidateResult]:
    submission = ExamSubmission(
        filename="students.pdf",
        original_pdf_key="uploads/students.pdf",
        result_json_key="results/raw.json",
        template_id=template_id,
        status="completed",
        pages_count=1,
        processed_at=datetime.utcnow(),
    )
    db.add(submission)
    db.flush()
    candidate = CandidateResult(
        submission_id=submission.id,
        page_number=1,
        candidate_name="Student One",
        candidate_number="001",
        country="MY",
        paper_type="A",
        extra_fields={"school": "Example"},
        answers=answers or {"1": "C", "2": "A"},
        drawing_questions={"9": "student drawing"},
    )
    db.add(candidate)
    db.commit()
    db.refresh(submission)
    db.refresh(candidate)
    return submission, candidate


def _assert_no_key_material(value):
    forbidden = {"answers", "accepted_answers", "question_spec", "drawing_key"}
    if isinstance(value, dict):
        assert not (forbidden & value.keys())
        for item in value.values():
            _assert_no_key_material(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_key_material(item)


def test_answer_key_gets_are_metadata_only_and_mutations_are_disabled(api):
    client, factory, _ = api
    with factory() as db:
        key = _answer_key(db)

    listing = client.get("/answer-keys")
    detail = client.get(f"/answer-keys/{key.id}")

    assert listing.status_code == detail.status_code == 200
    assert listing.json() == [detail.json()]
    assert set(detail.json()) == {
        "id",
        "name",
        "template_id",
        "version",
        "source_filename",
        "source_sha256",
        "total_questions",
        "total_marks",
        "is_active",
        "created_at",
        "updated_at",
    }
    _assert_no_key_material(listing.json())
    assert client.post("/answer-keys", json={"name": "rogue", "answers": {"1": "A"}}).status_code == 405
    assert client.delete(f"/answer-keys/{key.id}").status_code == 405
    assert client.get("/answer-keys/99999").status_code == 404


def test_marking_detail_exposes_completed_weighted_outcomes_without_key_answers(api):
    client, factory, _ = api
    with factory() as db:
        key = _answer_key(db)
        submission, candidate = _submission(db)
        run = MarkingRun(
            submission_id=submission.id,
            answer_key_id=key.id,
            status="completed",
            key_provenance={
                "answer_key_id": key.id,
                "template_id": key.template_id,
                "version": key.version,
                "source_filename": key.source_filename,
                "source_sha256": key.source_sha256,
                "total_marks": key.total_marks,
                "question_spec": key.question_spec,
            },
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        db.add(run)
        db.flush()
        db.add(
            CandidateMarking(
                marking_run_id=run.id,
                candidate_result_id=candidate.id,
                awarded_marks=2,
                max_marks=5,
                percentage=40.0,
                outcomes=[
                    {
                        "question_number": 1,
                        "status": "correct",
                        "response": "C",
                        "awarded_marks": 2,
                        "max_marks": 2,
                        "normalizer": "uppercase",
                    },
                    {
                        "question_number": 2,
                        "status": "incorrect",
                        "response": "A",
                        "awarded_marks": 0,
                        "max_marks": 3,
                        "normalizer": "uppercase",
                    },
                ],
            )
        )
        db.commit()
        submission_id = submission.id
        candidate_id = candidate.id

    response = client.get(f"/submission/{submission_id}/marking")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_run"]["status"] == "completed"
    assert body["latest_run"]["provenance"]["template_id"] == "seamo_2025_a"
    result = body["latest_run"]["candidates"][0]
    assert result["candidate_result_id"] == candidate_id
    assert result["awarded_marks"] == 2
    assert result["outcomes"][1]["status"] == "incorrect"
    assert len(body["history"]) == 1
    _assert_no_key_material(body)


@pytest.mark.parametrize("state", ["unmarked", "unavailable", "failed"])
def test_marking_detail_represents_unmarked_and_terminal_noncompleted_states(api, state):
    client, factory, _ = api
    with factory() as db:
        submission, _ = _submission(db, template_id="missing_template")
        if state != "unmarked":
            db.add(
                MarkingRun(
                    submission_id=submission.id,
                    status=state,
                    error_message=(
                        "No active answer key for template missing_template"
                        if state == "unavailable"
                        else "secret stack trace and accepted answer C"
                    ),
                    completed_at=datetime.utcnow(),
                )
            )
            db.commit()
        submission_id = submission.id

    response = client.get(f"/submission/{submission_id}/marking")

    assert response.status_code == 200
    body = response.json()
    if state == "unmarked":
        assert body == {
            "submission_id": submission_id,
            "latest_run": None,
            "history": [],
        }
    else:
        assert body["latest_run"]["status"] == state
        assert body["latest_run"]["candidates"] == []
        assert "secret" not in str(body).lower()
        _assert_no_key_material(body)


def test_marking_read_recovers_stale_run_with_sanitized_retry_state(api):
    client, factory, _ = api
    with factory() as db:
        submission, candidate = _submission(db)
        raw_before = dict(candidate.answers)
        stale = MarkingRun(
            submission_id=submission.id,
            status="processing",
            error_message="secret crash payload accepted answer C",
            started_at=datetime.utcnow() - MARKING_RUN_STALE_AFTER - timedelta(seconds=1),
        )
        db.add(stale)
        db.commit()
        submission_id = submission.id
        stale_id = stale.id
        candidate_id = candidate.id

    response = client.get(f"/submission/{submission_id}/marking")

    assert response.status_code == 200
    latest = response.json()["latest_run"]
    assert latest["id"] == stale_id
    assert latest["status"] == "failed"
    assert latest["error_message"] == "Marking failed"
    assert "secret" not in str(response.json()).lower()
    with factory() as db:
        recovered = db.get(MarkingRun, stale_id)
        assert recovered.error_message == "Marking interrupted; retry is available"
        assert recovered.completed_at is not None
        assert db.get(CandidateResult, candidate_id).answers == raw_before


def test_manual_mark_returns_conflict_for_fresh_run_without_duplicate(api):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)
        submission, _ = _submission(db)
        active = MarkingRun(
            submission_id=submission.id,
            status="processing",
            started_at=datetime.utcnow(),
        )
        db.add(active)
        db.commit()
        submission_id = submission.id
        active_id = active.id

    response = client.post(f"/submission/{submission_id}/mark", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "Marking is already in progress"
    with factory() as db:
        runs = db.scalars(
            select(MarkingRun).where(MarkingRun.submission_id == submission_id)
        ).all()
        assert [run.id for run in runs] == [active_id]
        assert runs[0].status == "processing"


def test_manual_retry_recovers_stale_run_then_creates_completed_history(api):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)
        submission, _ = _submission(db)
        stale = MarkingRun(
            submission_id=submission.id,
            status="processing",
            started_at=datetime.utcnow() - MARKING_RUN_STALE_AFTER - timedelta(seconds=1),
        )
        db.add(stale)
        db.commit()
        submission_id = submission.id
        stale_id = stale.id

    response = client.post(f"/submission/{submission_id}/mark", json={})

    assert response.status_code == 200
    assert response.json()["run"]["status"] == "completed"
    history = client.get(f"/submission/{submission_id}/marking").json()["history"]
    assert [run["status"] for run in history] == ["completed", "failed"]
    assert history[-1]["id"] == stale_id


def test_manual_remark_defaults_to_exact_active_key_and_appends_history(api):
    client, factory, _ = api
    with factory() as db:
        key = _answer_key(db)
        key_id = key.id
        submission, candidate = _submission(db)
        submission_id = submission.id
        candidate_id = candidate.id
        raw_before = dict(candidate.answers)

    first = client.post(f"/submission/{submission_id}/mark", json={})
    second = client.post(f"/submission/{submission_id}/mark", json={"answer_key_id": key_id})

    assert first.status_code == second.status_code == 200
    assert first.json()["run"]["status"] == second.json()["run"]["status"] == "completed"
    assert first.json()["run"]["id"] != second.json()["run"]["id"]
    assert second.json()["run"]["candidates"][0]["awarded_marks"] == 2
    history = client.get(f"/submission/{submission_id}/marking").json()["history"]
    assert [item["id"] for item in history] == [
        second.json()["run"]["id"],
        first.json()["run"]["id"],
    ]
    _assert_no_key_material(first.json())
    _assert_no_key_material(second.json())
    with factory() as db:
        assert db.get(CandidateResult, candidate_id).answers == raw_before


def test_nullable_candidate_number_is_empty_string_in_marking_responses(api):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)
        submission, candidate = _submission(db)
        candidate.candidate_number = None
        db.commit()
        submission_id = submission.id

    remarked = client.post(f"/submission/{submission_id}/mark", json={})
    history = client.get(f"/submission/{submission_id}/marking")

    assert remarked.status_code == history.status_code == 200
    assert remarked.json()["run"]["candidates"][0]["candidate_number"] == ""
    assert history.json()["latest_run"]["candidates"][0]["candidate_number"] == ""


def test_empty_candidate_submission_mark_and_export_return_same_conflict(api):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)
        submission = ExamSubmission(
            filename="empty.pdf",
            original_pdf_key="empty.pdf",
            template_id="seamo_2025_a",
            status="completed",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    remarked = client.post(f"/submission/{submission_id}/mark", json={})
    exported = client.get(f"/submission/{submission_id}/marked-json")

    assert remarked.status_code == exported.status_code == 409
    assert remarked.json()["detail"] == exported.json()["detail"]
    assert remarked.json()["detail"] == "No candidate results available for marking"
    marking = client.get(f"/submission/{submission_id}/marking").json()
    assert marking["latest_run"]["status"] == "failed"
    assert marking["latest_run"]["error_message"] == "Marking failed"


def test_manual_remark_rejects_unknown_key_allows_other_template_key(api):
    """Override key no longer requires submission.template_id match (mixed PDFs)."""
    client, factory, _ = api
    with factory() as db:
        _answer_key(db, "seamo_2025_a")
        other = _answer_key(db, "seamo_2025_b")
        submission, _ = _submission(db)
        submission_id = submission.id
        other_key_id = other.id

    # Other-template key is accepted; it only applies to matching candidate groups.
    other_resp = client.post(
        f"/submission/{submission_id}/mark", json={"answer_key_id": other_key_id}
    )
    assert other_resp.status_code == 200

    assert client.post(
        f"/submission/{submission_id}/mark", json={"answer_key_id": 99999}
    ).status_code == 404
    assert client.post("/submission/99999/mark", json={}).status_code == 404


def test_marked_json_is_downloadable_current_db_view_separate_from_raw_export(api):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)
        submission, candidate = _submission(db)
        submission_id = submission.id
        candidate_id = candidate.id

    assert client.post(f"/submission/{submission_id}/mark", json={}).status_code == 200
    marked = client.get(f"/submission/{submission_id}/marked-json")

    assert marked.status_code == 200
    assert marked.headers["content-type"].startswith("application/json")
    assert "attachment;" in marked.headers["content-disposition"]
    assert "marked" in marked.headers["content-disposition"]
    body = marked.json()
    assert body["submission"]["id"] == submission_id
    assert body["submission"]["template_id"] == "seamo_2025_a"
    assert body["marking"]["status"] == "completed"
    exported = body["candidates"][0]
    assert exported["id"] == candidate_id
    assert exported["answers"] == {"1": "C", "2": "A"}
    assert exported["marking"]["awarded_marks"] == 2
    assert exported["marking"]["outcomes"][0]["response"] == "C"
    _assert_no_key_material(body["marking"])
    _assert_no_key_material(exported["marking"])

    raw = client.get(f"/submission/{submission_id}")
    assert raw.status_code == 200
    assert raw.json()["candidates"][0]["answers"] == {"1": "C", "2": "A"}
    assert raw.json()["candidates"][0]["id"] == candidate_id
    assert raw.json()["candidates"][0]["marking"]["awarded_marks"] == 2
    assert client.get(f"/submission/{submission_id}/json").status_code != marked.status_code


def test_marked_json_returns_conflict_for_unmarked_or_noncompleted_run(api):
    client, factory, _ = api
    with factory() as db:
        unmarked, _ = _submission(db)
        unavailable, _ = _submission(db, template_id="missing")
        db.add(MarkingRun(submission_id=unavailable.id, status="unavailable"))
        db.commit()
        unmarked_id = unmarked.id
        unavailable_id = unavailable.id

    assert client.get(f"/submission/{unmarked_id}/marked-json").status_code == 409
    assert client.get(f"/submission/{unavailable_id}/marked-json").status_code == 409
    assert client.get("/submission/99999/marked-json").status_code == 404


@pytest.mark.parametrize(
    ("filename", "ascii_fallback", "encoded_name"),
    [
        (
            'folder/bad"\r\nname.pdf',
            "bad_name.marked.json",
            "bad%22name.marked.json",
        ),
        (
            r"..\unsafe\path:name.pdf",
            "path_name.marked.json",
            "path%3Aname.marked.json",
        ),
        (
            "答案.pdf",
            "download.marked.json",
            "%E7%AD%94%E6%A1%88.marked.json",
        ),
    ],
)
def test_marked_export_content_disposition_is_safe_and_utf8_encoded(
    api, filename, ascii_fallback, encoded_name
):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)
        submission, _ = _submission(db)
        submission.filename = filename
        db.commit()
        submission_id = submission.id

    assert client.post(f"/submission/{submission_id}/mark", json={}).status_code == 200
    response = client.get(f"/submission/{submission_id}/marked-json")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition == (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{encoded_name}"
    )
    assert "\r" not in disposition and "\n" not in disposition


def test_submission_detail_loads_latest_candidate_marking_without_n_plus_one(api):
    client, factory, engine = api
    with factory() as db:
        key = _answer_key(db)
        submission, first = _submission(db)
        db.add(
            CandidateResult(
                submission_id=submission.id,
                page_number=2,
                candidate_number="002",
                answers={"1": "A", "2": "D"},
            )
        )
        db.commit()
        submission_id = submission.id
        key_id = key.id
    assert client.post(f"/submission/{submission_id}/mark", json={"answer_key_id": key_id}).status_code == 200

    statement_count = 0

    def count_queries(*_args):
        nonlocal statement_count
        statement_count += 1

    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        response = client.get(f"/submission/{submission_id}")
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)

    assert response.status_code == 200
    assert len(response.json()["candidates"]) == 2
    assert all(candidate["marking"] is not None for candidate in response.json()["candidates"])
    assert response.json()["latest_marking"]["status"] == "completed"
    assert statement_count <= 5


def test_synchronous_marking_uses_weighted_service_and_rejects_inline_keys(
    api, monkeypatch, tmp_path
):
    client, factory, _ = api
    with factory() as db:
        _answer_key(db)

    page = tmp_path / "page.png"
    page.write_text("page")
    monkeypatch.setattr(
        "backend.services.template_service.get_template_registry",
        lambda: type("Registry", (), {"get": lambda _self, _id: object()})(),
    )
    monkeypatch.setattr(
        routes,
        "get_local_storage",
        lambda: type(
            "Storage",
            (),
            {
                "save_pdf": lambda _self, *_args: {
                    "absolute_path": str(tmp_path / "students.pdf")
                }
            },
        )(),
    )
    monkeypatch.setattr(
        routes,
        "get_pdf_converter",
        lambda: type(
            "Converter",
            (),
            {"convert_from_file": lambda _self, _path: [str(page)]},
        )(),
    )
    monkeypatch.setattr(
        routes,
        "TemplateExtractor",
        lambda _template_id: type(
            "Extractor",
            (),
            {
                "extract_pdf": lambda _self, *_args, **_kwargs: {
                    "candidates": [
                        {
                            "candidate_number": "001",
                            "answers": {"1": "C", "2": "A"},
                        }
                    ]
                }
            },
        )(),
    )

    response = client.post(
        "/extract/json/mark?template_id=seamo_2025_a",
        files={"file": ("students.pdf", b"pdf", "application/pdf")},
    )

    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["answers"] == {"1": "C", "2": "A"}
    assert candidate["marking"]["awarded_marks"] == 2
    assert candidate["marking"]["outcomes"][1]["status"] == "incorrect"
    _assert_no_key_material(candidate["marking"])

    rejected = client.post(
        "/extract/json/mark?template_id=seamo_2025_a",
        files={"file": ("students.pdf", b"pdf", "application/pdf")},
        data={"mark_request": '{"answer_key": {"1": "A"}}'},
    )
    assert rejected.status_code == 400
