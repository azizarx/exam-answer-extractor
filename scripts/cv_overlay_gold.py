#!/usr/bin/env python3
"""Burn CV MCQ overlays for gold page PNGs (visual geometry QA).

Expects gold folders with page_NNN.json + optional page_NNN.png.
Writes overlays to storage/cv_overlays/<page_id>.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.cv_overlay import overlay_page_file
from backend.services.gold_eval import load_gold_pages
from backend.services.template_service import get_template_registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default="tests/fixtures/gold/seamo_2025_format_b")
    parser.add_argument("--out", default="storage/cv_overlays")
    parser.add_argument("--no-deskew", action="store_true")
    args = parser.parse_args()

    registry = get_template_registry()
    out_root = Path(args.out)
    gold_pages = load_gold_pages(args.gold)
    if not gold_pages:
        print("No gold page_*.json found.", file=sys.stderr)
        return 2

    n = 0
    for gold in gold_pages:
        path = Path(gold["_path"])
        png = path.with_suffix(".png")
        if not png.exists():
            # also try jpg
            jpg = path.with_suffix(".jpg")
            if jpg.exists():
                png = jpg
            else:
                print(f"SKIP no image for {path}")
                continue
        tid = gold.get("template_id")
        if not tid:
            print(f"SKIP no template_id in {path}")
            continue
        template = registry.get(tid)
        if template is None:
            print(f"SKIP unknown template {tid}")
            continue
        page_id = str(gold.get("page_id") or path.stem).replace("/", "_")
        out = out_root / f"{page_id}.png"
        result = overlay_page_file(
            png, template, out, registry=registry, deskew=not args.no_deskew,
        )
        print(
            f"OK {page_id} cov={result.coverage:.2f} warn={result.warning} "
            f"anchor={result.anchor_score:.3f} -> {out}"
        )
        n += 1
    print(f"Wrote {n} overlays to {out_root}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
