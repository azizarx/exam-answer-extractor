"""
Template-driven extraction orchestrator.

Happy path (CV-owns-MCQ):
  * CV MCQ overlay   — bubble grid via OpenCV (deskew + anchor match).
  * LLM header/FR    — Gemini on cropped header + free-response regions only
                       (parallel with CV). MCQ is NOT asked of the LLM.
  * Mathpix overlay  — diagram CDN URLs when the template flags diagram qs.

Last resort: if CV reports low_coverage / bad anchor / huge_dy and
``mcq_llm_last_resort`` is enabled, Gemini may read an MCQ grid crop.
Otherwise those MCQ questions are flagged ``needs_review``.

Default upload mode auto-detects layout per page from the footer (OCR).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import cv2
import google.generativeai as genai
import numpy as np
from PIL import Image

from backend.config import get_settings
from backend.services import mathpix_client
from backend.services.diagram_cv import extract_seamo_x_a_q9
from backend.services.gemini_client import create_gemini_model
from backend.services.image_preprocessor import ImagePreprocessor
from backend.services.mcq_extractor import (
    ambiguous_mcq_questions,
    extract_page as mcq_extract_page,
)
from backend.services.page_deskew import deskew_if_enabled
from backend.services.page_layout_classifier import (
    PageLayoutDetection,
    _gemini_classify_header,
    apply_embedded_family_hint,
    classify_pdf_text_page_families,
    classify_pdf_text_pages,
    classify_page_path,
)
from backend.services.run_logger import llm_call
from backend.services.template_service import (
    AnswerSection,
    ExamTemplate,
    Region,
    TemplateRegistry,
    get_template_registry,
)

logger = logging.getLogger(__name__)

# Whole-page review for any CV quality warning.
MCQ_TRUST_WARNINGS = frozenset({
    "low_coverage",
    "anchor_match_low",
    "huge_dy",
    "huge_dx",
    "no_rows_extracted",
    "low_avg_ratio",
    "weak_fill_scores",
    "lattice_align_failed",
    "lattice_residual_high",
})
FR_SECTION_TYPES = frozenset({"open_response", "numeric_grid"})


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

class TemplateExtractor:
    """Template-driven extractor: LLM + CV + Mathpix overlays."""

    def __init__(
        self,
        template_id: str,
        *,
        shared_model=None,
        shared_model_name: Optional[str] = None,
    ):
        self.template_id = template_id
        self.registry: TemplateRegistry = get_template_registry()
        template = self.registry.get(template_id)
        if template is None:
            raise ValueError(f"Unknown template_id: {template_id!r}")
        self.template: ExamTemplate = template

        settings = get_settings()
        self._preprocess_enabled = bool(settings.enable_image_preprocessing)
        self._preprocess_mode = settings.preprocessing_mode
        self._image_preprocessor = ImagePreprocessor()

        # One Gemini model instance, shared across worker threads (SDK is
        # thread-safe for inference; the model_name selection is a one-time
        # network call we don't want repeated per page).
        if shared_model is not None:
            self.model = shared_model
            self.model_name = shared_model_name or getattr(shared_model, "model_name", "shared")
        else:
            self.model, self.model_name = create_gemini_model()

        # Mathpix is only run when needed
        self.mathpix_app_id = settings.mathpix_app_id
        self.mathpix_app_key = settings.mathpix_app_key
        self.mathpix_poll_interval = settings.mathpix_poll_interval_seconds
        self.mathpix_max_wait = settings.mathpix_max_wait_seconds

        logger.info(
            "TemplateExtractor init template=%s model=%s preproc=%s(%s)",
            self.template_id, self.model_name,
            self._preprocess_enabled, self._preprocess_mode,
        )

    # ----- main entrypoint -------------------------------------------------

    def extract_pdf(
        self,
        pdf_path: str,
        image_paths: List[str],
        *,
        submission_id: Optional[int] = None,
        db=None,
        max_workers: int = 3,
        filename: str = "",
    ) -> Dict[str, Any]:
        """Extract all candidates with this forced single template."""
        detections = [
            PageLayoutDetection(
                template_id=self.template_id,
                method="forced",
                raw_text="",
            )
            for _ in image_paths
        ]
        return extract_pages(
            pdf_path,
            image_paths,
            detections,
            max_workers=max_workers,
            shared_extractors={self.template_id: self},
        )

    # ----- per-page -----------------------------------------------------

    def _extract_one_page(
        self,
        image_path: str,
        page_num: int,
        run_cv_mcq: bool,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        settings = get_settings()
        bgr = cv2.imread(image_path)
        if bgr is None:
            logger.error("PAGE[%d] failed to load image %s", page_num, image_path)
            return _empty_candidate(page_num, errors=[f"image_not_readable: {image_path}"])

        bgr, deskew_deg = deskew_if_enabled(
            bgr, enabled=bool(settings.enable_page_deskew),
        )
        # Keep CV and AI crops on the same deskewed pixels.  The previous
        # full-page preprocessing pass was both expensive and ineffective:
        # _crop_region_pil() recropped the raw BGR image, discarding it.
        pil_image = _bgr_to_pil(bgr)

        logger.info(
            "PAGE[%d] START run_cv_mcq=%s deskew=%.2f",
            page_num, run_cv_mcq, deskew_deg,
        )

        page_template, page_scale = self.template.adapted_to_image(bgr)
        if abs(page_scale - 1.0) >= 0.10:
            extraction_flags_pre = [f"template_scale={page_scale:.3f}"]
        else:
            extraction_flags_pre = []

        mcq_answers: Dict[str, str] = {}
        mcq_warning: Optional[str] = None
        mcq_result = None
        llm_header: Dict[str, Any] = {}
        llm_fr: Dict[str, str] = {}
        review_qs: Set[str] = set()
        extraction_flags: List[str] = list(extraction_flags_pre)
        diagram_cv_result = None

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_mcq = None
            if run_cv_mcq:
                f_mcq = pool.submit(
                    lambda: mcq_extract_page(
                        bgr, self.template, page_num, self.registry, deskew=False,
                    )
                )
            f_ai = pool.submit(
                self._llm_extract_header_fr,
                pil_image,
                bgr,
                page_num,
                page_template,
            )

            if f_mcq is not None:
                try:
                    mcq_result = f_mcq.result()
                    mcq_answers = dict(mcq_result.answers)
                    mcq_warning = getattr(mcq_result, "warning", None) or getattr(
                        mcq_result, "reason", None
                    )
                    min_ratio = 1.2
                    min_ink = 110
                    for sec in page_template.sections:
                        if sec.type == "mcq_grid" and sec.scoring:
                            min_ratio = float(sec.scoring.min_ratio)
                            min_ink = int(sec.scoring.min_ink_pixels)
                            break
                    review_qs |= set(
                        ambiguous_mcq_questions(
                            mcq_result,
                            min_ratio=min_ratio,
                            min_ink_pixels=min_ink,
                        )
                    )
                    logger.info(
                        "PAGE[%d] CV_MCQ status=%s answers=%d warning=%s",
                        page_num, mcq_result.status, len(mcq_answers), mcq_warning,
                    )
                except Exception as exc:
                    logger.error("PAGE[%d] CV MCQ crashed: %s", page_num, exc)
                    extraction_flags.append("cv_mcq_error")
                    review_qs |= {str(q) for q in _mcq_question_numbers(page_template)}

            try:
                llm_header, llm_fr, hdr_ok, fr_ok, fr_review = f_ai.result()
                if not hdr_ok:
                    extraction_flags.append("llm_header_failed")
                if not fr_ok:
                    extraction_flags.append("llm_fr_failed")
                review_qs |= fr_review
            except Exception as exc:
                logger.error("PAGE[%d] LLM header/FR crashed: %s", page_num, exc)
                extraction_flags.append("llm_header_error")
                extraction_flags.append("llm_fr_error")
                review_qs |= {str(q) for q in _fr_question_numbers(page_template)}

        # Q9 has twelve adjacent sectors. Coarse AI descriptions such as
        # "lower-right" can conflate neighboring answers, so a focused CV
        # measurement emits an exact clock-face interval when trustworthy.
        if page_template.key_template_id == "seamo_x_2026_a":
            diagram_cv_result = extract_seamo_x_a_q9(bgr)
            if diagram_cv_result.status == "ok":
                llm_fr["9"] = str(diagram_cv_result.answer)
                review_qs.discard("9")
                extraction_flags.append("diagram_cv_q9")
            elif diagram_cv_result.status == "blank" and not _is_real_answer(
                llm_fr.get("9")
            ):
                llm_fr["9"] = "BL"
                review_qs.discard("9")
                extraction_flags.append("diagram_cv_q9_blank")
            else:
                review_qs.add("9")
                extraction_flags.append("diagram_cv_q9_needs_review")

        # Last-resort MCQ LLM or needs_review when CV trust is low.
        if run_cv_mcq and mcq_warning in MCQ_TRUST_WARNINGS:
            extraction_flags.append(f"mcq_{mcq_warning}")
            mcq_qs = {str(q) for q in _mcq_question_numbers(page_template)}
            if bool(settings.mcq_llm_last_resort):
                try:
                    llm_mcq, mcq_ok = self._llm_extract_mcq_crop(
                        pil_image, bgr, page_num, page_template,
                    )
                    if mcq_ok and llm_mcq:
                        for q, ans in llm_mcq.items():
                            if q in mcq_qs and _is_real_answer(ans):
                                # Fill gaps only — never silently overwrite a CV letter.
                                if not _is_real_answer(mcq_answers.get(q)):
                                    mcq_answers[q] = ans
                        extraction_flags.append("mcq_llm_last_resort_applied")
                    else:
                        review_qs |= mcq_qs
                        extraction_flags.append("mcq_llm_last_resort_failed")
                except Exception as exc:
                    logger.error("PAGE[%d] MCQ last-resort LLM failed: %s", page_num, exc)
                    review_qs |= mcq_qs
            else:
                review_qs |= mcq_qs

        candidate = _assemble_candidate(
            page_num=page_num,
            header=llm_header,
            mcq_answers=mcq_answers,
            fr_answers=llm_fr,
            review_qs=review_qs,
            mcq_warning=mcq_warning,
            extraction_flags=extraction_flags,
            template=page_template,
        )
        if deskew_deg:
            candidate.setdefault("extra_fields", {})["deskew_degrees"] = round(deskew_deg, 2)
        if diagram_cv_result is not None:
            candidate.setdefault("extra_fields", {})["diagram_cv"] = {
                "9": {
                    "status": diagram_cv_result.status,
                    "sectors": list(diagram_cv_result.sectors),
                    "peak_score": round(diagram_cv_result.peak_score, 3),
                    "reason": diagram_cv_result.reason,
                }
            }
            if diagram_cv_result.status in {"ok", "blank"}:
                candidate["diagram_cv_questions"] = [9]

        logger.info(
            "PAGE[%d] DONE answers=%d review=%d flags=%s t=%.2fs",
            page_num,
            len(candidate.get("answers") or {}),
            len(review_qs),
            extraction_flags,
            time.perf_counter() - t0,
        )
        return candidate

    # ----- anchor priming ------------------------------------------------

    def _prime_anchor_from_first_page(self, image_path: str) -> None:
        self._prime_anchor_from_page(image_path)

    def _prime_anchor_from_page(self, image_path: str) -> None:
        """Crop the template's anchor region from a page image and stash it
        in the registry cache so per-page anchor matching uses a crop from
        THIS submission's scan (not a stale on-disk reference).
        """
        first_img = cv2.imread(image_path)
        if first_img is None:
            logger.warning("ANCHOR prime failed: cannot read page %s", image_path)
            return
        settings = get_settings()
        first_img, _ = deskew_if_enabled(
            first_img, enabled=bool(settings.enable_page_deskew),
        )
        tmpl, scale = self.template.adapted_to_image(first_img)
        region = tmpl.anchor.region
        if region.w <= 0 or region.h <= 0:
            return
        h, w = first_img.shape[:2]
        x, y = region.x, region.y
        if y + region.h > h or x + region.w > w:
            logger.warning(
                "ANCHOR prime: region (%d,%d,%d,%d) outside image (%dx%d) — keeping cached anchor",
                x, y, region.w, region.h, w, h,
            )
            return
        crop = first_img[y : y + region.h, x : x + region.w].copy()
        self.registry._anchor_images[self.template_id] = crop
        logger.info(
            "ANCHOR primed template=%s from page (%dx%d at %d,%d) scale=%.3f",
            self.template_id, region.w, region.h, x, y, scale,
        )

    # ----- LLM: header / FR / last-resort MCQ ---------------------------

    def _llm_generate(
        self,
        stage: str,
        prompt: str,
        images: List[Image.Image],
        page_num: int,
        image_labels: Optional[Sequence[str]] = None,
    ) -> Tuple[Optional[str], bool]:
        """Call Gemini through the one bounded retry owner. Returns (text, ok)."""
        contents: List[Any] = [prompt]
        for index, image in enumerate(images):
            if image_labels and index < len(image_labels):
                contents.append(f"IMAGE {index + 1}: {image_labels[index]}")
            contents.append(image)
        try:
            response = llm_call(
                stage,
                self.model,
                contents,
                genai.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
                logger,
                image=images[0] if images else None,
            )
            return (response.text or "").strip(), True
        except Exception as exc:
            logger.error("PAGE[%d] LLM[%s] raised: %s", page_num, stage, exc)
            return None, False

    def _llm_extract_header_fr(
        self,
        pil_image: Image.Image,
        bgr: np.ndarray,
        page_num: int,
        template: Optional[Any] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, str], bool, bool, Set[str]]:
        """Extract header + FR with one primary call and selective fallbacks.

        The two regions remain separate images so the model keeps their native
        detail.  A partial response retries only the incomplete component;
        missing FR keys are always surfaced for review rather than silently
        becoming trusted blanks.
        """
        tmpl = template or self.template
        fr_sections = [s for s in tmpl.sections if s.type in FR_SECTION_TYPES]
        header_crop = _crop_region_pil(pil_image, bgr, tmpl.header_region)
        fr_specs = _fr_model_crop_specs(fr_sections)
        images = [header_crop]
        images.extend(
            _crop_region_pil(pil_image, bgr, crop_region)
            for _q_start, _q_end, _kind, crop_region in fr_specs
        )

        prompt = _build_header_fr_crop_prompt(tmpl, fr_specs)
        labels = ["HEADER"]
        labels.extend(
            (
                f"FREE RESPONSE Q{q_start}"
                if q_start == q_end
                else f"FREE RESPONSES Q{q_start}-Q{q_end}"
            )
            for q_start, q_end, _kind, _region in fr_specs
        )
        text, call_ok = self._llm_generate(
            f"header_fr:{page_num}",
            prompt,
            images,
            page_num,
            image_labels=labels,
        )
        parsed, parse_ok = _parse_extraction_json_with_status(text or "", page_num)
        header = dict(parsed.get("header") or {}) if parse_ok else {}

        expected_header = {field.key for field in tmpl.header_fields}
        expected_fr = {str(q) for q in _fr_question_numbers(tmpl)}
        raw_answers = parsed.get("answers") or {} if parse_ok else {}
        fr_answers = {
            str(q): value
            for q, value in raw_answers.items()
            if str(q) in expected_fr
        }

        header_ok = bool(call_ok and parse_ok and expected_header.issubset(header))
        fr_ok = bool(call_ok and parse_ok and expected_fr.issubset(fr_answers))

        if not header_ok:
            fallback_header, _fallback_ok = self._llm_extract_header(
                pil_image, bgr, page_num, tmpl,
            )
            # Preserve non-empty values from either pass; the focused fallback
            # fills only keys absent from the primary response.
            for key, value in fallback_header.items():
                if key not in header or header[key] in (None, ""):
                    header[key] = value
            header_ok = expected_header.issubset(header)

        review: Set[str] = set()
        if not fr_ok and expected_fr:
            fallback_fr, _fallback_ok, fallback_review = self._llm_extract_fr(
                pil_image, bgr, page_num, tmpl,
            )
            for key, value in fallback_fr.items():
                fr_answers.setdefault(str(key), value)
            review |= fallback_review
            fr_ok = expected_fr.issubset(fr_answers)

        missing_fr = expected_fr - set(fr_answers)
        if missing_fr:
            review |= missing_fr
            fr_ok = False

        return header, fr_answers, header_ok, fr_ok, review

    def _llm_extract_header(
        self,
        pil_image: Image.Image,
        bgr: np.ndarray,
        page_num: int,
        template: Optional[Any] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        tmpl = template or self.template
        crop = _crop_region_pil(pil_image, bgr, tmpl.header_region)
        prompt = _build_header_prompt(tmpl)
        text, ok = self._llm_generate(
            f"header:{page_num}", prompt, [crop], page_num, image_labels=["HEADER"],
        )
        if not ok or not text:
            return {}, False
        parsed, parse_ok = _parse_extraction_json_with_status(text, page_num)
        header = parsed.get("header") or {}
        expected = {field.key for field in tmpl.header_fields}
        return header, bool(parse_ok and expected.issubset(header))

    def _llm_extract_fr(
        self,
        pil_image: Image.Image,
        bgr: np.ndarray,
        page_num: int,
        template: Optional[Any] = None,
    ) -> Tuple[Dict[str, str], bool, Set[str]]:
        tmpl = template or self.template
        fr_sections = [s for s in tmpl.sections if s.type in FR_SECTION_TYPES]
        if not fr_sections:
            return {}, True, set()

        fr_specs = _fr_model_crop_specs(fr_sections)
        images = [
            _crop_region_pil(pil_image, bgr, crop_region)
            for _q_start, _q_end, _kind, crop_region in fr_specs
        ]
        prompt = _build_fr_prompt(fr_specs)
        labels = [
            (
                f"FREE RESPONSE Q{q_start}"
                if q_start == q_end
                else f"FREE RESPONSES Q{q_start}-Q{q_end}"
            )
            for q_start, q_end, _kind, _region in fr_specs
        ]
        text, ok = self._llm_generate(
            f"fr:{page_num}", prompt, images, page_num, image_labels=labels,
        )
        review: Set[str] = set()
        if not ok or not text:
            review = {str(q) for q in _fr_question_numbers(tmpl)}
            return {}, False, review
        parsed, parse_ok = _parse_extraction_json_with_status(text, page_num)
        fr_qs = {str(q) for q in _fr_question_numbers(tmpl)}
        answers = {
            str(k): v
            for k, v in (parsed.get("answers") or {}).items()
            if str(k) in fr_qs
        }
        missing = fr_qs - set(answers)
        review |= missing
        return answers, bool(parse_ok and not missing), review

    def _llm_extract_mcq_crop(
        self,
        pil_image: Image.Image,
        bgr: np.ndarray,
        page_num: int,
        template: Optional[Any] = None,
    ) -> Tuple[Dict[str, str], bool]:
        tmpl = template or self.template
        mcq_sections = [s for s in tmpl.sections if s.type == "mcq_grid"]
        if not mcq_sections:
            return {}, True
        images = [_crop_region_pil(pil_image, bgr, s.region) for s in mcq_sections]
        prompt = _build_mcq_last_resort_prompt(mcq_sections)
        text, ok = self._llm_generate(f"mcq_last:{page_num}", prompt, images, page_num)
        if not ok or not text:
            return {}, False
        parsed = _parse_extraction_json(text, page_num)
        mcq_qs = {str(q) for q in _mcq_question_numbers(tmpl)}
        answers = {
            str(k): v for k, v in (parsed.get("answers") or {}).items() if str(k) in mcq_qs
        }
        return answers, True

    # Kept for tests / debug callers that still invoke full-page extract.
    def _llm_extract_full(self, pil_image: Image.Image, page_num: int) -> Dict[str, Any]:
        prompt = _build_header_fr_legacy_prompt(self.template)
        text, ok = self._llm_generate(f"full_page:{page_num}", prompt, [pil_image], page_num)
        if not ok or not text:
            return {"header": {}, "answers": {}}
        return _parse_extraction_json(text, page_num)


def extract_pages(
    pdf_path: str,
    image_paths: List[str],
    detections: Sequence[PageLayoutDetection],
    *,
    max_workers: int = 3,
    shared_extractors: Optional[Dict[str, TemplateExtractor]] = None,
) -> Dict[str, Any]:
    """Extract pages where each page may use a different detected template.

    Pages with ``template_id=None`` are persisted as empty candidates with a
    detection warning (no Gemini/CV) so marking never uses the wrong key.
    """
    if len(detections) != len(image_paths):
        raise ValueError("detections length must match image_paths")

    t_start = time.perf_counter()
    settings = get_settings()
    extractors: Dict[str, TemplateExtractor] = dict(shared_extractors or {})
    shared_model = None
    shared_model_name = None

    def _get_extractor(tid: str) -> TemplateExtractor:
        nonlocal shared_model, shared_model_name
        if tid not in extractors:
            if shared_model is None:
                shared_model, shared_model_name = create_gemini_model()
            extractors[tid] = TemplateExtractor(
                tid,
                shared_model=shared_model,
                shared_model_name=shared_model_name,
            )
        return extractors[tid]

    # Prime anchors per detected template from the first page of that layout.
    primed: set[str] = set()
    for path, det in zip(image_paths, detections):
        tid = det.template_id
        if not tid or tid in primed:
            continue
        extractor = _get_extractor(tid)
        if extractor.template.has_mcq:
            extractor._prime_anchor_from_page(path)
        primed.add(tid)

    # Mathpix once if any page's template needs diagram URLs.
    page_diagram_qs: List[List[int]] = []
    any_diagrams = False
    for det in detections:
        if det.template_id and det.template_id in extractors:
            qs = _diagram_questions_for(extractors[det.template_id].template)
        elif det.template_id:
            qs = _diagram_questions_for(_get_extractor(det.template_id).template)
        else:
            qs = []
        page_diagram_qs.append(qs)
        if qs:
            any_diagrams = True

    sample = next(iter(extractors.values()), None)
    mathpix_app_id = (sample.mathpix_app_id if sample else settings.mathpix_app_id)
    mathpix_app_key = (sample.mathpix_app_key if sample else settings.mathpix_app_key)
    mathpix_poll_interval = (
        sample.mathpix_poll_interval if sample else settings.mathpix_poll_interval_seconds
    )
    mathpix_max_wait = (
        sample.mathpix_max_wait if sample else settings.mathpix_max_wait_seconds
    )
    run_mathpix = any_diagrams and bool(mathpix_app_id) and bool(mathpix_app_key)

    logger.info(
        "EXTRACT start pages=%d templates=%s run_mathpix=%s",
        len(image_paths),
        sorted({d.template_id for d in detections if d.template_id}),
        run_mathpix,
    )

    pdf_id: Optional[str] = None
    mathpix_submit_error: Optional[str] = None
    if run_mathpix:
        try:
            pdf_id = mathpix_client.submit_pdf(
                pdf_path,
                app_id=mathpix_app_id,
                app_key=mathpix_app_key,
            )
        except Exception as exc:
            mathpix_submit_error = f"{type(exc).__name__}: {exc}"
            logger.error("MATHPIX submit failed: %s", mathpix_submit_error)

    candidates: List[Dict[str, Any]] = [None] * len(image_paths)  # type: ignore[list-item]

    def _work(i: int) -> Dict[str, Any]:
        det = detections[i]
        path = image_paths[i]
        page_num = i + 1
        if not det.template_id:
            cand = _empty_candidate(
                page_num,
                errors=[det.warning or "layout_undetected"],
            )
            cand["template_id"] = None
            cand["detection"] = det.to_dict()
            return cand
        extractor = _get_extractor(det.template_id)
        cand = extractor._extract_one_page(
            path, page_num, extractor.template.has_mcq,
        )
        cand["template_id"] = det.template_id
        cand["detection"] = det.to_dict()
        cand["diagram_qs"] = list(page_diagram_qs[i])
        return cand

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(_work, i): i for i in range(len(image_paths))}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                candidates[i] = fut.result()
            except Exception as exc:
                logger.exception("PAGE[%d] worker crashed: %s", i + 1, exc)
                det = detections[i]
                candidates[i] = _empty_candidate(i + 1, errors=[str(exc)])
                candidates[i]["template_id"] = det.template_id
                candidates[i]["detection"] = det.to_dict()

    if pdf_id is not None:
        try:
            status = mathpix_client.poll_pdf(
                pdf_id,
                app_id=mathpix_app_id,
                app_key=mathpix_app_key,
                poll_interval=mathpix_poll_interval,
                max_wait=mathpix_max_wait,
            )
            if status.get("status") == "completed":
                mmd = mathpix_client.fetch_mmd(
                    pdf_id,
                    app_id=mathpix_app_id,
                    app_key=mathpix_app_key,
                )
                _apply_diagram_urls(candidates, mmd)
            else:
                logger.warning(
                    "MATHPIX status=%s; skipping diagram overlay (info=%s)",
                    status.get("status"), status.get("error_info"),
                )
        except Exception as exc:
            logger.error("MATHPIX poll/fetch failed: %s: %s", type(exc).__name__, exc)
    elif run_mathpix and mathpix_submit_error:
        logger.warning(
            "MATHPIX submit error earlier (%s); diagram answers will remain LLM defaults",
            mathpix_submit_error,
        )

    # Drop internal diagram_qs helper before return
    for cand in candidates:
        if cand is not None:
            cand.pop("diagram_qs", None)
            cand.pop("diagram_cv_questions", None)

    elapsed = time.perf_counter() - t_start
    pages_with_data = sum(
        1 for c in candidates
        if c and any(_is_real_answer(v) for v in (c.get("answers") or {}).values())
    )
    logger.info(
        "EXTRACT done pages=%d with_data=%d elapsed=%.2fs",
        len(candidates), pages_with_data, elapsed,
    )
    return {
        "candidates": candidates,
        "pages_processed": len(image_paths),
        "pages_with_data": pages_with_data,
        "processing_time": round(elapsed, 2),
    }


def extract_pdf_auto(
    pdf_path: str,
    image_paths: List[str],
    *,
    max_workers: int = 3,
) -> Dict[str, Any]:
    """Classify each page via footer OCR (Gemini header fallback), then extract."""
    settings = get_settings()
    classify_workers = max(1, int(getattr(settings, "max_classify_workers", 8) or 8))
    image_preprocessor = ImagePreprocessor()
    n = len(image_paths)
    try:
        detections = classify_pdf_text_pages(pdf_path)
        if len(detections) != n:
            logger.warning(
                "PDF_TEXT layout count mismatch text=%d images=%d; using raster fallback",
                len(detections), n,
            )
            detections = [None] * n
    except Exception as exc:
        logger.warning("PDF_TEXT layout fast path unavailable: %s", exc)
        detections = [None] * n

    try:
        embedded_families = classify_pdf_text_page_families(pdf_path)
        if len(embedded_families) != n:
            logger.warning(
                "PDF_TEXT family count mismatch text=%d images=%d; ignoring hints",
                len(embedded_families), n,
            )
            embedded_families = [None] * n
    except Exception as exc:
        logger.warning("PDF_TEXT family hints unavailable: %s", exc)
        embedded_families = [None] * n

    fast_count = sum(d is not None for d in detections)
    logger.info(
        "LAYOUT pdf_text resolved=%d fallback=%d",
        fast_count, n - fast_count,
    )

    # Raster misses share one lazily-created Gemini model.  This prevents each
    # worker from listing models and constructing its own client while keeping
    # the no-network OCR-only path intact.
    layout_model = None
    layout_model_lock = threading.Lock()

    def _shared_gemini_header(image_bgr: np.ndarray) -> Dict[str, Any]:
        nonlocal layout_model
        if layout_model is None:
            with layout_model_lock:
                if layout_model is None:
                    layout_model, _model_name = create_gemini_model()
                    logger.info("LAYOUT shared Gemini model=%s", _model_name)
        return _gemini_classify_header(image_bgr, model=layout_model)

    def _classify_one(i: int) -> tuple[int, PageLayoutDetection]:
        path = image_paths[i]
        if image_preprocessor.is_blank(path):
            return i, PageLayoutDetection(
                template_id=None,
                method="blank_check",
                warning="blank_page",
            )
        return i, classify_page_path(path, gemini_header=_shared_gemini_header)

    t_cls = time.perf_counter()
    unresolved = [i for i, detection in enumerate(detections) if detection is None]
    workers = min(classify_workers, max(1, len(unresolved)))
    if workers == 1 or len(unresolved) <= 1:
        for i in unresolved:
            idx, det = _classify_one(i)
            detections[idx] = det
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_classify_one, i) for i in unresolved]
            for fut in as_completed(futs):
                idx, det = fut.result()
                detections[idx] = det

    # The embedded instruction text is a stronger form-family signal than a
    # faded raster banner.  It changes only classic vs format-B; the page's
    # independently read series/year/paper fields stay untouched.
    detections = [
        (
            apply_embedded_family_hint(detection, embedded_families[index])
            if detection is not None
            else None
        )
        for index, detection in enumerate(detections)
    ]

    # type narrowing for mypy-ish use
    resolved: List[PageLayoutDetection] = [
        d if d is not None else PageLayoutDetection(
            template_id=None, method="footer_ocr", warning="classify_failed",
        )
        for d in detections
    ]

    for i, det in enumerate(resolved):
        if det.warning == "blank_page":
            logger.info("LAYOUT[%d] blank_page skipped", i + 1)
        else:
            logger.info(
                "LAYOUT[%d] template=%s method=%s warning=%s raw=%r",
                i + 1, det.template_id, det.method, det.warning, (det.raw_text or "")[:80],
            )

    logger.info(
        "LAYOUT classify done pages=%d workers=%d elapsed=%.2fs",
        n, workers, time.perf_counter() - t_cls,
    )

    return extract_pages(pdf_path, image_paths, resolved, max_workers=max_workers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bgr_to_pil(bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _crop_region_pil(
    pil_image: Image.Image,
    bgr: np.ndarray,
    region: Optional[Region],
) -> Image.Image:
    """Crop a template region; fall back to full page if region is empty."""
    if region is None or region.w <= 0 or region.h <= 0:
        return pil_image
    h, w = bgr.shape[:2]
    x1 = max(0, int(region.x))
    y1 = max(0, int(region.y))
    x2 = min(w, int(region.x + region.w))
    y2 = min(h, int(region.y + region.h))
    if x2 <= x1 or y2 <= y1:
        return pil_image
    crop_bgr = bgr[y1:y2, x1:x2]
    return _bgr_to_pil(crop_bgr)


def _fr_question_crop_specs(
    sections: Sequence[AnswerSection],
) -> List[Tuple[int, int, str, Region]]:
    """Return focused FR crops, splitting regular row grids per question.

    Sending five small, explicitly mapped answer boxes is more reliable than
    asking the model to re-segment one tall column (notably for superscripts,
    faint digits, and isolated short answers).  Multi-column grids store their
    rows in column-major order, so both the horizontal slab and the next-row
    boundary must be resolved within the question's visual column.  Irregular
    sections retain one region-level crop.
    """
    specs: List[Tuple[int, int, str, Region]] = []
    for section in sections:
        count = section.question_end - section.question_start + 1
        grid = section.grid
        rows = list(grid.row_positions) if grid and grid.row_positions else []
        if count > 0 and len(rows) >= count:
            # Only trust questions_per_col when it accounts for every question.
            # Otherwise retain the historical one-column interpretation.
            raw_column_counts = [
                int(value)
                for value in (grid.questions_per_col or [])
                if int(value) > 0
            ]
            column_counts = (
                raw_column_counts
                if raw_column_counts and sum(raw_column_counts) == count
                else [count]
            )
            column_total = len(column_counts)
            # A positive cell width confirms that the template describes real
            # horizontal grid geometry.  Each crop still takes the whole slab:
            # for numeric grids cell_width is one character cell, not the full
            # question width, and the question label lies outside those cells.
            split_columns = column_total > 1 and int(grid.cell_width or 0) > 0

            section_left = int(section.region.x)
            section_right = int(section.region.x + section.region.w)
            section_top = int(section.region.y)
            section_bottom = int(section.region.y + section.region.h)
            cell_height = int(grid.cell_height or max(40, section.region.h // count))
            normalized_rows = [
                int(y + section_top) if int(y) < section_top else int(y)
                for y in rows[:count]
            ]
            offset = 0
            for column_index, questions_in_column in enumerate(column_counts):
                if split_columns:
                    x1 = section_left + section.region.w * column_index // column_total
                    x2 = (
                        section_left
                        + section.region.w * (column_index + 1) // column_total
                    )
                else:
                    x1, x2 = section_left, section_right

                column_rows = normalized_rows[offset : offset + questions_in_column]
                for row_index, row_y in enumerate(column_rows):
                    question = section.question_start + offset + row_index
                    override = section.question_overrides.get(question)
                    if (
                        override
                        and override.region
                        and override.region.w > 0
                        and override.region.h > 0
                    ):
                        specs.append(
                            (
                                question,
                                question,
                                override.type,
                                override.region,
                            )
                        )
                        continue

                    y1 = max(section_top, row_y - 40)
                    if row_index + 1 < len(column_rows):
                        y2 = min(section_bottom, column_rows[row_index + 1] - 45)
                    else:
                        y2 = min(section_bottom, row_y + cell_height + 80)
                    if y2 <= y1:
                        y1, y2 = section_top, section_bottom
                    specs.append(
                        (
                            question,
                            question,
                            section.type,
                            Region(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                        )
                    )
                offset += questions_in_column
        else:
            specs.append(
                (
                    section.question_start,
                    section.question_end,
                    section.type,
                    section.region,
                )
            )
    return specs


def _fr_model_crop_specs(
    sections: Sequence[AnswerSection],
) -> List[Tuple[int, int, str, Region]]:
    """Return the small set of ordered crops sent to the vision model.

    A long list of nearly identical one-row images is easy for a multimodal
    model to associate with the wrong JSON key, even when every attachment is
    text-labelled.  Multi-column SEAMO X sheets already print the question
    numbers inside each visual column, so keep each column intact and send
    three self-identifying images.  One-column layouts retain the focused
    per-question crops that work well for their much smaller FR sections.
    """
    specs: List[Tuple[int, int, str, Region]] = []
    for section in sections:
        count = section.question_end - section.question_start + 1
        grid = section.grid
        column_counts = [
            int(value)
            for value in ((grid.questions_per_col if grid else None) or [])
            if int(value) > 0
        ]
        if len(column_counts) <= 1 or sum(column_counts) != count:
            specs.extend(_fr_question_crop_specs([section]))
            continue

        section_left = int(section.region.x)
        column_total = len(column_counts)
        question = int(section.question_start)
        for column_index, questions_in_column in enumerate(column_counts):
            x1 = section_left + section.region.w * column_index // column_total
            x2 = section_left + section.region.w * (column_index + 1) // column_total
            q_start = question
            q_end = question + questions_in_column - 1
            has_diagram = any(
                (
                    override := section.question_overrides.get(q)
                ) is not None and override.type == "diagram"
                for q in range(q_start, q_end + 1)
            )
            specs.append(
                (
                    q_start,
                    q_end,
                    "mixed_response" if has_diagram else section.type,
                    Region(
                        x=x1,
                        y=section.region.y,
                        w=x2 - x1,
                        h=section.region.h,
                    ),
                )
            )
            question = q_end + 1
    return specs


def _mcq_question_numbers(template: ExamTemplate) -> List[int]:
    qs: List[int] = []
    for s in template.sections:
        if s.type == "mcq_grid":
            qs.extend(range(s.question_start, s.question_end + 1))
    return qs


def _fr_question_numbers(template: ExamTemplate) -> List[int]:
    qs: List[int] = []
    for s in template.sections:
        if s.type in FR_SECTION_TYPES:
            qs.extend(range(s.question_start, s.question_end + 1))
    return qs


def _is_real_answer(value: Any) -> bool:
    """A non-empty answer that isn't a blank/missing placeholder."""
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    if s.upper() in ("BL", "IN"):
        return False
    return True


