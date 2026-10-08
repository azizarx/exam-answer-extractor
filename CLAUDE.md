# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Exam answer sheet extraction system: upload a PDF of exam answer sheets and get structured answers and weighted marking back. Omit the layout template for automatic per-page detection, or force one template across the PDF. Per-page extraction runs in parallel tracks (LLM header/free-response + CV MCQ overlay + diagram crops + optional Mathpix figure overlay), then merges. Diagram answers are marked by comparing the candidate's drawing with the answer key's drawing. FastAPI backend + React frontend.

> Start with `README.md` for setup and `docs/API.md` for the current external integration contract. The pipeline sketch below describes the forced-template path; automatic mode detects layouts per page before extraction.

## Commands

### Backend
```bash
python main.py                    # Start FastAPI server on :8000 (auto-reloads in debug)
pip install -r requirements.txt   # Install Python dependencies
python -c "from backend.db.database import init_db; init_db()"  # Create DB tables + apply column migrations
```

### Frontend
```bash
cd frontend && npm install
cd frontend && npm run dev        # Vite dev server on :3000
cd frontend && npm run build      # Production build
cd frontend && npm run lint
```

### Tests
There is no `conftest.py` / `pytest.ini`. Bare `pytest` fails with
`ModuleNotFoundError: No module named 'backend'` — always use `python -m pytest`.

```bash
DEBUG=false .venv/bin/python -m pytest tests/ -q                  # whole suite, ~20s
.venv/bin/python -m pytest tests/test_template_extractor.py -v    # mocked Gemini + Mathpix; covers merge logic + diagram URL matching
.venv/bin/python tests/test_mcq_pipeline.py                       # CV bubble extractor against synthetic filled bubbles
.venv/bin/python -m pytest tests/test_format_b_cv_accuracy.py -v  # the gold MCQ bar: all 500 answers across 25 adversarial scans
.venv/bin/python -m pytest tests/test_paper_k_cv_accuracy.py -v   # the Paper K bar: 150 answers + hand-annotated grids on 10 adversarial scans, plus trust cases
.venv/bin/python -m pytest tests/test_diagram_crops.py tests/test_diagram_vision_judge.py tests/test_diagram_endpoints.py -v   # diagram cropping, vision marking, image endpoints
.venv/bin/python -m pytest tests/test_queue_system.py tests/test_cancellation.py tests/test_cpu_limits.py -q   # durable queue, cancellation, CPU budget
.venv/bin/python scripts/extract_reference_diagrams.py --check    # answer-key reference drawings still match the key PDFs
.venv/bin/python scripts/build_api_docs.py --check                # generated API docs are current
```

The suite is hermetic — no network, no API keys, no Redis. The only external
binary dependency is **tesseract** (`test_page_layout_classifier.py`).
`.gitlab-ci.yml` runs **only** GitLab Secret Detection: none of the above is
enforced in CI, so run them locally before pushing.

API is interactively testable at `http://localhost:8000/docs`.

## Architecture

### Production queue

Compose runs the API, Redis, dispatcher, processing worker and archive worker.
`backend/queue/pipeline.py` processes bounded page batches and checkpoints them
in SQLite; the old background function below is the local development path.
Uploads stream through `QueuedUploadMiddleware` before form spooling.
`ProcessingJob` is the job ledger/outbox; Redis carries IDs only. See `DEPLOY.md`
and `docs/api/guide.md` for limits, private Spaces archival, optional eviction,
explicit queued deletion and recovery. Preserve existing DB rows and local
files on deployment. `ARCHIVE_EVICT_LOCAL` is enabled after validation; `ARCHIVE_PRESERVE_THROUGH_SUBMISSION_ID` pins pre-rollout files.

### Development extraction path

