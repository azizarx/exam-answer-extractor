"""Executable examples for the external integrator guide; no provider calls."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import routes
from backend.db.database import Base, get_db
from backend.db.models import AnswerKey
from main import app

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'docs/api/examples.json'


def normalize(value, key=''):
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, key) for v in value]
    if value is not None and (key.endswith('_at') or key in {'extraction_timestamp', 'at'}):
        return '2026-09-09T12:00:00' + ('Z' if key == 'at' else '')
    return value


def test_documented_http_examples(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/docs.sqlite', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routes, 'SessionLocal', factory)
    scheduled = []
    monkeypatch.setattr(routes, 'process_pdf_extraction', lambda *a: scheduled.append(a))
    page = tmp_path / 'page.png'
    page.write_bytes(b'example page')
    pdf = tmp_path / 'example-exam.pdf'
    pdf.write_bytes(b'example PDF')
    raw_candidate = {
        'page_number': 1, 'candidate_name': 'Sample Student',
        'candidate_number': '000123', 'country': 'VN', 'paper_type': 'A',
        'template_id': 'seamo_2025_a',
        'detection': {'method': 'footer_ocr', 'raw_text': 'SEAMO 2025 Paper A',
                      'brand': 'seamo', 'year': '2025', 'paper': 'a'},
        'answers': {'1': 'A', '2': 'B', '3': 'BL', '4': 'IN', '5': 'D'},
        'drawing_questions': {},
        'extra_fields': {'answer_trust': {'1': 'trusted', '2': 'trusted', '3': 'trusted', '4': 'trusted', '5': 'needs_review'},
                         'needs_review_questions': ['5']},
    }
    extraction = {'candidates': [raw_candidate], 'pages_processed': 1, 'pages_with_data': 1, 'processing_time': 1.25}
    monkeypatch.setattr(routes, 'extract_pdf_auto', lambda *a, **k: copy.deepcopy(extraction))
    def convert_pdf(path):
        if 'status_processing' not in outputs:
            capture('status_processing', 'get', '/status/1')
        return [str(page)]

    monkeypatch.setattr(routes, 'get_pdf_converter', lambda: SimpleNamespace(convert_from_file=convert_pdf))
    monkeypatch.setattr(routes, 'attach_run_log', lambda *a: SimpleNamespace(path=tmp_path/'run.log'))
    monkeypatch.setattr(routes, 'detach_run_log', lambda *a: None)
    storage = SimpleNamespace(
        save_pdf=lambda *a: {'absolute_path': str(pdf), 'relative_path': 'uploads/example-exam.pdf'},
        save_json=lambda data, name: (tmp_path/'raw.json').write_text(data) and {'relative_path': 'results/example-exam.json'},
        read_json=lambda key: (tmp_path/'raw.json').read_text(),
    )
    monkeypatch.setattr(routes, 'get_local_storage', lambda: storage)
    with factory() as db:
        db.add(AnswerKey(
            name='Example Paper A v1', template_id='seamo_2025_a', paper_type='A',
            version=1, source_filename='example-key.pdf', source_sha256='a'*64,
            total_questions=5, total_marks=10, is_active=True,
            answers={'1':'A','2':'C','3':'A','4':'B','5':'D'},
            question_spec=[{'number':i,'type':'mcq','accepted_answers':[answer],
                            'marks':marks,'normalizer':'uppercase'}
                           for i,answer,marks in [(1,'A',2),(2,'C',3),(3,'A',1),(4,'B',2),(5,'D',2)]],
        ))
        db.commit()

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    outputs = {}
    client = TestClient(app)

    def capture(name, method, path, expected=200, **kwargs):
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == expected, response.text
        outputs[name] = response.json()
        return outputs[name]

    try:
        capture('upload', 'post', '/upload', files={'file': ('example-exam.pdf', b'pdf', 'application/pdf')})
        capture('status_pending', 'get', '/status/1')
        capture('not_completed', 'get', '/submission/1', expected=400)
        # Invoke the real background handler with deterministic extracted inputs.
        REAL_PROCESS(*scheduled[0])
        capture('status_completed', 'get', '/status/1')
        capture('submission', 'get', '/submission/1')
        capture('marking', 'get', '/submission/1/marking')
        capture('marked_export', 'get', '/submission/1/marked-json')
        capture('raw_extraction', 'get', '/submission/1/json')
        capture('answer_keys', 'get', '/answer-keys')
        capture('sync_extract', 'post', '/extract/json', files={'file': ('example-exam.pdf', b'pdf', 'application/pdf')})
        capture('sync_mark', 'post', '/extract/json/mark', files={'file': ('example-exam.pdf', b'pdf', 'application/pdf')})
        outputs['confirm_review_request'] = {'answers': {'5': 'D'}, 'remark': True, 'edited_by': 'reviewer-42'}
        capture('confirm_review', 'post', '/submission/1/candidates/1/confirm-review', json=outputs['confirm_review_request'])
        outputs['remark_request'] = {'answer_key_id': 1}
        capture('remark', 'post', '/submission/1/mark', json=outputs['remark_request'])
        capture('cancel_conflict', 'post', '/submission/1/cancel', expected=409)
        capture('not_found', 'get', '/status/999999', expected=404)
        capture('validation_error', 'post', '/upload', expected=422)
        capture('bad_file', 'post', '/upload', files={'file': ('notes.txt', b'text', 'text/plain')}, expected=400)
        client.post('/upload', files={'file': ('example-exam.pdf', b'pdf', 'application/pdf')})
        capture('cancel', 'post', '/submission/2/cancel')
        capture('status_cancelled', 'get', '/status/2')
        client.post('/upload', files={'file': ('broken.pdf', b'pdf', 'application/pdf')})
        def broken_pdf(path):
            raise RuntimeError('Could not read PDF')
        monkeypatch.setattr(routes, 'get_pdf_converter', lambda: SimpleNamespace(convert_from_file=broken_pdf))
        REAL_PROCESS(*scheduled[-1])
        capture('status_failed', 'get', '/status/3')
        monkeypatch.setattr(routes, 'get_pdf_converter', lambda: SimpleNamespace(convert_from_file=convert_pdf))
        with factory() as db:
            db.query(AnswerKey).update({'is_active': False})
            db.commit()
        client.post('/upload', files={'file': ('example-exam.pdf', b'pdf', 'application/pdf')})
        REAL_PROCESS(*scheduled[-1])
        capture('marking_unavailable', 'get', '/submission/4/marking')
        capture('unavailable_export', 'get', '/submission/4/marked-json', expected=409)
        from backend.services import api_auth
        monkeypatch.setattr(api_auth, 'get_settings', lambda: SimpleNamespace(api_key='example-test-key'))
        capture('unauthorized', 'get', '/status/1', expected=401)
        # Prevent misleading "sync mark is the full marking workflow" examples.
        assert outputs['submission']['candidates'][0]['marking']['awarded_marks'] == 2
        assert outputs['sync_mark']['candidates'][0]['marking']['awarded_marks'] == 4
        assert outputs['confirm_review']['remarked'] is True
        normalized = normalize(outputs)
        if os.environ.get('UPDATE_API_DOC_EXAMPLES') == '1':
            EXAMPLES.write_text(json.dumps(normalized, indent=2, ensure_ascii=False)+'\n')
        assert normalized == json.loads(EXAMPLES.read_text())
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


REAL_PROCESS = routes.process_pdf_extraction