def _diagram_questions_for(template: ExamTemplate) -> List[int]:
    """Return sorted list of question numbers flagged as diagrams in the template."""
    qs: set[int] = set()
    for section in template.sections:
        if section.type == "diagram":
            qs.update(range(section.question_start, section.question_end + 1))
        for q, ov in section.question_overrides.items():
            if ov.type == "diagram":
                qs.add(int(q))
    return sorted(qs)


def _empty_candidate(page_num: int, errors: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "page_number": page_num,
        "candidate_name": None,
        "candidate_number": None,
        "country": None,
        "paper_type": None,
        "extra_fields": {"errors": errors} if errors else {},
        "answers": {},
        "drawing_questions": {},
    }


def _assemble_candidate(
    *,
    page_num: int,
    header: Dict[str, Any],
    mcq_answers: Dict[str, str],
    fr_answers: Dict[str, str],
    review_qs: Set[str],
    mcq_warning: Optional[str],
    extraction_flags: List[str],
    template: ExamTemplate,
) -> Dict[str, Any]:
    """CV owns MCQ; LLM owns FR; header from LLM; trust metadata in extra_fields."""
    answers: Dict[str, str] = {}
    for q, ans in (mcq_answers or {}).items():
        answers[str(q)] = ans
    for q, ans in (fr_answers or {}).items():
        answers[str(q)] = ans

    # Ensure MCQ keys exist so marking sees blanks explicitly.
    for q in _mcq_question_numbers(template):
        answers.setdefault(str(q), "BL")

    KNOWN = ("candidate_name", "candidate_number", "country", "paper_type")
    candidate: Dict[str, Any] = {
        "page_number": page_num,
        "answers": answers,
        "drawing_questions": {},
    }
    for key in KNOWN:
        candidate[key] = header.get(key) or None
    extra = {k: v for k, v in header.items() if k not in KNOWN}
    trust = {
        str(q): ("needs_review" if str(q) in review_qs else "trusted")
        for q in answers
    }
    for q in review_qs:
        trust[str(q)] = "needs_review"
    extra["answer_trust"] = trust
    extra["needs_review_questions"] = sorted(review_qs, key=lambda x: int(x) if str(x).isdigit() else str(x))
    if mcq_warning:
        extra["mcq_warning"] = mcq_warning
    if extraction_flags:
        extra["extraction_flags"] = extraction_flags
    candidate["extra_fields"] = extra
    return candidate