```
process_pdf_extraction(submission_id, pdf_path, template_id):
  1. PDF → page images                          (backend/services/pdf_to_images.py)
  2. Compute gates from the template:
       run_cv_mcq = template.has_mcq
       diagram_qs = [questions flagged type=diagram in template.sections]
       run_mathpix = bool(diagram_qs) and mathpix_creds
  3. If run_mathpix: mathpix_client.submit_pdf(pdf_path) → pdf_id      [fire-and-forget]
  4. For each page (ThreadPoolExecutor, max_workers=settings.max_extraction_workers, default 6):
       Inside each page worker, ThreadPoolExecutor(max_workers=2) runs:
         (a) LLM    : one Gemini call (default gemini-2.5-flash), NO max_output_tokens,
                      on CROPPED header + free-response regions only — never the
                      MCQ grid → {"header":{...}, "answers":{q→value}}
         (b) CV MCQ : (only if run_cv_mcq) mcq_extractor.extract_page(image, template)
                      → {q→letter}
       Merge (`_assemble_candidate`): CV owns MCQ, LLM owns FR. The LLM's answers
       are hard-filtered to the template's FR question numbers before the merge
       (`template_extractor.py:472-478`), so a hallucinated letter cannot reach an
       MCQ slot. Any MCQ question the CV did not produce is set to "BL".
  4b. Per page, for each diagram question: fit the printed scaffold inside
     `question_overrides[q].diagram_search_region` and save the crop to
     `storage/diagrams/sub<id>/p<page>_q<n>.png`.
  5. If pdf_id: mathpix_client.poll_pdf(pdf_id) → fetch_mmd → regex CDN URLs out
     of the markdown. For each diagram question, the first URL after that
     question's `\section*{Question N}` label is recorded in
     `extra_fields.diagram_sources` and the image is DOWNLOADED over the
     template-region crop (source="mathpix"). Spurious URLs after non-diagram
     labels are dropped. A URL is NEVER written into `answers`.
  6. Persist candidates as CandidateResult rows; save JSON to storage/results/.
```

### Diagram marking (vision, not text)

A drawing cannot survive a text round-trip, so `type=diagram` questions are
marked by comparing images, not strings:

```
marking_workflow._mark_one:
  mark  →  apply_extraction_trust  →  apply_fr_equivalence_judge  →  apply_diagram_vision_judge
```

- `diagram` is deliberately NOT in `FR_TYPES` (`marking_service.py`); the text
  judge never sees a diagram.
- `apply_diagram_vision_judge` sends one Gemini call per candidate with a
  labelled `STUDENT` / `CORRECT` image pair per diagram question. `CORRECT` is
  the answer key's own drawing, extracted from the key PDF into
  `answer_keys/reference_diagrams/<template_id>_q<n>.png` by
  `scripts/extract_reference_diagrams.py` (rerun with `--check` after any key
  change — note CI does **not** run it; see Tests above).
  `ManifestRegistry` refuses to load a manifest whose diagram question has no
  committed reference.
- Verdict → outcome: `match`→correct, `mismatch`→incorrect, anything else →
  `needs_review`. Missing crop / missing reference / judge exception all become
  `needs_review`, never a silent zero. `judge_source` is `diagram_*` throughout,
  which is how the frontend spots a diagram row.
- A `blank` diagram answer IS judged. "BL" means the extractor's *text* read was
  empty, which is exactly how faint pencil on a preprinted scaffold reads; only
  the crop can tell an unanswered question from a misread one. (A blank with no
  crop at all stays blank rather than flooding the review queue.)
- **Deterministic CV wins where it fires.** `seamo_x_2026_a` Q9 is measured by
  `diagram_cv.extract_seamo_x_a_q9` (per-sector ink density). That is more
  precise than reading a low-resolution crop — the vision judge reads that wedge
  one sector off — so extraction records `extra_fields.diagram_cv_questions` and
  the vision stage skips those questions.
- `DIAGRAM_VISION_ENABLED=false` reverts diagram questions to the text
  equivalence judge (`apply_fr_equivalence_judge(..., include_diagram=True)`)
  without a redeploy. It must not leave them on bare string equality: the key's
  accepted answers are prose, so every non-verbatim description would become a
  silent zero.

Five things matter to remember:

1. **No `max_output_tokens` on Gemini calls.** Gemini 2.5 models spend reasoning tokens from the same budget; the previous default cap of 1024 caused `finish_reason=MAX_TOKENS` on every call and starved the visible JSON. Letting the model use its default (~64k) is the deliberate fix. See `template_extractor.py::_llm_extract_full`.

2. **Anchor priming.** Before per-page CV MCQ runs, `TemplateExtractor._prime_anchor_from_page()` (`template_extractor.py:360`, called at `:649`) crops the template's anchor region from the first page carrying THAT layout and stashes it in `TemplateRegistry._anchor_images` (process-wide cache). Without this, the registry falls back to the on-disk `backend/templates/<id>_anchor.png` which is from a canonical reference scan, not the current scan — that mismatch drops MCQ coverage from ~99% to ~70%. (`_prime_anchor_from_first_page` at `:357` is a back-compat alias with no callers in `backend/`.) This now matters only on the legacy anchor fallback path; A–F and Paper K papers are fitted by the printed-label lattice and never call `_match_anchor`.

3. **Rate-limit retry.** `run_logger.llm_call` is the SINGLE retry owner — callers must not wrap it. Backoff is `_TRANSIENT_BACKOFF_SECONDS = (2.0, 5.0, 10.0)` (`run_logger.py:183`) with `GEMINI_TRANSIENT_RETRIES=2`, covering 429 *and* deadline/503/504. Pacing is separate: a process-wide sliding-60s token bucket at `GEMINI_MAX_RPM` (repo default 4.0, raise it on paid tiers). Under the queue this bucket lives in Redis and **fails closed** — a Redis outage fails the job rather than issuing unpaced calls.

4. **CPU budget: one native thread per OCR/CV process.** `cpu_limits.py` sets
   `OMP_THREAD_LIMIT=1` and `cv2.setNumThreads(1)` on a 4-vCPU host. This is not
   tuning preference — eight Tesseract processes each spawning an OpenMP team
   took **93s for 8 pages with 3 timeouts**; the identical work single-threaded
   took **1.7s** (`docs/performance-2026-09-09.md`). `MAX_OCR_WORKERS` is shared
   capacity across submissions, not a per-PDF budget.

5. **CV owns MCQ; the LLM is never shown the bubble grid.** Deskew runs once in
   `_extract_one_page`, so `mcq_extract_page` is called with `deskew=False` and
   the **unadapted** template (it calls `adapted_to_image` itself — passing the
   adapted one double-scales every coordinate). MCQ geometry fitting is
   answer-key-blind by contract: no keys, labels or reference images may leak
   into it, or the gold acceptance test becomes meaningless.

### Paper K: two-column label lattice

Paper K (Q1-10 left, Q11-15 right) is the only multi-column layout, and
`mcq_label_lattice._align_multi_column` registers it: the 10-row column is
fitted exactly like a single-column sheet, and the 5-row column is accepted
only if its own A/B/C labels appear where that fit predicts. One scale and
offset per axis is then refitted across both. The annotated gold set showed
why the anchor could never do this: K scans are routinely printed at **85-97 %
content scale**, which `adapted_to_image` cannot see (it only reacts to ≥10 %
page-size drift). The anchor scored those pages 0.13-0.17, or 1.000 when
self-primed, and put the grid 80-280 px off.

- The K-only glyph and gap constants (`MULTI_COL_*`) are separate so the
  single-column A-F path stays byte-identical. Don't merge them.
- K boxes have heavy printed outlines, so the K templates score only the box
  interior (`cell_height` 11, `bubble_height` 13, `cell_width` 66). Scoring the
  outline made every blank row read `IN`.
- A K row counts as seen when 2 of its 3 labels are found
  (`MULTI_COL_MIN_ROW_SUPPORT`), because a child's fill often covers one label.
  Requiring all 3 dropped Q1 on AU2-222 p194, and the fit then failed.
- K's `min_ratio` is 2.0 (it was 1.12). With the interior-only window, a clean
  single mark leaves the other boxes near zero. Across 1,845 harvested format-B
  rows, every real double mark scored ≤ 1.39 and every
  fill-plus-erasure-residue row scored ≥ 2.48. At 1.12, a filled-A /
  scribbled-C row was committed as A.
