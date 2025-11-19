"""
FastAPI routes for exam answer sheet processing
"""
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
import logging
import os
import tempfile

from backend.db.database import get_db
from backend.db.models import ExamSubmission, MultipleChoiceAnswer, FreeResponseAnswer, ProcessingLog
from backend.api.schemas import (
    UploadResponse,
    ProcessingStatusResponse,
    SubmissionDetailResponse,
    ErrorResponse
)
from backend.services.space_client import get_spaces_client
from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ai_extractor import get_ai_extractor
from backend.services.json_generator import get_json_generator
from backend.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


def process_pdf_extraction(submission_id: int, pdf_path: str):
    """
    Background task to process PDF extraction
    
    Args:
        submission_id: Database submission ID
        pdf_path: Path to downloaded PDF file
    """
    db = next(get_db())
    
    try:
        submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if not submission:
            logger.error(f"Submission {submission_id} not found")
            return
        
        # Update status to processing
        submission.status = "processing"
        db.commit()
        
        # Log processing start
        log_entry = ProcessingLog(
            submission_id=submission_id,
            action="extract_start",
            status="success",
            message="Starting PDF extraction"
        )
        db.add(log_entry)
        db.commit()
        
        # Convert PDF to images using PyMuPDF (no external dependencies)
        logger.info(f"Converting PDF to images: {pdf_path}")
        pdf_converter = get_pdf_converter()
        image_paths = pdf_converter.convert_from_file(pdf_path)
        
        submission.pages_count = len(image_paths)
        db.commit()
        
        # Extract using AI from images
        logger.info(f"Extracting answers using AI from {len(image_paths)} pages")
        ai_extractor = get_ai_extractor()
        extraction_result = ai_extractor.extract_from_multiple_images(image_paths)
        
        # Validate extraction
        validation_result = ai_extractor.validate_extraction(extraction_result)
        
        # Generate JSON
        json_generator = get_json_generator()
        json_data = json_generator.generate_with_validation(
            submission.filename,
            extraction_result,
            validation_result
        )
        
        # Upload JSON to Spaces
        spaces_client = get_spaces_client()
        json_filename = f"{os.path.splitext(submission.filename)[0]}.json"
        upload_result = spaces_client.upload_json(json_data, json_filename)
        
        submission.result_json_key = upload_result['key']
        
        # Save to database
        for mcq in extraction_result.get('multiple_choice', []):
            answer = MultipleChoiceAnswer(
                submission_id=submission_id,
                question_number=mcq['question'],
                selected_answer=mcq['answer'],
                page_number=mcq.get('page')  # Save page number
            )
            db.add(answer)
        
        for fr in extraction_result.get('free_response', []):
            answer = FreeResponseAnswer(
                submission_id=submission_id,
                question_number=fr['question'],
                response_text=fr['response'],
                word_count=len(fr['response'].split()),
                page_number=fr.get('page')  # Save page number
            )
            db.add(answer)
        
        # Update submission status
        submission.status = "completed"
        submission.processed_at = datetime.utcnow()
        
        # Log success
        log_entry = ProcessingLog(
            submission_id=submission_id,
            action="extract_complete",
            status="success",
            message=f"Extracted {len(extraction_result.get('multiple_choice', []))} MCQ and {len(extraction_result.get('free_response', []))} free response answers",
            metadata=validation_result
        )
        db.add(log_entry)
        
        db.commit()
        logger.info(f"Successfully processed submission {submission_id}")
        
        # Clean up temporary files
        for image_path in image_paths:
            try:
                os.remove(image_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup image {image_path}: {cleanup_error}")
        
        try:
            os.remove(pdf_path)
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup temp file {pdf_path}: {cleanup_error}")
        
    except Exception as e:
        logger.error(f"Failed to process submission {submission_id}: {str(e)}")
        
        # Update submission with error
        submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if submission:
            submission.status = "failed"
            submission.error_message = str(e)
            
            # Log error
            log_entry = ProcessingLog(
                submission_id=submission_id,
                action="extract_error",
                status="error",
                message=str(e)
            )
            db.add(log_entry)
            
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
    # Validate file
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    settings = get_settings()
    
    try:
        # Upload to Spaces
        logger.info(f"Uploading PDF: {file.filename}")
        spaces_client = get_spaces_client()
        upload_result = spaces_client.upload_pdf(file.file, file.filename)
        
        # Create database entry
        submission = ExamSubmission(
            filename=file.filename,
            original_pdf_key=upload_result['key'],
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
            message=f"Uploaded {file.filename} to Spaces"
        )
        db.add(log_entry)
        db.commit()
        
        # Download PDF for processing
        temp_pdf_path = os.path.join(tempfile.gettempdir(), f"submission_{submission.id}.pdf")
        spaces_client.download_pdf(upload_result['key'], temp_pdf_path)
        
        # Schedule background processing
        background_tasks.add_task(process_pdf_extraction, submission.id, temp_pdf_path)
        
        logger.info(f"Created submission {submission.id} for {file.filename}")
        
        return UploadResponse(
            status="success",
            message="PDF uploaded successfully. Processing started.",
            submission_id=submission.id,
            filename=file.filename,
            spaces_key=upload_result['key']
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
    
    return ProcessingStatusResponse(
        submission_id=submission.id,
        filename=submission.filename,
        status=submission.status,
        created_at=submission.created_at,
        processed_at=submission.processed_at,
        pages_count=submission.pages_count,
        mcq_count=len(submission.mcq_answers),
        free_response_count=len(submission.free_responses),
        error_message=submission.error_message
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
    
    if submission.status != "completed":
        raise HTTPException(status_code=400, detail=f"Submission is {submission.status}, not completed")
    
    return SubmissionDetailResponse(
        submission_id=submission.id,
        filename=submission.filename,
        status=submission.status,
        created_at=submission.created_at,
        processed_at=submission.processed_at,
        multiple_choice=[
            {"question": mcq.question_number, "answer": mcq.selected_answer}
            for mcq in submission.mcq_answers
        ],
        free_response=[
            {"question": fr.question_number, "response": fr.response_text}
            for fr in submission.free_responses
        ]
    )


@router.get("/submissions", response_model=List[ProcessingStatusResponse])
async def list_submissions(
    skip: int = 0,
    limit: int = 100,
    status: str = None,
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
    
    return [
        ProcessingStatusResponse(
            submission_id=sub.id,
            filename=sub.filename,
            status=sub.status,
            created_at=sub.created_at,
            processed_at=sub.processed_at,
            pages_count=sub.pages_count,
            mcq_count=len(sub.mcq_answers),
            free_response_count=len(sub.free_responses),
            error_message=sub.error_message
        )
        for sub in submissions
    ]


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
    
    # Delete from Spaces
    spaces_client = get_spaces_client()
    spaces_client.delete_file(submission.original_pdf_key)
    
    if submission.result_json_key:
        spaces_client.delete_file(submission.result_json_key)
    
    # Delete from database (cascades to answers)
    db.delete(submission)
    db.commit()
    
    logger.info(f"Deleted submission {submission_id}")
    
    return {"status": "success", "message": f"Submission {submission_id} deleted"}
