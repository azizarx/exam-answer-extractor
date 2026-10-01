"""Second opinion on MCQ answers the CV extractor would not commit to.

The CV bubble reader flags a question when the ink evidence is too weak to
separate the options — a faint pencil mark, a half-erased change of mind. Those
questions reach marking as ``needs_review`` and score zero, because auto-mark
must neither award nor deny marks on an untrusted reading.

This asks a vision model to look at that one question's row, cropped out of the
page, and say which box is filled. It deliberately reports *what is marked*, not
whether the answer is right: the deterministic marker still scores the result
against the key. The model therefore never sees the answer key and cannot
decide a mark, which keeps the existing audit trail and key-blindness intact.

Scope is MCQ only. An MCQ flag comes from CV, so a language model reading the
same pixels is a genuinely independent second opinion. Free-response flags come
from the extraction model's own uncertainty, and re-asking the same model on a
tighter crop is a much weaker signal.

Crucially the crop is self-validating. Every MCQ row carries its printed
question number, so the model is asked to report the number it can see. When
the page's geometry is wrong — an unmapped layout, a failed anchor lock — the
crop lands on the wrong row, the reported number disagrees with the one we
asked about, and the answer is refused. That is what stops a mis-registered
sheet from turning an honest "needs review" into a confident wrong mark.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

import google.generativeai as genai
import numpy as np
from PIL import Image

from backend.config import get_settings
from backend.services.gemini_client import create_gemini_model
from backend.services.judge_json import loads_lenient
from backend.services.run_logger import llm_call

logger = logging.getLogger(__name__)

BLANK = "BLANK"
UNSURE = "UNSURE"
MULTIPLE = "MULTIPLE"

# How much of the neighbouring rows to include. Enough that the printed
# question number and the whole box are inside the crop even if registration
# drifted slightly, little enough that the target row is unambiguous.
_ROW_PAD_RATIO = 0.30
# The question number is printed to the left of the first option box.
_LABEL_PAD_PX = 190
_RIGHT_PAD_PX = 60


@dataclass(frozen=True)
class QuestionCrop:
    question: int
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)


def _mcq_sections(template: Any) -> List[Any]:
    return [s for s in template.sections if s.type == "mcq_grid"]


def _column_counts(grid: Any, count: int) -> List[int]:
    raw = [int(v) for v in (grid.questions_per_col or []) if int(v) > 0]
    return raw if raw and sum(raw) == count else [count]


def mcq_question_crop(
    template: Any,
    question: int,
    *,
    image_shape: Optional[Tuple[int, int]] = None,
    dx: int = 0,
    dy: int = 0,
) -> Optional[QuestionCrop]:
    """Absolute pixel box around one MCQ question's row, including its number.

    Mirrors ``mcq_extractor._compute_row_positions`` / ``_compute_col_positions``:
    explicit grid positions are absolute page coordinates shifted by the
    registration offset, and a multi-column grid stores its rows column-major.
    """
    for section in _mcq_sections(template):
        start = int(section.question_start)
        end = int(section.question_end)
        if not (start <= question <= end):
            continue
        grid = section.grid
        if grid is None:
            return None
        count = end - start + 1
        counts = _column_counts(grid, count)
        index = question - start

        # Locate the question within its visual column.
        visual_col = 0
        offset = index
        for col_index, n in enumerate(counts):
            if offset < n:
                visual_col = col_index
                break
            offset -= n
        else:
            return None

        rows = list(grid.row_positions or [])
        if rows:
            row_start = sum(counts[:visual_col])
            column_rows = [int(y + dy) for y in rows[row_start : row_start + counts[visual_col]]]
        else:
            base = int(section.region.y) + dy + int(grid.first_row_offset or 0)
            pitch = float(grid.row_pitch or 0)
            if pitch <= 0:
                return None
            row_start = sum(counts[:visual_col])
            column_rows = [int(base + (row_start + i) * pitch) for i in range(counts[visual_col])]
        if offset >= len(column_rows):
            return None
        row_y = column_rows[offset]

        if grid.col_positions and visual_col < len(grid.col_positions):
            col_xs = [int(x + dx) for x in grid.col_positions[visual_col]]
        else:
            return None
        if not col_xs:
            return None

        cell_w = int(grid.cell_width or 0)
        cell_h = int(grid.cell_height or 0)
        bub_h = int(grid.bubble_height or 0)
        pitch = float(grid.row_pitch or (bub_h + cell_h) or 60)
        pad = int(max(12, pitch * _ROW_PAD_RATIO))

        y1 = row_y - pad
        y2 = row_y + cell_h + bub_h + pad
        x1 = min(col_xs) - cell_w // 2 - _LABEL_PAD_PX
        x2 = max(col_xs) + cell_w // 2 + _RIGHT_PAD_PX

        if image_shape is not None:
            h, w = image_shape
            x1 = max(0, min(x1, w - 1))
            x2 = max(x1 + 1, min(x2, w))
            y1 = max(0, min(y1, h - 1))
            y2 = max(y1 + 1, min(y2, h))
        if x2 - x1 < 10 or y2 - y1 < 10:
            return None
        return QuestionCrop(question=question, x1=int(x1), y1=int(y1), x2=int(x2), y2=int(y2))
    return None


def mcq_options(template: Any, question: int) -> List[str]:
    for section in _mcq_sections(template):
        if int(section.question_start) <= question <= int(section.question_end):
            grid = section.grid
            return [str(o).strip().upper() for o in (grid.options or [])] if grid else []
    return []


def build_prompt(items: Sequence[Dict[str, Any]]) -> str:
    payload = json.dumps(
        {
            "questions": [
                {"question_number": i["question"], "options": i["options"]}
                for i in items
            ]
        },
        ensure_ascii=False,
    )
    return (
        "You are reading scanned multiple-choice exam answer sheets.\n"
        "\n"
        "Each image is one question's answer row, cropped from a candidate's "
        "sheet. The printed question number appears on the left of the row, "
        "followed by the option boxes in order.\n"
        "\n"
        "For each image report ONLY what you can see:\n"
        "  question_number_seen — the question number printed beside the row "
        "whose option boxes you are reading. A neighbouring row is often partly "
        "visible; report the number for the row you actually read, even if it "
        "differs from the label given to you. Use null if no number is legible.\n"
        "  marked_boxes — the list of EVERY option box carrying deliberate ink, "
        "in order. Enumerate them; do not choose between them. One entry means "
        "one marked box, two entries mean two. Use [] if none is marked, and "
        f"\"{UNSURE}\" instead of a list if you cannot tell.\n"
        "  confident — true only if you would stake the candidate's mark on it.\n"
        "\n"
        "These marks are often faint pencil, partially erased, or ticked "
        "outside the box. A light but deliberate shading still counts as a "
        "mark. An erasure smudge — grey graphite residue with no deliberate "
        "shape — does not.\n"
        "\n"
        "A box that has been filled and then crossed out, struck through or "
        "scribbled over IS still a marked box. Deciding that the candidate "
        "changed their mind is a human's job, not yours. So if two or more "
        f"boxes carry any deliberate ink, answer \"{MULTIPLE}\" — even when one "
        "of them is crossed out and only one looks like the intended answer.\n"
        "\n"
        f"If that row's option boxes are cut off or not visible, answer "
        f"\"{UNSURE}\". Only answer \"{BLANK}\" when you can see the row's boxes "
        "and none of them is marked.\n"
        "\n"
        "Do not guess. Reporting UNSURE is correct and useful; a wrong "
        "confident answer costs a candidate real marks.\n"
        "You are not told the correct answer and must not infer one.\n"
        "\n"
        'Return JSON: {"items": [{"question_number": <int as labelled>, '
        '"question_number_seen": <int|null>, '
        f'"marked_boxes": <["A","B",...] | [] | "{UNSURE}">, '
        '"confident": <bool>}}]}}\n'
        "\n"
        f"QUESTIONS:\n{payload}\n"
    )


def _collapse_marked(item: Dict[str, Any]) -> str:
    """Reduce an enumerated box list to one verdict.

    Asking for a category invited the model to pick a winner between a
    struck-out box and a fresh one; asking it to enumerate every inked box and
    collapsing the count here keeps that judgement out of its hands.
    ``marked`` is still honoured so an older-style reply remains readable.
    """
    boxes = item.get("marked_boxes")
    if isinstance(boxes, list):
        letters = [str(b).strip().upper() for b in boxes if str(b).strip()]
        if not letters:
            return BLANK
        if len(set(letters)) > 1:
            return MULTIPLE
        return letters[0]
    if isinstance(boxes, str):
        value = boxes.strip().upper()
        if value in {UNSURE, BLANK, MULTIPLE}:
            return value
        return value or UNSURE
    return str(item.get("marked") or UNSURE).strip().upper()


def parse_response(text: str) -> List[Dict[str, Any]]:
    data = loads_lenient(text)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("review-vision response missing items list")
    cleaned: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or "question_number" not in item:
            continue
        try:
            number = int(item["question_number"])
        except (TypeError, ValueError):
            continue
        seen_raw = item.get("question_number_seen")
        try:
            seen = int(seen_raw) if seen_raw is not None else None
        except (TypeError, ValueError):
            seen = None
        cleaned.append(
            {
                "question_number": number,
                "question_number_seen": seen,
                "marked": _collapse_marked(item),
                "confident": bool(item.get("confident")),
            }
        )
    if not cleaned:
        raise ValueError("review-vision response had no usable items")
    return cleaned


class ReviewReader(Protocol):
    def read(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return list of {question_number, question_number_seen, marked, confident}."""


