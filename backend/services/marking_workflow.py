"""Transactional orchestration for automatic and manual submission marking."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from concurrent.futures import as_completed
from backend.services.cancellation import CancellationExecutor as ThreadPoolExecutor, check_cancelled, ExtractionCancelled
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Sequence

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
    apply_diagram_vision_judge,
    apply_extraction_trust,
    apply_fr_equivalence_judge,
)
from backend.services.diagram_vision_judge import GeminiDiagramVisionJudge
from backend.services.fr_equivalence_judge import GeminiFrEquivalenceJudge
from backend.services.local_storage import get_local_storage

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
        self._lock = threading.Lock()

    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._inner is None:
            with self._lock:
                if self._inner is None:
                    self._inner = GeminiFrEquivalenceJudge()
        return self._inner.judge(items)


class _LazyDiagramVisionJudge:
    """Create the production vision judge only when a diagram is queued."""

    def __init__(self) -> None:
        self._inner: GeminiDiagramVisionJudge | None = None
        self._lock = threading.Lock()

    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._inner is None:
            with self._lock:
                if self._inner is None:
                    self._inner = GeminiDiagramVisionJudge()
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
        from backend.db.models import ProcessingJob
        if db.query(ProcessingJob.id).filter(
            ProcessingJob.submission_id == run.submission_id,
            ProcessingJob.state == 'running',
            ProcessingJob.lease_until > recovered_at,
            ProcessingJob.kind.in_(['process', 'mark', 'review_mark']),
        ).first():
            continue
        run.status = "failed"
        run.completed_at = recovered_at
        run.error_message = INTERRUPTED_MARKING_MESSAGE
    db.commit()
    return stale


def _latest_run_markings(
    db: Session, submission_id: int
) -> dict[int, CandidateMarking]:
    """Per-candidate marks from the newest run, keyed by candidate id."""
    run = (
        db.query(MarkingRun)
        .filter(MarkingRun.submission_id == submission_id)
        .order_by(MarkingRun.id.desc())
        .first()
    )
    if run is None:
        return {}
    rows = (
        db.query(CandidateMarking)
        .filter(CandidateMarking.marking_run_id == run.id)
        .all()
    )
    return {row.candidate_result_id: row for row in rows}


def mark_submission_answers(
    db: Session,
    submission_id: int,
    *,
    answer_key_id: int | None = None,
    fr_judge: Any | None = None,
    diagram_judge: Any | None = None,
    only_candidate_ids: Sequence[int] | None = None,
) -> MarkingRun:
    """Create one auditable marking attempt for persisted raw candidates.

    The caller must commit raw extraction before invoking this function.
    This workflow commits the run's initial state first, then confines marks
    and terminal completion to a separate transaction. A marking failure can
    therefore roll back only marking output and still record a failed run.
    Calling the function again is the manual re-mark workflow: it appends a
    new run and never updates prior marks or CandidateResult.answers.

    ``only_candidate_ids`` re-marks just those candidates — used after a human
    edits one. The run stays *complete*: every other candidate's marks are
    carried forward from the previous run, because reads resolve a submission's
    marks from the newest run alone and a partial run would blank the rest.
    It is ignored when there is no previous run to carry forward from.
    """
    from backend.queue.runtime import current_job, checkpoint_get
    committed = checkpoint_get('committed-mark-run')
    if committed:
        previous_run = db.get(MarkingRun, committed['run_id'])
        if previous_run and previous_run.submission_id == submission_id and previous_run.status in {'completed','unavailable'}:
            return previous_run
    submission = db.get(ExamSubmission, submission_id)
    if submission is None:
        raise ValueError(f"Submission {submission_id} not found")

    recover_stale_marking_runs(db, submission_id=submission_id)

    target_ids: set[int] | None = None
    carry_forward: dict[int, dict[str, Any]] = {}
    if only_candidate_ids:
        requested = {int(cid) for cid in only_candidate_ids}
        previous = _latest_run_markings(db, submission_id)
        if previous:
            target_ids = requested
            carry_forward = {
                cid: {
                    "awarded_marks": row.awarded_marks,
                    "max_marks": row.max_marks,
                    "percentage": row.percentage,
                    "outcomes": row.outcomes,
                    "answer_key_id": row.answer_key_id,
                }
                for cid, row in previous.items()
                if cid not in requested
            }
        else:
            logger.info(
                "MARK submission=%s scoped re-mark requested with no previous "
                "run; marking every candidate instead", submission_id,
            )
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
        if target_ids is not None:
            candidates = [c for c in candidates if c.id in target_ids]
            if not candidates:
                raise NoCandidatesError(
                    f"Submission {submission_id} has no candidate matching "
                    f"{sorted(target_ids)}"
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

        key_template_ids: dict[str, str] = {}
        for tid in groups:
            if tid is None:
                missing_templates.append("(undetected)")
                continue
            key_template_ids[tid] = _key_tid_for_layout(tid)

        active_key_tids = {
            key_tid
            for key_tid in key_template_ids.values()
            if override_key is None or override_key.template_id != key_tid
        }
        active_keys: dict[str, AnswerKey] = {}
        if active_key_tids:
            for key in (
                db.query(AnswerKey)
                .filter(
                    AnswerKey.template_id.in_(active_key_tids),
                    AnswerKey.is_active.is_(True),
                )
                .all()
            ):
                if key.template_id in active_keys:
                    raise ValueError(
                        f"Multiple active answer keys for template {key.template_id}"
                    )
                active_keys[key.template_id] = key

        for tid, key_tid in key_template_ids.items():
            if override_key is not None and override_key.template_id == key_tid:
                keys_by_template[tid] = override_key
                continue
            key = active_keys.get(key_tid)
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

        # Snapshot all ORM-backed inputs before committing provenance. SQLAlchemy
        # expires ORM rows on commit; accessing candidates or keys afterwards
        # otherwise causes one refresh SELECT per candidate/key.
        prepared_groups = []
        for tid, group in groups.items():
            key = keys_by_template.get(tid) if tid else None
            if key is None:
                continue
            manifest = manifest_from_key(key)
            marker = MarkingService(manifest)
            jobs = []
            for candidate in group:
                extra = candidate.extra_fields or {}
                review_qs = list(extra.get("needs_review_questions") or [])
                crops = extra.get("diagram_crops")
                jobs.append(
                    (
                        candidate.id,
                        dict(candidate.answers or {}),
                        key.id,
                        review_qs,
                        dict(crops) if isinstance(crops, dict) else {},
                        list(extra.get("diagram_cv_questions") or []),
                    )
                )
            prepared_groups.append((tid, marker, manifest, jobs))

        db.commit()

        settings = get_settings()
        judge = fr_judge if fr_judge is not None else _LazyGeminiJudge()
        fr_workers = max(1, int(getattr(settings, "max_fr_judge_workers", 6) or 6))
        diagram_vision_enabled = bool(
            getattr(settings, "diagram_vision_enabled", True)
        )
        vision_judge = (
            diagram_judge
            if diagram_judge is not None
            else (_LazyDiagramVisionJudge() if diagram_vision_enabled else None)
        )
        crop_dir = get_local_storage().diagram_crop_dir(submission_id)
        from backend.db.models import StorageArtifact
        from backend.services.artifact_storage import ensure_local
        for stored in db.query(StorageArtifact).filter_by(submission_id=submission_id, kind='diagram', state='verified'):
            ensure_local(stored.local_path)

        def _mark_one(
            candidate_id: int,
            answers: dict,
            marker: MarkingService,
            manifest: AnswerKeyManifest,
            answer_key_id: int,
            needs_review_questions: list,
            diagram_crops: dict,
            cv_owned_questions: list,
        ):
            # Pass plain dicts only — ORM CandidateResult is not thread-safe
            # and lazy-loading from worker threads raises ObjectDeletedError.
            check_cancelled()
            import hashlib
            from backend.queue.runtime import checkpoint_get, checkpoint_put
            from backend.services.marking_service import CandidateMarkingResult, QuestionOutcome
            cache_key = hashlib.sha256(json.dumps([candidate_id, answers, answer_key_id,
                manifest.source_sha256, needs_review_questions, diagram_crops, cv_owned_questions], sort_keys=True).encode()).hexdigest()
            cached = checkpoint_get(cache_key)
            if cached is not None:
                return candidate_id, answer_key_id, CandidateMarkingResult(
                    outcomes=tuple(QuestionOutcome(**o) for o in cached['outcomes']),
                    awarded_marks=cached['awarded_marks'], max_marks=cached['max_marks'], percentage=cached['percentage'])
            result = marker.mark(answers or {})
            result = apply_extraction_trust(result, needs_review_questions)
            result = apply_fr_equivalence_judge(
                result, manifest, judge,
                # With the vision judge off, diagrams fall back to the text
                # judge rather than to exact string equality.
                include_diagram=vision_judge is None,
            )
            if vision_judge is not None:
                result = apply_diagram_vision_judge(
                    result,
                    manifest,
                    vision_judge,
                    diagram_crops=diagram_crops,
                    crop_dir=crop_dir,
                    cv_owned_questions=cv_owned_questions,
                )
            check_cancelled()
            checkpoint_put(cache_key, asdict(result))
            return candidate_id, answer_key_id, result

        with db.begin_nested():
            for tid, marker, manifest, jobs in prepared_groups:
                workers = min(fr_workers, max(1, len(jobs)))
                if workers == 1 or len(jobs) <= 1:
                    marked = [
                        _mark_one(
                            cid, answers, marker, manifest, key_id, review,
                            crops, cv_qs,
                        )
                        for cid, answers, key_id, review, crops, cv_qs in jobs
                    ]
                else:
                    marked = []
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futs = [
                            pool.submit(
                                _mark_one,
                                cid,
                                answers,
                                marker,
                                manifest,
                                key_id,
                                review,
                                crops,
                                cv_qs,
                            )
                            for cid, answers, key_id, review, crops, cv_qs in jobs
                        ]
                        for fut in as_completed(futs):
                            marked.append(fut.result())
                logger.info(
                    "MARK template=%s candidates=%d fr_workers=%d",
                    tid, len(jobs), workers,
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
            for candidate_id, snapshot in carry_forward.items():
                db.add(
                    CandidateMarking(
                        marking_run_id=run_id,
                        candidate_result_id=candidate_id,
                        **snapshot,
                    )
                )
            if carry_forward:
                logger.info(
                    "MARK submission=%s scoped re-mark: %d marked, %d carried forward",
                    submission_id, len(candidates), len(carry_forward),
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
            from backend.queue.runtime import assert_owned, current_job
            assert_owned(db, current_job.get())
            if current_job.get() is not None:
                from backend.db.models import JobCheckpoint
                db.add(JobCheckpoint(job_id=current_job.get().id,key='committed-mark-run',value={'run_id':run_id}))
            db.flush()
        db.commit()
    except ExtractionCancelled:
        db.rollback()
        run = db.get(MarkingRun, run_id)
        run.status = "failed"
        run.completed_at = datetime.utcnow()
        run.error_message = "Marking cancelled by user"
        db.commit()
        raise
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
