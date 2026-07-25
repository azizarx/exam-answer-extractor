import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.api import routes
from backend.db.database import Base, init_db
from backend.db.models import (
    AnswerKey,
    CandidateMarking,
    CandidateResult,
    ExamSubmission,
    MarkingRun,
)
from backend.services.marking_workflow import (
    MARKING_RUN_STALE_AFTER,
    MarkingInProgressError,
    _is_processing_ownership_violation,
    mark_submission_answers,
    recover_stale_marking_runs,
    record_failed_marking_attempt,
)
from backend.services.marking_service import MarkingService


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def engine(tmp_path):
    value = create_engine(f"sqlite:///{tmp_path / 'workflow.sqlite'}")
    Base.metadata.create_all(value)
    return value


def _key(template_id, *, paper_type="A", answer="C", active=True):
    return AnswerKey(
        name=template_id,
        paper_type=paper_type,
        answers={"1": answer},
        total_questions=1,
        template_id=template_id,
        version=1,
        source_filename=f"{template_id}.pdf",
        source_sha256="a" * 64,
        total_marks=3,
        question_spec=[
            {
                "number": 1,
                "type": "mcq",
                "accepted_answers": [answer],
                "marks": 3,
                "normalizer": "uppercase",
            }
        ],
        is_active=active,
    )


def _submission(db, template_id="seamo_2025_a", answers=None):
    submission = ExamSubmission(
        filename="paper.pdf",
        original_pdf_key="paper.pdf",
        template_id=template_id,
        status="processing",
    )
    db.add(submission)
    db.flush()
    candidate = CandidateResult(
        submission_id=submission.id,
        candidate_number="001",
        paper_type="A",
        answers=answers or {"1": "C"},
    )
    db.add(candidate)
    db.commit()
    return submission.id, candidate.id


def test_exact_template_resolution_ignores_same_paper_letter(engine):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        exact = _key("seamo_2025_a", answer="C")
        decoy = _key("seamo_2024_a", answer="D")
        db.add_all([decoy, exact])
        db.commit()

        run = mark_submission_answers(db, submission_id)

        assert run.status == "completed"
        assert run.answer_key_id == exact.id
        assert run.candidate_markings[0].awarded_marks == 3


def test_completed_run_persists_weighted_results_and_provenance(engine):
    with Session(engine) as db:
        submission_id, candidate_id = _submission(db)
        key = _key("seamo_2025_a")
        db.add(key)
        db.commit()

        run = mark_submission_answers(db, submission_id)
        db.expire_all()
        persisted = db.get(MarkingRun, run.id)
        marking = db.scalar(
            select(CandidateMarking).where(
                CandidateMarking.marking_run_id == persisted.id
            )
        )

        assert persisted.status == "completed"
        assert persisted.started_at is not None and persisted.completed_at is not None
        assert persisted.key_provenance == {
            "keys": [
                {
                    "answer_key_id": key.id,
                    "template_id": "seamo_2025_a",
                    "version": 1,
                    "source_filename": "seamo_2025_a.pdf",
                    "source_sha256": "a" * 64,
                    "total_marks": 3,
                    "question_spec": key.question_spec,
                    "candidate_count": 1,
                }
            ],
            "missing_templates": [],
        }
        assert marking.candidate_result_id == candidate_id
        assert marking.answer_key_id == key.id
        assert (marking.awarded_marks, marking.max_marks, marking.percentage) == (
            3,
            3,
            100.0,
        )
        assert marking.outcomes[0]["status"] == "correct"


