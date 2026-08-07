"""Unit tests for footer-OCR layout classification."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

from backend.services.page_layout_classifier import (
    PageLayoutDetection,
    apply_embedded_family_hint,
    classify_page_image,
    classify_embedded_page_family,
    classify_pdf_text_page_families,
    classify_pdf_text_pages,
    is_format_b,
    parse_footer_text,
    resolve_layout_fields,
    _parse_and_resolve,
    _parse_gemini_layout_response,
)


class _FakeRegistry:
    def __init__(self, ids):
        self._ids = set(ids)

    def get(self, template_id):
        return SimpleNamespace(id=template_id) if template_id in self._ids else None


def _pdf_with_positioned_text(path, pages):
    document = pymupdf.open()
    for entries in pages:
        page = document.new_page(width=600, height=800)
        for x, y, value in entries:
            page.insert_text((x, y), value)
    document.save(path)
    document.close()


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


def test_embedded_pdf_text_classifies_strong_footer_and_top_format_b(tmp_path):
    pdf_path = tmp_path / "embedded-layouts.pdf"
    _pdf_with_positioned_text(
        pdf_path,
        [
            [
                (30, 45, "PEN AND PAPER EXAM"),
                (30, 760, "SEAMO 2025 Paper B"),
            ],
            [
                # A body marker is outside both permitted format-B bands.
                (30, 400, "PEN AND PAPER EXAM"),
                (30, 500, "MARK ONLY ONE OPTION"),
                (30, 760, "SEAMO X 2026 Paper K"),
            ],
            [
                (30, 700, "BRING A PRINTED COPY FOR PEN AND PAPER EXAM"),
                (30, 760, "SEAMO 2025 Paper C"),
            ],
        ],
    )
    registry = _FakeRegistry(
        {"seamo_2025_b_fb", "seamo_x_2026_k", "seamo_2025_c_fb"}
    )

    detections = classify_pdf_text_pages(str(pdf_path), registry=registry)

    assert [d.template_id for d in detections] == [
        "seamo_2025_b_fb",
        "seamo_x_2026_k",
        "seamo_2025_c_fb",
    ]
    assert detections[0].format_b is True
    assert detections[1].format_b is False
    assert detections[2].format_b is True
    assert all(d.method == "pdf_text" for d in detections)
    assert detections[0].raw_text == "SEAMO 2025 Paper B"


def test_embedded_pdf_text_leaves_weak_or_unknown_pages_for_fallback(tmp_path):
    pdf_path = tmp_path / "embedded-unresolved.pdf"
    _pdf_with_positioned_text(
        pdf_path,
        [
            # A complete signature in the page body is not footer evidence.
            [(30, 400, "SEAMO 2025 Paper B")],
            # Candidate-like weak text must not be combined into a layout.
            [(30, 400, "Candidate CAN202512345"), (30, 760, "Paper B")],
            # Strong footer, but no known template in the registry.
            [(30, 760, "SEAMO 2042 Paper F")],
        ],
    )
    registry = _FakeRegistry({"seamo_2025_b"})

    detections = classify_pdf_text_pages(str(pdf_path), registry=registry)

    assert detections == [None, None, None]


def test_embedded_pdf_family_does_not_require_a_parseable_footer(tmp_path):
    pdf_path = tmp_path / "embedded-families.pdf"
    _pdf_with_positioned_text(
        pdf_path,
        [
            [(30, 400, "MARK ONLY ONE OPTION")],
            [(30, 400, "FILL IN THE BOXES WITH THE RIGHT ANSWER")],
            [(30, 400, "unrelated candidate writing")],
        ],
    )

    assert classify_pdf_text_page_families(str(pdf_path)) == [False, True, None]


def test_embedded_family_hint_corrects_only_the_form_family():
    registry = _FakeRegistry({"seamo_2025_b", "seamo_2025_b_fb"})
    raster = PageLayoutDetection(
        template_id="seamo_2025_b_fb",
        method="footer_ocr",
        raw_text="SEAMO 2025 Paper B",
        brand="seamo",
        year="2025",
        paper="b",
        format_b=True,
    )

    corrected = apply_embedded_family_hint(
        raster,
        False,
        registry=registry,
    )

    assert corrected.template_id == "seamo_2025_b"
    assert corrected.format_b is False
    assert corrected.brand == "seamo"
    assert corrected.year == "2025"
    assert corrected.paper == "b"
    assert corrected.method == "footer_ocr+pdf_text_family"


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


def test_resolve_seamo_x_when_footer_logo_has_inserted_ocr_noise():
    """An explicit X must survive a noisy OCR rendering of the SEAMO logo."""
    reg = _FakeRegistry({"seamo_2026_b", "seamo_x_2026_b"})

    det = _parse_and_resolve(
        "ANSWER SHEET\nSEAMOQO X 2026 Paper B",
        registry=reg,
    )

    assert det.template_id == "seamo_x_2026_b"
    assert det.brand == "seamo_x"
    assert det.year == "2026"
    assert det.paper == "b"


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


@pytest.mark.parametrize(
    "text,band_text,expected",
    [
        ("FILL IN THE DIAGRAMS WITH THE RIGHT ANSWER", "", True),
        ("EACH BOX MAY CONTAIN ONLY ONE CHARACTER", "", False),
        ("unrelated candidate writing", "", None),
        # A weak phrase in the answer body is not enough by itself.
        ("PEN AND PAPER EXAM", "", None),
        ("PEN AND PAPER EXAM", "PEN AND PAPER EXAM", True),
        # Conflicting family evidence must fall back instead of guessing.
        (
            "FILL IN THE DIAGRAMS WITH THE RIGHT ANSWER. "
            "UPPERCASE ALPHABETS ONLY",
            "",
            None,
        ),
    ],
)
def test_embedded_page_family_requires_positive_per_page_evidence(
    text, band_text, expected,
):
    assert classify_embedded_page_family(text, band_text=band_text) is expected


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
