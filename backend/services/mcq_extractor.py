"""
Generalized MCQ grid extractor.

Replaces the hardcoded NewMcqSolution.py with a template-driven approach.
Given an ExamTemplate with one or more mcq_grid sections, extracts bubble
answers from scanned page images using CV ink scoring.

Algorithm:
  1. Detect the synchronized lattice of printed option labels when available
  2. Fall back to anchor / affine hypotheses when that geometry is unsupported
  3. For each question row, score ink pixels in each option bubble
  4. Pick the highest-scoring option if it exceeds thresholds
  5. Surface weak classifications for review without moving trusted geometry
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# Offsets beyond this are almost always a false lock (e.g. thin rule → examiner
# bar, dx≈1400). Real page-to-page shifts on format-B gold are typically ≤120px.
# Values are in reference-DPI pixels; extract_page scales them to the page.
FALSE_LOCK_DX_THRESHOLD = 300
FALSE_LOCK_DY_THRESHOLD = 300

# Legacy names kept for importers / tests. Used only as a soft "large shift"
# log marker — no longer auto-zero the offset (see dual-hypothesis below).
HUGE_DY_THRESHOLD = 25
HUGE_DX_THRESHOLD = 25

# Offsets above this (at reference DPI) force a notable_offset warning even
# when under the huge_* clamp — moderate drift is still unsafe to auto-trust.
# (Reserved; not currently used for whole-page review — E/B good pages drift ~12px.)
NOTABLE_OFFSET_THRESHOLD = 10

# Letter answers whose winner/runner-up ratio is below min_ratio + margin are
# treated as ambiguous and flagged for review (bottom-of-page warp).
RATIO_TRUST_MARGIN = 0.15

# When coverage looks fine but median ink among resolved letters is weak,
# printed glyphs are often being scored as fills (Paper B blank sheets).
WEAK_FILL_MEDIAN_THRESHOLD = 360

# Uneven photocopy backgrounds can cross the normal binary threshold inside
# every option box and make one real mark look like a multi-mark.  Integrated
# darkness below the same threshold preserves how much darker the intended
# fill is instead of reducing every dark pixel to a binary vote.
INK_ENERGY_MIN_RATIO = 1.20
INK_ENERGY_MIN_MEAN_DEFICIT = 20.0


@dataclass
class RowResult:
    """Extraction result for a single question row."""
    question: int
    answer: str  # option label, "BL", or "IN"
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
    # Absolute MCQ AnswerSections used for scoring when lattice align applied
    # (dx=dy=0). Overlay/QA should draw these instead of the median template.
    overlay_mcq_sections: Optional[List[Any]] = None

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
    Classify a row's scores into an answer, "BL", or "IN" (multi-fill).

    Returns:
        (answer, best_score, second_score, ratio)
    """
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    ratio = float(best_score) / float(max(1, second_score))

    # Two clear fills with no dominant winner → invalid multi-mark.
    if (
        best_score >= scoring.min_ink_pixels
        and second_score >= scoring.min_ink_pixels
        and ratio < scoring.min_ratio
    ):
        return ("IN", best_score, second_score, ratio)

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