# Back-compat alias used by older tests / call sites.
def _merge_llm_and_mcq(
    llm_data: Dict[str, Any],
    mcq_answers: Dict[str, str],
    page_num: int,
) -> Dict[str, Any]:
    """Legacy merge: CV MCQ overwrites LLM for any key CV produced."""
    header = llm_data.get("header") or {}
    answers = dict(llm_data.get("answers") or {})
    for q, ans in (mcq_answers or {}).items():
        answers[str(q)] = ans
    known = ("candidate_name", "candidate_number", "country", "paper_type")
    candidate: Dict[str, Any] = {
        "page_number": page_num,
        "answers": answers,
        "drawing_questions": {},
    }
    for key in known:
        candidate[key] = header.get(key) or None
    candidate["extra_fields"] = {k: v for k, v in header.items() if k not in known}
    return candidate



# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _header_field_lines(template: ExamTemplate) -> str:
    header_lines = []
    for f in template.header_fields:
        hint = ""
        if f.type == "text_line":
            hint = " (handwritten)"
        elif f.type == "grid_boxes":
            hint = " (digits/characters in boxes)"
        elif f.type == "qr_code":
            hint = " (QR code value)"
        header_lines.append(f'  - "{f.key}": {f.label}{hint}')
    return "\n".join(header_lines) if header_lines else "  (none)"