def test_mixed_templates_mark_with_per_candidate_keys(engine):
    with Session(engine) as db:
        submission = ExamSubmission(
            filename="mixed.pdf",
            original_pdf_key="mixed.pdf",
            template_id=None,
            status="processing",
        )
        db.add(submission)
        db.flush()
        cand_a = CandidateResult(
            submission_id=submission.id,
            candidate_number="A1",
            template_id="seamo_2025_a",
            answers={"1": "C"},
        )
        cand_b = CandidateResult(
            submission_id=submission.id,
            candidate_number="B1",
            template_id="seamo_2025_b",
            answers={"1": "D"},
        )
        cand_x = CandidateResult(
            submission_id=submission.id,
            candidate_number="X1",
            template_id="seamo_x_2026_a",
            answers={"1": "C"},
        )
        key_a = _key("seamo_2025_a", answer="C")
        key_b = _key("seamo_2025_b", paper_type="B", answer="D")
        db.add_all([cand_a, cand_b, cand_x, key_a, key_b])
        db.commit()

        run = mark_submission_answers(db, submission.id)
        db.expire_all()
        markings = (
            db.query(CandidateMarking)
            .filter(CandidateMarking.marking_run_id == run.id)
            .all()
        )

        assert run.status == "completed"
        assert "seamo_x_2026_a" in (run.error_message or "")
        assert len(markings) == 2
        by_cand = {m.candidate_result_id: m for m in markings}
        assert by_cand[cand_a.id].answer_key_id == key_a.id
        assert by_cand[cand_a.id].awarded_marks == 3
        assert by_cand[cand_b.id].answer_key_id == key_b.id
        assert by_cand[cand_b.id].awarded_marks == 3
        assert cand_x.id not in by_cand


def test_format_b_layout_marks_with_classic_answer_key(engine):
    """Format-B layout ids share the classic template's answer key."""
    with Session(engine) as db:
        submission = ExamSubmission(
            filename="format-b.pdf",
            original_pdf_key="format-b.pdf",
            template_id=None,
            status="processing",
        )
        db.add(submission)
        db.flush()
        cand = CandidateResult(
            submission_id=submission.id,
            candidate_number="FB1",
            template_id="seamo_2025_a_fb",
            answers={"1": "C"},
        )
        key = _key("seamo_2025_a", answer="C")
        db.add_all([cand, key])
        db.commit()

        run = mark_submission_answers(db, submission.id)
        db.expire_all()
        marking = db.scalar(
            select(CandidateMarking).where(CandidateMarking.marking_run_id == run.id)
        )

        assert run.status == "completed"
        assert marking is not None
        assert marking.answer_key_id == key.id
        assert marking.awarded_marks == 3


def test_workflow_uses_fr_judge_and_persists_judged_outcome(engine):
    class FakeJudge:
        def judge(self, items):
            assert items == [
                {
                    "question_number": 1,
                    "type": "time",
                    "accepted_answers": ["5:00 PM"],
                    "response": "5:00vaqt",
                }
            ]
            return [
                {
                    "question_number": 1,
                    "verdict": "equivalent",
                    "reason": "same time",
                }
            ]

    with Session(engine) as db:
        submission_id, _ = _submission(db, answers={"1": "5:00vaqt"})
        key = _key("seamo_2025_a")
        key.question_spec = [
            {
                "number": 1,
                "type": "time",
                "accepted_answers": ["5:00 PM"],
                "marks": 3,
                "normalizer": "time_12_24",
            }
        ]
        db.add(key)
        db.commit()

        run = mark_submission_answers(db, submission_id, fr_judge=FakeJudge())
        marking = db.scalar(
            select(CandidateMarking).where(CandidateMarking.marking_run_id == run.id)
        )

        assert marking.outcomes[0]["status"] == "correct"
        assert marking.outcomes[0]["response"] == "5:00vaqt"
        assert marking.outcomes[0]["judge_source"] == "llm"


def test_no_exact_active_key_creates_unavailable_run_without_marks(engine):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        db.add(_key("seamo_2024_a"))
        db.commit()

        run = mark_submission_answers(db, submission_id)

        assert run.status == "unavailable"
        assert run.answer_key_id is None
        assert "seamo_2025_a" in (run.error_message or "")
        assert run.candidate_markings == []


def test_marking_failure_rolls_back_marks_and_preserves_raw_data(
    engine, monkeypatch
):
    with Session(engine) as db:
        original = {"1": "C"}
        submission_id, candidate_id = _submission(db, answers=original)
        db.add(_key("seamo_2025_a"))
        db.commit()

        def fail_mark(_self, _answers):
            raise ValueError("secret answer-key payload")

        monkeypatch.setattr(
            "backend.services.marking_workflow.MarkingService.mark", fail_mark
        )
        run = mark_submission_answers(db, submission_id)

        assert run.status == "failed"
        assert run.error_message == "Marking failed (ValueError)"
        assert "secret" not in run.error_message
        assert db.scalar(select(func.count()).select_from(CandidateMarking)) == 0
        assert db.get(CandidateResult, candidate_id).answers == original
        assert db.get(ExamSubmission, submission_id).status == "processing"


