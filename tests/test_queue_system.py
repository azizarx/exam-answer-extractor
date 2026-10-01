import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config import get_settings
from backend.db.database import Base, get_db
from backend.db.models import ExamSubmission, ProcessingJob, CandidateResult, PageCheckpoint, UploadReservation
from backend.queue import runtime, pipeline
from backend.services import local_storage, upload_middleware, artifact_storage
from main import app


@pytest.fixture
def env(tmp_path,monkeypatch):
    settings=get_settings()
    for key,value in {'queue_enabled':True,'min_free_disk_gb':0,'max_file_size_mb':3,'queue_batch_pages':2,'archive_enabled':False,'storage_root':str(tmp_path/'storage'),'api_key':''}.items():
        monkeypatch.setattr(settings,key,value)
    engine=create_engine(f'sqlite:///{tmp_path}/queue.sqlite',connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine)
    for module in [runtime,pipeline,upload_middleware,artifact_storage]:monkeypatch.setattr(module,'SessionLocal',factory)
    monkeypatch.setattr(local_storage,'_local_storage',None)
    def override():
        with factory() as db:yield db
    app.dependency_overrides[get_db]=override
    with pymupdf.open() as pdf:
        for i in range(3):pdf.new_page().insert_text((60,60),f'Synthetic page {i+1}')
        data=pdf.tobytes()
    yield SimpleNamespace(client=TestClient(app),db=factory,pdf=data,storage=local_storage.get_local_storage(),settings=settings)
    app.dependency_overrides.clear();engine.dispose()


def upload(env,**kwargs):
    response=env.client.post('/upload',files={'file':('test.pdf',env.pdf,'application/pdf')},**kwargs)
    assert response.status_code==200,response.text
    with env.db() as db:job=db.query(ProcessingJob).filter_by(submission_id=response.json()['submission_id']).one()
    return response.json()['submission_id'],job.id


def test_streaming_upload_is_durable_and_idempotent(env):
    sid,jid=upload(env,headers={'Idempotency-Key':'first'})
    with env.db() as db:
        sub=db.get(ExamSubmission,sid)
        assert sub.status=='pending'
        assert (env.storage.base_path/sub.original_pdf_key).read_bytes()==env.pdf
        assert db.get(ProcessingJob,jid).state=='pending'
    sid2,jid2=upload(env,headers={'Idempotency-Key':'first'})
    assert (sid2,jid2)==(sid,jid)
    conflict=env.client.post('/upload',files={'file':('different.pdf',env.pdf+b'different','application/pdf')},headers={'Idempotency-Key':'first'})
    assert conflict.status_code==409
    assert len(list(env.storage.uploads_path.glob('*.pdf')))==1
    assert not list(env.storage.uploads_path.glob('*.part'))


def test_limit_and_incomplete_upload_leave_no_jobs_or_files(env):
    response=env.client.post('/upload',files={'file':('big.pdf',b'x'*(3*1024**2+1),'application/pdf')})
    assert response.status_code==413
    response=env.client.post('/upload',content=b'--broken\r\n',headers={'Content-Type':'multipart/form-data; boundary=broken'})
    assert response.status_code==400
    with env.db() as db:
        assert db.query(ExamSubmission).count()==0
        assert db.query(UploadReservation).count()==0
    assert not list(env.storage.uploads_path.iterdir())


def test_cancel_before_execution_is_persistent(env):
    sid,jid=upload(env)
    assert env.client.post(f'/submission/{sid}/cancel').status_code==200
    runtime.execute(jid)
    with env.db() as db:
        assert db.get(ProcessingJob,jid).state=='cancelled'
        assert db.get(ExamSubmission,sid).status=='cancelled'
        assert db.query(PageCheckpoint).count()==0
    assert len(list(env.storage.uploads_path.glob('*.pdf')))==1