def build_contents(items: Sequence[Dict[str, Any]]) -> List[Any]:
    contents: List[Any] = [build_prompt(items)]
    for item in items:
        contents.append(f"IMAGE: QUESTION {item['question']}")
        contents.append(item["image"])
    return contents


class GeminiReviewReader:
    def __init__(self, model=None, model_name: Optional[str] = None):
        if model is None:
            preferred = (get_settings().review_vision_model or "").strip()
            self.model, self.model_name = create_gemini_model(
                preferred_model=preferred or None,
            )
        else:
            self.model = model
            self.model_name = model_name or "injected"

    def read(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            return []
        response = llm_call(
            "review_vision",
            self.model,
            build_contents(items),
            genai.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
            logger,
        )
        return parse_response(getattr(response, "text", None) or "")


def _crop_image(bgr: np.ndarray, crop: QuestionCrop) -> Image.Image:
    patch = bgr[crop.y1 : crop.y2, crop.x1 : crop.x2]
    rgb = patch[:, :, ::-1] if patch.ndim == 3 else patch
    return Image.fromarray(np.ascontiguousarray(rgb)).convert("RGB")


def recheck_flagged_mcq(
    bgr: np.ndarray,
    template: Any,
    flagged: Sequence[Any],
    reader: ReviewReader,
    *,
    cv_answers: Optional[Dict[str, str]] = None,
    geometry_trusted: bool = True,
    page_num: int = 0,
    max_questions: int = 25,
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Resolve what we can of ``flagged``; return (accepted answers, audit).

    An answer is accepted only when the model is confident, the question number
    it read out of the crop matches the one we asked about, and the option it
    reports is in that question's domain. Everything else stays flagged.

    Nothing is asked when ``geometry_trusted`` is false. The crop is cut by the
    same geometry CV just said it does not trust, so it can land between rows —
    showing one row's boxes beside the next row's number. The printed-number
    check is the guard against that, but it depends on the model reporting what
    it sees rather than echoing the number it was given, and on a real page it
    was observed doing both for the same crop on different runs. A guard that
    holds only sometimes is not a basis for overturning a review flag.

    Questions where CV returned ``IN`` are not asked about at all. ``IN`` is a
    positive detection of ink in more than one box, and on real pages that is
    the struck-out-and-rechosen case: a candidate fills one box, crosses it
    out, and fills another. The model reliably picks one of the two and cannot
    tell which was cancelled — observed twice on one page, each time choosing
    the crossed-out box and so costing the candidate both the mark and the
    human review. Deciding which mark was withdrawn is a person's job.
    """
    numbers: List[int] = []
    for value in flagged:
        try:
            numbers.append(int(value))
        except (TypeError, ValueError):
            continue
    numbers = sorted(set(numbers))
    if not numbers:
        return {}, []

    mcq_numbers = set()
    for section in _mcq_sections(template):
        mcq_numbers.update(range(int(section.question_start), int(section.question_end) + 1))
    numbers = [q for q in numbers if q in mcq_numbers]
    if not numbers:
        return {}, []

    if not geometry_trusted:
        logger.info(
            "REVIEW_VISION[page %d] skipped: page geometry is not trusted, so "
            "crops cannot be placed reliably (%d questions left flagged)",
            page_num, len(numbers),
        )
        return {}, [
            {"question": q, "outcome": "page_geometry_not_trusted"}
            for q in numbers
        ]

    multi_ink = {
        q for q in numbers
        if str((cv_answers or {}).get(str(q), "")).strip().upper() == "IN"
    }
    numbers = [q for q in numbers if q not in multi_ink]
    if not numbers:
        return {}, [
            {"question": q, "outcome": "cv_detected_multiple_marks"}
            for q in sorted(multi_ink)
        ]
    if len(numbers) > max_questions:
        # A whole-page flag means the geometry itself is suspect; the crops
        # would be cut from the wrong rows. Refuse rather than ask.
        logger.info(
            "REVIEW_VISION[page %d] skipped: %d flagged questions exceeds %d "
            "(page-level geometry distrust)",
            page_num, len(numbers), max_questions,
        )
        return {}, []

    items: List[Dict[str, Any]] = []
    h, w = bgr.shape[:2]
    for q in numbers:
        crop = mcq_question_crop(template, q, image_shape=(h, w))
        if crop is None:
            continue
        items.append(
            {
                "question": q,
                "options": mcq_options(template, q),
                "image": _crop_image(bgr, crop),
                "crop": crop.box,
            }
        )
    if not items:
        return {}, []

    try:
        results = reader.read(items)
    except Exception as exc:
        # A failed second opinion must leave the first one standing.
        logger.warning("REVIEW_VISION[page %d] reader failed: %s", page_num, exc)
        return {}, []

    by_question = {int(r["question_number"]): r for r in results}
    accepted: Dict[str, str] = {}
    audit: List[Dict[str, Any]] = []
    for item in items:
        q = item["question"]
        result = by_question.get(q)
        options = set(item["options"])
        if result is None:
            audit.append({"question": q, "outcome": "no_response"})
            continue
        marked = result["marked"]
        seen = result["question_number_seen"]
        if not result["confident"]:
            reason = "not_confident"
        elif seen is None:
            reason = "no_question_number_visible"
        elif seen != q:
            reason = f"question_number_mismatch:saw_{seen}"
        elif marked == BLANK:
            # Never accepted. A crop that missed its row looks exactly like a
            # genuinely empty one, and a half-erased mark reads as either —
            # both were observed on real pages. The asymmetry settles it:
            # blank scores zero whether we accept it or leave it flagged, so
            # accepting gains the candidate nothing while removing the human
            # who might have awarded the mark. A letter is different: it
            # requires positively seeing a filled box.
            reason = "blank_not_accepted"
        elif marked in options:
            accepted[str(q)] = marked
            reason = "accepted"
        elif marked == MULTIPLE:
            # A real finding, but it scores zero either way and a human should
            # still see it, so it stays flagged rather than becoming a hard 0.
            reason = "multiple_marks"
        else:
            reason = f"unusable:{marked}"
        audit.append(
            {
                "question": q,
                "outcome": reason,
                "marked": marked,
                "seen": seen,
                "confident": result["confident"],
            }
        )

    for q in sorted(multi_ink):
        audit.append({"question": q, "outcome": "cv_detected_multiple_marks"})

    logger.info(
        "REVIEW_VISION[page %d] asked=%d accepted=%d refused=%d not_asked=%d",
        page_num, len(items), len(accepted), len(items) - len(accepted),
        len(multi_ink),
    )
    return accepted, audit