def _build_header_prompt(template: ExamTemplate) -> str:
    return (
        "Extract HEADER fields from this cropped exam answer-sheet header.\n"
        "Return ONLY valid JSON. No code fences.\n"
        f"HEADER FIELDS:\n{_header_field_lines(template)}\n"
        "Rules: blank/illegible fields → null.\n"
        'Schema: {"header": {...}, "answers": {}}\n'
    )


def _build_fr_prompt(
    crop_specs: Sequence[Tuple[int, int, str, Region]],
) -> str:
    lines = []
    for image_index, (q_start, q_end, kind, _region) in enumerate(crop_specs, start=1):
        rng = f"Q{q_start}" if q_start == q_end else f"Q{q_start}-{q_end}"
        if kind == "numeric_grid":
            description = 'digits/characters written, e.g. "42"'
        elif kind == "mixed_response":
            description = "read each printed question label and transcribe its written response"
        else:
            description = "handwritten response verbatim"
        lines.append(f"  - Image {image_index}: {rng} → {description}")
    block = "\n".join(lines)
    return (
        "Extract FREE-RESPONSE answers from the cropped answer-box image(s).\n"
        "Return ONLY valid JSON. No code fences.\n"
        f"ANSWERS:\n{block}\n"
        "Rules: blank → \"BL\". Do not invent answers. Preserve every unit, "
        "space, exponent, and superscript exactly (for example 75² is not 752).\n"
        "BL means the answer-box interior has no handwriting. A handwritten "
        "answer may equal its question number; transcribe it anyway.\n"
        'Schema: {"header": {}, "answers": {"21": "...", ...}}\n'
    )


