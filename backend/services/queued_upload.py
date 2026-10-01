"""One-copy bounded multipart upload on persistent disk, before form spooling."""
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import shutil
import uuid

import anyio
from fastapi import HTTPException
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import func, text

from backend.config import get_settings
from backend.db.models import UploadReservation, ExamSubmission, ProcessingLog, ProcessingJob
from backend.queue.runtime import enqueue
from backend.services.local_storage import get_local_storage


class PDFReceiver:
    def __init__(self, boundary, path, limit):
        self.limit, self.size, self.parts, self.ended = limit, 0, 0, False
        self.filename = None
        self.fields = {}
        self.field = None
        self.text = bytearray()
        self.digest = hashlib.sha256()
        self.file = open(path, 'xb')
        self.headers = {}
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.header_bytes = 0
        self.parser = MultipartParser(boundary, {
            'on_part_begin': self.begin,
            'on_header_field': self.header_field,
            'on_header_value': self.header_value_data,
            'on_header_end': self.header_end,
            'on_headers_finished': self.headers_finished,
            'on_part_data': self.data,
            'on_end': self.end,
        })

    def begin(self):
        self.parts += 1
        self.headers = {}
        self.header_bytes = 0
        self.field = None
        self.text.clear()
        if self.parts > 2:
            raise HTTPException(400, 'Too many multipart fields')

    def header_field(self, data, start, end):
        self.header_bytes += end-start
        if self.header_bytes > 16384:
            raise HTTPException(400, 'Multipart headers too large')
        self.header_name.extend(data[start:end])

    def header_value_data(self, data, start, end):
        self.header_bytes += end-start
        if self.header_bytes > 16384:
            raise HTTPException(400, 'Multipart headers too large')
        self.header_value.extend(data[start:end])

    def header_end(self):
        self.headers[bytes(self.header_name).lower()] = bytes(self.header_value)
        self.header_name.clear(); self.header_value.clear()

    def headers_finished(self):
        _, options = parse_options_header(self.headers.get(b'content-disposition',b''))
        if options.get(b'name') == b'mark_request' and b'filename' not in options:
            if 'mark_request' in self.fields:raise HTTPException(400, 'Duplicate mark_request')
            self.field = 'mark_request'
            self.fields['mark_request'] = ''
            return
        if options.get(b'name') != b'file' or b'filename' not in options or self.filename is not None:
            raise HTTPException(422, 'Required multipart PDF field: file')
        self.field = 'file'
        self.filename = Path(options[b'filename'].decode('utf-8',errors='replace').replace('\\','/')).name
        if not self.filename.lower().endswith('.pdf'):
            raise HTTPException(400, 'Only PDF files are allowed')

    def data(self, data, start, end):
        chunk = data[start:end]
        if self.field == 'mark_request':
            self.text.extend(chunk)
            if len(self.text)>4096:raise HTTPException(400, 'mark_request is too large')
            self.fields['mark_request'] = self.text.decode('utf-8')
            return
        if self.field != 'file':raise HTTPException(400, 'Invalid multipart field')
        self.size += len(chunk)
        if self.size > self.limit:
            raise HTTPException(413, 'PDF exceeds the maximum file size')
        self.digest.update(chunk)
        self.file.write(chunk)

    def end(self):
        self.ended = True

    def finish(self):
        self.parser.finalize()
        if not self.ended or not self.filename or not self.size:
            raise HTTPException(400, 'Incomplete or empty multipart PDF')
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()


def upload_response(submission):
    return dict(status='success', message='PDF uploaded successfully. Processing queued.',
                submission_id=submission.id,filename=submission.filename,storage_path=submission.original_pdf_key)


