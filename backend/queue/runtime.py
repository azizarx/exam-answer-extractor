"""Database-owned execution leases, outbox dispatch and bounded recovery."""
from contextvars import ContextVar
from datetime import datetime, timedelta
import logging
import threading
import time
import uuid

from sqlalchemy import or_
from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import ProcessingJob, ExamSubmission, JobCheckpoint
from backend.services.cancellation import ExtractionCancelled

logger = logging.getLogger(__name__)
current_job = ContextVar('queue_job', default=None)


def next_submission_id(db):
    """Call under the admission write lock; deleted IDs remain in the job ledger.

    SQLite's existing INTEGER PRIMARY KEY can reuse deleted IDs. Reuse would
    alias old upload receipts and archived object namespaces to a new upload.
    """
    from sqlalchemy import func
    latest=db.query(func.max(ExamSubmission.id)).scalar() or 0
    retained=db.query(func.max(ProcessingJob.submission_id)).scalar() or 0
    return max(latest,retained)+1


def enqueue(db, kind, submission_id=None, payload=None):
    job = ProcessingJob(kind=kind, submission_id=submission_id, payload=payload or {})
    db.add(job)
    db.flush()
    return job


def assert_owned(db, execution):
    if execution is None:
        return
    row = db.query(ProcessingJob).filter(
        ProcessingJob.id == execution.id, ProcessingJob.owner == execution.owner,
        ProcessingJob.generation == execution.generation,
        ProcessingJob.state == 'running', ProcessingJob.lease_until > datetime.utcnow(),
    ).first()
    if row is None:
        raise ExtractionCancelled()
    return row


class Execution:
    def __init__(self, job):
        self.id, self.owner, self.generation = job.id, job.owner, job.generation
        self.submission_id = job.submission_id
        self.kind = job.kind
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.next_check = 0
        self.lock = threading.Lock()

    def check(self):
        if self.lost.is_set():
            raise ExtractionCancelled()
        with self.lock:
            if time.monotonic() < self.next_check:
                return
            with SessionLocal() as db:
                assert_owned(db, self)
                if self.submission_id:
                    sub = db.get(ExamSubmission, self.submission_id)
                    if self.kind != 'delete' and (sub is None or sub.status in {'cancelled','deleting'}):
                        raise ExtractionCancelled()
            self.next_check = time.monotonic() + .5

    def heartbeat(self):
        while not self.stop.wait(10):
            try:
                with SessionLocal() as db:
                    now = datetime.utcnow()
                    changed = db.query(ProcessingJob).filter(
                        ProcessingJob.id == self.id, ProcessingJob.owner == self.owner,
                        ProcessingJob.generation == self.generation, ProcessingJob.state == 'running',
                        ProcessingJob.lease_until > now,
                    ).update({'lease_until': now + timedelta(seconds=get_settings().queue_lease_seconds), 'updated_at': now})
                    db.commit()
                    if not changed:
                        self.lost.set()
                        return
            except Exception:
                # Never extend a lease on an unverified database write.
                logger.exception('Job heartbeat failed: %s', self.id)


def execute(job_id):
    with SessionLocal() as db:
        now = datetime.utcnow()
        owner = uuid.uuid4().hex
        claimed = db.query(ProcessingJob).filter(
            ProcessingJob.id == job_id, ProcessingJob.state == 'pending',
            ProcessingJob.available_at <= now,
        ).update({
            'state': 'running', 'owner': owner,
            'generation': ProcessingJob.generation + 1, 'attempts': ProcessingJob.attempts + 1,
            'lease_until': now + timedelta(seconds=get_settings().queue_lease_seconds), 'updated_at': now,
        }, synchronize_session=False)
        db.commit()
        if not claimed:
            return
        job = db.get(ProcessingJob, job_id)
        execution = Execution(job)
        kind, payload, sid = job.kind, dict(job.payload or {}), job.submission_id
    token = current_job.set(execution)
    heart = threading.Thread(target=execution.heartbeat, daemon=True)
    heart.start()
    try:
        execution.check()
        from backend.queue.pipeline import run_unit
        from backend.services.artifact_lifecycle import artifact_lock
        with artifact_lock(sid, exclusive=kind in {'process','legacy','exam','delete','evict'}):
            complete, result = run_unit(kind, sid, payload, execution)
        with SessionLocal() as db:
            row = assert_owned(db, execution)
            row.state = 'completed' if complete else 'pending'
            row.result = result if complete else row.result
            row.stage = 'completed' if complete else row.stage
            row.owner = None
            row.lease_until = None
            row.published_at = None
            row.available_at = datetime.utcnow()+timedelta(seconds=int((result or {}).get('retry_after',0)) if not complete else 0)
            row.updated_at = datetime.utcnow()
            row.attempts = 0 if not complete else row.attempts
            row.error = None
            db.commit()
    except ExtractionCancelled:
        with SessionLocal() as db:
            row=db.query(ProcessingJob).filter(
                ProcessingJob.id == job_id, ProcessingJob.owner == owner,
                ProcessingJob.state == 'running',
            ).first()
            if row:
                sub=db.get(ExamSubmission,sid)
                cancelled=kind!='delete' and (sub is None or sub.status in {'cancelled','deleting'})
                row.state='cancelled' if cancelled else ('failed' if row.attempts>=get_settings().queue_max_attempts else 'pending')
                row.owner=None;row.lease_until=None;row.published_at=None
                row.available_at=datetime.utcnow()
                row.error=None if cancelled else 'Execution lease expired; resuming committed checkpoints'
                if cancelled:row.stage='cancelled'
                if row.state=='failed' and kind in {'process','legacy','exam'}:
                    sub.status='failed';sub.error_message=row.error
            db.commit()
    except BlockingIOError:
        # A fenced native call may still hold the old execution's file lock.
        # Wait without consuming the failure budget or touching its artifacts.
        with SessionLocal() as db:
            row=db.query(ProcessingJob).filter_by(id=job_id,owner=owner,state='running').first()
            if row:
                row.state='pending';row.owner=None;row.lease_until=None;row.published_at=None
                row.available_at=datetime.utcnow()+timedelta(seconds=5)
                row.attempts=max(0,row.attempts-1);row.error='Artifacts are in use; waiting for the previous reader or worker'
                db.commit()
    except BaseException as exc:
        logger.exception('Job %s failed', job_id)
        with SessionLocal() as db:
            row = db.get(ProcessingJob, job_id)
            if row and row.owner == owner and row.state == 'running':
                permanent = isinstance(exc, (ValueError, FileNotFoundError))
                terminal = permanent or row.attempts >= get_settings().queue_max_attempts
                row.state = 'failed' if terminal else 'pending'
                row.error = f'{type(exc).__name__}: {str(exc)[:500]}'
                row.owner = None
                row.lease_until = None
                row.published_at = None
                row.available_at = datetime.utcnow() + timedelta(seconds=min(300, 10 * 2 ** row.attempts))
                row.updated_at = datetime.utcnow()
                if terminal and sid and kind in {'process','legacy','exam'}:
                    db.query(ExamSubmission).filter(ExamSubmission.id == sid, ExamSubmission.status.in_(['pending','processing'])).update({'status':'failed','error_message':row.error,'processed_at':datetime.utcnow()})
                db.commit()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        execution.stop.set()
        heart.join(timeout=2)
        current_job.reset(token)


