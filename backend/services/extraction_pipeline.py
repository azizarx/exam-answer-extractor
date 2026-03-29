"""
Refactored AI Extraction Pipeline
- Token-optimized Gemini usage
- Multi-stage processing with selective LLM calls
- Format caching and clustering support
"""

import google.generativeai as genai
from PIL import Image
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from backend.services.page_analyzer import (
    PageLayout, RegionType, DetectedRegion, QuestionType, PageAnalyzer
)

logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ExamFormat:
    """Detected exam format structure - cached per unique layout."""
    format_id: str
    header_fields: List[Dict[str, str]]  # [{"key": "candidate_number", "label": "..."}]
    question_ranges: Dict[str, Tuple[int, int]]  # {"mcq": (1, 30), "drawing": (31, 35)}
    answer_options: List[str]  # ["A", "B", "C", "D", "E"]
    total_questions: int
    description: str
    header_region_hint: Optional[Dict] = None  # Approximate location
    answer_region_hint: Optional[Dict] = None


@dataclass
class ExtractionContext:
    """Context passed to extraction functions."""
    page_number: int
    layout: PageLayout
    format: ExamFormat
    header_image: Optional[Image.Image] = None
    answer_region_image: Optional[Image.Image] = None


@dataclass
class CandidateExtraction:
    """Extraction result for a single candidate/page."""
    page_number: int
    candidate_name: Optional[str] = None
    candidate_number: Optional[str] = None
    country: Optional[str] = None
    paper_type: Optional[str] = None
    extra_fields: Dict[str, Any] = field(default_factory=dict)
    answers: Dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    extraction_method: str = "unknown"  # "cv_only", "llm_assisted", "llm_full"
    errors: List[str] = field(default_factory=list)


# =============================================================================
# FORMAT DETECTION (Stage 4) - One LLM call per unique layout
# =============================================================================

class FormatDetector:
    """
    Detects exam format using Gemini.
    Key optimization: Only calls LLM once per unique layout hash.
    """

    FORMAT_DETECTION_PROMPT = """You are analyzing an exam answer sheet image. Your task is to understand the structure of this exam format.

Analyze the image and return a JSON object with this EXACT structure:

{
    "header_fields": [
        {"key": "candidate_name", "label": "The label shown for candidate name field"},
        {"key": "candidate_number", "label": "The label shown for candidate number"},
        {"key": "country", "label": "The label shown for country/centre"},
        {"key": "paper_type", "label": "The label for paper type if present"}
    ],
    "mcq_range": {"start": 1, "end": 30},
    "drawing_range": {"start": 31, "end": 35},
    "answer_options": ["A", "B", "C", "D"],
    "total_questions": 35,
    "description": "Brief description of the exam format"
}

Rules:
1. Only include header_fields that are actually visible on the sheet
2. mcq_range: questions with bubble/checkbox answers (A, B, C, D, etc.)
3. drawing_range: questions with drawing boxes or free-response areas (set to null if none)
4. Identify all available answer options (usually A-D or A-E)
5. Count total questions accurately

Return ONLY valid JSON, no markdown or explanation."""

    def __init__(self, model: genai.GenerativeModel, cache: Dict[str, ExamFormat] = None):
        self.model = model
        self.cache = cache or {}

    def detect_format(
        self,
        image: Image.Image,
        layout: PageLayout,
        force_refresh: bool = False
    ) -> ExamFormat:
        """
        Detect exam format from representative page.
        Uses cache to avoid redundant LLM calls.
        """
        cache_key = layout.layout_hash

        if not force_refresh and cache_key in self.cache:
            logger.info(f"Format cache hit for layout {cache_key}")
            return self.cache[cache_key]

        logger.info(f"Detecting format for layout {cache_key} (LLM call)")

        try:
            response = self.model.generate_content(
                [self.FORMAT_DETECTION_PROMPT, image],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
                )
            )

            result = self._parse_format_response(response.text, layout.layout_hash)
            self.cache[cache_key] = result
            return result

        except Exception as e:
            logger.error(f"Format detection failed: {e}")
            return self._create_fallback_format(layout.layout_hash)

    def _parse_format_response(self, response_text: str, format_id: str) -> ExamFormat:
        """Parse LLM response into ExamFormat."""
        # Clean response
        text = response_text.strip()
        if text.startswith("```"):
            text = re.sub(r"```json?\s*", "", text)
            text = re.sub(r"```\s*$", "", text)

        data = json.loads(text)

        # Parse question ranges
        question_ranges = {}
        if data.get("mcq_range"):
            mcq = data["mcq_range"]
            if isinstance(mcq, dict):
                question_ranges["mcq"] = (mcq.get("start", 1), mcq.get("end", 30))
            elif isinstance(mcq, str) and "-" in mcq:
                parts = mcq.split("-")
                question_ranges["mcq"] = (int(parts[0]), int(parts[1]))

        if data.get("drawing_range"):
            dr = data["drawing_range"]
            if isinstance(dr, dict) and dr.get("start"):
                question_ranges["drawing"] = (dr.get("start"), dr.get("end"))
            elif isinstance(dr, str) and "-" in dr:
                parts = dr.split("-")
                question_ranges["drawing"] = (int(parts[0]), int(parts[1]))

        return ExamFormat(
            format_id=format_id,
            header_fields=data.get("header_fields", []),
            question_ranges=question_ranges,
            answer_options=data.get("answer_options", ["A", "B", "C", "D"]),
            total_questions=data.get("total_questions", 30),
            description=data.get("description", "Unknown format")
        )

    def _create_fallback_format(self, format_id: str) -> ExamFormat:
        """Fallback format when detection fails."""
        return ExamFormat(
            format_id=format_id,
            header_fields=[
                {"key": "candidate_number", "label": "Candidate Number"},
                {"key": "candidate_name", "label": "Candidate Name"}
            ],
            question_ranges={"mcq": (1, 30)},
            answer_options=["A", "B", "C", "D"],
            total_questions=30,
            description="Fallback format"
        )


