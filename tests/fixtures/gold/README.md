# Gold labels for extraction accuracy

Hand-labeled ground truth used by `scripts/eval_gold.py` and the CV overlay QA loop.

## Layout

```
tests/fixtures/gold/seamo_2025_format_b/
  paper_a/   … paper_k/
    page_001.json     # required: answers (+ optional header)
    page_001.png      # optional: page image for overlay / local eval
    manifest.json     # optional: lists pages in this paper folder
```

Aim for ~5 pages per paper A–K, including known bad scans (low coverage, skew, timeouts).

## Page JSON schema

```json
{
  "page_id": "paper_c/page_001",
  "template_id": "seamo_2025_c_fb",
  "source": {
    "pdf": "backend/examples/seamo-2025-format-b.pdf",
    "page_index": 95
  },
  "header": {
    "candidate_name": "…",
    "candidate_number": "CAN…",
    "country": "…",
    "school": null
  },
  "answers": {
    "1": "A",
    "2": "BL",
    "21": "42"
  },
  "notes": "optional free text"
}
```

- Use `"BL"` for blank MCQ, `"IN"` for invalid multi-fill.
- Omit a question key only if it should not be scored in eval (prefer explicit `BL`).

## Example

See [`seamo_2025_format_b/paper_c/page_001.example.json`](seamo_2025_format_b/paper_c/page_001.example.json). Copy to `page_001.json` when labeling (examples are ignored by the harness).
