"""Private artifact archive; verified copies precede optional local eviction."""
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import tempfile

from fastapi import HTTPException
from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import StorageArtifact, ExamSubmission, MarkingRun
from backend.services.local_storage import get_local_storage
from backend.services.cancellation import check_cancelled
from backend.queue.runtime import assert_owned


def spaces():
    import boto3
    from botocore.config import Config
    s=get_settings()
    if not all([s.spaces_endpoint,s.spaces_bucket,s.spaces_key,s.spaces_secret]):
        raise RuntimeError('Spaces credentials and bucket are not configured')
    return boto3.client('s3',endpoint_url=s.spaces_endpoint,region_name=s.spaces_region,
        aws_access_key_id=s.spaces_key,aws_secret_access_key=s.spaces_secret,
        config=Config(connect_timeout=10,read_timeout=60,retries={'max_attempts':3}))


def file_hash(path):
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            check_cancelled();digest.update(chunk)
    return digest.hexdigest()


def ensure_local(relative):
    """Restore a verified remote artifact atomically; readers never see partial files."""
    storage=get_local_storage()
    path=storage.resolve_within(relative)
    if path is None:raise FileNotFoundError('Invalid artifact path')
    if path.is_file():return path
    with SessionLocal() as db:
        row=db.query(StorageArtifact).filter_by(local_path=relative,state='verified').first()
        if not row:raise FileNotFoundError('Artifact is not retained')
        key,size,digest,sid=row.object_key,row.size,row.sha256,row.submission_id
    import shutil
    if shutil.disk_usage(storage.base_path).free-size < get_settings().min_free_disk_gb*1024**3:
        raise OSError('Insufficient cache space')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.restore')
    os.close(fd)
    try:
        spaces().download_file(get_settings().spaces_bucket,key,tmp)
        if os.path.getsize(tmp)!=size or file_hash(tmp)!=digest:raise OSError('Archived artifact checksum mismatch')
        os.replace(tmp,path)
        from backend.services.artifact_lifecycle import schedule_eviction
        with SessionLocal() as db:
            schedule_eviction(db,sid);db.commit()
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def artifact_response(relative, media_type='image/png', headers=None):
    """Stream remote images directly; source PDFs are never needed for archived pages."""
    from fastapi.responses import StreamingResponse
    storage=get_local_storage()
    path=storage.resolve_within(relative)
    if path:
        try: handle=path.open('rb')
        except FileNotFoundError: handle=None
        if handle:
            # Open before returning: an explicit delete/eviction cannot truncate
            # a response whose file descriptor is already pinned by the reader.
            def local_chunks():
                try:
                    while chunk:=handle.read(1024*1024):yield chunk
                finally:handle.close()
            return StreamingResponse(local_chunks(),media_type=media_type,
                headers={**(headers or {}),'Content-Length':str(os.fstat(handle.fileno()).st_size)})
    with SessionLocal() as db:
        row=db.query(StorageArtifact).filter_by(local_path=relative,state='verified').first()
        if not row:raise HTTPException(410,'Requested artifact is no longer retained')
        key=row.object_key
    try:body=spaces().get_object(Bucket=get_settings().spaces_bucket,Key=key)['Body']
    except Exception:raise HTTPException(503,'Archived storage is temporarily unavailable')
    def chunks():
        try:
            for chunk in body.iter_chunks(chunk_size=1024*1024):yield chunk
        finally:body.close()
    return StreamingResponse(chunks(),media_type=media_type,headers=headers)


