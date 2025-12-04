"""
FastAPI routes for exam answer sheet processing
"""
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import json as _json
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from pathlib import Path
import logging

from backend.db.database import get_db, SessionLocal
from backend.db.models import ExamSubmission, MultipleChoiceAnswer, FreeResponseAnswer, ProcessingLog
from backend.api.schemas import (
    UploadResponse,
    ProcessingStatusResponse,
    SubmissionDetailResponse,
    ErrorResponse
)
from backend.services.local_storage import get_local_storage
from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ai_extractor import get_ai_extractor
from backend.services.json_generator import get_json_generator

logger = logging.getLogger(__name__)
router = APIRouter()


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
        for mcq in extraction_result.get('multiple_choice', []):
            db.add(MultipleChoiceAnswer(
                submission_id=submission_id,
                question_number=mcq['question'],
                selected_answer=mcq['answer'],
                page_number=mcq.get('page')
            ))
        for fr in extraction_result.get('free_response', []):
            db.add(FreeResponseAnswer(
                submission_id=submission_id,
                question_number=fr['question'],
                response_text=fr['response'],
                word_count=len(fr['response'].split()),
                page_number=fr.get('page')
            ))
        setattr(sub, 'status', 'completed')
        setattr(sub, 'processed_at', datetime.utcnow())
        db.add(ProcessingLog(
            submission_id=submission_id,
            action="extract_complete",
            status="success",
            message=f"Extracted {len(extraction_result.get('multiple_choice', []))} MCQ and {len(extraction_result.get('free_response', []))} free response answers",
            metadata=validation_result
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
        current_candidate_name = log.extra_data.get("candidate_name")

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
    return ProcessingStatusResponse(
        submission_id=int(d.get("id") or 0),
        filename=str(d.get("filename") or ""),
        status=str(d.get("status") or "unknown"),
        created_at=created_at,
        processed_at=d.get("processed_at") if isinstance(d.get("processed_at"), datetime) or d.get("processed_at") is None else None,
        pages_count=int(d.get("pages_count") or 0),
        mcq_count=len(submission.mcq_answers) if hasattr(submission, 'mcq_answers') else 0,
        free_response_count=len(submission.free_responses) if hasattr(submission, 'free_responses') else 0,
        error_message=str(d.get("error_message")) if d.get("error_message") is not None else None,
        current_page=current_page,
        current_candidate_name=current_candidate_name
    )


@router.get("/submission/{submission_id}", response_model=SubmissionDetailResponse)
async def get_submission(submission_id: int, db: Session = Depends(get_db)):
    """
    Get full submission details with all answers
    
    Args:
        submission_id: Submission ID
        db: Database session
        
    Returns:
        Complete submission data
    """
    submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
    
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    status_value = str(getattr(submission, 'status'))
    if status_value != "completed":
        raise HTTPException(status_code=400, detail=f"Submission is {status_value}, not completed")
    
    from backend.api.schemas import MultipleChoiceAnswerSchema, FreeResponseAnswerSchema
    mcq_items = [
        MultipleChoiceAnswerSchema(
            question=int(getattr(mcq,'question_number')),
            answer=str(getattr(mcq,'selected_answer'))
        ) for mcq in getattr(submission,'mcq_answers', [])
    ]
    fr_items = [
        FreeResponseAnswerSchema(
            question=int(getattr(fr,'question_number')),
            response=str(getattr(fr,'response_text'))
        ) for fr in getattr(submission,'free_responses', [])
    ]
    return SubmissionDetailResponse(
        submission_id=int(getattr(submission,'id')),
        filename=str(getattr(submission,'filename')),
        status=str(getattr(submission,'status')),
        created_at=getattr(submission,'created_at'),
        processed_at=getattr(submission,'processed_at'),
        multiple_choice=mcq_items,
        free_response=fr_items
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
        results.append(ProcessingStatusResponse(
            submission_id=int(getattr(sub,'id')),
            filename=str(getattr(sub,'filename')),
            status=str(getattr(sub,'status')),
            created_at=getattr(sub,'created_at'),
            processed_at=getattr(sub,'processed_at'),
            pages_count=int(getattr(sub,'pages_count') or 0),
            mcq_count=len(getattr(sub,'mcq_answers', [])),
            free_response_count=len(getattr(sub,'free_responses', [])),
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
