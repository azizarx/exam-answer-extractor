"""Deskew / mild page alignment before CV anchor matching."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def estimate_skew_degrees(gray: np.ndarray) -> float:
    """Estimate page skew in degrees using Hough lines on edges.

    Positive angle means the page content is rotated counter-clockwise
    relative to the image axes (OpenCV rotate convention below).
    """
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    # Focus on central band — headers/footers can bias the estimate.
    y0, y1 = int(h * 0.15), int(h * 0.85)
    roi = gray[y0:y1, :]
    edges = cv2.Canny(roi, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=80, minLineLength=w // 6, maxLineGap=20,
    )
    if lines is None or len(lines) == 0:
        return 0.0

    angles = []
    for line in lines[:, 0]:
        x1, y1_, x2, y2_ = map(int, line)
        if x2 == x1:
            continue
        ang = np.degrees(np.arctan2(y2_ - y1_, x2 - x1))
        # Near-horizontal lines only.
        if abs(ang) <= 15:
            angles.append(ang)
        elif abs(abs(ang) - 90) <= 15:
            # Near-vertical → convert to horizontal equivalent.
            angles.append(ang - 90.0 if ang > 0 else ang + 90.0)

    if not angles:
        return 0.0
    return float(np.median(angles))


def deskew_bgr(
    image: np.ndarray,
    *,
    max_degrees: float = 8.0,
    min_degrees: float = 0.35,
) -> Tuple[np.ndarray, float]:
    """Rotate image to cancel estimated skew. Returns (image, applied_degrees)."""
    if image is None or image.size == 0:
        return image, 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    angle = estimate_skew_degrees(gray)
    if abs(angle) < min_degrees or abs(angle) > max_degrees:
        return image, 0.0

    h, w = image.shape[:2]
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    logger.info("DESKEW applied=%.2fdeg (estimated)", angle)
    return rotated, angle


def deskew_if_enabled(
    image: np.ndarray,
    enabled: bool = True,
) -> Tuple[np.ndarray, float]:
    if not enabled or image is None:
        return image, 0.0
    try:
        return deskew_bgr(image)
    except Exception as exc:
        logger.warning("DESKEW failed: %s", exc)
        return image, 0.0
