# SEAMO 2026 classic — layout mapping set

Every `seamo_2026_*` classic template is a stub that inherits its geometry from
the 2025 sibling via `variant_of`; only the `_fb` variants were ever mapped,
from the clean vector sheets in `backend/examples/seamo-2026-answer-key-format.pdf`
(all seven pages of that file carry the format-B marker). No clean 2026 classic
answer sheet exists in the repo, so these scans are the only source available
for mapping the classic layouts.

Source PDF: `backend/examples/SEAMO Australia 2026 answer scripts PART 2 - 131-pax-compressed.pdf`
Rendered at **300 DPI**, then deskewed with `backend.services.page_deskew.deskew_if_enabled`.
Page→template assignment is from production submission 35, which classified all
131 pages (two of them via the document-year fallback).

| Paper | Pages | Source page numbers |
|-------|-------|---------------------|
| paper_a | 4  | 35, 37, 40, 41 |
| paper_b | 7  | 18, 39, 42, 43, 66, 71, 74 |
| paper_c | 12 | 16, 17, 20, 33, 34, 38, 68, 72, 75, 101, 102, 115 |
| paper_d | 5  | 32, 36, 52, 70, 90 |
| paper_e | 1  | 73 |
| paper_f | 1  | 67 |
| paper_k | 1  | 21 |

## Why this matters most for Paper K

Paper K is two-column, so the printed-label lattice does not apply and it is the
only family still registered by anchor template-matching. With no
`seamo_2026_k_anchor.png` it falls back to `seamo_2025_k_anchor.png`, which
scores **0.169** against a 0.45 threshold — no offset is applied at all and the
grid lands on the letter labels instead of the answer boxes. Measured banner
separation confirms the sheets differ structurally rather than by translation:

| Sheet | banner 1 | banner 2 | separation |
|-------|----------|----------|------------|
| clean 2025 K (vector) | 850–934 | 2781–2860 | 1931 px |
| clean 2026 K **format B** (vector) | 940–1020 | 2805–2880 | 1865 px |
| scanned 2026 K **classic** | 929–999 | 2684–2749 | 1755 px |

A sweep of dx 0–90 × dy 0–160 scored by key-blind ink ratio peaks at 4/15
agreement, and the best agreement anywhere in that space is 9/15 — no
translation recovers this layout.

A–F degrade more gently because the label lattice refits geometry per page, which
is why `seamo_2026_a` (mean 27.8%) and `_e` (16.5%) trail their `_fb` siblings
(55.0%, 45.8%) rather than collapsing outright.

## Caveat

These are compressed scans, not blank forms. Morphological box detection finds
no complete rows on `paper_k/page_001.png`, which is why the geometry is being
clicked by hand rather than fitted automatically. Geometry derived here carries
this scan's residual scale; a clean blank 2026 classic sheet would be better
evidence and should replace this if one becomes available.
