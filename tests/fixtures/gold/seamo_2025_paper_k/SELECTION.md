# Paper K gold fixture selection

Paper K is the only two-column SEAMO layout (Q1-10 left, Q11-15 right).  Until
this set existed it was registered by anchor template-matching alone, with no
ground truth anywhere, which is why Paper K accuracy work was guesswork.

**What the annotations showed.** Every annotated grid is the template under one
uniform scale + offset (fit residual ~1 px), but the printed content is often
shrunk: the ID13-193 classic pages sit at **85-89 %** scale and format-B pages at
92-97 %.  The anchor cannot see content scale, so it scored these pages
0.13-0.17 (or 1.000 when self-primed) and put the grid 80-280 px off.  Paper K
now registers by its printed A/B/C label lattice
(`mcq_label_lattice._align_multi_column`), measured against these grids by
`tests/test_paper_k_cv_accuracy.py`.

Source: SEAMO 2025 marking Drive, country batches, pulled via the Drive
connector. Rendered at **300 DPI**, then deskewed with
`backend.services.page_deskew.deskew_if_enabled`. Metrics measured with
`MCQ_ANCHOR_SCALE_SEARCH=false` — the currently shipped behaviour.

Harvest: **52 Paper K pages** across 5 batches (Indonesia, Sri Lanka, Cambodia).
Selection follows `../seamo_2025_format_b` — five pages per layout, with the
variation **inside** each group: four different failure modes plus a baseline.
The grouping axis is the layout whose geometry is under test. Country is
provenance only and was not balanced on.

## seamo_2025_k (classic) — 25 harvested

| Fixture | Reason | Source batch | Src page | anchor | cov | lap | mean | edge | CV warning | CV read |
|---|---|---|---|---|---|---|---|---|---|---|
| page_001.png | worst anchor lock | Indonesia_ID13-193 | 8 | 0.131 | 0.93 | 171 | 232.1 | 0.0232 | weak_fill_scores | `BCB?C?C?CABA?A-` |
| page_002.png | different warning class | Indonesia_ID13-193 | 36 | 0.154 | 0.87 | 172 | 234.0 | 0.0237 | huge_dx | `A?AAAABCA-C???-` |
| page_003.png | lowest coverage | Indonesia_ID13-193 | 6 | 0.141 | 0.87 | 164 | 232.2 | 0.0235 | weak_fill_scores | `AAAAC?AA?-??AB-` |
| page_004.png | blurriest print | Indonesia_ID13-193 | 10 | 0.168 | 1.00 | 104 | 234.5 | 0.0253 | huge_dy | `??A?B?BCA??B??A` |
| page_005.png | clean baseline | SriLanka_LK2-206 | 17 | 0.928 | 1.00 | 179 | 234.0 | 0.0290 | — | `BBBACCBAABABACC` |

| metric | min | median | max |
|---|---|---|---|
| anchor score | 0.1315 | 0.337 | 0.9276 |
| coverage | 0.8667 | 1 | 1 |
| sharpness (lap) | 104.2 | 178.8 | 227.3 |
| brightness | 229.6 | 232.8 | 234.5 |
| edge density | 0.0232 | 0.02706 | 0.02903 |

Warnings across all 25: `{'none': 6, 'weak_fill_scores': 11, 'huge_dy': 3, 'huge_dx': 5}`

## seamo_2025_k_fb (format B) — 27 harvested

| Fixture | Reason | Source batch | Src page | anchor | cov | lap | mean | edge | CV warning | CV read |
|---|---|---|---|---|---|---|---|---|---|---|
| page_001.png | only A/B/C lattice failure in a 130-page second harvest; dithered scan, 90 % scale | Australia_AU2-222 | 194 | — | — | 2315 | 232.5 | — | huge_dx (old) | `B?C-B?A-A--??--` |
| page_002.png | blurriest page of the second harvest; scrawled marks | Indonesia_ID15-200 | 6 | — | — | 87 | — | — | (fit only at thr 140) | `AABCAABACCABCAB` |
| page_003.png | lowest coverage | SriLanka_LK2-206 | 71 | 0.506 | 0.87 | 168 | 235.0 | 0.0273 | huge_dx | `IAAAAAAAA-IAAI-` |
| page_004.png | blurriest print | Indonesia_ID13-193 | 4 | 0.520 | 1.00 | 89 | 236.0 | 0.0293 | weak_fill_scores | `ACBABA?CA?BBABB` |
| page_005.png | best available (still warns) | SriLanka_LK2-206 | 56 | 0.658 | 1.00 | 157 | 234.3 | 0.0292 | weak_fill_scores | `BABBBICIIBCAAAB` |

