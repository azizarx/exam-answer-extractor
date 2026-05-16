"""
Generalized MCQ grid extractor.

Replaces the hardcoded NewMcqSolution.py with a template-driven approach.
Given an ExamTemplate with one or more mcq_grid sections, extracts bubble
answers from scanned page images using CV ink scoring.

Algorithm (same proven logic as NewMcqSolution.py, now parameterized):
  1. Template-match the anchor region to find (dx, dy) page offset
  2. Shift all grid column positions by dx; compute row positions from
     the anchor's detected y-position + the known offsets
  3. For each question row, score ink pixels in each option bubble
  4. Pick the highest-scoring option if it exceeds thresholds
  5. Reject pages with low overall confidence
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from backend.services.template_service import (
    AnswerSection,
    ExamTemplate,
    GridGeometry,
    Region,
    ScoringParams,
    TemplateRegistry,
    get_template_registry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RowResult:
    """Extraction result for a single question row."""
    question: int
    answer: str  # option label or "BL" for blank/unresolved
    best_score: int = 0
    second_score: int = 0
    ratio: float = 0.0
    scores: Dict[str, int] = field(default_factory=dict)


@dataclass
class SectionResult:
    """Extraction result for one MCQ grid section."""
    section_type: str
    question_start: int
    question_end: int
    rows: List[RowResult] = field(default_factory=list)

    @property
    def answers(self) -> Dict[str, str]:
        return {str(r.question): r.answer for r in self.rows}

    @property
    def resolved_count(self) -> int:
        return sum(1 for r in self.rows if r.answer != "BL")

    @property
    def total_count(self) -> int:
        return len(self.rows)

    @property
    def coverage(self) -> float:
        return self.resolved_count / max(1, self.total_count)


@dataclass
class PageResult:
    """Full extraction result for a single page image.

    Status semantics in the new pipeline:
      "ok"     — extraction ran (whether confident or not). The user picked
                 this template; we emit whatever we got. `warning` may flag
                 quality concerns the caller can surface.
      "error"  — extraction couldn't run (image unreadable, anchor not
                 present at all, etc.). No answers.
    """
    page_number: int
    template_id: str
    status: str
    anchor_score: float = 0.0
    dx: int = 0
    dy: int = 0
    sections: List[SectionResult] = field(default_factory=list)
    reason: Optional[str] = None  # set when status == "error"
    warning: Optional[str] = None  # advisory; set when CV quality was low

    @property
    def answers(self) -> Dict[str, str]:
        """Merged answers from all sections."""
        merged: Dict[str, str] = {}
        for s in self.sections:
            merged.update(s.answers)
        return merged

    @property
    def coverage(self) -> float:
        total = sum(s.total_count for s in self.sections)
        resolved = sum(s.resolved_count for s in self.sections)
        return resolved / max(1, total)

    @property
    def diagnostics(self) -> dict:
        return {
            "status": self.status,
            "template_id": self.template_id,
            "anchor_score": round(self.anchor_score, 4),
            "dx": self.dx,
            "dy": self.dy,
            "coverage": round(self.coverage, 4),
            "reason": self.reason,
            "warning": self.warning,
        }


# ---------------------------------------------------------------------------
# Core scoring (unchanged algorithm from NewMcqSolution.py)
# ---------------------------------------------------------------------------

def _score_bubbles(
    bin_inv: np.ndarray,
    row_y: int,
    col_positions: List[int],
    cell_width: int,
    cell_height: int,
    bubble_height: int,
    labels: List[str],
) -> Dict[str, int]:
    """
    Score ink pixels for each option bubble in a single question row.

    Args:
        bin_inv: Binary-inverted grayscale image (ink pixels are white/255).
        row_y: Y-coordinate of the row (top of the cell area).
        col_positions: X-center of each option column.
        cell_width: Width of one bubble cell.
        cell_height: Height of the label area above the bubble.
        bubble_height: Height of the actual fill region below the label.
        labels: Option labels in column order, e.g. ["A", "B", "C"].

    Returns:
        Dict mapping option label to ink pixel count.
    """
    page_h, page_w = bin_inv.shape[:2]
    scores: Dict[str, int] = {}

    for col_idx, x_center in enumerate(col_positions):
        if col_idx >= len(labels):
            break

        x1 = int(x_center - cell_width / 2)
        y1 = row_y + cell_height  # skip the label area
        x2 = int(x_center + cell_width / 2)
        y2 = y1 + bubble_height

        # Clamp to image bounds
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(page_w, x2)
        y2 = min(page_h, y2)

        if x2 <= x1 or y2 <= y1:
            scores[labels[col_idx]] = 0
            continue

        roi = bin_inv[y1:y2, x1:x2]
        scores[labels[col_idx]] = int(cv2.countNonZero(roi))

    return scores


def _classify_row(
    scores: Dict[str, int],
    scoring: ScoringParams,
) -> Tuple[str, int, int, float]:
    """
    Classify a row's scores into an answer or "BL".

    Returns:
        (answer, best_score, second_score, ratio)
    """
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    ratio = float(best_score) / float(max(1, second_score))

    if best_score < scoring.min_ink_pixels or ratio < scoring.min_ratio:
        return ("BL", best_score, second_score, ratio)

    return (best_label, best_score, second_score, ratio)


# ---------------------------------------------------------------------------
# Grid position computation
# ---------------------------------------------------------------------------

def _compute_row_positions(
    grid: GridGeometry,
    section_region: Region,
    dy: int,
    visual_col: int,
) -> List[int]:
    """
    Compute absolute Y positions for question rows in a visual column.

    If explicit row_positions are provided in the grid, shift them by dy.
    Otherwise, compute from first_row_offset + row_pitch.

    For multi-column layouts (e.g. 2-column 10+5), the row_positions
    list covers ALL rows sequentially. We slice the appropriate range
    for each visual column based on questions_per_col.
    """
    questions_per_col = grid.questions_per_col or [grid.rows]

    # Determine which rows belong to this visual column
    start_row = sum(questions_per_col[:visual_col])
    count = questions_per_col[visual_col] if visual_col < len(questions_per_col) else 0

    if grid.row_positions:
        # Explicit positions: shift by vertical offset
        all_positions = [int(y + dy) for y in grid.row_positions]
        return all_positions[start_row : start_row + count]

    # Computed from pitch
    base_y = section_region.y + dy + grid.first_row_offset
    all_positions = [int(base_y + i * grid.row_pitch) for i in range(grid.rows)]
    return all_positions[start_row : start_row + count]


def _compute_col_positions(
    grid: GridGeometry,
    section_region: Region,
    dx: int,
    visual_col: int,
) -> List[int]:
    """
    Compute absolute X positions for option columns within a visual column.

    col_positions is a list-of-lists: one inner list per visual column.
    Single-column templates have [[x1,x2,...]], two-column have
    [[left_x1,...],[right_x1,...]].
    """
    if grid.col_positions and visual_col < len(grid.col_positions):
        return [int(x + dx) for x in grid.col_positions[visual_col]]

    # Fallback: compute from col_pitch
    if grid.col_pitch > 0:
        num_visual_cols = grid.cols or 1
        num_options = len(grid.options) or 1
        col_block_width = section_region.w // num_visual_cols
        block_x = section_region.x + dx + col_block_width * visual_col
        total_option_width = (num_options - 1) * grid.col_pitch
        start_x = block_x + (col_block_width - total_option_width) / 2
        return [int(start_x + i * grid.col_pitch) for i in range(num_options)]

    return []


# ---------------------------------------------------------------------------
# Section-level extraction
# ---------------------------------------------------------------------------

def extract_mcq_section(
    bin_inv: np.ndarray,
    section: AnswerSection,
    dx: int,
    dy: int,
) -> SectionResult:
    """
    Extract MCQ answers from one section of a page.

    Args:
        bin_inv: Binary-inverted grayscale image.
        section: The mcq_grid section definition from the template.
        dx: Horizontal offset from anchor matching.
        dy: Vertical offset from anchor matching.

    Returns:
        SectionResult with per-row extraction results.
    """
    grid = section.grid
    scoring = section.scoring or ScoringParams()
    labels = grid.options if grid else []

    if not grid or not labels:
        return SectionResult(
            section_type=section.type,
            question_start=section.question_start,
            question_end=section.question_end,
        )

    result = SectionResult(
        section_type=section.type,
        question_start=section.question_start,
        question_end=section.question_end,
    )

    num_visual_cols = grid.cols or 1
    questions_per_col = grid.questions_per_col or [grid.rows]
    question_number = section.question_start

    for vc in range(num_visual_cols):
        row_ys = _compute_row_positions(grid, section.region, dy, vc)
        col_xs = _compute_col_positions(grid, section.region, dx, vc)

        if not row_ys or not col_xs:
            # Not enough geometry to extract — fill with BL
            count = questions_per_col[vc] if vc < len(questions_per_col) else 0
            for _ in range(count):
                result.rows.append(RowResult(
                    question=question_number,
                    answer="BL",
                ))
                question_number += 1
            continue

        for row_y in row_ys:
            if question_number > section.question_end:
                break

            scores = _score_bubbles(
                bin_inv=bin_inv,
                row_y=row_y,
                col_positions=col_xs,
                cell_width=grid.cell_width,
                cell_height=grid.cell_height,
                bubble_height=grid.bubble_height,
                labels=labels,
            )

            answer, best, second, ratio = _classify_row(scores, scoring)

            result.rows.append(RowResult(
                question=question_number,
                answer=answer,
                best_score=best,
                second_score=second,
                ratio=ratio,
                scores=scores,
            ))
            question_number += 1

    return result


# ---------------------------------------------------------------------------
# Page-level extraction
# ---------------------------------------------------------------------------

def _match_anchor(
    image: np.ndarray,
    template: ExamTemplate,
    registry: Optional[TemplateRegistry] = None,
) -> Tuple[float, int, int, Optional[np.ndarray]]:
    """
    Match the template's anchor region against the page image.

    Returns:
        (match_score, dx, dy, anchor_template_image)
    """
    if registry is None:
        registry = get_template_registry()

    anchor_img = registry._get_anchor_image(template, image)
    if anchor_img is None:
        return (0.0, 0, 0, None)

    gray_page = image
    if len(image.shape) == 3:
        gray_page = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    gray_anchor = anchor_img
    if len(anchor_img.shape) == 3:
        gray_anchor = cv2.cvtColor(anchor_img, cv2.COLOR_BGR2GRAY)

    try:
        res = cv2.matchTemplate(gray_page, gray_anchor, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
    except cv2.error as exc:
        logger.warning("Anchor match failed: %s", exc)
        return (0.0, 0, 0, None)

    dx = int(max_loc[0] - template.anchor.region.x)
    dy = int(max_loc[1] - template.anchor.region.y)

    return (float(max_val), dx, dy, anchor_img)


def extract_page(
    image: np.ndarray,
    template: ExamTemplate,
    page_number: int = 1,
    registry: Optional[TemplateRegistry] = None,
) -> PageResult:
    """
    Extract MCQ answers from a single page image using the given template.

    Args:
        image: BGR page image (numpy array).
        template: The ExamTemplate to use.
        page_number: 1-based page number for reporting.
        registry: Optional registry for anchor image lookup.

    Returns:
        PageResult with answers and diagnostics.
    """
    # Step 1: Anchor matching
    anchor_score, dx, dy, _ = _match_anchor(image, template, registry)
    logger.info(
        "MCQ[%d/%s] anchor score=%.3f offset=(%d,%d) min_required=%.2f",
        page_number, template.id, anchor_score, dx, dy, template.anchor.min_match_score,
    )

    low_anchor_warning: Optional[str] = None
    if anchor_score < template.anchor.min_match_score:
        # Advisory only — the user picked this template; we still attempt
        # extraction with whatever offset matchTemplate produced. Bad anchor
        # likely means a wrong template choice (user error per policy).
        logger.info("MCQ[%d/%s] ADVISORY anchor_match_low score=%.3f",
                    page_number, template.id, anchor_score)
        low_anchor_warning = "anchor_match_low"

    # Step 2: Binarize
    mcq_sections = [s for s in template.sections if s.type == "mcq_grid"]
    if not mcq_sections:
        # No MCQ on this template — return ok with empty sections. The caller
        # will simply have nothing to overlay on the LLM result.
        logger.info("MCQ[%d/%s] no mcq sections; nothing to do", page_number, template.id)
        return PageResult(
            page_number=page_number,
            template_id=template.id,
            status="ok",
            anchor_score=anchor_score,
            dx=dx,
            dy=dy,
            warning=low_anchor_warning,
        )

    # Use the threshold from the first MCQ section (usually consistent)
    threshold = (mcq_sections[0].scoring or ScoringParams()).binary_threshold

    gray = image
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, bin_inv = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)

    # Step 3: Extract each MCQ section
    section_results: List[SectionResult] = []
    for section in mcq_sections:
        sr = extract_mcq_section(bin_inv, section, dx, dy)
        section_results.append(sr)

    # Step 4: Page-level quality check (advisory only).
    result = PageResult(
        page_number=page_number,
        template_id=template.id,
        status="ok",
        anchor_score=anchor_score,
        dx=dx,
        dy=dy,
        sections=section_results,
        warning=low_anchor_warning,
    )

    scoring = mcq_sections[0].scoring or ScoringParams()
    all_rows = [r for s in section_results for r in s.rows]

    if not all_rows:
        logger.info("MCQ[%d/%s] no rows extracted", page_number, template.id)
        if not result.warning:
            result.warning = "no_rows_extracted"
        return result

    avg_ratio = sum(r.ratio for r in all_rows) / len(all_rows)
    logger.info(
        "MCQ[%d/%s] coverage=%.2f avg_ratio=%.2f (advisory thresholds: cov>=%.2f ratio>=%.2f) rows=%d resolved=%d",
        page_number, template.id, result.coverage, avg_ratio,
        scoring.min_page_coverage, scoring.min_avg_ratio,
        len(all_rows), sum(s.resolved_count for s in section_results),
    )

    if result.coverage < scoring.min_page_coverage and not result.warning:
        result.warning = "low_coverage"
    elif avg_ratio < scoring.min_avg_ratio and not result.warning:
        result.warning = "low_avg_ratio"

    logger.info(
        "MCQ[%d/%s] ACCEPT answers=%d warning=%s",
        page_number, template.id,
        sum(s.resolved_count for s in section_results),
        result.warning,
    )
    return result


# ---------------------------------------------------------------------------
# Multi-page extraction (replaces extract_uz_mcq_from_images)
# ---------------------------------------------------------------------------

def extract_mcq_from_images(
    image_paths: List[str],
    template: ExamTemplate,
    registry: Optional[TemplateRegistry] = None,
) -> Tuple[Dict[int, Dict[str, str]], Dict[int, dict]]:
    """
    Extract MCQ answers from multiple page images using a template.

    Drop-in replacement for NewMcqSolution.extract_uz_mcq_from_images().

    Args:
        image_paths: List of image file paths.
        template: The ExamTemplate defining the MCQ grid layout.
        registry: Optional registry for anchor image lookup.

    Returns:
        answers_by_page: {page_number: {"1": "A", "2": "C", ...}}
        diagnostics_by_page: {page_number: {...diagnostics...}}
    """
    if not image_paths:
        return {}, {}

    if registry is None:
        registry = get_template_registry()

    # If the template has no calibrated anchor, try to build one from
    # the first page (same approach as NewMcqSolution.py).
    if template.anchor.region.w > 0 and template.id not in registry._anchor_images:
        first_img = cv2.imread(str(image_paths[0]))
        if first_img is not None:
            r = template.anchor.region
            h, w = first_img.shape[:2]
            if r.y + r.h <= h and r.x + r.w <= w:
                anchor = first_img[r.y : r.y + r.h, r.x : r.x + r.w].copy()
                registry._anchor_images[template.id] = anchor
                logger.info(
                    "Built anchor image from first page for template %s",
                    template.id,
                )

    answers_by_page: Dict[int, Dict[str, str]] = {}
    diagnostics_by_page: Dict[int, dict] = {}

    for page_idx, image_path in enumerate(image_paths, start=1):
        img = cv2.imread(str(image_path))
        if img is None:
            diagnostics_by_page[page_idx] = {
                "status": "error",
                "reason": "image_not_readable",
            }
            continue

        result = extract_page(img, template, page_number=page_idx, registry=registry)
        diagnostics_by_page[page_idx] = result.diagnostics

        if result.status == "ok":
            answers_by_page[page_idx] = result.answers

    accepted = len(answers_by_page)
    total = len(image_paths)
    logger.info(
        "MCQ extraction complete: template=%s pages=%d/%d accepted",
        template.id,
        accepted,
        total,
    )

    return answers_by_page, diagnostics_by_page


# ---------------------------------------------------------------------------
# Auto-detect + extract (convenience)
# ---------------------------------------------------------------------------

def auto_extract_mcq(
    image_paths: List[str],
    filename: Optional[str] = None,
    template_id: Optional[str] = None,
) -> Tuple[Dict[int, Dict[str, str]], Dict[int, dict], Optional[str]]:
    """
    Auto-detect the template and extract MCQ answers.

    If template_id is provided, uses that template directly.
    Otherwise, attempts auto-detection from the first page image.

    Args:
        image_paths: List of image file paths.
        filename: Original upload filename (for detection hints).
        template_id: Explicit template ID to use (skips detection).

    Returns:
        answers_by_page: {page_number: {"1": "A", ...}}
        diagnostics_by_page: {page_number: {...}}
        detected_template_id: The template ID used (or None if detection failed).
    """
    if not image_paths:
        return {}, {}, None

    registry = get_template_registry()

    # Explicit template
    if template_id:
        template = registry.get(template_id)
        if not template:
            logger.error("Unknown template_id: %s", template_id)
            return {}, {}, None
        if not template.has_mcq:
            logger.warning("Template %s has no MCQ sections", template_id)
            return {}, {}, template_id
        answers, diag = extract_mcq_from_images(image_paths, template, registry)
        return answers, diag, template_id

    # Auto-detect from first page
    first_img = cv2.imread(str(image_paths[0]))
    if first_img is None:
        return {}, {1: {"status": "error", "reason": "image_not_readable"}}, None

    detection = registry.detect(first_img, filename=filename)
    if detection is None:
        logger.info("No template matched for auto-detection")
        return {}, {}, None

    template, score, (dx, dy) = detection
    if not template.has_mcq:
        logger.info("Detected template %s but it has no MCQ sections", template.id)
        return {}, {}, template.id

    logger.info(
        "Auto-detected template %s (score=%.3f, offset=(%d,%d))",
        template.id, score, dx, dy,
    )

    answers, diag = extract_mcq_from_images(image_paths, template, registry)
    return answers, diag, template.id
