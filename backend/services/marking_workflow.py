"""Transactional orchestration for automatic and manual submission marking."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import get_settings
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
    apply_extraction_trust,
    apply_fr_equivalence_judge,
)
from backend.services.fr_equivalence_judge import GeminiFrEquivalenceJudge

logger = logging.getLogger(__name__)


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

        # Group by per-candidate template (auto mode). Fall back to submission
        # template_id for legacy rows that only have the submission-level id.
        groups: dict[str | None, list[CandidateResult]] = {}
        for candidate in candidates:
            tid = candidate.template_id or submission.template_id
            groups.setdefault(tid, []).append(candidate)

        # Optional forced key: only applies to the matching template group.
        override_key: AnswerKey | None = None
        if answer_key_id is not None:
            override_key = db.get(AnswerKey, answer_key_id)
            if override_key is None:
                run.status = "unavailable"
                run.error_message = f"Answer key {answer_key_id} not found"
                run.completed_at = datetime.utcnow()
                db.commit()
                db.refresh(run)
                return run

        keys_by_template: dict[str, AnswerKey] = {}
        missing_templates: list[str] = []
        from backend.services.template_service import get_template_registry

        registry = get_template_registry()

        def _key_tid_for_layout(layout_tid: str) -> str:
            tmpl = registry.get(layout_tid)
            if tmpl is not None:
                return tmpl.key_template_id
            return layout_tid

        for tid in groups:
            if tid is None:
                missing_templates.append("(undetected)")
                continue
            key_tid = _key_tid_for_layout(tid)
            if override_key is not None and override_key.template_id == key_tid:
                keys_by_template[tid] = override_key
                continue
            key = (
                db.query(AnswerKey)
                .filter(
                    AnswerKey.template_id == key_tid,
                    AnswerKey.is_active.is_(True),
                )
                .one_or_none()
            )
            if key is None:
                missing_templates.append(tid)
            else:
                keys_by_template[tid] = key

        if not keys_by_template:
            run.status = "unavailable"
            run.error_message = (
                "No active answer key for templates: "
                + ", ".join(missing_templates or list(groups.keys()))
            )
            run.completed_at = datetime.utcnow()
            db.commit()
            db.refresh(run)
            return run

        # Run-level key fields: first key for backward compat; full list in provenance.
        first_key = next(iter(keys_by_template.values()))
        run.answer_key_id = first_key.id
        run.key_provenance = {
            "keys": [
                {**_key_provenance(key), "candidate_count": len(groups.get(tid) or [])}
                for tid, key in keys_by_template.items()
            ],
            "missing_templates": missing_templates,
        }
        db.commit()

        judge = fr_judge if fr_judge is not None else _LazyGeminiJudge()
        fr_workers = max(1, int(getattr(get_settings(), "max_fr_judge_workers", 6) or 6))

        def _mark_one(
            candidate_id: int,
            answers: dict,
            marker: MarkingService,
            manifest: AnswerKeyManifest,
            answer_key_id: int,
            needs_review_questions: list,
        ):
            # Pass plain dicts only — ORM CandidateResult is not thread-safe
            # and lazy-loading from worker threads raises ObjectDeletedError.
            result = marker.mark(answers or {})
            result = apply_fr_equivalence_judge(result, manifest, judge)
            result = apply_extraction_trust(result, needs_review_questions)
            return candidate_id, answer_key_id, result

        with db.begin_nested():
            for tid, group in groups.items():
                key = keys_by_template.get(tid) if tid else None
                if key is None:
                    continue
                manifest = manifest_from_key(key)
                marker = MarkingService(manifest)
                key_id = key.id
                # Snapshot ORM fields on the main thread before any worker runs.
                jobs = []
                for c in group:
                    extra = c.extra_fields or {}
                    review_qs = list(extra.get("needs_review_questions") or [])
                    jobs.append(
                        (c.id, dict(c.answers or {}), marker, manifest, key_id, review_qs)
                    )
                workers = min(fr_workers, max(1, len(jobs)))
                if workers == 1 or len(jobs) <= 1:
                    marked = [
                        _mark_one(cid, answers, marker, manifest, kid, review)
                        for cid, answers, marker, manifest, kid, review in jobs
                    ]
                else:
                    marked = []
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futs = [
                            pool.submit(
                                _mark_one, cid, answers, marker, manifest, kid, review
                            )
                            for cid, answers, marker, manifest, kid, review in jobs
                        ]
                        for fut in as_completed(futs):
                            marked.append(fut.result())
                logger.info(
                    "MARK template=%s candidates=%d fr_workers=%d",
                    tid, len(group), workers,
                )
                for candidate_id, answer_key_id, result in marked:
                    db.add(
                        CandidateMarking(
                            marking_run_id=run_id,
                            candidate_result_id=candidate_id,
                            awarded_marks=result.awarded_marks,
                            max_marks=result.max_marks,
                            percentage=result.percentage,
                            outcomes=[asdict(outcome) for outcome in result.outcomes],
                            answer_key_id=answer_key_id,
                        )
                    )
            run = db.get(MarkingRun, run_id)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            if missing_templates:
                run.error_message = (
                    "Marked with available keys; no key for: "
                    + ", ".join(missing_templates)
                )
            else:
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
