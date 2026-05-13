"""
Extractors for non-MCQ answer section types:
- numeric_grid: one character per cell, read via OCR (Tesseract or Mathpix)
- open_response: free-form handwritten boxes, read via LLM vision
- diagram: visual answers (clocks, graphs, etc.), detected + interpreted via LLM

Each extractor takes a page image + section definition and returns answers.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from backend.services.template_service import (
    AnswerSection,
    ExamTemplate,
    GridGeometry,
    QuestionOverride,
    Region,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type (shared across all section extractors)
# ---------------------------------------------------------------------------

class SectionExtractionResult:
    """Extraction result for a non-MCQ section."""

    def __init__(self, section_type: str, question_start: int, question_end: int):
        self.section_type = section_type
        self.question_start = question_start
        self.question_end = question_end
        self.answers: Dict[str, str] = {}
        self.diagrams: Dict[str, Dict[str, Any]] = {}  # q -> {"type": "diagram", "crop": ndarray, ...}
        self.confidence: Dict[str, float] = {}
        self.errors: List[str] = []


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _get_question_regions(
    section: AnswerSection,
    dx: int = 0,
    dy: int = 0,
) -> Dict[int, Dict[str, Any]]:
    """
    Compute per-question regions from a section's grid geometry.

    Returns:
        {question_number: {"x": ..., "y": ..., "w": ..., "h": ..., "type": ..., "override": ...}}
    """
    grid = section.grid
    if not grid:
        return {}

    questions_per_col = grid.questions_per_col or [grid.rows]
    num_vc = grid.cols or 1
    regions: Dict[int, Dict[str, Any]] = {}
    question_number = section.question_start

    for vc in range(num_vc):
        start_row = sum(questions_per_col[:vc])
        count = questions_per_col[vc] if vc < len(questions_per_col) else 0
        row_ys = grid.row_positions[start_row : start_row + count]

        # Column x-positions for this visual column
        col_xs = (
            grid.col_positions[vc]
            if grid.col_positions and vc < len(grid.col_positions)
            else []
        )

        for row_y in row_ys:
            if question_number > section.question_end:
                break

            override = section.question_overrides.get(question_number)
            q_type = override.type if override else section.type

            if override and override.region and override.region.w > 0:
                # Use override's explicit region
                r = override.region
                regions[question_number] = {
                    "x": r.x + dx, "y": r.y + dy, "w": r.w, "h": r.h,
                    "type": q_type,
                    "override": override,
                }
            elif section.type == "open_response":
                # Open response: each question is a single large box
                # col_xs has column left edges for multi-column; compute from grid
                block_width = section.region.w // num_vc
                box_x = section.region.x + dx + block_width * vc
                regions[question_number] = {
                    "x": box_x, "y": row_y + dy, "w": grid.cell_width, "h": grid.cell_height,
                    "type": q_type,
                    "override": override,
                }
            elif section.type == "numeric_grid" and col_xs:
                # Numeric grid: multiple cells per question in a row
                first_x = col_xs[0] + dx
                total_w = (len(col_xs) - 1) * grid.cell_width + grid.cell_width if col_xs else grid.cell_width
                regions[question_number] = {
                    "x": first_x, "y": row_y + dy, "w": total_w, "h": grid.cell_height,
                    "type": q_type,
                    "override": override,
                    "cell_positions": [x + dx for x in col_xs],
                    "cell_width": grid.cell_width,
                    "cell_height": grid.cell_height,
                }
            else:
                regions[question_number] = {
                    "x": section.region.x + dx, "y": row_y + dy,
                    "w": section.region.w, "h": grid.cell_height or 100,
                    "type": q_type,
                    "override": override,
                }

            question_number += 1

    return regions


# ---------------------------------------------------------------------------
# Tesseract OCR fallback for numeric grids
# ---------------------------------------------------------------------------

def _ocr_cell_tesseract(cell_img: np.ndarray) -> str:
    """OCR a single cell image using Tesseract. Returns a single character."""
    try:
        import pytesseract
    except ImportError:
        return ""

    gray = cell_img
    if len(cell_img.shape) == 3:
        gray = cv2.cvtColor(cell_img, cv2.COLOR_BGR2GRAY)

    # Check if cell is empty (mostly white) — skip if so
    _, bin_check = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY_INV)
    ink_ratio = cv2.countNonZero(bin_check) / max(1, bin_check.size)
    if ink_ratio < 0.08:  # less than 8% ink = empty cell (borders alone are ~3-5%)
        return ""

    # Upscale small cells for better OCR
    h, w = gray.shape
    if h < 50 or w < 50:
        scale = max(50 / h, 50 / w, 2.0)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # Threshold
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Try PSM 10 (single char) first, fall back to PSM 8 (single word)
    for psm in [10, 8]:
        text = pytesseract.image_to_string(
            thresh,
            config=f"--psm {psm} -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        ).strip()
        if text:
            return text[:1]

    return ""


def _ocr_row_tesseract(cell_img: np.ndarray) -> str:
    """OCR a row of cells (the full question grid row) as a single string."""
    try:
        import pytesseract
    except ImportError:
        return ""

    gray = cell_img
    if len(cell_img.shape) == 3:
        gray = cv2.cvtColor(cell_img, cv2.COLOR_BGR2GRAY)

    h, w = gray.shape
    if h < 60:
        scale = max(60 / h, 2.0)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    text = pytesseract.image_to_string(
        thresh,
        config="--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz./-",
    ).strip()

    return text


# ---------------------------------------------------------------------------
# Numeric grid extractor
# ---------------------------------------------------------------------------

def extract_numeric_grid(
    image: np.ndarray,
    section: AnswerSection,
    dx: int = 0,
    dy: int = 0,
    use_mathpix: bool = False,
) -> SectionExtractionResult:
    """
    Extract answers from a numeric grid section (one char per cell).

    Uses Tesseract by default. Set use_mathpix=True to use Mathpix API.
    """
    result = SectionExtractionResult(
        section_type="numeric_grid",
        question_start=section.question_start,
        question_end=section.question_end,
    )

    regions = _get_question_regions(section, dx, dy)

    for q_num, region in sorted(regions.items()):
        if region["type"] == "diagram":
            continue

        try:
            img_h, img_w = image.shape[:2]
            cell_positions = region.get("cell_positions", [])
            cell_w = region.get("cell_width", 88)
            cell_h = region.get("cell_height", 90)
            row_y = region["y"]

            if use_mathpix:
                from backend.services.mathpix_client import ocr_region
                x, y, w, h = region["x"], region["y"], region["w"], region["h"]
                text = ocr_region(image, x, y, w, h)
                cleaned = "".join(c for c in text if c.isprintable() and c != " ")
                result.answers[str(q_num)] = cleaned.upper()
                result.confidence[str(q_num)] = 1.0 if cleaned else 0.0
            elif cell_positions:
                # OCR each cell individually for better accuracy
                chars = []
                inset = max(6, cell_w // 10)  # ~10% inset to avoid borders
                for cx in cell_positions:
                    x1 = max(0, cx - cell_w // 2 + inset)
                    y1 = max(0, row_y + inset)
                    x2 = min(img_w, cx + cell_w // 2 - inset)
                    y2 = min(img_h, row_y + cell_h - inset)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    cell_crop = image[y1:y2, x1:x2]
                    ch = _ocr_cell_tesseract(cell_crop)
                    if ch:
                        chars.append(ch)
                cleaned = "".join(chars).upper()
                result.answers[str(q_num)] = cleaned
                result.confidence[str(q_num)] = 1.0 if cleaned else 0.0
            else:
                # Fallback: OCR the whole row
                x, y, w, h = region["x"], region["y"], region["w"], region["h"]
                x = max(0, min(x, img_w - 1))
                y = max(0, min(y, img_h - 1))
                w = min(w, img_w - x)
                h = min(h, img_h - y)
                if w > 0 and h > 0:
                    crop = image[y : y + h, x : x + w]
                    text = _ocr_row_tesseract(crop)
                    cleaned = "".join(c for c in text if c.isprintable() and c != " ")
                    result.answers[str(q_num)] = cleaned.upper()
                else:
                    result.answers[str(q_num)] = ""

        except Exception as exc:
            result.errors.append(f"Q{q_num}: {exc}")
            result.answers[str(q_num)] = ""

    return result


# ---------------------------------------------------------------------------
# Open response extractor
# ---------------------------------------------------------------------------

def extract_open_response(
    image: np.ndarray,
    section: AnswerSection,
    dx: int = 0,
    dy: int = 0,
    use_mathpix: bool = False,
) -> SectionExtractionResult:
    """
    Extract answers from open response boxes (free-form handwritten).

    Crops each question's box and OCRs it.
    """
    result = SectionExtractionResult(
        section_type="open_response",
        question_start=section.question_start,
        question_end=section.question_end,
    )

    regions = _get_question_regions(section, dx, dy)

    for q_num, region in sorted(regions.items()):
        try:
            x, y, w, h = region["x"], region["y"], region["w"], region["h"]
            img_h, img_w = image.shape[:2]
            x = max(0, min(x, img_w - 1))
            y = max(0, min(y, img_h - 1))
            w = min(w, img_w - x)
            h = min(h, img_h - y)

            if w <= 0 or h <= 0:
                result.answers[str(q_num)] = ""
                continue

            crop = image[y : y + h, x : x + w]

            if use_mathpix:
                from backend.services.mathpix_client import ocr_region
                text = ocr_region(image, x, y, w, h)
            else:
                text = _ocr_row_tesseract(crop)

            result.answers[str(q_num)] = text.strip()
            result.confidence[str(q_num)] = 1.0 if text.strip() else 0.0

        except Exception as exc:
            result.errors.append(f"Q{q_num}: {exc}")
            result.answers[str(q_num)] = ""

    return result


# ---------------------------------------------------------------------------
# Diagram extractor
# ---------------------------------------------------------------------------

def extract_diagrams(
    image: np.ndarray,
    section: AnswerSection,
    dx: int = 0,
    dy: int = 0,
) -> SectionExtractionResult:
    """
    Extract diagram question regions from a section.

    Crops the diagram area for each question that has a diagram override.
    Returns the cropped images in result.diagrams for downstream processing
    (Mathpix detection or Gemini vision interpretation).
    """
    result = SectionExtractionResult(
        section_type="diagram",
        question_start=section.question_start,
        question_end=section.question_end,
    )

    regions = _get_question_regions(section, dx, dy)

    for q_num, region in sorted(regions.items()):
        if region["type"] != "diagram":
            continue

        override = region.get("override")
        prompt_hint = override.prompt_hint if override else None

        try:
            x, y, w, h = region["x"], region["y"], region["w"], region["h"]
            img_h, img_w = image.shape[:2]
            x = max(0, min(x, img_w - 1))
            y = max(0, min(y, img_h - 1))
            w = min(w, img_w - x)
            h = min(h, img_h - y)

            if w <= 0 or h <= 0:
                continue

            crop = image[y : y + h, x : x + w]

            result.diagrams[str(q_num)] = {
                "crop": crop,
                "region": {"x": x, "y": y, "w": w, "h": h},
                "prompt_hint": prompt_hint,
            }
            # Mark as diagram — actual interpretation done by caller
            result.answers[str(q_num)] = "DR"

        except Exception as exc:
            result.errors.append(f"Q{q_num} diagram: {exc}")

    return result


# ---------------------------------------------------------------------------
# Unified section extractor
# ---------------------------------------------------------------------------

def extract_section(
    image: np.ndarray,
    section: AnswerSection,
    dx: int = 0,
    dy: int = 0,
    use_mathpix: bool = False,
) -> SectionExtractionResult:
    """
    Extract answers from any section type. Dispatches to the appropriate
    extractor based on section.type.
    """
    if section.type == "numeric_grid":
        result = extract_numeric_grid(image, section, dx, dy, use_mathpix)
        # Also extract any diagram overrides within this section
        diagrams = extract_diagrams(image, section, dx, dy)
        result.diagrams.update(diagrams.diagrams)
        for q, ans in diagrams.answers.items():
            if q not in result.answers or not result.answers[q]:
                result.answers[q] = ans
        return result

    elif section.type == "open_response":
        return extract_open_response(image, section, dx, dy, use_mathpix)

    elif section.type == "diagram":
        return extract_diagrams(image, section, dx, dy)

    else:
        logger.warning("Unknown section type: %s", section.type)
        return SectionExtractionResult(
            section_type=section.type,
            question_start=section.question_start,
            question_end=section.question_end,
        )


def extract_all_sections(
    image: np.ndarray,
    template: ExamTemplate,
    dx: int = 0,
    dy: int = 0,
    use_mathpix: bool = False,
) -> Dict[str, str]:
    """
    Extract answers from ALL non-MCQ sections in a template.

    Returns merged answers dict: {"1": "42", "2": "A", ...}
    """
    from backend.services.mcq_extractor import extract_page, _match_anchor
    from backend.services.template_service import get_template_registry

    # If dx/dy not provided, do anchor matching
    if dx == 0 and dy == 0 and template.anchor.region.w > 0:
        registry = get_template_registry()
        score, dx, dy, _ = _match_anchor(image, template, registry)

    all_answers: Dict[str, str] = {}
    all_diagrams: Dict[str, Dict[str, Any]] = {}

    for section in template.sections:
        if section.type == "mcq_grid":
            continue  # Handled by mcq_extractor

        result = extract_section(image, section, dx, dy, use_mathpix)
        all_answers.update(result.answers)
        all_diagrams.update(result.diagrams)

    return all_answers, all_diagrams