| metric | min | median | max |
|---|---|---|---|
| anchor score | 0.2814 | 0.5198 | 0.6582 |
| coverage | 0.4 | 1 | 1 |
| sharpness (lap) | 89.21 | 158.4 | 698.7 |
| brightness | 231 | 232.7 | 238.3 |
| edge density | 0.02274 | 0.02654 | 0.03576 |

Warnings across all 27: `{'huge_dx': 6, 'lattice_align_failed': 1, 'weak_fill_scores': 18, 'none': 1, 'huge_dy': 1}`

**Note the baseline.** Only **1 of 27** format-B pages carries no warning, and
it registers poorly (anchor 0.334, coverage 0.80). The best anchor score anywhere
in the variant is 0.658. There is therefore no genuinely clean format-B baseline
to hold the line with — `page_005` is the best available and still warns. That is
itself a finding: the format-B K variant is registering marginally across the
board, not just on the hard pages.

### Numeric-option sheet (`numeric_option_sheet/`)

The two pages first selected as format-B page_001/page_002 (LK2-206 p29, p50)
are a **different answer sheet**: options printed `1 2 3` for Q1-10 and
Q11-15 as written-answer boxes.  They carry the same `SEAMO 2025 Paper K`
footer, school and exam date as the A/B/C sheets, so they classify as
`seamo_2025_k_fb`.  The 2025 Paper K key is 15 A/B/C MCQs, so no K template
can mark them.  They are kept as a refusal test: the lattice must find no
right-hand box column and the page must carry an MCQ trust warning.  That
The two freed slots were refilled from a second harvest (below).

### Trust cases (`trust_cases/`)

Four real pages, from a blind audit of 190 harvested K pages (123 format B
across LK4-207, ID4-274, ID18-200, AU2-222 and ID15-200, plus 67 classic). On
each page the CV path once *trusted* a wrong answer: three double marks and
one faint pencil mark. `trust_cases.json` gives the audited answer string, with
`?` where the row must go to review. The audit found zero wrong trusted answers
on the current code.

## Truth files

* `page_NNN.json` - verified answers (`gold_eval` format).  Read by CV at the
  annotated grid, then checked row by row against per-question crops.  Classic
  page_004 Q14-Q15 are genuinely blank.
* `grid_page_NNN.json` - the hand-annotated grid from
  `scripts/annotate_mcq_grid.py` (named so `load_gold_pages`, which globs
  `page_*.json`, skips it).  `raw_clicks.blocks[].fill_top_ys` are the printed
  box tops; `col_xs` the option centres.

## Annotating

`scripts/annotate_mcq_grid.py --gold tests/fixtures/gold/seamo_2025_paper_k`

Paper K settings: **options `3`**, **questions/column `10,5`**, **cell_height `0`**
(the row position *is* the fill top — there is no label band above it). Start
`cell_width` at 93 and `bubble_height` at 31 and adjust until the preview boxes
sit on the printed boxes.

The two variants are different geometries and must be annotated separately.

## What this set is for

A zero-silent-wrong gate for Paper K, the same bar `gold_eval.py` already applies
to format B: the system may be unsure as often as it likes, but must never
confidently emit a wrong answer.  Ten pages x 15 questions = 150 labelled
answers, all reproduced exactly; worst registration error 7 px against a
~145 px row pitch.

page_001/page_002 come from a second harvest (130 format-B pages across
LK4-207, ID4-274, ID18-200, AU2-222, ID15-200), chosen as the pages hardest for
the new lattice and checked by an independent verifier before annotation.
