# Paper C gold page selection

Source PDF: `backend/examples/seamo-2025-format-b-split/seamo-2025-format-b-paper-c.pdf` (60 pages).
Template: `seamo_2025_c_fb`.

Paper C historically had the **worst format-B MCQ coverage**. All five fixtures are intentionally hard scans (no clean baseline).

## Method

1. Rendered all 60 pages at 300 DPI via `backend.services.pdf_to_images.get_pdf_converter`.
2. Heuristic quality: grayscale mean/stddev, Laplacian variance, `page_deskew.estimate_skew_degrees`, bright/dark pixel fractions → composite **badness**.
3. Ran `mcq_extractor.extract_page` with `seamo_2025_c_fb` on every page; preferred low **cv_coverage**, `huge_dy` / `low_coverage` warnings, and high badness.
4. Diversified failure modes (washout, false anchor, misaligned grid, faint/messy fills) rather than five near-duplicates.

Corpus CV summary: mean coverage ≈ 0.787, min = 0.20.

## Chosen pages

| Fixture | 0-based idx | 1-based PDF page | Primary reason | std | lap | skew° | badness | CV cov | anchor | dy | warning |
|---------|-------------|------------------|----------------|-----|-----|-------|---------|--------|--------|----|---------|
| page_001 | 14 | 15 | Worst washout/low-contrast/blur (std=35.5, lap=54) | 35.46 | 54.25 | 0.0 | 56.77 | 0.55 | 0.7906 | 0 | huge_dy |
| page_002 | 54 | 55 | Lowest CV coverage (0.20) despite many visible fills  | 54.71 | 74.38 | 0.0 | 34.04 | 0.2 | 0.8752 | -29 | low_coverage |
| page_003 | 48 | 49 | Low CV coverage (0.25) | 51.29 | 85.29 | 0.0 | 34.94 | 0.25 | 0.8906 | -38 | low_coverage |
| page_004 | 4 | 5 | Second-worst heuristic (low contrast std=46) | 46.08 | 79.81 | 0.0 | 45.4 | 0.85 | 0.7461 | 0 | huge_dy |
| page_005 | 15 | 16 | Low CV coverage (0.35) | 49.41 | 68.06 | 0.0 | 40.71 | 0.35 | 0.8566 | 10 | low_coverage |

## Per-fixture notes

### page_001 — split index `14`

Worst washout/low-contrast/blur (std=35.5, lap=54); stroke-style MCQ marks; CV huge_dy + cov=0.55.

- Heuristics: mean=241.99, std=35.46, laplacian=54.25, skew_deg=0.0, pct_bright=0.8917, badness=56.77
- CV: coverage=0.55, anchor=0.7906, offset=(0,0), warning=huge_dy, non-BL answers=11

### page_002 — split index `54`

Lowest CV coverage (0.20) despite many visible fills — grid misalignment / low_coverage warning.

- Heuristics: mean=235.1, std=54.71, laplacian=74.38, skew_deg=0.0, pct_bright=0.8593, badness=34.04
- CV: coverage=0.2, anchor=0.8752, offset=(-44,-29), warning=low_coverage, non-BL answers=4

### page_003 — split index `48`

Low CV coverage (0.25); skewed grid; messy/faint pencil fills; low_coverage warning.

- Heuristics: mean=235.56, std=51.29, laplacian=85.29, skew_deg=0.0, pct_bright=0.8473, badness=34.94
- CV: coverage=0.25, anchor=0.8906, offset=(-66,-38), warning=low_coverage, non-BL answers=5

### page_004 — split index `4`

Second-worst heuristic (low contrast std=46); clockwise skew; CV huge_dy (false anchor lock).

- Heuristics: mean=240.17, std=46.08, laplacian=79.81, skew_deg=0.0, pct_bright=0.8931, badness=45.4
- CV: coverage=0.85, anchor=0.7461, offset=(0,0), warning=huge_dy, non-BL answers=17

### page_005 — split index `15`

Low CV coverage (0.35); washout; faint/slash marks mixed with fills; low_coverage warning.

- Heuristics: mean=237.45, std=49.41, laplacian=68.06, skew_deg=0.0, pct_bright=0.8701, badness=40.71
- CV: coverage=0.35, anchor=0.8566, offset=(-1,10), warning=low_coverage, non-BL answers=7

## Stub labels

`page_00N.json` files have empty `header` / `answers` for later hand-labeling. Do not treat them as ground truth yet.