- The classic ink-energy rescore is skipped for two-column sheets. On 66
  classic K pages it changed only three rows, each a double mark it promoted to
  a letter, and one of those was trusted wrongly.
- The K templates set `scoring.blank_review_ink` = 8. On a lattice fit, a
  blank row with that much ink goes to review. Audited blank K rows read
  exactly 0, and a missed faint pencil mark read 12. The setting defaults to 0
  (off) everywhere else.
- Audit (2026-10-08): 190 harvested K pages (123 format B, 67 classic) were
  transcribed blind by independent agents, and every disagreement was
  adjudicated. Zero trusted answers are wrong on the current code. The
  regressions live in `trust_cases/` and `test_paper_k_cv_accuracy.py`.
- If the lattice fails on a two-column sheet, the anchor fallback still runs
  for the reviewer, but the page is always flagged `label_lattice_failed`. A
  self-primed anchor read 4/15 correctly with no warning on a gold page.
- A sheet printed with `1 2 3` options and written Q11-15 also carries the
  "SEAMO 2025 Paper K" footer. No K template can mark it, and the lattice
  refuses it (no right-hand box column).

### Withdrawn layouts (`DISABLED_TEMPLATE_IDS`)

Nothing is withdrawn by default. The setting lists template ids to refuse, as
a comma-separated list. A page detected as a withdrawn layout is still
identified, so it stays traceable and can be marked by hand, but it is **not
extracted and not marked**. It stores empty answers with reason
`template_disabled`, and marking lists it under `missing_templates` as
`"<id> (withdrawn)"` rather than scoring blanks as a zero. An all-blank 0 is
indistinguishable from a candidate who answered nothing, which is why this is
a refusal rather than a low score. `/capabilities` reports the list as
`withdrawn_templates` so an integrator can route those pages for manual
handling.

`seamo_2026_k` was withdrawn from 2026-10-02 to 2026-10-08, while Paper K was
still registered by anchor: the anchor scored 0.169 against a 0.45 threshold
and the grid landed on the letter labels. It was re-enabled once Paper K
registered by its label lattice (above).

### Review-vision second opinion (`review_vision.py`)

A question CV will not commit to reaches marking as `needs_review` and scores
0 — `apply_extraction_trust` is terminal, and no judge can award it marks.
`recheck_flagged_mcq` gives those questions one more chance at extraction time:
it crops that single row and asks a vision model which box is filled. The model
reports **what is marked, never whether it is right** — the deterministic marker
still scores it, so the model never sees the key.

Four refusals, each from an observed failure on real pages — do not relax them
without new evidence:

1. **Untrusted page geometry → nothing is asked.** A page-level
   `MCQ_TRUST_WARNINGS` hit means the grid is suspect, so crops land between
   rows. On the real Paper K page the crop showed Q9's boxes beside Q10's
   number and the model reported seeing "9" — echoing the label it was given.
   The same crop was refused on another run, so that guard holds only sometimes.
2. **CV `IN` is never overturned.** `IN` is a positive multi-ink detection, and
   in practice that is the struck-out-and-rechosen case. The model picks one of
   the two and cannot tell which was cancelled; on page 54 it chose the
   crossed-out box twice, costing 6 marks and the review each time.
3. **`BLANK` is never accepted.** A crop that missed its row looks identical to
   an empty one. Blank scores 0 either way, so accepting gains nothing and only
   removes the human who might have awarded the mark.
4. **The printed question number must match.** Asked as a check against
   mis-registration; necessary but, per (1), not sufficient.

The model enumerates `marked_boxes` rather than naming a winner — asking for a
category let it choose between a struck-out box and a fresh one.
`REVIEW_VISION_ENABLED=false` disables the stage; flags then behave as before.

