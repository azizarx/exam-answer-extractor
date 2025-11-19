"""
Celery worker for background task processing
"""
from celery import Celery
import logging
import os
import tempfile
from datetime import datetime

from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import ExamSubmission, MultipleChoiceAnswer, FreeResponseAnswer, ProcessingLog
from backend.services.space_client import get_spaces_client
from backend.services.pdf_to_images import get_pdf_converter
from backend.services.ai_extractor import get_ai_extractor
from backend.services.json_generator import get_json_generator

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create Celery app
settings = get_settings()
celery_app = Celery(
    'exam_extractor',
    broker=settings.redis_url,
    backend=settings.redis_url
)

celery_app.conf.task_routes = {
    'backend.worker.process_exam_pdf': {'queue': 'exam_processing'},
}

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)


@celery_app.task(name='backend.worker.process_exam_pdf')
def process_exam_pdf(submission_id: int):
    """
    Celery task to process exam PDF extraction
    
    Args:
        submission_id: Database submission ID
    """
    db = SessionLocal()
    
    try:
        submission = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if not submission:
            logger.error(f"Submission {submission_id} not found")
            return {"status": "error", "message": "Submission not found"}
        
        logger.info(f"Processing submission {submission_id}: {submission.filename}")
        
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
        
        # Download PDF from Spaces
        spaces_client = get_spaces_client()
        temp_pdf_path = os.path.join(tempfile.gettempdir(), f"submission_{submission_id}.pdf")
        
        logger.info(f"Downloading PDF from Spaces: {submission.original_pdf_key}")
        if not spaces_client.download_pdf(submission.original_pdf_key, temp_pdf_path):
            raise Exception("Failed to download PDF from Spaces")
        
        # Convert PDF to images
        logger.info(f"Converting PDF to images: {temp_pdf_path}")
        pdf_converter = get_pdf_converter()
        image_paths = pdf_converter.convert_from_file(temp_pdf_path)
        
        submission.pages_count = len(image_paths)
        db.commit()
        
        # Extract using AI
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
        json_filename = f"{os.path.splitext(submission.filename)[0]}.json"
        upload_result = spaces_client.upload_json(json_data, json_filename)
        
        submission.result_json_key = upload_result['key']
        db.commit()
        
        # Save to database
        for mcq in extraction_result.get('multiple_choice', []):
            answer = MultipleChoiceAnswer(
                submission_id=submission_id,
                question_number=mcq['question'],
                selected_answer=mcq['answer']
            )
            db.add(answer)
        
        for fr in extraction_result.get('free_response', []):
            answer = FreeResponseAnswer(
                submission_id=submission_id,
                question_number=fr['question'],
                response_text=fr['response'],
                word_count=len(fr['response'].split())
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
            except:
                pass
        
        try:
            os.remove(temp_pdf_path)
        except:
            pass
        
        return {
            "status": "success",
            "submission_id": submission_id,
            "mcq_count": len(extraction_result.get('multiple_choice', [])),
            "free_response_count": len(extraction_result.get('free_response', []))
        }
        
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
        
        return {
            "status": "error",
            "submission_id": submission_id,
            "error": str(e)
        }
    
    finally:
        db.close()


if __name__ == '__main__':
    # Start worker with: celery -A backend.worker worker --loglevel=info -Q exam_processing
    logger.info("Starting Celery worker...")
