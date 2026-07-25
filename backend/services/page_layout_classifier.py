"""Per-page layout detection from the printed footer line.

SEAMO sheets print series + year + paper in the footer, e.g.
``SEAMO X 2026 Paper B``. We crop a bottom band, OCR with Tesseract,
and map brand/year/paper → a registry ``template_id``.

When footer/header OCR cannot resolve a layout, a Gemini call on a generous
top-band crop reads exam date / paper / format-B cues from the header.

This does not revive the deleted full-page OCR extraction pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract

from backend.services.template_service import get_template_registry

logger = logging.getLogger(__name__)

# Tight bottom band for SEAMO / year / paper OCR (avoids MCQ bubble noise).
FOOTER_FRACTION = 0.12
# Taller band used only to detect format-B markers (BRING / PEN AND PAPER).
FORMAT_B_BAND_FRACTION = 0.22
# Optional Tesseract header fallback when the footer yields nothing parseable.
HEADER_FRACTION = 0.10
# Gemini fallback crop — generous so slight vertical shift still includes
# title / date / paper / format-B banner without sending answer bubbles.
GEMINI_HEADER_FRACTION = 0.30

# Prefer "SEAMO [X] 20xx Paper L" so "PEN AND PAPER EXAM" cannot steal the letter.
_SEAMO_PAPER_RE = re.compile(
    r"SEAMO(?:\s*X)?\s*20\d{2}\s*P\s*A\s*P\s*E\s*R\s*([A-FK])",
    re.IGNORECASE,
)
# Fallback: PAPER + letter not followed by more letters (blocks PAPER EXAM).
_PAPER_RE = re.compile(r"P\s*A\s*P\s*E\s*R\s+([A-FK])(?![A-Z])", re.IGNORECASE)
_YEAR_RE = re.compile(r"(20\d{2})")
_FORMAT_B_RE = re.compile(
    r"BRING\s+(?:A\s+)?PRIN\w*\s+COPY|PEN\s+AND\s+PAPER\s+EXAM|FOR\s+PEN\s+AND\s+PAPER",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

_GEMINI_LAYOUT_PROMPT = (
    "You are classifying a SEAMO exam answer-sheet header crop.\n"
    "The image may be skewed, shifted, or partially cut — still read whatever "
    "layout cues are visible (title, exam year/date, paper letter, printed-copy "
    "or pen-and-paper exam banners).\n"
    "\n"
    "Return ONLY valid JSON (no markdown fences) with schema:\n"
    '{"brand":"seamo"|"seamo_x"|null,'
    '"year":"20xx"|null,'
    '"paper":"a"|"b"|"c"|"d"|"e"|"f"|"k"|null,'
    '"format_b":true|false}\n'
    "\n"
    "Rules:\n"
    "- brand is seamo_x only if the sheet says SEAMO X; otherwise seamo.\n"
    "- paper is the answer-sheet paper letter A–F or K (lowercase in JSON).\n"
    "- format_b is true if you see BRING A PRINTED COPY, PEN AND PAPER EXAM, "
    "or similar paper-exam instructions; false for classic bubble sheets.\n"
    "- Use null for any field you cannot determine.\n"
)


@dataclass
class PageLayoutDetection:
    template_id: Optional[str]
    method: str
    raw_text: str = ""
    warning: Optional[str] = None
    brand: Optional[str] = None
    year: Optional[str] = None
    paper: Optional[str] = None
    format_b: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Drop nulls so storage stays compact; keep format_b only when True
        out = {k: v for k, v in data.items() if v is not None and v is not False}
        return out


# Optional injectable: (bgr_image) -> {brand, year, paper, format_b}
GeminiHeaderFn = Callable[[np.ndarray], Dict[str, Any]]


def classify_page_image(
    image_bgr: np.ndarray,
    *,
    registry=None,
    gemini_header: Optional[GeminiHeaderFn] = None,
    use_gemini_fallback: bool = True,
) -> PageLayoutDetection:
    """Classify one page image into a template_id.

    Order: footer OCR → header OCR → optional Gemini top-band fallback.
    """
    if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return PageLayoutDetection(
            template_id=None,
            method="footer_ocr",
            warning="empty_image",
        )

    footer_text = _ocr_band(image_bgr, band="footer")
    format_b_text = _ocr_band(image_bgr, band="format_b")
    header_text = _ocr_band(image_bgr, band="header")
    detection = _parse_and_resolve(footer_text, registry=registry)

    # Format-B markers often sit above the SEAMO line — check the taller band
    # (and header) without polluting paper OCR with bubble noise.
    combined_for_fb = f"{footer_text}\n{format_b_text}\n{header_text}"
    if (
        detection.brand
        and detection.year
        and detection.paper
        and not detection.format_b
        and is_format_b(combined_for_fb)
    ):
        fb_det = resolve_layout_fields(
            detection.brand,
            detection.year,
            detection.paper,
            True,
            registry=registry,
            method=detection.method,
            raw_text=(footer_text or "").strip(),
        )
        if fb_det.template_id:
            return fb_det

    if detection.template_id:
        return detection

    # Extra bottom margin: try paper parse on the taller band as a second pass.
    if format_b_text.strip() and format_b_text.strip() != (footer_text or "").strip():
        tall_det = _parse_and_resolve(format_b_text, registry=registry)
        if tall_det.template_id:
            return tall_det
        if not detection.raw_text and tall_det.raw_text:
            detection = tall_det

    if header_text.strip():
        header_det = _parse_and_resolve(header_text, registry=registry)
        if header_det.template_id:
            header_det.method = "header_ocr"
            return header_det
        # Prefer footer raw_text when both failed; keep whichever had content.
        if not detection.raw_text and header_det.raw_text:
            detection = header_det
            detection.method = "header_ocr"

    if use_gemini_fallback:
        gemini_fn = gemini_header if gemini_header is not None else _gemini_classify_header
        try:
            fields = gemini_fn(image_bgr)
        except Exception as exc:
            logger.warning("Gemini header layout fallback failed: %s", exc)
            detection.warning = detection.warning or "gemini_header_failed"
            return detection

        gemini_det = resolve_layout_fields(
            fields.get("brand"),
            fields.get("year"),
            fields.get("paper"),
            bool(fields.get("format_b")),
            registry=registry,
            method="gemini_header",
            raw_text=(detection.raw_text or footer_text or header_text or "").strip(),
        )
        if gemini_det.template_id:
            return gemini_det
        detection.warning = gemini_det.warning or "gemini_header_failed"
        detection.method = "gemini_header"
        if fields.get("brand"):
            detection.brand = str(fields["brand"])
        if fields.get("year"):
            detection.year = str(fields["year"])
        if fields.get("paper"):
            detection.paper = str(fields["paper"]).lower()
        detection.format_b = bool(fields.get("format_b"))

    if not detection.warning:
        detection.warning = "unparseable_footer"
    return detection


def classify_page_path(
    image_path: str,
    *,
    registry=None,
    gemini_header: Optional[GeminiHeaderFn] = None,
    use_gemini_fallback: bool = True,
) -> PageLayoutDetection:
    image = cv2.imread(image_path)
    if image is None:
        return PageLayoutDetection(
            template_id=None,
            method="footer_ocr",
            warning=f"image_not_readable: {image_path}",
        )
    return classify_page_image(
        image,
        registry=registry,
        gemini_header=gemini_header,
        use_gemini_fallback=use_gemini_fallback,
    )


def parse_footer_text(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse brand / year / paper from OCR text. Public for unit tests."""
    normalized = _normalize_ocr(text)
    if not normalized:
        return None, None, None

    if re.search(r"SEAMO\s*X", normalized):
        brand = "seamo_x"
    elif "SEAMO" in normalized:
        brand = "seamo"
    else:
        brand = None

    year_m = _YEAR_RE.search(normalized)
    year = year_m.group(1) if year_m else None

    paper_m = _SEAMO_PAPER_RE.search(normalized) or _PAPER_RE.search(normalized)
    paper = paper_m.group(1).lower() if paper_m else None

    return brand, year, paper


