"""
Template-driven extraction orchestrator.

One LLM call per page (Gemini 2.5 Pro, NO max_output_tokens cap so reasoning
tokens cannot starve the JSON), plus two deterministic overlays:

  * CV MCQ overlay   — when the template has any mcq_grid section, run the
                       pure-CV bubble extractor and overwrite the LLM's MCQ
                       answers with its results.
  * Mathpix overlay  — when the template flags any question as a diagram,
                       submit the full PDF to Mathpix /v3/pdf once. Don't
                       poll yet; let Gemini + CV do their work first. After
                       both finish, poll Mathpix, fetch the .mmd, and replace
                       each diagram question's answer with the figure CDN URL
                       that follows the `\\section*{Question N}` label for
                       that question on each page.

The user picks the template at upload time; every page uses it. If they
choose wrong, that's a user error, not a pipeline error — we still emit
whatever the three sources produced.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import google.generativeai as genai
import numpy as np
from PIL import Image

from backend.config import get_settings
from backend.services import mathpix_client
from backend.services.gemini_client import create_gemini_model
from backend.services.image_preprocessor import ImagePreprocessor
from backend.services.mcq_extractor import extract_page as mcq_extract_page
from backend.services.run_logger import llm_call
from backend.services.template_service import (
    AnswerSection,
    ExamTemplate,
    TemplateRegistry,
    get_template_registry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

class TemplateExtractor:
    """Trust-the-template extractor: LLM + CV + Mathpix overlays."""

    def __init__(self, template_id: str):
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
        """Extract all candidates from a PDF.

        Returns:
            {
              "candidates": [
                {"page_number": int, "candidate_name": str|None,
                 "candidate_number": str|None, "country": str|None,
                 "paper_type": str|None, "extra_fields": {...},
                 "answers": {"1": "A", ...}, "drawing_questions": {...}},
                ...
              ],
              "pages_processed": int,
              "pages_with_data": int,
              "processing_time": float,
            }
        """
        t_start = time.perf_counter()
        diagram_qs = _diagram_questions_for(self.template)
        run_cv_mcq = self.template.has_mcq
        run_mathpix = bool(diagram_qs) and bool(self.mathpix_app_id) and bool(self.mathpix_app_key)

        logger.info(
            "EXTRACT start pages=%d template=%s run_cv_mcq=%s run_mathpix=%s "
            "diagram_qs=%s",
            len(image_paths), self.template_id, run_cv_mcq, run_mathpix, diagram_qs,
        )

        # Per-submission anchor priming: crop the template's anchor region from
        # THIS upload's page 1 and stash it in the registry's in-process cache,
        # overwriting any stale crop (e.g. from a previous calibration run or
        # the on-disk *_anchor.png file). This is what the deleted
        # auto_extract_mcq() did, and it's the difference between 99% and 75%
        # coverage on real scans whose layout differs slightly from the
        # canonical reference crop.
        if run_cv_mcq and image_paths:
            self._prime_anchor_from_first_page(image_paths[0])

        # Submit Mathpix early. Don't poll yet — let Gemini + CV burn the time.
        pdf_id: Optional[str] = None
        mathpix_submit_error: Optional[str] = None
        if run_mathpix:
            try:
                pdf_id = mathpix_client.submit_pdf(
                    pdf_path,
                    app_id=self.mathpix_app_id,
                    app_key=self.mathpix_app_key,
                )
            except Exception as exc:
                mathpix_submit_error = f"{type(exc).__name__}: {exc}"
                logger.error("MATHPIX submit failed: %s", mathpix_submit_error)

        # Per-page LLM + CV work (parallel across pages).
        candidates: List[Dict[str, Any]] = [None] * len(image_paths)  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {
                pool.submit(self._extract_one_page, p, i + 1, run_cv_mcq): i
                for i, p in enumerate(image_paths)
            }
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    candidates[i] = fut.result()
                except Exception as exc:
                    logger.exception("PAGE[%d] worker crashed: %s", i + 1, exc)
                    candidates[i] = _empty_candidate(i + 1, errors=[str(exc)])

        # NOW poll Mathpix and apply diagram URL overlays.
        if pdf_id is not None:
            try:
                status = mathpix_client.poll_pdf(
                    pdf_id,
                    app_id=self.mathpix_app_id,
                    app_key=self.mathpix_app_key,
                    poll_interval=self.mathpix_poll_interval,
                    max_wait=self.mathpix_max_wait,
                )
                if status.get("status") == "completed":
                    mmd = mathpix_client.fetch_mmd(
                        pdf_id,
                        app_id=self.mathpix_app_id,
                        app_key=self.mathpix_app_key,
                    )
                    _apply_diagram_urls(candidates, mmd, diagram_qs)
                else:
                    logger.warning(
                        "MATHPIX status=%s; skipping diagram overlay (info=%s)",
                        status.get("status"), status.get("error_info"),
                    )
            except Exception as exc:
                logger.error("MATHPIX poll/fetch failed: %s: %s",
                             type(exc).__name__, exc)
        elif run_mathpix and mathpix_submit_error:
            logger.warning(
                "MATHPIX submit error earlier (%s); diagram answers will remain LLM defaults",
                mathpix_submit_error,
            )

        elapsed = time.perf_counter() - t_start
        pages_with_data = sum(
            1 for c in candidates
            if c and any(_is_real_answer(v) for v in (c.get("answers") or {}).values())
        )
        logger.info(
            "EXTRACT done template=%s pages=%d with_data=%d elapsed=%.2fs",
            self.template_id, len(candidates), pages_with_data, elapsed,
        )
        return {
            "candidates": candidates,
            "pages_processed": len(image_paths),
            "pages_with_data": pages_with_data,
            "processing_time": round(elapsed, 2),
        }

    # ----- per-page -----------------------------------------------------

    def _extract_one_page(
        self,
        image_path: str,
        page_num: int,
        run_cv_mcq: bool,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        # Load BGR for CV; will convert to PIL/RGB for the LLM.
        bgr = cv2.imread(image_path)
        if bgr is None:
            logger.error("PAGE[%d] failed to load image %s", page_num, image_path)
            return _empty_candidate(page_num, errors=[f"image_not_readable: {image_path}"])

        pil_image = _bgr_to_pil(bgr)
        if self._preprocess_enabled:
            try:
                pil_image = self._image_preprocessor.preprocess_pil_image(
                    pil_image, mode=self._preprocess_mode,
                )
            except Exception as exc:
                logger.warning("PAGE[%d] preprocess failed: %s", page_num, exc)

        logger.info("PAGE[%d] START run_cv_mcq=%s", page_num, run_cv_mcq)

        # Run LLM + CV in parallel.
        llm_data: Dict[str, Any] = {"header": {}, "answers": {}}
        mcq_answers: Dict[str, str] = {}
        mcq_warning: Optional[str] = None
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_llm = pool.submit(self._llm_extract_full, pil_image, page_num)
            f_mcq = None
            if run_cv_mcq:
                f_mcq = pool.submit(
                    mcq_extract_page, bgr, self.template, page_num, self.registry,
                )

            try:
                llm_data = f_llm.result()
            except Exception as exc:
                logger.error("PAGE[%d] LLM call crashed: %s", page_num, exc)

            if f_mcq is not None:
                try:
                    mcq_result = f_mcq.result()
                    mcq_answers = dict(mcq_result.answers)
                    mcq_warning = getattr(mcq_result, "warning", None) or getattr(mcq_result, "reason", None)
                    logger.info(
                        "PAGE[%d] CV_MCQ status=%s answers=%d warning=%s",
                        page_num, mcq_result.status, len(mcq_answers), mcq_warning,
                    )
                except Exception as exc:
                    logger.error("PAGE[%d] CV MCQ crashed: %s", page_num, exc)

        candidate = _merge_llm_and_mcq(llm_data, mcq_answers, page_num)
        if mcq_warning:
            candidate.setdefault("extra_fields", {})["mcq_warning"] = mcq_warning

        logger.info(
            "PAGE[%d] DONE answers=%d mcq_overrides=%d t=%.2fs",
            page_num, len(candidate.get("answers") or {}),
            len(mcq_answers), time.perf_counter() - t0,
        )
        return candidate

    # ----- anchor priming ------------------------------------------------

    def _prime_anchor_from_first_page(self, image_path: str) -> None:
        """Crop the template's anchor region from the first page image and
        stash it in the registry cache so per-page anchor matching uses a
        crop from THIS submission's scan (not a stale on-disk reference).
        """
        first_img = cv2.imread(image_path)
        if first_img is None:
            logger.warning("ANCHOR prime failed: cannot read first page %s", image_path)
            return
        region = self.template.anchor.region
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
            "ANCHOR primed template=%s from page 1 (%dx%d at %d,%d)",
            self.template_id, region.w, region.h, x, y,
        )

    # ----- LLM call -----------------------------------------------------

    def _llm_extract_full(self, pil_image: Image.Image, page_num: int) -> Dict[str, Any]:
        """One Gemini call returning the full page extraction as JSON.

        Critically passes NO max_output_tokens — gemini-2.5-pro uses reasoning
        tokens internally and a 1024 cap (the old default) starved every
        response in this session's bench. Letting the model use its default
        (~64k) is the deliberate fix.
        """
        prompt = _build_prompt(self.template)
        try:
            response = llm_call(
                f"full_page:{page_num}",
                self.model,
                [prompt, pil_image],
                genai.GenerationConfig(temperature=0.1),  # no max_output_tokens
                logger,
                image=pil_image,
            )
            text = (response.text or "").strip()
        except Exception as exc:
            logger.error("PAGE[%d] LLM call raised: %s", page_num, exc)
            return {"header": {}, "answers": {}}

        return _parse_extraction_json(text, page_num)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bgr_to_pil(bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


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


def _merge_llm_and_mcq(
    llm_data: Dict[str, Any],
    mcq_answers: Dict[str, str],
    page_num: int,
) -> Dict[str, Any]:
    header = llm_data.get("header") or {}
    answers = dict(llm_data.get("answers") or {})

    # CV MCQ always wins for any question it produced an answer for.
    for q, ans in (mcq_answers or {}).items():
        answers[str(q)] = ans

    # Pull the four known header fields up; everything else lands in extra_fields.
    KNOWN = ("candidate_name", "candidate_number", "country", "paper_type")
    candidate = {
        "page_number": page_num,
        "answers": answers,
        "drawing_questions": {},
    }
    for key in KNOWN:
        candidate[key] = header.get(key) or None
    extra = {k: v for k, v in header.items() if k not in KNOWN}
    candidate["extra_fields"] = extra
    return candidate


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(template: ExamTemplate) -> str:
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
    headers_block = "\n".join(header_lines) if header_lines else "  (none)"

    section_lines = []
    for s in template.sections:
        rng = f"Q{s.question_start}-{s.question_end}"
        if s.type == "mcq_grid":
            opts = ", ".join(s.grid.options) if s.grid and s.grid.options else "A,B,C,D"
            section_lines.append(
                f'  - {rng} mcq_grid (options: {opts}) → '
                f'return marked option letter, "BL" if blank, "IN" if multiple marks'
            )
        elif s.type == "numeric_grid":
            section_lines.append(
                f'  - {rng} numeric_grid → return the digits/characters written, e.g. "42" or "999"'
            )
        elif s.type == "open_response":
            section_lines.append(
                f'  - {rng} open_response → return the handwritten text verbatim'
            )
        elif s.type == "diagram":
            section_lines.append(
                f'  - {rng} diagram → return "" (a figure URL will replace this)'
            )
        else:
            section_lines.append(f"  - {rng} {s.type}")
        # Per-question overrides
        for q, ov in sorted(s.question_overrides.items()):
            if ov.type == "diagram":
                section_lines.append(f'      • Q{q} is a diagram → return ""')
    sections_block = "\n".join(section_lines) if section_lines else "  (none)"

    return (
        "You are extracting structured data from one page of an exam answer sheet.\n"
        "\n"
        "HEADER FIELDS (return under \"header\"):\n"
        f"{headers_block}\n"
        "\n"
        "ANSWERS (return under \"answers\", keys are question numbers as strings):\n"
        f"{sections_block}\n"
        "\n"
        "Rules:\n"
        '  - For mcq_grid: a "marked" option has a clear deliberate fill/X/tick/dot/shading.\n'
        '    "BL" = no marks anywhere. "IN" = two or more options clearly marked.\n'
        '  - For numeric_grid: read the digits/characters the candidate wrote in the boxes.\n'
        "    Return them concatenated as a string, e.g. \"99990\".\n"
        "  - For open_response: transcribe the handwriting as-is.\n"
        "  - For diagram: return an empty string \"\". Do NOT describe the drawing.\n"
        "  - For header fields that are blank/illegible, return null (not an empty string).\n"
        "\n"
        "Return ONLY valid JSON. No code fences, no commentary.\n"
        'Schema: {"header": {...}, "answers": {"1": "A", "2": "BL", ...}}\n'
    )


def _parse_extraction_json(text: str, page_num: int) -> Dict[str, Any]:
    """Parse the LLM's JSON response. Strip code fences if present.

    Returns {"header": {...}, "answers": {...}} on success, or empty dicts
    on parse failure (the failure is already logged via run_logger).
    """
    if not text:
        logger.error("PAGE[%d] LLM returned empty text", page_num)
        return {"header": {}, "answers": {}}

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
        return {"header": {}, "answers": {}}

    if not isinstance(data, dict):
        logger.error("PAGE[%d] LLM JSON is not a dict: %r", page_num, type(data))
        return {"header": {}, "answers": {}}

    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    answers_raw = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    # Coerce keys/values to strings
    answers = {str(k): ("" if v is None else str(v)) for k, v in answers_raw.items()}
    return {"header": header, "answers": answers}


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


def _apply_diagram_urls(
    candidates: List[Dict[str, Any]],
    markdown_text: str,
    diagram_qs: List[int],
) -> None:
    """Walk Mathpix's markdown output; for each `Question N` label whose N is
    in `diagram_qs`, the next `cdn.mathpix.com` URL becomes that question's
    answer for the current page. Page transitions are implicit: once every
    diagram question is filled for the current page, advance to the next.

    Mathpix may emit URLs for non-diagram questions too (it crops every answer
    region). Those URLs are dropped because the active question pointer is
    only set when we see a label for a diagram question.
    """
    if not candidates or not diagram_qs or not markdown_text:
        return

    diag_set = set(diagram_qs)
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
                q = int(qnum)
            except ValueError:
                continue
            if q in diag_set and q not in filled_per_page[page_i]:
                active_q = q
            else:
                # Non-diagram question label clears the active pointer so the
                # next URL doesn't get stolen.
                active_q = None
        elif url is not None:
            urls_found += 1
            if active_q is None:
                continue  # spurious URL (e.g. Mathpix mis-detected handwriting)
            answers = candidates[page_i].setdefault("answers", {})
            answers[str(active_q)] = url
            filled_per_page[page_i].add(active_q)
            urls_applied += 1
            active_q = None
            if filled_per_page[page_i] == diag_set and page_i < pages - 1:
                page_i += 1

    expected = len(diagram_qs) * pages
    logger.info(
        "MATHPIX overlay: urls_found=%d urls_applied=%d expected=%d diagram_qs=%s",
        urls_found, urls_applied, expected, diagram_qs,
    )
    if urls_applied < expected:
        missing = [
            (i + 1, sorted(diag_set - filled_per_page[i]))
            for i in range(pages) if filled_per_page[i] != diag_set
        ]
        logger.warning("MATHPIX missing diagram URLs per page: %s", missing[:10])