# =============================================================================
# LIGHTWEIGHT CLASSIFICATION (Stage 6) - Reduce LLM dependency
# =============================================================================

class AnswerClassifier:
    """
    Lightweight classification of answers using CV when possible.
    Only escalates to LLM for ambiguous cases.
    """

    def __init__(self, page_analyzer: PageAnalyzer):
        self.analyzer = page_analyzer

    def classify_answers_cv(
        self,
        image: Image.Image,
        layout: PageLayout,
        format: ExamFormat
    ) -> Tuple[Dict[str, str], List[int]]:
        """
        Attempt to classify MCQ answers using computer vision.

        Returns:
            - answers: Dict of question -> answer for confident classifications
            - unclear_questions: List of question numbers needing LLM
        """
        answers = {}
        unclear_questions = []

        # Find answer grid region
        grid_regions = [r for r in layout.regions if r.region_type == RegionType.ANSWER_GRID]

        if not grid_regions:
            # No grid detected, all questions need LLM
            mcq_range = format.question_ranges.get("mcq", (1, 30))
            return {}, list(range(mcq_range[0], mcq_range[1] + 1))

        # Analyze bubbles in each grid
        for grid in grid_regions:
            bubbles = self.analyzer.detect_filled_bubbles(image, grid)

            # Group bubbles by row (question)
            # TODO: Implement full bubble-to-question mapping

        # For now, mark all as unclear (needs full implementation)
        mcq_range = format.question_ranges.get("mcq", (1, 30))
        return {}, list(range(mcq_range[0], mcq_range[1] + 1))

    def needs_llm_extraction(
        self,
        layout: PageLayout,
        format: ExamFormat,
        cv_answers: Dict[str, str]
    ) -> bool:
        """Determine if LLM extraction is needed."""
        total_mcq = 0
        mcq_range = format.question_ranges.get("mcq")
        if mcq_range:
            total_mcq = mcq_range[1] - mcq_range[0] + 1

        # If CV extracted less than 80% of answers, use LLM
        if len(cv_answers) < total_mcq * 0.8:
            return True

        # If there are drawing questions, we need LLM
        if format.question_ranges.get("drawing"):
            return True

        return False


# =============================================================================
# TOKEN-OPTIMIZED LLM EXTRACTION (Stage 7)
# =============================================================================

