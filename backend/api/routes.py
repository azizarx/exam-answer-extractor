"""
FastAPI routes for exam answer sheet processing
"""
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import json as _json
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pathlib import Path
import logging

from backend.db.database import get_db, SessionLocal
from backend.db.models import (
    ExamSubmission, MultipleChoiceAnswer, FreeResponseAnswer,
    ProcessingLog, CandidateResult, AnswerKey,
    Exam, ExamDocument, GeneratedJSON,
)
from backend.api.schemas import (
    UploadResponse,
    ProcessingStatusResponse,
    SubmissionDetailResponse,
    CandidateResultSchema,
    MarkedCandidateResultSchema,
    AnswerKeySchema,
    AnswerKeyResponse,
    MarkRequest,
    ErrorResponse,
    ExamCreateSchema,
    ExamResponse,
    ExamDocumentResponse,
    ExamDetailResponse,
    GeneratedJSONResponse,
)
from backend.services.local_storage import get_local_storage
from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ai_extractor import get_ai_extractor
from backend.services.json_generator import get_json_generator
from backend.services.space_client import get_spaces_client
from backend.services.image_preprocessor import ImagePreprocessor

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------- Exams APIs ------------------------------


@router.post("/exams", response_model=ExamResponse)
async def create_exam(body: ExamCreateSchema, db: Session = Depends(get_db)):
    exam = Exam(name=body.name)
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam


@router.get("/exams", response_model=List[ExamResponse])
async def list_exams(db: Session = Depends(get_db)):
    return db.query(Exam).order_by(Exam.created_at.desc()).all()