### Templates (`backend/templates/`)
- One JSON per layout/variant. Schema in `backend/templates/schema.json`.
- Pixel coords at reference DPI 300; `ExamTemplate.at_dpi()` scales to actual scan DPI.
- `*_anchor.png` files are template-matching anchors (overwritten in-memory by anchor priming on each new submission).
- Diagram questions are encoded via `sections[*].question_overrides[q].type == "diagram"` (e.g. `seamo_x_2026_a` flags Q4/Q6/Q9; `seamo_x_2026_b` flags Q5).
- A diagram override also carries `diagram_search_region`: a generous band searched for the printed scaffold when cropping. It is deliberately separate from `region`, which still means "crop exactly this box" to `_fr_question_crop_specs`, and may extend outside the section.

### Mathpix /v3/pdf flow
- `mathpix_client.submit_pdf` (multipart POST with `options_json={"conversion_formats":{"md":True}, ...}`). `.mmd` is always generated by default (do NOT list it in conversion_formats — Mathpix rejects that).
- `mathpix_client.poll_pdf` polls `GET /v3/pdf/{pdf_id}` every 3s until `status in {completed, error}` or `max_wait=600s`.
- `mathpix_client.fetch_mmd` GETs `/v3/pdf/{pdf_id}.mmd` and returns the markdown body. Figure URLs are inline as `![](https://cdn.mathpix.com/cropped/<uuid>-<page>.jpg?...)`. Confirmed against a real diagram-bearing page.

### Domain model (`backend/db/models.py`)
- `ExamSubmission` is the legacy single-PDF flow. New nullable `template_id` column records the chosen template. Frontend (`UploadPage`, `TrackingPage`) uses this flow exclusively.
- `Exam`/`ExamDocument`/`GeneratedJSON` is a multi-document flow (per-country). The `/exams/{id}/extract/{document_id}` endpoint also uses TemplateExtractor and requires `template_id`. No frontend UI for this flow yet.
- `CandidateResult` is the per-candidate row. `extra_fields` JSON column holds dynamic header fields beyond the four fixed columns.
- Lightweight migrations (column adds only, no schema rewrites) live in `backend/db/database.py::_apply_lightweight_migrations`, called from `init_db()`.

### Per-run logging
- `backend/services/run_logger.py::attach_run_log` attaches a `FileHandler` to the parent `backend` logger only. Python's child→parent propagation delivers every backend.* log line to the file exactly once. (Earlier versions attached to every named child and got each line written twice.)
- Output: `storage/pipeline_runs/sub<id>_<ts>_<filename>.log`. Includes `LLM[stage] CALL` / `LLM[stage] OK` with prompt size, latency, finish_reason, token usage, response preview; `MCQ[page/template] anchor score=...` lines; `MATHPIX submit pdf_id=... overlay urls_found=X urls_applied=Y` lines; per-page `PAGE[N] DONE answers=A mcq_overrides=B t=Xs`.

### Frontend
- React 18 + Vite + Tailwind. Entrypoint `frontend/src/App.jsx`.
- Uploads default to automatic paper detection; an optional template overrides it. File size comes from `/capabilities`, and tracking exposes queue and archive progress.
- API client `frontend/src/services/api.js` uses Axios; base URL is `VITE_API_BASE_URL` or `http://localhost:8000`.

## Configuration

All config via env vars (`.env`), managed by `backend/config.py`. Notable settings:

- `GEMINI_API_KEY` — required
- `GEMINI_MODEL` (default `gemini-2.5-flash`) / `GEMINI_FALLBACK_MODELS` (default `gemini-flash-latest`)
- `DATABASE_URL` — empty → SQLite at `./exam_db.sqlite`
- `ENABLE_IMAGE_PREPROCESSING` / `PREPROCESSING_MODE` (`balanced` | `aggressive`)
- `MAX_EXTRACTION_WORKERS` (default 6; roughly halves wall-clock vs 3, peak working set ~150MB. The OOM ceiling on a 27GB host with 5 backends in parallel was 10 — do not exceed 6 without re-benchmarking.)
- `MAX_PDF_RENDER_WORKERS` (3) / `PDF_RENDER_GRAYSCALE` (true) / `MAX_CLASSIFY_WORKERS` (3) / `MAX_FR_JUDGE_WORKERS` (6)
- `MAX_OCR_WORKERS` (3) / `OPENCV_THREADS` (1) / `OCR_TIMEOUT_SECONDS` (15) — see the CPU budget note below
- `GEMINI_MAX_RPM` (4.0) / `GEMINI_REQUEST_TIMEOUT_SECONDS` (60) / `GEMINI_TRANSIENT_RETRIES` (2)
- `QUEUE_ENABLED` (false in-repo; `docker-compose.yml` turns it on) / `QUEUE_BATCH_PAGES` (10) / `QUEUE_LEASE_SECONDS` (120) / `QUEUE_MAX_ATTEMPTS` (3)
- `MAX_FILE_SIZE_MB` (3072) / `MAX_UPLOADS` (2) / `MIN_FREE_DISK_GB` (20) / `MAX_PDF_PAGES` (10000)
- `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` — optional. Mathpix supplies a tighter diagram crop when configured; a template-region crop is always produced regardless, so diagram marking works without it.
- `DIAGRAM_VISION_ENABLED` (default true) / `DIAGRAM_VISION_MODEL` (empty inherits `GEMINI_MODEL`)
- `PAGE_PREVIEW_DPI` (default 150) — render DPI for the results UI's page viewer
- `MATHPIX_POLL_INTERVAL_SECONDS` (default 3.0) / `MATHPIX_MAX_WAIT_SECONDS` (default 600.0)
- `SPACES_*` / `ARCHIVE_ENABLED` — optional private queued DO Spaces archival; `ARCHIVE_EVICT_LOCAL` defaults false

> Gemini calls intentionally do NOT pass `max_output_tokens`. See `template_extractor.py::_llm_extract_full` and the comment in `config.py`.

## Layout policy

Default upload mode **auto-detects layout per page** from the printed footer
(e.g. `SEAMO X 2026 Paper B`), then extracts and marks each page with the
matching template / answer key. Mixed PDFs are supported; output may contain
multiple `template_id`s. The cascade is cheapest-first:

1. **Embedded PDF text** on a bottom clip (`classify_pdf_text_pages`) — no OCR at all
2. Tesseract on three raster bands (bottom 12%, bottom 22%, top 10%), with
   hand-tuned repairs for known OCR confusions (`SRAMO→SEAMO`, `£025→2025`)
3. Gemini on a top-30% crop, only for pages the first two missed
4. Document-year majority: a page that read a brand and paper but **no** year
   borrows the modal year of the pages that resolved on their own
   (`apply_document_year_majority`). It fills one absent field only — it never
   overrules a year that was read, never invents brand or paper, abstains on a
   tie, and the repaired page must still resolve to a real template. The method
   is suffixed `+doc_year` so the inference is auditable.

The bottom clip is deliberately narrow: OCR layers contain candidate numbers
that read like years. `resolve_layout_fields` is the only brand/year/paper → id
mapper; its SEAMO-X repair is anchored to `X` + year, and loosening that
lookahead promotes an ordinary SEAMO footer into the X series — a whole wrong
answer key.

Optional `?template_id=` on upload still forces a single layout for every page
(debug / override). If every stage fails for a page, that page is stored with
empty answers and a detection warning rather than guessing the wrong key.

Do not resurrect the deleted full-page OCR / clustering / auto-detect stack —
footer-band OCR is intentionally narrow and only used for layout ID.

## Deleted in the May 2026 simplification (do not resurrect)

`optimized_extractor.py`, `extraction_pipeline.py`, `ai_extractor.py`, `page_analyzer.py`, `ocr_engine.py`, `ocr_results_writer.py`, `section_extractor.py`, `worker.py`, `NewMcqSolution.py`, `page1_grid.json`, `train_from_examples.py`, `create_template.py`, `examples.py`, `tests/test_all_formats.py`, `tests/test_real_exams.py`. The old clustering / full-page Tesseract-OCR / Celery paths were removed. The September 2026 queue is a new durable implementation under `backend/queue/`; do not revive the removed worker architecture. Footer-band OCR for per-page layout ID (`page_layout_classifier.py`) is the intentional, narrow replacement — do not revive the deleted full-page OCR stack.
