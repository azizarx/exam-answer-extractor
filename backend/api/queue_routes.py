"""Queue status, capabilities and exact candidate/paper page resolution."""
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db.database import get_db
from backend.db.models import ProcessingJob, ExamSubmission, CandidateResult, StorageArtifact, PageCheckpoint
from backend.queue.runtime import enqueue

router=APIRouter()

# External exam IDs identify a series, independently of layout and answer keys.
PAGE_LOOKUP_EXAMS = {'31': {'series': 'seamo_2026', 'brand': 'seamo', 'year': 2026}}


def job_dict(job):
    return {'job_id':job.id,'submission_id':job.submission_id,'kind':job.kind,'status':job.state,'stage':job.stage,
        'attempt':job.attempts,'error':job.error,'created_at':job.created_at,'updated_at':job.updated_at,
        'result':job.result if job.state=='completed' else None}


@router.get('/capabilities')
def capabilities():
    from backend.services.template_service import disabled_template_ids
    s=get_settings()
    return {'max_file_bytes':s.max_file_size_mb*1024**2,'max_pdf_pages':s.max_pdf_pages,
        'max_page_pixels':s.max_page_pixels,'max_concurrent_uploads':s.max_uploads,
        'queue_enabled':s.queue_enabled,'archive_enabled':s.archive_enabled,
        'local_eviction_enabled':s.archive_evict_local,'candidate_page_exam_id':'external_exam_id_or_paper_code',
        'candidate_page_exams':{key:value['series'] for key,value in PAGE_LOOKUP_EXAMS.items()},
        # Layouts detected but deliberately not read or scored. Their pages
        # come back with no answers and no marks, so an integrator can route
        # them for manual handling instead of reading a zero as a real result.
        'withdrawn_templates':sorted(disabled_template_ids())}


@router.get('/jobs/{job_id}')
def get_job(job_id:int,db:Session=Depends(get_db)):
    job=db.get(ProcessingJob,job_id)
    if not job:raise HTTPException(404,'Job not found')
    return job_dict(job)


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id:int,db:Session=Depends(get_db)):
    job=db.get(ProcessingJob,job_id)
    if not job:raise HTTPException(404,'Job not found')
    if job.state in {'completed','failed'}:raise HTTPException(409,'Job is already terminal')
    if job.kind in {'delete','evict'}:raise HTTPException(409,'Storage cleanup cannot be cancelled; retry it if it fails')
    job.state='cancelled';job.stage='cancelled';job.updated_at=datetime.utcnow()
    if job.kind in {'process','legacy','exam'}:
        db.query(ExamSubmission).filter(ExamSubmission.id==job.submission_id,ExamSubmission.status.in_(['pending','processing'])).update({'status':'cancelled','processed_at':datetime.utcnow()})
    db.commit()
    return job_dict(job)


@router.post('/submission/{submission_id}/mark-jobs',status_code=202)
def enqueue_mark(submission_id:int,body:dict|None=None,db:Session=Depends(get_db)):
    from backend.db.models import AnswerKey
    from sqlalchemy import text
    db.rollback();db.execute(text('BEGIN IMMEDIATE'))
    sub=db.get(ExamSubmission,submission_id)
    if not sub:raise HTTPException(404,'Submission not found')
    if sub.status!='completed':raise HTTPException(409,'Submission must be completed')
    if not db.query(CandidateResult.id).filter_by(submission_id=submission_id).first():raise HTTPException(409,'Submission has no candidates')
    key=(body or {}).get('answer_key_id')
    if key is not None and (not isinstance(key,int) or isinstance(key,bool)):raise HTTPException(422,'answer_key_id must be an integer')
    if key is not None and db.get(AnswerKey,key) is None:raise HTTPException(404,'Answer key not found')
    existing=db.query(ProcessingJob).filter(ProcessingJob.submission_id==submission_id,ProcessingJob.kind.in_(['mark','review_mark']),ProcessingJob.state.in_(['pending','running'])).first()
    if existing:raise HTTPException(409,{'code':'marking_in_progress','job_id':existing.id})
    try:
        job=enqueue(db,'mark',submission_id,{'answer_key_id':key});db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,{'code':'marking_in_progress'})
    return job_dict(job)


@router.post('/jobs/{job_id}/retry',status_code=202)
def retry_job(job_id:int,db:Session=Depends(get_db)):
    job=db.get(ProcessingJob,job_id)
    if not job:raise HTTPException(404,'Job not found')
    if job.state!='failed':raise HTTPException(409,'Only failed jobs can be retried')
    if job.kind=='archive' and not get_settings().archive_enabled:raise HTTPException(409,'Spaces is disabled')
    sub=db.get(ExamSubmission,job.submission_id)
    if sub is None and job.kind!='delete':raise HTTPException(410,'Submission was deleted')
    if sub is not None and sub.status=='deleting' and job.kind!='delete':raise HTTPException(409,'Submission is being deleted')
    job.state='pending';job.owner=None;job.lease_until=None;job.published_at=None
    job.attempts=0;job.error=None;job.available_at=datetime.utcnow();job.updated_at=datetime.utcnow()
    if job.kind in {'process','legacy','exam'}:sub.status='pending';sub.error_message=None
    db.commit()
    return job_dict(job)