def test_pages_checkpoint_and_duplicate_delivery_do_not_duplicate_candidates(env,monkeypatch):
    from backend.services import template_extractor
    from backend.services import marking_workflow
    calls=[]
    def extract(path,images,**kwargs):
        calls.append(len(images))
        return {'candidates':[{'page_number':i+1,'candidate_number':f'{len(calls):02d}{i:02d}','candidate_name':'Synthetic',
            'template_id':'seamo_x_2026_b','answers':{'1':'A'},'extra_fields':{}} for i in range(len(images))]}
    monkeypatch.setattr(template_extractor,'extract_pdf_auto',extract)
    # No external OCR/AI: test real rendering, checkpoint writes and final output.
    def mark(db,sid):return SimpleNamespace(id=1,status='unavailable')
    monkeypatch.setattr(marking_workflow,'mark_submission_answers',mark)
    sid,jid=upload(env)
    runtime.execute(jid)
    with env.db() as db:
        assert db.query(CandidateResult).count()==2
        assert db.get(ProcessingJob,jid).payload['next_page']==2
    runtime.execute(jid)
    runtime.execute(jid)
    runtime.execute(jid)
    with env.db() as db:
        assert db.query(CandidateResult).count()==3
        assert db.get(ExamSubmission,sid).status=='completed'
        assert db.get(ProcessingJob,jid).state=='completed'
    assert calls==[2,1]
    assert len(list((env.storage.base_path/'pages'/f'sub{sid}').glob('*.png')))==3
    image=env.client.get('/candidate-page',params={'exam_id':'seamo_x_2026_b','candidate_number':'0100'})
    assert image.status_code==200,image.text[:200] if image.status_code!=200 else ''
    assert image.content.startswith(b'\x89PNG')
    assert image.headers['x-page-number']=='1'


def test_candidate_lookup_refuses_ambiguous_match_and_preserves_leading_zero(env):
    sid,_=upload(env)
    with env.db() as db:
        db.add_all([CandidateResult(submission_id=sid,page_number=i,candidate_number='000123',template_id='seamo_x_2026_b',answers={}) for i in [1,2]])
        db.commit()
    params={'exam_id':'seamo_x_2026_b','candidate_number':'000123'}
    ambiguous=env.client.get('/candidate-page',params=params)
    assert ambiguous.status_code==409
    assert len(ambiguous.json()['detail']['matches'])==2
    assert env.client.get('/candidate-page',params={**params,'candidate_number':'123'}).status_code==404
    selected=env.client.get('/candidate-page',params={**params,'submission_id':sid,'page_number':2})
    assert selected.status_code==200
    assert selected.headers['x-page-number']=='2'


def test_reaper_requeues_expired_lease_and_fences_old_owner(env,monkeypatch):
    from backend.queue.app import app as celery
    sent=[]
    monkeypatch.setattr(celery,'send_task',lambda *a,**kw:sent.append((a,kw)))
    sid,jid=upload(env)
    with env.db() as db:
        job=db.get(ProcessingJob,jid)
        job.state='running';job.owner='dead';job.generation=1;job.attempts=1
        job.lease_until=datetime.utcnow()-timedelta(seconds=1)
        db.commit();execution=runtime.Execution(job)
    runtime.dispatch_once()
    with env.db() as db:
        assert db.get(ProcessingJob,jid).state=='pending'
        with pytest.raises(BaseException):runtime.assert_owned(db,execution)
    assert sent[0][1]['args']==[jid]


def test_healthy_long_marking_is_not_failed_by_status_reads(env):
    from backend.db.models import MarkingRun
    from backend.services.marking_workflow import recover_stale_marking_runs
    sid,jid=upload(env)
    with env.db() as db:
        job=db.get(ProcessingJob,jid);job.state='running';job.lease_until=datetime.utcnow()+timedelta(seconds=120)
        mark=MarkingRun(submission_id=sid,status='processing',started_at=datetime.utcnow()-timedelta(hours=2))
        db.add(mark);db.commit()
        recover_stale_marking_runs(db,submission_id=sid)
        db.refresh(mark)
        assert mark.status=='processing'


