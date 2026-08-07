# Paper B gold fixture selection

Source PDF: `backend/examples/seamo-2025-format-b-split/seamo-2025-format-b-paper-b.pdf` (47 pages).
Rendered at **300 DPI** via `backend.services.pdf_to_images.get_pdf_converter`.
Template: `seamo_2025_b_fb`.

Selection bias: prefer problematic scans (blur / washout / wrinkle / skew / low MCQ),
plus one cleaner baseline. Paper B was relatively healthy overall (cov mean ~0.86 in sub89),
so weak pages were chosen from heuristic quality scores + pipeline MCQ/anchor failures.

| Fixture | 0-based idx | 1-based page | Reason | mean | std | lap | edge | skew° | size | sub89 MCQ cov | anchor |
|---------|-------------|--------------|--------|------|-----|-----|------|-------|------|---------------|--------|
| page_001 | 13 | 14 | pipeline low MCQ / bad anchor | 235.14 | 53.64 | 124.59 | 0.03002 | 0.0 | 2520x3537 | 0.35 | 0.797 (1451,-159) |
| page_002 | 30 | 31 | blurry / washed-out / sparse edges | 238.1 | 47.61 | 58.47 | 0.02269 | 0.0 | 2554x3570 | 0.95 | 0.876 (71,64) |
| page_003 | 28 | 29 | wrinkled / tall crop / faint FR | 238.21 | 46.99 | 74.23 | 0.02814 | 0.0 | 2558x3891 | 0.95 | 0.989 (13,13) |
| page_004 | 1 | 2 | soft blur + visible skew | 236.13 | 53.63 | 68.05 | 0.01907 | 0.0 | 2454x3495 | 1.00 | 0.988 (-6,0) |
| page_005 | 0 | 1 | clean baseline | 233.29 | 56.5 | 116.71 | 0.03175 | 0.0 | 2462x3491 | 1.00 | 1.000 (0,0) |

## Quality metric notes

- **mean / std**: grayscale mean and stddev (washout / contrast).
- **lap**: Laplacian variance (sharpness; lower = blurrier).
- **edge**: Canny edge density (sparse ≈ faint print / soft scan).
- **skew°**: `page_deskew.estimate_skew_degrees` (Hough); returned ~0 for all Paper B pages
  despite visible tilt on several — visual skew still noted in reasons.
- **sub89 MCQ**: from `storage/pipeline_runs/sub89_*_seamo-2025-format-b.pdf.log`
  (`MCQ[N/seamo_2025_b_fb]`); page N is 1-based in the full 197-page PDF (= split index+1).

## Chosen indices (0-based)

- **13** — pipeline low MCQ / bad anchor
- **30** — blurry / washed-out / sparse edges
- **28** — wrinkled / tall crop / faint FR
- **1** — soft blur + visible skew
- **0** — clean baseline
