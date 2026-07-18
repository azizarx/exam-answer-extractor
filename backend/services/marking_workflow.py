"""Transactional orchestration for automatic and manual submission marking."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import (
    AnswerKey,
    CandidateMarking,
    CandidateResult,
    ExamSubmission,
    MarkingRun,
)
from backend.services.marking_service import (
    AnswerKeyManifest,
    ManifestQuestion,
    MarkingService,
    apply_fr_equivalence_judge,
)
from backend.services.fr_equivalence_judge import GeminiFrEquivalenceJudge


class NoCandidatesError(ValueError):
    """Raised when a submission has no extracted candidates to mark."""


MARKING_RUN_STALE_AFTER = timedelta(minutes=5)
INTERRUPTED_MARKING_MESSAGE = "Marking interrupted; retry is available"
PROCESSING_OWNERSHIP_INDEX = "uq_marking_runs_one_processing_submission"


class _LazyGeminiJudge:
    """Create the production judge only when an FR fallback is queued."""

    def __init__(self) -> None:
        self._inner: GeminiFrEquivalenceJudge | None = None

    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._inner is None:
            self._inner = GeminiFrEquivalenceJudge()
        return self._inner.judge(items)


class MarkingInProgressError(RuntimeError):
    """Raised when a fresh run already owns marking for a submission."""

    def __init__(self, run_id: int | None):
        super().__init__("Marking is already in progress")
        self.run_id = run_id


def _is_processing_ownership_violation(error: IntegrityError) -> bool:
    """Recognize only the partial unique processing-owner constraint."""
    original = error.orig
    constraint_name = getattr(
        getattr(original, "diag", None), "constraint_name", None
    )
    if constraint_name == PROCESSING_OWNERSHIP_INDEX:
        return True
    message = str(original).lower()
    if PROCESSING_OWNERSHIP_INDEX in message:
        return True
    statement = str(error.statement or "").lower()
    inserts_marking_run = re.search(
        r"insert\s+into\s+[\"`]?marking_runs[\"`]?", statement
    )
    return (
        isinstance(original, sqlite3.IntegrityError)
        and inserts_marking_run is not None
        and "unique constraint failed: marking_runs.submission_id" in message
    )


def recover_stale_marking_runs(
    db: Session,
    *,
    submission_id: int | None = None,
    now: datetime | None = None,
) -> list[MarkingRun]:
    """Fail abandoned processing runs without changing candidates or history."""
    recovered_at = now or datetime.utcnow()
    cutoff = recovered_at - MARKING_RUN_STALE_AFTER
    query = db.query(MarkingRun).filter(
        MarkingRun.status == "processing",
        or_(
            MarkingRun.started_at < cutoff,
            and_(
                MarkingRun.started_at.is_(None),
                MarkingRun.created_at < cutoff,
            ),
        ),
    )
    if submission_id is not None:
        query = query.filter(MarkingRun.submission_id == submission_id)
    stale = query.order_by(MarkingRun.id).all()
    if not stale:
        return []
    for run in stale:
        run.status = "failed"
        run.completed_at = recovered_at
        run.error_message = INTERRUPTED_MARKING_MESSAGE
    db.commit()
    return stale


def mark_submission_answers(
    db: Session,
    submission_id: int,
    *,
    answer_key_id: int | None = None,
    fr_judge: Any | None = None,
) -> MarkingRun:
    """Create one auditable marking attempt for persisted raw candidates.

    The caller must commit raw extraction before invoking this function.
    This workflow commits the run's initial state first, then confines marks
    and terminal completion to a separate transaction. A marking failure can
    therefore roll back only marking output and still record a failed run.
    Calling the function again is the manual re-mark workflow: it appends a
    new run and never updates prior marks or CandidateResult.answers.
    """
    submission = db.get(ExamSubmission, submission_id)
    if submission is None:
        raise ValueError(f"Submission {submission_id} not found")

    recover_stale_marking_runs(db, submission_id=submission_id)
    active = (
        db.query(MarkingRun)
        .filter(
            MarkingRun.submission_id == submission_id,
            MarkingRun.status == "processing",
        )
        .order_by(MarkingRun.id.desc())
        .first()
    )
    if active is not None:
        raise MarkingInProgressError(active.id)

    template_id = submission.template_id
    now = datetime.utcnow()
    run = MarkingRun(
        submission_id=submission_id,
        status="processing",
        started_at=now,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        ownership_conflict = _is_processing_ownership_violation(exc)
        db.rollback()
        if not ownership_conflict:
            raise
        owner = (
            db.query(MarkingRun)
            .filter(MarkingRun.submission_id == submission_id)
            .order_by(MarkingRun.id.desc())
            .first()
        )
        raise MarkingInProgressError(owner.id if owner is not None else None) from exc
    run_id = run.id

    try:
        candidates = (
            db.query(CandidateResult)
            .filter(CandidateResult.submission_id == submission_id)
            .order_by(CandidateResult.id)
            .all()
        )
        if not candidates:
            raise NoCandidatesError(
                f"Submission {submission_id} has no candidate results"
            )

        key_query = db.query(AnswerKey).filter(AnswerKey.template_id == template_id)
        if answer_key_id is None:
            key_query = key_query.filter(AnswerKey.is_active.is_(True))
        else:
            key_query = key_query.filter(AnswerKey.id == answer_key_id)
        key = key_query.one_or_none()
        if key is None:
            run.status = "unavailable"
            run.error_message = (
                f"No active answer key for template {template_id}"
            )
            run.completed_at = datetime.utcnow()
            db.commit()
            db.refresh(run)
            return run

        run.answer_key_id = key.id
        run.key_provenance = _key_provenance(key)
        db.commit()

        manifest = manifest_from_key(key)
        marker = MarkingService(manifest)
        judge = fr_judge if fr_judge is not None else _LazyGeminiJudge()
        with db.begin_nested():
            for candidate in candidates:
                result = marker.mark(candidate.answers or {})
                result = apply_fr_equivalence_judge(result, manifest, judge)
                db.add(
                    CandidateMarking(
                        marking_run_id=run_id,
                        candidate_result_id=candidate.id,
                        awarded_marks=result.awarded_marks,
                        max_marks=result.max_marks,
                        percentage=result.percentage,
                        outcomes=[asdict(outcome) for outcome in result.outcomes],
                    )
                )
            run = db.get(MarkingRun, run_id)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            run.error_message = None
            db.flush()
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(MarkingRun, run_id)
        run.status = "failed"
        run.completed_at = datetime.utcnow()
        run.error_message = f"Marking failed ({type(exc).__name__})"
        db.commit()

    db.refresh(run)
    return run


def record_failed_marking_attempt(
    db: Session,
    submission_id: int,
    error: Exception,
    *,
    after_run_id: int | None = None,
    force_new: bool = False,
) -> MarkingRun:
    """Reuse a terminal attempt, fail processing, or append if none exists."""
    run = None
    if not force_new:
        query = (
            db.query(MarkingRun)
            .filter(MarkingRun.submission_id == submission_id)
            .order_by(MarkingRun.id.desc())
        )
        if after_run_id is not None:
            query = query.filter(MarkingRun.id > after_run_id)
        run = query.first()
    if run is not None and run.status in {"completed", "unavailable", "failed"}:
        return run
    if run is None:
        run = MarkingRun(submission_id=submission_id, status="failed")
        db.add(run)
    run.status = "failed"
    run.completed_at = datetime.utcnow()
    run.error_message = f"Marking failed ({type(error).__name__})"
    db.commit()
    db.refresh(run)
    return run


def manifest_from_key(key: AnswerKey) -> AnswerKeyManifest:
    questions = tuple(
        ManifestQuestion(
            number=item["number"],
            type=item["type"],
            accepted_answers=tuple(item["accepted_answers"]),
            marks=item["marks"],
            normalizer=item["normalizer"],
        )
        for item in (key.question_spec or [])
    )
    if not questions or not key.total_marks:
        raise ValueError("Answer key has no weighted question specification")
    return AnswerKeyManifest(
        template_id=key.template_id,
        version=key.version,
        source_filename=key.source_filename or "",
        source_sha256=key.source_sha256 or "",
        total_marks=key.total_marks,
        questions=questions,
    )


def _key_provenance(key: AnswerKey) -> dict[str, Any]:
    return {
        "answer_key_id": key.id,
        "template_id": key.template_id,
        "version": key.version,
        "source_filename": key.source_filename,
        "source_sha256": key.source_sha256,
        "total_marks": key.total_marks,
        "question_spec": json.loads(json.dumps(key.question_spec)),
    }