def test_archive_failure_retains_files_and_verification_allows_remote_read(env,monkeypatch):
    import io
    from backend.db.models import StorageArtifact
    sid,jid=upload(env)
    with env.db() as db:
        sub=db.get(ExamSubmission,sid);sub.status='completed'
        source=env.storage.base_path/sub.original_pdf_key
        job=runtime.enqueue(db,'archive',sid);db.commit();aid=job.id
    monkeypatch.setattr(env.settings,'archive_enabled',True)
    monkeypatch.setattr(env.settings,'spaces_bucket','synthetic-bucket')
    objects={};parts={};fail={'enabled':True}
    class Body(io.BytesIO):
        def iter_chunks(self,chunk_size):
            while data:=self.read(chunk_size):yield data
    class Client:
        class exceptions:
            class ClientError(Exception):pass
        def create_multipart_upload(self,**kw):return {'UploadId':'u1'}
        def get_paginator(self,name):return SimpleNamespace(paginate=lambda **kw:[{'Parts':[]}])
        def upload_part(self,**kw):
            if fail['enabled']:raise OSError('simulated Spaces outage')
            parts[kw['PartNumber']]=kw['Body'];return {'ETag':str(kw['PartNumber'])}
        def complete_multipart_upload(self,**kw):objects[kw['Key']]=b''.join(parts[i] for i in sorted(parts));return {}
        def head_object(self,**kw):return {'ContentLength':len(objects[kw['Key']])}
        def get_object(self,**kw):return {'Body':Body(objects[kw['Key']])}
        def put_object(self,**kw):objects[kw['Key']]=kw['Body'];return {}
        def download_file(self,bucket,key,path):Path(path).write_bytes(objects[key])
    monkeypatch.setattr(artifact_storage,'spaces',lambda:Client())
    runtime.execute(aid)
    assert source.read_bytes()==env.pdf
    with env.db() as db:
        job=db.get(ProcessingJob,aid);assert job.state=='pending';job.available_at=datetime.utcnow();db.commit()
    fail['enabled']=False
    runtime.execute(aid)
    with env.db() as db:
        assert db.get(ProcessingJob,aid).state=='completed'
        row=db.query(StorageArtifact).filter_by(submission_id=sid,kind='source').one()
        assert row.state=='verified'
        relative=row.local_path
    assert source.exists()  # Eviction remains disabled even after successful archival.
    source.unlink()  # Only a synthetic test fixture; simulate an evicted cache.
    restored=artifact_storage.ensure_local(relative)
    assert restored.read_bytes()==env.pdf


def test_non_json_or_duplicate_multipart_files_are_rejected(env):
    response=env.client.post('/upload',files=[('file',('one.pdf',env.pdf)),('file',('two.pdf',env.pdf))])
    assert response.status_code==422
    with env.db() as db:assert db.query(ProcessingJob).count()==0


def test_real_marking_checkpoints_are_saved_in_queue_job(env,monkeypatch):
    from backend.db.models import AnswerKey, CandidateMarking, JobCheckpoint
    from backend.services import template_extractor
    def extract(path,images,**kwargs):
        return {'candidates':[{'page_number':i+1,'candidate_number':f'{i:04d}','template_id':'seamo_x_2026_b','answers':{'1':'A'},'extra_fields':{}} for i in range(len(images))]}
    monkeypatch.setattr(template_extractor,'extract_pdf_auto',extract)
    with env.db() as db:
        db.add(AnswerKey(name='Synthetic',template_id='seamo_x_2026_b',version=1,source_filename='synthetic.pdf',source_sha256='a'*64,total_questions=1,total_marks=2,is_active=True,
            answers={'1':'A'},question_spec=[{'number':1,'type':'mcq','accepted_answers':['A'],'marks':2,'normalizer':'uppercase'}]))
        db.commit()
    sid,jid=upload(env)
    for _ in range(3):runtime.execute(jid)
    with env.db() as db:
        assert db.get(ProcessingJob,jid).state=='completed',db.get(ProcessingJob,jid).error
        assert db.query(CandidateMarking).count()==3
        assert db.query(JobCheckpoint).filter_by(job_id=jid,key='committed-mark-run').count()==1
        assert db.query(JobCheckpoint).filter_by(job_id=jid).count()==4


def test_explicit_delete_is_queued_and_cannot_race_active_work(env,monkeypatch):
    from backend.services import artifact_lifecycle
    monkeypatch.setattr(artifact_lifecycle,'SessionLocal',env.db)
    sid,jid=upload(env,headers={'Idempotency-Key':'delete-fixture'})
    assert env.client.delete(f'/submission/{sid}').status_code==409
    assert env.client.post(f'/submission/{sid}/cancel').status_code==200
    response=env.client.delete(f'/submission/{sid}')
    assert response.status_code==202,response.text
    delete_id=response.json()['job_id']
    assert env.client.post(f'/jobs/{delete_id}/cancel').status_code==409
    with env.db() as db:
        assert db.get(ExamSubmission,sid).status=='deleting'
        assert len(list(env.storage.uploads_path.glob('*.pdf')))==1
    runtime.execute(delete_id)
    runtime.execute(delete_id)
    with env.db() as db:
        assert db.get(ExamSubmission,sid) is None
        assert db.get(ProcessingJob,delete_id).state=='completed'
    assert not list(env.storage.uploads_path.glob('*.pdf'))
    new_sid,_=upload(env)
    assert new_sid>sid
    replay=env.client.post('/upload',files={'file':('test.pdf',env.pdf,'application/pdf')},headers={'Idempotency-Key':'delete-fixture'})
    assert replay.status_code==410


