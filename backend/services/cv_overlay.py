"""Burn CV MCQ reads onto page images for visual geometry QA."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from backend.services.mcq_extractor import (
    PageResult,
    _compute_row_positions,
    extract_page,
)
from backend.services.page_deskew import deskew_if_enabled
from backend.services.template_service import ExamTemplate, TemplateRegistry

logger = logging.getLogger(__name__)


def _draw_label(img: np.ndarray, text: str, org: Tuple[int, int], color) -> None:
    cv2.putText(
        img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA,
    )
    cv2.putText(
        img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA,
    )


def render_mcq_overlay(
    image_bgr: np.ndarray,
    template: ExamTemplate,
    result: PageResult,
    *,
    registry: Optional[TemplateRegistry] = None,
) -> np.ndarray:
    """Return a copy of the page with bubbles, answers, and anchor diagnostics."""
    out = image_bgr.copy()
    dx, dy = result.dx, result.dy

    # Anchor rectangle (template coords shifted)
    ar = template.anchor.region
    cv2.rectangle(
        out,
        (ar.x + dx, ar.y + dy),
        (ar.x + dx + ar.w, ar.y + dy + ar.h),
        (255, 128, 0),
        2,
    )
    _draw_label(
        out,
        f"anchor={result.anchor_score:.3f} dx={dx} dy={dy} warn={result.warning}",
        (20, 40),
        (255, 128, 0),
    )

    for section in template.sections:
        if section.type != "mcq_grid" or section.grid is None:
            continue
        grid = section.grid
        scoring = section.scoring or ScoringParams()
        n_visual = len(grid.col_positions) if grid.col_positions else 1
        row_by_q = {r.question: r for s in result.sections for r in s.rows}

        for vcol in range(n_visual):
            row_ys = _compute_row_positions(grid, section.region, dy, vcol)
            if grid.col_positions and vcol < len(grid.col_positions):
                cols = [c + dx for c in grid.col_positions[vcol]]
            else:
                continue
            labels = list(grid.options or [])
            # questions in this visual column
            q_start = section.question_start
            # Walk rows in order for this column
            qps = grid.questions_per_col or [section.question_end - section.question_start + 1]
            q_offset = sum(qps[:vcol]) if vcol < len(qps) else 0
            for row_i, row_y in enumerate(row_ys):
                qnum = q_start + q_offset + row_i
                row = row_by_q.get(qnum)
                ans = row.answer if row else "?"
                color = (0, 180, 0) if ans not in ("BL", "IN", "?", "") else (0, 0, 220)
                if ans == "IN":
                    color = (0, 165, 255)
                for li, x_center in enumerate(cols):
                    if li >= len(labels):
                        break
                    x1 = int(x_center - grid.cell_width / 2)
                    y1 = int(row_y + grid.cell_height)
                    x2 = int(x_center + grid.cell_width / 2)
                    y2 = int(y1 + (grid.bubble_height or grid.cell_height))
                    cv2.rectangle(out, (x1, y1), (x2, y2), color, 1)
                    if row and labels[li] == ans:
                        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
                _draw_label(out, f"Q{qnum}:{ans}", (cols[0] - 80, row_y + 20), color)

    return out


def overlay_page_file(
    image_path: str | Path,
    template: ExamTemplate,
    out_path: str | Path,
    *,
    registry: Optional[TemplateRegistry] = None,
    deskew: bool = True,
) -> PageResult:
    """Run CV MCQ on an image, write overlay PNG, return PageResult."""
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(image_path)
    image, _ = deskew_if_enabled(image, enabled=deskew)
    result = extract_page(image, template, page_number=1, registry=registry)
    overlay = render_mcq_overlay(image, template, result, registry=registry)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), overlay)
    logger.info(
        "OVERLAY wrote %s coverage=%.2f warning=%s",
        out_path, result.coverage, result.warning,
    )
    return result
