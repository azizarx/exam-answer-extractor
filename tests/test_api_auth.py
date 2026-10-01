"""Authentication stays enforced while browser preflight remains usable."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend.services import api_auth
from main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_auth, 'get_settings', lambda: SimpleNamespace(api_key='synthetic-test-key'))
    return TestClient(app)


def test_protected_routes_require_valid_key(client):
    assert client.get('/templates/all').status_code == 401
    assert client.get('/templates/all', headers={'X-API-Key': 'wrong'}).status_code == 401
    for header in [{'X-API-Key': 'synthetic-test-key'}, {'Authorization': 'Bearer synthetic-test-key'}]:
        assert client.get('/templates/all', headers=header).status_code == 200


def test_public_docs_and_health_stay_accessible(client):
    for path in ['/', '/health', '/docs', '/openapi.json']:
        assert client.get(path).status_code == 200


def test_browser_preflight_and_unauthorized_response_have_cors_headers(client):
    origin = 'https://aimarker.seamo-official.org'
    response = client.options('/templates/all', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'GET',
        'Access-Control-Request-Headers': 'x-api-key',
    })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] in {'*', origin}
    response = client.get('/templates/all', headers={'Origin': origin})
    assert response.status_code == 401
    assert response.headers['access-control-allow-origin'] in {'*', origin}