def test_eviction_requires_verified_archive_and_explicit_opt_in(env,monkeypatch):
    import os
    from backend.services import artifact_lifecycle
    from backend.db.models import StorageArtifact
    monkeypatch.setattr(artifact_lifecycle,'SessionLocal',env.db)
    sid,jid=upload(env)
    with env.db() as db:
        sub=db.get(ExamSubmission,sid);sub.status='completed'
        source=env.storage.base_path/sub.original_pdf_key
        db.get(ProcessingJob,jid).state='completed'
        row=pipeline.artifact(db,sid,source,'source')
        row.state='verified';row.size=len(env.pdf);row.sha256=hashlib.sha256(env.pdf).hexdigest()
        row.verified_at=datetime.utcnow()-timedelta(days=2)
        archive=runtime.enqueue(db,'archive',sid);archive.state='completed'
        job=runtime.enqueue(db,'evict',sid);db.commit();eid=job.id
    runtime.execute(eid)
    assert source.exists()
    monkeypatch.setattr(env.settings,'archive_evict_local',True)
    os.utime(source,(1,1))
    monkeypatch.setattr(artifact_storage,'spaces',lambda:SimpleNamespace(head_object=lambda **kw:{'ContentLength':len(env.pdf),'Metadata':{'sha256':hashlib.sha256(env.pdf).hexdigest()}}))
    with env.db() as db:
        job=runtime.enqueue(db,'evict',sid);db.commit();eid=job.id
    runtime.execute(eid)
    assert not source.exists()
    with env.db() as db:
        assert db.get(ProcessingJob,eid).state=='completed'
        assert db.query(StorageArtifact).filter_by(submission_id=sid).one().state=='verified'
        assert db.get(ExamSubmission,sid).status=='completed'


def test_artifact_lock_fences_cleanup_until_old_worker_releases(env,monkeypatch):
    from backend.services.artifact_lifecycle import artifact_lock
    sid,jid=upload(env)
    with artifact_lock(sid):
        with pytest.raises(BlockingIOError):
            with artifact_lock(sid,exclusive=True):pass
    with artifact_lock(sid,exclusive=True):pass


def test_expired_execution_lease_retries_instead_of_cancelling_submission(env,monkeypatch):
    sid,jid=upload(env)
    def expire(*args):
        with env.db() as db:
            job=db.get(ProcessingJob,jid);job.lease_until=datetime.utcnow()-timedelta(seconds=1);db.commit()
        return True,{}
    monkeypatch.setattr(pipeline,'run_unit',expire)
    runtime.execute(jid)
    with env.db() as db:
        assert db.get(ProcessingJob,jid).state=='pending'
        assert db.get(ExamSubmission,sid).status=='pending'


def test_legacy_exam_uploads_share_streaming_admission(env):
    exam=env.client.post('/exams',json={'name':'Synthetic queue exam'}).json()
    response=env.client.post(f"/exams/{exam['id']}/student-pdfs?country=Test",files={'file':('test.pdf',env.pdf,'application/pdf')})
    assert response.status_code==200,response.text
    assert response.json()['pages_count']==3
    assert response.json()['country']=='Test'
    correction=env.client.post(f"/exams/{exam['id']}/correction",files={'file':('key.pdf',env.pdf,'application/pdf')})
    assert correction.status_code==200,correction.text
    with env.db() as db:
        assert db.query(ProcessingJob).count()==0
        assert db.query(UploadReservation).filter_by(state='completed').count()==2
    assert len(list(env.storage.uploads_path.glob('*.pdf')))==2


def test_upload_admission_counts_running_batch_workspace(env,monkeypatch):
    from backend.services import queued_upload
    monkeypatch.setattr(queued_upload.shutil,'disk_usage',lambda _:SimpleNamespace(free=10*1024**2))
    with env.db() as db:
        db.add(ProcessingJob(kind='process',state='running',payload={'_workspace_bytes':8*1024**2}))
        db.commit()
    response=env.client.post('/upload',files={'file':('test.pdf',env.pdf,'application/pdf')})
    assert response.status_code==503
    assert response.json()['detail']['code']=='disk_capacity'
    assert not list(env.storage.uploads_path.iterdir())


def test_stale_worker_file_lock_defers_retry_without_consuming_attempt(env):
    from backend.services.artifact_lifecycle import artifact_lock
    sid,jid=upload(env)
    with artifact_lock(sid,exclusive=True):runtime.execute(jid)
    with env.db() as db:
        job=db.get(ProcessingJob,jid)
        assert job.state=='pending'
        assert job.attempts==0
        assert db.query(PageCheckpoint).count()==0