def _build_header_fr_crop_prompt(
    template: ExamTemplate,
    crop_specs: Sequence[Tuple[int, int, str, Region]],
) -> str:
    """One-call prompt for the ordered header and free-response crops."""
    header_schema = {field.key: None for field in template.header_fields}
    fr_questions = [
        str(q)
        for q_start, q_end, _kind, _region in crop_specs
        for q in range(q_start, q_end + 1)
    ]
    answer_schema = {q: "BL" for q in fr_questions}
    schema = json.dumps(
        {"header": header_schema, "answers": answer_schema},
        ensure_ascii=False,
    )
    fr_lines = []
    for index, (q_start, q_end, kind, _region) in enumerate(crop_specs, start=2):
        if kind == "numeric_grid":
            answer_kind = "digits/characters written in the boxes"
        elif kind == "mixed_response":
            answer_kind = (
                "use the printed question labels to map every numeric or "
                "diagram response in this column"
            )
        else:
            answer_kind = "handwritten response verbatim"
        question_label = f"Q{q_start}" if q_start == q_end else f"Q{q_start}-{q_end}"
        override_hints = []
        for section in template.sections:
            for question in range(q_start, q_end + 1):
                override = section.question_overrides.get(question)
                if override is not None and override.prompt_hint:
                    override_hints.append(f"Q{question}: {override.prompt_hint}")
        hint_suffix = (
            "; " + " ".join(override_hints)
            if override_hints
            else ""
        )
        fr_lines.append(
            f"  - Image {index}: {question_label}; "
            f"{answer_kind}{hint_suffix}"
        )
    fr_block = "\n".join(fr_lines) if fr_lines else "  (no free-response images)"
    return (
        "Extract the header and free-response answers from these ordered exam crops.\n"
        "Image 1 is the HEADER. Do not read MCQ bubble answers.\n"
        f"HEADER FIELDS:\n{_header_field_lines(template)}\n"
        f"FREE-RESPONSE IMAGES:\n{fr_block}\n"
        "Return ONLY one valid JSON object. Include every shown header key and "
        "every listed question key exactly once.\n"
        "Use null for a blank/illegible header field and \"BL\" for a blank "
        "answer. Preserve every written unit, space, exponent, and superscript "
        "exactly (for example 75² is not 752); do not infer or invent.\n"
        "BL means the answer-box interior has no handwriting. A handwritten "
        "answer may equal its question number; transcribe it anyway.\n"
        f"Required shape: {schema}\n"
    )


