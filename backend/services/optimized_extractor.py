"""
Integration layer connecting the refactored pipeline with existing API routes.
Provides backwards-compatible interface while using optimized internals.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import asdict

from backend.config import get_settings
from backend.services.extraction_pipeline import (
    RefactoredPipeline,
    CandidateExtraction,
    ExamFormat
)
from backend.services.page_analyzer import PageAnalyzer, PageLayout

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
        max_workers: int = 3
    ):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model_name or settings.gemini_model
        self.use_parallel = use_parallel
        self.max_workers = max_workers

        # Initialize refactored pipeline
        self.pipeline = RefactoredPipeline(
            gemini_api_key=self.api_key,
            gemini_model=self.model_name,
            max_workers=max_workers
        )

        # Format cache (persists across extractions)
        self._format_cache: Dict[str, ExamFormat] = {}

    def analyze_format(self, image_path: str) -> Dict[str, Any]:
        """
        Analyze exam format from a single image.
        Backwards compatible with existing AIExtractor.analyze_format()
        """
        from PIL import Image

        try:
            image = Image.open(image_path)
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
        from PIL import Image

        try:
            image = Image.open(image_path)
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
        max_workers: int = 4
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
        """
        from PIL import Image
        import time

        start_time = time.time()

        # Load images from paths
        images = []
        for path in image_paths:
            try:
                img = Image.open(path)
                images.append(img)
            except Exception as e:
                logger.error(f"Failed to load image {path}: {e}")
                continue

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

        elapsed_time = time.time() - start_time
        pages_with_data = sum(1 for c in candidates if c.get("answers"))

        return {
            "candidates": candidates,
            "pages_processed": len(image_paths),
            "pages_with_data": pages_with_data,
            "processing_time": round(elapsed_time, 2),
        }

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
        result = {
            "page_number": extraction.page_number,
            "candidate_name": extraction.candidate_name,
            "candidate_number": extraction.candidate_number,
            "country": extraction.country,
            "paper_type": extraction.paper_type,
            "answers": extraction.answers,
            "drawing_questions": {},  # Extracted from answers with "DR" values
            "extra_fields": extraction.extra_fields,
            "confidence": extraction.confidence
        }

        # Separate drawing questions
        for q, answer in extraction.answers.items():
            if answer == "DR" or (isinstance(answer, str) and len(answer) > 2):
                result["drawing_questions"][q] = answer
                result["answers"][q] = "DR"

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
                level="INFO",
                message=message,
                extra_data={"current": current, "total": total, "progress": current / total}
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