def test_manual_remark_appends_history_without_mutating_raw_answers(engine):
    with Session(engine) as db:
        original = {"1": "C"}
        submission_id, candidate_id = _submission(db, answers=original)
        db.add(_key("seamo_2025_a"))
        db.commit()

        first = mark_submission_answers(db, submission_id)
        second = mark_submission_answers(db, submission_id)

        assert first.id != second.id
        assert db.scalars(
            select(MarkingRun)
            .where(MarkingRun.submission_id == submission_id)
            .order_by(MarkingRun.id)
        ).all() == [first, second]
        assert db.scalar(select(func.count()).select_from(CandidateMarking)) == 2
        assert db.get(CandidateResult, candidate_id).answers == original


def test_zero_candidate_marking_fails_without_changing_completed_extraction(engine):
    with Session(engine) as db:
        submission = ExamSubmission(
            filename="empty.pdf",
            original_pdf_key="empty.pdf",
            template_id="seamo_2025_a",
            status="completed",
        )
        db.add(submission)
        db.flush()
        db.add(_key("seamo_2025_a"))
        db.commit()
        submission_id = submission.id

        run = mark_submission_answers(db, submission_id)

        assert run.status == "failed"
        assert run.error_message == "Marking failed (NoCandidatesError)"
        assert run.candidate_markings == []
        assert db.get(ExamSubmission, submission_id).status == "completed"


def test_stale_processing_recovery_is_idempotent_and_preserves_data(engine):
    with Session(engine) as db:
        original = {"1": "C"}
        submission_id, candidate_id = _submission(db, answers=original)
        provenance = {"template_id": "seamo_2025_a", "version": 1}
        stale = MarkingRun(
            submission_id=submission_id,
            status="processing",
            key_provenance=provenance,
            started_at=datetime.utcnow() - MARKING_RUN_STALE_AFTER - timedelta(seconds=1),
        )
        db.add(stale)
        db.commit()

        first = recover_stale_marking_runs(db)
        completed_at = db.get(MarkingRun, stale.id).completed_at
        second = recover_stale_marking_runs(db)

        recovered = db.get(MarkingRun, stale.id)
        assert [run.id for run in first] == [stale.id]
        assert second == []
        assert recovered.status == "failed"
        assert recovered.completed_at == completed_at
        assert recovered.error_message == "Marking interrupted; retry is available"
        assert recovered.key_provenance == provenance
        assert db.get(CandidateResult, candidate_id).answers == original


def test_fresh_processing_run_is_preserved_and_blocks_duplicate_marking(engine):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        active = MarkingRun(
            submission_id=submission_id,
            status="processing",
            started_at=datetime.utcnow(),
        )
        db.add(active)
        db.commit()

        assert recover_stale_marking_runs(db) == []
        with pytest.raises(MarkingInProgressError) as raised:
            mark_submission_answers(db, submission_id)

        assert raised.value.run_id == active.id
        assert db.get(MarkingRun, active.id).status == "processing"
        assert db.scalar(select(func.count()).select_from(MarkingRun)) == 1