def archive_submission(sid,execution):
    settings=get_settings()
    if not settings.archive_enabled:raise ValueError('Spaces archiving is disabled; local files retained')
    client=spaces();bucket=settings.spaces_bucket
    storage=get_local_storage()
    from backend.queue.pipeline import artifact
    with SessionLocal() as db:
        assert_owned(db,execution)
        sub=db.get(ExamSubmission,sid)
        if sub is None:raise FileNotFoundError('Submission deleted')
        # Register historical assets without changing or deleting their originals.
        for key,kind in [(sub.original_pdf_key,'source'),(sub.result_json_key,'extraction')]:
            if key:
                path=storage.resolve_within(key)
                if path and path.is_file():artifact(db,sid,path,kind)
        for folder,kind in [(storage.base_path/'pages'/f'sub{sid}','page'),(storage.diagram_crop_dir(sid),'diagram')]:
            if folder.exists():
                for path in folder.glob('*.png'):artifact(db,sid,path,kind)
        latest=db.query(MarkingRun).filter_by(submission_id=sid).order_by(MarkingRun.id.desc()).first()
        if latest and latest.status=='completed':
            import asyncio
            from backend.api.marking_routes import get_marked_json
            try:
                response=asyncio.run(get_marked_json(sid,db))
                target=storage.results_path/f'sub{sid}-marking-{latest.id}.json'
                target.write_bytes(response.body)
                artifact(db,sid,target,'marked')
            except HTTPException as exc:
                if exc.status_code!=409:raise
        db.commit()
        ids=[r.id for r in db.query(StorageArtifact).filter_by(submission_id=sid).all()]
    for artifact_id in ids:
        check_cancelled()
        with SessionLocal() as db:
            assert_owned(db,execution)
            row=db.get(StorageArtifact,artifact_id)
            if row.state=='verified':continue
            path=storage.resolve_within(row.local_path)
            if not path or not path.is_file():raise FileNotFoundError('Local artifact missing before archival verification')
            digest=file_hash(path);size=path.stat().st_size
            if row.sha256 and row.sha256!=digest:raise ValueError('Artifact changed during archive')
            row.sha256=digest;row.size=size
            key=row.object_key
            upload_id=row.upload_id
            db.commit()
        # Multipart resume state is stored in the ledger, and list_parts reconciles
        # a successful part upload whose DB checkpoint was interrupted.
        if not upload_id:
            content_type='application/pdf' if path.suffix=='.pdf' else ('image/png' if path.suffix=='.png' else 'application/json')
            upload_id=client.create_multipart_upload(Bucket=bucket,Key=key,ContentType=content_type,ACL='private',Metadata={'sha256':digest})['UploadId']
            with SessionLocal() as db:
                assert_owned(db,execution);db.get(StorageArtifact,artifact_id).upload_id=upload_id;db.commit()
        parts=[];remote={}
        try:
            paginator=client.get_paginator('list_parts')
            for page in paginator.paginate(Bucket=bucket,Key=key,UploadId=upload_id):
                remote.update({p['PartNumber']:p for p in page.get('Parts',[])})
            with open(path,'rb') as f:
                number=0
                while True:
                    chunk=f.read(16*1024*1024)
                    if not chunk:break
                    check_cancelled();number+=1
                    if number in remote and remote[number]['Size']==len(chunk):etag=remote[number]['ETag']
                    else:
                        import base64
                        md5=base64.b64encode(hashlib.md5(chunk).digest()).decode()
                        etag=client.upload_part(Bucket=bucket,Key=key,UploadId=upload_id,PartNumber=number,Body=chunk,ContentMD5=md5)['ETag']
                    parts.append({'PartNumber':number,'ETag':etag})
            client.complete_multipart_upload(Bucket=bucket,Key=key,UploadId=upload_id,MultipartUpload={'Parts':parts})
        except client.exceptions.ClientError as exc:
            if exc.response.get('Error',{}).get('Code')!='NoSuchUpload':raise
            # Completion may have succeeded before its DB commit. Verify the object.
        try:head=client.head_object(Bucket=bucket,Key=key)
        except client.exceptions.ClientError as exc:
            if exc.response.get('Error',{}).get('Code') in {'404','NoSuchKey','NotFound'}:
                # An abandoned upload may have expired without completing.
                with SessionLocal() as db:
                    assert_owned(db,execution)
                    db.get(StorageArtifact,artifact_id).upload_id=None
                    db.commit()
            raise
        if head['ContentLength']!=size:raise OSError('Remote artifact size mismatch')
        # End-to-end readback avoids relying on multipart ETags or echoed metadata.
        body=client.get_object(Bucket=bucket,Key=key)['Body']
        verified=hashlib.sha256()
        try:
            for chunk in body.iter_chunks(chunk_size=1024*1024):check_cancelled();verified.update(chunk)
        finally:body.close()
        if verified.hexdigest()!=digest:raise OSError('Remote artifact checksum mismatch')
        with SessionLocal() as db:
            assert_owned(db,execution)
            row=db.get(StorageArtifact,artifact_id)
            row.state='verified';row.verified_at=datetime.utcnow();row.upload_id=None
            db.commit()
    with SessionLocal() as db:
        assert_owned(db,execution)
        rows=db.query(StorageArtifact).filter_by(submission_id=sid).all()
        manifest={'submission_id':sid,'artifacts':[{'key':r.object_key,'sha256':r.sha256,'bytes':r.size,'kind':r.kind,'page_number':r.page_number} for r in rows]}
    client.put_object(Bucket=bucket,Key=f'{settings.spaces_archive_prefix}/submissions/{sid}/manifests/job-{execution.id}.json',Body=json.dumps(manifest).encode(),ContentType='application/json',ACL='private')
    # Local eviction is deliberately a separate opt-in maintenance operation.
    # Deployment keeps archive_evict_local=False, preserving all existing files.
    return {'submission_id':sid,'archive_status':'verified','artifacts':len(rows)}