class OptimizedExtractor:
    """
    Token-optimized extraction using Gemini.
    Key strategies:
    1. Send cropped regions instead of full page
    2. Use structured prompts with format context
    3. Batch similar extractions
    """

    # Concise extraction prompt - minimal tokens
    HEADER_EXTRACTION_PROMPT = """Extract student info from this header image.
Return JSON: {"candidate_name": str|null, "candidate_number": str|null, "country": str|null, "paper_type": str|null}
Only include fields visible in the image. Return null for missing fields."""

    ANSWER_EXTRACTION_TEMPLATE = """Extract answers from this exam sheet region.
Questions {start}-{end} are {question_type}.
Options: {options}

Return JSON object with question numbers as keys:
{{"1": "A", "2": "B", "3": "BL", ...}}

Answer codes:
- "A", "B", "C", "D", "E" = selected answer
- "BL" = blank/unanswered
- "IN" = invalid (multiple marks or unclear)
{drawing_instruction}

Return ONLY the JSON object."""

    def __init__(
        self,
        model: genai.GenerativeModel,
        max_workers: int = 3
    ):
        self.model = model
        self.max_workers = max_workers

    def extract_header(
        self,
        header_image: Image.Image,
        format: ExamFormat
    ) -> Dict[str, Any]:
        """Extract header fields from cropped header region."""
        try:
            # Build field-aware prompt
            field_hints = ", ".join([f["label"] for f in format.header_fields])
            prompt = f"{self.HEADER_EXTRACTION_PROMPT}\nExpected fields: {field_hints}"

            response = self.model.generate_content(
                [prompt, header_image],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=256,
                )
            )

            return self._parse_json_response(response.text)

        except Exception as e:
            logger.error(f"Header extraction failed: {e}")
            return {}

    def extract_answers(
        self,
        image: Image.Image,
        format: ExamFormat,
        question_range: Tuple[int, int],
        question_type: str = "MCQ"
    ) -> Dict[str, str]:
        """Extract answers for a range of questions."""
        try:
            # Build targeted prompt
            drawing_instruction = ""
            if question_type == "drawing":
                drawing_instruction = '- "DR" = drawing question (describe briefly if content visible)'

            prompt = self.ANSWER_EXTRACTION_TEMPLATE.format(
                start=question_range[0],
                end=question_range[1],
                question_type=question_type,
                options=", ".join(format.answer_options),
                drawing_instruction=drawing_instruction
            )

            response = self.model.generate_content(
                [prompt, image],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=512,
                )
            )

            return self._parse_json_response(response.text)

        except Exception as e:
            logger.error(f"Answer extraction failed: {e}")
            # Return BL for all questions on failure
            return {
                str(q): "BL"
                for q in range(question_range[0], question_range[1] + 1)
            }

    def extract_full_page(
        self,
        image: Image.Image,
        format: ExamFormat
    ) -> CandidateExtraction:
        """
        Full page extraction when CV pre-processing is insufficient.
        Still uses format context for efficiency.
        """
        prompt = self._build_full_extraction_prompt(format)

        try:
            response = self.model.generate_content(
                [prompt, image],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
                )
            )

            data = self._parse_json_response(response.text)
            return self._data_to_extraction(data, extraction_method="llm_full")

        except Exception as e:
            logger.error(f"Full extraction failed: {e}")
            return CandidateExtraction(
                page_number=0,
                errors=[str(e)],
                extraction_method="failed"
            )

    def _build_full_extraction_prompt(self, format: ExamFormat) -> str:
        """Build a token-efficient full extraction prompt."""
        # List expected fields
        header_keys = [f["key"] for f in format.header_fields]

        # Question info
        q_info = []
        for q_type, (start, end) in format.question_ranges.items():
            q_info.append(f"Q{start}-Q{end}: {q_type}")

        return f"""Extract all data from this exam sheet.

Header fields: {', '.join(header_keys)}
Questions: {'; '.join(q_info)}
Options: {', '.join(format.answer_options)}

Return JSON:
{{
    "candidate_name": str|null,
    "candidate_number": str|null,
    "country": str|null,
    "paper_type": str|null,
    "answers": {{"1": "A", "2": "BL", ...}}
}}

Codes: A-E=answer, BL=blank, IN=invalid, DR=drawing
Return ONLY JSON."""

    def _parse_json_response(self, text: str) -> Dict:
        """Parse JSON from LLM response."""
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"```json?\s*", "", text)
            text = re.sub(r"```\s*$", "", text)
        return json.loads(text)

    def _data_to_extraction(
        self,
        data: Dict,
        extraction_method: str = "llm_full"
    ) -> CandidateExtraction:
        """Convert raw dict to CandidateExtraction."""
        return CandidateExtraction(
            page_number=0,
            candidate_name=data.get("candidate_name"),
            candidate_number=data.get("candidate_number"),
            country=data.get("country"),
            paper_type=data.get("paper_type"),
            answers=data.get("answers", {}),
            extraction_method=extraction_method,
            confidence=0.9
        )


# =============================================================================
# MAIN PIPELINE ORCHESTRATOR
# =============================================================================

