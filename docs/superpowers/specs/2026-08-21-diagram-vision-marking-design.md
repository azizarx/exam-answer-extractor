# Diagram Vision Marking + Exam Paper Viewer — Design

**Date:** 2026-08-21
**Status:** Approved for planning

## Problem

Diagram/graphical answers are not being awarded marks in the deployed system, even
though answer keys exist for them. Separately, there is no way to look at a
candidate's actual scanned paper from the results UI, which makes any answer that
needs human review impossible to verify without digging into the source PDF by hand.

## Root cause

Diagram questions are fully wired into marking already. `marking_service.py:24`
admits `diagram` as an allowed type, `marking_service.py:30` maps it to the `text`
normalizer, and `marking_service.py:518-524` gives it the *wider* LLM-judge fallback
(`incorrect` and `invalid` both escalate). Nothing skips diagram questions.

The break is a single write. After every page is extracted, the Mathpix overlay
replaces the LLM's descriptive answer with a raw CDN URL:

```python
# backend/services/template_extractor.py:1529-1530
answers = candidates[target_i].setdefault("answers", {})
answers[str(active_q)] = url
```

That URL is then text-normalized and compared against prose accepted answers, which
can never match, so the question scores 0. It is applied *after* `needs_review` is
computed (`template_extractor.py:1151-1158`), so nothing flags it either — the
question is recorded as `trusted` and silently scores zero.

Evidence: `storage/results/20260516154727_af42fb38_PAPER_A.json` holds 60 URL-valued
answers from a run where Mathpix succeeded. Local runs mark diagrams correctly only
because Mathpix has been failing — `storage/pipeline_runs/sub97_20260801T181347Z_ZONE_Z.pdf.log`
records `MATHPIX submit failed: ConnectionError` and `diagram answers will remain LLM
defaults`, and that submission's diagram questions marked normally.

Beyond the hotfix, the current design is lossy in a way that caps accuracy: the mark
is decided by *drawing -> canonical string -> string match against prose*, two
independent failure points. Local outcome counts for `seamo_x_2026_a` are Q4 33
correct / 62 incorrect, Q6 60/35, Q9 16/74; `seamo_x_2026_b` Q5 56/34. Q9 at 16% is
more plausibly the text round-trip failing than 84% of candidates being wrong.

## Goals

1. Diagram questions are marked by comparing the candidate's drawing against the
   answer key's reference drawing, as images.
2. The Mathpix URL never lands in `answers` again.
3. A reviewer can open the candidate's actual scanned page from the results UI.
4. A reviewer can see, for a diagram row, exactly what the judge compared.

## Non-goals

- Re-marking the 99 existing submissions. The vision judge applies to new
  submissions only. Existing marks are left untouched.
- Partial credit. Diagram marking stays all-or-nothing, matching every other type.
- Any change to MCQ, numeric, time, or free-response marking.
- Answer-key authoring UI, or answer keys for the `seamo_2026_*` templates.

## Scope

Four questions across two templates, and no others:

| Template | Diagram questions | Key manifest |
|---|---|---|
| `seamo_x_2026_a` | Q4 (clock), Q6 (2x2 grid), Q9 (12-sector pie) | `answer_keys/seamo_x_2026/seamo_x_2026_a.json` v2 |
| `seamo_x_2026_b` | Q5 (triangle network) | `answer_keys/seamo_x_2026/seamo_x_2026_b.json` v2 |

Verified by scanning all 14 manifests and all 35 template files.

## Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Mathpix locates the crop; the extractor downloads and stores it locally | Mathpix's figure detection removes the need to hand-calibrate crop geometry, but its CDN URLs decay (a May-2026 job's URLs now return HTTP 500 while an August job's still resolve) and marking is re-runnable by design. Storing the bytes makes re-marks work forever. |
| D2 | Every page also saves a template-region crop, unconditionally | Mathpix is intermittent (`ConnectionError` in `sub97`) and may be unconfigured on the remote. A local fallback means the vision judge never depends on a third party. It also yields a direct quality comparison between the two crop sources. |
| D3 | The vision judge replaces the text path for `type: diagram` | One source of truth for the mark. The extractor's prose description is kept only as fallback display text. |
| D4 | The judge runs at marking time, not extraction time | Marking is independently re-runnable (`POST /submission/{id}/mark`); extraction is not. Crops are produced at extraction and consumed at marking. |
| D5 | Two labelled images per pair, not one composited canvas | The repo already sends multi-image calls (`template_extractor.py:381-385`, pinned at `tests/test_template_extractor.py:391-398`), so no transport work is needed and no compositing helper has to be written. Reference crops are ~190px wide; rescaling them into a shared canvas loses detail that the comparison depends on. |
| D6 | Reference images live at a conventional path, not in the manifest | `ManifestQuestion` is a dataclass whose `asdict()` feeds `question_spec`. Adding any field — even one defaulting to `None` — changes the canonical JSON for every question and trips `_validate_immutable_answer_key` (`marking_service.py:717-739`) for all 14 keys at startup. Convention avoids a 14-manifest version bump. |
| D7 | Images reach the browser as blobs via axios, not `<img src>` | `ApiKeyMiddleware` (`backend/services/api_auth.py`) gates every non-public path when `API_KEY` is set. `<img src>` cannot send `X-API-Key`. Blob fetch + `URL.createObjectURL` matches the existing `getMarkedJSONDownload` pattern and stays correct if `API_KEY` is later enabled. |
| D8 | The viewer is a toggled panel inside the existing modal | `CandidateDetailModal` is the app's only dialog and its Escape/focus-trap logic is inline (`CandidateDetailModal.jsx:98-124`). A second modal would duplicate that logic and stack z-indexes. |
| D9 | The "view paper" button sits in the modal header, not on the card face | The card is a single `<button>` (`ResultsDisplay/index.jsx:377`); a nested button is invalid HTML and would require restructuring the card. |

## Architecture

### 1. Hotfix — stop the URL overwrite

`_apply_diagram_urls` (`template_extractor.py:1471-1559`) no longer writes into
`answers`. The LLM's prose description remains the answer. The URL is recorded at
`candidate["extra_fields"]["diagram_sources"][str(q)]` for provenance and for the
download step.

This is shippable on its own and should go out first: the moment the remote gains
Mathpix credentials, every diagram question there silently scores 0.

`tests/test_template_extractor.py:218-334` currently asserts the URL-as-answer
behaviour across five tests. They are rewritten to assert the answer is unchanged and
the URL landed in `extra_fields.diagram_sources`.

### 2. Reference diagram extraction (one-time, offline)

`answer_keys/seamo_x_2026/Paper A key.pdf` embeds the correct diagrams as PNGs
(Q4 191x186, Q6 176x159, Q9 97x94, alongside a 359x79 logo). Paper B embeds one
(Q5 207x161).

A script, `scripts/extract_reference_diagrams.py`, maps each embedded image to a
question by locating the `Q4`/`Q6`/`Q9` header words with `page.get_text("words")`
and matching the image rect to the cell beneath. It writes:

```
answer_keys/seamo_x_2026/reference_diagrams/
    seamo_x_2026_a_q4.png
    seamo_x_2026_a_q6.png
    seamo_x_2026_a_q9.png
    seamo_x_2026_b_q5.png
    provenance.json     # {template_id, question, source_pdf, source_sha256, image_sha256}
```

Outputs are committed and reviewed visually once. The script is idempotent and
re-runnable when a key PDF changes.

`ManifestRegistry` gains a startup check: every question with `type == "diagram"`
must have a reference file on disk, else `ManifestValidationError`. This makes a
missing reference a boot-time failure rather than a silent marking failure.

### 3. Crop sourcing and storage

Two producers, both writing to the same place:

**Template-region crop (always).** Inside each page worker, for every diagram
question in the page's template, crop `question_overrides[q].region` from the page
image (scaled by actual/reference page size) and save it. The page image is in hand
here, well before the cleanup in `routes.py:705-710`.

This requires recalibrating the four `region` boxes. The current values clip the
drawing — `seamo_x_2026_a` Q4 is `{x:100, y:1810, w:715, h:280}`, which captures the
"Question 4" heading and the top arc of the clock while cutting off the hands.

