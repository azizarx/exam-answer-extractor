"""Explicit deletion and opt-in eviction of verified, idle submission artifacts."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import fcntl
import shutil

from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import (ExamSubmission, ProcessingJob, StorageArtifact,
    PageCheckpoint, JobCheckpoint, UploadReservation, ProcessingLog)
from backend.queue.runtime import assert_owned
from backend.services.local_storage import get_local_storage


@contextmanager
def artifact_lock(sid, *, exclusive=False):
    """Single-host file locks also fence a worker whose database lease expired."""
    folder=get_local_storage().base_path/'locks'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/f'sub{int(sid)}.lock').open('a+b') as handle:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def evict_submission(sid, execution):
    settings=get_settings()
    if not settings.archive_evict_local:
        return {'submission_id':sid,'evicted':0,'reason':'Local preservation enabled'}
    if sid<=settings.archive_preserve_through_submission_id:
        return {'submission_id':sid,'evicted':0,'reason':'Pre-rollout submission is pinned locally'}
    storage=get_local_storage();removed=0
    with SessionLocal() as db:
        assert_owned(db,execution)
        sub=db.get(ExamSubmission,sid)
        if not sub or sub.status!='completed':raise ValueError('Only completed submissions are eligible for eviction')
        if db.query(ProcessingJob.id).filter(ProcessingJob.submission_id==sid,
            ProcessingJob.kind!='evict',ProcessingJob.id!=execution.id,ProcessingJob.state.in_(['pending','running'])).first():
            return {'retry_after':60,'reason':'Submission has active work'}
        archive=db.query(ProcessingJob).filter_by(submission_id=sid,kind='archive',state='completed').order_by(ProcessingJob.id.desc()).first()
        if not archive:raise ValueError('A completed, verified archive is required')
        cutoff=datetime.utcnow()-timedelta(hours=settings.archive_cache_hours)
        rows=db.query(StorageArtifact).filter_by(submission_id=sid).all()
        if any(r.state!='verified' or not r.verified_at for r in rows):raise ValueError('Every artifact must be verified before eviction')
        from backend.services.artifact_storage import spaces, file_hash
        client=spaces()
        for row in rows:
            if row.verified_at>cutoff:continue
            path=storage.resolve_within(row.local_path)
            if not path or not path.is_file():continue
            # Restored cache files also receive the full cache interval.
            if datetime.utcfromtimestamp(path.stat().st_mtime)>cutoff:continue
            if path.stat().st_size!=row.size or file_hash(path)!=row.sha256:
                raise OSError('Local artifact changed; refusing eviction')
            head=client.head_object(Bucket=settings.spaces_bucket,Key=row.object_key)
            if head['ContentLength']!=row.size or head.get('Metadata',{}).get('sha256')!=row.sha256:
                raise OSError('Verified remote copy is unavailable; retaining local file')
            assert_owned(db,execution)
            path.unlink();removed+=1
    return {'submission_id':sid,'evicted':removed}


def schedule_eviction(db,sid):
    settings=get_settings()
    if not settings.archive_evict_local or sid<=settings.archive_preserve_through_submission_id:return
    from backend.queue.runtime import enqueue
    job=db.query(ProcessingJob).filter_by(submission_id=sid,kind='evict',state='pending').first()
    if job is None:job=enqueue(db,'evict',sid)
    job.available_at=datetime.utcnow()+timedelta(hours=settings.archive_cache_hours)
    job.published_at=None
    return job


def delete_submission_artifacts(sid,execution):
    """Only runs after an explicit DELETE; tombstone prevents new work."""
    storage=get_local_storage()
    with SessionLocal() as db:
        assert_owned(db,execution)
        sub=db.get(ExamSubmission,sid)
        if sub is None:return {'submission_id':sid,'deleted':True}
        if sub.status!='deleting':raise ValueError('Deletion requires a submission tombstone')
        rows=db.query(StorageArtifact).filter_by(submission_id=sid).all()
        had_archive=db.query(ProcessingJob.id).filter(ProcessingJob.submission_id==sid,ProcessingJob.kind=='archive',ProcessingJob.attempts>0).first() is not None
        retained=[(r.local_path,r.object_key,r.upload_id,r.state) for r in rows]
        paths={p for p in [sub.original_pdf_key,sub.result_json_key] if p}
        paths.update(r[0] for r in retained)
    if retained and (had_archive or any(state=='verified' or upload for _,_,upload,state in retained)):
        from backend.services.artifact_storage import spaces
        settings=get_settings();client=spaces();prefix=f'{settings.spaces_archive_prefix}/submissions/{sid}/'
        for _,key,upload,_ in retained:
            if key and not key.startswith(prefix):raise ValueError('Artifact is outside the submission namespace')
            if upload:
                try:client.abort_multipart_upload(Bucket=settings.spaces_bucket,Key=key,UploadId=upload)
                except client.exceptions.ClientError as exc:
                    if exc.response.get('Error',{}).get('Code')!='NoSuchUpload':raise
        # Reconcile uploads created just before a worker died, before its upload
        # ID could be committed. Never inspect or abort another namespace.
        for page in client.get_paginator('list_multipart_uploads').paginate(Bucket=settings.spaces_bucket,Prefix=prefix):
            for pending in page.get('Uploads',[]):
                if not pending['Key'].startswith(prefix):continue
                try:client.abort_multipart_upload(Bucket=settings.spaces_bucket,Key=pending['Key'],UploadId=pending['UploadId'])
                except client.exceptions.ClientError as exc:
                    if exc.response.get('Error',{}).get('Code')!='NoSuchUpload':raise
        # Includes revision manifests and objects completed just before a crash.
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=settings.spaces_bucket,Prefix=prefix):
            for obj in page.get('Contents',[]):
                from backend.services.cancellation import check_cancelled
                check_cancelled()
                client.delete_object(Bucket=settings.spaces_bucket,Key=obj['Key'])
    for relative in paths:
        path=storage.resolve_within(relative)
        if path:path.unlink(missing_ok=True)
    for folder in [storage.base_path/'pages'/f'sub{sid}',storage.diagram_crop_dir(sid),storage.base_path/'work'/f'sub{sid}']:
        if folder.exists():shutil.rmtree(folder)
    with SessionLocal() as db:
        assert_owned(db,execution)
        job_ids=[row[0] for row in db.query(ProcessingJob.id).filter_by(submission_id=sid)]
        db.query(JobCheckpoint).filter(JobCheckpoint.job_id.in_(job_ids)).delete(synchronize_session=False)
        for model in [StorageArtifact,PageCheckpoint,ProcessingLog]:
            db.query(model).filter_by(submission_id=sid).delete(synchronize_session=False)
        # Keep the idempotency receipt: a replay returns 410 instead of resurrecting a deleted upload.
        db.query(ProcessingJob).filter(ProcessingJob.submission_id==sid,ProcessingJob.id!=execution.id).update({'payload':{},'result':None},synchronize_session=False)
        sub=db.get(ExamSubmission,sid)
        if sub:db.delete(sub)
        db.commit()
    return {'submission_id':sid,'deleted':True}
