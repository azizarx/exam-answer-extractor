"""
Celery worker for background task processing
"""
from celery import Celery
import logging
from datetime import datetime
from pathlib import Path

from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import ExamSubmission, MultipleChoiceAnswer, FreeResponseAnswer, ProcessingLog
from backend.services.local_storage import get_local_storage
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
    db = SessionLocal()
    try:
        sub = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if not sub:
            logger.error(f"Submission {submission_id} not found")
            return {"status": "error", "message": "Submission not found"}
        logger.info(f"Processing submission {submission_id}: {getattr(sub,'filename')}")
        setattr(sub,'status','processing')
        db.commit()
        db.add(ProcessingLog(
            submission_id=submission_id,
            action="extract_start",
            status="success",
            message="Starting PDF extraction"
        ))
        db.commit()
        storage = get_local_storage()
        pdf_path_obj = storage.get_absolute_path(getattr(sub,'original_pdf_key'))
        if not pdf_path_obj or not pdf_path_obj.exists():
            raise Exception("Stored PDF not found for submission")
        pdf_path = str(pdf_path_obj)
        logger.info(f"Converting PDF to images: {pdf_path}")
        pdf_converter = get_pdf_converter()
        image_paths = pdf_converter.convert_from_file(pdf_path)
        setattr(sub,'pages_count', len(image_paths))
        db.commit()
        logger.info(f"Extracting answers using AI from {len(image_paths)} pages")
        ai_extractor = get_ai_extractor()
        extraction_result = ai_extractor.extract_from_multiple_images(
            image_paths,
            extraction_prompt=None,
            submission_id=submission_id,
            db=db
        )
        validation_result = ai_extractor.validate_extraction(extraction_result)
        json_gen = get_json_generator()
        json_data = json_gen.generate_with_validation(
            str(getattr(sub,'filename')),
            extraction_result,
            validation_result
        )
        json_filename = f"{Path(str(getattr(sub,'filename'))).stem}.json"
        save = storage.save_json(json_data, json_filename)
        setattr(sub,'result_json_key', save['relative_path'])
        db.commit()
        for mcq in extraction_result.get('multiple_choice', []):
            db.add(MultipleChoiceAnswer(
                submission_id=submission_id,
                question_number=mcq['question'],
                selected_answer=mcq['answer']
            ))
        for fr in extraction_result.get('free_response', []):
            db.add(FreeResponseAnswer(
                submission_id=submission_id,
                question_number=fr['question'],
                response_text=fr['response'],
                word_count=len(fr['response'].split())
            ))
        setattr(sub,'status','completed')
        setattr(sub,'processed_at', datetime.utcnow())
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
            except Exception:
                pass
        return {
            "status": "success",
            "submission_id": submission_id,
            "mcq_count": len(extraction_result.get('multiple_choice', [])),
            "free_response_count": len(extraction_result.get('free_response', []))
        }
    except Exception as e:
        logger.error(f"Failed to process submission {submission_id}: {e}")
        sub = db.query(ExamSubmission).filter(ExamSubmission.id == submission_id).first()
        if sub:
            setattr(sub,'status','failed')
            setattr(sub,'error_message', str(e))
            db.add(ProcessingLog(
                submission_id=submission_id,
                action="extract_error",
                status="error",
                message=str(e)
            ))
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
