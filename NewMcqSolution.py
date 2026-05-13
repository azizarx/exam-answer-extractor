import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


# UZ page-1 calibrated anchor at 300 DPI.
TOP_X, TOP_Y, TOP_W, TOP_H = 134, 989, 695, 74
Y_ADJUST = 18

# Row pitch from UZ page calibration.
ROW_PITCH = (2777 - 1170) / 19


def _load_reference_grid(grid_path: str) -> dict:
    with open(grid_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _score_row_options(
    bin_inv: np.ndarray,
    row_y: int,
    shifted_cols: List[int],
    median_w: int,
    median_h: int,
    bubble_h: int,
    labels: List[str],
) -> Dict[str, int]:
    page_h, page_w = bin_inv.shape[:2]
    scores: Dict[str, int] = {}

    for col_idx, x in enumerate(shifted_cols):
        x1 = int(x - median_w / 2)
        y1 = row_y + median_h
        x2 = int(x + median_w / 2)
        y2 = y1 + bubble_h

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


def extract_uz_mcq_from_images(
    image_paths: List[str],
    grid_json_path: str = "page1_grid.json",
) -> Tuple[Dict[int, Dict[str, str]], Dict[int, dict]]:
    """
    Extract UZ MCQ answers from page image paths.

    Returns:
    - answers_by_page: {page_number: {"1": "A", ..., "20": "E"}}
    - diagnostics_by_page: quality metrics per page
    """
    if not image_paths:
        return {}, {}

    ref = _load_reference_grid(grid_json_path)
    ref_cols = [int(v) for v in ref["columns"]]
    ref_rows = [int(v) for v in ref["rows"]]
    med_w = int(ref["median_w"])
    med_h = int(ref["median_h"])
    bubble_h = int(ref["bubble_h"])

    offset_first_row = ref_rows[0] - TOP_Y
    labels = ["A", "B", "C", "D", "E"]

    page1 = cv2.imread(str(image_paths[0]))
    if page1 is None:
        return {}, {}

    if TOP_Y + TOP_H > page1.shape[0] or TOP_X + TOP_W > page1.shape[1]:
        return {}, {}

    top_tpl = page1[TOP_Y:TOP_Y + TOP_H, TOP_X:TOP_X + TOP_W].copy()
    answers_by_page: Dict[int, Dict[str, str]] = {}
    diagnostics_by_page: Dict[int, dict] = {}

    for page_idx, image_path in enumerate(image_paths, start=1):
        img = cv2.imread(str(image_path))
        if img is None:
            diagnostics_by_page[page_idx] = {"status": "error", "reason": "image_not_readable"}
            continue

        res = cv2.matchTemplate(img, top_tpl, cv2.TM_CCOEFF_NORMED)
        _, top_val, _, top_loc = cv2.minMaxLoc(res)
        if top_val < 0.45:
            diagnostics_by_page[page_idx] = {
                "status": "rejected",
                "reason": "anchor_match_low",
                "anchor_score": round(float(top_val), 4),
            }
            continue

        dx = int(top_loc[0] - TOP_X)
        top_y = int(top_loc[1])
        first_row_y = int(top_y + offset_first_row + Y_ADJUST)
        rows = [int(first_row_y + i * ROW_PITCH) for i in range(20)]
        shifted_cols = [int(c + dx) for c in ref_cols]

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, bin_inv = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

        page_answers: Dict[str, str] = {}
        ratio_values: List[float] = []
        unresolved_count = 0
        total_best = 0

        for row_idx, y in enumerate(rows):
            scores = _score_row_options(
                bin_inv=bin_inv,
                row_y=y,
                shifted_cols=shifted_cols,
                median_w=med_w,
                median_h=med_h,
                bubble_h=bubble_h,
                labels=labels,
            )

            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            best_label, best_score = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0
            ratio = float(best_score) / float(max(1, second_score))
            ratio_values.append(ratio)
            total_best += int(best_score)

            # Low-ink or weak-separation rows are uncertain.
            if best_score < 35 or ratio < 1.12:
                unresolved_count += 1
                page_answers[str(row_idx + 1)] = "BL"
            else:
                page_answers[str(row_idx + 1)] = best_label

        coverage = (20 - unresolved_count) / 20.0
        avg_ratio = float(sum(ratio_values) / len(ratio_values)) if ratio_values else 0.0
        avg_best = float(total_best / 20.0)

        diagnostics_by_page[page_idx] = {
            "status": "ok",
            "anchor_score": round(float(top_val), 4),
            "coverage": round(coverage, 4),
            "avg_ratio": round(avg_ratio, 4),
            "avg_best_ink": round(avg_best, 2),
            "unresolved_rows": unresolved_count,
        }

        # Reject low-confidence non-UZ-like detections.
        if coverage < 0.60 or avg_ratio < 1.10:
            diagnostics_by_page[page_idx]["status"] = "rejected"
            diagnostics_by_page[page_idx]["reason"] = "low_mcq_confidence"
            continue

        answers_by_page[page_idx] = page_answers

    return answers_by_page, diagnostics_by_page


def looks_like_uz_job(image_paths: List[str]) -> bool:
    if not image_paths:
        return False
    tokens = [Path(path).name.upper() for path in image_paths]
    return any("UZ" in token for token in tokens)