async def receive_upload(request, db, template_id=None, *, kind='process', payload=None):
    import json
    settings = get_settings()
    limit = settings.max_file_size_mb * 1024**2
    content_type, options = parse_options_header(request.headers.get('content-type',''))
    if content_type != b'multipart/form-data' or not options.get(b'boundary') or len(options[b'boundary']) > 200:
        raise HTTPException(400, 'Expected multipart/form-data with a boundary')
    declared = request.headers.get('content-length')
    if declared:
        try:
            if int(declared) > limit + 1024**2:
                raise HTTPException(413, 'Request exceeds the upload limit')
        except ValueError:
            raise HTTPException(400, 'Invalid Content-Length')
    idem = request.headers.get('idempotency-key')
    if idem and kind in {'document','correction'}:
        raise HTTPException(400,'Idempotency-Key is supported on /upload and /extract/json routes')
    if idem and len(idem)>200:
        raise HTTPException(400, 'Idempotency-Key must be at most 200 characters')
    checksum = request.headers.get('x-content-sha256','').lower()
    if checksum and (len(checksum)!=64 or any(c not in '0123456789abcdef' for c in checksum)):
        raise HTTPException(400, 'Invalid X-Content-SHA256')
    storage = get_local_storage()
    identifier = uuid.uuid4().hex
    relative = f'uploads/{identifier}.pdf'
    destination = storage.base_path / relative
    partial = destination.with_suffix('.part')
    replay = None
    signature = hashlib.sha256(json.dumps([kind,template_id,payload],sort_keys=True).encode()).hexdigest()
    # No network or file streaming inside this transaction. Serialize reservations.
    db.rollback()
    if db.bind.dialect.name == 'sqlite':
        db.execute(text('BEGIN IMMEDIATE'))
    try:
        if idem:
            previous = db.query(UploadReservation).filter_by(idempotency_key=idem).first()
            if previous:
                if previous.state != 'completed':
                    raise HTTPException(409, {'code':'upload_in_progress','message':'This idempotency key already has an upload'})
                replay = (previous.submission_id, previous.sha256, previous.request_signature)
                if checksum and kind=='process':
                    if signature != previous.request_signature:
                        raise HTTPException(409, 'Idempotency key belongs to different processing options')
                    if checksum != previous.sha256:
                        raise HTTPException(409, 'Idempotency key belongs to different content')
                    sub = db.get(ExamSubmission, previous.submission_id)
                    if sub is None:
                        raise HTTPException(410, 'Original submission was deleted')
                    result = upload_response(sub)
                    db.rollback()
                    return result, None
        active = db.query(UploadReservation).filter_by(state='receiving').all()
        # Expiry is handled by the dispatcher, not by assuming an upload is idle.
        if len(active) >= settings.max_uploads:
            raise HTTPException(429, {'code':'upload_capacity','message':'Upload slots are occupied'}, headers={'Retry-After':'30'})
        reserved = sum(r.reserved_bytes for r in active)
        reserved += sum(int((r.payload or {}).get('_workspace_bytes',0)) for r in db.query(ProcessingJob).filter_by(state='running'))
        if shutil.disk_usage(storage.base_path).free - reserved - limit < settings.min_free_disk_gb * 1024**3:
            raise HTTPException(503, {'code':'disk_capacity','message':'Insufficient staging space'},headers={'Retry-After':'60'})
        upload_deadline = datetime.utcnow()+timedelta(hours=6)
        reservation = UploadReservation(id=identifier,idempotency_key=None if replay else idem,
            reserved_bytes=limit,local_path=str(partial),request_signature=signature,expires_at=upload_deadline)
        db.add(reservation); db.commit()
    except BaseException:
        db.rollback()
        raise
    receiver = None
    committed = False
    try:
        receiver = PDFReceiver(options[b'boundary'], partial, limit)
        received = 0
        async for chunk in request.stream():
            if datetime.utcnow() > upload_deadline:
                raise HTTPException(408, 'Upload exceeded the six-hour deadline')
            received += len(chunk)
            if received > limit + 1024**2:
                raise HTTPException(413, 'Multipart request exceeds the upload limit')
            await anyio.to_thread.run_sync(receiver.parser.write, chunk)
        await anyio.to_thread.run_sync(receiver.finish)
        digest = receiver.digest.hexdigest()
        if 'mark_request' in receiver.fields:
            if kind != 'legacy' or not (payload or {}).get('marked'):
                raise HTTPException(400, 'mark_request is only accepted on /extract/json/mark')
            import json
            try:
                selection=json.loads(receiver.fields['mark_request'])
                if not isinstance(selection,dict) or set(selection)!={'answer_key_id'} or type(selection['answer_key_id']) is not int:
                    raise ValueError()
            except (ValueError,TypeError):raise HTTPException(400, 'mark_request must contain only an integer answer_key_id')
            from backend.db.models import AnswerKey
            key=db.get(AnswerKey,selection['answer_key_id'])
            if key is None:raise HTTPException(404, 'Answer key not found')
            if template_id and key.template_id!=template_id:raise HTTPException(409,'Answer key does not match template')
            payload={**(payload or {}),'answer_key_id':key.id}
        if checksum and digest != checksum:
            raise HTTPException(400, 'PDF checksum does not match X-Content-SHA256')
        if replay:
            signature = hashlib.sha256(json.dumps([kind,template_id,payload],sort_keys=True).encode()).hexdigest()
            if signature != replay[2]:
                raise HTTPException(409, 'Idempotency key belongs to different processing options')
            if digest != replay[1]:
                raise HTTPException(409, 'Idempotency key belongs to different content')
            sub = db.get(ExamSubmission, replay[0])
            if sub is None:
                raise HTTPException(410, 'Original submission was deleted')
            return upload_response(sub), None
        os.replace(partial,destination)
        directory_fd = os.open(destination.parent,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
        # File is durable before job/outbox commit; dispatcher never sees partial PDFs.
        if kind in {'document','correction'}:
            from backend.db.models import Exam, ExamDocument
            from backend.api.schemas import ExamResponse, ExamDocumentResponse
            exam=db.get(Exam,payload['exam_id'])
            if exam is None:raise HTTPException(404,'Exam not found')
            if kind=='correction':
                exam.correction_pdf_path=relative
                result=ExamResponse.model_validate(exam).model_dump(mode='json')
            else:
                def page_count():
                    import pymupdf
                    with pymupdf.open(destination) as pdf:
                        if pdf.needs_pass:raise HTTPException(400,'Password-protected PDFs are not supported')
                        return pdf.page_count
                pages=await anyio.to_thread.run_sync(page_count)
                if not 0<pages<=settings.max_pdf_pages:raise HTTPException(400,'PDF exceeds the page-count limit')
                document=ExamDocument(exam_id=exam.id,country=payload.get('country'),file_path=relative,pages_count=pages)
                db.add(document);db.flush()
                result=ExamDocumentResponse.model_validate(document).model_dump(mode='json')
            row=db.get(UploadReservation,identifier)
            row.state='completed';row.sha256=digest;row.local_path=relative
            db.commit();committed=True
            return result,None
        from backend.queue.runtime import next_submission_id
        db.rollback();db.execute(text('BEGIN IMMEDIATE'))
        sub = ExamSubmission(id=next_submission_id(db),filename=receiver.filename,original_pdf_key=relative,template_id=template_id,status='pending')
        db.add(sub); db.flush()
        job = enqueue(db,kind,sub.id,payload)
        row=db.get(UploadReservation,identifier)
        row.request_signature=hashlib.sha256(json.dumps([kind,template_id,payload],sort_keys=True).encode()).hexdigest()
        row.state='completed';row.sha256=digest;row.submission_id=sub.id;row.local_path=relative
        db.add(ProcessingLog(submission_id=sub.id,action='upload',status='success',message='PDF accepted into durable queue'))
        db.commit()
        committed=True
        return upload_response(sub), job.id
    finally:
        if receiver and not receiver.file.closed:receiver.file.close()
        if not committed:
            partial.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            db.rollback()
            row=db.get(UploadReservation,identifier)
            if row:db.delete(row);db.commit()
