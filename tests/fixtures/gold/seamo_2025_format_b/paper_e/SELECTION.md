# Paper E gold page selection

Source: `backend/examples/seamo-2025-format-b-split/seamo-2025-format-b-paper-e.pdf` (13 pages; full-PDF pages 164–176 via `page_map.txt`).

Template: `seamo_2025_e_fb` (no dedicated `*_anchor.png`; CV diagnostics below **prime the anchor from page 0**, matching pipeline anchor-priming behavior).

Paper E historically had large `|dy|` / low-anchor failures on full-PDF pages ~165–176. `estimate_skew_degrees` returned ~0° (Hough median) on all pages, but several scans are still visually tilted — ranking uses raw `|dy|` and anchor score.

min_match_score=0.74, HUGE_DY_THRESHOLD=80.

## Chosen (exactly 5)

| gold | 0-based idx | full PDF page | raw anchor | raw dx,dy | warning | why |
|------|-------------|---------------|------------|-----------|---------|-----|
| page_001 | 7 | 171 | 0.5525 | (-66,+640) | anchor_match_low | largest raw |dy|=640; low-ish anchor; visibly CCW skew |
| page_002 | 3 | 167 | 0.4456 | (-66,+569) | anchor_match_low | raw |dy|=569; low anchor; skewed scan |
| page_003 | 5 | 169 | 0.9343 | (-37,+94) | huge_dy | huge_dy: score above min_match but |dy|=94>80 — classic false-lock |
| page_004 | 11 | 175 | 0.3362 | (-82,+38) | anchor_match_low | lowest raw anchor score among the 13 pages |
| page_005 | 0 | 164 | 1.0000 | (+0,+0) | None | clean CV baseline after page-0 priming: anchor≈1.0, dx=dy=0, no warning |

## Quality metrics (selected)

| gold | mean | std | Laplacian | skew_est° |
|------|------|-----|-----------|-----------|
| page_001 | 235.1 | 54.5 | 90.2 | 0.000 |
| page_002 | 232.1 | 58.1 | 110.6 | 0.000 |
| page_003 | 235.0 | 53.6 | 109.7 | 0.000 |
| page_004 | 229.4 | 59.7 | 131.7 | 0.000 |
| page_005 | 233.7 | 56.0 | 98.8 | 0.000 |

## All 13 pages (raw anchor, primed from idx 0)

| idx | full | raw_anchor | dx | dy | |dy| | would warn |
|-----|------|------------|----|----|------|------------|
| 0 | 164 | 1.0000 | +0 | +0 | 0 | — |
| 1 | 165 | 0.4357 | -76 | +70 | 70 | anchor_match_low |
| 2 | 166 | 0.4088 | -67 | +480 | 480 | anchor_match_low |
| 3 | 167 | 0.4456 | -66 | +569 | 569 | anchor_match_low |
| 4 | 168 | 0.5974 | -56 | +82 | 82 | anchor_match_low |
| 5 | 169 | 0.9343 | -37 | +94 | 94 | huge_dy |
| 6 | 170 | 0.4562 | -125 | +58 | 58 | anchor_match_low |
| 7 | 171 | 0.5525 | -66 | +640 | 640 | anchor_match_low |
| 8 | 172 | 0.6947 | -23 | +523 | 523 | anchor_match_low |
| 9 | 173 | 0.6856 | -27 | -1 | 1 | anchor_match_low |
| 10 | 174 | 0.6286 | -64 | +393 | 393 | anchor_match_low |
| 11 | 175 | 0.3362 | -82 | +38 | 38 | anchor_match_low |
| 12 | 176 | 0.4330 | -68 | +70 | 70 | anchor_match_low |

## Not chosen (notable)

- idx 2 (|dy|=480) and idx 8 (|dy|=523): strong dy failures but redundant with idx 7/3.
- idx 12: blurriest Laplacian (~74) but milder |dy| than the selected worst cases.