def _build_mcq_last_resort_prompt(sections: List[AnswerSection]) -> str:
    lines = []
    for s in sections:
        opts = ", ".join(s.grid.options) if s.grid and s.grid.options else "A,B,C,D,E"
        lines.append(
            f'  - Q{s.question_start}-{s.question_end} options {opts} → '
            f'letter, "BL" if blank, "IN" if multiple marks'
        )
    block = "\n".join(lines)
    return (
        "LAST RESORT: read MCQ bubbles from this cropped grid image.\n"
        "Return ONLY valid JSON.\n"
        f"{block}\n"
        'Schema: {"header": {}, "answers": {"1": "A", ...}}\n'
    )


def _build_header_fr_legacy_prompt(template: ExamTemplate) -> str:
    """Full-page prompt without MCQ (debug / legacy full-page path)."""
    fr_lines = []
    for s in template.sections:
        if s.type not in FR_SECTION_TYPES and s.type != "diagram":
            continue
        rng = f"Q{s.question_start}-{s.question_end}"
        if s.type == "numeric_grid":
            fr_lines.append(f'  - {rng} numeric_grid')
        elif s.type == "open_response":
            fr_lines.append(f"  - {rng} open_response")
        elif s.type == "diagram":
            fr_lines.append(f'  - {rng} diagram → ""')
    fr_block = "\n".join(fr_lines) if fr_lines else "  (none)"
    return (
        "Extract header + free-response answers from this exam page.\n"
        "Do NOT extract MCQ bubble answers (handled by CV).\n"
        f"HEADER:\n{_header_field_lines(template)}\n"
        f"FR ANSWERS:\n{fr_block}\n"
        'Schema: {"header": {...}, "answers": {"21": "...", ...}}\n'
    )


