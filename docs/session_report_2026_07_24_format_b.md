# Session Report — Dual sheet formats, FB harden, MCQ calib, pipeline speed (Jul 2026)

**Audience:** someone who has not worked on this repo before.  
**Date range:** ~23–24 Jul 2026  
**Primary test corpus:** `backend/examples/seamo-2025-format-b.pdf` (197 pages)  
**Comparison submissions:** **86 → 87 → 88 / 89** (SQLite `exam_submissions.id`)

---

## 0. Project primer (read this first)

### What this product does

**Exam Answer Extractor** turns scanned **bubble-sheet / written answer sheets** (PDF) into structured JSON: who the candidate is, which paper they sat, and what they answered for each question. Optionally it then **marks** those answers against an official answer key and stores scores.

Typical user flow:

1. Upload a multi-page PDF of filled answer sheets (often many candidates in one file).
2. The system figures out **which physical layout** each page uses.
3. It extracts **header fields** (name, candidate number, school, …) and **answers**.
4. It can **auto-mark** against stored answer keys and show results in a React UI.

Stack: **FastAPI** backend (`python main.py`, port 8000) + **React/Vite** frontend (port 3000) + **SQLite** by default (`exam_db.sqlite`) + **Google Gemini** for vision/LLM steps + **OpenCV** for bubble reading + optional **Mathpix** for diagram questions.

Authoritative engineering notes live in `CLAUDE.md` (the top-level `README.md` is stale and should be ignored).

### Domain: SEAMO exams

**SEAMO** is a math competition brand. Each sitting has **papers** labeled by letter (difficulty / age band), e.g. Papers **A–F** and **K** (kindergarten). A single PDF upload often mixes many candidates and sometimes several papers.

Physically, candidates fill:

- **MCQ bubbles** (multiple-choice letters A–E), and/or  
- **Numeric / free-response** boxes, and/or  
- Occasional **diagram** answers.

The same *exam content* (Paper C 2025) can appear on **two different printed sheet designs**. That is the core problem this session solved.

### Classic vs format B (the two sheet designs)

| | Classic (format A) | Format B (`_fb`) |
|--|--------------------|------------------|
| Look | Older / “standard” SEAMO answer sheet | “PAPER-EXAM” style — markers like **BRING A PRINTED COPY**, **PEN AND PAPER EXAM** |
| Template ids | `seamo_2025_c`, `seamo_2026_a`, … | `seamo_2025_c_fb`, `seamo_2026_a_fb`, … |
| Bubble / box geometry | Calibrated for classic scans | Different pixel layout — needs its own template |
| Correct answers | Same official key as classic | **Shares** the classic answer key |

So: **layout templates are about geometry and detection; answer keys are about marking.** Format B must not get a second conflicting key.

### How extraction works (current architecture)

For a mixed / auto PDF the pipeline is roughly:

```
PDF upload
  → render each page to an image (~300 DPI, PyMuPDF)
  → classify layout per page  →  template_id  (e.g. seamo_2025_c_fb)
  → for each page, in parallel tracks:
        (a) Gemini LLM: read header + answers from the full page image
        (b) OpenCV MCQ: locate the bubble grid via an “anchor” image match,
            then score filled bubbles → letters
        (c) optional Mathpix: diagram URL overlay for flagged questions
  → merge: LLM answers first; CV MCQ overwrites MCQ questions it resolved
  → save CandidateResult rows + JSON under storage/results/
  → optional auto-mark against AnswerKey rows
```

Important policy: **the template owns the geometry**. Wrong template ⇒ weak MCQ coverage and messy answers, but the system does not try to “auto-fix” layouts beyond the classifier. Re-upload with the right detection is the recovery path.

### Glossary

