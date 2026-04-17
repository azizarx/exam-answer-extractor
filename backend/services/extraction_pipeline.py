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
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from backend.services.page_analyzer import (
    PageLayout, RegionType, DetectedRegion, QuestionType, PageAnalyzer
)
from backend.services.gemini_client import create_gemini_model

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

    FORMAT_DETECTION_PROMPT = """Analyze this exam answer sheet image to understand its structure.

Return a JSON object:

{
    "header_fields": [
        {"key": "candidate_name", "label": "Label for name field"},
        {"key": "candidate_number", "label": "Label for candidate number"},
        {"key": "country", "label": "Label for country/centre"},
        {"key": "paper_type", "label": "Label for paper type"}
    ],
    "mcq_range": {"start": 1, "end": 20},
    "numeric_range": {"start": 21, "end": 25},
    "drawing_range": null,
    "answer_options": ["A", "B", "C", "D", "E"],
    "total_questions": 25,
    "description": "Brief format description"
}

QUESTION TYPE DEFINITIONS:

1. mcq_range: Multiple choice with BUBBLES to fill (A, B, C, D, E options)
   - Student marks ONE bubble per question
   - Look for grid of circles/bubbles

2. numeric_range: FREE RESPONSE with BOXES for writing numbers/text
   - Student WRITES digits or letters in boxes
   - Examples: "99990", "328", "Paris", "2024"
   - These are WRITTEN answers, not drawings

3. drawing_range: ONLY for actual SKETCH/DIAGRAM areas
   - Student draws pictures, graphs, figures, stick figures
   - NOT for written numbers or text
   - Set to null if no drawing areas exist

IMPORTANT: Written numbers in boxes = numeric_range, NOT drawing_range
A drawing is a PICTURE you cannot type. Numbers/text you CAN type.

Return ONLY valid JSON."""

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

        if data.get("numeric_range"):
            nr = data["numeric_range"]
            if isinstance(nr, dict) and nr.get("start"):
                question_ranges["numeric"] = (nr.get("start"), nr.get("end"))
            elif isinstance(nr, str) and "-" in nr:
                parts = nr.split("-")
                question_ranges["numeric"] = (int(parts[0]), int(parts[1]))

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
            question_ranges={"numeric": (1, 20)},  # Default to numeric for flexibility
            answer_options=["A", "B", "C", "D"],
            total_questions=20,
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
        mcq_range = format.question_ranges.get("mcq")
        if not mcq_range:
            return {}, []

        options = [str(opt).strip().upper() for opt in (format.answer_options or []) if str(opt).strip()]
        if len(options) < 2:
            options = ["A", "B", "C", "D", "E"]

        # Find answer grid region (prefer largest if multiple)
        grid_regions = [r for r in layout.regions if r.region_type == RegionType.ANSWER_GRID]
        if not grid_regions:
            return {}, list(range(mcq_range[0], mcq_range[1] + 1))

        grid = max(grid_regions, key=lambda region: region.bbox.width * region.bbox.height)
        return self._estimate_marks_from_grid(image, grid, mcq_range, options)

    def _estimate_marks_from_grid(
        self,
        image: Image.Image,
        grid_region: DetectedRegion,
        mcq_range: Tuple[int, int],
        options: List[str],
    ) -> Tuple[Dict[str, str], List[int]]:
        """
        Estimate marked MCQ options from the detected answer grid.
        Returns only high-confidence option letters; ambiguous rows are left unclear.
        """
        cv_image = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        cropped = grid_region.bbox.crop_from(gray)

        q_start, q_end = int(mcq_range[0]), int(mcq_range[1])
        q_count = max(1, q_end - q_start + 1)
        option_count = max(2, len(options))
        height, width = cropped.shape[:2]

        if height < q_count * 6 or width < option_count * 10:
            return {}, list(range(q_start, q_end + 1))

        denoised = cv2.medianBlur(cropped, 3)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)
        binary_inv = cv2.adaptiveThreshold(
            enhanced,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            35,
            9,
        )

        # Skip left-side question number gutter; focus on options area.
        left_gutter = int(width * 0.18)
        answer_area = binary_inv[:, left_gutter:] if left_gutter < width - option_count else binary_inv
        answer_h, answer_w = answer_area.shape[:2]

        row_h = answer_h / float(q_count)
        col_w = answer_w / float(option_count)

        answers: Dict[str, str] = {}
        unclear: List[int] = []

        min_mark_score = 0.075
        min_separation = 1.20

        for idx in range(q_count):
            q_num = q_start + idx
            y0 = int(round(idx * row_h))
            y1 = int(round((idx + 1) * row_h))
            if y1 <= y0:
                y1 = min(answer_h, y0 + 1)

            row = answer_area[max(0, y0):min(answer_h, y1), :]
            if row.size == 0:
                unclear.append(q_num)
                continue

            pad = max(1, int(row.shape[0] * 0.08))
            row_core = row[pad:row.shape[0] - pad, :] if row.shape[0] > pad * 2 else row

            option_scores: List[float] = []
            for col in range(option_count):
                x0 = int(round(col * col_w))
                x1 = int(round((col + 1) * col_w))
                if x1 <= x0:
                    x1 = min(answer_w, x0 + 1)
                cell = row_core[:, max(0, x0):min(answer_w, x1)]
                if cell.size == 0:
                    option_scores.append(0.0)
                    continue

                ink_ratio = float(np.count_nonzero(cell)) / float(cell.size)
                lower = cell[cell.shape[0] // 2 :, :]
                lower_ink = float(np.count_nonzero(lower)) / float(lower.size) if lower.size else 0.0
                score = (ink_ratio * 0.65) + (lower_ink * 0.35)
                option_scores.append(score)

            ranked = sorted(
                enumerate(option_scores),
                key=lambda item: item[1],
                reverse=True,
            )
            best_idx, best_score = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0

            if best_score < min_mark_score:
                unclear.append(q_num)
                continue

            separation = best_score / max(second_score, 1e-6)
            if separation < min_separation:
                unclear.append(q_num)
                continue

            answers[str(q_num)] = options[min(best_idx, len(options) - 1)]

        return answers, unclear

    def needs_llm_extraction(
        self,
        layout: PageLayout,
        format: ExamFormat,
        cv_answers: Dict[str, str]
    ) -> bool:
        """Determine if LLM extraction is needed."""
        # If CV got zero answers, we definitely need LLM
        if len(cv_answers) == 0:
            return True

        total_mcq = 0
        mcq_range = format.question_ranges.get("mcq")
        if mcq_range:
            total_mcq = mcq_range[1] - mcq_range[0] + 1

        # If CV extracted less than 80% of answers, use LLM
        if total_mcq > 0 and len(cv_answers) < total_mcq * 0.8:
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
{{"1": "A", "2": "99990", "3": "BL", ...}}

ANSWER TYPES:
- MCQ (bubbles/boxes/rectangles): return the LETTER LABEL of the marked option.
  The mark may be X, shading, dark fill, tick, dot, or a colored box under the option.
  Use the printed labels on the sheet; do NOT infer by position index alone.
- FREE RESPONSE (written in boxes): Extract the actual number/text, e.g. "99990", "328", "Paris"
- "BL" = completely blank (no marks, nothing written)
- "IN" = invalid (multiple marks or illegible)
{drawing_instruction}

IMPORTANT: Written numbers like "99990" or "328" are FREE RESPONSES, not drawings!
IMPORTANT: If handwriting spills slightly outside the answer box, still read it as that question's response.
Only use "DR" for actual pictures/sketches that cannot be typed.

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
                drawing_instruction = '- "DR" = ONLY for actual drawings/sketches/diagrams (not numbers or text). Describe the drawing briefly, e.g. "stick figure", "triangle"'

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
        format: ExamFormat,
        cv_seed_answers: Optional[Dict[str, str]] = None
    ) -> CandidateExtraction:
        """
        Full page extraction when CV pre-processing is insufficient.
        Uses two-pass approach for better MCQ detection.
        """
        try:
            # Check if we have MCQ questions - use focused extraction
            mcq_range = format.question_ranges.get("mcq")

            if mcq_range:
                # PASS 1: Focused MCQ extraction
                mcq_answers = self._extract_mcq_focused(image, mcq_range, format.answer_options)
            else:
                mcq_answers = {}

            # PASS 2: Header + other answers
            prompt = self._build_full_extraction_prompt(format)
            response = self.model.generate_content(
                [prompt, image],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
                )
            )

            data = self._parse_json_response(response.text)
            data.setdefault("answers", {})

            # Merge MCQ answers (prefer focused extraction)
            if mcq_answers:
                for q, ans in mcq_answers.items():
                    if ans != "BL" or data.get("answers", {}).get(q) == "BL":
                        data.setdefault("answers", {})[q] = ans

            # High-confidence CV marks override LLM for MCQ when available.
            if cv_seed_answers:
                option_set = {str(opt).strip().upper() for opt in (format.answer_options or []) if str(opt).strip()}
                if not option_set:
                    option_set = {"A", "B", "C", "D", "E"}
                for q, ans in cv_seed_answers.items():
                    normalized = str(ans).strip().upper()
                    if normalized in option_set:
                        data["answers"][str(q)] = normalized

            logger.info(f"LLM extraction successful: {len(data.get('answers', {}))} answers")
            return self._data_to_extraction(data, format, extraction_method="llm_full")

        except Exception as e:
            logger.error(f"Full extraction failed: {e}")
            return CandidateExtraction(
                page_number=0,
                errors=[str(e)],
                extraction_method="failed"
            )

    def _extract_mcq_focused(
        self,
        image: Image.Image,
        mcq_range: tuple,
        options: List[str]
    ) -> Dict[str, str]:
        """Focused MCQ-only extraction for better accuracy."""
        prompt = f"""TASK: Extract MCQ answers from questions {mcq_range[0]} to {mcq_range[1]}.

Each question row has {len(options)} option areas labeled: {', '.join(options)}
Students mark by X/fill/shade/check, or by coloring a box under the chosen option.

HOW TO READ EACH ROW:
1. Find the question number on the left
2. Look across the row at each option area ({', '.join(options)})
3. Identify which ONE option area has a clear deliberate mark
4. Return the PRINTED LETTER LABEL of that marked option

IMPORTANT DISTINCTIONS:
- A MARKED option area: has visible X/fill/shading/check/tick/dot or colored box under label
- An UNMARKED option area: empty/clear/pristine with no deliberate mark
- Each question has exactly ONE answer - find the ONE marked option
- If truly no option is marked, return "BL"
- If more than one option is clearly marked, return "IN"

Return JSON with question numbers as keys:
{{{', '.join([f'"{q}": "?"' for q in range(mcq_range[0], min(mcq_range[0]+3, mcq_range[1]+1))])}...}}

Return ONLY the JSON object."""

        try:
            response = self.model.generate_content(
                [prompt, image],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=512,
                )
            )
            return self._parse_json_response(response.text)
        except Exception as e:
            logger.warning(f"Focused MCQ extraction failed: {e}")
            return {}

    def _build_full_extraction_prompt(self, format: ExamFormat) -> str:
        """Build a token-efficient full extraction prompt."""
        # List expected fields
        header_keys = [f["key"] for f in format.header_fields]

        # Build detailed MCQ instructions if present
        mcq_instruction = ""
        mcq_range = format.question_ranges.get("mcq")
        if mcq_range:
            mcq_instruction = f"""
MCQ QUESTIONS {mcq_range[0]}-{mcq_range[1]}:
- Each row has options: {', '.join(format.answer_options)}
- CAREFULLY examine each option area (bubble/box/rectangle) for ANY deliberate mark:
  * X mark or cross through option area
  * Filled, shaded, darkened, or colored box under an option
  * Checkmark, tick, or dot
  * Any pencil/pen mark in/through option area or directly under its label
- Return the PRINTED LETTER LABEL for the marked option (not just column position)
- "BL" = ONLY if the entire row is pristine/untouched with zero marks
- Most students answer every question, so BL should be rare
"""

        # Build numeric/free-response instructions if present
        numeric_instruction = ""
        numeric_range = format.question_ranges.get("numeric")
        if numeric_range:
            numeric_instruction = f"""
FREE RESPONSE QUESTIONS {numeric_range[0]}-{numeric_range[1]}:
These are questions where students WRITE numbers or text in boxes.
- Extract EXACTLY what is written: numbers like "99990", "328", "5040", "12"
- Or text like "Paris", "Monday", "2024"
- These are NOT drawings - they contain readable characters
- If writing crosses slightly outside box boundaries, still capture it for that question
- Return "BL" only if the boxes are completely empty
"""

        # Build drawing instructions if present
        drawing_instruction = ""
        drawing_range = format.question_ranges.get("drawing")
        if drawing_range:
            drawing_instruction = f"""
DRAWING QUESTIONS {drawing_range[0]}-{drawing_range[1]}:
These are questions where students DRAW pictures, diagrams, or sketches.
- "DR" = actual graphical content: stick figures, shapes, graphs, diagrams, arrows
- If you see NUMBERS or TEXT written instead, extract them as string (not DR)
- A drawing is something you cannot type - it must be sketched/drawn
"""

        return f"""Extract all data from this exam answer sheet image.

STEP 1 - HEADER INFORMATION:
Extract: {', '.join(header_keys)}
Look for labeled fields at the top of the sheet.

STEP 2 - ANSWERS:
{mcq_instruction}{numeric_instruction}{drawing_instruction}
Return JSON:
{{
    "candidate_name": str|null,
    "candidate_number": str|null,
    "country": str|null,
    "paper_type": str|null,
    "answers": {{"1": "B", "2": "C", "3": "99990", ...}}
}}

CRITICAL RULES:
1. MCQ: Look for marked option areas (bubble/box/colored area under option) and return the PRINTED letter label
2. FREE RESPONSE: Numbers/text WRITTEN in boxes → extract exact value ("99990", "328", "hello"), including slight overflow outside box
3. DRAWING: Only for actual SKETCHES/DIAGRAMS that cannot be typed → return "DR"
4. BLANK: Only if completely empty with no marks at all → return "BL"

NEVER return "DR" for numbers or text - those are free responses, not drawings!
A drawing is a picture/sketch/diagram, NOT written characters.

Return ONLY valid JSON."""

    def _parse_json_response(self, text: str) -> Dict:
        """Parse JSON from LLM response with tolerant fallbacks."""
        raw = (text or "").strip()
        if not raw:
            raise ValueError("Empty model response")

        candidates: List[str] = []

        # 1) JSON inside fenced code blocks
        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.IGNORECASE | re.DOTALL)
        if fence:
            candidates.append(fence.group(1).strip())

        # 2) Entire response as-is
        candidates.append(raw)

        # 3) First JSON object span
        obj_start = raw.find("{")
        obj_end = raw.rfind("}")
        if obj_start != -1 and obj_end > obj_start:
            candidates.append(raw[obj_start:obj_end + 1].strip())

        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise ValueError("No valid JSON object found in model response")

    @staticmethod
    def _question_key(raw_key: Any) -> str:
        key = str(raw_key).strip()
        if not key:
            return ""
        match = re.search(r"\d+", key)
        if match:
            return str(int(match.group(0)))
        return key

    @staticmethod
    def _normalize_range(question_range: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
        if not question_range:
            return None
        try:
            start = int(question_range[0])
            end = int(question_range[1])
        except (TypeError, ValueError, IndexError):
            return None
        if start > end:
            start, end = end, start
        return start, end

    @staticmethod
    def _is_blank_token(value: str) -> bool:
        return value in {
            "", "BL", "BLANK", "EMPTY", "NONE", "NO ANSWER", "N/A", "NA", "UNANSWERED", "NULL"
        }

    @staticmethod
    def _is_invalid_token(value: str) -> bool:
        return value in {
            "IN", "INVALID", "MULTIPLE", "MULTI", "MULTI-MARK", "UNCLEAR", "ILLEGIBLE", "AMBIGUOUS"
        }

    def _looks_like_mcq_answer(self, raw_value: Any, options: List[str]) -> bool:
        if raw_value is None:
            return True

        value = str(raw_value).strip().upper()
        if self._is_blank_token(value) or self._is_invalid_token(value):
            return True

        option_set = {opt.upper() for opt in options if opt}
        if not option_set:
            option_set = {"A", "B", "C", "D", "E"}

        if value in option_set:
            return True

        pattern = "|".join(re.escape(opt) for opt in sorted(option_set, key=len, reverse=True))
        if pattern and re.search(rf"(?<![A-Z0-9])({pattern})(?![A-Z0-9])", value):
            return True

        compact = re.sub(r"[^A-Z]", "", value)
        if compact and all(ch in option_set for ch in compact):
            return True

        return False

    def _normalize_mcq_answer(self, raw_value: Any, options: List[str]) -> str:
        option_set = {opt.upper() for opt in options if opt}
        if not option_set:
            option_set = {"A", "B", "C", "D", "E"}

        if raw_value is None:
            return "BL"

        value = str(raw_value).strip()
        upper = value.upper()
        if self._is_blank_token(upper):
            return "BL"
        if self._is_invalid_token(upper):
            return "IN"

        pattern = "|".join(re.escape(opt) for opt in sorted(option_set, key=len, reverse=True))
        if pattern:
            matches = re.findall(rf"(?<![A-Z0-9])({pattern})(?![A-Z0-9])", upper)
            unique = sorted(set(matches))
            if len(unique) == 1:
                return unique[0]
            if len(unique) > 1:
                return "IN"

        compact = re.sub(r"[^A-Z]", "", upper)
        if compact and len(set(compact)) == 1 and compact[0] in option_set:
            return compact[0]
        if compact:
            unique = {ch for ch in compact if ch in option_set}
            if len(unique) == 1:
                return next(iter(unique))
            if len(unique) > 1:
                return "IN"

        return "IN"

    def _normalize_open_answer(self, raw_value: Any) -> str:
        if raw_value is None:
            return "BL"
        value = str(raw_value).strip()
        upper = value.upper()
        if self._is_blank_token(upper):
            return "BL"
        if self._is_invalid_token(upper):
            return "IN"
        return value

    def _normalize_drawing_answer(self, raw_value: Any) -> str:
        normalized = self._normalize_open_answer(raw_value)
        if normalized in {"BL", "IN"}:
            return normalized

        upper = normalized.upper()
        if upper == "DR":
            return "DR"

        # If model returns a drawing descriptor, normalize to DR for consistency.
        if re.search(r"\b(DRAW|DRAWING|SKETCH|DIAGRAM|GRAPH|FIGURE|SHAPE|DR)\b", upper):
            return "DR"

        return normalized

    def _normalize_answers(self, raw_answers: Any, format: Optional[ExamFormat]) -> Dict[str, str]:
        if not isinstance(raw_answers, dict):
            raw_answers = {}

        keyed_answers: Dict[str, Any] = {}
        for raw_key, raw_value in raw_answers.items():
            key = self._question_key(raw_key)
            if key:
                keyed_answers[key] = raw_value

        normalized: Dict[str, str] = {}
        covered = set()
        options = format.answer_options if format and format.answer_options else ["A", "B", "C", "D", "E"]

        if format:
            mcq_range = self._normalize_range(format.question_ranges.get("mcq"))
            if mcq_range:
                for q in range(mcq_range[0], mcq_range[1] + 1):
                    key = str(q)
                    normalized[key] = self._normalize_mcq_answer(keyed_answers.get(key), options)
                    covered.add(key)

            for q_type, q_range in format.question_ranges.items():
                if q_type in {"mcq", "drawing"}:
                    continue
                normalized_range = self._normalize_range(q_range)
                if not normalized_range:
                    continue
                for q in range(normalized_range[0], normalized_range[1] + 1):
                    key = str(q)
                    normalized[key] = self._normalize_open_answer(keyed_answers.get(key))
                    covered.add(key)

            drawing_range = self._normalize_range(format.question_ranges.get("drawing"))
            if drawing_range:
                for q in range(drawing_range[0], drawing_range[1] + 1):
                    key = str(q)
                    normalized[key] = self._normalize_drawing_answer(keyed_answers.get(key))
                    covered.add(key)

        for key, raw_value in keyed_answers.items():
            if key in covered:
                continue
            if self._looks_like_mcq_answer(raw_value, options):
                normalized[key] = self._normalize_mcq_answer(raw_value, options)
            else:
                normalized[key] = self._normalize_open_answer(raw_value)

        return dict(
            sorted(
                normalized.items(),
                key=lambda item: (0, int(item[0])) if item[0].isdigit() else (1, item[0])
            )
        )

    def _data_to_extraction(
        self,
        data: Dict,
        format: Optional[ExamFormat] = None,
        extraction_method: str = "llm_full"
    ) -> CandidateExtraction:
        """Convert raw dict to CandidateExtraction."""
        aliases = {
            "candidate_name": ["candidate_name", "name", "student_name"],
            "candidate_number": ["candidate_number", "candidate_id", "candidate_no", "student_number", "id"],
            "country": ["country", "centre", "center"],
            "paper_type": ["paper_type", "paper"],
        }

        def first_non_empty(source: Dict[str, Any], keys: List[str]) -> Optional[str]:
            for key in keys:
                value = source.get(key)
                if value is not None and str(value).strip() != "":
                    return str(value).strip()
            return None

        candidate_name = first_non_empty(data, aliases["candidate_name"])
        candidate_number = first_non_empty(data, aliases["candidate_number"])
        country = first_non_empty(data, aliases["country"])
        paper_type = first_non_empty(data, aliases["paper_type"])

        known_keys = {
            "answers",
            *aliases["candidate_name"],
            *aliases["candidate_number"],
            *aliases["country"],
            *aliases["paper_type"],
        }
        extra_fields = {
            str(k): v for k, v in data.items()
            if k not in known_keys and v is not None
        }

        return CandidateExtraction(
            page_number=0,
            candidate_name=candidate_name,
            candidate_number=candidate_number,
            country=country,
            paper_type=paper_type,
            extra_fields=extra_fields,
            answers=self._normalize_answers(data.get("answers", {}), format),
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
        gemini_model: Optional[str] = None,
        max_workers: int = 3
    ):
        self.model, self.model_name = create_gemini_model(
            api_key=gemini_api_key,
            preferred_model=gemini_model,
        )

        self.page_analyzer = PageAnalyzer()
        self.format_detector = FormatDetector(self.model)
        self.answer_classifier = AnswerClassifier(self.page_analyzer)
        self.extractor = OptimizedExtractor(self.model, max_workers)
        self.max_workers = max_workers
        logger.info(
            "RefactoredPipeline initialized | model=%s | workers=%d",
            self.model_name,
            self.max_workers,
        )

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
        logger.info(f"Starting optimized pipeline with {total_pages} pages")

        # Stage 2-3: Analyze all pages
        if progress_callback:
            progress_callback("Analyzing page layouts...", 0, total_pages)

        layouts = []
        for i, img in enumerate(images):
            layout = self.page_analyzer.analyze_page(img, i + 1)
            layouts.append(layout)
            logger.debug(f"Page {i + 1}: blank={layout.is_blank}, hash={layout.layout_hash}")
            if progress_callback:
                progress_callback(f"Analyzed page {i+1}", i + 1, total_pages)

        # Filter non-blank pages
        valid_layouts = [l for l in layouts if not l.is_blank]
        logger.info(f"Found {len(valid_layouts)} non-blank pages out of {total_pages}")

        if len(valid_layouts) == 0:
            logger.warning("All pages detected as blank! Returning empty results.")
            return results

        # Stage 3: Cluster layouts
        from backend.services.page_analyzer import LayoutClusterer
        clusterer = LayoutClusterer()
        clusters = clusterer.cluster_layouts(valid_layouts)
        logger.info(f"Detected {len(clusters)} unique exam formats: {list(clusters.keys())}")

        # Stage 4: Detect format per cluster (ONE LLM call per unique layout)
        formats: Dict[str, ExamFormat] = {}
        for layout_hash, page_nums in clusters.items():
            rep_page = clusterer.get_representative_page(page_nums, layouts)
            rep_image = images[rep_page - 1]
            rep_layout = layouts[rep_page - 1]

            logger.info(f"Detecting format for cluster {layout_hash} using page {rep_page}")
            exam_format = self.format_detector.detect_format(rep_image, rep_layout)
            formats[layout_hash] = exam_format
            logger.info(f"Format {layout_hash}: {exam_format.description}, mcq={exam_format.question_ranges.get('mcq')}")

        # Stage 5-7: Extract per page
        if progress_callback:
            progress_callback("Extracting answers...", 0, len(valid_layouts))

        # Process pages in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for layout in valid_layouts:
                img = images[layout.page_number - 1]
                exam_format = formats.get(layout.format_group)
                if exam_format is None:
                    logger.warning(f"No format for page {layout.page_number}, format_group={layout.format_group}")
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
                    logger.info(f"Page {page_num} extracted: {len(extraction.answers)} answers, method={extraction.extraction_method}")
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
        logger.info(f"Pipeline complete: {len(results)} candidates extracted")
        return results

    def _extract_page(
        self,
        image: Image.Image,
        layout: PageLayout,
        format: ExamFormat
    ) -> CandidateExtraction:
        """Extract data from a single page."""
        # Handle case where format is None (defensive)
        if format is None:
            logger.warning(f"No format detected for page {layout.page_number}, using fallback")
            format = self.format_detector._create_fallback_format(layout.layout_hash or "unknown")

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

        # Need LLM extraction (with high-confidence CV seeds for MCQ where available)
        return self.extractor.extract_full_page(
            image,
            format,
            cv_seed_answers=cv_answers,
        )

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
