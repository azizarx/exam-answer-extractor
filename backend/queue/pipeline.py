"""Checkpointed, bounded-page processing. No deletion of accepted source files."""
from datetime import datetime
from pathlib import Path
import json
import os
import shutil
import time

import pymupdf

from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import ExamSubmission, CandidateResult, PageCheckpoint, ProcessingLog, StorageArtifact, MarkingRun, ProcessingJob
from backend.queue.runtime import assert_owned, enqueue
from backend.services.cancellation import check_cancelled
from backend.services.local_storage import get_local_storage


def artifact(db, sid, path, kind, page=None):
    storage = get_local_storage()
    relative = str(Path(path).resolve().relative_to(storage.base_path))
    row = db.query(StorageArtifact).filter_by(local_path=relative).first()
    if row is None:
        row=StorageArtifact(submission_id=sid,local_path=relative,kind=kind,page_number=page,
                            object_key=f'{get_settings().spaces_archive_prefix}/submissions/{sid}/{relative}')
        db.add(row)
    return row


def capacity_check(required_bytes=0, execution=None):
    settings=get_settings()
    from sqlalchemy import text
    from backend.db.models import UploadReservation
    with SessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        reserved=sum(row.reserved_bytes for row in db.query(UploadReservation).filter_by(state='receiving'))
        for job in db.query(ProcessingJob).filter_by(state='running'):
            if execution is None or job.id!=execution.id:
                reserved+=int((job.payload or {}).get('_workspace_bytes',0))
        if shutil.disk_usage(get_local_storage().base_path).free-reserved-required_bytes < settings.min_free_disk_gb*1024**3:
            raise OSError('Disk reserve reached; retained files have not been removed')
        if execution is not None:
            job=assert_owned(db,execution)
            job.payload={**(job.payload or {}),'_workspace_bytes':required_bytes}
        db.commit()


def candidate_row(sid, data):
    data=dict(data)
    for alias,target in {'candidate_no':'candidate_number','candidate_id':'candidate_number','student_number':'candidate_number','name':'candidate_name','student_name':'candidate_name'}.items():
        if not data.get(target) and data.get(alias):data[target]=data[alias]
    known={'page_number','candidate_name','candidate_number','country','paper_type','template_id','detection','answers','drawing_questions'}
    extra=dict(data.get('extra_fields') or {})
    for key,value in data.items():
        if key not in known and key not in {'extra_fields','confidence','is_blank','diagram_qs'}:extra[key]=value
    values={key:data.get(key) for key in known}
    return CandidateResult(submission_id=sid,extra_fields=extra,**values)


