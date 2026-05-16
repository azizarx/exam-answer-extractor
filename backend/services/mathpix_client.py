"""
Mathpix OCR client.

Two surfaces:
- /v3/text: single-image OCR (legacy, used by section_extractor only — that
  module is slated for deletion).
- /v3/pdf: full-PDF async OCR. The new pipeline uses this to get figure CDN
  URLs out of exam answer sheets when the chosen template flags any question
  as a diagram. Workflow: submit_pdf -> poll_pdf -> fetch_mmd.
"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import requests

from backend.config import get_settings

logger = logging.getLogger(__name__)

MATHPIX_API_URL = "https://api.mathpix.com/v3/text"
MATHPIX_PDF_URL = "https://api.mathpix.com/v3/pdf"


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


# ---------------------------------------------------------------------------
# /v3/pdf — async PDF OCR with figure CDN URLs embedded in the markdown output
# ---------------------------------------------------------------------------

def _pdf_headers(app_id: Optional[str], app_key: Optional[str]) -> Dict[str, str]:
    settings = get_settings()
    app_id = app_id or settings.mathpix_app_id
    app_key = app_key or settings.mathpix_app_key
    if not app_id or not app_key:
        raise RuntimeError(
            "Mathpix credentials not configured. "
            "Set MATHPIX_APP_ID and MATHPIX_APP_KEY in .env"
        )
    return {"app_id": app_id, "app_key": app_key}


def submit_pdf(
    pdf_path: str,
    *,
    app_id: Optional[str] = None,
    app_key: Optional[str] = None,
    timeout: int = 120,
) -> str:
    """POST a PDF to /v3/pdf and return the pdf_id.

    Returns immediately — Mathpix processes the PDF asynchronously. Use
    poll_pdf() to wait for completion, then fetch_mmd() to get the result.
    """
    headers = _pdf_headers(app_id, app_key)
    # .mmd is always generated and fetchable at /v3/pdf/{id}.mmd; it is NOT a
    # valid conversion_formats key. Mathpix returns 4xx if we list it.
    options = {
        "conversion_formats": {"md": True},
        "math_inline_delimiters": ["$", "$"],
        "rm_spaces": True,
    }
    logger.info("MATHPIX submit pdf=%s options=%s", pdf_path, options)
    with open(pdf_path, "rb") as fh:
        resp = requests.post(
            MATHPIX_PDF_URL,
            headers=headers,
            data={"options_json": json.dumps(options)},
            files={"file": fh},
            timeout=timeout,
        )
    resp.raise_for_status()
    data = resp.json()
    pdf_id = data.get("pdf_id")
    if not pdf_id:
        raise RuntimeError(f"Mathpix submit returned no pdf_id: {data!r}")
    logger.info("MATHPIX submitted pdf_id=%s", pdf_id)
    return pdf_id


def poll_pdf(
    pdf_id: str,
    *,
    app_id: Optional[str] = None,
    app_key: Optional[str] = None,
    poll_interval: float = 3.0,
    max_wait: float = 600.0,
) -> Dict[str, Any]:
    """Poll GET /v3/pdf/{pdf_id} until status in {'completed','error'} or timeout.

    Returns the final status payload dict. Raises TimeoutError on max_wait.
    """
    headers = _pdf_headers(app_id, app_key)
    url = f"{MATHPIX_PDF_URL}/{pdf_id}"
    deadline = time.monotonic() + max_wait
    logger.info("MATHPIX poll start pdf_id=%s max_wait=%.0fs", pdf_id, max_wait)
    while True:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        percent = data.get("percent_done")
        logger.debug("MATHPIX poll pdf_id=%s status=%s percent=%s", pdf_id, status, percent)
        if status in ("completed", "error"):
            logger.info("MATHPIX poll done pdf_id=%s status=%s", pdf_id, status)
            return data
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Mathpix PDF processing did not complete within {max_wait}s "
                f"(pdf_id={pdf_id}, last_status={status})"
            )
        time.sleep(poll_interval)


def fetch_mmd(
    pdf_id: str,
    *,
    app_id: Optional[str] = None,
    app_key: Optional[str] = None,
    timeout: int = 60,
) -> str:
    """GET /v3/pdf/{pdf_id}.mmd. Return the markdown body as text.

    The MMD body contains inline image references like
    ![](https://cdn.mathpix.com/snip/images/<hash>.original.fullsize.png)
    for figures Mathpix detected in the PDF.
    """
    headers = _pdf_headers(app_id, app_key)
    url = f"{MATHPIX_PDF_URL}/{pdf_id}.mmd"
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    logger.info("MATHPIX fetched .mmd pdf_id=%s bytes=%d", pdf_id, len(text))
    return text