class RefactoredPipeline:
    """
    Main pipeline orchestrating all stages.
    Processes PDFs with minimal LLM token usage.
    """

    def __init__(
        self,
        gemini_api_key: str,
        gemini_model: str = "gemini-2.0-flash",
        max_workers: int = 3
    ):
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel(gemini_model)

        self.page_analyzer = PageAnalyzer()
        self.format_detector = FormatDetector(self.model)
        self.answer_classifier = AnswerClassifier(self.page_analyzer)
        self.extractor = OptimizedExtractor(self.model, max_workers)
        self.max_workers = max_workers

    def process_images(
        self,
        images: List[Image.Image],
        progress_callback: Optional[callable] = None
    ) -> List[CandidateExtraction]:
        """
        Main entry point: process list of page images.

        Pipeline stages:
        1. Analyze all pages (CV)
        2. Cluster by layout
        3. Detect format per cluster (LLM - once per unique format)
        4. Extract per page (CV + selective LLM)
        """
        results = []
        total_pages = len(images)

        # Stage 2-3: Analyze all pages
        if progress_callback:
            progress_callback("Analyzing page layouts...", 0, total_pages)

        layouts = []
        for i, img in enumerate(images):
            layout = self.page_analyzer.analyze_page(img, i + 1)
            layouts.append(layout)
            if progress_callback:
                progress_callback(f"Analyzed page {i+1}", i + 1, total_pages)

        # Filter non-blank pages
        valid_layouts = [l for l in layouts if not l.is_blank]
        logger.info(f"Found {len(valid_layouts)} non-blank pages out of {total_pages}")

        # Stage 3: Cluster layouts
        from backend.services.page_analyzer import LayoutClusterer
        clusterer = LayoutClusterer()
        clusters = clusterer.cluster_layouts(valid_layouts)
        logger.info(f"Detected {len(clusters)} unique exam formats")

        # Stage 4: Detect format per cluster (ONE LLM call per unique layout)
        formats: Dict[str, ExamFormat] = {}
        for layout_hash, page_nums in clusters.items():
            rep_page = clusterer.get_representative_page(page_nums, layouts)
            rep_image = images[rep_page - 1]
            rep_layout = layouts[rep_page - 1]

            exam_format = self.format_detector.detect_format(rep_image, rep_layout)
            formats[layout_hash] = exam_format
            logger.info(f"Format {layout_hash}: {exam_format.description}")

        # Stage 5-7: Extract per page
        if progress_callback:
            progress_callback("Extracting answers...", 0, len(valid_layouts))

        # Process pages in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for layout in valid_layouts:
                img = images[layout.page_number - 1]
                exam_format = formats.get(layout.format_group)
                future = executor.submit(
                    self._extract_page, img, layout, exam_format
                )
                futures[future] = layout.page_number

            completed = 0
            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    extraction = future.result()
                    extraction.page_number = page_num
                    results.append(extraction)
                except Exception as e:
                    logger.error(f"Page {page_num} extraction failed: {e}")
                    results.append(CandidateExtraction(
                        page_number=page_num,
                        errors=[str(e)]
                    ))

                completed += 1
                if progress_callback:
                    progress_callback(f"Extracted page {page_num}", completed, len(valid_layouts))

        # Sort by page number
        results.sort(key=lambda x: x.page_number)
        return results

    def _extract_page(
        self,
        image: Image.Image,
        layout: PageLayout,
        format: ExamFormat
    ) -> CandidateExtraction:
        """Extract data from a single page."""
        # Attempt CV-based answer classification first
        cv_answers, unclear = self.answer_classifier.classify_answers_cv(
            image, layout, format
        )

        # Determine extraction strategy
        if not self.answer_classifier.needs_llm_extraction(layout, format, cv_answers):
            # CV was sufficient
            return CandidateExtraction(
                page_number=layout.page_number,
                answers=cv_answers,
                extraction_method="cv_only",
                confidence=0.95
            )

        # Need LLM extraction
        return self.extractor.extract_full_page(image, format)

    def to_output_format(
        self,
        extractions: List[CandidateExtraction]
    ) -> List[Dict[str, Any]]:
        """Convert extractions to final JSON output format."""
        output = []
        for ext in extractions:
            candidate = {
                "candidate_name": ext.candidate_name,
                "candidate_number": ext.candidate_number,
                "country": ext.country,
                "answers": ext.answers
            }
            if ext.paper_type:
                candidate["paper_type"] = ext.paper_type
            if ext.extra_fields:
                candidate.update(ext.extra_fields)
            output.append(candidate)
        return output
