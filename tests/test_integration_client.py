"""Verify the downloadable client against the tested API response fixtures."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('integration_client', ROOT / 'docs/api/integration_client.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
EXAMPLES = json.loads((ROOT / 'docs/api/examples.json').read_text())


def response(example):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(EXAMPLES[example]).encode()
    return result


def test_polling_retries_reads_and_saves_both_result_contracts(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    client = module.MarkerClient('https://example.test', 'example-key')
    assert client.session.headers['X-API-Key'] == 'example-key'
    client.session.get = Mock(side_effect=[
        requests.ConnectionError('temporary'), response('status_pending'),
        response('status_completed'), response('submission'),
        response('marking'), response('marked_export'),
    ])
    client.session.post = Mock()
    output = tmp_path / 'marked.json'
    result = client.collect(1, output)
    assert result == EXAMPLES['marked_export']
    assert json.loads(output.read_text()) == result
    extracted = json.loads((tmp_path / 'marked.extracted.json').read_text())
    assert extracted == EXAMPLES['submission']
    assert extracted['candidates'][0]['candidate_number'] == '000123'
    client.session.post.assert_not_called()
    assert [call.args[0].split('example.test')[1] for call in client.session.get.call_args_list] == [
        '/status/1', '/status/1', '/status/1', '/submission/1',
        '/submission/1/marking', '/submission/1/marked-json',
    ]


def test_unavailable_marking_preserves_extracted_answers(tmp_path):
    client = module.MarkerClient('https://example.test')
    client.session.get = Mock(side_effect=[
        response('status_completed'), response('submission'), response('marking_unavailable'),
    ])
    output = tmp_path / 'marked.json'
    with pytest.raises(RuntimeError, match='Marking is unavailable'):
        client.collect(4, output)
    assert (tmp_path / 'marked.extracted.json').exists()
    assert not output.exists()


def test_upload_uses_multipart_and_never_retries_an_ambiguous_post(tmp_path):
    pdf = tmp_path / 'exam.pdf'
    pdf.write_bytes(b'example PDF')
    client = module.MarkerClient('https://example.test/')

    def upload(url, **kwargs):
        assert url == 'https://example.test/upload'
        assert kwargs['params'] == {'template_id': 'seamo_2025_a'}
        assert 'files' not in kwargs
        stream = kwargs['data']
        prepared = requests.Request('POST', url, data=stream, headers=kwargs['headers']).prepare()
        assert prepared.body is stream
        assert prepared.headers['Content-Length'] == str(len(stream))
        body = b''.join(stream)
        assert b'filename="exam.pdf"' in body
        assert b'Content-Type: application/pdf' in body
        assert b'example PDF' in body
        assert kwargs['headers']['Idempotency-Key']
        raise requests.Timeout('Response lost after upload')

    client.session.post = Mock(side_effect=upload)
    with pytest.raises(requests.Timeout):
        client.upload(pdf, 'seamo_2025_a')
    client.session.post.assert_called_once()


def test_large_pdf_client_prepares_without_reading_or_buffering_file(tmp_path):
    pdf = tmp_path / 'three-gib.pdf'
    with pdf.open('wb') as output:
        output.truncate(3 * 1024 ** 3)
    with pdf.open('rb') as source:
        body = module.MultipartPDF(source, pdf.name)
        prepared = requests.Request('POST', 'https://example.test/upload', data=body).prepare()
        assert prepared.body is body
        assert source.tell() == 0
        assert int(prepared.headers['Content-Length']) > 3 * 1024 ** 3
        chunks = iter(body)
        assert len(next(chunks)) < 4096
        assert len(next(chunks)) == 1024 ** 2
        assert source.tell() == 1024 ** 2