@router.get("/exams/{exam_id}", response_model=ExamDetailResponse)
async def get_exam(exam_id: int, db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.post("/exams/{exam_id}/correction", response_model=ExamResponse)
async def upload_correction_pdf(exam_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A valid PDF file is required")
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    storage = get_local_storage()
    saved = storage.save_pdf(file.file, f"exam_{exam_id}_correction.pdf")
    exam.correction_pdf_path = saved["relative_path"]
    db.commit()
    db.refresh(exam)
    return exam


@router.post("/exams/{exam_id}/student-pdfs", response_model=ExamDocumentResponse)
async def upload_student_pdf(exam_id: int, country: Optional[str] = None, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A valid PDF file is required")
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    storage = get_local_storage()
    saved = storage.save_pdf(file.file, file.filename)
    pdf_converter = get_pdf_converter()
    
    absolute_path = storage.get_absolute_path(saved["relative_path"])
    if not absolute_path:
        raise HTTPException(status_code=500, detail="Could not resolve saved file path")

    image_paths = pdf_converter.convert_from_file(absolute_path)
    pages = len(image_paths)
    # cleanup images
    for img in image_paths:
        try:
            Path(img).unlink(missing_ok=True)
        except Exception:
            pass
    doc = ExamDocument(
        exam_id=exam_id,
        country=country,
        file_path=saved["relative_path"],
        pages_count=pages,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/exams/{exam_id}/extract/{document_id}", response_model=GeneratedJSONResponse)
async def extract_exam_document(exam_id: int, document_id: int, db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    doc = db.query(ExamDocument).filter(ExamDocument.id == document_id, ExamDocument.exam_id == exam_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found for this exam")

    storage = get_local_storage()
    pdf_converter = get_pdf_converter()
    space_client = get_spaces_client()
    image_preprocessor = ImagePreprocessor()
    
    absolute_pdf = storage.get_absolute_path(doc.file_path)
    if absolute_pdf is None:
        raise HTTPException(status_code=500, detail="Could not resolve PDF file path")
    
    try:
        all_image_paths = pdf_converter.convert_from_file(absolute_pdf)
    except Exception as e:
        logger.error(f"Failed to convert PDF for doc_id={document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"PDF conversion failed: {e}")

    valid_image_paths = []
    for image_path in all_image_paths:
        # 1. Check if the page is blank
        if image_preprocessor.is_blank(image_path):
            logger.info(f"Skipping blank page: {Path(image_path).name}")
            continue

        # 2. Archive the valid image to Spaces (if enabled)
        try:
            space_client.upload_image(
                image_path=image_path,
                submission_id=document_id, # Using document_id as a proxy for submission_id
                original_pdf_name=Path(absolute_pdf).name
            )
        except Exception as e:
            # Log the error but don't block the main extraction process
            logger.error(f"Failed to archive image {Path(image_path).name} to Spaces: {e}")

        valid_image_paths.append(image_path)

    if not valid_image_paths:
        # If all pages were blank, there's nothing to process.
        logger.warning(f"No valid (non-blank) pages found in PDF for doc_id={document_id}. Aborting extraction.")
        # We could raise an error or return a specific response.
        # For now, let's create an empty JSON record to signify it was "processed".
        record = GeneratedJSON(
            exam_id=exam_id,
            file_path=None,
            filename=f"exam_{exam_id}_doc_{document_id}_empty.json",
            metadata={"status": "aborted", "reason": "No valid pages found"}
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    ai_extractor = get_ai_extractor()
    from backend.config import get_settings
    settings = get_settings()

    extraction_result = ai_extractor.extract_from_multiple_images(
        valid_image_paths,  # Use only valid images
        use_parallel=settings.use_parallel_extraction,
        max_workers=settings.max_extraction_workers,
    )

    validation_result = ai_extractor.validate_extraction(extraction_result)

    json_generator = get_json_generator()
    if settings.minimal_output:
        json_data = json_generator.generate_minimal(
            Path(absolute_pdf).name,
            extraction_result,
        )
    else:
        json_data = json_generator.generate_with_validation(
            Path(absolute_pdf).name,
            extraction_result,
            validation_result,
        )

    json_filename = f"exam_{exam_id}_doc_{document_id}.json"
    saved_json = storage.save_json(json_data, json_filename)

    record = GeneratedJSON(
        exam_id=exam_id,
        file_path=saved_json["relative_path"],
        filename=json_filename,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # cleanup all generated images (blank and valid)
    for img in all_image_paths:
        try:
            Path(img).unlink(missing_ok=True)
        except Exception:
            pass

    return record


@router.get("/exams/{exam_id}/jsons", response_model=List[GeneratedJSONResponse])
async def list_exam_jsons(exam_id: int, db: Session = Depends(get_db)):
    return db.query(GeneratedJSON).filter(GeneratedJSON.exam_id == exam_id).order_by(GeneratedJSON.created_at.desc()).all()


@router.get("/jsons/{json_id}")
async def download_json(json_id: int, db: Session = Depends(get_db)):
    storage = get_local_storage()
    record = db.query(GeneratedJSON).filter(GeneratedJSON.id == json_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="JSON not found")
    abs_path = storage.get_absolute_path(record.file_path)
    if not Path(abs_path).exists():
        raise HTTPException(status_code=404, detail="File missing")
    return JSONResponse(content=_json.loads(Path(abs_path).read_text(encoding="utf-8")))


@router.delete("/jsons/{json_id}")
async def delete_json(json_id: int, db: Session = Depends(get_db)):
    storage = get_local_storage()
    record = db.query(GeneratedJSON).filter(GeneratedJSON.id == json_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="JSON not found")
    abs_path = storage.get_absolute_path(record.file_path)
    try:
        Path(abs_path).unlink(missing_ok=True)
    except Exception:
        pass
    db.delete(record)
    db.commit()
    return {"status": "deleted"}


def process_pdf_extraction(submission_id: int, pdf_path: str):
    """Background task to convert pages, extract answers, and persist JSON locally."""
    db = SessionLocal()
    try:
        sub = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if not sub:
            logger.error(f"Submission {submission_id} not found")
            return
        setattr(sub, 'status', 'processing')
        db.add(ProcessingLog(
            submission_id=submission_id,
            action="extract_start",
            status="success",
            message="Starting PDF extraction"
        ))
        db.commit()
        logger.info(f"Converting PDF to images: {pdf_path}")
        pdf_converter = get_pdf_converter()
        image_paths = pdf_converter.convert_from_file(pdf_path)
        setattr(sub, 'pages_count', len(image_paths))
        db.commit()
        logger.info(f"Extracting answers using AI from {len(image_paths)} pages")
        ai_extractor = get_ai_extractor()
        from backend.config import get_settings
        settings = get_settings()
        extraction_result = ai_extractor.extract_from_multiple_images(
            image_paths,
            extraction_prompt=None,
            submission_id=submission_id,
            db=db,
            use_parallel=settings.use_parallel_extraction,
            max_workers=settings.max_extraction_workers
        )
        validation_result = ai_extractor.validate_extraction(extraction_result)
        json_gen = get_json_generator()
        json_data = json_gen.generate_with_validation(
            str(getattr(sub, 'filename')),
            extraction_result,
            validation_result
        )
        storage = get_local_storage()
        json_filename = f"{Path(str(getattr(sub, 'filename'))).stem}.json"
        save = storage.save_json(json_data, json_filename)
        setattr(sub, 'result_json_key', save['relative_path'])

        # Save per-candidate results to DB (supports dynamic header fields)
        KNOWN_COLUMNS = {"candidate_name", "candidate_number", "country", "paper_type"}
        # Common AI aliases that should map to known columns
        FIELD_ALIASES = {
            "candidate_no": "candidate_number",
            "candidate_id": "candidate_number",
            "name": "candidate_name",
            "student_name": "candidate_name",
            "student_number": "candidate_number",
        }
        for candidate in extraction_result.get('candidates', []):
            # Apply field aliases before splitting known vs extra
            normalized = {}
            for k, v in candidate.items():
                mapped_key = FIELD_ALIASES.get(k, k)
                # Don't overwrite if the canonical key already has a non-empty value
                if mapped_key in normalized and normalized[mapped_key]:
                    # Keep the canonical value, store alias in extra
                    pass
                else:
                    normalized[mapped_key] = v
            extra = {k: v for k, v in normalized.items()
                     if k not in KNOWN_COLUMNS
                     and k not in ("page_number", "answers", "drawing_questions")}
            db.add(CandidateResult(
                submission_id=submission_id,
                page_number=normalized.get('page_number'),
                candidate_name=normalized.get('candidate_name', ''),
                candidate_number=normalized.get('candidate_number', ''),
                country=normalized.get('country', ''),
                paper_type=normalized.get('paper_type', ''),
                extra_fields=extra if extra else None,
                answers=normalized.get('answers', {}),
                drawing_questions=normalized.get('drawing_questions', {}),
            ))

        setattr(sub, 'status', 'completed')
        setattr(sub, 'processed_at', datetime.utcnow())
        db.add(ProcessingLog(
            submission_id=submission_id,
            action="extract_complete",
            status="success",
            message=f"Extracted {len(extraction_result.get('candidates', []))} candidates",
        ))
        db.commit()
        logger.info(f"Successfully processed submission {submission_id}")
        for image_path in image_paths:
            try:
                Path(image_path).unlink(missing_ok=True)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup image {image_path}: {cleanup_error}")
    except Exception as e:
        logger.error(f"Failed to process submission {submission_id}: {e}")
        sub = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if sub:
            setattr(sub, 'status', 'failed')
            setattr(sub, 'error_message', str(e))
            db.add(ProcessingLog(
                submission_id=submission_id,
                action="extract_error",
                status="error",
                message=str(e)
            ))
            db.commit()
    finally:
        db.close()


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload a PDF exam answer sheet for processing
    
    Args:
        file: PDF file upload
        db: Database session
        
    Returns:
        Upload confirmation with submission ID
    """
    if not file or not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        storage = get_local_storage()
        logger.info(f"Storing PDF locally: {file.filename}")
        upload_result = storage.save_pdf(file.file, file.filename)
        
        # Create database entry
        submission = ExamSubmission(
            filename=file.filename,
            original_pdf_key=upload_result['relative_path'],
            status="pending"
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        
        # Log upload
        log_entry = ProcessingLog(
            submission_id=submission.id,
            action="upload",
            status="success",
            message=f"Stored {file.filename} at {upload_result['relative_path']}"
        )
        db.add(log_entry)
        db.commit()
        
        # Schedule background processing
        background_tasks.add_task(process_pdf_extraction, submission.id, upload_result['absolute_path'])
        
        logger.info(f"Created submission {submission.id} for {file.filename}")
        
        return UploadResponse(
            status="success",
            message="PDF uploaded successfully. Processing started.",
            submission_id=submission.id,
            filename=file.filename,
            storage_path=upload_result['relative_path']
        )
        
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/status/{submission_id}", response_model=ProcessingStatusResponse)
async def get_status(submission_id: int, db: Session = Depends(get_db)):
    """
    Get processing status of a submission
    
    Args:
        submission_id: Submission ID
        db: Database session
        
    Returns:
        Processing status details
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Default progress info
    current_page = None
    current_candidate_name = None

    # Get latest page/candidate info from ProcessingLog (if available)
    d = submission.__dict__
    log = (
        db.query(ProcessingLog)
        .filter(ProcessingLog.submission_id == d.get("id"), ProcessingLog.action == "page_progress")
        .order_by(ProcessingLog.created_at.desc())
        .first()
    )
    if log is not None and log.extra_data:
        current_page = log.extra_data.get("page")
        current_candidate_name = log.extra_data.get("label") or log.extra_data.get("candidate_name")

    # If we don't have a page from logs, try to infer it from saved answers
    if current_page is None:
        try:
            mcq_max = db.query(MultipleChoiceAnswer).filter(MultipleChoiceAnswer.submission_id == submission.id).order_by(MultipleChoiceAnswer.page_number.desc()).first()
            fr_max = db.query(FreeResponseAnswer).filter(FreeResponseAnswer.submission_id == submission.id).order_by(FreeResponseAnswer.page_number.desc()).first()
            max_mcq_page = mcq_max.page_number if mcq_max is not None else None
            max_fr_page = fr_max.page_number if fr_max is not None else None
            inferred_page = None
            if max_mcq_page is not None and max_fr_page is not None:
                inferred_page = max(max_mcq_page, max_fr_page)
            elif max_mcq_page is not None:
                inferred_page = max_mcq_page
            elif max_fr_page is not None:
                inferred_page = max_fr_page
            if inferred_page is not None:
                current_page = inferred_page
        except Exception as e:
            logger.debug(f"Failed to infer page from answers for submission {submission_id}: {e}")

    created_at = d.get("created_at")
    if not isinstance(created_at, datetime) or created_at is None:
        created_at = datetime.utcnow()

    # Count candidates and answers
    cand_count = db.query(CandidateResult).filter(CandidateResult.submission_id == submission.id).count()
    
    # Sum up answers and drawing questions from CandidateResult rows
    answers_count = 0
    drawing_count = 0
    cand_rows = db.query(CandidateResult).filter(CandidateResult.submission_id == submission.id).all()
    for cr in cand_rows:
        ans = getattr(cr, 'answers', None)
        if isinstance(ans, dict):
            answers_count += len(ans)
        drw = getattr(cr, 'drawing_questions', None)
        if isinstance(drw, dict):
            drawing_count += len(drw)

    return ProcessingStatusResponse(
        submission_id=int(d.get("id") or 0),
        filename=str(d.get("filename") or ""),
        status=str(d.get("status") or "unknown"),
        created_at=created_at,
        processed_at=d.get("processed_at") if isinstance(d.get("processed_at"), datetime) or d.get("processed_at") is None else None,
        pages_count=int(d.get("pages_count") or 0),
        candidates_count=cand_count,
        answers_count=answers_count,
        drawing_count=drawing_count,
        error_message=str(d.get("error_message")) if d.get("error_message") is not None else None,
        current_page=current_page,
        current_candidate_name=current_candidate_name
    )


@router.get("/submission/{submission_id}", response_model=SubmissionDetailResponse)
async def get_submission(submission_id: int, db: Session = Depends(get_db)):
    """
    Get full submission details with all candidate results
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    status_value = str(getattr(submission, 'status'))
    if status_value != "completed":
        raise HTTPException(status_code=400, detail=f"Submission is {status_value}, not completed")
    
    # Load candidate results from DB
    candidate_rows = db.query(CandidateResult).filter(
        CandidateResult.submission_id == submission_id
    ).order_by(CandidateResult.page_number).all()
    
    candidates = []
    for cr in candidate_rows:
        # Coerce None values in answers/drawing dicts to empty strings
        raw_answers = getattr(cr, 'answers') or {}
        clean_answers = {k: (str(v) if v is not None else '') for k, v in raw_answers.items()}
        raw_drawing = getattr(cr, 'drawing_questions') or {}
        clean_drawing = {k: (str(v) if v is not None else '') for k, v in raw_drawing.items()}
        
        # Merge extra_fields into display: use candidate_no/candidate_id as candidate_number if empty
        extra = getattr(cr, 'extra_fields') or {}
        cand_name = str(getattr(cr, 'candidate_name') or '') or extra.get('name', '') or extra.get('student_name', '')
        cand_number = str(getattr(cr, 'candidate_number') or '') or extra.get('candidate_no', '') or extra.get('candidate_id', '') or extra.get('student_number', '')
        
        candidates.append(CandidateResultSchema(
            candidate_name=cand_name,
            candidate_number=cand_number,
            country=str(getattr(cr, 'country') or ''),
            paper_type=str(getattr(cr, 'paper_type') or ''),
            extra_fields=extra if extra else None,
            answers=clean_answers,
            drawing_questions=clean_drawing,
        ))
    
    return SubmissionDetailResponse(
        submission_id=int(getattr(submission, 'id')),
        filename=str(getattr(submission, 'filename')),
        status=str(getattr(submission, 'status')),
        created_at=getattr(submission, 'created_at'),
        processed_at=getattr(submission, 'processed_at'),
        candidates=candidates,
    )


@router.get("/submissions", response_model=List[ProcessingStatusResponse])
async def list_submissions(
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    db: Session = Depends(get_db)
):
    """
    List all submissions with optional filtering
    
    Args:
        skip: Number of records to skip
        limit: Maximum records to return
        status: Filter by status (optional)
        db: Database session
        
    Returns:
        List of submissions
    """
    query = db.query(ExamSubmission)
    
    if status:
        query = query.filter(ExamSubmission.status == status)
    
    submissions = query.order_by(ExamSubmission.created_at.desc()).offset(skip).limit(limit).all()
    
    results: List[ProcessingStatusResponse] = []
    for sub in submissions:
        # Count candidates and answers for this submission
        cand_rows = db.query(CandidateResult).filter(CandidateResult.submission_id == sub.id).all()
        cand_count = len(cand_rows)
        answers_count = 0
        drawing_count = 0
        for cr in cand_rows:
            ans = getattr(cr, 'answers', None)
            if isinstance(ans, dict):
                answers_count += len(ans)
            drw = getattr(cr, 'drawing_questions', None)
            if isinstance(drw, dict):
                drawing_count += len(drw)
        results.append(ProcessingStatusResponse(
            submission_id=int(getattr(sub,'id')),
            filename=str(getattr(sub,'filename')),
            status=str(getattr(sub,'status')),
            created_at=getattr(sub,'created_at'),
            processed_at=getattr(sub,'processed_at'),
            pages_count=int(getattr(sub,'pages_count') or 0),
            candidates_count=cand_count,
            answers_count=answers_count,
            drawing_count=drawing_count,
            error_message=str(getattr(sub,'error_message')) if getattr(sub,'error_message') is not None else None
        ))
    return results


@router.get("/submission/{submission_id}/json")
async def get_submission_json(submission_id: int, db: Session = Depends(get_db)):
    """
    Get the raw JSON result file for a submission
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    result_key = getattr(submission,'result_json_key', None)
    if result_key is None or str(result_key).strip() == "":
        raise HTTPException(status_code=404, detail="JSON result not found")
    try:
        storage = get_local_storage()
        key = getattr(submission, 'result_json_key')
        logger.info(f"Loading JSON for submission {submission_id} from {key}")
        json_data = storage.read_json(key)
        if json_data is None:
            raise HTTPException(status_code=404, detail="JSON result file not found in storage.")
        # Parse JSON string and return as proper JSON response
        try:
            parsed = _json.loads(json_data)
            return JSONResponse(content=parsed)
        except _json.JSONDecodeError:
            # If parsing fails, log and return the raw string as text with application/json media type
            logger.error(f"Downloaded JSON is invalid JSON for submission {submission_id}")
            return JSONResponse(content={"raw": json_data})
    except Exception as e:
        logger.error(f"Failed to retrieve JSON for {submission_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve JSON: {str(e)}")


@router.get("/submission/{submission_id}/logs")
async def get_submission_logs(submission_id: int, limit: int = 10, db: Session = Depends(get_db)):
    """
    Return the most recent processing logs for a submission.
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    logs = (
        db.query(ProcessingLog)
        .filter(ProcessingLog.submission_id == submission_id)
        .order_by(ProcessingLog.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for log in logs:
        dt = getattr(log, 'created_at', None)
        out.append({
            "id": getattr(log, 'id', None),
            "action": getattr(log, 'action', None),
            "status": getattr(log, 'status', None),
            "message": getattr(log, 'message', None),
            "extra_data": getattr(log, 'extra_data', None),
            "created_at": dt.isoformat() if dt else None,
        })
    return out


@router.delete("/submission/{submission_id}")
async def delete_submission(submission_id: int, db: Session = Depends(get_db)):
    """
    Delete a submission and all associated data
    
    Args:
        submission_id: Submission ID
        db: Database session
        
    Returns:
        Deletion confirmation
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    storage = get_local_storage()
    storage.delete_file(getattr(submission, 'original_pdf_key', None))
    if getattr(submission, 'result_json_key', None):
        storage.delete_file(getattr(submission, 'result_json_key'))
    
    # Delete from database (cascades to answers)
    db.delete(submission)
    db.commit()
    
    logger.info(f"Deleted submission {submission_id}")
    
    return {"status": "success", "message": f"Submission {submission_id} deleted"}


@router.post("/extract/json")
async def extract_json(file: UploadFile = File(...)):
    """Synchronous PDF → JSON extraction endpoint for third-party use.

    Accepts a PDF upload and returns structured JSON immediately without
    creating DB records or using background tasks.
    """
    # Validate file
    if not file or not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    try:
        # Convert PDF to images
        pdf_converter = get_pdf_converter()
        # Save to a temporary path under local storage uploads for consistency
        storage = get_local_storage()
        saved = storage.save_pdf(file.file, file.filename)
        image_paths = pdf_converter.convert_from_file(saved["absolute_path"]) 

        # Run extraction with parallel processing
        ai_extractor = get_ai_extractor()
        from backend.config import get_settings
        settings = get_settings()
        extraction_result = ai_extractor.extract_from_multiple_images(
            image_paths,
            extraction_prompt=None,
            submission_id=None,
            db=None,
            use_parallel=settings.use_parallel_extraction,
            max_workers=settings.max_extraction_workers
        )
        validation_result = ai_extractor.validate_extraction(extraction_result)

        # Generate structured JSON
        json_generator = get_json_generator()
        json_data = json_generator.generate_with_validation(
            file.filename,
            extraction_result,
            validation_result,
        )

        # Cleanup images
        for img in image_paths:
            try:
                Path(img).unlink(missing_ok=True)
            except Exception:
                pass

        # Return parsed JSON
        return JSONResponse(content=_json.loads(json_data))
    except Exception as e:
        logger.error(f"Synchronous extraction failed: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")


# ── Answer Key CRUD ──────────────────────────────────────────────────

@router.post("/answer-keys", response_model=AnswerKeyResponse)
async def create_answer_key(body: AnswerKeySchema, db: Session = Depends(get_db)):
    """Create a new answer key for auto-marking."""
    ak = AnswerKey(
        name=body.name,
        paper_type=body.paper_type,
        answers=body.answers,
        drawing_key=body.drawing_key,
        total_questions=len(body.answers) + (len(body.drawing_key) if body.drawing_key else 0),
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return AnswerKeyResponse(
        id=int(getattr(ak, 'id')),
        name=str(getattr(ak, 'name')),
        paper_type=getattr(ak, 'paper_type'),
        answers=getattr(ak, 'answers'),
        drawing_key=getattr(ak, 'drawing_key'),
        total_questions=getattr(ak, 'total_questions'),
        created_at=getattr(ak, 'created_at'),
        updated_at=getattr(ak, 'updated_at'),
    )


@router.get("/answer-keys", response_model=List[AnswerKeyResponse])
async def list_answer_keys(db: Session = Depends(get_db)):
    """List all answer keys."""
    keys = db.query(AnswerKey).order_by(AnswerKey.created_at.desc()).all()
    return [
        AnswerKeyResponse(
            id=int(getattr(ak, 'id')),
            name=str(getattr(ak, 'name')),
            paper_type=getattr(ak, 'paper_type'),
            answers=getattr(ak, 'answers'),
            drawing_key=getattr(ak, 'drawing_key'),
            total_questions=getattr(ak, 'total_questions'),
            created_at=getattr(ak, 'created_at'),
            updated_at=getattr(ak, 'updated_at'),
        )
        for ak in keys
    ]


@router.get("/answer-keys/{key_id}", response_model=AnswerKeyResponse)
async def get_answer_key(key_id: int, db: Session = Depends(get_db)):
    """Get a single answer key."""
    ak = db.query(AnswerKey).filter(AnswerKey.id == key_id).first()
    if not ak:
        raise HTTPException(status_code=404, detail="Answer key not found")
    return AnswerKeyResponse(
        id=int(getattr(ak, 'id')),
        name=str(getattr(ak, 'name')),
        paper_type=getattr(ak, 'paper_type'),
        answers=getattr(ak, 'answers'),
        drawing_key=getattr(ak, 'drawing_key'),
        total_questions=getattr(ak, 'total_questions'),
        created_at=getattr(ak, 'created_at'),
        updated_at=getattr(ak, 'updated_at'),
    )


@router.delete("/answer-keys/{key_id}")
async def delete_answer_key(key_id: int, db: Session = Depends(get_db)):
    """Delete an answer key."""
    ak = db.query(AnswerKey).filter(AnswerKey.id == key_id).first()
    if not ak:
        raise HTTPException(status_code=404, detail="Answer key not found")
    db.delete(ak)
    db.commit()
    return {"status": "success", "message": f"Answer key {key_id} deleted"}


# ── Auto-marking endpoints ───────────────────────────────────────────

@router.post("/submission/{submission_id}/mark")
async def mark_submission(
    submission_id: int,
    body: MarkRequest,
    db: Session = Depends(get_db)
):
    """
    Auto-mark all candidates in a submission against an answer key.
    
    Provide EITHER answer_key_id (to use a stored key) OR inline answer_key dict.
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if str(getattr(submission, 'status')) != "completed":
        raise HTTPException(status_code=400, detail="Submission not yet completed")

    # Resolve answer key
    if body.answer_key_id:
        ak = db.query(AnswerKey).filter(AnswerKey.id == body.answer_key_id).first()
        if not ak:
            raise HTTPException(status_code=404, detail="Answer key not found")
        mcq_key = getattr(ak, 'answers') or {}
        draw_key = getattr(ak, 'drawing_key')
    elif body.answer_key:
        mcq_key = body.answer_key
        draw_key = body.drawing_key
    else:
        raise HTTPException(status_code=400, detail="Provide answer_key_id or answer_key")

    # Load candidate results
    candidate_rows = db.query(CandidateResult).filter(
        CandidateResult.submission_id == submission_id
    ).all()
    
    if not candidate_rows:
        raise HTTPException(status_code=404, detail="No candidate results found for this submission")

    # Build dicts for marking
    candidates_data = []
    for cr in candidate_rows:
        candidates_data.append({
            "db_id": getattr(cr, 'id'),
            "candidate_name": getattr(cr, 'candidate_name') or '',
            "candidate_number": getattr(cr, 'candidate_number') or '',
            "country": getattr(cr, 'country') or '',
            "paper_type": getattr(cr, 'paper_type') or '',
            "extra_fields": getattr(cr, 'extra_fields') or {},
            "answers": getattr(cr, 'answers') or {},
            "drawing_questions": getattr(cr, 'drawing_questions') or {},
        })

    # Run marking
    json_gen = get_json_generator()
    marked = json_gen.mark_answers(
        [c for c in candidates_data],
        mcq_key,
        draw_key,
    )

    # Persist marking results back to DB
    results = []
    for i, m in enumerate(marked):
        cr = candidate_rows[i]
        setattr(cr, 'marked_answers', m.get('marked_answers'))
        setattr(cr, 'marked_drawing', m.get('marked_drawing'))
        score = m.get('score', {})
        setattr(cr, 'score_correct', score.get('correct'))
        setattr(cr, 'score_total', score.get('total'))
        setattr(cr, 'score_percentage', score.get('percentage'))

        results.append({
            "candidate_name": m.get('candidate_name', ''),
            "candidate_number": m.get('candidate_number', ''),
            "marked_answers": m.get('marked_answers', {}),
            "marked_drawing": m.get('marked_drawing', {}),
            "score": score,
        })

    db.commit()
    
    return {
        "status": "success",
        "submission_id": submission_id,
        "total_candidates_marked": len(results),
        "results": results,
    }


@router.post("/extract/json/mark")
async def extract_and_mark(
    file: UploadFile = File(...),
    mark_request: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Synchronous: extract PDF + auto-mark against an answer key.
    
    Accepts an optional `mark_request` form field (JSON string) with:
      - answer_key_id: int (use a stored answer key)
      - answer_key: dict (inline MCQ answer key, e.g. {"1": "D", "2": "B"})
      - drawing_key: dict (inline drawing key, e.g. {"31": "circle"})
    
    If no mark_request is provided, tries to auto-match stored answer keys
    by paper_type. If no match found, returns unmarked results.
    """
    if not file or not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    try:
        # Extract
        pdf_converter = get_pdf_converter()
        storage = get_local_storage()
        saved = storage.save_pdf(file.file, file.filename)
        image_paths = pdf_converter.convert_from_file(saved["absolute_path"])

        ai_extractor = get_ai_extractor()
        from backend.config import get_settings
        settings = get_settings()
        extraction_result = ai_extractor.extract_from_multiple_images(
            image_paths,
            use_parallel=settings.use_parallel_extraction,
            max_workers=settings.max_extraction_workers,
        )

        candidates = extraction_result.get("candidates", [])
        json_gen = get_json_generator()

        # Parse inline mark request if provided
        inline_answer_key = None
        inline_drawing_key = None
        if mark_request:
            import json as _j
            try:
                mr = _j.loads(mark_request)
            except _j.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid mark_request JSON")
            
            # If answer_key_id provided, load from DB
            if mr.get("answer_key_id"):
                ak = db.query(AnswerKey).filter(AnswerKey.id == mr["answer_key_id"]).first()
                if not ak:
                    raise HTTPException(status_code=404, detail=f"Answer key {mr['answer_key_id']} not found")
                inline_answer_key = getattr(ak, 'answers') or {}
                inline_drawing_key = getattr(ak, 'drawing_key')
            else:
                inline_answer_key = mr.get("answer_key")
                inline_drawing_key = mr.get("drawing_key")

        marked_candidates = []

        if inline_answer_key:
            # Apply inline answer key to ALL candidates
            marked_candidates = json_gen.mark_answers(
                candidates, inline_answer_key, inline_drawing_key
            )
        else:
            # Auto-match by paper_type from stored answer keys
            all_keys = db.query(AnswerKey).all()
            keys_by_type = {}
            for ak in all_keys:
                pt = str(getattr(ak, 'paper_type') or '').strip().upper()
                if pt:
                    keys_by_type[pt] = ak

            for candidate in candidates:
                pt_raw = str(candidate.get('paper_type', '')).strip().upper()
                # Fuzzy match: check if any stored key's paper_type is contained in the candidate's paper_type
                matched_ak = None
                if pt_raw in keys_by_type:
                    matched_ak = keys_by_type[pt_raw]
                else:
                    for stored_pt, ak in keys_by_type.items():
                        if stored_pt in pt_raw or pt_raw in stored_pt:
                            matched_ak = ak
                            break

                if matched_ak:
                    marked_list = json_gen.mark_answers(
                        [candidate],
                        getattr(matched_ak, 'answers') or {},
                        getattr(matched_ak, 'drawing_key'),
                    )
                    marked_candidates.append(marked_list[0])
                else:
                    marked_candidates.append(candidate)

        # Cleanup images
        for img in image_paths:
            try:
                Path(img).unlink(missing_ok=True)
            except Exception:
                pass

        return JSONResponse(content={
            "filename": file.filename,
            "total_candidates": len(marked_candidates),
            "candidates": marked_candidates,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Extract & mark failed: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")