def _apply_classic_ink_energy(
    gray: np.ndarray,
    section: AnswerSection,
    result: SectionResult,
    *,
    base_threshold: int,
) -> SectionResult:
    """Recover or confirm one dark mark on a noisy classic sheet.

    Binary counting remains authoritative for blanks and confident letters.
    For an apparent multi-mark or thin-ratio letter, integrate each pixel's
    darkness below the normal threshold.  A large, dominant energy winner is a
    real fill; genuine double marks retain two comparable energies and remain
    ``IN``.  A letter is never changed here: matching energy can only strengthen
    its confidence, while disagreement remains flagged for review.
    """
    scoring = section.scoring or ScoringParams()
    review_ratio = float(scoring.min_ratio) + RATIO_TRUST_MARGIN
    if not any(
        row.answer == "IN"
        or (
            row.answer not in ("", "BL")
            and row.ratio < review_ratio
        )
        for row in result.rows
    ):
        return result

    grid = section.grid
    if grid is None:
        return result

    labels = list(grid.options)
    questions_per_col = grid.questions_per_col or [grid.rows]
    row_index = 0
    for visual_col in range(grid.cols or 1):
        row_ys = _compute_row_positions(
            grid,
            section.region,
            0,
            visual_col,
        )
        col_xs = _compute_col_positions(
            grid,
            section.region,
            0,
            visual_col,
        )
        count = (
            questions_per_col[visual_col]
            if visual_col < len(questions_per_col)
            else 0
        )
        for row_y in row_ys[:count]:
            if row_index >= len(result.rows):
                break
            current = result.rows[row_index]
            is_letter = current.answer not in ("", "BL", "IN")
            if current.answer != "IN" and not (
                is_letter and current.ratio < review_ratio
            ):
                row_index += 1
                continue

            energies: Dict[str, int] = {}
            areas: Dict[str, int] = {}
            page_h, page_w = gray.shape[:2]
            for option_index, x_center in enumerate(col_xs):
                if option_index >= len(labels):
                    break
                label = labels[option_index]
                x1 = max(0, int(x_center - grid.cell_width / 2))
                y1 = max(0, int(row_y + grid.cell_height))
                x2 = min(page_w, int(x_center + grid.cell_width / 2))
                y2 = min(page_h, int(y1 + grid.bubble_height))
                roi = gray[y1:y2, x1:x2]
                areas[label] = int(roi.size)
                if roi.size:
                    deficit = np.maximum(
                        0,
                        int(base_threshold) - roi.astype(np.int16),
                    )
                    energies[label] = int(deficit.sum())
                else:
                    energies[label] = 0

            ranked = sorted(
                energies.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if len(ranked) >= 2:
                best_label, best_energy = ranked[0]
                second_energy = ranked[1][1]
                ratio = float(best_energy) / float(max(1, second_energy))
                mean_deficit = float(best_energy) / float(
                    max(1, areas.get(best_label, 0))
                )
                if (
                    ratio >= INK_ENERGY_MIN_RATIO
                    and mean_deficit >= INK_ENERGY_MIN_MEAN_DEFICIT
                    and (
                        current.answer == "IN"
                        or best_label == current.answer
                    )
                ):
                    result.rows[row_index] = RowResult(
                        question=current.question,
                        answer=best_label,
                        best_score=best_energy,
                        second_score=second_energy,
                        ratio=ratio,
                        scores=energies,
                    )
            row_index += 1

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

    # Disk / primed anchors are stored at the template's reference scale.
    # After adapted_to_image(), resize so matchTemplate geometry lines up.
    tw = int(template.anchor.region.w)
    th = int(template.anchor.region.h)
    if tw > 0 and th > 0 and (anchor_img.shape[1] != tw or anchor_img.shape[0] != th):
        interp = cv2.INTER_AREA if (
            anchor_img.shape[1] > tw or anchor_img.shape[0] > th
        ) else cv2.INTER_LINEAR
        anchor_img = cv2.resize(anchor_img, (tw, th), interpolation=interp)

    gray_page = image
    if len(image.shape) == 3:
        gray_page = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    gray_anchor = anchor_img
    if len(anchor_img.shape) == 3:
        gray_anchor = cv2.cvtColor(anchor_img, cv2.COLOR_BGR2GRAY)

    try:
        # Search only the upper portion of the page so a thin rule cannot
        # false-lock onto the examiner box at the bottom.
        search_h = max(gray_anchor.shape[0] + 8, int(gray_page.shape[0] * 0.55))
        search_h = min(search_h, gray_page.shape[0])
        search_region = gray_page[0:search_h, :]
        res = cv2.matchTemplate(search_region, gray_anchor, cv2.TM_CCOEFF_NORMED)
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
    *,
    deskew: bool = True,
) -> PageResult:
    """
    Extract MCQ answers from a single page image using the given template.

    Args:
        image: BGR page image (numpy array).
        template: The ExamTemplate to use.
        page_number: 1-based page number for reporting.
        registry: Optional registry for anchor image lookup.
        deskew: When True, lightly deskew before anchor matching.

    Returns:
        PageResult with answers and diagnostics.
    """
    from backend.services.page_deskew import deskew_if_enabled

    image, applied = deskew_if_enabled(image, enabled=deskew)
    if applied:
        logger.info("MCQ[%d/%s] deskew_applied=%.2fdeg", page_number, template.id, applied)

    template, scale = template.adapted_to_image(image)
    if abs(scale - 1.0) >= 0.10:
        logger.info(
            "MCQ[%d/%s] template_scaled factor=%.3f page=%dx%d",
            page_number, template.id, scale,
            image.shape[1], image.shape[0],
        )
    # Find/binarize the MCQ area before doing legacy anchor work.  The printed
    # label lattice is self-registering, so a successful fit makes the much
    # more expensive full-page template match unnecessary.
    mcq_sections = [s for s in template.sections if s.type == "mcq_grid"]
    if not mcq_sections:
        logger.info("MCQ[%d/%s] no mcq sections; nothing to do", page_number, template.id)
        return PageResult(
            page_number=page_number,
            template_id=template.id,
            status="ok",
            anchor_score=1.0,
            dx=0,
            dy=0,
        )

    threshold = (mcq_sections[0].scoring or ScoringParams()).binary_threshold
    gray = image
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, bin_inv = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)

    # Primary geometry path for forms with a repeated printed option-label
    # lattice.  Unlike anchor/ink ranking, this is synchronized across all
    # option columns and all rows, so it cannot silently choose the adjacent
    # question-number column or the option-letter band as a fill band.
    from backend.services.mcq_label_lattice import align_mcq_section_from_labels

    label_results: List[SectionResult] = []
    label_geometry: List[AnswerSection] = []
    label_fits = []
    label_thresholds: List[int] = []
    for section in mcq_sections:
        geometry_thresholds = [
            threshold,
            *(
                candidate
                for candidate in (160, 150, 140, 130, 120, 110, 100)
                if candidate < threshold
            ),
        ]
        aligned = None
        label_fit = None
        chosen_threshold = threshold
        successful_fits = []
        for geometry_threshold in dict.fromkeys(geometry_thresholds):
            candidate_section, candidate_fit = align_mcq_section_from_labels(
                gray,
                section,
                binary_threshold=geometry_threshold,
            )
            label_fit = candidate_fit
            if candidate_section is not None and candidate_fit.ok:
                successful_fits.append(
                    (candidate_section, candidate_fit, geometry_threshold)
                )
            if candidate_fit.warning == "label_lattice_unsupported_geometry":
                break
        assert label_fit is not None
        if successful_fits:
            aligned, label_fit, chosen_threshold = max(
                successful_fits,
                key=lambda candidate: (
                    candidate[1].n_observed_rows,
                    candidate[1].n_strong_rows,
                    -candidate[1].n_inferred_rows,
                    -candidate[1].row_rmse,
                    -candidate[1].col_rmse,
                ),
            )
        label_fits.append(label_fit)
        label_thresholds.append(chosen_threshold)
        if aligned is None or not label_fit.ok:
            break
        label_geometry.append(aligned)
        section_result = extract_mcq_section(bin_inv, aligned, 0, 0)
        if not template.id.endswith("_fb"):
            section_result = _apply_classic_ink_energy(
                gray,
                aligned,
                section_result,
                base_threshold=threshold,
            )
        label_results.append(section_result)

    if len(label_results) == len(mcq_sections):
        result = PageResult(
            page_number=page_number,
            template_id=template.id,
            status="ok",
            # The lattice itself is the registration signal on this path.
            # Report a fully trusted registration rather than a skipped anchor.
            anchor_score=1.0,
            dx=0,
            dy=0,
            sections=label_results,
            overlay_mcq_sections=label_geometry,
        )
        logger.info(
            "MCQ[%d/%s] label_lattice_applied coverage=%.2f fits=%s",
            page_number,
            template.id,
            result.coverage,
            [
                {
                    "sx": round(fit.sx, 3),
                    "sy": round(fit.sy, 3),
                    "col_rmse": round(fit.col_rmse, 2),
                    "row_rmse": round(fit.row_rmse, 2),
                    "observed_rows": fit.n_observed_rows,
                    "inferred_rows": fit.n_inferred_rows,
                }
                for fit in label_fits
            ],
        )
        if any(value != threshold for value in label_thresholds):
            logger.info(
                "MCQ[%d/%s] label_lattice_thresholds=%s scoring_threshold=%d",
                page_number,
                template.id,
                label_thresholds,
                threshold,
            )
        return result

    if label_fits:
        logger.info(
            "MCQ[%d/%s] label_lattice_unavailable warn=%s; using legacy fallback",
            page_number,
            template.id,
            label_fits[-1].warning,
        )

    # Legacy fallback only: template-match the anchor and evaluate both the
    # identity and plausible shifted hypotheses.
    false_dx = max(8, int(round(FALSE_LOCK_DX_THRESHOLD * scale)))
    false_dy = max(8, int(round(FALSE_LOCK_DY_THRESHOLD * scale)))
    huge_dx = max(8, int(round(HUGE_DX_THRESHOLD * scale)))
    huge_dy = max(8, int(round(HUGE_DY_THRESHOLD * scale)))
    anchor_score, raw_dx, raw_dy, _ = _match_anchor(image, template, registry)
    logger.info(
        "MCQ[%d/%s] anchor score=%.3f offset=(%d,%d) min_required=%.2f",
        page_number, template.id, anchor_score, raw_dx, raw_dy,
        template.anchor.min_match_score,
    )

    # Step 3: Dual-hypothesis offset selection.
    # Previously we zeroed any low-score or |offset|>25 lock. That discarded
    # real ~50–120px page shifts while false locks (~1400px) need rejecting.
    # Try identity always; also try the anchor offset unless it is absurd.
    # Pick by median letter ink (then coverage/ratio) so printed glyphs don't win.
    candidates: List[Tuple[int, int]] = [(0, 0)]
    if (raw_dx, raw_dy) != (0, 0):
        if abs(raw_dx) > false_dx or abs(raw_dy) > false_dy:
            logger.info(
                "MCQ[%d/%s] skip_false_lock |dx|=%d|dy|=%d thresholds=(%d,%d) "
                "score=%.3f — trying identity only",
                page_number, template.id, abs(raw_dx), abs(raw_dy),
                false_dx, false_dy, anchor_score,
            )
        else:
            candidates.append((raw_dx, raw_dy))

    def _run_offset(dx: int, dy: int) -> Tuple[List[SectionResult], float, float, float]:
        sections = [extract_mcq_section(bin_inv, section, dx, dy) for section in mcq_sections]
        rows = [r for s in sections for r in s.rows]
        if not rows:
            return sections, 0.0, 0.0, 0.0
        resolved = sum(s.resolved_count for s in sections)
        total = sum(s.total_count for s in sections)
        cov = resolved / max(1, total)
        avg_r = sum(r.ratio for r in rows) / len(rows)
        letter_ink = sorted(
            r.best_score for r in rows if r.answer not in ("BL", "IN", "")
        )
        med_ink = float(letter_ink[len(letter_ink) // 2]) if letter_ink else 0.0
        return sections, cov, avg_r, med_ink

    best_sections: List[SectionResult] = []
    best_dx, best_dy = 0, 0
    best_cov, best_ratio, best_med_ink = -1.0, -1.0, -1.0
    for cdx, cdy in candidates:
        sections, cov, avg_r, med_ink = _run_offset(cdx, cdy)
        # Prefer real dark fills over high "coverage" from printed glyphs.
        key = (med_ink, cov, avg_r, -(abs(cdx) + abs(cdy)))
        best_key = (best_med_ink, best_cov, best_ratio, -(abs(best_dx) + abs(best_dy)))
        if key > best_key:
            best_sections, best_dx, best_dy = sections, cdx, cdy
            best_cov, best_ratio, best_med_ink = cov, avg_r, med_ink

    if len(candidates) > 1:
        logger.info(
            "MCQ[%d/%s] offset_pick chosen=(%d,%d) cov=%.2f ratio=%.2f med_ink=%.0f "
            "candidates=%s raw=(%d,%d)",
            page_number, template.id, best_dx, best_dy, best_cov, best_ratio,
            best_med_ink, candidates, raw_dx, raw_dy,
        )

    section_results = best_sections
    dx, dy = best_dx, best_dy

    # Step 3b: Per-page anisotropic lattice alignment (sx,sy,tx,ty).
    # Column + row lattice detection (LS); fail closed to translation.
    from backend.services.mcq_lattice_align import (
        align_mcq_section,
        sanitize_lattice_dy_prior,
    )

    scoring = mcq_sections[0].scoring or ScoringParams()
    dx_prior = float(raw_dx) if abs(raw_dx) <= false_dx else float(dx)
    dy_prior = float(raw_dy) if abs(raw_dy) <= false_dy else float(dy)
    if abs(raw_dx) > false_dx or abs(raw_dy) > false_dy:
        dx_prior, dy_prior = float(dx), float(dy)
    dy_prior = sanitize_lattice_dy_prior(dy_prior, anchor_score=float(anchor_score))

    lattice_used = False
    lattice_warning: Optional[str] = None
    lattice_sections: List[SectionResult] = []
    lattice_geom: List[AnswerSection] = []
    lattice_ok_all = True
    for section in mcq_sections:
        new_sec, fit = align_mcq_section(
            gray,
            section,
            binary_threshold=threshold,
            dx_prior=dx_prior,
            dy_prior=dy_prior,
            anchor_score=float(anchor_score),
        )
        if not fit.ok or new_sec is None:
            lattice_ok_all = False
            lattice_warning = fit.warning or "lattice_align_failed"
            break
        sr = extract_mcq_section(bin_inv, new_sec, 0, 0)
        lattice_sections.append(sr)
        lattice_geom.append(new_sec)

    if lattice_ok_all and lattice_sections:
        lat_rows = [r for s in lattice_sections for r in s.rows]
        lat_resolved = sum(s.resolved_count for s in lattice_sections)
        lat_total = sum(s.total_count for s in lattice_sections)
        lat_cov = lat_resolved / max(1, lat_total)
        lat_letter = sorted(
            r.best_score for r in lat_rows if r.answer not in ("BL", "IN", "")
        )
        lat_med = float(lat_letter[len(lat_letter) // 2]) if lat_letter else 0.0
        lat_avg_r = (
            sum(r.ratio for r in lat_rows) / len(lat_rows) if lat_rows else 0.0
        )
        n_in = sum(1 for r in lat_rows if r.answer == "IN")
        weak_letter = any(
            r.answer not in ("BL", "IN", "") and r.best_score < 250
            for r in lat_rows
        )
        tmp = PageResult(
            page_number=page_number,
            template_id=template.id,
            status="ok",
            sections=lattice_sections,
        )
        amb_hard = hard_ambiguous_mcq_questions(
            tmp,
            min_ratio=float(scoring.min_ratio),
            min_ink_pixels=int(scoring.min_ink_pixels),
        )
        if lat_cov < scoring.min_page_coverage:
            lattice_warning = lattice_warning or "low_coverage"
        elif lat_avg_r < scoring.min_avg_ratio:
            lattice_warning = lattice_warning or "low_avg_ratio"
        # Blank-aware: low median ink is only a page-geometry smell when most
        # rows resolved to letters. Sparse pages (many true blanks) often have
        # lighter fills without being misaligned.
        elif lat_med < WEAK_FILL_MEDIAN_THRESHOLD and lat_cov >= 0.85:
            lattice_warning = lattice_warning or "weak_fill_scores"
        elif n_in >= 1 or weak_letter:
            lattice_warning = lattice_warning or "weak_fill_scores"
        elif len(amb_hard) >= 4:
            # Many thin letter ratios ⇒ likely misaligned; a single ambiguous
            # letter stays as per-question review after lattice apply.
            lattice_warning = lattice_warning or "weak_fill_scores"

        if lattice_warning is None:
            # Trusted lattice geometry — absolute coords, no further dx/dy.
            section_results = lattice_sections
            dx, dy = 0, 0
            lattice_used = True
            logger.info(
                "MCQ[%d/%s] lattice_applied cov=%.2f med_ink=%.0f",
                page_number, template.id, lat_cov, lat_med,
            )
        else:
            logger.info(
                "MCQ[%d/%s] lattice_rejected warn=%s — keeping translation hypothesis",
                page_number, template.id, lattice_warning,
            )

    # Anchor-quality warnings. Coverage alone is not enough — wrong grids can
    # "resolve" printed text. Escalate when the lock is conflicted.
    low_anchor_warning: Optional[str] = None
    if not lattice_used:
        raw_is_absurd = abs(raw_dx) > false_dx or abs(raw_dy) > false_dy
        raw_is_large = abs(raw_dx) > huge_dx or abs(raw_dy) > huge_dy
        if raw_is_absurd:
            if best_cov < scoring.min_page_coverage:
                low_anchor_warning = "huge_dy" if abs(raw_dy) > abs(raw_dx) else "huge_dx"
        elif (dx, dy) == (0, 0) and raw_is_large:
            low_anchor_warning = "huge_dy" if abs(raw_dy) > abs(raw_dx) else "huge_dx"
        elif (dx, dy) != (0, 0) and raw_is_large and best_med_ink < WEAK_FILL_MEDIAN_THRESHOLD:
            low_anchor_warning = "huge_dy" if abs(dy) > abs(dx) else "huge_dx"
        elif (dx, dy) == (0, 0) and anchor_score < template.anchor.min_match_score:
            if best_cov < scoring.min_page_coverage:
                low_anchor_warning = "anchor_match_low"
        if lattice_warning and not low_anchor_warning and best_cov < scoring.min_page_coverage:
            low_anchor_warning = lattice_warning

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
        overlay_mcq_sections=list(lattice_geom) if lattice_used else None,
    )

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
    elif not result.warning:
        letter_best = sorted(
            r.best_score for r in all_rows
            if r.answer not in ("BL", "IN", "")
        )
        if letter_best:
            med_ink = letter_best[len(letter_best) // 2]
            dense = result.coverage >= max(scoring.min_page_coverage, 0.85)
            # Blank-aware exception: sparse pages with strong letter ink under a
            # trusted lattice may keep per-question review only (e.g. many true
            # blanks). Weak ink on a translation lock still means whole-page review.
            lattice_ok = bool(result.overlay_mcq_sections)
            strong_sparse = (not dense) and lattice_ok and med_ink >= 300
            if (
                result.coverage >= scoring.min_page_coverage
                and med_ink < WEAK_FILL_MEDIAN_THRESHOLD
                and not strong_sparse
            ):
                result.warning = "weak_fill_scores"

    # Many hard-ambiguous rows (IN / thin letter) ⇒ grid likely misaligned.
    # Blank gray-zone flags stay per-question and do not dump the whole page.
    if not result.warning:
        amb_hard = hard_ambiguous_mcq_questions(
            result,
            min_ratio=float(scoring.min_ratio),
            min_ink_pixels=int(scoring.min_ink_pixels),
        )
        if len(amb_hard) >= 4:
            result.warning = "weak_fill_scores"
            logger.info(
                "MCQ[%d/%s] hard_ambiguous_rows=%d → weak_fill_scores",
                page_number, template.id, len(amb_hard),
            )

    logger.info(
        "MCQ[%d/%s] ACCEPT answers=%d warning=%s dx=%d dy=%d",
        page_number, template.id,
        sum(s.resolved_count for s in section_results),
        result.warning, dx, dy,
    )
    return result


def ambiguous_mcq_questions(
    page_result: "PageResult",
    *,
    min_ratio: float = 1.2,
    min_ink_pixels: int = 110,
) -> List[str]:
    """Question numbers that should not be auto-trusted.

    Flags:
      * ``IN`` (multi-mark)
      * letter answers with razor-thin winner ratio
      * ``BL`` with enough ink that a light/partial fill may have been missed
      * ``BL`` with near-zero ink on an otherwise filled page (ROI miss)
    """
    ratio_threshold = min_ratio + RATIO_TRUST_MARGIN
    # Ink above this but still classified BL → possible under-threshold fill.
    blank_gray_zone = max(40, int(min_ink_pixels * 0.55))
    page_has_fills = any(
        (row.answer or "").strip().upper() not in ("", "BL", "IN")
        for section in page_result.sections
        for row in section.rows
    )
    flagged: List[str] = []
    seen: set[str] = set()
    # A successful printed-label lattice fit proves every blank ROI is on the
    # intended row/column.  On that path, border/noise ink and true zero-ink
    # blanks are not geometry uncertainty.  Keep the conservative blank rules
    # for legacy anchor geometry, where an empty ROI can still mean a miss.
    legacy_geometry = not bool(page_result.overlay_mcq_sections)
    for section in page_result.sections:
        for row in section.rows:
            q = str(row.question)
            if q in seen:
                continue
            ans = (row.answer or "").strip().upper()
            suspicious = False
            if ans == "IN":
                suspicious = True
            elif ans not in ("", "BL") and row.ratio < ratio_threshold:
                suspicious = True
            elif legacy_geometry and ans == "BL" and row.best_score >= blank_gray_zone:
                suspicious = True
            elif legacy_geometry and ans == "BL" and page_has_fills and row.best_score < 5:
                # Completely empty ROI while other bubbles filled → geometry miss.
                suspicious = True
            if suspicious:
                seen.add(q)
                flagged.append(q)
    return flagged


def hard_ambiguous_mcq_questions(
    page_result: "PageResult",
    *,
    min_ratio: float = 1.2,
    min_ink_pixels: int = 110,
) -> List[str]:
    """Ambiguous questions that imply geometry / mark conflict (not blank gray-zone).

    Used for whole-page review escalation. Blank-only ambiguity stays per-question.
    """
    answers = {
        str(k): str(v or "").strip().upper()
        for k, v in (page_result.answers or {}).items()
    }
    return [
        q
        for q in ambiguous_mcq_questions(
            page_result, min_ratio=min_ratio, min_ink_pixels=min_ink_pixels,
        )
        if answers.get(q, "") not in ("", "BL")
    ]


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
