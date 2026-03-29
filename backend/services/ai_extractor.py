"""
AI Extractor Service
Uses Google Gemini Vision API for intelligent answer extraction from exam sheets.
"""
import google.generativeai as genai
from typing import List, Dict, Optional
import logging
import json
import re
import time
import os
from PIL import Image
import hashlib

from backend.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_FORMAT = {
    "header_fields": [
        {"key": "candidate_name", "label": "Candidate Name"},
        {"key": "candidate_number", "label": "Candidate Number"},
        {"key": "country", "label": "Country"},
        {"key": "paper_type", "label": "Paper Type"},
    ],
    "mcq_range": "1-30",
    "drawing_range": "31-35",
    "answer_options": ["A", "B", "C", "D", "E"],
    "description": "Standard bubble-sheet with MCQ + drawing questions",
}


class AIExtractor:
    """AI-powered extractor using Google Gemini Vision API with dynamic format detection"""

    def __init__(self, template_dir: Optional[str] = None):
        settings = get_settings()
        # NOTE: google.generativeai is deprecated but still functional.
        # The module API is not fully typed so we ignore type checks here.
        genai.configure(api_key=settings.gemini_api_key)  # type: ignore[attr-defined]
        self.model = genai.GenerativeModel(settings.gemini_model)  # type: ignore[attr-defined]
        self.template_dir = template_dir or os.path.join(
            os.path.dirname(__file__), "templates"
        )
        self._format_cache: Dict[str, Dict] = {}
        self._prompt_cache: Dict[str, str] = {}
        self._use_prompt_cache = settings.cache_page_prompts
        self._page_hash_size = settings.page_hash_size
        logger.info("AIExtractor initialized | model=%s", settings.gemini_model)

    def _compute_image_hash(self, image_path: str, size: Optional[int] = None) -> str:
        """Compute a lightweight hash of an image for layout similarity checks."""

        try:
            with Image.open(image_path) as img:
                # Convert to grayscale and resize to fixed small dimensions
                size = size or self._page_hash_size
                img = img.convert("L").resize((size, size))
                pixels = list(img.getdata())
                avg = sum(pixels) / len(pixels)
                bits = ''.join('1' if px > avg else '0' for px in pixels)
                h = hashlib.sha256(bits.encode('utf-8')).hexdigest()
                return h
        except Exception as e:
            logger.warning("Failed to compute image hash for %s: %s", image_path, e)
            return ""

    # ------------------------------------------------------------------
    # 1.  FORMAT ANALYSIS
    # ------------------------------------------------------------------
    def analyze_format(self, image_path: str) -> Dict:
        """Send the first page to Gemini and ask it to describe the exam format."""
        try:
            stat = os.stat(image_path)
            cache_key = f"{os.path.abspath(image_path)}::{stat.st_mtime_ns}::{stat.st_size}"
        except Exception:
            cache_key = os.path.abspath(image_path)

        if cache_key in self._format_cache:
            logger.info("Using cached format descriptor")
            return self._format_cache[cache_key]

        prompt = (
            "You are an expert at analysing exam / answer-sheet images.\n"
            "Look at this image carefully and describe its structure so that another AI\n"
            "can later extract data from every page of the same format.\n\n"
            "Return ONLY valid JSON with these keys:\n\n"
            "{\n"
            '  "header_fields": [\n'
            '    {"key": "<snake_case_field_name>", "label": "<Human-readable label as printed on the sheet>"}\n'
            "  ],\n"
            '  "mcq_range": "<first_q>-<last_q> or null if no MCQ section",\n'
            '  "drawing_range": "<first_q>-<last_q> or null if no drawing/free-response section",\n'
            '  "answer_options": ["A","B","C","D"] or whatever options appear,\n'
            '  "description": "One-line human-readable description of the sheet layout"\n'
            "}\n\n"
            "Rules:\n"
            "- header_fields: list ALL metadata fields printed on the sheet (name, number,\n"
            "  country, date, zone, subject, session, paper, level, etc.). Use snake_case\n"
            "  for the key. Keep the label exactly as printed.\n"
            "- mcq_range: the range of question numbers that are multiple-choice bubbles.\n"
            '  Use "start-end" format. If there is no MCQ section write null.\n'
            "- drawing_range: the range of questions that require a written / drawn response.\n"
            "  If none, write null.\n"
            "- answer_options: the letters shown for MCQ choices.\n"
            "- description: a short summary of the layout.\n\n"
            "Return ONLY the JSON object, nothing else."
        )

        try:
            image = Image.open(image_path)
            logger.info("Analyzing format | path=%s | size=%s | mode=%s", image_path, image.size, image.mode)
            response = self.model.generate_content([prompt, image])
            content = response.text.strip()
            logger.debug("Format analysis raw response (%d chars): %s", len(content), content[:500])

            try:
                fmt = json.loads(content)
            except json.JSONDecodeError:
                m = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
                if m:
                    fmt = json.loads(m.group(1))
                else:
                    logger.warning("Format analysis unparseable, using default. Response: %s", content[:300])
                    return dict(DEFAULT_FORMAT)

            if "header_fields" not in fmt or not isinstance(fmt["header_fields"], list):
                logger.warning("Format analysis missing header_fields, using default")
                return dict(DEFAULT_FORMAT)

            logger.info(
                "Format detected: %s | headers=%s | mcq=%s | drawing=%s | options=%s",
                fmt.get("description", "?"),
                [f["key"] for f in fmt["header_fields"]],
                fmt.get("mcq_range"),
                fmt.get("drawing_range"),
                fmt.get("answer_options"),
            )
            self._format_cache[cache_key] = fmt
            return fmt

        except Exception as e:
            logger.error("Format analysis failed: %s: %s", type(e).__name__, e)
            return dict(DEFAULT_FORMAT)

    # ------------------------------------------------------------------
    # 2.  BUILD DYNAMIC EXTRACTION PROMPT
    # ------------------------------------------------------------------
    @staticmethod
    def build_extraction_prompt(fmt: Dict) -> str:
        """Build a page-level extraction prompt from a format descriptor."""
        header_json_lines = []
        for f in fmt.get("header_fields", []):
            header_json_lines.append(f'  "{f["key"]}": "<{f["label"]}>"')
        header_json = ",\n".join(header_json_lines)

        mcq_section = ""
        mcq_example = ""
        options = fmt.get("answer_options") or []
        mcq_range = fmt.get("mcq_range")

        if mcq_range and options:
            mcq_section = (
                f"\nMCQ QUESTIONS ({mcq_range}):\n"
                f"- Answer options are: {', '.join(options)}\n"
                f"- For a clearly marked single answer use the letter (e.g. {options[0]})\n"
                '- For a blank/unanswered question use "BL"\n'
                '- For an invalid answer (two or more bubbles marked) use "IN"\n'
            )
            mcq_example = (
                '  "answers": {\n'
                f'    "1": "{options[0]}",\n'
                '    "2": "BL",\n'
                '    "3": "IN"\n'
                "  }"
            )
        elif mcq_range:
            mcq_section = (
                f"\nSHORT-ANSWER QUESTIONS ({mcq_range}):\n"
                "- Each question has a boxed area; read the handwritten/typed answer.\n"
                '- Return the text exactly (e.g. "34", "Wednesday", "12:35").\n'
                '- If empty, use "BL".\n'
            )
            mcq_example = '  "answers": {\n    "1": "34",\n    "2": "BL",\n    "3": "41"\n  }'
        else:
            mcq_example = '  "answers": {}'

        drawing_section = ""
        drawing_example = ""
        drawing_range = fmt.get("drawing_range")
        if drawing_range:
            drawing_section = (
                f"\nFREE-RESPONSE / DRAWING QUESTIONS ({drawing_range}):\n"
                "- Transcribe the student's written answer exactly\n"
                '- If blank, use empty string ""\n'
            )
            start_q = drawing_range.split("-")[0]
            drawing_example = f'  "drawing_questions": {{\n    "{start_q}": "student written answer"\n  }}'
        else:
            drawing_example = '  "drawing_questions": {}'

        header_labels = "\n".join("- " + f["label"] for f in fmt.get("header_fields", []))

        prompt = (
            "You are analyzing an exam answer sheet. Extract ALL data with high accuracy.\n\n"
            "HEADER / METADATA FIELDS (at the top of the page):\n"
            f"{header_labels}\n"
            "- Read candidate identifiers carefully (handwriting and boxed digits).\n"
            f"{mcq_section}{drawing_section}\n"
            "Return ONLY valid JSON in this exact format:\n"
            "{\n"
            f"{header_json},\n"
            f"{mcq_example},\n"
            f"{drawing_example}\n"
            "}\n\n"
            "IMPORTANT:\n"
            "- Return ONLY the JSON object, no extra text.\n"
            '- For every MCQ question in the range, include an entry. Use "BL" if blank.\n'
            '- Use "IN" for invalid (multiple marks).\n'
            "- Pay close attention to which bubble is filled/marked; ignore faint marks.\n"
            "- If a header field is not visible, return an empty string.\n"
        )
        logger.debug("Built extraction prompt (%d chars)", len(prompt))
        return prompt

    # ------------------------------------------------------------------
    # 3.  SINGLE-PAGE EXTRACTION
    # ------------------------------------------------------------------
    def extract_from_image(self, image_path: str, extraction_prompt: Optional[str] = None, use_examples: bool = True) -> Dict:
        """Extract data from a single exam-sheet image."""
        try:
            if extraction_prompt is None:
                extraction_prompt = self.build_extraction_prompt(DEFAULT_FORMAT)

            image = Image.open(image_path)
            logger.info("Sending to Gemini: %s (%dx%d)", os.path.basename(image_path), image.size[0], image.size[1])

            response = self.model.generate_content([extraction_prompt, image])
            content = response.text
            logger.debug("Gemini response (%d chars): %s", len(content), content[:400])

            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                m = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
                if m:
                    result = json.loads(m.group(1))
                else:
                    logger.error("JSON parse failed for %s. Raw: %s", os.path.basename(image_path), content[:300])
                    result = {}

            n_answers = len(result.get("answers", {}))
            n_drawing = len(result.get("drawing_questions", {}))
            header_keys = [k for k in result.keys() if k not in ("answers", "drawing_questions")]
            logger.info("Extracted from %s: %d answers, %d drawing, headers=%s", os.path.basename(image_path), n_answers, n_drawing, header_keys)
            return result

        except Exception as e:
            logger.error("Extraction failed for %s: %s: %s", os.path.basename(image_path), type(e).__name__, e)
            return {"error": str(e), "answers": {}, "drawing_questions": {}}

    # ------------------------------------------------------------------
    # 4.  SINGLE-PAGE WITH RETRY
    # ------------------------------------------------------------------
    def _process_single_page(self, image_path: str, page_num: int, extraction_prompt: Optional[str] = None, max_retries: int = 3) -> Dict:
        """Process a single page with retry logic."""
        for attempt in range(max_retries):
            try:
                logger.info("Page %d - attempt %d/%d", page_num, attempt + 1, max_retries)

                # If no prompt is provided, analyze this page's format dynamically.
                if extraction_prompt is None:
                    fmt = self.analyze_format(image_path)
                    extraction_prompt = self.build_extraction_prompt(fmt)

                result = self.extract_from_image(image_path, extraction_prompt)
                result["page_num"] = page_num
                result["image_path"] = image_path

                # Record how many drawing questions were detected, before normalizing.
                result["_drawing_count"] = len(result.get("drawing_questions", {}) or {})

                # Normalize drawing questions: mark them as DR instead of trying to interpret.
                drawing = result.get("drawing_questions", {}) or {}
                if drawing:
                    answers = result.setdefault("answers", {})
                    for q in drawing.keys():
                        answers[str(q)] = "DR"
                    result["drawing_questions"] = {}

                n_answers = len(result.get("answers", {}))
                n_drawing = len(result.get("drawing_questions", {}))
                has_error = "error" in result

                if has_error:
                    logger.warning("Page %d returned error: %s", page_num, result["error"])
                elif n_answers == 0 and n_drawing == 0:
                    logger.warning("Page %d returned 0 answers and 0 drawing", page_num)
                else:
                    logger.info("Page %d OK - %d answers, %d drawing", page_num, n_answers, n_drawing)
                return result

            except Exception as e:
                logger.warning("Page %d attempt %d error: %s: %s", page_num, attempt + 1, type(e).__name__, e)
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.info("Retrying page %d in %ds ...", page_num, wait_time)
                    time.sleep(wait_time)
                else:
                    logger.error("All %d attempts failed for page %d: %s", max_retries, page_num, e)
                    return {"page_num": page_num, "error": str(e), "answers": {}, "drawing_questions": {}}

        return {"page_num": page_num, "error": "Max retries exhausted", "answers": {}, "drawing_questions": {}}

    # ------------------------------------------------------------------
    # 5.  MULTI-PAGE EXTRACTION
    # ------------------------------------------------------------------
    def extract_from_multiple_images(self, image_paths: List[str], extraction_prompt: Optional[str] = None, submission_id: Optional[int] = None, db=None, use_parallel: bool = True, max_workers: int = 4) -> Dict:
        """Extract data from multiple exam-sheet images with dynamic format detection."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        candidates: List[Dict] = []
        errors: List[str] = []
        detected_format: Optional[Dict] = None
        start_time = time.time()

        logger.info("Starting extraction: %d pages, parallel=%s, workers=%d", len(image_paths), use_parallel, max_workers)

        def _handle_result(result: Dict, page_num: int):
            if "error" in result:
                errors.append(f"Page {page_num}: {result['error']}")

            # Keep output minimal and consistent across pages regardless of format.
            candidate_number = (
                result.get("candidate_number")
                or result.get("candidate_id")
                or result.get("id")
                or ""
            )
            paper_type = result.get("paper_type") or result.get("paper") or ""

            candidate: Dict = {
                "page_number": page_num,
                "candidate_number": str(candidate_number) if candidate_number is not None else "",
                "paper_type": str(paper_type) if paper_type is not None else "",
                "answers": result.get("answers", {}),
            }
            candidates.append(candidate)

            if db is not None and submission_id is not None:
                from backend.db.models import ProcessingLog
                label = str(result.get("candidate_number") or result.get("candidate_id") or "")
                n_ans = len(result.get("answers", {}))
                n_draw = int(result.get("_drawing_count", 0))
                db.add(ProcessingLog(
                    submission_id=submission_id,
                    action="page_progress",
                    status="info",
                    message=f"Page {page_num}: {n_ans} answers, {n_draw} drawing",
                    extra_data={"page": page_num, "label": label, "answers_count": n_ans, "drawing_count": n_draw},
                ))
                db.commit()

        # Step 2: Extract pages
        logger.info("Step 2: Extracting %d pages ...", len(image_paths))
        if use_parallel and len(image_paths) > 1:
            effective_workers = max_workers
            if len(image_paths) > 20:
                effective_workers = min(max_workers, 2)
            logger.info("Parallel mode: %d pages, %d workers", len(image_paths), effective_workers)

            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                future_to_page = {
                    executor.submit(self._process_single_page, img, i, extraction_prompt): i
                    for i, img in enumerate(image_paths, start=1)
                }
                for future in as_completed(future_to_page):
                    page_num = future_to_page[future]
                    try:
                        _handle_result(future.result(), page_num)
                    except Exception as e:
                        logger.error("Future error page %d: %s: %s", page_num, type(e).__name__, e)
                        errors.append(f"Page {page_num}: {e}")
        else:
            logger.info("Sequential mode: %d pages", len(image_paths))
            for i, img in enumerate(image_paths, start=1):
                # Use a cached prompt when possible so similar pages don't re-run format analysis.
                prompt = extraction_prompt
                if prompt is None:
                    page_hash = self._compute_image_hash(img)
                    prompt = self._prompt_cache.get(page_hash)
                    if prompt is None:
                        fmt = self.analyze_format(img)
                        prompt = self.build_extraction_prompt(fmt)
                        self._prompt_cache[page_hash] = prompt

                result = self._process_single_page(img, i, prompt)
                _handle_result(result, i)

        # Step 3: Aggregate
        elapsed_time = time.time() - start_time
        candidates.sort(key=lambda x: x.get("page_number", 0))
        pages_with_data = sum(1 for c in candidates if c.get("answers"))

        logger.info(
            "EXTRACTION SUMMARY: pages=%d, with_data=%d, empty=%d, errors=%d, time=%.1fs (%.1fs/page)",
            len(image_paths), pages_with_data, len(image_paths) - pages_with_data,
            len(errors), elapsed_time, elapsed_time / max(len(image_paths), 1),
        )

        if pages_with_data == 0:
            logger.error("ZERO pages produced data! Check model, API key, and image quality.")
        elif pages_with_data < len(image_paths):
            logger.warning("%d pages had no data", len(image_paths) - pages_with_data)

        result = {
            "candidates": candidates,
            "pages_processed": len(image_paths),
            "pages_with_data": pages_with_data,
            "processing_time": round(elapsed_time, 2),
        }
        if errors:
            result["errors"] = errors
        return result

    # ------------------------------------------------------------------
    # 6.  VALIDATION
    # ------------------------------------------------------------------
    def validate_extraction(self, extraction_result: Dict) -> Dict:
        """Validate extracted data for common issues."""
        warnings: List[str] = []
        candidates = extraction_result.get("candidates", [])
        fmt = extraction_result.get("detected_format", DEFAULT_FORMAT)
        valid_answers = set(fmt.get("answer_options", ["A", "B", "C", "D", "E"])) | {"BL", "IN"}

        total_answers = 0
        total_drawing = 0

        for i, candidate in enumerate(candidates):
            answers = candidate.get("answers", {})
            drawing = candidate.get("drawing_questions", {})
            total_answers += len(answers)
            total_drawing += len(drawing)

            for q_num, answer in answers.items():
                if answer not in valid_answers:
                    warnings.append(f"Page {candidate.get('page_number', i + 1)}: Unexpected answer '{answer}' for Q{q_num}")

        logger.info("Validation: %d candidates, %d answers, %d drawing, %d warnings", len(candidates), total_answers, total_drawing, len(warnings))

        return {
            "is_valid": len(warnings) == 0,
            "warnings": warnings,
            "total_candidates": len(candidates),
            "total_answers": total_answers,
            "total_drawing_questions": total_drawing,
        }


def get_ai_extractor():
    """Factory function to create extractor instance.

    Returns OptimizedAIExtractor if use_optimized_pipeline=True in config,
    otherwise returns legacy AIExtractor.
    """
    settings = get_settings()

    if settings.use_optimized_pipeline:
        try:
            from backend.services.optimized_extractor import OptimizedAIExtractor
            logger.info("Using OPTIMIZED pipeline (CV preprocessing + token optimization)")
            return OptimizedAIExtractor(
                max_workers=settings.max_extraction_workers
            )
        except ImportError as e:
            logger.warning(f"Failed to import OptimizedAIExtractor, falling back to legacy: {e}")
            return AIExtractor()
    else:
        logger.info("Using LEGACY pipeline")
        return AIExtractor()
