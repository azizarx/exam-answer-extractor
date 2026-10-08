"""Align a single-column MCQ grid from its repeated printed option labels.

Format-B answer sheets print the option glyphs (A-E) in a very clean 5 x 20
lattice immediately above the fill boxes.  Detecting that repeated structure is
more reliable than ranking unconstrained projection peaks: question numbers can
look like a sixth column, while label and fill bands can look like two possible
row phases.

This module uses geometry only.  It does not inspect answer labels, answer keys,
annotations, OCR text, or an anchor/reference image.  The selected template is
used for the approximate search region, expected pitches, and ROI dimensions.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from backend.services.template_service import (
    AnswerSection,
    GridGeometry,
    Region,
    ScoringParams,
)

logger = logging.getLogger(__name__)


# Search / component geometry, expressed relative to the template lattice.
ROI_PAD_COL_PITCHES = 2.0
ROI_PAD_ROW_PITCHES = 3.1
GLYPH_WIDTH_MIN_FRAC = 0.13
GLYPH_WIDTH_MAX_FRAC = 0.38
GLYPH_HEIGHT_MIN_FRAC = 0.22
GLYPH_HEIGHT_MAX_FRAC = 0.50
GLYPH_AREA_MIN_FRAC = 0.009
X_CLUSTER_TOL_FRAC = 0.065
Y_CLUSTER_TOL_FRAC = 0.065
MIN_X_GROUP_SUPPORT = 8

# Candidate and trust gates.  The 25-page calibration corpus observed maxima of
# 2.3 px for the column fit and three inferred rows; these leave useful margin
# without accepting a question-number column or an arbitrary text lattice.
SCALE_MIN = 0.75
SCALE_MAX = 1.25
CANDIDATE_MAX_COL_RMSE = 12.0
TRUST_MAX_COL_RMSE = 5.0
TRUST_MAX_ROW_RMSE = 5.0
TRUST_MAX_PITCH_CV = 0.08
MIN_OBSERVED_ROW_FRACTION = 0.75
MIN_STRONG_ROW_FRACTION = 0.60
MAX_INFERRED_ROW_FRACTION = 0.25

# Median printed-glyph bottom -> top edge of its fill rectangle.  Scaling by
# the detected row pitch keeps this stable when image DPI changes.
LABEL_BOTTOM_TO_FILL_TOP = 4.0

# Multi-column sheets (Paper K: Q1-10 left, Q11-15 right).  The column with
# the most rows is fitted by the single-column path above; the others are
# then searched only near where that fit predicts their labels, within this
# fraction of a column/row pitch.
SECONDARY_SEARCH_TOL_FRAC = 0.25
# Paper K prints the same ~34 px option letters as format B but on a much
# taller row (about 170 px against ~100), so glyph height as a fraction of the
# row pitch drops to 0.22-0.24, below the single-column floor.  Measured on the
# Paper K gold set (tests/fixtures/gold/seamo_2025_paper_k).
MULTI_COL_GLYPH_WIDTH_MIN_FRAC = 0.09
MULTI_COL_GLYPH_HEIGHT_MIN_FRAC = 0.15
MULTI_COL_GLYPH_HEIGHT_MAX_FRAC = 0.35
MULTI_COL_GLYPH_AREA_MIN_FRAC = 0.006
# Label bottom -> outer top edge of the printed box, which is what a Paper K
# row position marks.  The scored band then starts the template's own
# cell_height below it, so a template can score only the box interior.
MULTI_COL_LABEL_BOTTOM_TO_ROW_TOP = 19.0
# A Paper K row has only three labels, and a child's fill often runs up over
# one of them.  Count a row seen when two of its three labels are; the strong-
# row and regular-pitch gates of the single-column fit still apply.
MULTI_COL_MIN_ROW_SUPPORT = 2


@dataclass
class LabelLatticeFit:
    """Diagnostics for :func:`align_mcq_section_from_labels`."""

    ok: bool
    sx: float = 1.0
    sy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0
    col_rmse: float = 0.0
    row_rmse: float = 0.0
    row_pitch_cv: float = 0.0
    n_components: int = 0
    n_x_groups: int = 0
    n_observed_rows: int = 0
    n_strong_rows: int = 0
    n_inferred_rows: int = 0
    min_column_support: int = 0
    synchronized_support: int = 0
    col_centers: Optional[List[float]] = None
    row_centers: Optional[List[float]] = None
    fill_tops: Optional[List[float]] = None
    warning: Optional[str] = None


# (center_x, center_y, width, height, area, top_x, top_y)
_Component = Tuple[float, float, int, int, int, int, int]


def _fit_1d(src: Sequence[float], dst: Sequence[float]) -> Tuple[float, float, float]:
    if len(src) < 2 or len(src) != len(dst):
        return 1.0, 0.0, 1e9
    xs = np.asarray(src, dtype=np.float64)
    ys = np.asarray(dst, dtype=np.float64)
    design = np.column_stack([xs, np.ones_like(xs)])
    solution, _, _, _ = np.linalg.lstsq(design, ys, rcond=None)
    scale, offset = float(solution[0]), float(solution[1])
    residual = scale * xs + offset - ys
    rmse = float(np.sqrt(np.mean(residual * residual)))
    return scale, offset, rmse


def _cluster_components(
    components: Sequence[_Component],
    *,
    axis: int,
    tolerance: float,
) -> List[List[_Component]]:
    """Greedily cluster sorted component centres along one axis."""
    groups: List[List[_Component]] = []
    for component in sorted(components, key=lambda item: item[axis]):
        value = float(component[axis])
        if groups and value - float(groups[-1][-1][axis]) <= tolerance:
            groups[-1].append(component)
        else:
            groups.append([component])
    return groups


def _row_groups_for_columns(
    columns: Sequence[Sequence[_Component]],
    *,
    tolerance: float,
) -> List[List[_Component]]:
    """Group vertically coincident glyphs, keeping one per option column."""
    tagged: List[Tuple[float, int, _Component]] = []
    for column_index, column in enumerate(columns):
        for component in column:
            tagged.append((float(component[1]), column_index, component))
    tagged.sort(key=lambda item: item[0])

    raw_groups: List[List[Tuple[float, int, _Component]]] = []
    for item in tagged:
        if raw_groups and item[0] - raw_groups[-1][-1][0] <= tolerance:
            raw_groups[-1].append(item)
        else:
            raw_groups.append([item])

    groups: List[List[_Component]] = []
    for raw_group in raw_groups:
        by_column: dict[int, _Component] = {}
        for _center_y, column_index, component in raw_group:
            previous = by_column.get(column_index)
            # Touching ink can create a second nearby component.  Retain the
            # larger glyph-like component for this option column.
            if previous is None or component[4] > previous[4]:
                by_column[column_index] = component
        groups.append(list(by_column.values()))
    return groups


def _extract_glyph_components(
    gray: np.ndarray,
    section: AnswerSection,
    *,
    col_pitch: float,
    row_pitch: float,
    binary_threshold: int,
    width_min_frac: float = GLYPH_WIDTH_MIN_FRAC,
    height_min_frac: float = GLYPH_HEIGHT_MIN_FRAC,
    height_max_frac: float = GLYPH_HEIGHT_MAX_FRAC,
    area_min_frac: float = GLYPH_AREA_MIN_FRAC,
) -> List[_Component]:
    region = section.region
    pad_x = int(round(ROI_PAD_COL_PITCHES * col_pitch))
    pad_y = int(round(ROI_PAD_ROW_PITCHES * row_pitch))
    image_h, image_w = gray.shape[:2]
    x0 = max(0, int(region.x) - pad_x)
    y0 = max(0, int(region.y) - pad_y)
    x1 = min(image_w, int(region.x + region.w) + pad_x)
    y1 = min(image_h, int(region.y + region.h) + pad_y)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return []

    crop = gray[y0:y1, x0:x1]
    _, binary = cv2.threshold(
        crop, int(binary_threshold), 255, cv2.THRESH_BINARY_INV,
    )
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8,
    )

    min_area = max(20, int(round(area_min_frac * col_pitch * row_pitch)))
    components: List[_Component] = []
    for component_index in range(1, count):
        x, y, width, height, area = map(int, stats[component_index])
        if not (
            width_min_frac * col_pitch
            <= width
            <= GLYPH_WIDTH_MAX_FRAC * col_pitch
        ):
            continue
        if not (
            height_min_frac * row_pitch
            <= height
            <= height_max_frac * row_pitch
        ):
            continue
        if area < min_area:
            continue
        components.append(
            (
                float(centroids[component_index, 0] + x0),
                float(centroids[component_index, 1] + y0),
                width,
                height,
                area,
                x + x0,
                y + y0,
            )
        )
    return components


def _reconstruct_rows(
    observed_groups: Sequence[Sequence[_Component]],
    *,
    n_rows: int,
    expected_pitch: float,
) -> Tuple[Optional[List[dict]], int]:
    """Return all row records, inferring a few label rows lost to fill ink."""
    observed: List[dict] = []
    for group in sorted(
        observed_groups,
        key=lambda items: float(np.median([item[1] for item in items])),
    ):
        observed.append(
            {
                "center": float(np.median([item[1] for item in group])),
                "bottom": float(np.median([item[6] + item[3] for item in group])),
                "height": float(np.median([item[3] for item in group])),
                "support": len(group),
            }
        )

    if len(observed) == n_rows:
        return observed, 0
    if len(observed) < 2 or len(observed) > n_rows:
        return None, 0

    centers = np.asarray([row["center"] for row in observed], dtype=np.float64)
    gaps = np.diff(centers)
    multiples = np.maximum(1, np.rint(gaps / expected_pitch).astype(int))
    pitch = float(np.median(gaps / multiples))
    if pitch <= 1.0:
        return None, 0

    # Calibration pages that need reconstruction retain their first printed
    # row; inferred gaps occur in the middle or at the bottom.  Reject a span
    # that cannot fit within the declared row count instead of changing phase.
    indices = np.rint((centers - centers[0]) / pitch).astype(int)
    if (
        indices[0] != 0
        or indices[-1] >= n_rows
        or len(set(map(int, indices))) != len(indices)
    ):
        return None, 0

    start = float(np.mean(centers - indices * pitch))
    median_height = float(np.median([row["height"] for row in observed]))
    by_index = {int(index): row for index, row in zip(indices, observed)}
    rows: List[dict] = []
    for index in range(n_rows):
        if index in by_index:
            rows.append(by_index[index])
            continue
        center = start + index * pitch
        rows.append(
            {
                "center": center,
                "bottom": center + median_height / 2.0,
                "height": median_height,
                "support": 0,
            }
        )
    return rows, n_rows - len(observed)


def _select_regular_row_groups(
    groups: Sequence[Sequence[_Component]],
    *,
    n_rows: int,
    expected_pitch: float,
) -> List[List[_Component]]:
    """Discard extra synchronized glyph rows outside the answer lattice.

    Some forms repeat A-E in the instructions below the answer table.  Those
    five glyphs can have stronger component support than a genuine row touched
    by fill ink, so truncating by support alone drops a real answer row.  When
    extras are present, choose the ``n_rows`` subset with the tightest regular
    pitch instead.
    """
    ordered = sorted(
        (list(group) for group in groups),
        key=lambda items: float(np.median([item[1] for item in items])),
    )
    if len(ordered) <= n_rows:
        return ordered

    # In normal forms there is at most one instruction-example row.  Bound the
    # combinatorics defensively; a noisy page with many candidates is handled
    # by contiguous windows and will still face the residual trust gates.
    if len(ordered) - n_rows <= 4:
        candidates = itertools.combinations(ordered, n_rows)
    else:
        candidates = (
            ordered[start : start + n_rows]
            for start in range(len(ordered) - n_rows + 1)
        )

    best: Optional[Tuple[Tuple[float, float], List[List[_Component]]]] = None
    template_indices = np.arange(n_rows, dtype=np.float64)
    for candidate_tuple in candidates:
        candidate = list(candidate_tuple)
        centers = np.asarray(
            [np.median([item[1] for item in group]) for group in candidate],
            dtype=np.float64,
        )
        pitch, _start, rmse = _fit_1d(template_indices, centers)
        if not SCALE_MIN * expected_pitch <= pitch <= SCALE_MAX * expected_pitch:
            continue
        support = float(sum(len(group) for group in candidate))
        key = (rmse, -support)
        if best is None or key < best[0]:
            best = (key, candidate)
    return best[1] if best is not None else ordered[:n_rows]


def _failed(
    warning: str,
    *,
    n_components: int = 0,
    n_x_groups: int = 0,
) -> Tuple[None, LabelLatticeFit]:
    return None, LabelLatticeFit(
        ok=False,
        n_components=n_components,
        n_x_groups=n_x_groups,
        warning=warning,
    )


def align_mcq_section_from_labels(
    gray: np.ndarray,
    section: AnswerSection,
    *,
    binary_threshold: Optional[int] = None,
) -> Tuple[Optional[AnswerSection], LabelLatticeFit]:
    """Build absolute MCQ geometry from the repeated printed option glyphs.

    The supported geometry is one visual grid with explicit option columns and
    row positions.  On any weak or ambiguous lattice this function fails
    closed and returns ``(None, fit)``.
    """
    if gray is None or gray.size == 0:
        return _failed("label_lattice_image_empty")
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)

    grid = section.grid
    if grid is not None and len(grid.col_positions) > 1:
        return _align_multi_column(
            gray, section, binary_threshold=binary_threshold,
        )
    return _align_single_column(gray, section, binary_threshold=binary_threshold)


def _align_single_column(
    gray: np.ndarray,
    section: AnswerSection,
    *,
    binary_threshold: Optional[int],
    glyph_width_min_frac: float = GLYPH_WIDTH_MIN_FRAC,
    glyph_height_frac: Tuple[float, float] = (
        GLYPH_HEIGHT_MIN_FRAC, GLYPH_HEIGHT_MAX_FRAC,
    ),
    glyph_area_min_frac: float = GLYPH_AREA_MIN_FRAC,
    label_bottom_to_fill_top: float = LABEL_BOTTOM_TO_FILL_TOP,
    min_row_support: Optional[int] = None,
) -> Tuple[Optional[AnswerSection], LabelLatticeFit]:
    grid = section.grid
    if (
        grid is None
        or len(grid.col_positions) != 1
        or len(grid.col_positions[0]) < 3
    ):
        return _failed("label_lattice_unsupported_geometry")

    # Classic boxed-grid templates predate explicit row coordinates and store
    # the same geometry as first-row offset + pitch.  Materialize that lattice
    # locally so these scans get the same self-registering CV path as newer
    # format-B templates.
    template_rows = list(grid.row_positions)
    if len(template_rows) < 2:
        if grid.rows < 2 or grid.row_pitch <= 1.0:
            return _failed("label_lattice_unsupported_geometry")
        first_row = float(section.region.y + grid.first_row_offset)
        template_rows = [
            first_row + row_index * float(grid.row_pitch)
            for row_index in range(grid.rows)
        ]

    template_cols = np.asarray(grid.col_positions[0], dtype=np.float64)
    n_options = len(template_cols)
    n_rows = len(template_rows)
    col_pitch = float(np.median(np.diff(template_cols)))
    row_pitch = float(np.median(np.diff(np.asarray(template_rows, dtype=float))))
    if col_pitch <= 1.0 or row_pitch <= 1.0:
        return _failed("label_lattice_unsupported_geometry")

    scoring = section.scoring or ScoringParams()
    geometry_threshold = (
        int(binary_threshold)
        if binary_threshold is not None
        else int(scoring.binary_threshold)
    )
    components = _extract_glyph_components(
        gray,
        section,
        col_pitch=col_pitch,
        row_pitch=row_pitch,
        binary_threshold=geometry_threshold,
        width_min_frac=glyph_width_min_frac,
        height_min_frac=glyph_height_frac[0],
        height_max_frac=glyph_height_frac[1],
        area_min_frac=glyph_area_min_frac,
    )
    if not components:
        return _failed("label_lattice_no_components")

    x_tolerance = max(3.0, X_CLUSTER_TOL_FRAC * col_pitch)
    x_groups = _cluster_components(
        components, axis=0, tolerance=x_tolerance,
    )
    x_groups = [group for group in x_groups if len(group) >= MIN_X_GROUP_SUPPORT]
    x_groups.sort(key=lambda group: float(np.median([item[0] for item in group])))
    if len(x_groups) < n_options:
        return _failed(
            "label_lattice_too_few_columns",
            n_components=len(components),
            n_x_groups=len(x_groups),
        )

    y_tolerance = max(3.0, Y_CLUSTER_TOL_FRAC * row_pitch)
    min_observed_rows = max(2, int(np.ceil(n_rows * MIN_OBSERVED_ROW_FRACTION)))
    if min_row_support is None:
        min_row_support = max(3, n_options - 2)
    strong_row_support = max(3, n_options - 1)

    best = None
    for indices in itertools.combinations(range(len(x_groups)), n_options):
        columns = [x_groups[index] for index in indices]
        centers = np.asarray(
            [np.median([item[0] for item in column]) for column in columns],
            dtype=np.float64,
        )
        sx, tx, col_rmse = _fit_1d(template_cols, centers)
        if (
            not SCALE_MIN <= sx <= SCALE_MAX
            or col_rmse > CANDIDATE_MAX_COL_RMSE
        ):
            continue

        row_groups = _row_groups_for_columns(columns, tolerance=y_tolerance)
        useful = [group for group in row_groups if len(group) >= min_row_support]
        useful = _select_regular_row_groups(
            useful,
            n_rows=n_rows,
            expected_pitch=row_pitch,
        )
        if len(useful) < min_observed_rows:
            continue

        n_strong = sum(len(group) >= strong_row_support for group in useful)
        synchronized_support = sum(len(group) for group in useful)
        column_support = sum(min(n_rows, len(column)) for column in columns)
        # Synchronised rows dominate the rank.  This makes an adjacent
        # question-number column lose even when its raw vertical support is
        # comparable to a partially merged option-letter column.
        rank = (
            n_strong,
            synchronized_support,
            column_support,
            -col_rmse,
        )
        if best is None or rank > best[0]:
            best = (
                rank,
                columns,
                centers,
                useful,
                sx,
                tx,
                col_rmse,
                n_strong,
                synchronized_support,
            )

    if best is None:
        return _failed(
            "label_lattice_no_candidate",
            n_components=len(components),
            n_x_groups=len(x_groups),
        )

    (
        _rank,
        columns,
        col_centers,
        observed_groups,
        sx,
        tx,
        col_rmse,
        n_strong_rows,
        synchronized_support,
    ) = best
    n_observed_rows = len(observed_groups)
    rows, n_inferred_rows = _reconstruct_rows(
        observed_groups,
        n_rows=n_rows,
        expected_pitch=row_pitch,
    )
    if rows is None:
        return None, LabelLatticeFit(
            ok=False,
            sx=sx,
            tx=tx,
            col_rmse=col_rmse,
            n_components=len(components),
            n_x_groups=len(x_groups),
            n_observed_rows=n_observed_rows,
            n_strong_rows=n_strong_rows,
            synchronized_support=synchronized_support,
            warning="label_lattice_row_reconstruction_failed",
        )

    observed_pitch = float(np.median(np.diff([row["center"] for row in rows])))
    sy_size = observed_pitch / row_pitch
    fill_tops = [
        float(row["bottom"] + label_bottom_to_fill_top * sy_size)
        for row in rows
    ]
    template_fill_tops = [
        float(y + (grid.cell_height or 0)) for y in template_rows
    ]
    sy, ty, row_rmse = _fit_1d(template_fill_tops, fill_tops)
    fill_pitch = np.diff(np.asarray(fill_tops, dtype=np.float64))
    mean_pitch = float(np.mean(fill_pitch)) if fill_pitch.size else 0.0
    row_pitch_cv = (
        float(np.std(fill_pitch) / mean_pitch) if mean_pitch > 1e-6 else 1e9
    )
    min_column_support = min(len(column) for column in columns)

    fit = LabelLatticeFit(
        ok=False,
        sx=sx,
        sy=sy,
        tx=tx,
        ty=ty,
        col_rmse=col_rmse,
        row_rmse=row_rmse,
        row_pitch_cv=row_pitch_cv,
        n_components=len(components),
        n_x_groups=len(x_groups),
        n_observed_rows=n_observed_rows,
        n_strong_rows=n_strong_rows,
        n_inferred_rows=n_inferred_rows,
        min_column_support=min_column_support,
        synchronized_support=synchronized_support,
        col_centers=list(map(float, col_centers)),
        row_centers=[float(row["center"]) for row in rows],
        fill_tops=list(fill_tops),
    )

    min_strong_rows = int(np.ceil(n_rows * MIN_STRONG_ROW_FRACTION))
    max_inferred_rows = int(np.floor(n_rows * MAX_INFERRED_ROW_FRACTION))
    warning: Optional[str] = None
    if not (SCALE_MIN <= sx <= SCALE_MAX and SCALE_MIN <= sy <= SCALE_MAX):
        warning = "label_lattice_scale_out_of_range"
    elif col_rmse > TRUST_MAX_COL_RMSE:
        warning = "label_lattice_column_residual_high"
    elif row_rmse > TRUST_MAX_ROW_RMSE or row_pitch_cv > TRUST_MAX_PITCH_CV:
        warning = "label_lattice_row_residual_high"
    elif n_observed_rows < min_observed_rows or n_strong_rows < min_strong_rows:
        warning = "label_lattice_support_low"
    elif n_inferred_rows > max_inferred_rows or min_column_support < MIN_X_GROUP_SUPPORT:
        warning = "label_lattice_support_low"
    elif any(
        center < 0 or center >= gray.shape[1]
        for center in col_centers
    ) or any(top < 0 or top >= gray.shape[0] for top in fill_tops):
        warning = "label_lattice_out_of_bounds"

    if warning is not None:
        fit.warning = warning
        logger.info(
            "label_lattice FAIL warn=%s cols=%d rows=%d strong=%d inferred=%d "
            "sx=%.3f sy=%.3f crmse=%.1f rrmse=%.1f pitch_cv=%.3f",
            warning,
            len(col_centers),
            n_observed_rows,
            n_strong_rows,
            n_inferred_rows,
            sx,
            sy,
            col_rmse,
            row_rmse,
            row_pitch_cv,
        )
        return None, fit

    new_cell_width = max(8, int(round((grid.cell_width or 36) * sx)))
    new_cell_height = max(0, int(round((grid.cell_height or 0) * sy)))
    new_bubble_height = max(8, int(round((grid.bubble_height or 20) * sy)))
    new_rows = [int(round(top - new_cell_height)) for top in fill_tops]
    new_cols = [int(round(center)) for center in col_centers]

    new_grid = GridGeometry(
        rows=grid.rows,
        cols=grid.cols,
        questions_per_col=list(grid.questions_per_col),
        options=list(grid.options),
        cells_per_question=grid.cells_per_question,
        row_positions=new_rows,
        col_positions=[new_cols],
        row_pitch=float(np.median(np.diff(new_rows))) if len(new_rows) > 1 else 0.0,
        col_pitch=float(np.median(np.diff(new_cols))) if len(new_cols) > 1 else 0.0,
        cell_width=new_cell_width,
        cell_height=new_cell_height,
        bubble_height=new_bubble_height,
        first_row_offset=grid.first_row_offset,
    )

    region = section.region
    x0 = int(round(sx * region.x + tx))
    x1 = int(round(sx * (region.x + region.w) + tx))
    y0 = int(round(sy * region.y + ty))
    y1 = int(round(sy * (region.y + region.h) + ty))
    new_region = Region(
        x=x0,
        y=y0,
        w=max(1, x1 - x0),
        h=max(1, y1 - y0),
    )
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
    fit.ok = True
    logger.info(
        "label_lattice OK cols=%d rows=%d strong=%d inferred=%d "
        "sx=%.3f sy=%.3f crmse=%.1f rrmse=%.1f pitch_cv=%.3f",
        len(col_centers),
        n_observed_rows,
        n_strong_rows,
        n_inferred_rows,
        sx,
        sy,
        col_rmse,
        row_rmse,
        row_pitch_cv,
    )
    return new_section, fit


def _align_multi_column(
    gray: np.ndarray,
    section: AnswerSection,
    *,
    binary_threshold: Optional[int],
) -> Tuple[Optional[AnswerSection], LabelLatticeFit]:
    """Fit a grid printed as several visual columns sharing one lattice.

    The column with the most rows is fitted exactly as a single-column sheet,
    with every trust gate of that path.  Each remaining column is then accepted
    only if its own option labels are found where that fit predicts them, and
    one scale/offset per axis is refitted across all columns.  A missing or
    misplaced column fails closed: a sheet whose right-hand block is written
    answers, not boxes, must not borrow the left block's registration.
    """
    grid = section.grid
    qpc = list(grid.questions_per_col or [])
    n_visual = len(grid.col_positions)
    widths = {len(cols) for cols in grid.col_positions}
    if (
        len(qpc) != n_visual
        or len(widths) != 1
        or min(widths) < 3
        or len(grid.row_positions) != sum(qpc)
        or min(qpc) < 2
    ):
        return _failed("label_lattice_unsupported_geometry")

    starts = [sum(qpc[:visual]) for visual in range(n_visual)]
    visual_rows = [
        [float(y) for y in grid.row_positions[start : start + count]]
        for start, count in zip(starts, qpc)
    ]
    primary = max(range(n_visual), key=lambda visual: qpc[visual])
    primary_section = AnswerSection(
        type=section.type,
        question_start=section.question_start,
        question_end=section.question_end,
        region=section.region,
        grid=GridGeometry(
            rows=qpc[primary],
            cols=1,
            questions_per_col=[qpc[primary]],
            options=list(grid.options),
            cells_per_question=grid.cells_per_question,
            row_positions=list(grid.row_positions[starts[primary] : starts[primary] + qpc[primary]]),
            col_positions=[list(grid.col_positions[primary])],
            row_pitch=grid.row_pitch,
            col_pitch=grid.col_pitch,
            cell_width=grid.cell_width,
            # Fit the printed box tops; cell_height is re-applied below.
            cell_height=0,
            bubble_height=grid.bubble_height,
            first_row_offset=grid.first_row_offset,
        ),
        extraction_strategy=section.extraction_strategy,
        scoring=section.scoring,
        question_overrides=section.question_overrides,
    )
    aligned_primary, fit = _align_single_column(
        gray,
        primary_section,
        binary_threshold=binary_threshold,
        glyph_width_min_frac=MULTI_COL_GLYPH_WIDTH_MIN_FRAC,
        glyph_height_frac=(
            MULTI_COL_GLYPH_HEIGHT_MIN_FRAC, MULTI_COL_GLYPH_HEIGHT_MAX_FRAC,
        ),
        glyph_area_min_frac=MULTI_COL_GLYPH_AREA_MIN_FRAC,
        label_bottom_to_fill_top=MULTI_COL_LABEL_BOTTOM_TO_ROW_TOP,
        min_row_support=MULTI_COL_MIN_ROW_SUPPORT,
    )
    if aligned_primary is None or not fit.ok:
        return None, fit

    template_cols = np.asarray(grid.col_positions[primary], dtype=np.float64)
    col_pitch = float(np.median(np.diff(template_cols)))
    row_pitch = float(np.median(np.diff(np.asarray(visual_rows[primary]))))
    scoring = section.scoring or ScoringParams()
    geometry_threshold = (
        int(binary_threshold)
        if binary_threshold is not None
        else int(scoring.binary_threshold)
    )
    components = _extract_glyph_components(
        gray,
        section,
        col_pitch=col_pitch,
        row_pitch=row_pitch,
        binary_threshold=geometry_threshold,
        width_min_frac=MULTI_COL_GLYPH_WIDTH_MIN_FRAC,
        height_min_frac=MULTI_COL_GLYPH_HEIGHT_MIN_FRAC,
        height_max_frac=MULTI_COL_GLYPH_HEIGHT_MAX_FRAC,
        area_min_frac=MULTI_COL_GLYPH_AREA_MIN_FRAC,
    )
    # Box top sits a fixed distance below its label row's centre; carry that
    # offset over from the fitted column to predict the others' labels.
    label_to_top = float(np.median(
        np.asarray(fit.fill_tops) - np.asarray(fit.row_centers)
    ))
    x_tol = SECONDARY_SEARCH_TOL_FRAC * col_pitch * fit.sx
    y_tol = SECONDARY_SEARCH_TOL_FRAC * row_pitch * fit.sy
    gap = MULTI_COL_LABEL_BOTTOM_TO_ROW_TOP * fit.sy
    n_options = len(template_cols)
    min_observed = max(2, int(np.ceil(min(qpc) * MIN_OBSERVED_ROW_FRACTION)))

    x_src = list(map(float, template_cols))
    x_dst = list(fit.col_centers or [])
    y_src = list(visual_rows[primary])
    y_dst = list(fit.fill_tops or [])
    observed_tops: dict = {}
    observed_cols: dict = {}
    n_observed_rows = fit.n_observed_rows
    for visual in range(n_visual):
        if visual == primary:
            continue
        cols = [float(x) for x in grid.col_positions[visual]]
        found_x: List[List[float]] = [[] for _ in cols]
        tops: dict = {}
        for row_index, row_y in enumerate(visual_rows[visual]):
            centre_y = fit.sy * row_y + fit.ty - label_to_top
            bottoms = []
            hits = []
            for option, col_x in enumerate(cols):
                centre_x = fit.sx * col_x + fit.tx
                near = [
                    c for c in components
                    if abs(c[0] - centre_x) <= x_tol and abs(c[1] - centre_y) <= y_tol
                ]
                if not near:
                    continue
                best = max(near, key=lambda c: c[4])
                hits.append((option, best))
                bottoms.append(best[6] + best[3])
            if len(hits) >= MULTI_COL_MIN_ROW_SUPPORT:
                for option, best in hits:
                    found_x[option].append(best[0])
                tops[row_index] = float(np.median(bottoms)) + gap
        if len(tops) < min_observed or not all(found_x):
            fit.ok = False
            fit.warning = "label_lattice_secondary_column_missing"
            logger.info(
                "label_lattice FAIL warn=%s visual_col=%d observed=%d/%d",
                fit.warning, visual, len(tops), qpc[visual],
            )
            return None, fit
        observed_cols[visual] = [float(np.median(xs)) for xs in found_x]
        observed_tops[visual] = tops
        x_src.extend(cols)
        x_dst.extend(observed_cols[visual])
        for row_index, top in tops.items():
            y_src.append(visual_rows[visual][row_index])
            y_dst.append(top)
        n_observed_rows += len(tops)

    sx, tx, col_rmse = _fit_1d(x_src, x_dst)
    sy, ty, row_rmse = _fit_1d(y_src, y_dst)
    fit.sx, fit.tx, fit.sy, fit.ty = sx, tx, sy, ty
    fit.col_rmse = max(fit.col_rmse, col_rmse)
    fit.row_rmse = max(fit.row_rmse, row_rmse)
    fit.n_observed_rows = n_observed_rows
    if col_rmse > TRUST_MAX_COL_RMSE or row_rmse > TRUST_MAX_ROW_RMSE:
        fit.ok = False
        fit.warning = (
            "label_lattice_column_residual_high"
            if col_rmse > TRUST_MAX_COL_RMSE
            else "label_lattice_row_residual_high"
        )
        logger.info(
            "label_lattice FAIL warn=%s multi_col crmse=%.1f rrmse=%.1f",
            fit.warning, col_rmse, row_rmse,
        )
        return None, fit

    new_cell_height = max(0, int(round((grid.cell_height or 0) * sy)))
    new_cols: List[List[int]] = []
    new_rows: List[int] = []
    for visual in range(n_visual):
        if visual == primary:
            new_cols.append(list(aligned_primary.grid.col_positions[0]))
            new_rows.extend(aligned_primary.grid.row_positions)
            continue
        new_cols.append([int(round(x)) for x in observed_cols[visual]])
        for row_index, row_y in enumerate(visual_rows[visual]):
            top = observed_tops[visual].get(row_index, sy * row_y + ty)
            new_rows.append(int(round(top)))

    new_grid = GridGeometry(
        rows=grid.rows,
        cols=grid.cols,
        questions_per_col=list(qpc),
        options=list(grid.options),
        cells_per_question=grid.cells_per_question,
        row_positions=new_rows,
        col_positions=new_cols,
        row_pitch=aligned_primary.grid.row_pitch,
        col_pitch=aligned_primary.grid.col_pitch,
        cell_width=aligned_primary.grid.cell_width,
        cell_height=new_cell_height,
        bubble_height=aligned_primary.grid.bubble_height,
        first_row_offset=grid.first_row_offset,
    )
    region = section.region
    x0 = int(round(sx * region.x + tx))
    x1 = int(round(sx * (region.x + region.w) + tx))
    y0 = int(round(sy * region.y + ty))
    y1 = int(round(sy * (region.y + region.h) + ty))
    new_section = AnswerSection(
        type=section.type,
        question_start=section.question_start,
        question_end=section.question_end,
        region=Region(x=x0, y=y0, w=max(1, x1 - x0), h=max(1, y1 - y0)),
        grid=new_grid,
        extraction_strategy=section.extraction_strategy,
        scoring=section.scoring,
        question_overrides=section.question_overrides,
    )
    fit.col_centers = [x for cols in new_cols for x in map(float, cols)]
    fit.fill_tops = [float(y + new_cell_height) for y in new_rows]
    logger.info(
        "label_lattice OK multi_col visual_cols=%d rows=%d sx=%.3f sy=%.3f "
        "crmse=%.1f rrmse=%.1f",
        n_visual, n_observed_rows, sx, sy, col_rmse, row_rmse,
    )
    return new_section, fit