| Term | Meaning |
|------|---------|
| **Template** | JSON layout definition (`backend/templates/*.json`): header regions, MCQ grid coords, anchors, question types. |
| **`variant_of`** | Inheritance: a template copies geometry from a parent and overrides fields (year, paper letter, …). |
| **Anchor** | Small reference image crop used by OpenCV `matchTemplate` to locate the MCQ block on a scan. |
| **Anchor priming** | On each upload, crop the anchor from **page 1 of this PDF** into memory so matching matches *this* scan, not only the canonical PNG on disk. |
| **MCQ coverage** | Fraction of expected MCQ questions for which CV produced a letter. Low coverage ⇒ grid alignment failed. |
| **Zero-resolved** | Page where CV returned **0** MCQ answers (total miss). |
| **Big \|dy\|** | Vertical offset from anchor match &gt; 500 px — usually a false match elsewhere on the page. |
| **Submission (`sub N`)** | One uploaded PDF job in the DB (`exam_submissions.id = N`). |
| **Marking run** | One attempt to score all candidates in a submission (`marking_runs`). Re-marking appends a new run; it does not edit raw extracted answers. |
| **FR / free response** | Non-MCQ answers (numeric, time, …). After deterministic normalize/compare, an LLM **equivalence judge** can accept alternate correct forms. |
| **Gemini** | Google multimodal model used for page reading and FR judging (default `gemini-2.5-flash`). |
| **OCR** | Optical character recognition (here: footer text for layout detection). |
| **Deskew** | Geometric straightening of crooked scans — **not** implemented; left out of scope this session. |

### Metrics used in this report

| Metric | How to read it |
|--------|----------------|
| **Answers mean** | Average count of non-blank extracted answers per candidate (higher ≈ fuller extraction; papers have different max Q counts). |
| **Mark mean** | Average **awarded marks** across candidates after marking (same keys ⇒ higher is better extraction+marking agreement). |
| **Cov mean** | Mean MCQ coverage across pages (0–1). Target: high and stable; **0.8+** is solid for this corpus. |

### How to run / inspect locally

```bash
# Backend
python main.py                          # API :8000
python -c "from backend.db.database import init_db; init_db()"

# Frontend
cd frontend && npm run dev              # :3000

# Logs for a submission
ls storage/pipeline_runs/sub89_*
```

Interactive API docs: `http://localhost:8000/docs`.

---

## 1. Executive summary

This session made the system understand **two physical sheet designs** for the same SEAMO papers, then proved the full path on a hard 197-page format-B PDF.

| Goal | Outcome |
|------|---------|
| Dual classic / format-B templates sharing one answer key per paper | Done — `*_fb` layouts point at classic `answer_key_template_id` |
| Format-B detection on sample PDF | **197/197** `_fb` after harden (OCR + doc promote + Gemini header fallback) |
| MCQ coverage on format B | Mean **0.63 → 0.81 → 0.84**; zero-resolved pages **37 → 0** |
| Pipeline wall-clock | Classify **~248s → ~167s**; PDF still ~96–99s; extract/mark dominated by Gemini RPM/latency |
| Mark quality (mean awarded marks) | **16.6 → 22.2 → 22.7** (subs 86 / 87 / 89) |

**Best extract + mark snapshot:** submission **89**, marking run **34** — 197 candidates, MCQ cov mean **0.838**, mark mean **22.71**.

---

## 2. Why dual formats were needed

Before this work, templates largely assumed one sheet geometry per paper letter. A sample PDF that looked “2026-ish” was actually **2025 content printed on format-B sheets**. Using classic templates on those pages:

- Classifier often picked the wrong family (classic vs `_fb`).
- Even with the right paper letter, **bubble coordinates did not line up** → CV MCQ missed many questions.
- Marking must still use the **same official key** as classic Paper C 2025 — only the printed form changed.

**Design choice:** two template ids per series+paper; one shared answer key keyed by the classic id.

### Naming

| Family | Template id pattern | Example |
|--------|---------------------|---------|
| Classic (format A) | `seamo_YYYY_p` | `seamo_2025_c`, `seamo_2026_a` |
| Format B (PAPER-EXAM) | `seamo_YYYY_p_fb` | `seamo_2025_c_fb` |

