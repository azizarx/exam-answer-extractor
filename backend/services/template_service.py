"""
Template loader and registry for exam layout definitions.

Loads JSON template configs from backend/templates/, resolves variant
inheritance, and provides auto-detection of which template matches a
given page image.
"""

import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Region:
    """Pixel bounding box at reference DPI."""
    x: int
    y: int
    w: int
    h: int

    def scaled(self, factor: float) -> "Region":
        return Region(
            x=round(self.x * factor),
            y=round(self.y * factor),
            w=round(self.w * factor),
            h=round(self.h * factor),
        )

    def to_tuple(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def crop_from(self, img: np.ndarray) -> np.ndarray:
        """Crop this region from an image (numpy array)."""
        return img[self.y : self.y + self.h, self.x : self.x + self.w]


@dataclass
class HeaderField:
    key: str
    label: str
    type: str  # text_line | grid_boxes | qr_code
    region: Optional[Region] = None
    prefix: Optional[str] = None
    max_length: Optional[int] = None


@dataclass
class ScoringParams:
    min_ink_pixels: int = 35
    min_ratio: float = 1.12
    binary_threshold: int = 180
    min_page_coverage: float = 0.60
    min_avg_ratio: float = 1.10


@dataclass
class GridGeometry:
    rows: int = 0
    cols: int = 1
    questions_per_col: List[int] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    cells_per_question: int = 0
    row_positions: List[int] = field(default_factory=list)
    col_positions: List[List[int]] = field(default_factory=list)
    row_pitch: float = 0.0
    col_pitch: float = 0.0
    cell_width: int = 0
    cell_height: int = 0
    bubble_height: int = 0
    first_row_offset: int = 0

    def scaled(self, factor: float) -> "GridGeometry":
        return GridGeometry(
            rows=self.rows,
            cols=self.cols,
            questions_per_col=list(self.questions_per_col),
            options=list(self.options),
            cells_per_question=self.cells_per_question,
            row_positions=[round(v * factor) for v in self.row_positions],
            col_positions=[[round(v * factor) for v in col] for col in self.col_positions],
            row_pitch=self.row_pitch * factor,
            col_pitch=self.col_pitch * factor,
            cell_width=round(self.cell_width * factor),
            cell_height=round(self.cell_height * factor),
            bubble_height=round(self.bubble_height * factor),
            first_row_offset=round(self.first_row_offset * factor),
        )


@dataclass
class QuestionOverride:
    type: str  # mcq_grid | numeric_grid | open_response | diagram
    region: Optional[Region] = None
    prompt_hint: Optional[str] = None


@dataclass
class AnswerSection:
    type: str  # mcq_grid | numeric_grid | open_response | diagram
    question_start: int
    question_end: int
    region: Region
    grid: Optional[GridGeometry] = None
    extraction_strategy: str = "cv_then_llm"
    scoring: Optional[ScoringParams] = None
    question_overrides: Dict[int, QuestionOverride] = field(default_factory=dict)

    @property
    def question_count(self) -> int:
        return self.question_end - self.question_start + 1

    def scaled(self, factor: float) -> "AnswerSection":
        return AnswerSection(
            type=self.type,
            question_start=self.question_start,
            question_end=self.question_end,
            region=self.region.scaled(factor),
            grid=self.grid.scaled(factor) if self.grid else None,
            extraction_strategy=self.extraction_strategy,
            scoring=self.scoring,  # thresholds are unitless, no scaling
            question_overrides={
                q: QuestionOverride(
                    type=ov.type,
                    region=ov.region.scaled(factor) if ov.region else None,
                    prompt_hint=ov.prompt_hint,
                )
                for q, ov in self.question_overrides.items()
            },
        )


@dataclass
class AnchorConfig:
    region: Region
    min_match_score: float = 0.45


@dataclass
class DetectionHints:
    filename_pattern: Optional[str] = None
    text_markers: List[str] = field(default_factory=list)
    logo_anchor: Optional[Region] = None


@dataclass
class ExamTemplate:
    """Fully resolved exam layout template."""
    id: str
    name: str
    brand: str
    year: Optional[int]
    paper: Optional[str]
    reference_dpi: int
    page_size: Tuple[int, int]  # (width, height)
    anchor: AnchorConfig
    detection: DetectionHints
    header_region: Region
    header_fields: List[HeaderField]
    sections: List[AnswerSection]
    variant_of: Optional[str] = None

    def scale_factor(self, actual_dpi: int) -> float:
        """Compute scale factor from reference DPI to actual DPI."""
        if actual_dpi == self.reference_dpi or actual_dpi <= 0:
            return 1.0
        return actual_dpi / self.reference_dpi

    def at_dpi(self, actual_dpi: int) -> "ExamTemplate":
        """Return a copy with all coordinates scaled to actual_dpi."""
        f = self.scale_factor(actual_dpi)
        if f == 1.0:
            return self
        return ExamTemplate(
            id=self.id,
            name=self.name,
            brand=self.brand,
            year=self.year,
            paper=self.paper,
            reference_dpi=actual_dpi,
            page_size=(round(self.page_size[0] * f), round(self.page_size[1] * f)),
            anchor=AnchorConfig(
                region=self.anchor.region.scaled(f),
                min_match_score=self.anchor.min_match_score,
            ),
            detection=self.detection,
            header_region=self.header_region.scaled(f),
            header_fields=self.header_fields,
            sections=[s.scaled(f) for s in self.sections],
            variant_of=self.variant_of,
        )

    @property
    def total_questions(self) -> int:
        return sum(s.question_count for s in self.sections)

    @property
    def has_mcq(self) -> bool:
        return any(s.type == "mcq_grid" for s in self.sections)

    @property
    def has_free_response(self) -> bool:
        return any(s.type in ("numeric_grid", "open_response", "diagram") for s in self.sections)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_region(d: Optional[dict]) -> Region:
    if not d:
        return Region(0, 0, 0, 0)
    return Region(x=d.get("x", 0), y=d.get("y", 0), w=d.get("w", 0), h=d.get("h", 0))


def _parse_header_field(d: dict) -> HeaderField:
    return HeaderField(
        key=d["key"],
        label=d["label"],
        type=d["type"],
        region=_parse_region(d.get("region")),
        prefix=d.get("prefix"),
        max_length=d.get("max_length"),
    )


def _parse_grid(d: Optional[dict]) -> Optional[GridGeometry]:
    if not d:
        return None
    # Normalize col_positions: flat [x1,x2,...] → [[x1,x2,...]]
    raw_cols = d.get("col_positions", [])
    if raw_cols and isinstance(raw_cols[0], (int, float)):
        col_positions = [raw_cols]
    else:
        col_positions = raw_cols
    return GridGeometry(
        rows=d.get("rows", 0),
        cols=d.get("cols", 1),
        questions_per_col=d.get("questions_per_col", []),
        options=d.get("options", []),
        cells_per_question=d.get("cells_per_question", 0),
        row_positions=d.get("row_positions", []),
        col_positions=col_positions,
        row_pitch=d.get("row_pitch", 0.0),
        col_pitch=d.get("col_pitch", 0.0),
        cell_width=d.get("cell_width", 0),
        cell_height=d.get("cell_height", 0),
        bubble_height=d.get("bubble_height", 0),
        first_row_offset=d.get("first_row_offset", 0),
    )


def _parse_scoring(d: Optional[dict]) -> Optional[ScoringParams]:
    if not d:
        return None
    return ScoringParams(
        min_ink_pixels=d.get("min_ink_pixels", 35),
        min_ratio=d.get("min_ratio", 1.12),
        binary_threshold=d.get("binary_threshold", 180),
        min_page_coverage=d.get("min_page_coverage", 0.60),
        min_avg_ratio=d.get("min_avg_ratio", 1.10),
    )


def _parse_question_overrides(d: Optional[dict]) -> Dict[int, QuestionOverride]:
    if not d:
        return {}
    overrides = {}
    for q_str, ov in d.items():
        overrides[int(q_str)] = QuestionOverride(
            type=ov["type"],
            region=_parse_region(ov.get("region")),
            prompt_hint=ov.get("prompt_hint"),
        )
    return overrides


def _parse_section(d: dict) -> AnswerSection:
    q = d["questions"]
    return AnswerSection(
        type=d["type"],
        question_start=q["start"],
        question_end=q["end"],
        region=_parse_region(d.get("region")),
        grid=_parse_grid(d.get("grid")),
        extraction_strategy=d.get("extraction_strategy", "cv_then_llm"),
        scoring=_parse_scoring(d.get("scoring")),
        question_overrides=_parse_question_overrides(d.get("question_overrides")),
    )


def _parse_detection(d: Optional[dict]) -> DetectionHints:
    if not d:
        return DetectionHints()
    return DetectionHints(
        filename_pattern=d.get("filename_pattern"),
        text_markers=d.get("text_markers", []),
        logo_anchor=_parse_region(d.get("logo_anchor")),
    )


def _parse_template(data: dict) -> ExamTemplate:
    header = data.get("header", {})
    return ExamTemplate(
        id=data["id"],
        name=data.get("name", data["id"]),
        brand=data.get("brand", ""),
        year=data.get("year"),
        paper=data.get("paper"),
        reference_dpi=data.get("reference_dpi", 300),
        page_size=(
            data.get("page_size", {}).get("width", 2480),
            data.get("page_size", {}).get("height", 3508),
        ),
        anchor=AnchorConfig(
            region=_parse_region(data.get("anchor")),
            min_match_score=data.get("anchor", {}).get("min_match_score", 0.45),
        ),
        detection=_parse_detection(data.get("detection")),
        header_region=_parse_region(header.get("region")),
        header_fields=[_parse_header_field(f) for f in header.get("fields", [])],
        sections=[_parse_section(s) for s in data.get("sections", [])],
        variant_of=data.get("variant_of"),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TemplateRegistry:
    """
    Loads all template JSON files, resolves variant inheritance, and
    provides lookup by ID or auto-detection from a page image.
    """

    def __init__(self, templates_dir: Path = TEMPLATES_DIR):
        self._templates: Dict[str, ExamTemplate] = {}
        self._raw: Dict[str, dict] = {}
        self._anchor_images: Dict[str, Optional[np.ndarray]] = {}
        self._templates_dir = templates_dir
        self._load_all()

    def _load_all(self) -> None:
        """Load and parse all template JSON files."""
        raw_by_id: Dict[str, dict] = {}

        for path in sorted(self._templates_dir.glob("*.json")):
            if path.name == "schema.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tid = data.get("id")
                if not tid:
                    logger.warning("Template file %s has no 'id' field, skipping", path.name)
                    continue
                raw_by_id[tid] = data
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to load template %s: %s", path.name, exc)

        self._raw = raw_by_id

        # Resolve variants: merge base into variant
        resolved: Dict[str, dict] = {}
        for tid, data in raw_by_id.items():
            resolved[tid] = self._resolve_variant(tid, raw_by_id)

        # Parse into typed dataclasses
        for tid, data in resolved.items():
            try:
                self._templates[tid] = _parse_template(data)
            except (KeyError, TypeError) as exc:
                logger.warning("Failed to parse template %s: %s", tid, exc)

        logger.info(
            "Loaded %d templates (%d base, %d variants)",
            len(self._templates),
            sum(1 for t in self._templates.values() if not t.variant_of),
            sum(1 for t in self._templates.values() if t.variant_of),
        )

    def _resolve_variant(
        self, tid: str, raw_by_id: Dict[str, dict], _seen: Optional[set] = None
    ) -> dict:
        """Deep-merge a variant onto its base template. Handles chains."""
        if _seen is None:
            _seen = set()
        if tid in _seen:
            raise ValueError(f"Circular variant_of chain detected: {tid}")
        _seen.add(tid)

        data = raw_by_id[tid]
        base_id = data.get("variant_of")
        if not base_id:
            return data

        if base_id not in raw_by_id:
            logger.warning(
                "Template %s references unknown base %s, treating as standalone",
                tid, base_id,
            )
            return data

        base = self._resolve_variant(base_id, raw_by_id, _seen)
        merged = deepcopy(base)
        # Overlay variant fields onto the base
        for key, value in data.items():
            if key == "variant_of":
                merged["variant_of"] = value
            elif isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged

    # -- Lookup ---

    def get(self, template_id: str) -> Optional[ExamTemplate]:
        """Get a template by its ID."""
        return self._templates.get(template_id)

    def get_or_raise(self, template_id: str) -> ExamTemplate:
        t = self._templates.get(template_id)
        if not t:
            raise KeyError(f"Unknown template: {template_id}. Available: {list(self._templates)}")
        return t

    def list_ids(self) -> List[str]:
        return sorted(self._templates.keys())

    def list_templates(self) -> List[ExamTemplate]:
        return sorted(self._templates.values(), key=lambda t: t.id)

    def list_by_brand(self, brand: str) -> List[ExamTemplate]:
        return [t for t in self._templates.values() if t.brand == brand]

    # -- Auto-detection ---

    def detect_by_filename(self, filename: str) -> List[ExamTemplate]:
        """
        Return templates whose filename_pattern matches the given filename,
        sorted by specificity (more text_markers = better).
        """
        matches = []
        for t in self._templates.values():
            pattern = t.detection.filename_pattern
            if pattern and re.search(pattern, filename, re.IGNORECASE):
                matches.append(t)
        # Sort: more text_markers = more specific = better match
        matches.sort(key=lambda t: len(t.detection.text_markers), reverse=True)
        return matches

    def detect_by_text(self, ocr_text: str) -> List[ExamTemplate]:
        """
        Return templates whose text_markers all appear in the OCR text,
        sorted by number of markers matched (most specific first).
        """
        text_upper = ocr_text.upper()
        matches = []
        for t in self._templates.values():
            markers = t.detection.text_markers
            if not markers:
                continue
            if all(m.upper() in text_upper for m in markers):
                matches.append((t, len(markers)))
        matches.sort(key=lambda pair: pair[1], reverse=True)
        return [t for t, _ in matches]

    def detect_by_anchor(
        self,
        page_image: np.ndarray,
        candidates: Optional[List[ExamTemplate]] = None,
    ) -> List[Tuple[ExamTemplate, float, Tuple[int, int]]]:
        """
        Template-match the anchor region against the page image.

        Args:
            page_image: BGR or grayscale image (numpy array).
            candidates: If provided, only try these templates.
                        Otherwise tries all templates with non-zero anchors.

        Returns:
            List of (template, match_score, (dx, dy)) sorted by score descending.
            Only includes matches above the template's min_match_score.
        """
        if candidates is None:
            candidates = list(self._templates.values())

        # Filter to templates that have calibrated anchors
        candidates = [
            t for t in candidates
            if t.anchor.region.w > 0 and t.anchor.region.h > 0
        ]

        if not candidates:
            return []

        gray = page_image
        if len(page_image.shape) == 3:
            gray = cv2.cvtColor(page_image, cv2.COLOR_BGR2GRAY)

        results = []
        for t in candidates:
            anchor_img = self._get_anchor_image(t, page_image)
            if anchor_img is None:
                continue

            anchor_gray = anchor_img
            if len(anchor_img.shape) == 3:
                anchor_gray = cv2.cvtColor(anchor_img, cv2.COLOR_BGR2GRAY)

            try:
                res = cv2.matchTemplate(gray, anchor_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
            except cv2.error:
                continue

            if max_val >= t.anchor.min_match_score:
                dx = int(max_loc[0] - t.anchor.region.x)
                dy = int(max_loc[1] - t.anchor.region.y)
                results.append((t, float(max_val), (dx, dy)))

        results.sort(key=lambda r: r[1], reverse=True)
        return results

    def detect(
        self,
        page_image: np.ndarray,
        filename: Optional[str] = None,
        ocr_text: Optional[str] = None,
    ) -> Optional[Tuple[ExamTemplate, float, Tuple[int, int]]]:
        """
        Full auto-detection pipeline:
        1. Narrow candidates by filename (if provided)
        2. Narrow further by OCR text markers (if provided)
        3. Anchor template-match against remaining candidates
        4. Return best match or None

        Returns:
            (template, anchor_score, (dx, dy)) or None if no match.
        """
        candidates = list(self._templates.values())

        # Step 1: filename filter (narrows candidates but doesn't eliminate)
        if filename:
            filename_matches = self.detect_by_filename(filename)
            if filename_matches:
                candidates = filename_matches

        # Step 2: text marker filter
        if ocr_text:
            text_matches = self.detect_by_text(ocr_text)
            if text_matches:
                # Intersect with current candidates if possible
                text_ids = {t.id for t in text_matches}
                narrowed = [t for t in candidates if t.id in text_ids]
                if narrowed:
                    candidates = narrowed
                else:
                    candidates = text_matches

        # Step 3: anchor matching
        anchor_results = self.detect_by_anchor(page_image, candidates)
        if anchor_results:
            return anchor_results[0]

        # Fallback: if we had text/filename matches but no calibrated anchor,
        # return the best text/filename match with zero offset
        if candidates and candidates[0].anchor.region.w == 0:
            return (candidates[0], 0.0, (0, 0))

        return None

    def _get_anchor_image(
        self, template: ExamTemplate, reference_image: np.ndarray
    ) -> Optional[np.ndarray]:
        """
        Get the anchor region image for template matching.
        Crops from the reference_image using the template's anchor coordinates.
        In production this would come from a stored reference image per template.
        """
        tid = template.id
        if tid in self._anchor_images:
            return self._anchor_images[tid]

        # Try to load a stored reference anchor image
        anchor_path = self._templates_dir / f"{tid}_anchor.png"
        if anchor_path.exists():
            img = cv2.imread(str(anchor_path))
            self._anchor_images[tid] = img
            return img

        # Fallback: crop from the provided image (only useful when the
        # reference_image IS the template's reference page)
        r = template.anchor.region
        if r.w > 0 and r.h > 0:
            h, w = reference_image.shape[:2]
            if r.y + r.h <= h and r.x + r.w <= w:
                anchor = reference_image[r.y : r.y + r.h, r.x : r.x + r.w].copy()
                self._anchor_images[tid] = anchor
                return anchor

        self._anchor_images[tid] = None
        return None

    def save_anchor_image(
        self, template_id: str, reference_image: np.ndarray
    ) -> Path:
        """
        Crop and save the anchor region from a reference scan.
        Call this once during calibration to persist the anchor image.
        """
        t = self.get_or_raise(template_id)
        r = t.anchor.region
        if r.w == 0 or r.h == 0:
            raise ValueError(f"Template {template_id} has no anchor region defined")

        anchor = reference_image[r.y : r.y + r.h, r.x : r.x + r.w].copy()
        out_path = self._templates_dir / f"{template_id}_anchor.png"
        cv2.imwrite(str(out_path), anchor)
        self._anchor_images[template_id] = anchor
        logger.info("Saved anchor image for %s: %s", template_id, out_path)
        return out_path


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[TemplateRegistry] = None


def get_template_registry() -> TemplateRegistry:
    """Get or create the global template registry singleton."""
    global _registry
    if _registry is None:
        _registry = TemplateRegistry()
    return _registry


def get_template(template_id: str) -> ExamTemplate:
    """Convenience: get a template by ID from the global registry."""
    return get_template_registry().get_or_raise(template_id)


def detect_template(
    page_image: np.ndarray,
    filename: Optional[str] = None,
    ocr_text: Optional[str] = None,
) -> Optional[Tuple[ExamTemplate, float, Tuple[int, int]]]:
    """Convenience: auto-detect template from the global registry."""
    return get_template_registry().detect(page_image, filename, ocr_text)
