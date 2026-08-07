#!/usr/bin/env python3
"""Evaluate predicted answer JSON / CandidateResult dumps against gold labels.

Examples:
  .venv/bin/python scripts/eval_gold.py \\
      --gold tests/fixtures/gold/seamo_2025_format_b \\
      --predictions path/to/predictions.json

predictions.json format:
  {
    "paper_c/page_001": {
      "answers": {"1": "A", ...},
      "answer_trust": {"1": "trusted"},
      "needs_review_questions": []
    }
  }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.gold_eval import evaluate_page, load_gold_pages, summarize


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        default="tests/fixtures/gold/seamo_2025_format_b",
        help="Gold root directory",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="JSON map page_id → {answers, answer_trust?, needs_review_questions?}",
    )
    args = parser.parse_args()

    preds = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    gold_pages = load_gold_pages(args.gold)
    if not gold_pages:
        print(f"No gold pages under {args.gold} (add page_*.json labels).", file=sys.stderr)
        return 2

    evaluated = []
    for gold in gold_pages:
        page_id = str(gold.get("page_id") or "")
        pred = preds.get(page_id) or preds.get(gold.get("_path", "")) or {}
        evaluated.append(
            evaluate_page(
                gold,
                pred.get("answers") or {},
                answer_trust=pred.get("answer_trust"),
                needs_review_questions=pred.get("needs_review_questions"),
            )
        )

    summary = summarize(evaluated)
    print(json.dumps(summary, indent=2))
    for page in evaluated:
        if page.silent_wrong:
            wrongs = [q for q in page.questions if q.bucket == "silent_wrong"]
            print(f"SILENT_WRONG {page.page_id}: " + ", ".join(
                f"Q{q.question} gold={q.gold} pred={q.predicted}" for q in wrongs
            ))
    return 0 if summary["meets_zero_silent_wrong"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