- Classic `seamo_2026_*` clones inherit geometry from 2025 via `variant_of`.
- Format-B 2025/2026 share geometry; 2025 `_fb` papers B–F inherit MCQ geometry from the calibrated base `seamo_2026_a_fb` via `variant_of`.

### Answer keys

- Canonical key id = **classic** template id (`seamo_2025_c`, etc.).
- Format-B templates set `answer_key_template_id` to that classic id.
- Marking resolves keys through `ExamTemplate.key_template_id` (`backend/services/marking_workflow.py`).
- Unit test: `test_format_b_layout_marks_with_classic_answer_key`.

### Sample asset

- Renamed confusing “2026-only” PDF → `backend/examples/seamo-2025-format-b.pdf`.
- **197 pages**, Papers **B–F** as format B only (no A/K in this file). One page ≈ one candidate sheet.

Observed mix (stable across subs 86–89):

| Template | Pages |
|----------|------:|
| `seamo_2025_c_fb` | 60 |
| `seamo_2025_d_fb` | 56 |
| `seamo_2025_b_fb` | 47 |
| `seamo_2025_f_fb` | 21 |
| `seamo_2025_e_fb` | 13 |

---

## 3. Layout classification (format B)

### What “classification” means

Before extracting answers, each page image is labeled with a `template_id`. The classifier mainly reads the **page footer** with OCR (brand, year, paper letter) and looks for format-B marker phrases. If that fails, a **Gemini** call on the top portion of the page can recover header cues. After per-page labels, a **document-level promotion** can flip classic→`_fb` when the PDF is clearly a format-B batch.

Picking classic vs `_fb` wrong is expensive: extraction still runs, but CV MCQ uses the wrong grid.

### Failure modes on first full-PDF classify

Initial footer OCR on 197 pages:

| Result | Count | Share |
|--------|------:|------:|
| Correct `*_fb` | 126 | 64% |
| Classic (missed `_fb`) | 38 | 19% |
| Undetected | 33 | 17% |

Root causes (full geometric deskew out of scope):

1. Format-B markers sit **above** the tight SEAMO footer line — too-short OCR band missed them.
2. OCR garbles brand tokens (`SEAMQO`, `SCAMOC`, …).
3. Extra bottom margin / blank-ish pages push or erase footer text.
4. Crooked pages remain hard without deskew.

### Hardening shipped

File: `backend/services/page_layout_classifier.py` (wired from `extract_pdf_auto` in `template_extractor.py`).

| Technique | Role |
|-----------|------|
| Dual-band footer OCR | Tight band for paper letter; taller band for BRING/PEN markers |
| OCR normalize | Map common SEAMO garble → `SEAMO` |
| Doc-level `_fb` promotion | If majority of pages are format B, promote paper-parsed classic misses to `_fb` |
| Gemini top-~30% header fallback | When footer OCR fails paper/year |
| Blank-page skip | Avoid inventing layouts on empty sheets |

### Classification results after harden

| Stage | Result |
|-------|--------|
| OCR + promote only | ~181/197 `_fb` |
| + Gemini on remaining misses | **197/197** `_fb` |

Classic mixed PDFs (e.g. UZ1-style) still classify as classic `seamo_2025_*` (smoke retained).

---

## 4. MCQ calibration (format B)

### Why CV MCQ exists alongside Gemini

Gemini reads the whole page and is good at headers and free text, but **filled bubbles** are more reliable with classical computer vision when the grid is correctly located: threshold ink in each bubble cell, pick the darkest option. That needs accurate **pixel coordinates** relative to a found anchor.

If the anchor match lands in the wrong place (large `dy`), every bubble is scored on white space or the wrong row → coverage collapses and marks look artificially low.

### Problem (sub 86, pre-calib)

Early format-B geometry did not match the PAPER-EXAM bubble grids:

| Metric | Sub 86 |
|--------|-------:|
| MCQ coverage mean | 0.626 |
| Coverage ≥ 0.6 | 129/197 |
| Zero-resolved MCQ pages | **37** |
| Low-coverage warnings | 68 |
| Large anchor \|dy\| > 500 | **42** |
| Per-paper cov (B/C/D/E/F) | 0.92 / **0.29** / 0.77 / 0.76 / **0.45** |

