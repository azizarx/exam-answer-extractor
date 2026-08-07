"""Public marking APIs with answer-key-safe response contracts."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.api.schemas import (
    AnswerKeyMetadataSchema,
    AnswerKeyProvenanceSchema,
    CandidateWeightedMarkingSchema,
    ConfirmReviewRequest,
    ConfirmReviewResponse,
    ManualRemarkRequest,
    ManualRemarkResponse,
    MarkedCandidateExportSchema,
    MarkedExportSchema,
    MarkedExportSubmissionSchema,
    MarkingRunDetailSchema,
    MarkingRunMetadataSchema,
    QuestionOutcomeSchema,
    SubmissionMarkingDetailSchema,
)
from backend.db.database import get_db
from backend.db.models import (
    AnswerKey,
    CandidateMarking,
    CandidateResult,
    ExamSubmission,
    MarkingRun,
)
from backend.services.marking_workflow import (
    MarkingInProgressError,
    mark_submission_answers,
    recover_stale_marking_runs,
)


router = APIRouter()

NO_CANDIDATES_DETAIL = "No candidate results available for marking"
_PROVENANCE_FIELDS = (
    "answer_key_id",
    "template_id",
    "version",
    "source_filename",
    "source_sha256",
    "total_marks",
)


def _clean_string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item) if item is not None else ""
        for key, item in value.items()
    }


def _marked_export_disposition(uploaded_filename: str) -> str:
    """Build an injection-safe download header with UTF-8 filename support."""
    leaf = str(uploaded_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf = "".join(
        character
        for character in leaf
        if ord(character) >= 32 and ord(character) != 127
    )
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    stem = stem.strip(" .") or "download"
    utf8_name = f"{stem}.marked.json"

    ascii_stem = (
        unicodedata.normalize("NFKD", stem)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_stem)
    ascii_stem = ascii_stem.strip("._-") or "download"
    ascii_name = f"{ascii_stem}.marked.json"
    encoded_name = quote(utf8_name, safe=".")
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded_name}"
    )


def _sanitize_provenance(value: Any) -> Optional[AnswerKeyProvenanceSchema]:
    if not isinstance(value, dict):
        return None
    # Multi-key runs store {"keys": [...], "missing_templates": [...]};
    # surface the first key for the existing single-provenance schema.
    source = value
    keys = value.get("keys")
    if isinstance(keys, list) and keys and isinstance(keys[0], dict):
        source = keys[0]
    safe = {field: source.get(field) for field in _PROVENANCE_FIELDS}
    return AnswerKeyProvenanceSchema(**safe)


def _sanitize_error(run: MarkingRun) -> Optional[str]:
    if run.status == "failed":
        return "Marking failed"
    if run.status == "unavailable":
        return "No active answer key is available for this submission"
    # Completed runs may still note missing keys for some templates.
    if run.status == "completed" and run.error_message:
        return run.error_message
    return None


def _run_metadata(run: MarkingRun) -> MarkingRunMetadataSchema:
    return MarkingRunMetadataSchema(
        id=run.id,
        status=run.status,
        answer_key_id=run.answer_key_id,
        provenance=_sanitize_provenance(run.key_provenance),
        error_message=_sanitize_error(run),
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        updated_at=run.updated_at,
    )


def _question_outcomes(value: Any) -> list[QuestionOutcomeSchema]:
    if not isinstance(value, list):
        return []
    return [QuestionOutcomeSchema.model_validate(item) for item in value]


def _candidate_marking(
    row: CandidateMarking,
    candidate_number: Optional[str] = "",
) -> CandidateWeightedMarkingSchema:
    return CandidateWeightedMarkingSchema(
        candidate_result_id=row.candidate_result_id,
        candidate_number=candidate_number or "",
        awarded_marks=row.awarded_marks,
        max_marks=row.max_marks,
        percentage=row.percentage,
        outcomes=_question_outcomes(row.outcomes),
    )


def load_latest_marking(
    db: Session, submission_id: int
) -> tuple[Optional[MarkingRun], dict[int, CandidateMarking]]:
    """Load the latest run and all its candidate marks in at most two queries."""
    recover_stale_marking_runs(db, submission_id=submission_id)
    run = (
        db.query(MarkingRun)
        .filter(MarkingRun.submission_id == submission_id)
        .order_by(MarkingRun.id.desc())
        .first()
    )
    if run is None:
        return None, {}
    markings = (
        db.query(CandidateMarking)
        .filter(CandidateMarking.marking_run_id == run.id)
        .all()
    )
    return run, {row.candidate_result_id: row for row in markings}


def marking_metadata(run: Optional[MarkingRun]) -> Optional[MarkingRunMetadataSchema]:
    return _run_metadata(run) if run is not None else None


def candidate_marking_schema(
    row: Optional[CandidateMarking], candidate_number: Optional[str] = ""
) -> Optional[CandidateWeightedMarkingSchema]:
    return _candidate_marking(row, candidate_number) if row is not None else None


def _run_detail(
    run: MarkingRun,
    markings: dict[int, CandidateMarking],
    candidate_numbers: dict[int, Optional[str]],
) -> MarkingRunDetailSchema:
    metadata = _run_metadata(run)
    candidates = [
        _candidate_marking(row, candidate_numbers.get(row.candidate_result_id, ""))
        for row in sorted(markings.values(), key=lambda item: item.candidate_result_id)
    ]
    return MarkingRunDetailSchema(
        **metadata.model_dump(),
        candidates=candidates,
    )


@router.get("/answer-keys", response_model=list[AnswerKeyMetadataSchema])
async def list_answer_keys(db: Session = Depends(get_db)):
    return db.query(AnswerKey).order_by(AnswerKey.created_at.desc()).all()


@router.get("/answer-keys/{key_id}", response_model=AnswerKeyMetadataSchema)
async def get_answer_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(AnswerKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Answer key not found")
    return key


@router.get(
    "/submission/{submission_id}/marking",
    response_model=SubmissionMarkingDetailSchema,
)
async def get_submission_marking(
    submission_id: int, db: Session = Depends(get_db)
):
    submission = db.get(ExamSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    recover_stale_marking_runs(db, submission_id=submission_id)
    runs = (
        db.query(MarkingRun)
        .filter(MarkingRun.submission_id == submission_id)
        .order_by(MarkingRun.id.desc())
        .all()
    )
    if not runs:
        return SubmissionMarkingDetailSchema(
            submission_id=submission_id,
            latest_run=None,
            history=[],
        )

    latest = runs[0]
    markings = (
        db.query(CandidateMarking)
        .filter(CandidateMarking.marking_run_id == latest.id)
        .all()
    )
    candidate_ids = [row.candidate_result_id for row in markings]
    numbers = {}
    if candidate_ids:
        numbers = dict(
            db.query(CandidateResult.id, CandidateResult.candidate_number)
            .filter(CandidateResult.id.in_(candidate_ids))
            .all()
        )
    by_candidate = {row.candidate_result_id: row for row in markings}
    return SubmissionMarkingDetailSchema(
        submission_id=submission_id,
        latest_run=_run_detail(latest, by_candidate, numbers),
        history=[_run_metadata(run) for run in runs],
    )


@router.post(
    "/submission/{submission_id}/candidates/{candidate_id}/confirm-review",
    response_model=ConfirmReviewResponse,
)
async def confirm_candidate_review(
    submission_id: int,
    candidate_id: int,
    body: ConfirmReviewRequest,
    db: Session = Depends(get_db),
):
    """Apply human-confirmed answers and clear needs_review flags for those Qs.

    Does not re-mark automatically — call POST .../mark after confirming.
    """
    submission = db.get(ExamSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    candidate = db.get(CandidateResult, candidate_id)
    if candidate is None or candidate.submission_id != submission_id:
        raise HTTPException(status_code=404, detail="Candidate not found")

    answers = dict(candidate.answers or {})
    for q, value in (body.answers or {}).items():
        answers[str(q)] = "" if value is None else str(value)
    candidate.answers = answers

    extra = dict(candidate.extra_fields or {})
    review = {str(q) for q in (extra.get("needs_review_questions") or [])}
    trust = dict(extra.get("answer_trust") or {})
    if body.clear_all_review:
        confirmed = set(review)
    else:
        confirmed = {str(q) for q in (body.answers or {}).keys()}
    review -= confirmed
    for q in confirmed:
        trust[str(q)] = "trusted"
    for q in review:
        trust[str(q)] = "needs_review"
    extra["needs_review_questions"] = sorted(
        review, key=lambda x: int(x) if str(x).isdigit() else str(x)
    )
    extra["answer_trust"] = trust
    candidate.extra_fields = extra
    db.commit()
    db.refresh(candidate)
    return ConfirmReviewResponse(
        candidate_result_id=candidate.id,
        needs_review_questions=list(extra.get("needs_review_questions") or []),
        answers=_clean_string_map(candidate.answers),
    )


@router.post(
    "/submission/{submission_id}/mark",
    response_model=ManualRemarkResponse,
)
async def mark_submission(
    submission_id: int,
    body: Optional[ManualRemarkRequest] = None,
    db: Session = Depends(get_db),
):
    submission = db.get(ExamSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Submission is {submission.status}, not completed",
        )

    selected_key_id = body.answer_key_id if body else None
    if selected_key_id is not None:
        key = db.get(AnswerKey, selected_key_id)
        if key is None:
            raise HTTPException(status_code=404, detail="Answer key not found")
        # Key may target one layout within a mixed submission; do not require
        # equality with submission.template_id (null in auto mode).

    try:
        run = mark_submission_answers(
            db,
            submission_id,
            answer_key_id=selected_key_id,
        )
    except MarkingInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail="Marking is already in progress",
        ) from exc
    markings = (
        db.query(CandidateMarking)
        .filter(CandidateMarking.marking_run_id == run.id)
        .all()
    )
    has_candidates = (
        db.query(CandidateResult.id)
        .filter(CandidateResult.submission_id == submission_id)
        .first()
        is not None
    )
    if not has_candidates:
        raise HTTPException(status_code=409, detail=NO_CANDIDATES_DETAIL)
    candidate_ids = [row.candidate_result_id for row in markings]
    numbers = {}
    if candidate_ids:
        numbers = dict(
            db.query(CandidateResult.id, CandidateResult.candidate_number)
            .filter(CandidateResult.id.in_(candidate_ids))
            .all()
        )
    by_candidate = {row.candidate_result_id: row for row in markings}
    detail = _run_detail(run, by_candidate, numbers)
    return ManualRemarkResponse(
        submission_id=submission_id,
        total_candidates_marked=len(markings),
        run=detail,
    )


@router.get(
    "/submission/{submission_id}/marked-json",
    response_model=MarkedExportSchema,
)
async def get_marked_json(
    submission_id: int, db: Session = Depends(get_db)
):
    submission = db.get(ExamSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    candidates = (
        db.query(CandidateResult)
        .filter(CandidateResult.submission_id == submission_id)
        .order_by(CandidateResult.page_number, CandidateResult.id)
        .all()
    )
    if not candidates:
        raise HTTPException(status_code=409, detail=NO_CANDIDATES_DETAIL)

    run, marking_by_candidate = load_latest_marking(db, submission_id)
    if run is None or run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="A completed marking run is required for marked JSON export",
        )

    exported = []
    for candidate in candidates:
        marking = marking_by_candidate.get(candidate.id)
        if marking is None:
            raise HTTPException(
                status_code=409,
                detail="Latest marking run does not cover every candidate",
            )
        exported.append(
            MarkedCandidateExportSchema(
                id=candidate.id,
                page_number=candidate.page_number,
                candidate_name=candidate.candidate_name or "",
                candidate_number=candidate.candidate_number or "",
                country=candidate.country or "",
                paper_type=candidate.paper_type or "",
                extra_fields=_clean_string_map(candidate.extra_fields) or None,
                answers=_clean_string_map(candidate.answers),
                drawing_questions=_clean_string_map(candidate.drawing_questions) or None,
                marking=_candidate_marking(
                    marking, candidate.candidate_number or ""
                ),
            )
        )

    payload = MarkedExportSchema(
        submission=MarkedExportSubmissionSchema(
            id=submission.id,
            filename=submission.filename,
            template_id=submission.template_id,
            status=submission.status,
            pages_count=submission.pages_count or 0,
            created_at=submission.created_at,
            processed_at=submission.processed_at,
        ),
        marking=_run_metadata(run),
        candidates=exported,
    )
    return JSONResponse(
        content=jsonable_encoder(payload),
        headers={
            "Content-Disposition": _marked_export_disposition(
                submission.filename
            )
        },
        media_type="application/json",
    )