def is_format_b(text: str) -> bool:
    """True when OCR shows PAPER-EXAM / printed-copy markers (format B)."""
    return bool(_FORMAT_B_RE.search(_normalize_ocr(text) or text or ""))


def resolve_layout_fields(
    brand: Optional[str],
    year: Optional[str],
    paper: Optional[str],
    format_b: bool,
    *,
    registry=None,
    method: str = "footer_ocr",
    raw_text: str = "",
) -> PageLayoutDetection:
    """Map brand/year/paper/format_b → registry template_id."""
    brand_s = (str(brand).strip().lower() if brand is not None else None) or None
    year_s = (str(year).strip() if year is not None else None) or None
    paper_s = (str(paper).strip().lower() if paper is not None else None) or None
    if year_s and not re.fullmatch(r"20\d{2}", year_s):
        year_m = _YEAR_RE.search(year_s)
        year_s = year_m.group(1) if year_m else None
    if brand_s not in ("seamo", "seamo_x"):
        brand_s = None
    if paper_s not in ("a", "b", "c", "d", "e", "f", "k"):
        paper_s = None

    base = PageLayoutDetection(
        template_id=None,
        method=method,
        raw_text=raw_text,
        brand=brand_s,
        year=year_s,
        paper=paper_s,
        format_b=bool(format_b),
    )
    if not brand_s or not year_s or not paper_s:
        base.warning = "unparseable_footer"
        return base

    canonical = f"{brand_s}_{year_s}_{paper_s}"
    template_id = f"{canonical}_fb" if format_b else canonical
    reg = registry if registry is not None else get_template_registry()

    if reg.get(template_id) is None:
        if format_b and reg.get(canonical) is not None:
            base.template_id = canonical
            base.warning = f"missing_format_b_layout:{template_id}"
            return base
        base.warning = f"unknown_template:{template_id}"
        return base

    base.template_id = template_id
    base.warning = None
    return base


