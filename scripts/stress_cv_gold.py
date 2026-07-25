#!/usr/bin/env python3
"""Stress-test CV MCQ on gold page images (rotate/crop/jpeg/downscale)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from backend.services.cv_overlay import overlay_page_file, render_mcq_overlay
from backend.services.gold_eval import load_gold_pages
from backend.services.mcq_extractor import extract_page
from backend.services.page_deskew import deskew_if_enabled
from backend.services.scan_stress import DEFAULT_STRESS
from backend.services.template_service import get_template_registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default="tests/fixtures/gold/seamo_2025_format_b")
    parser.add_argument("--out", default="storage/cv_overlays/stress")
    args = parser.parse_args()

    registry = get_template_registry()
    out_root = Path(args.out)
    summary = []

    for gold in load_gold_pages(args.gold):
        path = Path(gold["_path"])
        png = path.with_suffix(".png")
        if not png.exists():
            continue
        tid = gold.get("template_id")
        template = registry.get(tid) if tid else None
        if template is None:
            continue
        base = cv2.imread(str(png))
        if base is None:
            continue
        page_id = str(gold.get("page_id") or path.stem).replace("/", "_")
        for name, fn in DEFAULT_STRESS:
            degraded = fn(base)
            degraded, _ = deskew_if_enabled(degraded, enabled=True)
            result = extract_page(degraded, template, page_number=1, registry=registry)
            overlay = render_mcq_overlay(degraded, template, result, registry=registry)
            out = out_root / page_id / f"{name}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), overlay)
            row = {
                "page_id": page_id,
                "stress": name,
                "coverage": round(result.coverage, 3),
                "warning": result.warning,
                "anchor_score": round(result.anchor_score, 3),
                "dx": result.dx,
                "dy": result.dy,
            }
            summary.append(row)
            print(row)

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {len(summary)} stress overlays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
