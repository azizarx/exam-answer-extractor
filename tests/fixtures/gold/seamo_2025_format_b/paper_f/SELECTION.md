# Paper F gold page selection

Source: `backend/examples/seamo-2025-format-b-split/seamo-2025-format-b-paper-f.pdf` (21 pages).
Template: `seamo_2025_f_fb`. Rendered at 300 DPI via `get_pdf_converter`.

## Chosen pages (exactly 5)

| fixture | 0-based idx | 1-based | full-PDF source_page | role | sharp | contrast | skew° | CV cov | CV anchor | CV dy | badness |
|---------|-------------|---------|----------------------|------|-------|----------|-------|--------|-----------|-------|---------|
| page_001 | 7 | 8 | 184 | crop / horizontal shift | 108.5 | 57.3 | 0.0 | 1.0 | 0.8732 | -4.0 | 2.105 |
| page_002 | 9 | 10 | 186 | severe blur / noise | 35.5 | 50.2 | 0.0 | 1.0 | 0.8539 | 33.0 | 2.859 |
| page_003 | 13 | 14 | 190 | weak CV anchor / large |dy| | 98.3 | 52.8 | 0.0 | 1.0 | 0.8167 | -77.0 | 2.156 |
| page_004 | 17 | 18 | 194 | clean baseline | 193.6 | 56.2 | 0.0 | 1.0 | 0.9039 | 5.0 | 1.002 |
| page_005 | 18 | 19 | 195 | low contrast + CV stress | 62.4 | 49.0 | 0.0 | 0.9 | 0.8055 | 0.0 | 2.528 |

## Why these

- **page_001** (idx `7`): Highest border-vs-center crop_score (0.71); CV dx=-61 (lateral shift). Mild blur (sharp=108.5). Good crop/misalignment stress case.
- **page_002** (idx `9`): Worst Laplacian sharpness in the split (sharp=35.5); grainy/noisy scan with slight clockwise skew. Primary blur fixture.
- **page_003** (idx `13`): Low-ish anchor score (0.817) with dy=-77; soft/noisy scan. Alignment stress for format-B MCQ.
- **page_004** (idx `17`): Best quality in the 21-page split (sharp=193.6, contrast=56.2, CV cov=1.0, anchor=0.90). Clean baseline for Paper F format B.
- **page_005** (idx `18`): Lowest contrast (49.0), blurry (sharp=62.4), CV coverage 0.90 with huge_dy warning and weaker anchor (0.805). Low-contrast / weak-CV fixture.

## Method

1. Rendered all 21 pages with `backend.services.pdf_to_images.get_pdf_converter(dpi=300)`.
2. Heuristics per page: Laplacian variance (sharpness), grayscale stddev (contrast), mean brightness,
   `page_deskew.estimate_skew_degrees`, 1–99% dynamic range, border-vs-center mean (crop proxy).
3. Optional CV: `mcq_extractor.extract_page` with template `seamo_2025_f_fb` → coverage / anchor / dx/dy / warning.
4. Composite `badness` ranked worst-first; picked diverse shit-scan modes + one clean baseline.
5. Stub JSON left with empty `header`/`answers` for later hand-labeling.

## Notes

- Hough skew estimates were ~0° for all pages; visual inspection still shows mild clockwise tilt on several picks.
- All pages are slightly smaller than the template reference (2481×3508); dims vary ~2458–2470 × 3491–3504.
- CV still reaches high coverage on most pages; idx 18 is the weakest CV case (cov=0.90, `huge_dy` warning).

