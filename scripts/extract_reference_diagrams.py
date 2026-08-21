#!/usr/bin/env python
"""Extract the answer key's reference drawing for every diagram question.

Diagram questions cannot be marked by comparing strings, so the marking-time
vision judge needs the key's own drawing to compare a candidate's against.  The
key PDFs embed those drawings as images inside the answer table, one per
diagram question, which makes them extractable losslessly rather than
re-rendered.

Driven entirely by the manifests: a manifest names its source PDF and which of
its questions are diagrams, so nothing here is hardcoded per paper.  Rerun after
changing a key PDF; output is deterministic and committed to the repo.

    python scripts/extract_reference_diagrams.py [--check]

``--check`` verifies the committed files match what the PDFs contain and exits
non-zero on drift, without writing anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSWER_KEYS_ROOT = REPO_ROOT / "answer_keys"
OUTPUT_DIR = ANSWER_KEYS_ROOT / "reference_diagrams"
PROVENANCE_PATH = OUTPUT_DIR / "provenance.json"

_LABEL_RE = re.compile(r"^Q(\d+)$")
# The paper's logo is embedded like any other image; a real answer drawing sits
# under a question label, which the logo never does.
_MAX_LABEL_GAP_PT = 60.0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _diagram_questions(manifest: dict[str, Any]) -> list[int]:
    return [
        int(q["number"])
        for q in manifest.get("questions", [])
        if q.get("type") == "diagram"
    ]


def _question_labels(page: pymupdf.Page) -> dict[int, pymupdf.Rect]:
    labels: dict[int, pymupdf.Rect] = {}
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        match = _LABEL_RE.match(word.strip())
        if match:
            labels[int(match.group(1))] = pymupdf.Rect(x0, y0, x1, y1)
    return labels


def _image_for_question(
    doc: pymupdf.Document, page: pymupdf.Page, label: pymupdf.Rect
) -> tuple[int, pymupdf.Rect] | None:
    """The embedded image sitting directly beneath ``label``, if any."""
    best: tuple[float, int, pymupdf.Rect] | None = None
    for info in page.get_images(full=True):
        xref = info[0]
        for rect in page.get_image_rects(xref):
            horizontally_aligned = (
                rect.x0 < label.x1 and rect.x1 > label.x0
            )
            gap = rect.y0 - label.y1
            if not horizontally_aligned or gap < 0 or gap > _MAX_LABEL_GAP_PT:
                continue
            if best is None or gap < best[0]:
                best = (gap, xref, rect)
    if best is None:
        return None
    return best[1], best[2]


def extract(check_only: bool) -> int:
    entries: list[dict[str, Any]] = []
    problems: list[str] = []

    for manifest_path in sorted(ANSWER_KEYS_ROOT.rglob("*.json")):
        if manifest_path.parent == OUTPUT_DIR:
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        questions = _diagram_questions(manifest)
        if not questions:
            continue

        template_id = manifest["template_id"]
        source_rel = manifest["source"]["filename"]
        source_path = ANSWER_KEYS_ROOT / source_rel
        if not source_path.exists():
            problems.append(f"{template_id}: source PDF missing at {source_rel}")
            continue

        source_sha = _sha256(source_path.read_bytes())
        if source_sha != manifest["source"]["sha256"]:
            problems.append(
                f"{template_id}: source PDF sha256 does not match its manifest"
            )
            continue

        with pymupdf.open(source_path) as doc:
            for question in questions:
                found = None
                for page in doc:
                    labels = _question_labels(page)
                    label = labels.get(question)
                    if label is None:
                        continue
                    found = _image_for_question(doc, page, label)
                    if found is not None:
                        break
                if found is None:
                    problems.append(
                        f"{template_id} Q{question}: no drawing found under its label"
                    )
                    continue

                xref, _rect = found
                image = doc.extract_image(xref)
                data = image["image"]
                name = f"{template_id}_q{question}.png"
                destination = OUTPUT_DIR / name

                if check_only:
                    if not destination.exists():
                        problems.append(f"{name}: missing")
                    elif _sha256(destination.read_bytes()) != _sha256(data):
                        problems.append(f"{name}: differs from the key PDF")
                else:
                    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)

                entries.append(
                    {
                        "template_id": template_id,
                        "question": question,
                        "file": name,
                        "width": image["width"],
                        "height": image["height"],
                        "source_pdf": source_rel,
                        "source_sha256": source_sha,
                        "image_sha256": _sha256(data),
                    }
                )
                print(
                    f"  {template_id} Q{question}: {image['width']}x{image['height']} -> {name}"
                )

    if not check_only and entries:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        PROVENANCE_PATH.write_text(
            json.dumps(
                sorted(entries, key=lambda e: (e["template_id"], e["question"])),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if problems:
        print("\nPROBLEMS:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"\n{len(entries)} reference diagram(s) {'checked' if check_only else 'written'}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed files match the key PDFs instead of writing",
    )
    args = parser.parse_args()
    return extract(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
