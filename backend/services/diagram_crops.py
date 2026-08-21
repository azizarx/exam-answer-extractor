"""Per-question diagram crops: locate the printed scaffold, save the drawing.

A diagram question's answer lives inside a preprinted scaffold (a clock face, a
2x2 grid, a sector circle, a node network).  Scans carry a page offset beyond
pure scale, so a fixed box drifts off the drawing.  The template instead gives
``question_overrides[q].diagram_search_region``: a generous band to search, in
which the largest scaffold-shaped contour is fitted.  That absorbs the offset
and frames the crop the same way on every page.

Crops are written under ``storage/diagrams/sub<id>/`` and referenced from the
candidate's ``extra_fields.diagram_crops`` by bare filename, so nothing that
reaches the database can escape that directory when it is served back.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# A scaffold outline is a closed shape of roughly page-column size.  Anything
# smaller is a glyph or a stray mark; anything far from square is a table rule.
_MIN_SIDE_PX = 120
_MIN_ASPECT = 0.5
_MAX_ASPECT = 2.3
_INK_THRESHOLD = 200
_MARGIN_FRACTION = 0.10

CROP_NAME_RE = re.compile(r"^p\d+_q\d+\.png$")


def crop_filename(page_number: int, question: int) -> str:
    return f"p{int(page_number)}_q{int(question)}.png"


def _fit_scaffold(bgr: np.ndarray, band: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Return the crop box for the scaffold inside ``band``.

    Falls back to the band itself when no scaffold-shaped contour is found, so
    a layout this heuristic does not recognize still produces a usable crop.
    """
    height, width = bgr.shape[:2]
    x0, y0, x1, y1 = band
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 - x0 < _MIN_SIDE_PX or y1 - y0 < _MIN_SIDE_PX:
        return x0, y0, x1, y1

    window = bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(window, cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, _INK_THRESHOLD, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best: Optional[Tuple[float, int, int, int, int]] = None
    for contour in contours:
        bx, by, bw, bh = cv2.boundingRect(contour)
        if bw < _MIN_SIDE_PX or bh < _MIN_SIDE_PX:
            continue
        if not _MIN_ASPECT < bw / max(1, bh) < _MAX_ASPECT:
            continue
        area = cv2.contourArea(contour)
        if best is None or area > best[0]:
            best = (area, bx, by, bw, bh)

    if best is None:
        return x0, y0, x1, y1

    _, bx, by, bw, bh = best
    margin = int(_MARGIN_FRACTION * max(bw, bh))
    return (
        max(0, x0 + bx - margin),
        max(0, y0 + by - margin),
        min(width, x0 + bx + bw + margin),
        min(height, y0 + by + bh + margin),
    )


def extract_diagram_crops(
    bgr: np.ndarray,
    page_template: Any,
    page_num: int,
    crop_dir: Optional[Path],
) -> Dict[str, Dict[str, str]]:
    """Save a crop per diagram question on this page.

    ``page_template`` must already be adapted to the page image (its regions in
    image pixels).  Returns ``{question: {"file": name, "source": ...}}``;
    empty when there is nowhere to write or the template has no diagrams.
    """
    if crop_dir is None:
        return {}

    saved: Dict[str, Dict[str, str]] = {}
    for section in page_template.sections:
        for question, override in section.question_overrides.items():
            if override.type != "diagram":
                continue
            region = override.diagram_search_region
            if region is None or region.w <= 0 or region.h <= 0:
                continue
            band = (
                int(region.x),
                int(region.y),
                int(region.x + region.w),
                int(region.y + region.h),
            )
            x0, y0, x1, y1 = _fit_scaffold(bgr, band)
            crop = bgr[y0:y1, x0:x1]
            if crop.size == 0:
                logger.warning(
                    "DIAGRAM[%d/q%s] empty crop for band %s", page_num, question, band,
                )
                continue
            name = crop_filename(page_num, int(question))
            try:
                crop_dir.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(crop_dir / name), crop):
                    raise OSError("cv2.imwrite returned False")
            except Exception as exc:
                logger.error(
                    "DIAGRAM[%d/q%s] crop save failed: %s: %s",
                    page_num, question, type(exc).__name__, exc,
                )
                continue
            saved[str(question)] = {"file": name, "source": "template_region"}
            logger.info(
                "DIAGRAM[%d/q%s] crop %dx%d -> %s",
                page_num, question, x1 - x0, y1 - y0, name,
            )
    return saved