Recalibration is side-effect free. `override.region` is consumed only by
`_fr_question_crop_specs` (`template_extractor.py:906`, use at `:966-978`), and
`_fr_model_crop_specs` only delegates there when `len(column_counts) <= 1 or
sum(column_counts) != count`. Both affected templates take the column path
unconditionally — `seamo_x_2026_a` is `questions_per_col: [6, 7, 7]` over 20
questions and `seamo_x_2026_b` is `[5, 5, 5]` over 15, so in each case the list has
3 entries and sums to the question count. These region values are therefore dead
config today. The LLM currently receives the
whole column crop labelled `mixed_response`, which is unaffected.

**Mathpix crop (when available).** After the overlay resolves a URL for a diagram
question, the extractor GETs the image immediately — while the URL is fresh — and
saves it, overwriting the template-region crop's entry as the preferred source.

**Layout.**

```
storage/diagrams/sub{submission_id}/p{page_number}_q{question}.png
```

recorded on the candidate as:

```json
"extra_fields": {
  "diagram_crops": {
    "4": {"path": "diagrams/sub97/p3_q4.png", "source": "mathpix"},
    "6": {"path": "diagrams/sub97/p3_q6.png", "source": "template_region"}
  },
  "diagram_sources": {"4": "https://cdn.mathpix.com/cropped/..."}
}
```

`extra_fields` is a JSON column, so no DB migration is needed. Both new keys are
added to `FORBIDDEN_DISPLAY_KEYS` (`ResultsDisplay/index.jsx:65-75`) so they do not
render as card chips.

Volume is negligible: at most 4 crops per page at roughly 10-30KB each. Deletion is
wired into `DELETE /submission/{id}` (`routes.py:1100`) alongside the PDF and result
JSON.

### 4. The vision judge

New module `backend/services/diagram_vision_judge.py`, modelled on
`backend/services/fr_equivalence_judge.py` — the cleanest precedent for adding a
judge with its own prompt builder, parser, and injectable model.

One call per candidate, covering all of that candidate's diagram questions:

```
prompt:
  - comparison rules (adapted from fr_equivalence_judge.py:44-52: compare only the
    candidate-variable content — drawn marks, hands, shading, fills — and treat
    preprinted scaffold as fixed)
  - per question: number, marks, the key's accepted_answers as textual context,
    and the template's prompt_hint
contents:
  "IMAGE 1: STUDENT Q4"   <student crop PIL>
  "IMAGE 2: CORRECT Q4"   <reference crop PIL>
  "IMAGE 3: STUDENT Q6"   ...
```

Sent through `run_logger.llm_call(stage="diagram_vision", ...)`
(`run_logger.py:231`) with `GenerationConfig(temperature=0.0,
response_mime_type="application/json")`, matching every other call in the repo.
Rate limiting, transient retry, and per-run logging come free from that wrapper.

Response, one object per question:

```json
{"question_number": 4, "verdict": "match|mismatch|uncertain",
 "observed": "Clock: 8:45", "reason": "Minute hand at 9, hour hand past 8; key shows hour hand between 7 and 8."}
```

`observed` becomes the outcome's displayed `response`, replacing the extractor's
prose description — it is what the model actually saw when deciding the mark.

**Blank short-circuit.** Before spending a call, each student crop is ink-tested
(mean-ink threshold reused from `diagram_cv.py:103-107`). A blank crop yields
`status="blank"`, `awarded=0`, no call — mirroring how blank answers already bypass
the FR judge (`marking_service.py:523-524`).

**Cost.** One extra call per candidate that has diagram questions. Because
`_acquire_gemini_token` (`run_logger.py:202-228`) is a process-wide sliding-window
bucket, this adds paced wall-clock rather than 429s: roughly `N / GEMINI_MAX_RPM`
minutes. A 20-page PDF adds ~20s at the local `GEMINI_MAX_RPM=60` but ~5 minutes at
the shipped default of 4 (`config.py:89`). Raising the remote's `GEMINI_MAX_RPM` is
a deployment note, not a code change.

### 5. Marking integration

- `diagram` is removed from `FR_TYPES` (`marking_service.py:454`) so the text
  equivalence judge no longer touches it.
- New stage `apply_diagram_vision_judge(result, manifest, judge, crops)` in
  `marking_service.py`, added to `marking_workflow._mark_one` (`:323-325`) after
  `apply_extraction_trust`:

  ```
  mark -> apply_extraction_trust -> apply_fr_equivalence_judge -> apply_diagram_vision_judge
  ```

