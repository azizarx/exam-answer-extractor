"""Deterministic CV for structured diagram answers.

The SEAMO X Paper A Q9 response is a fixed 12-sector circle.  Broad labels
such as "lower-right" are not precise enough because two adjacent sectors
share that description.  This module locates the printed circle and reports
the filled sector(s) using clock-face intervals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class SectorDiagramResult:
    status: str
    answer: Optional[str]
    sectors: tuple[int, ...]
    peak_score: float
    reason: Optional[str] = None


def _clock_interval(sector: int) -> str:
    start = (3 + sector - 1) % 12 + 1
    end = start % 12 + 1
    return f"{start}-{end}"


def _format_sector_answer(sectors: tuple[int, ...]) -> str:
    intervals = [_clock_interval(sector) for sector in sectors]
    if len(intervals) == 1:
        return f"Pie chart: shaded sector {intervals[0]} o'clock"
    return f"Pie chart: shaded sectors {' and '.join(intervals)} o'clock"


def extract_seamo_x_a_q9(image_bgr: np.ndarray) -> SectorDiagramResult:
    """Extract Paper A Q9's filled sector(s) from a full answer-sheet image.

    Sector 0 spans 3-4 o'clock and numbering proceeds clockwise.  The search
    window and radius are expressed as page fractions so the detector works
    at both the reference 300 DPI and lower render resolutions.
    """
    if image_bgr is None or not isinstance(image_bgr, np.ndarray) or image_bgr.size == 0:
        return SectorDiagramResult("needs_review", None, (), 0.0, "empty image")
    if image_bgr.ndim == 3:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    elif image_bgr.ndim == 2:
        gray = image_bgr
    else:
        return SectorDiagramResult("needs_review", None, (), 0.0, "invalid image")

    height, width = gray.shape[:2]
    x1, x2 = int(0.30 * width), int(0.56 * width)
    y1, y2 = int(0.45 * height), int(0.65 * height)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return SectorDiagramResult("needs_review", None, (), 0.0, "empty search region")

    circles = cv2.HoughCircles(
        cv2.medianBlur(roi, 5),
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(40, int(0.05 * width)),
        param1=100,
        param2=35,
        minRadius=max(20, int(0.045 * width)),
        maxRadius=max(30, int(0.085 * width)),
    )
    if circles is None or not len(circles[0]):
        return SectorDiagramResult("needs_review", None, (), 0.0, "circle not found")

    target = np.array([0.43 * width - x1, 0.55 * height - y1])
    circle = min(
        circles[0],
        key=lambda item: float(np.linalg.norm(item[:2] - target)),
    )
    center_x = float(circle[0] + x1)
    center_y = float(circle[1] + y1)
    radius = float(circle[2])

    yy, xx = np.indices(gray.shape)
    dx = xx - center_x
    dy = yy - center_y
    distance = np.hypot(dx, dy)
    angle = (np.degrees(np.arctan2(dy, dx)) + 360.0) % 360.0
    darkness = np.maximum(0, 210 - gray.astype(np.int16))
    radial = (distance > 0.22 * radius) & (distance < 0.82 * radius)

    scores: list[float] = []
    for start in range(0, 360, 30):
        relative = (angle - start + 360.0) % 360.0
        # Ignore four degrees beside each printed spoke.  This makes the score
        # measure filled interior rather than the fixed diagram scaffold.
        interior = radial & (relative >= 4.0) & (relative < 26.0)
        scores.append(float(darkness[interior].mean()) if interior.any() else 0.0)

    peak = max(scores)
    if peak < 3.0:
        return SectorDiagramResult("blank", "BL", (), peak)
    if peak < 8.0:
        return SectorDiagramResult(
            "needs_review", None, (), peak, "shading is below the trusted CV threshold"
        )

    cutoff = max(4.0, peak * 0.20)
    sectors = tuple(index for index, score in enumerate(scores) if score >= cutoff)
    if not sectors:
        return SectorDiagramResult("needs_review", None, (), peak, "no stable sector")
    return SectorDiagramResult(
        "ok",
        _format_sector_answer(sectors),
        sectors,
        peak,
    )