def _build_prompt(template: ExamTemplate) -> str:
    """Backward-compatible name used by older tests — header+FR only."""
    return _build_header_fr_legacy_prompt(template)


def _parse_extraction_json_with_status(
    text: str,
    page_num: int,
) -> Tuple[Dict[str, Any], bool]:
    """Parse the LLM's JSON response. Strip code fences if present.

    Returns (normalized payload, parsed_ok).  The status lets callers
    distinguish a valid all-blank response from malformed output.
    """
    if not text:
        logger.error("PAGE[%d] LLM returned empty text", page_num)
        return {"header": {}, "answers": {}}, False

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)

    # Best-effort: find the first { ... } block if extra prose surrounds it.
    if not stripped.startswith("{"):
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            stripped = match.group(0)

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.error(
            "PAGE[%d] LLM JSON parse failed: %s; preview=%r",
            page_num, exc, stripped[:300],
        )
        return {"header": {}, "answers": {}}, False

    if not isinstance(data, dict):
        logger.error("PAGE[%d] LLM JSON is not a dict: %r", page_num, type(data))
        return {"header": {}, "answers": {}}, False

    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    answers_raw = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    # Coerce keys/values to strings
    answers = {str(k): ("" if v is None else str(v)) for k, v in answers_raw.items()}
    top_level_ok = isinstance(data.get("header"), dict) and isinstance(
        data.get("answers"), dict
    )
    return {"header": header, "answers": answers}, top_level_ok


