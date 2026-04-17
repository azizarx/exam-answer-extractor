"""
OCR artifact writer.
Stores OCR output in a structured OCRResults directory for troubleshooting.
"""
from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import pytesseract
from PIL import Image

from backend.config import get_settings
from backend.services.image_preprocessor import ImagePreprocessor
from backend.services.ocr_engine import get_ocr_engine

logger = logging.getLogger(__name__)


class OCRResultsWriter:
    """Persist per-page OCR outputs and a summary JSON report."""

    def __init__(self):
        settings = get_settings()
        self.settings = settings
        self.storage_root = Path(settings.storage_root).expanduser().resolve()
        self.ocr_root = self.storage_root / settings.ocr_results_folder_name
        self.ocr_root.mkdir(parents=True, exist_ok=True)

        self.preprocessor = ImagePreprocessor()
        self.ocr_engine = get_ocr_engine(lang=settings.ocr_language)

    @staticmethod
    def _safe_token(value: str, max_len: int = 80) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
        cleaned = cleaned.strip("._-")
        if not cleaned:
            cleaned = "unknown"
        return cleaned[:max_len]

    def _build_run_dir(self, context_id: str, source_filename: Optional[str]) -> Path:
        now = datetime.utcnow()
        source_stem = self._safe_token(Path(source_filename or "unknown").stem)
        context_token = self._safe_token(context_id)

        run_dir = (
            self.ocr_root
            / now.strftime("%Y")
            / now.strftime("%m")
            / now.strftime("%d")
            / context_token
            / source_stem
            / now.strftime("%H%M%S")
        )
        (run_dir / "pages").mkdir(parents=True, exist_ok=True)
        return run_dir

    @staticmethod
    def _confidence_stats(image: Image.Image, lang: str) -> Dict[str, Any]:
        data = pytesseract.image_to_data(
            image,
            lang=lang,
            output_type=pytesseract.Output.DICT,
        )
        confidences: List[int] = []
        for raw_conf in data.get("conf", []):
            text_conf = str(raw_conf).strip()
            if not text_conf:
                continue
            try:
                score = int(float(text_conf))
            except ValueError:
                continue
            if score >= 0:
                confidences.append(score)

        average_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        return {
            "average_confidence": average_confidence,
            "confidence_samples": len(confidences),
        }

    def save_from_images(
        self,
        image_paths: List[str],
        context_id: str,
        source_filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not image_paths:
            raise ValueError("image_paths is empty; cannot create OCR results")

        run_dir = self._build_run_dir(context_id=context_id, source_filename=source_filename)
        pages_dir = run_dir / "pages"

        page_entries: List[Dict[str, Any]] = []
        all_confidences: List[float] = []

        for page_index, image_path in enumerate(image_paths, start=1):
            page_file = pages_dir / f"page_{page_index:03d}.txt"
            page_info: Dict[str, Any] = {
                "page": page_index,
                "image_file": Path(image_path).name,
                "text_file": str(page_file.relative_to(run_dir)).replace("\\", "/"),
            }
            try:
                with Image.open(image_path) as opened:
                    page_image = opened.convert("RGB")

                if self.settings.enable_image_preprocessing:
                    page_image = self.preprocessor.preprocess_pil_image(
                        page_image,
                        mode=self.settings.preprocessing_mode,
                    )

                text = self.ocr_engine.extract_from_pil(page_image)
                stats = self._confidence_stats(page_image, self.ocr_engine.lang)
                word_count = len([piece for piece in (text or "").split() if piece.strip()])

                page_file.write_text(text or "", encoding="utf-8")

                page_info.update(
                    {
                        "status": "ok",
                        "word_count": word_count,
                        **stats,
                    }
                )
                if stats["average_confidence"] > 0:
                    all_confidences.append(float(stats["average_confidence"]))
            except Exception as exc:
                page_file.write_text("", encoding="utf-8")
                page_info.update(
                    {
                        "status": "error",
                        "error": str(exc),
                        "word_count": 0,
                        "average_confidence": 0.0,
                        "confidence_samples": 0,
                    }
                )
                logger.warning("OCR page extraction failed for %s page=%s: %s", context_id, page_index, exc)

            page_entries.append(page_info)

        summary = {
            "name": "OCRResults",
            "context_id": context_id,
            "source_filename": source_filename,
            "created_at": datetime.utcnow().isoformat(),
            "preprocessing_enabled": bool(self.settings.enable_image_preprocessing),
            "preprocessing_mode": self.settings.preprocessing_mode,
            "pages_total": len(page_entries),
            "pages_ok": sum(1 for page in page_entries if page.get("status") == "ok"),
            "average_confidence": round(sum(all_confidences) / len(all_confidences), 2) if all_confidences else 0.0,
            "pages": page_entries,
        }

        summary_path = run_dir / "OCRResults.json"
        summary_path.write_text(_to_json(summary), encoding="utf-8")

        relative_run_dir = str(run_dir.relative_to(self.storage_root)).replace("\\", "/")
        relative_summary = str(summary_path.relative_to(self.storage_root)).replace("\\", "/")

        logger.info("Saved OCRResults | context=%s | path=%s", context_id, relative_summary)
        return {
            "context_id": context_id,
            "relative_dir": relative_run_dir,
            "relative_summary_path": relative_summary,
            "absolute_summary_path": str(summary_path),
            "pages_total": summary["pages_total"],
            "pages_ok": summary["pages_ok"],
            "average_confidence": summary["average_confidence"],
        }


def _to_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


_ocr_results_writer: Optional[OCRResultsWriter] = None


def get_ocr_results_writer() -> OCRResultsWriter:
    global _ocr_results_writer
    if _ocr_results_writer is None:
        _ocr_results_writer = OCRResultsWriter()
    return _ocr_results_writer