def process_unit(sid, execution):
    settings=get_settings(); storage=get_local_storage()
    with SessionLocal() as db:
        job=assert_owned(db,execution)
        sub=db.get(ExamSubmission,sid)
        if sub is None:raise FileNotFoundError('Submission was deleted')
        source=storage.resolve_within(sub.original_pdf_key)
        if source is None or not source.is_file():raise FileNotFoundError('Source PDF is missing')
        payload=dict(job.payload or {})
        payload.pop('_workspace_bytes',None)
        kind=job.kind
        template_id=sub.template_id
        filename=sub.filename
        start=int(payload.get('next_page',0))
        stage=payload.get('stage','pages')
        # Terminal state may have committed immediately before a worker restart.
        if sub.status=='completed':return True, job.result or {'submission_id':sid}
        sub.status='processing'
        job.stage='marking' if stage=='marking' else 'extracting'
        db.commit()
    if stage=='marking':
        from backend.services.marking_workflow import mark_submission_answers
        with SessionLocal() as db:
            assert_owned(db,execution)
            # An abandoned run from a previous claim cannot block this retry.
            db.query(MarkingRun).filter_by(submission_id=sid,status='processing').update({'status':'failed','error_message':'Worker interrupted; resuming from marking checkpoints','completed_at':datetime.utcnow()})
            db.commit()
            run=mark_submission_answers(db,sid)
            check_cancelled()
            assert_owned(db,execution)
            sub=db.get(ExamSubmission,sid)
            sub.status='completed';sub.processed_at=datetime.utcnow()
            result={'submission_id':sid,'marking_run_id':run.id,'marking_status':run.status}
            db.get(ProcessingJob,execution.id).result=result
            db.add(ProcessingLog(submission_id=sid,action='extract_complete',status='success',message='Queued extraction and marking finished'))
            if settings.archive_enabled:enqueue(db,'archive',sid)
            db.commit()
            return True, result
    capacity_check(execution=execution)
    work=storage.base_path/'work'/f'sub{sid}'
    work.mkdir(parents=True,exist_ok=True)
    pages_dir=storage.base_path/'pages'/f'sub{sid}'
    pages_dir.mkdir(parents=True,exist_ok=True)
    with pymupdf.open(source) as original:
        if original.needs_pass:raise ValueError('Password-protected PDFs are not supported')
        total=original.page_count
        if total<1 or total>settings.max_pdf_pages:raise ValueError(f'PDF page count must be between 1 and {settings.max_pdf_pages}')
        end=min(total,start+settings.queue_batch_pages)
        from backend.services.pdf_to_images import get_pdf_converter
        converter=get_pdf_converter()
        workspace_bytes=source.stat().st_size
        crop_workspace_bytes=0
        for number in range(start,end):
            page=original[number]
            pixels=(page.rect.width*converter.dpi/72)*(page.rect.height*converter.dpi/72)
            if pixels>settings.max_page_pixels:raise ValueError(f'Page {number+1} exceeds the raster pixel limit')
            crop_workspace_bytes+=int(pixels)*4
            workspace_bytes+=int(pixels)*8  # Conversion images plus bounded crop workspace.
        capacity_check(workspace_bytes,execution)
        segment=work/f'batch-{start}.pdf'
        if start<total:
            segment_tmp=segment.with_suffix('.pdf.part')
            with pymupdf.open() as document:
                document.insert_pdf(original,from_page=start,to_page=end-1)
                document.save(segment_tmp)
            os.replace(segment_tmp,segment)
    with SessionLocal() as db:
        assert_owned(db,execution)
        db.get(ExamSubmission,sid).pages_count=total
        artifact(db,sid,source,'source')
        db.commit()
    if start<total:
        from backend.services.template_extractor import TemplateExtractor, extract_pdf_auto
        crop_dir=work/f'crops-{start}'
        image_paths=converter.convert_from_file(str(segment),output_dir=str(work/f'images-{start}'))
        # Retain the exact conversion images, before extraction can fail.
        retained=[]
        for offset,path in enumerate(image_paths,start+1):
            target=pages_dir/f'{offset:06d}.png'
            os.replace(path,target);retained.append(str(target))
        capacity_check(crop_workspace_bytes,execution)
        if template_id:
            result=TemplateExtractor(template_id).extract_pdf(str(segment),retained,max_workers=settings.max_extraction_workers,diagram_crop_dir=crop_dir)
        else:
            result=extract_pdf_auto(str(segment),retained,max_workers=settings.max_extraction_workers,diagram_crop_dir=crop_dir)
        check_cancelled()
        final_crops=storage.diagram_crop_dir(sid)
        final_crops.mkdir(parents=True,exist_ok=True)
        with SessionLocal() as db:
            job=assert_owned(db,execution)
            for i,path in enumerate(retained,start+1):artifact(db,sid,path,'page',i)
            for data in result.get('candidates',[]):
                data=dict(data)
                local_page=int(data.get('page_number') or 1)
                global_page=start+local_page
                if not start<global_page<=end:raise ValueError('Extraction returned an invalid page index')
                data['page_number']=global_page
                for question,entry in (data.get('extra_fields',{}).get('diagram_crops',{}) or {}).items():
                    old=crop_dir/Path(entry.get('file','')).name
                    if old.is_file():
                        name=f'p{global_page}_q{int(question)}.png'
                        target=final_crops/name
                        os.replace(old,target);entry['file']=name
                        artifact(db,sid,target,'diagram',global_page)
                previous=db.query(PageCheckpoint).filter_by(submission_id=sid,page_number=global_page).first()
                if previous is None:
                    row=candidate_row(sid,data);db.add(row);db.flush()
                    db.add(PageCheckpoint(submission_id=sid,page_number=global_page,candidate_id=row.id,extraction=data))
            payload['next_page']=end
            job.payload=payload
            job.stage='extracting'
            db.add(ProcessingLog(submission_id=sid,action='page_progress',status='info',message=f'Processed {end} of {total} pages',extra_data={'current':end,'total':total,'page':end}))
            db.commit()
        # Only disposable batch PDFs are removed, never source or page artifacts.
        segment.unlink(missing_ok=True)
        if end<total:return False,None
    with SessionLocal() as db:
        job=assert_owned(db,execution)
        sub=db.get(ExamSubmission,sid)
        candidates=[p.extraction for p in db.query(PageCheckpoint).filter_by(submission_id=sid).order_by(PageCheckpoint.page_number)]
        from backend.services.json_generator import get_json_generator
        raw=get_json_generator().generate_with_validation(filename,{'candidates':candidates,'pages_processed':total,'pages_with_data':sum(bool(c.get('answers')) for c in candidates),'processing_time':(datetime.utcnow()-job.created_at).total_seconds()},None)
        saved=storage.save_json(raw,filename)
        sub.result_json_key=saved['relative_path']
        artifact(db,sid,saved['absolute_path'],'extraction')
        if kind in {'legacy','exam'}:
            result=json.loads(raw)
            if payload.get('marked'):
                from dataclasses import asdict
                from backend.db.models import AnswerKey
                from backend.services.marking_service import MarkingService
                from backend.services.marking_workflow import manifest_from_key
                for candidate in candidates:
                    key=db.get(AnswerKey,payload['answer_key_id']) if payload.get('answer_key_id') else db.query(AnswerKey).filter_by(template_id=candidate.get('template_id'),is_active=True).first()
                    if key:
                        graded=MarkingService(manifest_from_key(key)).mark(candidate.get('answers') or {})
                        candidate['marking']={**asdict(graded),'answer_key_id':key.id}
                result={'filename':filename,'template_id':template_id,'mode':'forced' if template_id else 'auto','total_candidates':len(candidates),'candidates':candidates}
            if kind=='exam':
                from backend.db.models import GeneratedJSON
                generated=GeneratedJSON(exam_id=payload['exam_id'],filename=Path(saved['relative_path']).name,file_path=saved['relative_path'])
                db.add(generated);db.flush()
                result={'id':generated.id,'exam_id':generated.exam_id,'filename':generated.filename,'file_path':generated.file_path,'created_at':generated.created_at.isoformat()}
            sub.status='completed';sub.processed_at=datetime.utcnow()
            job.result=result
            if settings.archive_enabled:enqueue(db,'archive',sid)
            db.commit()
            return True,result
        payload['stage']='marking';job.payload=payload;job.stage='marking'
        db.commit()
    return False,None


