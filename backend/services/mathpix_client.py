"""
Mathpix OCR client for numeric grid reading and diagram detection.

Uses the v3/text endpoint with line_data to get per-region OCR results
with bounding polygon coordinates.
"""

import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import requests

from backend.config import get_settings

logger = logging.getLogger(__name__)

MATHPIX_API_URL = "https://api.mathpix.com/v3/text"


def _image_to_base64(image: np.ndarray) -> str:
    """Encode a BGR numpy image as base64 JPEG data URI."""
    _, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    b64 = base64.b64encode(buf.tobytes()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def ocr_image(
    image: np.ndarray,
    app_id: Optional[str] = None,
    app_key: Optional[str] = None,
    include_line_data: bool = True,
) -> Dict[str, Any]:
    """
    Send an image to Mathpix v3/text and return the parsed response.

    Args:
        image: BGR numpy array.
        app_id: Mathpix app ID (falls back to settings).
        app_key: Mathpix app key (falls back to settings).
        include_line_data: Request per-line bounding regions.

    Returns:
        Mathpix API response dict with keys: text, confidence, line_data, etc.
    """
    settings = get_settings()
    app_id = app_id or settings.mathpix_app_id
    app_key = app_key or settings.mathpix_app_key

    if not app_id or not app_key:
        raise RuntimeError(
            "Mathpix credentials not configured. "
            "Set MATHPIX_APP_ID and MATHPIX_APP_KEY in .env"
        )

    headers = {
        "app_id": app_id,
        "app_key": app_key,
        "Content-Type": "application/json",
    }

    body: Dict[str, Any] = {
        "src": _image_to_base64(image),
        "formats": ["text", "data"],
        "include_line_data": include_line_data,
        "data_options": {
            "include_latex": True,
        },
    }

    resp = requests.post(MATHPIX_API_URL, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ocr_region(
    image: np.ndarray,
    x: int, y: int, w: int, h: int,
    app_id: Optional[str] = None,
    app_key: Optional[str] = None,
) -> str:
    """
    Crop a region from the image and OCR it. Returns the text result.
    """
    crop = image[y : y + h, x : x + w]
    result = ocr_image(crop, app_id=app_id, app_key=app_key, include_line_data=False)
    return result.get("text", "").strip()


def ocr_cells(
    image: np.ndarray,
    cell_regions: List[Dict[str, int]],
    app_id: Optional[str] = None,
    app_key: Optional[str] = None,
) -> List[str]:
    """
    OCR a list of individual cell regions (for numeric grids).
    Each cell is a dict with x, y, w, h.

    Concatenates all cells into a single strip image and OCRs once
    for efficiency, then splits the result by position.
    """
    if not cell_regions:
        return []

    # Build a horizontal strip of all cells
    crops = []
    for cell in cell_regions:
        crop = image[cell["y"] : cell["y"] + cell["h"], cell["x"] : cell["x"] + cell["w"]]
        crops.append(crop)

    # Resize all to same height for concatenation
    target_h = max(c.shape[0] for c in crops)
    resized = []
    for crop in crops:
        if crop.shape[0] != target_h:
            scale = target_h / crop.shape[0]
            crop = cv2.resize(crop, (int(crop.shape[1] * scale), target_h))
        resized.append(crop)

    # Add separator lines between cells
    sep = np.full((target_h, 3, 3), 200, dtype=np.uint8)
    parts = []
    for i, crop in enumerate(resized):
        if i > 0:
            parts.append(sep)
        parts.append(crop)

    strip = np.hstack(parts)
    result = ocr_image(strip, app_id=app_id, app_key=app_key, include_line_data=False)
    text = result.get("text", "").strip()

    # The strip should produce characters separated by spaces or just concatenated
    # Return the raw text — the caller parses per their needs
    return [text]
