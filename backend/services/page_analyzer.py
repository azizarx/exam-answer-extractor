"""
Stage 2 & 3: Page Analysis and Layout Detection
Handles preprocessing, blank detection, and layout segmentation using CV techniques.
"""

import cv2
import numpy as np
from PIL import Image
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
import hashlib


class RegionType(Enum):
    HEADER = "header"
    ANSWER_GRID = "answer_grid"
    DRAWING_AREA = "drawing_area"
    TEXT_BLOCK = "text_block"
    UNKNOWN = "unknown"


class QuestionType(Enum):
    MCQ = "mcq"
    DRAWING = "drawing"
    OPEN_ENDED = "open_ended"
    UNKNOWN = "unknown"


@dataclass
class BoundingBox:
    x: int
    y: int
    width: int
    height: int

    def to_tuple(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    def crop_from(self, image: np.ndarray) -> np.ndarray:
        return image[self.y:self.y + self.height, self.x:self.x + self.width]


@dataclass
class DetectedRegion:
    region_type: RegionType
    bbox: BoundingBox
    confidence: float
    metadata: Dict = field(default_factory=dict)


@dataclass
class PageLayout:
    page_number: int
    width: int
    height: int
    layout_hash: str  # For clustering similar layouts
    regions: List[DetectedRegion] = field(default_factory=list)
    is_blank: bool = False
    quality_score: float = 1.0
    format_group: Optional[str] = None  # Assigned after clustering


@dataclass
class AnswerBubble:
    question_number: int
    option: str  # A, B, C, D, E
    bbox: BoundingBox
    is_filled: bool
    fill_confidence: float


class PageAnalyzer:
    """
    Analyzes exam pages using computer vision to:
    1. Detect blank/low-quality pages
    2. Segment layout into regions (header, answer grid, drawing areas)
    3. Generate layout fingerprints for clustering
    """

    def __init__(
        self,
        blank_threshold: float = 10.0,
        min_quality_score: float = 0.3,
        bubble_fill_threshold: float = 0.4
    ):
        self.blank_threshold = blank_threshold
        self.min_quality_score = min_quality_score
        self.bubble_fill_threshold = bubble_fill_threshold

    def analyze_page(self, image: Image.Image, page_number: int) -> PageLayout:
        """Main entry point: analyze a single page."""
        # Convert PIL to OpenCV format
        cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)

        height, width = gray.shape

        # Check if blank
        is_blank = self._is_blank(gray)
        if is_blank:
            return PageLayout(
                page_number=page_number,
                width=width,
                height=height,
                layout_hash="blank",
                is_blank=True,
                quality_score=0.0
            )

        # Quality assessment
        quality_score = self._assess_quality(gray)

        # Detect regions
        regions = self._detect_regions(cv_image, gray)

        # Generate layout hash for clustering
        layout_hash = self._compute_layout_hash(regions, width, height)

        return PageLayout(
            page_number=page_number,
            width=width,
            height=height,
            layout_hash=layout_hash,
            regions=regions,
            is_blank=False,
            quality_score=quality_score
        )

    def _is_blank(self, gray: np.ndarray) -> bool:
        """Detect if page is blank using standard deviation."""
        return np.std(gray) < self.blank_threshold

    def _assess_quality(self, gray: np.ndarray) -> float:
        """
        Assess image quality based on:
        - Sharpness (Laplacian variance)
        - Contrast
        - Noise level
        """
        # Laplacian variance for sharpness
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        sharpness = min(laplacian_var / 500.0, 1.0)

        # Contrast ratio
        contrast = (gray.max() - gray.min()) / 255.0

        # Combined score
        return (sharpness * 0.6 + contrast * 0.4)

    def _detect_regions(self, color_image: np.ndarray, gray: np.ndarray) -> List[DetectedRegion]:
        """
        Detect and classify regions using contour analysis and heuristics.
        """
        regions = []
        height, width = gray.shape

        # Apply adaptive thresholding
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 11, 2
        )

        # Find contours
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Header detection: top 15% of page with horizontal lines/text
        header_region = self._detect_header_region(gray, width, height)
        if header_region:
            regions.append(header_region)

        # Answer grid detection: look for regular bubble patterns
        grid_regions = self._detect_answer_grids(binary, gray, width, height)
        regions.extend(grid_regions)

        # Drawing area detection: large empty boxes
        drawing_regions = self._detect_drawing_areas(contours, width, height)
        regions.extend(drawing_regions)

        return regions

    def _detect_header_region(self, gray: np.ndarray, width: int, height: int) -> Optional[DetectedRegion]:
        """Detect the header region (typically top 10-20% with student info)."""
        # Assume header is in top 20% of page
        header_height = int(height * 0.20)
        header_crop = gray[0:header_height, :]

        # Check if there's content in this region
        if np.std(header_crop) > self.blank_threshold:
            return DetectedRegion(
                region_type=RegionType.HEADER,
                bbox=BoundingBox(0, 0, width, header_height),
                confidence=0.9,
                metadata={"estimated": True}
            )
        return None

    def _detect_answer_grids(
        self, binary: np.ndarray, gray: np.ndarray, width: int, height: int
    ) -> List[DetectedRegion]:
        """
        Detect MCQ answer grids by looking for:
        - Regular patterns of circles/bubbles
        - Horizontal alignment of options
        """
        regions = []

        # Use Hough circles to find bubbles
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1, minDist=15,
            param1=50, param2=30, minRadius=5, maxRadius=20
        )

        if circles is not None:
            circles = np.uint16(np.around(circles))

            # Cluster circles into rows (questions)
            if len(circles[0]) > 10:  # Likely an answer grid
                # Find bounding box of all circles
                xs = circles[0, :, 0]
                ys = circles[0, :, 1]

                min_x, max_x = int(xs.min()) - 20, int(xs.max()) + 20
                min_y, max_y = int(ys.min()) - 20, int(ys.max()) + 20

                regions.append(DetectedRegion(
                    region_type=RegionType.ANSWER_GRID,
                    bbox=BoundingBox(min_x, min_y, max_x - min_x, max_y - min_y),
                    confidence=0.85,
                    metadata={"bubble_count": len(circles[0])}
                ))

        return regions

    def _detect_drawing_areas(
        self, contours: List, width: int, height: int
    ) -> List[DetectedRegion]:
        """Detect drawing/free-response areas (large rectangular regions)."""
        regions = []
        min_area = (width * height) * 0.02  # At least 2% of page

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            aspect_ratio = w / h if h > 0 else 0

            # Drawing areas are typically wide rectangles
            if area > min_area and 0.5 < aspect_ratio < 5:
                # Check if mostly empty inside (drawing box)
                regions.append(DetectedRegion(
                    region_type=RegionType.DRAWING_AREA,
                    bbox=BoundingBox(x, y, w, h),
                    confidence=0.7,
                    metadata={"area_ratio": area / (width * height)}
                ))

        return regions

    def _compute_layout_hash(
        self, regions: List[DetectedRegion], width: int, height: int
    ) -> str:
        """
        Generate a hash representing the layout structure.
        Similar layouts will produce similar hashes for clustering.
        """
        # Normalize region positions to 10x10 grid
        grid_size = 10
        layout_grid = []

        for region in regions:
            norm_x = int((region.bbox.x / width) * grid_size)
            norm_y = int((region.bbox.y / height) * grid_size)
            norm_w = int((region.bbox.width / width) * grid_size)
            norm_h = int((region.bbox.height / height) * grid_size)
            layout_grid.append(f"{region.region_type.value}:{norm_x},{norm_y},{norm_w},{norm_h}")

        layout_str = "|".join(sorted(layout_grid))
        return hashlib.md5(layout_str.encode()).hexdigest()[:12]

    def detect_filled_bubbles(
        self, image: Image.Image, grid_region: DetectedRegion
    ) -> List[AnswerBubble]:
        """
        Analyze an answer grid region to detect which bubbles are filled.
        Returns list of detected bubble states.
        """
        cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)

        # Crop to grid region
        cropped = grid_region.bbox.crop_from(gray)

        # Find circles
        circles = cv2.HoughCircles(
            cropped, cv2.HOUGH_GRADIENT, dp=1, minDist=15,
            param1=50, param2=30, minRadius=5, maxRadius=20
        )

        bubbles = []
        if circles is not None:
            circles = np.uint16(np.around(circles))

            for circle in circles[0]:
                x, y, r = circle

                # Create mask for this bubble
                mask = np.zeros(cropped.shape, dtype=np.uint8)
                cv2.circle(mask, (x, y), r, 255, -1)

                # Calculate fill ratio
                bubble_pixels = cropped[mask == 255]
                fill_ratio = 1 - (np.mean(bubble_pixels) / 255.0)

                is_filled = fill_ratio > self.bubble_fill_threshold

                # TODO: Map to question number and option based on position
                # This requires knowing the grid structure

        return bubbles


class LayoutClusterer:
    """
    Groups pages with similar layouts together.
    This allows us to detect different exam formats in the same PDF.
    """

    def __init__(self, similarity_threshold: float = 0.8):
        self.similarity_threshold = similarity_threshold

    def cluster_layouts(self, layouts: List[PageLayout]) -> Dict[str, List[int]]:
        """
        Group pages by layout similarity.
        Returns: {format_group_id: [page_numbers]}
        """
        clusters: Dict[str, List[int]] = {}

        for layout in layouts:
            if layout.is_blank:
                continue

            # Simple clustering by exact hash match
            # TODO: Implement fuzzy matching for more robustness
            if layout.layout_hash not in clusters:
                clusters[layout.layout_hash] = []

            clusters[layout.layout_hash].append(layout.page_number)
            layout.format_group = layout.layout_hash

        return clusters

    def get_representative_page(self, cluster_pages: List[int], layouts: List[PageLayout]) -> int:
        """Get the best quality page from a cluster to use for format detection."""
        best_page = cluster_pages[0]
        best_quality = 0.0

        for layout in layouts:
            if layout.page_number in cluster_pages and layout.quality_score > best_quality:
                best_quality = layout.quality_score
                best_page = layout.page_number

        return best_page