def test_two_sqlite_sessions_compete_for_one_processing_run(tmp_path, monkeypatch):
    ownership_barrier = threading.Barrier(2, timeout=3)

    class RacingSession(Session):
        def commit(self):
            if any(
                isinstance(item, MarkingRun) and item.status == "processing"
                for item in self.new
            ):
                ownership_barrier.wait()
            return super().commit()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'ownership-race.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 3},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=3000")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=RacingSession)
    with factory() as db:
        submission_id, _ = _submission(db)
        db.add(_key("seamo_2025_a"))
        db.commit()

    mark_entered = threading.Event()
    release_marking = threading.Event()
    original_mark = MarkingService.mark

    def pause_winner(self, answers):
        mark_entered.set()
        assert release_marking.wait(timeout=3)
        return original_mark(self, answers)

    monkeypatch.setattr(MarkingService, "mark", pause_winner)
    outcomes = []
    conflict_observed = threading.Event()

    def compete():
        try:
            with factory() as db:
                run = mark_submission_answers(db, submission_id)
                outcomes.append(("winner", run.id))
        except MarkingInProgressError as exc:
            outcomes.append(("conflict", exc.run_id))
            conflict_observed.set()
        except BaseException as exc:
            outcomes.append(("error", exc))
            conflict_observed.set()

    workers = [threading.Thread(target=compete) for _ in range(2)]
    for worker in workers:
        worker.start()
    assert mark_entered.wait(timeout=3)
    assert conflict_observed.wait(timeout=3)
    with Session(engine) as observer:
        processing = observer.scalars(
            select(MarkingRun).where(MarkingRun.status == "processing")
        ).all()
        assert len(processing) == 1
    release_marking.set()
    for worker in workers:
        worker.join(timeout=3)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(kind for kind, _ in outcomes) == ["conflict", "winner"]
    with Session(engine) as db:
        runs = db.scalars(select(MarkingRun)).all()
        assert len(runs) == 1
        assert runs[0].status == "completed"
    engine.dispose()


def test_processing_ownership_violation_detection_is_backend_specific():
    sqlite_known = IntegrityError(
        "INSERT INTO marking_runs (submission_id, status) VALUES (?, ?)",
        (1, "processing"),
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: marking_runs.submission_id"
        ),
    )

    class PostgresOriginal(Exception):
        class Diag:
            constraint_name = "uq_marking_runs_one_processing_submission"

        diag = Diag()

    postgres_known = IntegrityError(
        "INSERT INTO marking_runs (submission_id, status) VALUES (%s, %s)",
        (1, "processing"),
        PostgresOriginal("duplicate key value violates unique constraint"),
    )
    unrelated = IntegrityError(
        "INSERT INTO candidate_markings (marking_run_id) VALUES (?)",
        (1,),
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: candidate_markings.marking_run_id"
        ),
    )

    assert _is_processing_ownership_violation(sqlite_known)
    assert _is_processing_ownership_violation(postgres_known)
    assert not _is_processing_ownership_violation(unrelated)


def test_fast_winner_still_translates_loser_integrity_race(
    tmp_path, monkeypatch
):
    ownership_barrier = threading.Barrier(2, timeout=3)

    class RacingSession(Session):
        def commit(self):
            if any(
                isinstance(item, MarkingRun) and item.status == "processing"
                for item in self.new
            ):
                ownership_barrier.wait()
            return super().commit()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'fast-winner.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 3},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=3000")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=RacingSession)
    with factory() as db:
        submission_id, _ = _submission(db)
        db.add(_key("seamo_2025_a"))
        db.commit()

    winner_done = threading.Event()
    original_detector = _is_processing_ownership_violation

    def delay_conflict_lookup(error):
        known = original_detector(error)
        if known:
            assert winner_done.wait(timeout=3)
        return known

    monkeypatch.setattr(
        "backend.services.marking_workflow._is_processing_ownership_violation",
        delay_conflict_lookup,
    )
    outcomes = []

    def compete():
        try:
            with factory() as db:
                run = mark_submission_answers(db, submission_id)
                outcomes.append(("winner", run.id))
                winner_done.set()
        except MarkingInProgressError as exc:
            outcomes.append(("conflict", exc.run_id))
        except BaseException as exc:
            outcomes.append(("error", exc))
            winner_done.set()

    workers = [threading.Thread(target=compete) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(kind for kind, _ in outcomes) == ["conflict", "winner"]
    winner_id = next(value for kind, value in outcomes if kind == "winner")
    conflict_id = next(value for kind, value in outcomes if kind == "conflict")
    assert conflict_id == winner_id
    with Session(engine) as db:
        runs = db.scalars(select(MarkingRun)).all()
        assert len(runs) == 1
        assert runs[0].status == "completed"
    engine.dispose()


def test_unrelated_initial_run_integrity_error_propagates(engine):
    unrelated = IntegrityError(
        "INSERT INTO other_table (value) VALUES (?)",
        ("value",),
        sqlite3.IntegrityError("UNIQUE constraint failed: other_table.value"),
    )

    class FailingSession(Session):
        def commit(self):
            if any(isinstance(item, MarkingRun) for item in self.new):
                raise unrelated
            return super().commit()

    factory = sessionmaker(bind=engine, class_=FailingSession)
    with factory() as db:
        submission_id, _ = _submission(db)

        with pytest.raises(IntegrityError) as raised:
            mark_submission_answers(db, submission_id)

        assert raised.value is unrelated
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(MarkingRun)) == 0