Papers **C** and **F** were especially broken; answers mean **16.6**, mark mean **16.6**.

### Calibration persisted

Base template: `backend/templates/seamo_2026_a_fb.json` (+ `seamo_2026_a_fb_anchor.png`). Child `_fb` papers inherit via `variant_of`.

| Change | Detail |
|--------|--------|
| Anchor | Taller “Questions 1 to 20” crop |
| Columns | `[325, 436, 548, 659, 768]` (A–E bubble X centers @ 300 DPI) |
| Row pitch / Ys | Explicit `row_positions` (~80 px pitch) |
| `min_match_score` | Raised to **0.72** (reject weak matches) |
| Low-score matches | Force **dx=0, dy=0** instead of a wild offset |
| Search ROI | `matchTemplate` limited to upper **55%** of the page (`mcq_extractor.py`) |

### Post-calib progression

| Metric | 86 (pre) | 87 (1st calib) | 88 / 89 (recalib + gates) |
|--------|---------:|---------------:|--------------------------:|
| Cov mean | 0.626 | 0.813 | **0.838** |
| Cov ≥ 0.6 | 129 | 163 | **169** |
| Cov ≥ 0.9 | 96 | 107 | **115** |
| Zero-resolved | 37 | **0** | **0** |
| Low-coverage | 68 | 31 | **27** |
| Big \|dy\| | 42 | 5 | 6 |
| Anchor zeroed (score gate) | 0 | 0 | 13 |
| Answers mean | 16.64 | 20.39 | 20.60–20.75 |
| Mark mean | 16.56 | 22.16 | **22.71** (89) |

Per-paper coverage after recalib (sub 89):

| Paper | Cov mean |
|-------|---------:|
| B | 0.861 |
| C | 0.713 |
| D | 0.900 |
| E | 0.877 |
| F | 0.950 |

**C** remains the weakest (~0.71); paper-specific geometry is the main remaining MCQ lever.

---

## 5. Pipeline speed work

On sub 87 (~20 min end-to-end), time broke down roughly as:

| Stage | What it does | ~Time on sub 87 |
|-------|----------------|----------------:|
| PDF render | Vector/PDF → PNG pages | ~98s |
| Classify | OCR (+ Gemini fallback) per page | ~4 min serial |
| Extract | Gemini + CV per page | ~4 min |
| Mark / FR judge | Score + LLM equivalence on free-response | ~10 min serial |

### Parallelism added

| Stage | Change | Config default |
|-------|--------|----------------|
| PDF → images | Chunk-parallel PyMuPDF | `MAX_PDF_RENDER_WORKERS=4` |
| Layout classify | Parallel footer OCR + Gemini fallback | `MAX_CLASSIFY_WORKERS=8` |
| FR equivalence judge | Parallel Gemini judge calls | `MAX_FR_JUDGE_WORKERS=6` |

Documented in `.env.example` / `backend/config.py`. Host also used `GEMINI_MAX_RPM=60`.

### Timing comparison (extract run logs)

| Stage | Sub 87 | Sub 88 | Sub 89 |
|-------|-------:|-------:|-------:|
| PDF | 98.1s | 99.4s | 96.4s |
| Classify | ~(serial ~248s) | **167.3s** | **166.7s** |
| Extract (page Gemini+CV) | 230s | 1252s | **2020s** |
| Total run | 1194s (~20m) | 1686s | 2322s (~39m) |

**Classify** clearly improved with 8 workers. **PDF** stays ~100s on this host. **Extract** is Gemini-dominated: sub 89 saw long mid-run stalls (order of ~600s/page), so wall-clock rose despite concurrency.

### Marking bug from parallel FR work

Worker threads received live SQLAlchemy `CandidateResult` objects and triggered lazy loads off a shared session → **`ObjectDeletedError`**. Auto-mark on sub 89 failed (runs 32–33).