def run_unit(kind,sid,payload,execution):
    if kind in {'process','legacy','exam'}:return process_unit(sid,execution)
    if kind in {'delete','evict'}:
        from backend.services.artifact_lifecycle import delete_submission_artifacts, evict_submission
        result=(delete_submission_artifacts if kind=='delete' else evict_submission)(sid,execution)
        return 'retry_after' not in result,result
    if kind=='archive':
        from backend.services.artifact_storage import archive_submission
        result=archive_submission(sid,execution)
        if get_settings().archive_evict_local:
            from backend.services.artifact_lifecycle import schedule_eviction
            with SessionLocal() as db:
                assert_owned(db,execution)
                schedule_eviction(db,sid)
                db.commit()
        return True,result
    if kind=='mark':
        import asyncio
        from backend.api.marking_routes import mark_submission
        from backend.api.schemas import ManualRemarkRequest
        with SessionLocal() as db:
            db.query(MarkingRun).filter_by(submission_id=sid,status='processing').update({'status':'failed','error_message':'Worker interrupted; marking retry','completed_at':datetime.utcnow()})
            db.commit()
            result=asyncio.run(mark_submission(sid,ManualRemarkRequest(answer_key_id=payload.get('answer_key_id')),db))
            if get_settings().archive_enabled:enqueue(db,'archive',sid);db.commit()
            return True,result.model_dump(mode='json')
    if kind=='review_mark':
        from backend.services.marking_workflow import mark_submission_answers
        with SessionLocal() as db:
            db.query(MarkingRun).filter_by(submission_id=sid,status='processing').update({'status':'failed','error_message':'Worker interrupted; marking retry','completed_at':datetime.utcnow()})
            db.commit()
            run=mark_submission_answers(db,sid,only_candidate_ids=payload['candidate_ids'])
            if get_settings().archive_enabled:enqueue(db,'archive',sid);db.commit()
            return True,{'remarked':run.status=='completed','marking_run_id':run.id,'remark_error':run.error_message}
    raise ValueError(f'Unknown job kind: {kind}')