- The deterministic pass still runs over diagram questions and is harmless; the
  vision stage overwrites those outcomes, exactly as the FR judge already overwrites
  its own.
- `apply_extraction_trust` runs first and continues to win: a diagram question
  flagged `needs_review` during extraction is not sent to the judge.

**Verdict mapping.**

| Verdict | status | awarded | judge_source |
|---|---|---|---|
| `match` | `correct` | full marks | `diagram_vision` |
| `mismatch` | `incorrect` | 0 | `diagram_vision` |
| `uncertain` / malformed / missing | `needs_review` | 0 | `diagram_vision` |
| judge raised | `needs_review` | 0 | `diagram_vision_error` |
| crop missing on disk | `needs_review` | 0 | `diagram_crop_missing` |

This mirrors `marking_service.py:578-592` and `:537-556`. No new `QuestionOutcome`
fields are needed — `judge_source`, `judge_verdict`, and `judge_reason` already exist
and are already exposed through `QuestionOutcomeSchema` (`schemas.py:16-25`).

### 6. API surface

Three new endpoints, all under prefixes that `deploy/nginx-aimarker.conf:26` already
proxies (`submission`, `answer-keys`), so no nginx change is required.

| Endpoint | Returns |
|---|---|
| `GET /submission/{id}/page/{page_number}.png` | The candidate's scanned page, re-rendered on demand from `original_pdf_key` at `PAGE_PREVIEW_DPI` (default 150) |
| `GET /submission/{id}/candidates/{candidate_id}/diagram/{question}.png` | The stored student crop |
| `GET /answer-keys/reference/{template_id}/{question}.png` | The committed reference diagram |

Page images are deleted after extraction (`routes.py:705-710`), so the page endpoint
re-renders from the retained PDF. This is viable because `original_pdf_key` is
`nullable=False` and never deleted except by `DELETE /submission/{id}`, and
`CandidateResult.page_number` is populated on all 3,813 existing rows with an exact
1:1 mapping to PDF pages (`template_extractor.py:678` assigns `page_num = i + 1`
over the ordered page list). A 150-DPI single-page render is ~150ms; responses carry
`Cache-Control: private, max-age=3600`. No render cache until measurement shows one
is needed.

**Guards.** `page_number` is validated against `ExamSubmission.pages_count`.
`local_storage.get_absolute_path` (`:91-99`) performs no containment check on the
joined relative path, so every one of these endpoints resolves and asserts the final
path is inside `settings.storage_root` before opening it. `question` is validated
against the template's diagram question set rather than used as a raw path segment.

`page_number` is added to `CandidateResultSchema` (`schemas.py:107-141`) and to the
constructor at `routes.py:939-955`, so the UI knows which page to request. The
exported results JSON is left unchanged.

### 7. Frontend

`frontend/src/services/api.js` gains three blob-returning methods —
`getPageImage`, `getDiagramCrop`, `getReferenceDiagram` — each following the existing
`getMarkedJSONDownload` blob pattern (`api.js:175-187`) and returning an object URL.
Callers revoke object URLs on unmount.

**Paper viewer.** A `FileText`-icon button joins Download and Close in the modal
header (`CandidateDetailModal.jsx:184-200`), reusing that file's icon-button classes.
It toggles a panel within the modal's scroll body showing the rendered page with
zoom and pan implemented as a CSS `transform` driven by wheel and pointer-drag
handlers. No new dependency: `frontend/package.json` carries no viewer library and
does not need one.

**Diagram rows in the outcomes table.** For rows whose question is a diagram, the
table (`CandidateDetailModal.jsx:264-311`) renders the student crop beside the
reference crop, with `judge_reason` beneath — so the awarded mark is auditable
without leaving the modal. Non-diagram rows are unchanged. The frontend identifies
diagram rows by `judge_source` starting with `diagram_`.

## Error handling

Every failure degrades to a reviewable state rather than an exception, consistent
with the rest of the pipeline:

- Mathpix unavailable or the download fails: the template-region crop remains, and
  the judge runs normally.
