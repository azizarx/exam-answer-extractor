"""Stream queued uploads before FastAPI's multipart dependency spools the body."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from backend.config import get_settings
from backend.db.database import SessionLocal
import re

class QueuedUploadMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        exam_route=re.fullmatch(r'/exams/(\d+)/(correction|student-pdfs)',scope.get('path',''))
        if scope['type']!='http' or scope['method']!='POST' or (scope['path'] not in {'/upload','/extract/json','/extract/json/mark'} and not exam_route) or not get_settings().queue_enabled:
            return await self.app(scope,receive,send)
        request=Request(scope,receive)
        try:
            from backend.api.routes import _validate_optional_template_id
            template=_validate_optional_template_id(request.query_params.get('template_id'))
            from backend.services.queued_upload import receive_upload
            legacy=scope['path'].startswith('/extract/')
            with SessionLocal() as db:
                if exam_route:
                    from backend.db.models import Exam
                    exam_id=int(exam_route[1])
                    if db.get(Exam,exam_id) is None:raise HTTPException(404,'Exam not found')
                    body,job_id=await receive_upload(request,db,kind='correction' if exam_route[2]=='correction' else 'document',payload={'exam_id':exam_id,'country':request.query_params.get('country')})
                else:
                    body,job_id=await receive_upload(request,db,template,kind='legacy' if legacy else 'process',payload={'marked':scope['path'].endswith('/mark')} if legacy else None)
                if legacy and job_id is None:
                    from backend.db.models import ProcessingJob
                    job=db.query(ProcessingJob).filter_by(submission_id=body['submission_id'],kind='legacy').first()
                    if job is None:raise HTTPException(409,'Idempotency key belongs to a different endpoint')
                    job_id=job.id
            if legacy:
                from backend.queue.runtime import wait_job
                body=await wait_job(job_id)
            response=JSONResponse(body,headers={'X-Job-ID':str(job_id)} if job_id else None)
        except HTTPException as exc:response=JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Upload failed before queue acceptance')
            response=JSONResponse({'detail':'Upload failed before acceptance; reconcile the idempotency key before retrying'},status_code=500)
        await response(scope,receive,send)
