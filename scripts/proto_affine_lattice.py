#!/usr/bin/env python3
"""Gold eval for per-page affine lattice alignment (production extract_page).

Usage:
  .venv/bin/python scripts/proto_affine_lattice.py
  .venv/bin/python scripts/proto_affine_lattice.py --oracle  # fit vs annotations
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from backend.services.gold_eval import evaluate_page, load_gold_pages, summarize
from backend.services.mcq_extractor import (
    _match_anchor,
    ambiguous_mcq_questions,
    extract_page,
)
from backend.services.mcq_lattice_align import align_mcq_section
from backend.services.page_deskew import deskew_if_enabled
from backend.services.template_extractor import MCQ_TRUST_WARNINGS
from backend.services.template_service import ScoringParams, get_template_registry


def _oracle_scale(ann_path: Path, tmpl_cols, tmpl_fill_y) -> dict:
    if not ann_path.is_file():
        return {}
    data = json.loads(ann_path.read_text(encoding="utf-8"))
    cols = data["raw_clicks"]["col_xs"]
    fills = data["raw_clicks"]["fill_top_ys"]
    if len(cols) < 2 or len(fills) < 2 or len(tmpl_cols) < 2 or len(tmpl_fill_y) < 2:
        return {}
    sx = (cols[-1] - cols[0]) / (tmpl_cols[-1] - tmpl_cols[0])
    sy = (fills[-1] - fills[0]) / (tmpl_fill_y[-1] - tmpl_fill_y[0])
    return {"oracle_sx": round(sx, 3), "oracle_sy": round(sy, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        default="tests/fixtures/gold/seamo_2025_format_b",
    )
    parser.add_argument(
        "--out",
        default="storage/annotations/gold_cv_preds_affine_proto.json",
    )
    parser.add_argument(
        "--oracle",
        action="store_true",
        help="Print annotation-implied sx/sy for diagnostics",
    )
    args = parser.parse_args()

    registry = get_template_registry()
    preds = {}
    fit_rows = []

    for gold in load_gold_pages(args.gold):
        path = Path(gold["_path"])
        png = path.with_suffix(".png")
        if not png.exists():
            continue
        tid = gold.get("template_id")
        template = registry.get(tid) if tid else None
        if template is None:
            continue

        page_id = str(gold.get("page_id") or path.stem)
        img = cv2.imread(str(png))
        if img is None:
            continue
        img, _ = deskew_if_enabled(img, enabled=True)
        adapted, _scale = template.adapted_to_image(img)

        # Production path (includes lattice + trust gates).
        result = extract_page(
            img, adapted, page_number=1, registry=registry, deskew=False,
        )
        mode = "lattice" if result.overlay_mcq_sections else "translation"

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        mcq_sec = next((s for s in adapted.sections if s.type == "mcq_grid"), None)
        fit_info: dict = {"ok": False}
        if mcq_sec is not None and mcq_sec.grid is not None:
            thr = (mcq_sec.scoring or ScoringParams()).binary_threshold
            _anc, raw_dx, raw_dy, _ = _match_anchor(img, adapted, registry)
            if abs(raw_dx) > 300 or abs(raw_dy) > 300:
                dx_prior, dy_prior = 0.0, 0.0
            else:
                dx_prior, dy_prior = float(raw_dx), float(raw_dy)
            _new_sec, fit = align_mcq_section(
                gray, mcq_sec, binary_threshold=thr,
                dx_prior=dx_prior, dy_prior=dy_prior,
            )
            oracle = {}
            if args.oracle and mcq_sec.grid.col_positions:
                paper, page = page_id.split("/") if "/" in page_id else ("", page_id)
                ann = Path("storage/annotations") / f"{paper}_{page}_grid.json"
                ch = mcq_sec.grid.cell_height or 36
                bh = mcq_sec.grid.bubble_height or 20
                tmpl_fill = [y + ch + bh / 2 for y in mcq_sec.grid.row_positions]
                oracle = _oracle_scale(ann, mcq_sec.grid.col_positions[0], tmpl_fill)
            fit_info = {
                "ok": fit.ok,
                "sx": round(fit.sx, 4),
                "sy": round(fit.sy, 4),
                "tx": round(fit.tx, 2),
                "ty": round(fit.ty, 2),
                "col_rmse": round(fit.col_rmse, 2),
                "row_rmse": round(fit.row_rmse, 2),
                "n_col_peaks": fit.n_col_peaks,
                "n_row_peaks": fit.n_row_peaks,
                "warning": fit.warning,
                **oracle,
            }

        answers = {str(k): v for k, v in (result.answers or {}).items()}
        for q in range(1, 21):
            answers.setdefault(str(q), "BL")

        review = set()
        warn = result.warning
        if warn in MCQ_TRUST_WARNINGS:
            review |= {str(q) for q in range(1, 21)}
        scoring = (mcq_sec.scoring if mcq_sec else None) or ScoringParams()
        for q in ambiguous_mcq_questions(
            result,
            min_ratio=float(scoring.min_ratio),
            min_ink_pixels=int(scoring.min_ink_pixels),
        ):
            review.add(str(q))

        trust = {q: ("needs_review" if q in review else "trusted") for q in answers}
        preds[page_id] = {
            "answers": answers,
            "answer_trust": trust,
            "needs_review_questions": sorted(review, key=lambda x: int(x)),
            "mcq_warning": warn,
            "mode": mode,
            "fit": fit_info,
            "coverage": round(result.coverage, 3),
            "dx": result.dx,
            "dy": result.dy,
        }

        fit_rows.append(
            f"{page_id:20s} mode={mode:11s} "
            f"sx={fit_info.get('sx', 0):.3f} sy={fit_info.get('sy', 0):.3f} "
            f"cov={result.coverage:.2f} warn={warn} "
            f"dx={result.dx:4d} dy={result.dy:4d} rev={len(review):2d}"
            + (
                f"  oracle_sx={fit_info.get('oracle_sx')} "
                f"oracle_sy={fit_info.get('oracle_sy')}"
                if args.oracle and fit_info.get("oracle_sx") is not None
                else ""
            )
        )

    for line in fit_rows:
        print(line)

    evaluated = []
    raw_ok = raw_tot = 0
    by_paper: dict = {}
    for gold in load_gold_pages(args.gold):
        page_id = str(gold.get("page_id") or "")
        pred = preds.get(page_id) or {}
        gold_mcq = dict(gold)
        gold_mcq["answers"] = {
            k: v
            for k, v in (gold.get("answers") or {}).items()
            if str(k).isdigit() and 1 <= int(k) <= 20
        }
        evaluated.append(
            evaluate_page(
                gold_mcq,
                pred.get("answers") or {},
                answer_trust=pred.get("answer_trust"),
                needs_review_questions=pred.get("needs_review_questions"),
            )
        )
        paper = page_id.split("/")[0] if "/" in page_id else page_id
        for q, g in gold_mcq["answers"].items():
            raw_tot += 1
            ok = (
                str(g).strip().upper()
                == str((pred.get("answers") or {}).get(str(q), "")).strip().upper()
            )
            raw_ok += int(ok)
            st = by_paper.setdefault(paper, [0, 0])
            st[0] += int(ok)
            st[1] += 1

    summary = summarize(evaluated)
    n_lattice = sum(1 for p in preds.values() if p.get("mode") == "lattice")
    n_whole_review = sum(
        1 for p in preds.values() if len(p.get("needs_review_questions") or []) >= 20
    )
    print(json.dumps(summary, indent=2))
    print(f"raw letter match: {raw_ok}/{raw_tot} = {100 * raw_ok / max(1, raw_tot):.1f}%")
    print(f"lattice_ok_pages: {n_lattice}/{len(preds)}  whole_page_review: {n_whole_review}")
    for paper, (ok, tot) in sorted(by_paper.items()):
        print(f"  {paper}: {ok}/{tot} = {100 * ok / tot:.1f}%")
    for page in evaluated:
        if page.silent_wrong:
            wrongs = [q for q in page.questions if q.bucket == "silent_wrong"]
            print(
                "SILENT",
                page.page_id,
                [(q.question, q.gold, q.predicted) for q in wrongs],
            )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(preds, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0 if summary["meets_zero_silent_wrong"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