@router.post('/submission/{submission_id}/archive',status_code=202)
def enqueue_archive(submission_id:int,db:Session=Depends(get_db)):
    if not get_settings().archive_enabled:raise HTTPException(409,'Spaces is not enabled; local files are retained')
    from sqlalchemy import text
    db.rollback();db.execute(text('BEGIN IMMEDIATE'))
    sub=db.get(ExamSubmission,submission_id)
    if not sub:raise HTTPException(404,'Submission not found')
    if sub.status!='completed':raise HTTPException(409,'Submission must be completed')
    existing=db.query(ProcessingJob).filter(ProcessingJob.submission_id==submission_id,ProcessingJob.kind=='archive',ProcessingJob.state.in_(['pending','running'])).first()
    if existing:return job_dict(existing)
    job=enqueue(db,'archive',submission_id);db.commit();return job_dict(job)


@router.get('/candidate-page',responses={200:{'content':{'image/png':{}}}})
def candidate_page(
    exam_id:str=Query(...,description='External exam ID: 31 means SEAMO 2026 across all papers/layouts. Existing paper codes remain accepted.'),
    candidate_number:str=Query(...,description='Candidate number as a string; preserve leading zeros.'),
    submission_id:int|None=None,page_number:int|None=Query(None,ge=1),db:Session=Depends(get_db),
):
    """Find a candidate's source page within an exam series, without requiring marking or answer keys."""
    from backend.services.template_service import get_template_registry
    registry=get_template_registry()
    exam=PAGE_LOOKUP_EXAMS.get(exam_id)
    if exam is not None:
        ids=[t.id for t in registry.list_templates() if (t.brand,t.year)==(exam['brand'],exam['year'])]
    else:
        paper=registry.get(exam_id)
        if paper is None:raise HTTPException(422,'Unknown exam_id; use 31 for SEAMO 2026 or an existing paper code from /templates/all')
        # Layout inheritance and answer-key aliases can cross exam series.
        # Only explicit brand/year/paper metadata defines paper equivalence.
        ids=[t.id for t in registry.list_templates() if t.id==exam_id or (
            paper.brand and paper.year and paper.paper and
            (t.brand,t.year,t.paper)==(paper.brand,paper.year,paper.paper))]
    number=candidate_number.strip()
    if not number or len(number)>100:raise HTTPException(422,'candidate_number must be a nonempty string of at most 100 characters')
    query=db.query(CandidateResult).join(ExamSubmission).filter(
        func.trim(CandidateResult.candidate_number)==number,
        func.coalesce(CandidateResult.template_id,ExamSubmission.template_id).in_(ids),
    )
    if submission_id is not None:query=query.filter(CandidateResult.submission_id==submission_id)
    if page_number is not None:query=query.filter(CandidateResult.page_number==page_number)
    matches=query.order_by(CandidateResult.id).limit(101).all()
    if not matches:raise HTTPException(404,'Candidate not found for this exam')
    if len(matches)>1:
        raise HTTPException(409,{'code':'ambiguous_candidate_page','message':'Provide submission_id and, if needed, page_number.',
            'matches':[{'submission_id':c.submission_id,'candidate_id':c.id,'page_number':c.page_number} for c in matches[:100]],'truncated':len(matches)>100})
    candidate=matches[0]
    if not candidate.page_number:raise HTTPException(409,{'code':'page_not_ready'})
    from backend.api.routes import get_submission_page_image
    response=get_submission_page_image(candidate.submission_id,candidate.page_number,db)
    response.headers.update({'X-Submission-ID':str(candidate.submission_id),'X-Candidate-ID':str(candidate.id),'X-Page-Number':str(candidate.page_number)})
    return response


def queue_status(db,sid):
    job=db.query(ProcessingJob).filter_by(submission_id=sid,kind='process').order_by(ProcessingJob.id.desc()).first()
    archive=db.query(ProcessingJob).filter_by(submission_id=sid,kind='archive').order_by(ProcessingJob.id.desc()).first()
    if job is None:return {}
    position=None
    if job.state=='pending':position=1+db.query(ProcessingJob).filter(ProcessingJob.kind!='archive',ProcessingJob.state=='pending',ProcessingJob.available_at<job.available_at).count()
    return {'stage':'queued' if job.state=='pending' and job.stage=='queued' else job.stage,
        'queue_position':position,'pages_completed':int((job.payload or {}).get('next_page',0)),
        'attempt':job.attempts,'job_id':job.id,
        'archive_status':archive.state if archive else ('not_started' if get_settings().archive_enabled else 'disabled'),
        'archive_error':archive.error if archive else None}
