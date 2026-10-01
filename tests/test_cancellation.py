from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import routes
from backend.db.database import Base, get_db
from backend.db.models import ExamSubmission, ProcessingLog
from backend.services.cancellation import (
    CancellationExecutor, CancellationToken, ExtractionCancelled,
    current_cancellation,
)
from main import app


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cancel.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(routes, 'SessionLocal', factory)

    def get_test_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = get_test_db
    with factory() as db:
        for sid, status in enumerate(['pending', 'processing', 'completed', 'failed'], 1):
            db.add(ExamSubmission(id=sid, filename='paper.pdf', original_pdf_key='paper.pdf', status=status))
        db.commit()
    try:
        yield TestClient(app), factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_cancel_is_persistent_idempotent_and_scoped(api):
    client, factory = api
    for sid in (1, 2):
        assert client.post(f'/submission/{sid}/cancel').json()['status'] == 'cancelled'
        assert client.post(f'/submission/{sid}/cancel').status_code == 200
        assert client.get(f'/status/{sid}').json()['status'] == 'cancelled'
    for sid in (3, 4):
        assert client.post(f'/submission/{sid}/cancel').status_code == 409
    assert client.post('/submission/999/cancel').status_code == 404
    with factory() as db:
        assert db.query(ProcessingLog).count() == 2
        assert db.get(ExamSubmission, 3).status == 'completed'


def test_nested_workers_stop_and_skip_queued_work(api):
    client, factory = api
    token = CancellationToken(2, factory)
    context = current_cancellation.set(token)
    entered, release = threading.Event(), threading.Event()
    queued_ran = []

    def nested():
        entered.set()
        assert release.wait(5)
        with CancellationExecutor(max_workers=1) as nested_pool:
            nested_pool.submit(lambda: queued_ran.append('nested')).result()

    try:
        with CancellationExecutor(max_workers=1) as pool:
            running = pool.submit(nested)
            assert entered.wait(5)
            queued = pool.submit(lambda: queued_ran.append('queued'))
            assert client.post('/submission/2/cancel').status_code == 200
            token.next_check = 0
            release.set()
            with pytest.raises(ExtractionCancelled):
                running.result(timeout=5)
            with pytest.raises(ExtractionCancelled):
                queued.result(timeout=5)
        assert queued_ran == []
        # Independent submissions keep working on the same process.
        with ThreadPoolExecutor() as other_pool:
            assert other_pool.submit(lambda: 'alive').result() == 'alive'
    finally:
        release.set()
        current_cancellation.reset(context)


@pytest.mark.parametrize('error_after_cancel', [False, True])
def test_worker_does_not_overwrite_cancellation(api, monkeypatch, tmp_path, error_after_cancel):
    client, factory = api
    image = tmp_path / 'page.png'
    image.write_bytes(b'image')
    monkeypatch.setattr(routes, 'attach_run_log', lambda *a: type('Log', (), {'path': tmp_path / 'run.log'})())
    monkeypatch.setattr(routes, 'detach_run_log', lambda *a: None)

    def convert(_path):
        assert client.post('/submission/1/cancel').status_code == 200
        current_cancellation.get().next_check = 0
        if error_after_cancel:
            raise RuntimeError('worker failed after cancellation')
        return [str(image)]

    monkeypatch.setattr(routes, 'get_pdf_converter', lambda: type('Converter', (), {'convert_from_file': staticmethod(convert)})())
    monkeypatch.setattr(routes, 'extract_pdf_auto', lambda *a, **k: pytest.fail('extraction ran after cancellation'))
    routes.process_pdf_extraction(1, 'paper.pdf')
    with factory() as db:
        assert db.get(ExamSubmission, 1).status == 'cancelled'
        assert db.get(ExamSubmission, 2).status == 'processing'
    assert current_cancellation.get() is None
    if not error_after_cancel:
        assert not image.exists()


def test_cancelled_queued_submission_never_starts(api, monkeypatch, tmp_path):
    client, _ = api
    client.post('/submission/1/cancel')
    monkeypatch.setattr(routes, 'attach_run_log', lambda *a: type('Log', (), {'path': tmp_path / 'run.log'})())
    monkeypatch.setattr(routes, 'detach_run_log', lambda *a: None)
    monkeypatch.setattr(routes, 'get_pdf_converter', lambda: pytest.fail('cancelled job started'))
    routes.process_pdf_extraction(1, 'paper.pdf')