def test_retry_after_stale_recovery_appends_new_completed_run(engine):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        db.add(_key("seamo_2025_a"))
        stale = MarkingRun(
            submission_id=submission_id,
            status="processing",
            started_at=datetime.utcnow() - MARKING_RUN_STALE_AFTER - timedelta(seconds=1),
        )
        db.add(stale)
        db.commit()

        retried = mark_submission_answers(db, submission_id)
        runs = db.scalars(
            select(MarkingRun)
            .where(MarkingRun.submission_id == submission_id)
            .order_by(MarkingRun.id)
        ).all()

        assert [run.status for run in runs] == ["failed", "completed"]
        assert retried.id == runs[-1].id


def test_startup_recovers_stale_processing_runs(engine):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        stale = MarkingRun(
            submission_id=submission_id,
            status="processing",
            started_at=datetime.utcnow() - MARKING_RUN_STALE_AFTER - timedelta(seconds=1),
        )
        db.add(stale)
        db.commit()
        stale_id = stale.id

    init_db(bind=engine)

    with Session(engine) as db:
        recovered = db.get(MarkingRun, stale_id)
        assert recovered.status == "failed"
        assert recovered.completed_at is not None


def test_failed_workflow_fallback_does_not_duplicate_existing_failed_run(engine):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        existing = MarkingRun(
            submission_id=submission_id,
            status="failed",
            error_message="Marking failed (ValueError)",
        )
        db.add(existing)
        db.commit()

        recovered = record_failed_marking_attempt(
            db, submission_id, RuntimeError("secret"), after_run_id=0
        )

        assert recovered.id == existing.id
        assert db.scalar(select(func.count()).select_from(MarkingRun)) == 1
        assert recovered.error_message == "Marking failed (ValueError)"


@pytest.mark.parametrize("terminal_status", ["completed", "unavailable"])
def test_post_commit_refresh_failure_reuses_existing_terminal_run(
    engine, terminal_status
):
    with Session(engine) as db:
        submission_id, _ = _submission(db)
        existing = MarkingRun(
            submission_id=submission_id,
            status=terminal_status,
        )
        db.add(existing)
        db.commit()

        recovered = record_failed_marking_attempt(
            db,
            submission_id,
            RuntimeError("simulated post-commit refresh failure"),
            after_run_id=0,
        )

        assert recovered.id == existing.id
        assert recovered.status == terminal_status
        assert db.scalar(select(func.count()).select_from(MarkingRun)) == 1


