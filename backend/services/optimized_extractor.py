"""
Integration layer connecting the refactored pipeline with existing API routes.
Provides backwards-compatible interface while using optimized internals.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import asdict
from pathlib import Path

from backend.config import get_settings
from backend.services.extraction_pipeline import (
    RefactoredPipeline,
    CandidateExtraction,
    ExamFormat
)
from backend.services.page_analyzer import PageAnalyzer, PageLayout
from backend.services.image_preprocessor import ImagePreprocessor
from backend.services.mcq_extractor import auto_extract_mcq
from backend.services.template_service import get_template_registry

logger = logging.getLogger(__name__)
settings = get_settings()


class OptimizedAIExtractor:
    """
    Drop-in replacement for AIExtractor using the refactored pipeline.
    Maintains the same interface for backwards compatibility.
    """

    def __init__(
        self,
        api_key: str = None,
        model_name: str = None,
        use_parallel: bool = True,
        max_workers: int = 3,
        mcq_template_id: Optional[str] = None,
    ):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model_name or settings.gemini_model
        self.use_parallel = use_parallel
        self.max_workers = max_workers
        self._preprocess_enabled = bool(settings.enable_image_preprocessing)
        self._preprocess_mode = settings.preprocessing_mode
        self._image_preprocessor = ImagePreprocessor()
        self._mcq_template_id = mcq_template_id

        # Initialize refactored pipeline
        self.pipeline = RefactoredPipeline(
            gemini_api_key=self.api_key,
            gemini_model=self.model_name,
            max_workers=max_workers
        )
        self.model_name = self.pipeline.model_name

        # Format cache (persists across extractions)
        self._format_cache: Dict[str, ExamFormat] = {}
        logger.info(
            "OptimizedAIExtractor initialized | model=%s | preprocessing=%s(%s) | mcq_template=%s",
            self.model_name,
            self._preprocess_enabled,
            self._preprocess_mode,
            self._mcq_template_id or "auto-detect",
        )

    def _load_image(self, image_path: str):
        from PIL import Image

        with Image.open(image_path) as raw:
            image = raw.convert("RGB")
        if not self._preprocess_enabled:
            return image
        return self._image_preprocessor.preprocess_pil_image(
            image,
            mode=self._preprocess_mode,
        )

    def analyze_format(self, image_path: str) -> Dict[str, Any]:
        """
        Analyze exam format from a single image.
        Backwards compatible with existing AIExtractor.analyze_format()
        """
        try:
            image = self._load_image(image_path)
        except Exception as e:
            logger.error(f"Failed to load image {image_path}: {e}")
            # Return default format on error
            return {
                "header_fields": [
                    {"key": "candidate_number", "label": "Candidate Number"},
                    {"key": "candidate_name", "label": "Candidate Name"}
                ],
                "mcq_range": "1-30",
                "drawing_range": None,
                "answer_options": ["A", "B", "C", "D"],
                "total_questions": 30,
                "description": "Default format (image load failed)"
            }

        analyzer = PageAnalyzer()
        layout = analyzer.analyze_page(image, page_number=1)

        exam_format = self.pipeline.format_detector.detect_format(image, layout)

        # Convert to legacy format
        return {
            "header_fields": exam_format.header_fields,
            "mcq_range": self._range_to_string(exam_format.question_ranges.get("mcq")),
            "drawing_range": self._range_to_string(exam_format.question_ranges.get("drawing")),
            "answer_options": exam_format.answer_options,
            "total_questions": exam_format.total_questions,
            "description": exam_format.description
        }

    def _range_to_string(self, range_tuple: Optional[tuple]) -> Optional[str]:
        """Convert (1, 30) to '1-30'"""
        if range_tuple:
            return f"{range_tuple[0]}-{range_tuple[1]}"
        return None

    def extract_from_image(
        self,
        image_path: str,
        extraction_prompt: Optional[str] = None,
        use_examples: bool = True
    ) -> Dict[str, Any]:
        """
        Extract data from a single image.
        Backwards compatible with existing AIExtractor.extract_from_image()
        """
        try:
            image = self._load_image(image_path)
        except Exception as e:
            logger.error(f"Failed to load image {image_path}: {e}")
            return {"error": str(e), "answers": {}, "drawing_questions": {}}

        # Analyze layout
        analyzer = PageAnalyzer()
        layout = analyzer.analyze_page(image, page_number=1)

        if layout.is_blank:
            return self._empty_result(1)

        # Detect format and extract
        exam_format = self.pipeline.format_detector.detect_format(image, layout)
        extraction = self.pipeline._extract_page(image, layout, exam_format)
        extraction.page_number = 1

        return self._extraction_to_dict(extraction)

    def extract_from_multiple_images(
        self,
        image_paths: List[str],
        extraction_prompt: Optional[str] = None,
        submission_id: Optional[int] = None,
        db=None,
        use_parallel: bool = True,
        max_workers: int = 4,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extract data from multiple images.
        Backwards compatible with existing AIExtractor.extract_from_multiple_images()

        Args:
            image_paths: List of paths to image files
            extraction_prompt: Ignored (kept for compatibility)
            submission_id: Optional submission ID for logging
            db: Optional database session for logging
            use_parallel: Whether to use parallel processing
            max_workers: Number of parallel workers
            filename: Original upload filename (used for MCQ template detection)
        """
        import time

        start_time = time.time()

        # Load and preprocess images in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed
        images_ordered = [None] * len(image_paths)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(self._load_image, path): i
                for i, path in enumerate(image_paths)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    images_ordered[idx] = future.result()
                except Exception as e:
                    logger.error(f"Failed to load image {image_paths[idx]}: {e}")
        images = [img for img in images_ordered if img is not None]

        if not images:
            return {
                "candidates": [],
                "pages_processed": 0,
                "pages_with_data": 0,
                "processing_time": 0,
                "errors": ["No valid images loaded"]
            }

        def progress_callback(message: str, current: int, total: int):
            # Log to database if session provided
            if db and submission_id:
                self._log_progress(db, submission_id, message, current, total)

        # Use the pipeline
        extractions = self.pipeline.process_images(images, progress_callback)

        # Convert to legacy format
        candidates = []
        for ext in extractions:
            candidates.append(self._extraction_to_dict(ext))

        self._apply_mcq_overrides(candidates, image_paths, filename=filename)
        self._apply_section_overrides(candidates, image_paths)

        elapsed_time = time.time() - start_time
        pages_with_data = sum(1 for c in candidates if c.get("answers"))

        return {
            "candidates": candidates,
            "pages_processed": len(image_paths),
            "pages_with_data": pages_with_data,
            "processing_time": round(elapsed_time, 2),
        }

    def _apply_mcq_overrides(
        self,
        candidates: List[Dict[str, Any]],
        image_paths: List[str],
        filename: Optional[str] = None,
    ) -> None:
        """
        Override MCQ answers using template-based CV bubble extraction.

        Auto-detects the exam template from the first page image (or uses
        the explicit mcq_template_id if set). If a matching template with
        MCQ sections is found, its CV-extracted answers overwrite the
        LLM-extracted ones for higher accuracy on bubble sheets.
        """
        if not image_paths:
            return

        try:
            answers_by_page, diagnostics, detected_id = auto_extract_mcq(
                image_paths=image_paths,
                filename=filename,
                template_id=self._mcq_template_id,
            )
        except Exception as exc:
            logger.warning("MCQ template extractor failed: %s", exc)
            return

        if not answers_by_page:
            if detected_id:
                logger.info(
                    "MCQ extractor (template=%s) produced no accepted pages. diagnostics=%s",
                    detected_id, diagnostics,
                )
            return

        applied_pages = 0
        applied_answers = 0
        for candidate in candidates:
            page_no_raw = candidate.get("page_number")
            try:
                page_no = int(page_no_raw)
            except (TypeError, ValueError):
                continue

            page_answers = answers_by_page.get(page_no)
            if not page_answers:
                continue

            answers = candidate.setdefault("answers", {})
            for question, answer in page_answers.items():
                answers[str(question)] = str(answer).strip().upper()
            applied_pages += 1
            applied_answers += len(page_answers)

        logger.info(
            "Applied MCQ CV overrides: template=%s pages=%d answers=%d accepted_pages=%s",
            detected_id,
            applied_pages,
            applied_answers,
            sorted(list(answers_by_page.keys())),
        )

    def _apply_section_overrides(
        self,
        candidates: List[Dict[str, Any]],
        image_paths: List[str],
    ) -> None:
        """
        Process diagram questions using Mathpix OCR.

        For sections with diagram question_overrides, crops the diagram
        region and sends it to Mathpix for recognition. Only handles
        diagram-type questions — numeric_grid and open_response are
        already handled by the Gemini pipeline.
        """
        if not self._mcq_template_id or not image_paths:
            return

        registry = get_template_registry()
        template = registry.get(self._mcq_template_id)
        if not template:
            return

        # Only process sections that have diagram overrides
        diagram_sections = [
            s for s in template.sections
            if s.question_overrides and any(
                ov.type == "diagram" for ov in s.question_overrides.values()
            )
        ]
        if not diagram_sections:
            return

        has_mathpix = bool(settings.mathpix_app_id and settings.mathpix_app_key)
        if not has_mathpix:
            logger.info("Mathpix not configured, skipping diagram extraction")
            return

        logger.info("Using Mathpix OCR for diagram questions")

        import cv2
        from backend.services.mcq_extractor import _match_anchor
        from backend.services.mathpix_client import ocr_image

        # Build anchor from first page if needed
        if template.anchor.region.w > 0 and template.id not in registry._anchor_images:
            first_img = cv2.imread(str(image_paths[0]))
            if first_img is not None:
                r = template.anchor.region
                h, w = first_img.shape[:2]
                if r.y + r.h <= h and r.x + r.w <= w:
                    anchor = first_img[r.y : r.y + r.h, r.x : r.x + r.w].copy()
                    registry._anchor_images[template.id] = anchor

        applied_total = 0
        for page_idx, image_path in enumerate(image_paths):
            page_num = page_idx + 1
            img = cv2.imread(str(image_path))
            if img is None:
                continue

            candidate = None
            for c in candidates:
                try:
                    if int(c.get("page_number", 0)) == page_num:
                        candidate = c
                        break
                except (TypeError, ValueError):
                    continue
            if candidate is None:
                continue

            score, dx, dy, _ = _match_anchor(img, template, registry)
            if score < template.anchor.min_match_score:
                continue

            answers = candidate.setdefault("answers", {})
            drawing_questions = candidate.setdefault("drawing_questions", {})

            for section in diagram_sections:
                for q_num, override in section.question_overrides.items():
                    if override.type != "diagram":
                        continue
                    if not override.region or override.region.w == 0:
                        continue

                    try:
                        r = override.region
                        x, y = r.x + dx, r.y + dy
                        img_h, img_w = img.shape[:2]
                        x = max(0, min(x, img_w - 1))
                        y = max(0, min(y, img_h - 1))
                        w = min(r.w, img_w - x)
                        h = min(r.h, img_h - y)

                        if w <= 0 or h <= 0:
                            continue

                        crop = img[y : y + h, x : x + w]
                        result = ocr_image(crop, include_line_data=True)

                        text = result.get("text", "").strip()
                        prompt_hint = override.prompt_hint or ""

                        answers[str(q_num)] = text if text else "DR"
                        drawing_questions[str(q_num)] = text if text else prompt_hint
                        applied_total += 1

                        logger.debug(
                            "Diagram Q%d: mathpix_text=%r confidence=%.2f",
                            q_num, text[:50], result.get("confidence", 0),
                        )
                    except Exception as exc:
                        logger.warning("Diagram extraction failed Q%d page %d: %s", q_num, page_num, exc)

        logger.info(
            "Applied diagram overrides via Mathpix: template=%s diagrams=%d",
            self._mcq_template_id, applied_total,
        )

    def validate_extraction(self, extraction_result: Dict) -> Dict:
        """
        Validate extracted data for common issues.
        Backwards compatible with AIExtractor.validate_extraction()
        """
        warnings: List[str] = []
        candidates = extraction_result.get("candidates", [])
        valid_answers = {"A", "B", "C", "D", "E", "BL", "IN", "DR"}

        total_answers = 0
        total_drawing = 0

        for i, candidate in enumerate(candidates):
            answers = candidate.get("answers", {})
            drawing = candidate.get("drawing_questions", {})
            total_answers += len(answers)
            total_drawing += len(drawing)

            for q_num, answer in answers.items():
                normalized = str(answer).strip().upper() if answer is not None else ""
                if normalized in valid_answers:
                    continue
                # Allow numeric/text free-response answers in the shared answers map.
                if normalized:
                    continue
                if answer not in valid_answers:
                    warnings.append(
                        f"Page {candidate.get('page_number', i + 1)}: "
                        f"Unexpected answer '{answer}' for Q{q_num}"
                    )

        logger.info(
            "Validation: %d candidates, %d answers, %d drawing, %d warnings",
            len(candidates), total_answers, total_drawing, len(warnings)
        )

        return {
            "is_valid": len(warnings) == 0,
            "warnings": warnings,
            "total_candidates": len(candidates),
            "total_answers": total_answers,
            "total_drawing_questions": total_drawing,
        }

    def _extraction_to_dict(self, extraction: CandidateExtraction) -> Dict[str, Any]:
        """Convert CandidateExtraction to legacy dict format."""
        normalized_answers: Dict[str, Any] = {}
        drawing_questions: Dict[str, Any] = {}

        for q, response in (extraction.drawing_questions or {}).items():
            q_key = str(q)
            value = str(response).strip() if response is not None else ""
            if value:
                drawing_questions[q_key] = value

        for q, answer in (extraction.answers or {}).items():
            q_key = str(q)
            value = str(answer).strip() if answer is not None else ""

            if not value:
                normalized_answers[q_key] = "BL"
                continue

            upper = value.upper()
            if upper == "DR":
                # Keep DR in answers and mirror to drawing_questions for compatibility.
                normalized_answers[q_key] = "DR"
                drawing_questions.setdefault(q_key, "DR")
            elif upper in {"A", "B", "C", "D", "E", "BL", "IN"}:
                normalized_answers[q_key] = upper
            else:
                # Preserve numeric/text responses as-is (do not collapse to DR).
                normalized_answers[q_key] = value

        # Ensure any extracted drawing responses are mirrored in answers.
        for q_key, response in drawing_questions.items():
            if q_key not in normalized_answers or not str(normalized_answers[q_key]).strip():
                normalized_answers[q_key] = response

        result = {
            "page_number": extraction.page_number,
            "candidate_name": extraction.candidate_name,
            "candidate_number": extraction.candidate_number,
            "country": extraction.country,
            "paper_type": extraction.paper_type,
            "answers": normalized_answers,
            "drawing_questions": drawing_questions,
            "extra_fields": extraction.extra_fields,
            "confidence": extraction.confidence
        }

        return result

    def _dict_to_format(self, format_dict: Dict, format_id: str) -> ExamFormat:
        """Convert legacy format dict to ExamFormat."""
        question_ranges = {}

        if format_dict.get("mcq_range"):
            mcq = format_dict["mcq_range"]
            if isinstance(mcq, str) and "-" in mcq:
                parts = mcq.split("-")
                question_ranges["mcq"] = (int(parts[0]), int(parts[1]))
            elif isinstance(mcq, dict):
                question_ranges["mcq"] = (mcq["start"], mcq["end"])

        if format_dict.get("drawing_range"):
            dr = format_dict["drawing_range"]
            if isinstance(dr, str) and "-" in dr:
                parts = dr.split("-")
                question_ranges["drawing"] = (int(parts[0]), int(parts[1]))
            elif isinstance(dr, dict):
                question_ranges["drawing"] = (dr["start"], dr["end"])

        return ExamFormat(
            format_id=format_id,
            header_fields=format_dict.get("header_fields", []),
            question_ranges=question_ranges,
            answer_options=format_dict.get("answer_options", ["A", "B", "C", "D"]),
            total_questions=format_dict.get("total_questions", 30),
            description=format_dict.get("description", "")
        )

    def _empty_result(self, page_number: int) -> Dict[str, Any]:
        """Return empty result for blank pages."""
        return {
            "page_number": page_number,
            "candidate_name": None,
            "candidate_number": None,
            "country": None,
            "paper_type": None,
            "answers": {},
            "drawing_questions": {},
            "extra_fields": {},
            "confidence": 0.0,
            "is_blank": True
        }

    def _log_progress(
        self,
        db_session,
        submission_id: int,
        message: str,
        current: int,
        total: int
    ):
        """Log progress to database."""
        from backend.db.models import ProcessingLog
        try:
            log = ProcessingLog(
                submission_id=submission_id,
                action="page_progress",
                status="info",
                message=message,
                extra_data={"current": current, "total": total, "progress": current / total if total > 0 else 0}
            )
            db_session.add(log)
            db_session.commit()
        except Exception as e:
            logger.warning(f"Failed to log progress: {e}")


# Factory function for easy switching
def get_extractor(optimized: bool = True, **kwargs) -> Any:
    """
    Get an extractor instance.

    Args:
        optimized: If True, returns OptimizedAIExtractor (new pipeline)
                   If False, returns legacy AIExtractor

    Returns:
        Extractor instance
    """
    if optimized:
        return OptimizedAIExtractor(**kwargs)
    else:
        from backend.services.ai_extractor import AIExtractor
        return AIExtractor(**kwargs)


# Singleton instance for reuse across requests
_default_extractor = None


def get_default_extractor() -> OptimizedAIExtractor:
    """Get or create the default optimized extractor."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = OptimizedAIExtractor()
    return _default_extractor
