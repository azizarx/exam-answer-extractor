"""Compare extraction results against hand-labeled gold fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_GOLD_ROOT = Path("tests/fixtures/gold")


@dataclass
class QuestionEval:
    question: str
    gold: Optional[str]
    predicted: Optional[str]
    trust: str  # trusted | needs_review | missing
    bucket: str  # exact_match | blank_ok | silent_wrong | flagged_review | missing_gold


@dataclass
class PageEval:
    page_id: str
    template_id: Optional[str]
    questions: List[QuestionEval] = field(default_factory=list)

    @property
    def silent_wrong(self) -> int:
        return sum(1 for q in self.questions if q.bucket == "silent_wrong")

    @property
    def flagged_review(self) -> int:
        return sum(1 for q in self.questions if q.bucket == "flagged_review")

    @property
    def exact_match(self) -> int:
        return sum(1 for q in self.questions if q.bucket == "exact_match")


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def load_gold_pages(gold_root: Path | str = DEFAULT_GOLD_ROOT) -> List[Dict[str, Any]]:
    """Load all ``page_*.json`` labels (skip ``*.example.json``)."""
    root = Path(gold_root)
    pages: List[Dict[str, Any]] = []
    if not root.exists():
        return pages
    for path in sorted(root.rglob("page_*.json")):
        if path.name.endswith(".example.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path)
        pages.append(data)
    return pages


def evaluate_page(
    gold: Dict[str, Any],
    predicted_answers: Dict[str, Any],
    *,
    answer_trust: Optional[Dict[str, str]] = None,
    needs_review_questions: Optional[Iterable[str]] = None,
) -> PageEval:
    """Score one page. ``silent_wrong`` = wrong prediction that was trusted."""
    trust_map = {str(k): str(v) for k, v in (answer_trust or {}).items()}
    review_set = {str(q) for q in (needs_review_questions or [])}
    gold_answers = gold.get("answers") or {}
    pred = {str(k): v for k, v in (predicted_answers or {}).items()}

    result = PageEval(
        page_id=str(gold.get("page_id") or gold.get("_path") or "unknown"),
        template_id=gold.get("template_id"),
    )

    for q, gold_raw in gold_answers.items():
        qk = str(q)
        g = _norm(gold_raw)
        p = _norm(pred.get(qk))
        trust = trust_map.get(qk, "needs_review" if qk in review_set else "trusted")
        if trust == "needs_review" or qk in review_set:
            bucket = "flagged_review"
        elif g in ("", "BL") and p in ("", "BL"):
            bucket = "blank_ok"
        elif g == p:
            bucket = "exact_match"
        else:
            bucket = "silent_wrong"
        result.questions.append(
            QuestionEval(
                question=qk,
                gold=g or None,
                predicted=p or None,
                trust=trust,
                bucket=bucket,
            )
        )
    return result


def summarize(pages: List[PageEval]) -> Dict[str, Any]:
    silent = sum(p.silent_wrong for p in pages)
    flagged = sum(p.flagged_review for p in pages)
    exact = sum(p.exact_match for p in pages)
    total_q = sum(len(p.questions) for p in pages)
    return {
        "pages": len(pages),
        "questions": total_q,
        "exact_match": exact,
        "flagged_review": flagged,
        "silent_wrong": silent,
        "meets_zero_silent_wrong": silent == 0,
    }
