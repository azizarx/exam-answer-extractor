"""Serving the scanned page, the candidate's crop, and the key's drawing."""

from __future__ import annotations

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import routes  # noqa: F401  (registers the routes under test)
from backend.db.database import Base, get_db
from backend.db.models import CandidateResult, ExamSubmission
from backend.services.local_storage import get_local_storage
from main import app


@pytest.fixture
def api(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    (storage_root / "uploads").mkdir(parents=True)
    monkeypatch.setattr(
        get_local_storage(), "base_path", storage_root.resolve(), raising=True
    )

    pdf_path = storage_root / "uploads" / "exam.pdf"
    document = pymupdf.open()
    for _ in range(3):
        document.new_page(width=595, height=842)
    document.save(pdf_path)
    document.close()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'diagram-api.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with factory() as db:
        submission = ExamSubmission(
            filename="exam.pdf",
            original_pdf_key="uploads/exam.pdf",
            status="completed",
            pages_count=3,
        )
        db.add(submission)
        db.flush()
        candidate = CandidateResult(
            submission_id=submission.id,
            page_number=2,
            template_id="seamo_x_2026_a",
            answers={"4": "Clock: 7:45"},
            extra_fields={"diagram_crops": {"4": {"file": "p2_q4.png", "source": "template_region"}}},
        )
        db.add(candidate)
        db.commit()
        submission_id, candidate_id = submission.id, candidate.id

    crop_dir = storage_root / "diagrams" / f"sub{submission_id}"
    crop_dir.mkdir(parents=True)
    Image.new("RGB", (40, 40), "white").save(crop_dir / "p2_q4.png")

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), submission_id, candidate_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


# --- the scanned page ------------------------------------------------------

def test_page_is_rendered_on_demand_from_the_retained_pdf(api):
    client, submission_id, _ = api
    response = client.get(f"/submission/{submission_id}/page/2.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


@pytest.mark.parametrize("page", [0, 4, 99])
def test_a_page_outside_the_submission_is_not_found(api, page):
    client, submission_id, _ = api
    assert client.get(f"/submission/{submission_id}/page/{page}.png").status_code == 404


def test_an_unknown_submission_is_not_found(api):
    client, _, _ = api
    assert client.get("/submission/999999/page/1.png").status_code == 404


def test_a_pdf_key_pointing_outside_storage_is_refused(api, tmp_path):
    """original_pdf_key is ours, but a traversal must still never be opened."""
    client, submission_id, _ = api
    secret = tmp_path / "secret.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(secret)
    document.close()

    for db in app.dependency_overrides[get_db]():
        submission = db.get(ExamSubmission, submission_id)
        submission.original_pdf_key = "../../../../" + str(secret.relative_to(secret.anchor))
        db.commit()
        break

    assert client.get(f"/submission/{submission_id}/page/1.png").status_code == 410


# --- the candidate's crop --------------------------------------------------

def test_a_stored_crop_is_served(api):
    client, submission_id, candidate_id = api
    response = client.get(
        f"/submission/{submission_id}/candidates/{candidate_id}/diagram/4.png"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_a_question_with_no_stored_crop_is_not_found(api):
    client, submission_id, candidate_id = api
    response = client.get(
        f"/submission/{submission_id}/candidates/{candidate_id}/diagram/9.png"
    )
    assert response.status_code == 404


def test_a_candidate_from_another_submission_is_not_reachable(api):
    client, submission_id, candidate_id = api
    response = client.get(
        f"/submission/{submission_id + 1}/candidates/{candidate_id}/diagram/4.png"
    )
    assert response.status_code == 404


def test_a_crop_filename_from_the_payload_is_not_trusted(api):
    """A poisoned extra_fields must not become an arbitrary file read."""
    client, submission_id, candidate_id = api
    for db in app.dependency_overrides[get_db]():
        candidate = db.get(CandidateResult, candidate_id)
        candidate.extra_fields = {
            "diagram_crops": {"4": {"file": "../../../../etc/passwd", "source": "x"}}
        }
        db.commit()
        break

    response = client.get(
        f"/submission/{submission_id}/candidates/{candidate_id}/diagram/4.png"
    )
    assert response.status_code == 404


# --- the answer key's drawing ----------------------------------------------

@pytest.mark.parametrize(
    ("template_id", "question"),
    [("seamo_x_2026_a", 4), ("seamo_x_2026_a", 6), ("seamo_x_2026_a", 9),
     ("seamo_x_2026_b", 5)],
)
def test_every_committed_reference_drawing_is_served(api, template_id, question):
    client, _, _ = api
    response = client.get(f"/answer-keys/reference/{template_id}/{question}.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_a_non_diagram_question_has_no_reference(api):
    client, _, _ = api
    assert client.get("/answer-keys/reference/seamo_x_2026_a/7.png").status_code == 404


def test_a_traversal_in_the_template_id_is_refused(api):
    client, _, _ = api
    response = client.get("/answer-keys/reference/..%2f..%2fetc%2fpasswd/4.png")
    assert response.status_code == 404