def promote_format_b_detections(
    detections: List[PageLayoutDetection],
    *,
    registry=None,
) -> List[PageLayoutDetection]:
    """If any page is format B, upgrade classic siblings that already parse paper.

    Homogeneous format-B uploads often OCR SEAMO but miss BRING on some pages.
    Mixed classic+fb in one PDF is rare; promotion is the deliberate tradeoff.
    """
    doc_is_fb = any(
        d.format_b or (d.template_id or "").endswith("_fb") for d in detections
    )
    if not doc_is_fb:
        return detections

    reg = registry if registry is not None else get_template_registry()
    out: List[PageLayoutDetection] = []
    for d in detections:
        tid = d.template_id or ""
        if tid.endswith("_fb"):
            out.append(d)
            continue
        if not tid:
            out.append(d)
            continue
        # Classic id — promote to _fb twin when present
        brand = d.brand
        year = d.year
        paper = d.paper
        if brand and year and paper:
            fb_id = f"{brand}_{year}_{paper}_fb"
        elif tid and not tid.endswith("_fb"):
            fb_id = f"{tid}_fb"
        else:
            out.append(d)
            continue
        if reg.get(fb_id) is None:
            out.append(d)
            continue
        out.append(
            replace(
                d,
                template_id=fb_id,
                format_b=True,
                warning="format_b_promoted",
            )
        )
    return out


def _parse_and_resolve(text: str, *, registry=None) -> PageLayoutDetection:
    brand, year, paper = parse_footer_text(text)
    return resolve_layout_fields(
        brand,
        year,
        paper,
        is_format_b(text),
        registry=registry,
        method="footer_ocr",
        raw_text=(text or "").strip(),
    )


def _normalize_ocr(text: str) -> str:
    if not text:
        return ""
    t = text.upper()
    # Common OCR substitutions for SEAMO footers
    t = t.replace("SRAMO", "SEAMO")
    t = t.replace("SCAMOC", "SEAMO")
    t = t.replace("SLAMO", "SEAMO")
    t = t.replace("SEAM0", "SEAMO")
    # SEAMQO / SEAMBO / etc. → SEAMO (optional char between M and O)
    t = re.sub(r"SEAM.?O", "SEAMO", t)
    t = t.replace("SEAMOX", "SEAMO X")
    # Year / paper letter OCR noise
    t = re.sub(r"[£E]025\b", "2025", t)
    t = re.sub(r"PAPER\s+8\b", "PAPER B", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _ocr_band(image_bgr: np.ndarray, *, band: str) -> str:
    h, w = image_bgr.shape[:2]
    if band == "footer":
        y0 = max(0, int(h * (1.0 - FOOTER_FRACTION)))
        crop = image_bgr[y0:h, 0:w]
    elif band == "format_b":
        y0 = max(0, int(h * (1.0 - FORMAT_B_BAND_FRACTION)))
        crop = image_bgr[y0:h, 0:w]
    else:
        y1 = max(1, int(h * HEADER_FRACTION))
        crop = image_bgr[0:y1, 0:w]

    if crop.size == 0:
        return ""

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Light threshold helps thin footer print on scans
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    try:
        return pytesseract.image_to_string(binary, config="--psm 6") or ""
    except Exception as exc:
        logger.warning("Tesseract OCR failed on %s band: %s", band, exc)
        return ""


def _crop_gemini_header(image_bgr: np.ndarray) -> np.ndarray:
    h, w = image_bgr.shape[:2]
    y1 = max(1, int(h * GEMINI_HEADER_FRACTION))
    return image_bgr[0:y1, 0:w]


def _gemini_classify_header(image_bgr: np.ndarray) -> Dict[str, Any]:
    """Default Gemini fallback: classify layout from the top-band crop."""
    import google.generativeai as genai
    from PIL import Image

    from backend.services.gemini_client import create_gemini_model
    from backend.services.run_logger import llm_call

    crop = _crop_gemini_header(image_bgr)
    if crop.size == 0:
        return {}
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    model, _name = create_gemini_model()
    response = llm_call(
        "layout_header",
        model,
        [_GEMINI_LAYOUT_PROMPT, pil_image],
        genai.GenerationConfig(temperature=0.0),  # no max_output_tokens
        logger,
        image=pil_image,
    )
    text = getattr(response, "text", None) or ""
    return _parse_gemini_layout_response(text)


def _parse_gemini_layout_response(text: str) -> Dict[str, Any]:
    if not text or not str(text).strip():
        return {}
    raw = str(text).strip()
    fence = _FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Gemini layout JSON parse failed: %r", raw[:200])
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    if data.get("brand") is not None:
        out["brand"] = str(data["brand"]).strip().lower()
    if data.get("year") is not None:
        out["year"] = str(data["year"]).strip()
    if data.get("paper") is not None:
        out["paper"] = str(data["paper"]).strip().lower()
    out["format_b"] = bool(data.get("format_b"))
    return out