- Both crops missing: `needs_review` with `judge_source="diagram_crop_missing"`.
- Judge call raises: `needs_review` with `judge_source="diagram_vision_error"` and
  the exception in `judge_reason`, mirroring `marking_service.py:537-556`.
- Malformed judge JSON: those questions become `needs_review`; the run does not fail.
- Page endpoint: 404 for unknown submission, 404 for out-of-range page, 410 when
  `original_pdf_key` no longer resolves on disk.
- Crop endpoint: 404 when the crop was never produced (all pre-existing submissions).
  The frontend shows a "no crop stored" placeholder rather than a broken image.

All new modules log under `backend.services.*`, so `attach_run_log`
(`run_logger.py:52-83`) captures them in `storage/pipeline_runs/` with no extra
wiring.

## Testing

| Area | Tests |
|---|---|
| Hotfix | Rewrite `tests/test_template_extractor.py:218-334` to assert `answers` keeps the prose description and the URL lands in `extra_fields.diagram_sources` |
| Crops | Template-region crop written per diagram question; Mathpix crop overwrites and tags `source`; crop path recorded; region crop still produced when Mathpix is absent |
| Regions | Each recalibrated region, applied to a committed sample page, produces a crop containing the full drawing (ink present in the lower half — the exact failure of the current boxes) |
| Reference extraction | `scripts/extract_reference_diagrams.py` maps all 4 questions correctly; `provenance.json` hashes match; the registry's startup check fails when a reference file is missing |
| Vision judge | Prompt carries correctly ordered `STUDENT`/`CORRECT` labels and image pairs; verdict parsing for all four verdict paths; blank crop short-circuits without a call. Uses the existing `FakeModel`/`_StubGeminiResponse` scaffolding (`tests/test_template_extractor.py:340-380`) — no network |
| Marking | `diagram` no longer reaches the FR text judge; vision verdicts map to the right status and marks; `apply_extraction_trust` still wins; missing crop yields `diagram_crop_missing` |
| Endpoints | Page bounds rejection; path-containment guard rejects a crafted `original_pdf_key`; correct content types; 404/410 paths |

## Risks

| Risk | Mitigation |
|---|---|
| The vision judge is *worse* than the current text path on some question (Q6 currently lands 60/95) | Both crop sources are stored and every verdict carries `judge_reason`, so a regression is diagnosable per question. `DIAGRAM_VISION_ENABLED` (default true) reverts to the text path without a deploy. |
| Region recalibration is done against one sample scan and misses skew on others | Regions are validated against multiple committed sample pages, and the Mathpix crop remains preferred when present. Anchor priming already normalizes page alignment upstream. |
| Extra LLM call slows large batches at `GEMINI_MAX_RPM=4` | One call per candidate, not per question. Deployment note to raise the remote's RPM. |
| `storage/diagrams` grows unbounded | Crops are deleted with their submission. `storage/uploads` already has the same unbounded-growth property with 112 PDFs and no pruning job; retention policy stays out of scope here. |

## Sequencing

| Phase | Content | Depends on |
|---|---|---|
| 0 | Hotfix: stop the URL overwrite; rewrite the five tests that pin it | nothing |
| 1 | Reference diagram extraction script + committed PNGs + registry startup check | nothing |
| 2 | Region recalibration + template-region crop persistence + Mathpix crop download | 0 |
| 3 | Vision judge + marking integration + config flags | 1, 2 |
| 4 | Page endpoint + `page_number` in schema + viewer panel + inline diagram crops in the outcomes table | 2 for the crop display; the page viewer alone depends on nothing |

Phase 0 and the page-viewer half of Phase 4 are independently shippable and should
go out first — 0 because it is a live landmine, 4 because it delivers standalone
review value.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `DIAGRAM_VISION_ENABLED` | `true` | Kill switch reverting diagram marking to the text path |
| `DIAGRAM_VISION_MODEL` | `""` (inherits `GEMINI_MODEL`) | Lets the comparison run on a different model than extraction |
| `DIAGRAM_CROP_DPI` | `300` | Render DPI for template-region crops; matches the templates' reference DPI |
| `PAGE_PREVIEW_DPI` | `150` | Render DPI for the paper viewer |

Deployment note: the remote should set `GEMINI_MAX_RPM` well above the shipped
default of 4 before Phase 3 lands.