def dispatch_once():
    """The job ledger itself is the transactional outbox; publication is replayable."""
    from backend.queue.app import app
    clean_expired_uploads()
    now = datetime.utcnow()
    with SessionLocal() as db:
        expired = db.query(ProcessingJob).filter(ProcessingJob.state == 'running', ProcessingJob.lease_until < now).all()
        for job in expired:
            job.state = 'failed' if job.attempts >= get_settings().queue_max_attempts else 'pending'
            job.error = 'Worker lease expired'
            job.owner = None
            job.lease_until = None
            job.published_at = None
            job.available_at = now
            if job.state == 'failed' and job.kind in {'process','legacy','exam'}:
                db.query(ExamSubmission).filter(ExamSubmission.id == job.submission_id, ExamSubmission.status.in_(['pending','processing'])).update({'status':'failed','error_message':job.error})
        db.commit()
        # Republish missing deliveries. Duplicate messages cannot claim a running job.
        ids = db.query(ProcessingJob.id, ProcessingJob.kind).filter(
            ProcessingJob.state == 'pending', ProcessingJob.available_at <= now,
            or_(ProcessingJob.published_at.is_(None), ProcessingJob.published_at < now-timedelta(seconds=60)),
        ).order_by(ProcessingJob.available_at, ProcessingJob.id).limit(20).all()
        for job_id, kind in ids:
            app.send_task('aimarker.execute', args=[job_id], queue='archive' if kind in {'archive','delete','evict'} else 'process')
            db.query(ProcessingJob).filter(ProcessingJob.id == job_id, ProcessingJob.state == 'pending').update({'published_at':now})
            db.commit()


def checkpoint_get(key):
    execution = current_job.get()
    if execution is None:
        return None
    with SessionLocal() as db:
        row = db.query(JobCheckpoint).filter_by(job_id=execution.id, key=key).first()
        return row.value if row else None


def checkpoint_put(key, value):
    execution = current_job.get()
    if execution is None:
        return
    with SessionLocal() as db:
        assert_owned(db, execution)
        row = db.query(JobCheckpoint).filter_by(job_id=execution.id, key=key).first()
        if row is None:
            db.add(JobCheckpoint(job_id=execution.id, key=key, value=value))
        else:
            row.value = value
        db.commit()


async def wait_job(job_id):
    import asyncio
    from fastapi import HTTPException
    deadline = time.monotonic() + get_settings().queue_sync_wait_seconds
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job.state == 'completed':
                return job.result
            if job.state in {'failed','cancelled'}:
                raise HTTPException(409, {'code':job.state, 'job_id':job_id, 'message':job.error})
        await asyncio.sleep(.5)
    raise HTTPException(504, {'code':'job_still_running','job_id':job_id,'status_url':f'/jobs/{job_id}'})


def clean_expired_uploads():
    from backend.db.models import UploadReservation
    from backend.services.local_storage import get_local_storage
    from pathlib import Path
    storage=get_local_storage()
    with SessionLocal() as db:
        rows=db.query(UploadReservation).filter(UploadReservation.state=='receiving',UploadReservation.expires_at<datetime.utcnow()).limit(20).all()
        for row in rows:
            # Only generated upload files owned by this reservation are eligible.
            if len(row.id)!=32 or any(c not in '0123456789abcdef' for c in row.id):continue
            relative=f'uploads/{row.id}.pdf'
            if db.query(ExamSubmission.id).filter_by(original_pdf_key=relative).first():continue
            path=storage.base_path/relative
            path.with_suffix('.part').unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            db.delete(row)
        db.commit()
