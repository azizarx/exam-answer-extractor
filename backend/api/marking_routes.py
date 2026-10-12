"""Public marking APIs with answer-key-safe response contracts."""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from itertools import islice
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.schemas import (
    AnswerKeyMetadataSchema,
    AnswerKeyProvenanceSchema,
    CandidateNumberCorrectionRequest,
    CandidateNumberCorrectionResponse,
    CandidateNumberCorrectionResult,
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
from backend.services.diagram_vision_judge import reference_diagram_path
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


logger = logging.getLogger(__name__)

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


def _clean_extra_fields(value: Any) -> dict[str, Any]:
    """Preserve nested trust metadata; stringify only scalar leftovers."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or item == {}:
            continue
        if isinstance(item, (dict, list, bool, int, float)):
            out[str(key)] = item
        else:
            out[str(key)] = str(item)
    return out


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
    """Apply a human correction to a candidate and clear its review flags.

    Handles both the review queue (answers flagged during extraction) and a
    free-form edit of any answer or identity field. Every change is appended to
    ``extra_fields.manual_edits`` with the value the extractor originally
    produced, so a mark can still be defended against what the scan said.

    Re-marking is opt-in via ``remark`` so existing callers keep the old
    behaviour of leaving scores untouched.
    """
    def reject(status_code: int, detail: Any):
        # This route runs on the event loop: a lock held until the session
        # closes would stall every other edit waiting on it.
        db.rollback()
        raise HTTPException(status_code=status_code, detail=detail)

    # The lock number corrections take, in every mode, so neither edit can
    # drop the other's manual_edits entry.
    submission = _lock_submission_for_edit(db, submission_id)
    if submission is None:
        reject(404, "Submission not found")
    candidate = db.get(CandidateResult, candidate_id)
    if candidate is None or candidate.submission_id != submission_id:
        reject(404, "Candidate not found")

    from backend.config import get_settings
    if get_settings().queue_enabled:
        from backend.db.models import ProcessingJob
        if submission.status != 'completed' or db.query(ProcessingJob.id).filter(
            ProcessingJob.submission_id==submission_id,
            ProcessingJob.kind.in_(['mark','review_mark']),
            ProcessingJob.state.in_(['pending','running']),
        ).first():
            reject(409, 'Wait for extraction and marking to finish before editing')

    edited_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    edits: list[dict[str, Any]] = []

    answers = dict(candidate.answers or {})
    for q, value in (body.answers or {}).items():
        new_value = "" if value is None else str(value)
        previous = answers.get(str(q))
        if previous != new_value:
            edits.append({
                "target": f"answers.{q}",
                "from": previous,
                "to": new_value,
                "at": edited_at,
                "by": body.edited_by,
            })
        answers[str(q)] = new_value
    candidate.answers = answers

    for field in ("candidate_name", "candidate_number", "country", "paper_type"):
        requested = getattr(body, field)
        if requested is None:
            continue  # omitted means "leave unchanged", not "clear"
        new_value = str(requested)
        previous = getattr(candidate, field) or ""
        if previous != new_value:
            edits.append({
                "target": field,
                "from": previous,
                "to": new_value,
                "at": edited_at,
                "by": body.edited_by,
            })
            setattr(candidate, field, new_value)

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
    if edits:
        history = list(extra.get("manual_edits") or [])
        history.extend(edits)
        extra["manual_edits"] = history
    candidate.extra_fields = extra
    queued_remark = None
    from backend.queue.runtime import current_job, enqueue
    if body.remark and get_settings().queue_enabled and current_job.get() is None:
        queued_remark = enqueue(db, 'review_mark', submission_id, {'candidate_ids':[candidate.id]})
    db.commit()
    db.refresh(candidate)

    remarked = False
    marking_run_id: Optional[int] = None
    remark_error: Optional[str] = None
    remark_job_id: Optional[int] = None
    if body.remark:
        try:
            from backend.config import get_settings
            from backend.queue.runtime import current_job, enqueue, wait_job
            if get_settings().queue_enabled and current_job.get() is None:
                remark_job_id = queued_remark.id
                db.rollback()
                result = await wait_job(remark_job_id)
                remarked, marking_run_id, remark_error = result['remarked'], result['marking_run_id'], result['remark_error']
            else:
                run = mark_submission_answers(db, submission_id, only_candidate_ids=[candidate.id])
                remarked = run.status == "completed"
                marking_run_id = run.id
                if not remarked:
                    remark_error = _sanitize_error(run) or run.status
        except MarkingInProgressError:
            remark_error = "another marking run is already in progress"
        except HTTPException as exc:
            remark_error = str(exc.detail)
        except Exception as exc:
            # The edit itself is committed and must not be reported as failed
            # just because re-marking could not run.
            logger.exception("Re-mark after edit failed for candidate %s", candidate.id)
            remark_error = f"{type(exc).__name__}"
        db.refresh(candidate)

    return ConfirmReviewResponse(
        candidate_result_id=candidate.id,
        needs_review_questions=list(extra.get("needs_review_questions") or []),
        answers=_clean_string_map(candidate.answers),
        candidate_name=candidate.candidate_name or "",
        candidate_number=candidate.candidate_number or "",
        country=candidate.country or "",
        paper_type=candidate.paper_type or "",
        edits_recorded=len(edits),
        remarked=remarked,
        marking_run_id=marking_run_id,
        remark_error=remark_error,
        remark_job_id=remark_job_id,
    )


_MATCH_LIMIT = 100
_LOAD_CHUNK = 500


def _number(value: Any) -> str:
    """A candidate number as clients compare it: a string, surrounding space ignored."""
    return "" if value is None else str(value).strip()


def _lock_submission_for_edit(
    db: Session, submission_id: int
) -> Optional[ExamSubmission]:
    """Load the submission holding the write lock until commit.

    Every read a candidate edit makes must see one state: two corrections could
    otherwise each pass the duplicate check and then collide, and a concurrent
    edit could drop the other's ``manual_edits`` entry. SQLite serializes
    writers with BEGIN IMMEDIATE; other databases lock the submission row.
    """
    db.rollback()
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
        return db.get(ExamSubmission, submission_id)
    return (
        db.query(ExamSubmission)
        .filter(ExamSubmission.id == submission_id)
        .with_for_update()
        .one_or_none()
    )


def _paper_key(template_id: Optional[str]) -> Optional[tuple]:
    """Which paper a layout belongs to, or None when the layout is unknown.

    One student can sit two papers in the same PDF, so a shared number is only
    suspicious within a paper. Layout variants of a paper share brand, year and
    paper.
    """
    if not template_id:
        return None
    from backend.services.template_service import get_template_registry

    template = get_template_registry().get(template_id)
    if template is not None and template.brand and template.year and template.paper:
        return ("paper", template.brand, template.year, template.paper)
    return ("template", template_id)


def _candidate_ref(row: Any, template_id: Optional[str], **extra: Any) -> dict[str, Any]:
    return {
        "candidate_id": row.id,
        "page_number": row.page_number,
        "template_id": template_id,
        **extra,
    }


@router.post(
    "/submission/{submission_id}/candidate-number-corrections",
    response_model=CandidateNumberCorrectionResponse,
)
def correct_candidate_numbers(
    submission_id: int,
    body: CandidateNumberCorrectionRequest,
    db: Session = Depends(get_db),
):
    """Correct misread candidate numbers, finding each candidate by the number
    the results currently show.

    Built for an integrator whose import rejected an unknown number: send the
    number from the export and the right one. The batch is all-or-nothing, so
    two numbers can be swapped and a rejected batch changes nothing. A number is
    never resolved by guessing: when several candidates match, the 409 lists
    them and ``candidate_id`` or ``page_number`` selects one.

    Only the database changes. Every result read (/submission, /marking,
    /marked-json, /candidate-page) joins the number in at read time, and marks
    are keyed by candidate id, so nothing is re-marked. The raw extraction JSON
    and archived snapshots stay as recorded, and ``extra_fields.manual_edits``
    keeps the number the extractor read.
    """

    def reject(status_code: int, detail: Any):
        # Release the write lock now rather than when the session closes.
        db.rollback()
        raise HTTPException(status_code=status_code, detail=detail)

    submission = _lock_submission_for_edit(db, submission_id)
    if submission is None:
        reject(404, "Submission not found")
    # Marking neither reads nor writes candidate_number, so unlike answer edits
    # this needs no wait for marking jobs. Archiving needs none either: an
    # archive never rewrites a snapshot it has already registered.
    if submission.status != "completed":
        reject(409, {
            "code": "submission_not_completed",
            "status": submission.status,
            "message": "Candidate numbers can be corrected once processing has completed",
        })

    rows = (
        db.query(
            CandidateResult.id,
            CandidateResult.page_number,
            CandidateResult.candidate_number,
            CandidateResult.template_id,
        )
        .filter(CandidateResult.submission_id == submission_id)
        .order_by(CandidateResult.page_number, CandidateResult.id)
        .all()
    )
    by_id = {row.id: row for row in rows}
    current = {row.id: _number(row.candidate_number) for row in rows}
    template_of = {row.id: row.template_id or submission.template_id for row in rows}
    by_number: dict[str, list[Any]] = {}
    by_page: dict[Optional[int], list[Any]] = {}
    for row in rows:
        by_number.setdefault(current[row.id], []).append(row)
        by_page.setdefault(row.page_number, []).append(row)
    paper_keys: dict[Optional[str], Optional[tuple]] = {}

    def paper_of(row: Any) -> Optional[tuple]:
        template_id = template_of[row.id]
        if template_id not in paper_keys:
            paper_keys[template_id] = _paper_key(template_id)
        return paper_keys[template_id]

    # Every number correction already made in this submission, by (from, to).
    # A resend is recognised by the candidate that received the correction,
    # whatever that candidate shows now.
    applied: dict[tuple[str, str], dict[int, dict]] = {}
    for row_id, edits in (
        db.query(CandidateResult.id, CandidateResult.extra_fields["manual_edits"])
        .filter(CandidateResult.submission_id == submission_id)
    ):
        for edit in edits if isinstance(edits, list) else []:
            if isinstance(edit, dict) and edit.get("target") == "candidate_number":
                key = (_number(edit.get("from")), _number(edit.get("to")))
                applied.setdefault(key, {}).setdefault(row_id, edit)

    errors: list[dict[str, Any]] = []
    # (correction, candidate row, outcome, number before this request)
    resolved: list[tuple[Any, Any, str, str]] = []
    first_index: dict[int, int] = {}

    def not_found(index: int, wrong: str, message: str) -> None:
        errors.append({
            "index": index,
            "code": "candidate_not_found",
            "message": message,
            "candidate_number": wrong,
        })

    for index, item in enumerate(body.corrections):
        wrong, right = item.candidate_number, item.corrected_candidate_number
        if item.candidate_id is not None:
            selected = by_id.get(item.candidate_id)
            if selected is None:
                not_found(index, wrong, f"candidate_id {item.candidate_id} is not in this submission")
                continue
            if item.page_number is not None and selected.page_number != item.page_number:
                not_found(index, wrong, (
                    f"candidate_id {selected.id} is on page {selected.page_number}, "
                    f"not page {item.page_number}"
                ))
                continue
            scope = [selected]
        elif item.page_number is not None:
            scope = by_page.get(item.page_number, [])
            if not scope:
                not_found(index, wrong, f"No candidate is on page {item.page_number}")
                continue
        else:
            scope = None  # the whole submission

        # A candidate matches when it shows the wrong number, or when its edit
        # history shows it already received this exact correction (a resend
        # after a lost response), whatever it shows now. Counting both means a
        # correction is never applied twice: a resent swap is ambiguous, and a
        # correction changed since is refused rather than redone.
        showing = by_number.get(wrong, []) if scope is None else [
            row for row in scope if current[row.id] == wrong
        ]
        matches = {row.id: row for row in showing}
        received: dict[int, dict] = {}
        if wrong != right:
            in_scope = None if scope is None else {row.id for row in scope}
            received = {
                row_id: edit
                for row_id, edit in applied.get((wrong, right), {}).items()
                if in_scope is None or row_id in in_scope
            }
            matches.update((row_id, by_id[row_id]) for row_id in received)

        if len(matches) > 1:
            ordered = sorted(matches.values(), key=lambda row: (row.page_number or 0, row.id))
            errors.append({
                "index": index,
                "code": "ambiguous_candidate_number",
                "message": (
                    f"{len(ordered)} candidates in this submission show this number or "
                    "already received this correction; add candidate_id or page_number"
                ),
                "candidate_number": wrong,
                "matches": [
                    _candidate_ref(row, template_of[row.id], candidate_number=row.candidate_number or "")
                    for row in ordered[:_MATCH_LIMIT]
                ],
                "truncated": len(ordered) > _MATCH_LIMIT,
            })
            continue
        if not matches and scope:
            errors.append({
                "index": index,
                "code": "candidate_number_mismatch",
                "message": "The selected candidate shows a different number; re-read the results",
                "candidate_number": wrong,
                "candidates": [
                    _candidate_ref(row, template_of[row.id], candidate_number=row.candidate_number or "")
                    for row in scope[:_MATCH_LIMIT]
                ],
            })
            continue
        if not matches:
            not_found(index, wrong, "No candidate in this submission shows this number")
            continue

        row = next(iter(matches.values()))
        if row.id in received:
            if current[row.id] != right:
                errors.append({
                    "index": index,
                    "code": "correction_superseded",
                    "message": (
                        "This candidate already received this correction and its "
                        "number has changed since; re-read the results"
                    ),
                    "candidate_number": wrong,
                    "candidates": [
                        _candidate_ref(row, template_of[row.id], candidate_number=row.candidate_number or "")
                    ],
                })
                continue
            outcome, previous = "already_applied", str(received[row.id].get("from") or "")
        else:
            previous = row.candidate_number or ""
            # Equal once trimmed still rewrites a padded stored number.
            outcome = "unchanged" if previous == right else "corrected"
        if row.id in first_index:
            errors.append({
                "index": index,
                "code": "duplicate_target",
                "message": "Another correction in this request already targets this candidate",
                "candidate_id": row.id,
                "first_index": first_index[row.id],
            })
            continue
        first_index[row.id] = index
        resolved.append((item, row, outcome, previous))

    # Check the numbers the whole batch leaves behind, so a swap passes and two
    # corrections cannot both claim one number. A page whose layout was never
    # identified may be any paper, so it conflicts with every paper.
    final = dict(current)
    checked: set[str] = set()
    for item, row, outcome, _ in resolved:
        if outcome == "corrected":
            final[row.id] = item.corrected_candidate_number
            if not item.allow_duplicate:
                checked.add(item.corrected_candidate_number)
    holders_of: dict[str, dict[Optional[tuple], list[Any]]] = {}
    for row in rows:
        if final[row.id] in checked:
            holders_of.setdefault(final[row.id], {}).setdefault(paper_of(row), []).append(row)
    for item, row, outcome, _ in resolved:
        if outcome != "corrected" or item.allow_duplicate:
            continue
        groups = holders_of[item.corrected_candidate_number]
        paper = paper_of(row)
        same_paper = (
            list(groups.values()) if paper is None
            else [groups.get(paper, []), groups.get(None, [])]
        )
        # Every group holding this row includes it once.
        others = sum(len(group) for group in same_paper) - 1
        if others:
            conflicts = islice(
                (other for group in same_paper for other in group if other.id != row.id),
                _MATCH_LIMIT,
            )
            errors.append({
                "index": first_index[row.id],
                "code": "candidate_number_in_use",
                "message": (
                    "Another candidate on the same paper in this submission would "
                    "share this number; set allow_duplicate if both sheets are "
                    "really this candidate's"
                ),
                "corrected_candidate_number": item.corrected_candidate_number,
                "conflicts": [_candidate_ref(other, template_of[other.id]) for other in conflicts],
                "truncated": others > _MATCH_LIMIT,
            })

    if errors:
        errors.sort(key=lambda error: error["index"])
        reject(409, {
            "code": "candidate_number_corrections_rejected",
            "message": "No corrections were applied; fix or remove the listed corrections and resend",
            "errors": errors,
        })

    edited_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    targets = [entry for entry in resolved if entry[2] == "corrected"]
    for start in range(0, len(targets), _LOAD_CHUNK):
        chunk = targets[start:start + _LOAD_CHUNK]
        loaded = {
            candidate.id: candidate
            for candidate in db.query(CandidateResult)
            .filter(CandidateResult.id.in_([row.id for _, row, _, _ in chunk]))
            .all()
        }
        for item, row, _, _ in chunk:
            candidate = loaded[row.id]
            extra = dict(candidate.extra_fields or {})
            # Same audit shape as confirm-review, so the history reads as one.
            extra["manual_edits"] = list(extra.get("manual_edits") or []) + [{
                "target": "candidate_number",
                "from": candidate.candidate_number or "",
                "to": item.corrected_candidate_number,
                "at": edited_at,
                "by": body.edited_by,
            }]
            candidate.extra_fields = extra
            candidate.candidate_number = item.corrected_candidate_number
    db.commit()
    if targets:
        logger.info(
            "Corrected %d candidate number(s) in submission %s", len(targets), submission_id
        )

    return CandidateNumberCorrectionResponse(
        submission_id=submission_id,
        corrected=len(targets),
        results=[
            CandidateNumberCorrectionResult(
                candidate_id=row.id,
                page_number=row.page_number,
                template_id=template_of[row.id],
                previous_candidate_number=previous,
                candidate_number=(
                    item.corrected_candidate_number
                    if outcome == "corrected"
                    else row.candidate_number or ""
                ),
                outcome=outcome,
            )
            for item, row, outcome, previous in resolved
        ],
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
    from backend.config import get_settings
    from backend.queue.runtime import current_job, wait_job
    if get_settings().queue_enabled and current_job.get() is None:
        from backend.api.queue_routes import enqueue_mark
        accepted = enqueue_mark(submission_id, body.model_dump() if body else {}, db)
        db.rollback()
        return ManualRemarkResponse(**await wait_job(accepted['job_id']))
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
                extra_fields=_clean_extra_fields(candidate.extra_fields) or None,
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


@router.get("/answer-keys/reference/{template_id}/{question}.png", tags=["Marking"])
async def get_reference_diagram(template_id: str, question: int):
    """The answer key's own drawing for a diagram question.

    Paired with the candidate's crop in the results UI so a diagram mark can be
    checked against exactly what the vision judge compared.
    """
    path = reference_diagram_path(template_id, question)
    if path is None:
        raise HTTPException(
            status_code=404, detail="No reference diagram for that question"
        )
    return FileResponse(
        str(path),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