def test_pre_rollout_submissions_stay_pinned_when_eviction_is_enabled(env,monkeypatch):
    from backend.services import artifact_lifecycle
    monkeypatch.setattr(artifact_lifecycle,'SessionLocal',env.db)
    sid,jid=upload(env)
    monkeypatch.setattr(env.settings,'archive_evict_local',True)
    monkeypatch.setattr(env.settings,'archive_preserve_through_submission_id',sid)
    with env.db() as db:
        assert artifact_lifecycle.schedule_eviction(db,sid) is None
        job=runtime.enqueue(db,'evict',sid);db.commit();eid=job.id
    runtime.execute(eid)
    with env.db() as db:
        assert db.get(ProcessingJob,eid).state=='completed'
        assert 'pinned' in db.get(ProcessingJob,eid).result['reason']
    assert len(list(env.storage.uploads_path.glob('*.pdf')))==1


def test_delete_supersedes_delayed_local_cache_cleanup(env):
    sid,jid=upload(env)
    assert env.client.post(f'/submission/{sid}/cancel').status_code==200
    with env.db() as db:
        job=runtime.enqueue(db,'evict',sid);job.available_at=datetime.utcnow()+timedelta(days=1);db.commit();eid=job.id
    response=env.client.delete(f'/submission/{sid}')
    assert response.status_code==202,response.text
    with env.db() as db:assert db.get(ProcessingJob,eid).state=='cancelled'


@pytest.mark.parametrize('template_id', ['seamo_2026_k', 'seamo_2026_a', 'seamo_2026_b_fb', 'seamo_2026_f_fb'])
def test_external_exam_31_resolves_seamo_2026_without_answer_keys(env, template_id):
    from backend.db.models import AnswerKey
    sid,_=upload(env)
    with env.db() as db:
        assert db.query(AnswerKey).count()==0
        row=CandidateResult(submission_id=sid,page_number=1,candidate_number='000123',template_id=template_id,answers={})
        db.add(row);db.commit();cid=row.id
    response=env.client.get('/candidate-page',params={'exam_id':31,'candidate_number':'000123'})
    assert response.status_code==200,response.text[:100] if response.status_code!=200 else ''
    assert response.content.startswith(b'\x89PNG')
    assert response.headers['x-candidate-id']==str(cid)
    assert env.client.get('/candidate-page',params={'exam_id':31,'candidate_number':'123'}).status_code==404
    caps=env.client.get('/capabilities').json()
    assert caps['candidate_page_exams']=={'31':'seamo_2026'}


def test_external_exam_series_excludes_other_years_and_seamo_x(env):
    sid,_=upload(env)
    with env.db() as db:
        db.add_all([CandidateResult(submission_id=sid,page_number=i,candidate_number='000123',template_id=template,answers={})
            for i,template in enumerate(['seamo_2025_a','seamo_x_2026_b','seamo_2025_a_fb'],1)])
        db.commit()
    assert env.client.get('/candidate-page',params={'exam_id':31,'candidate_number':'000123'}).status_code==404
    assert env.client.get('/candidate-page',params={'exam_id':32,'candidate_number':'000123'}).status_code==422
    assert env.client.get('/candidate-page',params={'exam_id':'seamo_x_2026_b','candidate_number':'000123'}).status_code==200


def test_external_exam_series_requires_disambiguation_across_papers_and_submissions(env):
    sid,_=upload(env);other_sid,_=upload(env)
    with env.db() as db:
        db.get(ExamSubmission,sid).template_id='seamo_2026_a'
        db.add_all([
            CandidateResult(submission_id=sid,page_number=1,candidate_number='000123',template_id=None,answers={}),
            CandidateResult(submission_id=other_sid,page_number=2,candidate_number='000123',template_id='seamo_2026_b_fb',answers={}),
        ])
        db.commit()
    params={'exam_id':31,'candidate_number':'000123'}
    response=env.client.get('/candidate-page',params=params)
    assert response.status_code==409
    assert len(response.json()['detail']['matches'])==2
    selected=env.client.get('/candidate-page',params={**params,'submission_id':other_sid})
    assert selected.status_code==200
    assert selected.headers['x-page-number']=='2'
    # A discriminator may never broaden the requested exam series.
    assert env.client.get('/candidate-page',params={**params,'exam_id':'seamo_2025_a','submission_id':other_sid}).status_code==404