**Fix:** on the main thread, snapshot `(candidate_id, dict(answers))` before `ThreadPoolExecutor` (`marking_workflow.py`). Remake: run **34**, 197 markings, mean **22.71** in ~161s.

---

## 6. Full extract run logbook

Think of each **submission** as one full upload of the same format-B PDF after a set of code/template changes.

| Sub | Role | Extract | Auto-mark | Notes |
|-----|------|---------|-----------|-------|
| **86** | Baseline pre-MCQ-calib | completed | completed (run 29) | Weak C/F MCQ; mark mean 16.56 |
| **87** | Post first MCQ calib | completed | completed (run 30) | Cov 0.81; mark mean 22.16; ~20 min |
| **88** | Speed + recalib | completed (after outage recovery) | no completed mark | Power outage mid-run; later finished 197 cands; classify 167s |
| **89** | Clean retest post-outage | completed | failed×2 then remade run 34 | Same MCQ as 88; Gemini stalls; mark mean 22.71 |

Artifacts:

- Pipeline logs: `storage/pipeline_runs/sub{86,87,88,89}_*_seamo-2025-format-b.pdf.log`
- Result JSON: `storage/results/*_seamo-2025-format-b.json`
- DB: `exam_submissions`, `candidate_results`, `marking_runs`, `candidate_markings`

---

## 7. Operational lessons (carry forward)

These are project invariants discovered earlier and still true:

1. **Do not set `max_output_tokens` on Gemini 2.5** for full-page extract — reasoning tokens share the budget; a low cap yields empty/`MAX_TOKENS` JSON.
2. **Anchor priming** from the current upload’s first page beats relying only on on-disk `*_anchor.png` from a different scanner/scan.
3. **429 retry with backoff** (5s / 15s / 30s / 60s) is required when page workers share a free-tier RPM limit.
4. **Wrong template is an acceptable failure mode** — warn on low MCQ coverage; let the user re-upload. Do not resurrect deleted auto-detect / clustering pipelines unless explicitly requested (`CLAUDE.md`).

---

## 8. Key files touched this session

| Area | Paths |
|------|-------|
| Classifier | `backend/services/page_layout_classifier.py` |
| Extract / classify parallel | `backend/services/template_extractor.py` |
| PDF parallel render | `backend/services/pdf_to_images.py` |
| MCQ CV + ROI / score gate | `backend/services/mcq_extractor.py` |
| Marking + FR parallel + ORM fix | `backend/services/marking_workflow.py` |
| Template model / keys | `backend/services/template_service.py`, template JSON `answer_key_template_id` |
| FB geometry base | `backend/templates/seamo_2026_a_fb.json`, `seamo_2026_a_fb_anchor.png` |
| Config | `backend/config.py`, `.env.example` |
| Tests | classifier/marking unit tests; format-B → classic key resolution |
| This report | `docs/session_report_2026_07_24_format_b.md` |

Related older docs (some partially stale): `docs/PIPELINE_ARCHITECTURE.md`, `docs/session_report_2026_05_13.md`, `CLAUDE.md`.

---

## 9. Open items / next levers

1. **Paper C FB geometry** — still ~0.71 mean cov; largest remaining MCQ gap.  
2. **PDF convert ~100s** — further chunk/worker tuning or caching if upload latency matters.  
3. **Gemini extract latency** — RPM/backoff present; stalls dominate wall-clock more than CPU parallelism.  
4. **Optional: remake mark for sub 88** — extract looks good (cov 0.838, ans mean 20.75) but no completed marking run.  
5. **Deskew** — still out of scope; crooked pages remain a known failure class.  
6. **Restart long-lived API** after the marking ORM fix so production processes pick it up (`python main.py`).

---

## 10. One-line verdict

Format-B is production-viable on the 197-page sample: **correct layout ids, MCQ coverage ~0.84 with zero dead pages, marking ~22.7 mean**, with classify sped up and one parallel-marking race fixed; remaining work is Paper C geometry and Gemini-bound extract time.
