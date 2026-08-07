"""Per-page anisotropic affine alignment of an MCQ grid to the bubble lattice.

Fits ``x' = sx * x + tx``, ``y' = sy * y + ty`` mapping template column /
fill-center coordinates onto the page. Columns and rows both come from
lattice detection (blob / guided / projection) + least-squares — not
ink-max search around a sketchy anchor ``dy`` (that locks onto Q-numbers).

Failed fits return ``ok=False`` so callers keep translation-only geometry and
send the page to review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from backend.services.template_service import AnswerSection, GridGeometry, Region, ScoringParams

logger = logging.getLogger(__name__)

SCALE_MIN = 0.75
SCALE_MAX = 1.25
MAX_COL_RMSE = 18.0
MAX_ROW_RMSE = 18.0
# Detection may retain noisier candidates for diagnostics, but a row lattice
# with this much unexplained vertical error is not safe to render/trust.
TRUST_MAX_ROW_RMSE = 10.0
# Soft anisotropy gate (hard reject above ANISO_HARD). Between soft and hard,
# accept only when column residual is tight and letter ink is strong.
ANISO_SOFT = 0.06
ANISO_HARD = 0.28
ANISO_MAX_COL_RMSE = 12.0
ANISO_MIN_MED_INK = 250.0
# |dy| beyond this is almost always a false lock for lattice seeding.
FALSE_LOCK_DY_FOR_LATTICE = 300.0
WEAK_ANCHOR_FOR_LATTICE = 0.80
LARGE_DY_WITH_WEAK_ANCHOR = 40.0
ROI_PAD_X = 80
ROI_PAD_Y = 100


@dataclass
class LatticeFit:
    ok: bool
    sx: float = 1.0
    sy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0
    col_rmse: float = 0.0
    row_rmse: float = 0.0
    n_col_peaks: int = 0
    n_row_peaks: int = 0
    warning: Optional[str] = None  # lattice_align_failed | lattice_residual_high
    det_cols: Optional[List[float]] = None
    det_rows: Optional[List[float]] = None


def sanitize_lattice_dy_prior(
    dy_prior: float,
    *,
    anchor_score: Optional[float] = None,
    false_lock: float = FALSE_LOCK_DY_FOR_LATTICE,
) -> float:
    """Zero out absurd / weak-anchor dy so row seeds are not poisoned."""
    if abs(dy_prior) > false_lock:
        return 0.0
    if (
        anchor_score is not None
        and anchor_score < WEAK_ANCHOR_FOR_LATTICE
        and abs(dy_prior) > LARGE_DY_WITH_WEAK_ANCHOR
    ):
        return 0.0
    return float(dy_prior)


def _clamp_roi(x: int, y: int, w: int, h: int, shape: Tuple[int, ...]) -> Tuple[int, int, int, int]:
    ih, iw = shape[:2]
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(iw, x + w)
    y1 = min(ih, y + h)
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


def _smooth_1d(arr: np.ndarray, k: int = 9) -> np.ndarray:
    k = max(3, k | 1)
    kernel = np.ones(k, dtype=np.float64) / k
    return np.convolve(arr.astype(np.float64), kernel, mode="same")


def _find_peaks(proj: np.ndarray, n: int, min_dist: int) -> List[int]:
    if proj.size == 0 or n <= 0:
        return []
    work = proj.copy()
    peaks: List[int] = []
    for _ in range(n):
        idx = int(np.argmax(work))
        if work[idx] <= 0:
            break
        peaks.append(idx)
        lo = max(0, idx - min_dist)
        hi = min(work.size, idx + min_dist + 1)
        work[lo:hi] = 0
    peaks.sort()
    return peaks


def _dominant_pitch(proj: np.ndarray, hint: float, lo_frac: float = 0.55, hi_frac: float = 1.45) -> float:
    if proj.size < 16 or hint <= 1:
        return hint
    x = proj.astype(np.float64)
    x = x - x.mean()
    corr = np.correlate(x, x, mode="full")
    corr = corr[corr.size // 2 :]
    lo = max(2, int(hint * lo_frac))
    hi = min(corr.size - 1, int(hint * hi_frac))
    if hi <= lo:
        return hint
    return float(lo + int(np.argmax(corr[lo : hi + 1])))


def _fit_1d(src: Sequence[float], dst: Sequence[float]) -> Tuple[float, float, float]:
    if len(src) < 2 or len(src) != len(dst):
        return 1.0, 0.0, 1e9
    xs = np.asarray(src, dtype=np.float64)
    ys = np.asarray(dst, dtype=np.float64)
    a = np.column_stack([xs, np.ones_like(xs)])
    sol, _, _, _ = np.linalg.lstsq(a, ys, rcond=None)
    s, t = float(sol[0]), float(sol[1])
    pred = s * xs + t
    rmse = float(np.sqrt(np.mean((pred - ys) ** 2)))
    return s, t, rmse


def _fit_ty_fixed_scale(
    src: Sequence[float], dst: Sequence[float], scale: float,
) -> Tuple[float, float]:
    """Least-squares ty with sy fixed; returns (ty, rmse)."""
    if len(src) < 1 or len(src) != len(dst):
        return 0.0, 1e9
    xs = np.asarray(src, dtype=np.float64)
    ys = np.asarray(dst, dtype=np.float64)
    ty = float(np.mean(ys - scale * xs))
    pred = scale * xs + ty
    rmse = float(np.sqrt(np.mean((pred - ys) ** 2)))
    return ty, rmse


def detect_lattice_axes(
    gray: np.ndarray,
    region: Region,
    *,
    n_cols: int = 5,
    n_rows: int = 20,
    binary_threshold: int = 180,
    expected_col_pitch: float = 110.0,
    expected_row_pitch: float = 80.0,
) -> Tuple[List[float], List[float]]:
    """Detect column X axes via greedy projection peaks (legacy / fallback)."""
    del n_rows  # rows come from template-guided fit, not greedy peaks
    x, y, w, h = _clamp_roi(
        region.x - ROI_PAD_X,
        region.y - ROI_PAD_Y,
        region.w + 2 * ROI_PAD_X,
        region.h + 2 * ROI_PAD_Y,
        gray.shape,
    )
    if w < 20 or h < 20:
        return [], []

    crop = gray[y : y + h, x : x + w]
    _, bin_inv = cv2.threshold(crop, binary_threshold, 255, cv2.THRESH_BINARY_INV)
    cw = max(3, int(round(expected_col_pitch * 0.12)))
    ch = max(3, int(round(expected_row_pitch * 0.12)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cw, ch))
    cleaned = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, kernel, iterations=1)

    vproj = _smooth_1d(cleaned.sum(axis=0), k=max(5, int(expected_col_pitch * 0.2) | 1))
    min_dx = max(8, int(expected_col_pitch * 0.45))
    col_idx = _find_peaks(vproj, n_cols, min_dx)
    cols = [float(x + i) for i in col_idx]
    return cols, []


def detect_blob_columns(
    gray: np.ndarray,
    region: Region,
    *,
    n_cols: int,
    expected_col_pitch: float,
    binary_threshold: int = 180,
    dx: float = 0.0,
    dy: float = 0.0,
) -> List[float]:
    """Column centers from bubble-sized connected components (ignores thin rules / Q-nums)."""
    x, y, w, h = _clamp_roi(
        int(region.x + dx) - ROI_PAD_X,
        int(region.y + dy) - ROI_PAD_Y,
        region.w + 2 * ROI_PAD_X,
        region.h + 2 * ROI_PAD_Y,
        gray.shape,
    )
    if w < 20 or h < 20 or n_cols < 2:
        return []
    crop = gray[y : y + h, x : x + w]
    _, bin_inv = cv2.threshold(crop, binary_threshold, 255, cv2.THRESH_BINARY_INV)
    pitch = max(8.0, float(expected_col_pitch))
    min_a = int((pitch * 0.15) ** 2)
    max_a = int((pitch * 0.95) ** 2)
    _n, _lab, stats, centroids = cv2.connectedComponentsWithStats(bin_inv, connectivity=8)
    xs: List[float] = []
    for i in range(1, _n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if not (min_a <= area <= max_a):
            continue
        if not (pitch * 0.12 <= bw <= pitch * 0.9):
            continue
        if not (pitch * 0.10 <= bh <= pitch * 1.3):
            continue
        if bh > 0 and not (0.35 <= (bw / bh) <= 3.5):
            continue
        xs.append(float(centroids[i, 0] + x))
    if len(xs) < n_cols * 2:
        return []
    arr = np.asarray(sorted(xs), dtype=np.float64)
    lo, hi = float(arr.min() - 5), float(arr.max() + 5)
    bins = np.zeros(int(hi - lo) + 1, dtype=np.float64)
    for v in arr:
        bins[int(v - lo)] += 1.0
    bins = _smooth_1d(bins, k=max(5, int(pitch * 0.15) | 1))
    peaks = _find_peaks(bins, n_cols, max(8, int(pitch * 0.45)))
    if len(peaks) < n_cols:
        return []
    return [float(lo + p) for p in peaks]


def detect_guided_columns(
    gray: np.ndarray,
    region: Region,
    tmpl_cols: Sequence[float],
    *,
    sx: float,
    tx: float,
    binary_threshold: int = 180,
) -> List[float]:
    """Local vertical-projection max near each expected template column."""
    if len(tmpl_cols) < 2:
        return []
    col_pitch = (tmpl_cols[-1] - tmpl_cols[0]) / max(1, len(tmpl_cols) - 1)
    x, y, w, h = _clamp_roi(
        region.x - ROI_PAD_X,
        region.y - ROI_PAD_Y,
        region.w + 2 * ROI_PAD_X,
        region.h + 2 * ROI_PAD_Y,
        gray.shape,
    )
    if w < 20 or h < 20:
        return []
    crop = gray[y : y + h, x : x + w]
    _, bin_inv = cv2.threshold(crop, binary_threshold, 255, cv2.THRESH_BINARY_INV)
    vproj = _smooth_1d(
        bin_inv.sum(axis=0), k=max(5, int(col_pitch * abs(sx) * 0.15) | 1),
    )
    rad = max(8, int(col_pitch * abs(sx) * 0.32))
    out: List[float] = []
    for c in tmpl_cols:
        ex = int(round(sx * float(c) + tx - x))
        lo = max(0, ex - rad)
        hi = min(vproj.size, ex + rad + 1)
        if hi <= lo:
            return []
        peak = lo + int(np.argmax(vproj[lo:hi]))
        out.append(float(x + peak))
    return out


def _pitch_cv(vals: Sequence[float]) -> float:
    if len(vals) < 2:
        return 1e9
    pitches = np.diff(np.asarray(vals, dtype=np.float64))
    mean = float(np.mean(pitches))
    if mean <= 1e-6:
        return 1e9
    return float(np.std(pitches) / mean)


def pick_column_lattice(
    gray: np.ndarray,
    region: Region,
    tmpl_cols: Sequence[float],
    *,
    binary_threshold: int = 180,
    expected_row_pitch: float = 80.0,
    dx_prior: float = 0.0,
    dy_prior: float = 0.0,
) -> Tuple[List[float], float, float, float, str]:
    """Choose best column set among blob / guided / greedy projection.

    Returns ``(det_cols, sx, tx, col_rmse, method)``. Empty det_cols on failure.
    """
    n = len(tmpl_cols)
    if n < 2:
        return [], 1.0, 0.0, 1e9, "none"
    col_pitch = (tmpl_cols[-1] - tmpl_cols[0]) / max(1, n - 1)
    cands: List[Tuple[str, List[float], float, float, float]] = []

    blob = detect_blob_columns(
        gray, region, n_cols=n, expected_col_pitch=float(col_pitch),
        binary_threshold=binary_threshold, dx=dx_prior, dy=dy_prior,
    )
    if len(blob) == n:
        sx, tx, rmse = _fit_1d(tmpl_cols, blob)
        cands.append(("blob", blob, sx, tx, rmse))

    guided = detect_guided_columns(
        gray, region, tmpl_cols, sx=1.0, tx=dx_prior,
        binary_threshold=binary_threshold,
    )
    if len(guided) == n:
        sx, tx, rmse = _fit_1d(tmpl_cols, guided)
        cands.append(("guided1", guided, sx, tx, rmse))
        if SCALE_MIN <= sx <= SCALE_MAX:
            guided2 = detect_guided_columns(
                gray, region, tmpl_cols, sx=sx, tx=tx,
                binary_threshold=binary_threshold,
            )
            if len(guided2) == n:
                sx2, tx2, rmse2 = _fit_1d(tmpl_cols, guided2)
                cands.append(("guided2", guided2, sx2, tx2, rmse2))

    shifted = Region(
        x=int(region.x + dx_prior),
        y=int(region.y + dy_prior),
        w=region.w,
        h=region.h,
    )
    for reg in (shifted, region):
        greedy, _ = detect_lattice_axes(
            gray, reg,
            n_cols=n, n_rows=20,
            binary_threshold=binary_threshold,
            expected_col_pitch=float(col_pitch),
            expected_row_pitch=float(expected_row_pitch),
        )
        if len(greedy) == n:
            sx, tx, rmse = _fit_1d(tmpl_cols, greedy)
            cands.append(("greedy", greedy, sx, tx, rmse))
            break
        if len(greedy) >= max(3, n - 1):
            det_c = [
                float(greedy[0] + (greedy[-1] - greedy[0]) * i / (n - 1))
                for i in range(n)
            ]
            sx, tx, rmse = _fit_1d(tmpl_cols, det_c)
            cands.append(("greedy_resample", det_c, sx, tx, rmse))
            break

    viable = [
        c for c in cands
        if SCALE_MIN <= c[2] <= SCALE_MAX and c[4] <= MAX_COL_RMSE
    ]
    if not viable:
        if cands:
            name, cols, sx, tx, rmse = min(cands, key=lambda c: c[4])
            return cols, sx, tx, rmse, name
        return [], 1.0, 0.0, 1e9, "none"

    def _score(c: Tuple[str, List[float], float, float, float]) -> Tuple[float, ...]:
        _name, cols, _sx, _tx, rmse = c
        return (rmse + 30.0 * _pitch_cv(cols),)

    name, cols, sx, tx, rmse = min(viable, key=_score)
    return cols, sx, tx, rmse, name


def _column_strip_roi(
    gray: np.ndarray,
    region: Region,
    mapped_cols: Sequence[float],
    *,
    col_pitch: float,
    sx: float,
    dy_prior: float,
    binary_threshold: int,
) -> Tuple[int, int, int, int, np.ndarray, np.ndarray]:
    """Return (x,y,w,h), ROI bin_inv crop, and 1D hproj of the column strip."""
    x, y, w, h = _clamp_roi(
        region.x - ROI_PAD_X,
        int(region.y + dy_prior) - ROI_PAD_Y,
        region.w + 2 * ROI_PAD_X,
        region.h + 2 * ROI_PAD_Y,
        gray.shape,
    )
    empty = np.zeros(0, dtype=np.float64)
    if w < 20 or h < 20 or not mapped_cols:
        return x, y, w, h, np.zeros((max(h, 0), max(w, 0)), dtype=np.uint8), empty
    crop = gray[y : y + h, x : x + w]
    _, bin_inv = cv2.threshold(crop, binary_threshold, 255, cv2.THRESH_BINARY_INV)
    pad = abs(col_pitch * 0.6 * sx)
    c0 = int(max(0, min(mapped_cols) - x - pad))
    c1 = int(min(w, max(mapped_cols) - x + pad))
    strip = bin_inv[:, c0:c1] if c1 > c0 else bin_inv
    row_pitch_hint = 80.0
    hproj = _smooth_1d(strip.sum(axis=1), k=max(5, int(row_pitch_hint * 0.2) | 1))
    return x, y, w, h, bin_inv, hproj


def detect_blob_rows(
    gray: np.ndarray,
    region: Region,
    *,
    n_rows: int,
    expected_row_pitch: float,
    mapped_cols: Sequence[float],
    col_pitch: float,
    sx: float,
    binary_threshold: int = 180,
    dy_prior: float = 0.0,
) -> List[float]:
    """Fill-center Ys from bubble-sized blobs inside the mapped column strip."""
    x, y, w, h, bin_inv, _hproj = _column_strip_roi(
        gray, region, mapped_cols,
        col_pitch=col_pitch, sx=sx, dy_prior=dy_prior,
        binary_threshold=binary_threshold,
    )
    if w < 20 or h < 20 or n_rows < 2 or bin_inv.size == 0:
        return []
    pad = abs(col_pitch * 0.6 * sx)
    c0 = int(max(0, min(mapped_cols) - x - pad))
    c1 = int(min(w, max(mapped_cols) - x + pad))
    strip = bin_inv[:, c0:c1] if c1 > c0 else bin_inv
    pitch = max(8.0, float(expected_row_pitch))
    min_a = int((pitch * 0.12) ** 2)
    max_a = int((pitch * 0.95) ** 2)
    _n, _lab, stats, centroids = cv2.connectedComponentsWithStats(strip, connectivity=8)
    ys: List[float] = []
    for i in range(1, _n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if not (min_a <= area <= max_a):
            continue
        if not (pitch * 0.08 <= bh <= pitch * 0.85):
            continue
        if not (pitch * 0.15 <= bw <= pitch * 2.5):
            continue
        ys.append(float(centroids[i, 1] + y))
    if len(ys) < max(n_rows, n_rows // 2 + 3):
        return []
    arr = np.asarray(sorted(ys), dtype=np.float64)
    lo, hi = float(arr.min() - 5), float(arr.max() + 5)
    bins = np.zeros(int(hi - lo) + 1, dtype=np.float64)
    for v in arr:
        bins[int(v - lo)] += 1.0
    bins = _smooth_1d(bins, k=max(5, int(pitch * 0.15) | 1))
    peaks = _find_peaks(bins, n_rows, max(6, int(pitch * 0.40)))
    if len(peaks) < n_rows:
        return []
    return [float(lo + p) for p in peaks]


def detect_guided_rows(
    gray: np.ndarray,
    region: Region,
    tmpl_fill_y: Sequence[float],
    *,
    mapped_cols: Sequence[float],
    col_pitch: float,
    sx: float,
    sy: float,
    ty: float,
    binary_threshold: int = 180,
    dy_prior: float = 0.0,
) -> List[float]:
    """Local horizontal-projection max near each expected fill-center Y."""
    if len(tmpl_fill_y) < 2:
        return []
    row_pitch = (tmpl_fill_y[-1] - tmpl_fill_y[0]) / max(1, len(tmpl_fill_y) - 1)
    _x, y, _w, _h, _bin_inv, hproj = _column_strip_roi(
        gray, region, mapped_cols,
        col_pitch=col_pitch, sx=sx, dy_prior=dy_prior,
        binary_threshold=binary_threshold,
    )
    if hproj.size < 8:
        return []
    hproj = _smooth_1d(hproj, k=max(5, int(row_pitch * abs(sy) * 0.15) | 1))
    rad = max(6, int(row_pitch * abs(sy) * 0.32))
    out: List[float] = []
    for fy in tmpl_fill_y:
        ey = int(round(sy * float(fy) + ty - y))
        lo = max(0, ey - rad)
        hi = min(hproj.size, ey + rad + 1)
        if hi <= lo:
            return []
        peak = lo + int(np.argmax(hproj[lo:hi]))
        out.append(float(y + peak))
    return out


def detect_strip_rows(
    gray: np.ndarray,
    region: Region,
    *,
    n_rows: int,
    expected_row_pitch: float,
    mapped_cols: Sequence[float],
    col_pitch: float,
    sx: float,
    binary_threshold: int = 180,
    dy_prior: float = 0.0,
) -> List[float]:
    """Greedy peaks on the column-strip horizontal projection."""
    _x, y, _w, _h, _bin_inv, hproj = _column_strip_roi(
        gray, region, mapped_cols,
        col_pitch=col_pitch, sx=sx, dy_prior=dy_prior,
        binary_threshold=binary_threshold,
    )
    if hproj.size < 8 or n_rows < 2:
        return []
    pitch = max(8.0, float(expected_row_pitch) * abs(sx))
    hproj = _smooth_1d(hproj, k=max(5, int(pitch * 0.15) | 1))
    peaks = _find_peaks(hproj, n_rows, max(6, int(pitch * 0.40)))
    if len(peaks) < n_rows:
        return []
    return [float(y + p) for p in peaks]


def pick_row_lattice(
    gray: np.ndarray,
    region: Region,
    tmpl_fill_y: Sequence[float],
    *,
    mapped_cols: Sequence[float],
    col_pitch: float,
    sx: float,
    binary_threshold: int = 180,
    dy_prior: float = 0.0,
    cell_height: float = 36.0,
    bubble_height: float = 20.0,
) -> Tuple[List[float], float, float, float, str]:
    """Choose best fill-center Y set among blob / guided / strip peaks.

    Blob/strip peaks often lock onto the printed option-label band above the
    fill. Candidates are therefore also evaluated after shifting peaks down by
    common label→fill offsets before LS to ``tmpl_fill_y``.

    Returns ``(det_rows, sy, ty, row_rmse, method)``.
    """
    n = len(tmpl_fill_y)
    if n < 2 or len(mapped_cols) < 2:
        return [], 1.0, 0.0, 1e9, "none"
    row_pitch = (tmpl_fill_y[-1] - tmpl_fill_y[0]) / max(1, n - 1)
    ch = float(cell_height) * abs(sx)
    bh = float(bubble_height) * abs(sx)
    # label center → fill center ≈ ch/2 + bh/2; also try fill-top and raw.
    offsets = {
        "raw": 0.0,
        "half_ch": ch * 0.5,
        "label_to_fill": ch * 0.5 + bh * 0.5,
        "cell": ch,
    }

    raw_sets: List[Tuple[str, List[float]]] = []

    blob = detect_blob_rows(
        gray, region, n_rows=n, expected_row_pitch=float(row_pitch),
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(blob) == n:
        raw_sets.append(("blob", blob))

    guided = detect_guided_rows(
        gray, region, tmpl_fill_y,
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        sy=1.0, ty=dy_prior, binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(guided) == n:
        raw_sets.append(("guided1", guided))

    guided_iso = detect_guided_rows(
        gray, region, tmpl_fill_y,
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        sy=sx, ty=dy_prior, binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(guided_iso) == n:
        raw_sets.append(("guided_iso", guided_iso))

    strip = detect_strip_rows(
        gray, region, n_rows=n, expected_row_pitch=float(row_pitch),
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(strip) == n:
        raw_sets.append(("strip", strip))

    cands: List[Tuple[str, List[float], float, float, float]] = []
    for set_name, peaks in raw_sets:
        for off_name, off in offsets.items():
            shifted = [float(p + off) for p in peaks]
            sy, ty, rmse = _fit_1d(tmpl_fill_y, shifted)
            cands.append((f"{set_name}+{off_name}", shifted, sy, ty, rmse))
            ty_i, rmse_i = _fit_ty_fixed_scale(tmpl_fill_y, shifted, sx)
            cands.append((f"{set_name}+{off_name}_iso", shifted, sx, ty_i, rmse_i))

        # One guided refine from best free LS of this set (raw offset only).
        if set_name.startswith("guided"):
            continue
        sy0, ty0, _ = _fit_1d(tmpl_fill_y, peaks)
        if SCALE_MIN <= sy0 <= SCALE_MAX:
            refined = detect_guided_rows(
                gray, region, tmpl_fill_y,
                mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
                sy=sy0, ty=ty0, binary_threshold=binary_threshold, dy_prior=dy_prior,
            )
            if len(refined) == n:
                for off_name, off in offsets.items():
                    shifted = [float(p + off) for p in refined]
                    sy, ty, rmse = _fit_1d(tmpl_fill_y, shifted)
                    cands.append((f"refine_{set_name}+{off_name}", shifted, sy, ty, rmse))

    # Guided refine with label_to_fill seed (common case).
    seed_ty = dy_prior + offsets["label_to_fill"]
    guided2 = detect_guided_rows(
        gray, region, tmpl_fill_y,
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        sy=sx, ty=seed_ty, binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(guided2) == n:
        sy, ty, rmse = _fit_1d(tmpl_fill_y, guided2)
        cands.append(("guided2_fillseed", guided2, sy, ty, rmse))
        ty_i, rmse_i = _fit_ty_fixed_scale(tmpl_fill_y, guided2, sx)
        cands.append(("guided2_fillseed_iso", guided2, sx, ty_i, rmse_i))
        if SCALE_MIN <= sy <= SCALE_MAX:
            guided3 = detect_guided_rows(
                gray, region, tmpl_fill_y,
                mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
                sy=sy, ty=ty, binary_threshold=binary_threshold, dy_prior=dy_prior,
            )
            if len(guided3) == n:
                sy3, ty3, rmse3 = _fit_1d(tmpl_fill_y, guided3)
                cands.append(("guided3", guided3, sy3, ty3, rmse3))

    viable = [
        c for c in cands
        if SCALE_MIN <= c[2] <= SCALE_MAX and c[4] <= MAX_ROW_RMSE
    ]
    if not viable:
        if cands:
            name, rows, sy, ty, rmse = min(cands, key=lambda c: c[4])
            return rows, sy, ty, rmse, name
        return [], 1.0, 0.0, 1e9, "none"

    def _score(c: Tuple[str, List[float], float, float, float]) -> Tuple[float, ...]:
        _name, rows, sy, _ty, rmse = c
        aniso_pen = 8.0 if abs(sy - sx) / max(abs(sx), 1e-6) > ANISO_SOFT else 0.0
        return (rmse + 30.0 * _pitch_cv(rows) + aniso_pen,)

    name, rows, sy, ty, rmse = min(viable, key=_score)
    return rows, sy, ty, rmse, name


def rank_row_lattice_candidates(
    gray: np.ndarray,
    region: Region,
    tmpl_fill_y: Sequence[float],
    *,
    mapped_cols: Sequence[float],
    col_pitch: float,
    sx: float,
    binary_threshold: int = 180,
    dy_prior: float = 0.0,
    cell_height: float = 36.0,
    bubble_height: float = 20.0,
    top_k: int = 8,
) -> List[Tuple[str, List[float], float, float, float]]:
    """Return up to ``top_k`` viable (method, rows, sy, ty, rmse) sorted by geometry score."""
    # Reuse pick's candidate generation by calling internals via a shared builder.
    n = len(tmpl_fill_y)
    if n < 2 or len(mapped_cols) < 2:
        return []
    row_pitch = (tmpl_fill_y[-1] - tmpl_fill_y[0]) / max(1, n - 1)
    ch = float(cell_height) * abs(sx)
    bh = float(bubble_height) * abs(sx)
    offsets = {
        "raw": 0.0,
        "half_ch": ch * 0.5,
        "label_to_fill": ch * 0.5 + bh * 0.5,
        "cell": ch,
    }
    raw_sets: List[Tuple[str, List[float]]] = []
    blob = detect_blob_rows(
        gray, region, n_rows=n, expected_row_pitch=float(row_pitch),
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(blob) == n:
        raw_sets.append(("blob", blob))
    strip = detect_strip_rows(
        gray, region, n_rows=n, expected_row_pitch=float(row_pitch),
        mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
        binary_threshold=binary_threshold, dy_prior=dy_prior,
    )
    if len(strip) == n:
        raw_sets.append(("strip", strip))
    for seed_name, sy0, ty0 in (
        ("g1", 1.0, dy_prior),
        ("g_iso", sx, dy_prior),
        ("g_fill", sx, dy_prior + offsets["label_to_fill"]),
    ):
        g = detect_guided_rows(
            gray, region, tmpl_fill_y,
            mapped_cols=mapped_cols, col_pitch=col_pitch, sx=sx,
            sy=sy0, ty=ty0, binary_threshold=binary_threshold, dy_prior=dy_prior,
        )
        if len(g) == n:
            raw_sets.append((seed_name, g))

    cands: List[Tuple[str, List[float], float, float, float]] = []
    for set_name, peaks in raw_sets:
        for off_name, off in offsets.items():
            shifted = [float(p + off) for p in peaks]
            sy, ty, rmse = _fit_1d(tmpl_fill_y, shifted)
            cands.append((f"{set_name}+{off_name}", shifted, sy, ty, rmse))
            ty_i, rmse_i = _fit_ty_fixed_scale(tmpl_fill_y, shifted, sx)
            cands.append((f"{set_name}+{off_name}_iso", shifted, sx, ty_i, rmse_i))

    viable = [
        c for c in cands
        if SCALE_MIN <= c[2] <= SCALE_MAX and c[4] <= MAX_ROW_RMSE
    ]
    if not viable:
        return []

    def _score(c: Tuple[str, List[float], float, float, float]) -> Tuple[float, ...]:
        _name, rows, sy, _ty, rmse = c
        aniso_pen = 8.0 if abs(sy - sx) / max(abs(sx), 1e-6) > ANISO_SOFT else 0.0
        # Mild preference for label→fill style offsets (blob peaks sit on labels).
        off_bonus = -1.5 if "label_to_fill" in _name else 0.0
        return (rmse + 30.0 * _pitch_cv(rows) + aniso_pen + off_bonus,)

    viable.sort(key=_score)
    # Dedupe near-identical (sy,ty)
    out: List[Tuple[str, List[float], float, float, float]] = []
    for c in viable:
        if any(abs(c[2] - o[2]) < 0.01 and abs(c[3] - o[3]) < 2.0 for o in out):
            continue
        out.append(c)
        if len(out) >= top_k:
            break
    return out


def aniso_acceptable(sx: float, sy: float, col_rmse: float, med_ink: float) -> bool:
    """Whether independent X/Y scale is safe to trust."""
    aniso = abs(sy - sx) / max(abs(sx), 1e-6)
    if aniso <= ANISO_SOFT:
        return True
    if aniso > ANISO_HARD:
        return False
    return col_rmse <= ANISO_MAX_COL_RMSE and med_ink >= ANISO_MIN_MED_INK


def fit_lattice_to_template(
    gray: np.ndarray,
    region: Region,
    tmpl_cols: Sequence[float],
    tmpl_fill_y: Sequence[float],
    *,
    binary_threshold: int = 180,
    cell_width: int = 45,
    cell_height: int = 36,
    bubble_height: int = 20,
    labels: Optional[Sequence[str]] = None,
    scoring: Optional[ScoringParams] = None,
    dy_prior: float = 0.0,
    dx_prior: float = 0.0,
    anchor_score: Optional[float] = None,
) -> LatticeFit:
    """Fit sx,tx from cols and sy,ty from row lattice (LS fill centers)."""
    from backend.services.mcq_extractor import _classify_row, _score_bubbles

    if len(tmpl_cols) < 2 or len(tmpl_fill_y) < 2:
        return LatticeFit(ok=False, warning="lattice_align_failed")

    dy_prior = sanitize_lattice_dy_prior(dy_prior, anchor_score=anchor_score)

    labels_l = list(labels or ["A", "B", "C", "D", "E"])
    scoring_params = scoring or ScoringParams(
        min_ink_pixels=80, min_ratio=1.15, binary_threshold=binary_threshold,
    )

    col_pitch = (tmpl_cols[-1] - tmpl_cols[0]) / max(1, len(tmpl_cols) - 1)
    row_pitch = (tmpl_fill_y[-1] - tmpl_fill_y[0]) / max(1, len(tmpl_fill_y) - 1)

    det_cols, sx, tx, col_rmse, col_method = pick_column_lattice(
        gray, region, tmpl_cols,
        binary_threshold=binary_threshold,
        expected_row_pitch=float(row_pitch),
        dx_prior=dx_prior,
        dy_prior=dy_prior,
    )
    if len(det_cols) < len(tmpl_cols):
        return LatticeFit(
            ok=False, n_col_peaks=len(det_cols),
            warning="lattice_align_failed", det_cols=list(det_cols),
        )
    if not (SCALE_MIN <= sx <= SCALE_MAX) or col_rmse > MAX_COL_RMSE:
        return LatticeFit(
            ok=False, sx=sx, tx=tx, col_rmse=col_rmse,
            n_col_peaks=len(det_cols),
            warning="lattice_align_failed" if not (SCALE_MIN <= sx <= SCALE_MAX) else "lattice_residual_high",
            det_cols=list(det_cols),
        )
    logger.debug(
        "lattice cols method=%s sx=%.3f tx=%.1f crmse=%.1f",
        col_method, sx, tx, col_rmse,
    )

    mapped_cols = [sx * c + tx for c in tmpl_cols]
    col_xs = [int(round(v)) for v in mapped_cols]
    cw = max(8, int(round(cell_width * sx)))

    row_cands = rank_row_lattice_candidates(
        gray, region, tmpl_fill_y,
        mapped_cols=mapped_cols,
        col_pitch=float(col_pitch),
        sx=sx,
        binary_threshold=binary_threshold,
        dy_prior=dy_prior,
        cell_height=float(cell_height),
        bubble_height=float(bubble_height),
        top_k=8,
    )
    if not row_cands:
        # Fall back to single pick for diagnostics.
        det_rows, sy, ty, row_rmse, row_method = pick_row_lattice(
            gray, region, tmpl_fill_y,
            mapped_cols=mapped_cols,
            col_pitch=float(col_pitch),
            sx=sx,
            binary_threshold=binary_threshold,
            dy_prior=dy_prior,
            cell_height=float(cell_height),
            bubble_height=float(bubble_height),
        )
        return LatticeFit(
            ok=False, sx=sx, tx=tx, sy=sy, ty=ty,
            col_rmse=col_rmse, row_rmse=row_rmse,
            n_col_peaks=len(det_cols), n_row_peaks=len(det_rows),
            warning="lattice_align_failed",
            det_cols=list(det_cols), det_rows=list(det_rows) if det_rows else None,
        )

    # Extra isotropic ink-seeded candidate. Use a clamped dy so large priors
    # (D005-style) cannot steer the window onto the wrong band.
    seed_dy = 0.0 if abs(dy_prior) > LARGE_DY_WITH_WEAK_ANCHOR else float(dy_prior)
    _, bin_inv_seed = cv2.threshold(gray, binary_threshold, 255, cv2.THRESH_BINARY_INV)

    def _ink_seed(sy: float, ty: float) -> Tuple[float, float]:
        bh = max(8, int(round(bubble_height * sy)))
        ch_use = max(8, int(round(cell_height * sy)))
        letter_ink: List[float] = []
        resolved = 0
        for fy in tmpl_fill_y:
            fill_y = sy * fy + ty
            row_y = int(round(fill_y - ch_use - bh / 2.0))
            scores = _score_bubbles(bin_inv_seed, row_y, col_xs, cw, ch_use, bh, labels_l)
            ans, best, _second, _ratio = _classify_row(scores, scoring_params)
            if ans not in ("BL", "IN", ""):
                resolved += 1
                letter_ink.append(float(best))
        cov = resolved / max(1, len(tmpl_fill_y))
        med = float(np.median(letter_ink)) if letter_ink else 0.0
        return cov, med

    ty_seed = float(tmpl_fill_y[0]) * (1.0 - sx) + seed_dy
    search = max(row_pitch * sx * 0.55, 25.0)
    step = max(1.0, row_pitch * sx / 25.0)
    best_seed = None
    ty_scan = ty_seed - search
    while ty_scan <= ty_seed + search:
        cov_s, med_s = _ink_seed(sx, ty_scan)
        key_s = (med_s, cov_s)
        if best_seed is None or key_s > best_seed[0]:
            best_seed = (key_s, ty_scan)
        ty_scan += step
    if best_seed is not None:
        _ks, ty_s = best_seed
        seed_rows = [sx * fy + ty_s for fy in tmpl_fill_y]
        row_cands = list(row_cands) + [("ink_iso_seed", seed_rows, sx, ty_s, 0.0)]

    _, bin_inv = cv2.threshold(gray, binary_threshold, 255, cv2.THRESH_BINARY_INV)
    _x, y0, _w, _h, _b, hproj = _column_strip_roi(
        gray, region, mapped_cols,
        col_pitch=float(col_pitch), sx=sx, dy_prior=dy_prior,
        binary_threshold=binary_threshold,
    )
    if hproj.size > 8:
        hproj = _smooth_1d(hproj, k=max(5, int(row_pitch * 0.15) | 1))

    def _strip_residual(sy: float, ty: float) -> float:
        if hproj.size < 8:
            return 0.0
        rad = max(2, int(row_pitch * abs(sy) / 3))
        errs = []
        for fy in tmpl_fill_y:
            yy = sy * fy + ty - y0
            yi = int(round(yy))
            lo = max(0, yi - rad)
            hi = min(hproj.size, yi + rad + 1)
            if hi <= lo:
                errs.append(float(rad))
                continue
            peak = lo + int(np.argmax(hproj[lo:hi]))
            errs.append(float(abs(peak - yi)))
        return float(np.mean(errs)) if errs else 0.0

    def _ink_for(sy: float, ty: float) -> Tuple[float, float, float]:
        bh = max(8, int(round(bubble_height * sy)))
        ch_use = max(8, int(round(cell_height * sy)))
        letter_ink: List[float] = []
        ratios: List[float] = []
        resolved = 0
        n_in = 0
        for fy in tmpl_fill_y:
            fill_y = sy * fy + ty
            row_y = int(round(fill_y - ch_use - bh / 2.0))
            scores = _score_bubbles(bin_inv, row_y, col_xs, cw, ch_use, bh, labels_l)
            ans, best, _second, ratio = _classify_row(scores, scoring_params)
            if ans == "IN":
                n_in += 1
            elif ans not in ("BL", ""):
                resolved += 1
                letter_ink.append(float(best))
                ratios.append(float(ratio))
        cov = resolved / max(1, len(tmpl_fill_y))
        med = float(np.median(letter_ink)) if letter_ink else 0.0
        avg_r = float(np.mean(ratios)) if ratios else 0.0
        # Penalize IN-heavy locks (wrong band often multi-hits printed glyphs).
        return cov - 0.15 * n_in, med, avg_r

    best_row = None  # (ink_key, method, rows, sy, ty, rmse, cov, med)
    for method, rows, sy, ty, rmse in row_cands:
        mapped_r = [sy * r + ty for r in tmpl_fill_y]
        if mapped_r != sorted(mapped_r):
            continue
        cov, med, avg_r = _ink_for(sy, ty)
        strip_res = _strip_residual(sy, ty)
        aniso_pen = 40.0 if abs(sy - sx) / max(abs(sx), 1e-6) > ANISO_SOFT else 0.0
        # Strong fills first; then low strip residual (on-lattice); then rmse.
        ink_key = (med - aniso_pen, cov, -strip_res, avg_r, -rmse)
        if best_row is None or ink_key > best_row[0]:
            best_row = (ink_key, method, rows, sy, ty, rmse, cov, med)

    if best_row is None:
        return LatticeFit(
            ok=False, sx=sx, tx=tx, col_rmse=col_rmse,
            n_col_peaks=len(det_cols), warning="lattice_align_failed",
            det_cols=list(det_cols),
        )

    _ik, row_method, det_rows, sy, ty, row_rmse, cov, med = best_row
    logger.debug(
        "lattice rows method=%s sy=%.3f ty=%.1f rrmse=%.1f med=%.0f cov=%.2f",
        row_method, sy, ty, row_rmse, med, cov,
    )

    if not (SCALE_MIN <= sy <= SCALE_MAX) or row_rmse > TRUST_MAX_ROW_RMSE:
        return LatticeFit(
            ok=False, sx=sx, sy=sy, tx=tx, ty=ty,
            col_rmse=col_rmse, row_rmse=row_rmse,
            n_col_peaks=len(det_cols), n_row_peaks=len(det_rows),
            warning="lattice_align_failed" if not (SCALE_MIN <= sy <= SCALE_MAX) else "lattice_residual_high",
            det_cols=list(det_cols), det_rows=list(det_rows),
        )

    mapped_c = [sx * c + tx for c in tmpl_cols]
    mapped_r = [sy * r + ty for r in tmpl_fill_y]
    if mapped_c != sorted(mapped_c) or mapped_r != sorted(mapped_r):
        return LatticeFit(
            ok=False, sx=sx, sy=sy, tx=tx, ty=ty,
            col_rmse=col_rmse, row_rmse=row_rmse,
            warning="lattice_align_failed",
            det_cols=list(det_cols), det_rows=list(det_rows),
        )

    if med < 200 or cov < 0.35:
        return LatticeFit(
            ok=False, sx=sx, sy=sy, tx=tx, ty=ty,
            col_rmse=col_rmse, row_rmse=row_rmse,
            n_col_peaks=len(det_cols), n_row_peaks=len(det_rows),
            warning="lattice_align_failed",
            det_cols=list(det_cols), det_rows=list(det_rows),
        )

    if not aniso_acceptable(sx, sy, col_rmse, med):
        return LatticeFit(
            ok=False, sx=sx, sy=sy, tx=tx, ty=ty,
            col_rmse=col_rmse, row_rmse=row_rmse,
            n_col_peaks=len(det_cols), n_row_peaks=len(tmpl_fill_y),
            warning="lattice_residual_high",
            det_cols=list(det_cols), det_rows=list(det_rows),
        )

    return LatticeFit(
        ok=True, sx=sx, sy=sy, tx=tx, ty=ty,
        col_rmse=col_rmse, row_rmse=row_rmse,
        n_col_peaks=len(det_cols), n_row_peaks=len(tmpl_fill_y),
        det_cols=list(det_cols), det_rows=mapped_r,
    )


def apply_affine_to_grid(
    grid: GridGeometry,
    fit: LatticeFit,
    *,
    use_fill_centers: bool = True,
) -> GridGeometry:
    if not fit.ok:
        return grid
    cols_in = list(grid.col_positions[0]) if grid.col_positions else []
    rows_in = list(grid.row_positions)
    ch = grid.cell_height or 0
    bh = grid.bubble_height or 0
    new_cols = [int(round(fit.sx * x + fit.tx)) for x in cols_in]
    new_cw = max(8, int(round((grid.cell_width or 36) * fit.sx)))
    new_bh = max(8, int(round((grid.bubble_height or 32) * fit.sy)))
    new_ch = max(8, int(round((grid.cell_height or 36) * fit.sy)))
    if use_fill_centers:
        fill_c = [y + ch + bh / 2.0 for y in rows_in]
        new_fill = [fit.sy * y + fit.ty for y in fill_c]
        # row_positions are consumed with the transformed cell/bubble sizes.
        # Convert the mapped fill centres back using those same sizes, or the
        # rendered/scored band drifts by (sy - 1) * (ch + bh/2).
        new_rows = [int(round(y - new_ch - new_bh / 2.0)) for y in new_fill]
    else:
        new_rows = [int(round(fit.sy * y + fit.ty)) for y in rows_in]
    pitch = (
        (new_rows[-1] - new_rows[0]) / max(1, len(new_rows) - 1)
        if len(new_rows) >= 2 else grid.row_pitch
    )
    return GridGeometry(
        rows=grid.rows,
        cols=grid.cols,
        questions_per_col=list(grid.questions_per_col),
        options=list(grid.options),
        cells_per_question=grid.cells_per_question,
        row_positions=new_rows,
        col_positions=[new_cols] if new_cols else [],
        row_pitch=round(pitch, 1),
        col_pitch=grid.col_pitch * fit.sx if grid.col_pitch else 0.0,
        cell_width=new_cw,
        cell_height=new_ch,
        bubble_height=new_bh,
        first_row_offset=grid.first_row_offset,
    )


def apply_affine_to_region(region: Region, fit: LatticeFit) -> Region:
    if not fit.ok:
        return region
    x0 = int(round(fit.sx * region.x + fit.tx))
    y0 = int(round(fit.sy * region.y + fit.ty))
    x1 = int(round(fit.sx * (region.x + region.w) + fit.tx))
    y1 = int(round(fit.sy * (region.y + region.h) + fit.ty))
    return Region(x=x0, y=y0, w=max(1, x1 - x0), h=max(1, y1 - y0))


def align_mcq_section(
    gray: np.ndarray,
    section: AnswerSection,
    *,
    binary_threshold: Optional[int] = None,
    dx_prior: float = 0.0,
    dy_prior: float = 0.0,
    anchor_score: Optional[float] = None,
) -> Tuple[Optional[AnswerSection], LatticeFit]:
    """Detect lattice, fit affine, return a rewritten MCQ section (or None)."""
    grid = section.grid
    if grid is None or not grid.col_positions or not grid.row_positions:
        return None, LatticeFit(ok=False, warning="lattice_align_failed")

    thr = binary_threshold
    if thr is None:
        thr = (section.scoring.binary_threshold if section.scoring else 180)

    cols = grid.col_positions[0]
    ch = grid.cell_height or 36
    bh = grid.bubble_height or 32
    tmpl_fill_y = [float(y + ch + bh / 2.0) for y in grid.row_positions]

    fit = fit_lattice_to_template(
        gray,
        section.region,
        cols,
        tmpl_fill_y,
        binary_threshold=int(thr),
        cell_width=int(grid.cell_width or 45),
        cell_height=int(ch),
        bubble_height=int(bh),
        labels=list(grid.options or ["A", "B", "C", "D", "E"]),
        scoring=section.scoring,
        dx_prior=dx_prior,
        dy_prior=dy_prior,
        anchor_score=anchor_score,
    )
    if not fit.ok:
        logger.info(
            "lattice_align FAIL warn=%s cols=%d rows=%d sx=%.3f sy=%.3f crmse=%.1f rrmse=%.1f",
            fit.warning, fit.n_col_peaks, fit.n_row_peaks,
            fit.sx, fit.sy, fit.col_rmse, fit.row_rmse,
        )
        return None, fit

    new_grid = apply_affine_to_grid(grid, fit, use_fill_centers=True)
    new_region = apply_affine_to_region(section.region, fit)
    new_section = AnswerSection(
        type=section.type,
        question_start=section.question_start,
        question_end=section.question_end,
        region=new_region,
        grid=new_grid,
        extraction_strategy=section.extraction_strategy,
        scoring=section.scoring,
        question_overrides=section.question_overrides,
    )
    logger.info(
        "lattice_align OK sx=%.3f sy=%.3f tx=%.1f ty=%.1f crmse=%.1f rrmse=%.1f",
        fit.sx, fit.sy, fit.tx, fit.ty, fit.col_rmse, fit.row_rmse,
    )
    return new_section, fit
