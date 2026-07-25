"""Unit tests for footer-OCR layout classification."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from backend.services.page_layout_classifier import (
    PageLayoutDetection,
    classify_page_image,
    is_format_b,
    parse_footer_text,
    promote_format_b_detections,
    resolve_layout_fields,
    _parse_and_resolve,
    _parse_gemini_layout_response,
)


class _FakeRegistry:
    def __init__(self, ids):
        self._ids = set(ids)

    def get(self, template_id):
        return SimpleNamespace(id=template_id) if template_id in self._ids else None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SEAMO X 2026 Paper B", ("seamo_x", "2026", "b")),
        ("SEAMO 2025 Paper A", ("seamo", "2025", "a")),
        ("SEAMO X2026 PAPER B", ("seamo_x", "2026", "b")),
        ("SEAMO X 2026 Pa per B", ("seamo_x", "2026", "b")),
        ("seamo x 2026 paper k", ("seamo_x", "2026", "k")),
        (
            "BRING A PRINTED COPY OF THIS PAGE WITH YOU TO THE EXAM HALL "
            "FOR PEN AND PAPER EXAM.\nANSWER SHEET\nSEAMO 2026 Paper K",
            ("seamo", "2026", "k"),
        ),
        (
            "PEN AND PAPER EXAM.\nANSWER SHEET\nSEAMO 2026 Paper A",
            ("seamo", "2026", "a"),
        ),
        # OCR mangling: SEAMQO → SEAMO
        (
            "BRING A PRINTED COPY\nFOR PEN AND PAPER EXAM.\nSEAMQO 2025 Paper B",
            ("seamo", "2025", "b"),
        ),
        ("something else", (None, None, None)),
        ("", (None, None, None)),
    ],
)
def test_parse_footer_text(text, expected):
    assert parse_footer_text(text) == expected


def test_format_b_markers():
    assert is_format_b(
        "BRING A PRINTED COPY OF THIS PAGE WITH YOU TO THE EXAM HALL "
        "FOR PEN AND PAPER EXAM.\nANSWER SHEET\nSEAMO 2025 Paper B"
    )
    assert not is_format_b("ANSWER SHEET\nSEAMO 2025 Paper B")


def test_resolve_format_b_template():
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb", "seamo_2026_a_fb"})
    det = _parse_and_resolve(
        "BRING A PRINTED COPY...\nFOR PEN AND PAPER EXAM.\n"
        "ANSWER SHEET\nSEAMO 2025 Paper B",
        registry=reg,
    )
    assert det.template_id == "seamo_2025_b_fb"
    assert det.format_b is True
    assert det.warning is None


def test_resolve_seamqo_format_b():
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb"})
    det = _parse_and_resolve(
        "BRING A PRINTED COPY\nFOR PEN AND PAPER EXAM.\nSEAMQO 2025 Paper B",
        registry=reg,
    )
    assert det.template_id == "seamo_2025_b_fb"
    assert det.brand == "seamo"


def test_resolve_classic_without_format_b_marker():
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb"})
    det = _parse_and_resolve("ANSWER SHEET\nSEAMO 2025 Paper B", registry=reg)
    assert det.template_id == "seamo_2025_b"
    assert det.format_b is False


def test_resolve_known_template():
    reg = _FakeRegistry({"seamo_x_2026_b", "seamo_2025_a"})
    det = _parse_and_resolve("SEAMO X 2026 Paper B", registry=reg)
    assert det.template_id == "seamo_x_2026_b"
    assert det.warning is None
    assert det.method == "footer_ocr"


def test_resolve_unknown_template():
    reg = _FakeRegistry({"seamo_2025_a"})
    det = _parse_and_resolve("SEAMO X 2026 Paper B", registry=reg)
    assert det.template_id is None
    assert det.warning == "unknown_template:seamo_x_2026_b"


def test_resolve_unparseable():
    reg = _FakeRegistry({"seamo_2025_a"})
    det = _parse_and_resolve("no useful footer", registry=reg)
    assert det.template_id is None
    assert det.warning == "unparseable_footer"


def test_detection_to_dict_drops_nulls():
    det = PageLayoutDetection(
        template_id="seamo_2025_a",
        method="footer_ocr",
        raw_text="SEAMO 2025 Paper A",
        warning=None,
        brand="seamo",
        year="2025",
        paper="a",
    )
    data = det.to_dict()
    assert data["template_id"] == "seamo_2025_a"
    assert "warning" not in data
    assert "format_b" not in data


def test_promote_format_b_upgrades_classic_siblings():
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb", "seamo_2025_c", "seamo_2025_c_fb"})
    detections = [
        PageLayoutDetection(
            template_id="seamo_2025_b_fb",
            method="footer_ocr",
            brand="seamo",
            year="2025",
            paper="b",
            format_b=True,
        ),
        PageLayoutDetection(
            template_id="seamo_2025_c",
            method="footer_ocr",
            brand="seamo",
            year="2025",
            paper="c",
            format_b=False,
        ),
    ]
    out = promote_format_b_detections(detections, registry=reg)
    assert out[0].template_id == "seamo_2025_b_fb"
    assert out[1].template_id == "seamo_2025_c_fb"
    assert out[1].warning == "format_b_promoted"
    assert out[1].format_b is True


def test_promote_format_b_noop_when_doc_classic():
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb"})
    detections = [
        PageLayoutDetection(
            template_id="seamo_2025_b",
            method="footer_ocr",
            brand="seamo",
            year="2025",
            paper="b",
            format_b=False,
        ),
    ]
    out = promote_format_b_detections(detections, registry=reg)
    assert out[0].template_id == "seamo_2025_b"
    assert out[0].warning is None


def test_gemini_header_fallback_when_ocr_fails(monkeypatch):
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb"})

    def fake_ocr(image_bgr, *, band):
        return "garbage"

    def fake_gemini(image_bgr):
        return {
            "brand": "seamo",
            "year": "2025",
            "paper": "b",
            "format_b": True,
        }

    monkeypatch.setattr(
        "backend.services.page_layout_classifier._ocr_band",
        fake_ocr,
    )
    image = np.zeros((100, 80, 3), dtype=np.uint8)
    det = classify_page_image(
        image,
        registry=reg,
        gemini_header=fake_gemini,
        use_gemini_fallback=True,
    )
    assert det.template_id == "seamo_2025_b_fb"
    assert det.method == "gemini_header"
    assert det.format_b is True


def test_gemini_not_called_when_ocr_resolves(monkeypatch):
    reg = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb"})
    called = {"n": 0}

    def fake_ocr(image_bgr, *, band):
        if band == "footer":
            return "BRING A PRINTED COPY\nPEN AND PAPER EXAM\nSEAMO 2025 Paper B"
        return ""

    def fake_gemini(image_bgr):
        called["n"] += 1
        return {}

    monkeypatch.setattr(
        "backend.services.page_layout_classifier._ocr_band",
        fake_ocr,
    )
    image = np.zeros((100, 80, 3), dtype=np.uint8)
    det = classify_page_image(
        image,
        registry=reg,
        gemini_header=fake_gemini,
        use_gemini_fallback=True,
    )
    assert det.template_id == "seamo_2025_b_fb"
    assert called["n"] == 0


def test_parse_gemini_layout_response():
    data = _parse_gemini_layout_response(
        '```json\n{"brand":"seamo","year":"2025","paper":"B","format_b":true}\n```'
    )
    assert data == {
        "brand": "seamo",
        "year": "2025",
        "paper": "b",
        "format_b": True,
    }


def test_resolve_layout_fields_direct():
    reg = _FakeRegistry({"seamo_2025_a_fb"})
    det = resolve_layout_fields(
        "seamo", "2025", "a", True, registry=reg, method="gemini_header"
    )
    assert det.template_id == "seamo_2025_a_fb"
    assert det.method == "gemini_header"