def _parse_extraction_json(text: str, page_num: int) -> Dict[str, Any]:
    """Backward-compatible payload-only wrapper."""
    parsed, _ok = _parse_extraction_json_with_status(text, page_num)
    return parsed


# ---------------------------------------------------------------------------
# Mathpix diagram URL overlay
# ---------------------------------------------------------------------------

# Match either a question-number label or a Mathpix CDN URL. Question labels
# come in three flavors in real Mathpix MMD output:
#   \section*{Question 4}    (most common — what we saw in smoke testing)
#   Question 4               (when the .mmd isn't styled with section)
#   4.                       (some templates label answers as "4." inline)
_TOKEN_RE = re.compile(
    r"(?:\\section\*?\{\s*Question\s+(?P<q1>\d{1,3})\s*\})"
    r"|(?:\bQuestion\s+(?P<q2>\d{1,3})\b)"
    r"|(?:(?:^|[\s(])(?P<q3>\d{1,3})\s*[.)])"
    r"|(?P<url>https?://cdn\.mathpix\.com/[^\s)\"\]]+)",
    re.IGNORECASE | re.MULTILINE,
)

_MATHPIX_PAGE_RE = re.compile(
    r"-(?P<page>\d{1,3})\.(?:jpe?g|png)(?:\?|$)",
    re.IGNORECASE,
)


def _mathpix_url_page_number(url: str) -> Optional[int]:
    """Read Mathpix's one-based source page suffix (``...-07.jpg``)."""
    match = _MATHPIX_PAGE_RE.search(url or "")
    return int(match.group("page")) if match else None


def _mathpix_url_matches_diagram_crop(
    candidate: Dict[str, Any],
    question: int,
    url: str,
) -> bool:
    """Reject same-page figure URLs that are visibly outside an X diagram.

    Mathpix can emit small crops for ordinary answer boxes after a diagram it
    did not recognize.  Its URL exposes the crop coordinates, which makes
    those false associations deterministic to reject.  Unknown templates and
    URL forms remain compatible with the legacy behaviour.
    """
    template_id = str(candidate.get("template_id") or "")
    if template_id not in {"seamo_x_2026_a", "seamo_x_2026_b"}:
        return True

    normalized = (url or "").replace(r"\&", "&")
    values: Dict[str, int] = {}
    for key in ("top_left_x", "top_left_y", "height"):
        match = re.search(rf"(?:[?&]){key}=(\d+)", normalized)
        if match is None:
            return True
        values[key] = int(match.group(1))

    x = values["top_left_x"]
    y = values["top_left_y"]
    height = values["height"]
    if template_id == "seamo_x_2026_b" and question == 5:
        return x < 500 and 1750 <= y <= 2350 and height >= 150
    if template_id == "seamo_x_2026_a":
        if question == 4:
            return x < 500 and 1400 <= y <= 1900 and height >= 150
        if question == 6:
            return x < 500 and 1850 <= y <= 2400 and height >= 150
        if question == 9:
            return 500 <= x <= 1200 and 1150 <= y <= 1900 and height >= 150
    return True


def _apply_diagram_urls(
    candidates: List[Dict[str, Any]],
    markdown_text: str,
    diagram_qs: Optional[List[int]] = None,
) -> None:
    """Overlay diagram URLs without allowing a missed figure to shift pages.

    Mathpix URLs normally carry the one-based source-page number, which is the
    authoritative association. Alternate URL forms without that suffix retain
    the legacy sequential fallback for compatibility.

    Per-page diagram sets may differ in mixed-layout PDFs. Each candidate may
    carry ``diagram_qs``; otherwise ``diagram_qs`` (shared) is used for all.
    """
    if not candidates or not markdown_text:
        return

    per_page: List[set[int]] = []
    for cand in candidates:
        qs = cand.get("diagram_qs") if isinstance(cand, dict) else None
        if qs is None:
            qs = diagram_qs or []
        cv_qs = set(int(q) for q in (cand.get("diagram_cv_questions") or []))
        per_page.append(set(int(q) for q in qs) - cv_qs)

    if not any(per_page):
        return

    pages = len(candidates)
    filled_per_page: List[set[int]] = [set() for _ in range(pages)]
    page_i = 0
    active_q: Optional[int] = None
    urls_found = 0
    urls_applied = 0

    for m in _TOKEN_RE.finditer(markdown_text):
        qnum = m.group("q1") or m.group("q2") or m.group("q3")
        url = m.group("url")
        if qnum is not None:
            try:
                active_q = int(qnum)
            except ValueError:
                continue
        elif url is not None:
            urls_found += 1
            if active_q is None:
                continue  # spurious URL (e.g. Mathpix mis-detected handwriting)

            explicit_page = _mathpix_url_page_number(url)
            target_i = page_i if explicit_page is None else explicit_page - 1
            if (
                0 <= target_i < pages
                and active_q in per_page[target_i]
                and active_q not in filled_per_page[target_i]
                and _mathpix_url_matches_diagram_crop(
                    candidates[target_i], active_q, url,
                )
            ):
                answers = candidates[target_i].setdefault("answers", {})
                answers[str(active_q)] = url
                filled_per_page[target_i].add(active_q)
                urls_applied += 1
            active_q = None

            # URLs without Mathpix's source-page suffix retain the legacy
            # sequential fallback for synthetic fixtures / alternate CDN forms.
            diag_set = per_page[page_i]
            if (
                explicit_page is None
                and diag_set
                and filled_per_page[page_i] == diag_set
                and page_i < pages - 1
            ):
                page_i += 1
                # Skip empty (non-diagram) pages
                while page_i < pages - 1 and not per_page[page_i]:
                    page_i += 1

    expected = sum(len(s) for s in per_page)
    logger.info(
        "MATHPIX overlay: urls_found=%d urls_applied=%d expected=%d",
        urls_found, urls_applied, expected,
    )
    if urls_applied < expected:
        missing = [
            (i + 1, sorted(per_page[i] - filled_per_page[i]))
            for i in range(pages) if filled_per_page[i] != per_page[i]
        ]
        logger.warning("MATHPIX missing diagram URLs per page: %s", missing[:10])