def test_process_extraction_commits_raw_rows_before_terminal_marking(
    engine, monkeypatch, tmp_path
):
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        submission = ExamSubmission(
            filename="paper.pdf",
            original_pdf_key="paper.pdf",
            template_id="seamo_2025_a",
            status="pending",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    image = tmp_path / "page.png"
    image.write_text("image")
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(
        routes, "get_pdf_converter", lambda: type("C", (), {
            "convert_from_file": lambda _self, _path: [str(image)]
        })()
    )
    monkeypatch.setattr(
        routes,
        "TemplateExtractor",
        lambda _template_id: type("E", (), {
            "extract_pdf": lambda _self, *_args, **_kwargs: {
                "candidates": [
                    {
                        "candidate_number": "001",
                        "paper_type": "A",
                        "answers": {"1": "C"},
                    }
                ],
                "pages_processed": 1,
                "pages_with_data": 1,
            }
        })(),
    )
    monkeypatch.setattr(
        routes, "get_json_generator", lambda: type("J", (), {
            "generate_with_validation": lambda _self, *_args: {"candidates": []}
        })()
    )
    monkeypatch.setattr(
        routes, "get_local_storage", lambda: type("S", (), {
            "save_json": lambda _self, *_args: {"relative_path": "result.json"}
        })()
    )
    monkeypatch.setattr(
        routes,
        "attach_run_log",
        lambda *_args: type("L", (), {"path": tmp_path / "run.log"})(),
    )
    monkeypatch.setattr(routes, "detach_run_log", lambda *_args: None)

    observed = {}

    def fake_mark(_db, target_submission_id):
        with factory() as observer:
            observed["raw_count"] = observer.scalar(
                select(func.count()).select_from(CandidateResult).where(
                    CandidateResult.submission_id == target_submission_id
                )
            )
        raise RuntimeError("secret workflow details")

    monkeypatch.setattr(routes, "mark_submission_answers", fake_mark)

    routes.process_pdf_extraction(
        submission_id, str(tmp_path / "paper.pdf"), "seamo_2025_a"
    )

    with factory() as db:
        assert observed["raw_count"] == 1
        assert db.get(ExamSubmission, submission_id).status == "completed"
        run = db.scalar(
            select(MarkingRun).where(MarkingRun.submission_id == submission_id)
        )
        assert run.status == "failed"
        assert run.error_message == "Marking failed (RuntimeError)"
        assert "secret" not in run.error_message
        assert db.scalar(select(func.count()).select_from(CandidateResult)) == 1


def test_transient_boundary_lookup_failure_creates_new_failed_attempt(
    engine, monkeypatch, tmp_path
):
    class MarkingLookupFailureSession(Session):
        marking_query_attempts = 0

        def query(self, *entities, **kwargs):
            if entities and entities[0] is MarkingRun:
                self.marking_query_attempts += 1
                if self.marking_query_attempts == 1:
                    raise RuntimeError("marking database lookup failed")
            return super().query(*entities, **kwargs)

    factory = sessionmaker(
        bind=engine,
        class_=MarkingLookupFailureSession,
        autocommit=False,
        autoflush=False,
    )
    with factory() as db:
        submission = ExamSubmission(
            filename="paper.pdf",
            original_pdf_key="paper.pdf",
            template_id="seamo_2025_a",
            status="pending",
        )
        db.add(submission)
        db.flush()
        db.add(MarkingRun(submission_id=submission.id, status="completed"))
        db.commit()
        submission_id = submission.id

    image = tmp_path / "lookup-page.png"
    image.write_text("image")
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(
        routes,
        "get_pdf_converter",
        lambda: type("C", (), {
            "convert_from_file": lambda _self, _path: [str(image)]
        })(),
    )
    monkeypatch.setattr(
        routes,
        "TemplateExtractor",
        lambda _template_id: type("E", (), {
            "extract_pdf": lambda _self, *_args, **_kwargs: {
                "candidates": [
                    {
                        "candidate_number": "001",
                        "paper_type": "A",
                        "answers": {"1": "C"},
                    }
                ],
                "pages_processed": 1,
                "pages_with_data": 1,
            }
        })(),
    )
    monkeypatch.setattr(
        routes,
        "get_json_generator",
        lambda: type("J", (), {
            "generate_with_validation": lambda _self, *_args: {"candidates": []}
        })(),
    )
    monkeypatch.setattr(
        routes,
        "get_local_storage",
        lambda: type("S", (), {
            "save_json": lambda _self, *_args: {"relative_path": "result.json"}
        })(),
    )
    monkeypatch.setattr(
        routes,
        "attach_run_log",
        lambda *_args: type("L", (), {"path": tmp_path / "run.log"})(),
    )
    monkeypatch.setattr(routes, "detach_run_log", lambda *_args: None)

    routes.process_pdf_extraction(
        submission_id, str(tmp_path / "paper.pdf"), "seamo_2025_a"
    )

    with Session(engine) as db:
        assert db.get(ExamSubmission, submission_id).status == "completed"
        assert db.scalar(select(func.count()).select_from(CandidateResult)) == 1
        runs = db.scalars(
            select(MarkingRun)
            .where(MarkingRun.submission_id == submission_id)
            .order_by(MarkingRun.id)
        ).all()
        assert [run.status for run in runs] == ["completed", "failed"]
        assert runs[-1].error_message == "Marking failed (RuntimeError)"


def test_process_extraction_genuine_extraction_error_sets_submission_failed(
    engine, monkeypatch, tmp_path
):
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        submission = ExamSubmission(
            filename="paper.pdf",
            original_pdf_key="paper.pdf",
            template_id="seamo_2025_a",
            status="pending",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(
        routes,
        "attach_run_log",
        lambda *_args: type("L", (), {"path": tmp_path / "run.log"})(),
    )
    monkeypatch.setattr(routes, "detach_run_log", lambda *_args: None)
    monkeypatch.setattr(
        routes,
        "get_pdf_converter",
        lambda: type("C", (), {
            "convert_from_file": lambda _self, _path: (_ for _ in ()).throw(
                RuntimeError("conversion failed")
            )
        })(),
    )

    routes.process_pdf_extraction(
        submission_id, str(tmp_path / "paper.pdf"), "seamo_2025_a"
    )

    with factory() as db:
        submission = db.get(ExamSubmission, submission_id)
        assert submission.status == "failed"
        assert submission.error_message == "conversion failed"
        assert db.scalar(select(func.count()).select_from(CandidateResult)) == 0
        assert db.scalar(select(func.count()).select_from(MarkingRun)) == 0


@pytest.mark.parametrize(
    ("include_exact", "expected_status"),
    [(True, "correct"), (False, None)],
)
def test_synchronous_extract_mark_uses_exact_template_or_leaves_unmarked(
    engine, monkeypatch, tmp_path, include_exact, expected_status
):
    with Session(engine) as db:
        db.add(_key("seamo_2024_a", paper_type="A", answer="D"))
        if include_exact:
            db.add(_key("seamo_2025_a", paper_type="A", answer="C"))
        db.commit()

        image = tmp_path / "sync-page.png"
        image.write_text("image")
        monkeypatch.setattr(
            "backend.services.template_service.get_template_registry",
            lambda: type("R", (), {"get": lambda _self, _id: object()})(),
        )
        monkeypatch.setattr(
            routes,
            "get_local_storage",
            lambda: type("S", (), {
                "save_pdf": lambda _self, *_args: {
                    "absolute_path": str(tmp_path / "paper.pdf")
                }
            })(),
        )
        monkeypatch.setattr(
            routes,
            "get_pdf_converter",
            lambda: type("C", (), {
                "convert_from_file": lambda _self, _path: [str(image)]
            })(),
        )
        monkeypatch.setattr(
            routes,
            "TemplateExtractor",
            lambda _template_id: type("E", (), {
                "extract_pdf": lambda _self, *_args, **_kwargs: {
                    "candidates": [
                        {
                            "candidate_number": "001",
                            "paper_type": "A",
                            "answers": {"1": "C"},
                        }
                    ]
                }
            })(),
        )

        response = asyncio.run(
            routes.extract_and_mark(
                file=UploadFile(filename="paper.pdf", file=BytesIO(b"pdf")),
                template_id="seamo_2025_a",
                mark_request=None,
                db=db,
            )
        )
        candidate = json.loads(response.body)["candidates"][0]

        marking = candidate.get("marking")
        assert (
            marking["outcomes"][0]["status"] if marking is not None else None
        ) == expected_status


def test_startup_sync_is_idempotent_on_explicit_temp_database(engine):
    checked_db = ROOT / "exam_db.sqlite"
    before_hash = hashlib.sha256(checked_db.read_bytes()).hexdigest()
    before_mtime = checked_db.stat().st_mtime_ns

    import_db = engine.url.database + ".import"
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{import_db}",
    }
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import backend.db.database as database; database.init_db()",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    importlib.import_module("backend.db.database")
    init_db(bind=engine)
    init_db(bind=engine)

    with Session(engine) as db:
        rows = db.scalars(select(AnswerKey)).all()
        assert len(rows) == 7
        assert all(row.is_active for row in rows)

    assert hashlib.sha256(checked_db.read_bytes()).hexdigest() == before_hash
    assert checked_db.stat().st_mtime_ns == before_mtime
