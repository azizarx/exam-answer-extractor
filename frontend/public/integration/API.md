# AI Marker API — Integration Guide

**Contract reviewed: 20 September 2026.** For backend developers integrating PDF answer-sheet extraction, AI-assisted marking, and human review. All examples use synthetic candidate data and a five-question demonstration key; IDs, timestamps, filenames, and totals are illustrative.

## 1. Connection and integration choice

| Resource | Address |
| --- | --- |
| Production API base URL | `https://aimarker-bk.seamo-official.org` |
| Browser application | `https://aimarker.seamo-official.org` |
| Interactive endpoint reference | [Swagger UI](https://aimarker-bk.seamo-official.org/docs) |
| Live OpenAPI schema | [openapi.json](https://aimarker-bk.seamo-official.org/openapi.json) |
| Downloadable guide and client files | [Integration documentation](https://aimarker.seamo-official.org/integration/) |

**For AI marking, use the persisted upload workflow.** It supplies candidate IDs, automatic marking, human review, cancellation, and downloadable marked results.

| Capability | `POST /upload` workflow | `POST /extract/json` | `POST /extract/json/mark` |
| --- | --- | --- | --- |
| HTTP interaction | Upload returns a task ID; poll separately | Wait for extraction response | Wait for extraction and marking response |
| Extract answers and identity | Yes | Yes | Yes |
| Deterministic weighted marking | Automatic, when keys exist | No | Yes, when keys exist |
| Extraction trust checks during marking | Yes | No marking | **No** |
| AI free-response equivalence / diagram judging | Yes, subject to server configuration | No marking | **No** |
| Persisted candidate IDs, review, marking history | Yes | No | No |
| Cancel endpoint | Yes | No | No |
| Success response body | Upload acknowledgment | `document_information` + `candidates` | `filename` + `template_id` + `mode` + `total_candidates` + `candidates` |

The synchronous marking endpoint currently calls the deterministic marker directly. It is not equivalent to the full AI marking workflow. Choose it only when that narrower behavior meets your requirements. No JSON-only "submit answers for AI marking" endpoint or caller-supplied answer-key endpoint is currently exposed.

### Authentication

Send either header on requests that require authentication:

```http
X-API-Key: YOUR_API_KEY
```

```http
Authorization: Bearer YOUR_API_KEY
```

When configured, the API uses one shared deployment key; `X-API-Key` takes precedence if both headers are present. Obtain the key from the service operator and keep it in your backend. This is not per-user authentication or tenant isolation: caller-level permissions and mapping submissions to your users belong in your application.

Authentication enforcement is deployment-configurable. `GET /` returns `api_key_required`; do not infer it from the availability of the docs. `/`, `/health`, `/docs`, `/redoc`, `/openapi.json`, and `/favicon.ico` are public. Invalid or missing credentials on protected routes return **401**:

```json
{"detail":"Missing or invalid API key. Send X-API-Key or Authorization: Bearer."}
```

Server-to-server callers do not need CORS configuration. Browser callers must use origins permitted by the deployment. The live OpenAPI schema does not currently declare the middleware's authentication schemes; include the header explicitly in your HTTP client.

The browser application prompts for the API key when authentication is enabled. It retains the entered key in the current tab's session and sends it with API requests, including image downloads. Use **Forget API key** to clear it. Developer documentation remains publicly accessible; the deployment key is never embedded in the frontend build.

### Common conventions

- Request and response JSON is UTF-8. File uploads use `multipart/form-data` with a field named **`file`**. Let your HTTP library generate the multipart boundary.
- Submission, candidate, marking-run, and answer-key IDs are integers with different meanings. Store the IDs returned by the API; do not predict the next task number.
- Candidate numbers are **strings** and may contain leading zeros. They are extracted identifiers, not guaranteed unique database IDs.
- PDF page numbers start at **1**. Question keys in `answers` are strings such as `"12"`; `outcomes[].question_number` is an integer.
- Timestamps represent UTC. Most current responses serialize UTC timestamps **without a trailing `Z` or offset**; interpret these as UTC. Human-edit audit timestamps include `Z`.
- Missing identity fields can be `null` in raw extraction and are normalized to `""` in persisted candidate responses. Empty drawing payloads commonly serialize as `null`.
- Dynamic extraction and `extra_fields` objects may gain keys. Read documented fields and tolerate additional metadata.

## 2. Quick start: upload → poll → marked results

The upload workflow performs both extraction and automatic marking. **A completed submission does not guarantee a completed or complete-coverage marking run.** Check both states before accepting scores.

```bash
export API_BASE="https://aimarker-bk.seamo-official.org"
export API_KEY="YOUR_API_KEY"

# 1. Discover supported layouts and installed answer-key versions.
curl --fail-with-body "$API_BASE/templates/all" -H "X-API-Key: $API_KEY"
curl --fail-with-body "$API_BASE/answer-keys" -H "X-API-Key: $API_KEY"

# 2. Upload; retain submission_id from the JSON response.
curl --fail-with-body -X POST "$API_BASE/upload" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@example-exam.pdf;type=application/pdf"

# 3. Replace 1 with the returned ID. Poll about every three seconds.
curl --fail-with-body "$API_BASE/status/1" -H "X-API-Key: $API_KEY"

# 4. Once completed, inspect candidates and the latest marking state.
curl --fail-with-body "$API_BASE/submission/1" -H "X-API-Key: $API_KEY"
curl --fail-with-body "$API_BASE/submission/1/marking" -H "X-API-Key: $API_KEY"

# 5. A complete marking run covering every candidate can be exported.
curl --fail-with-body "$API_BASE/submission/1/marked-json" \
  -H "X-API-Key: $API_KEY" -o example-exam.marked.json
```

A runnable [Python client](https://aimarker.seamo-official.org/integration/integration_client.py) follows this flow, streams PDF uploads in bounded chunks, saves the extracted response separately, supports resuming polling by submission ID, and reports questions needing review. Install `requests`, then run:

```bash
python integration_client.py example-exam.pdf --output example-exam.marked.json
# Resume without uploading or processing the same file again:
python integration_client.py --submission-id 1 --output example-exam.marked.json
```

Before uploading, the client saves an `.upload.json` receipt containing an idempotency key. If the upload outcome is unknown, repeat the command with `--idempotency-key <saved-key>` and the same PDF/options. It never retries a POST automatically. The client reads `API_BASE` and `API_KEY` from the environment. Download all examples, the client, this guide, and an OpenAPI snapshot as the [integration pack](https://aimarker.seamo-official.org/integration/integration-pack.zip).

## 3. Upload inputs and task lifecycle

### POST /upload

**Input**

| Location | Name | Type | Required | Meaning |
| --- | --- | --- | --- | --- |
| Multipart form | `file` | Binary PDF with a `.pdf` filename | Yes | Scanned answer sheets |
| Query string | `template_id` | String | No | Force one layout on every page; omit for per-page detection |
| Header | `Idempotency-Key` | String, up to 200 characters | No | Reconcile a repeated upload of the same PDF/options |
| Header | `X-Content-SHA256` | 64 hexadecimal characters | No | Verify the uploaded PDF bytes against this checksum |

Do not put `template_id` in a JSON body. For example: `/upload?template_id=seamo_2025_a`.

Automatic mode supports mixed-layout PDFs. Detection uses embedded PDF text and narrow OCR bands, with an AI header fallback when needed. An unreadable or unsupported layout can produce a candidate with empty answers, `template_id: null`, and a detection warning. HTTP success does not imply that every page was readable.

The filename extension is checked at upload time; malformed PDFs can fail later during processing. There is no caller-supplied callback URL, grading rubric, or answer-key selection field on `/upload`. Upload reconciliation uses the optional `Idempotency-Key` header.

**Output — HTTP 200, `application/json`** (the current API returns 200, not 202):

```json
{
  "status": "success",
  "message": "PDF uploaded successfully. Processing started.",
  "submission_id": 1,
  "filename": "example-exam.pdf",
  "storage_path": "uploads/example-exam.pdf"
}
```

`storage_path` is a server-relative storage reference, not a public download URL. The source PDF is retained for page previews and review. A task corresponds to an `ExamSubmission`; `/track/{submission_id}` is a browser UI route, not an API endpoint.

### GET /status/{submission_id}

No body. Returns **200** with these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `submission_id`, `filename` | Integer, string | Task identity |
| `status` | String | `pending`, `processing`, `completed`, `failed`, or `cancelled` |
| `created_at`, `processed_at` | UTC timestamp, nullable UTC timestamp | Acceptance and terminal timestamps; failed processing may leave `processed_at` null |
| `pages_count` | Integer | Number of rendered pages; can remain 0 during conversion |
| `candidates_count`, `answers_count`, `drawing_count` | Integers | Counters, often 0 until completion; not a reliable progress percentage |
| `error_message` | String or null | Failure details, when available |
| `current_page`, `current_candidate_name` | Integer or null, string or null | Best-effort progress; may be null for the entire run |

An accepted task before work starts:

```json
{
  "submission_id": 1,
  "filename": "example-exam.pdf",
  "status": "pending",
  "created_at": "2026-09-09T12:00:00",
  "processed_at": null,
  "pages_count": 0,
  "candidates_count": 0,
  "answers_count": 0,
  "drawing_count": 0,
  "error_message": null,
  "current_page": null,
  "current_candidate_name": null,
  "stage": null,
  "queue_position": null,
  "pages_completed": 0,
  "attempt": 0,
  "job_id": null,
  "archive_status": null,
  "archive_error": null
}
```

A completed task:

```json
{
  "submission_id": 1,
  "filename": "example-exam.pdf",
  "status": "completed",
  "created_at": "2026-09-09T12:00:00",
  "processed_at": "2026-09-09T12:00:00",
  "pages_count": 1,
  "candidates_count": 1,
  "answers_count": 5,
  "drawing_count": 0,
  "error_message": null,
  "current_page": null,
  "current_candidate_name": null,
  "stage": null,
  "queue_position": null,
  "pages_completed": 0,
  "attempt": 0,
  "job_id": null,
  "archive_status": null,
  "archive_error": null
}
```

| State | Client action |
| --- | --- |
| `pending` | Continue polling; cancellation is available |
| `processing` | Continue polling; includes automatic marking time |
| `completed` | Fetch candidate details and inspect the marking run |
| `failed` | Stop polling; record `error_message`; investigate before a deliberate new upload |
| `cancelled` | Stop polling; no completed-result guarantee |

Uploads are persisted with a durable job record before acknowledgment. Separate workers process bounded page batches; pending work survives API/worker restarts. Shared OCR and Gemini limits apply across submissions. Retain the task ID, and use an `Idempotency-Key` header to reconcile a repeated upload rather than blindly creating another task.

### POST /submission/{submission_id}/cancel

No body. Available for `pending` and `processing` tasks. **200**:

```json
{
  "submission_id": 2,
  "status": "cancelled"
}
```

Cancellation is persistent and idempotent. The state changes to `cancelled` immediately; queued work is skipped and in-flight native/network work stops at the next checkpoint. Existing external OCR work is not revoked. The source PDF and any already-saved data are retained, but completed candidate details are not promised for a cancelled task.

A repeated cancellation returns 200. Cancelling a completed or failed task returns **409**; an unknown ID returns **404**. Closing the HTTP connection is not a cancellation request. This endpoint does not cancel a synchronous extraction request or a later manual re-mark of a completed submission.

## 4. Candidate results and marked export

### GET /submission/{submission_id}

Returns **200** only for a completed submission. Unknown IDs return **404**; all non-completed states return **400**. This is the primary persisted candidate response, with stable candidate IDs and page numbers.

| Top-level field | Type | Meaning |
| --- | --- | --- |
| `submission_id`, `filename`, `status` | Integer, string, string | Submission identity and state |
| `created_at`, `processed_at` | Timestamp, nullable timestamp | Submission timestamps |
| `candidates` | Array of candidate objects | Ordered by page number |
| `latest_marking` | Marking-run metadata or null | Latest run, including a failed or incomplete attempt |

| Candidate field | Type | Meaning |
| --- | --- | --- |
| `id` | Integer | Use as `candidate_id` in review/image endpoints |
| `page_number` | Integer or null | One-based source PDF page |
| `candidate_name`, `candidate_number`, `country`, `paper_type` | Strings | Extracted identity; empty string when unavailable |
| `template_id` | String or null | Detected or forced layout for this candidate |
| `detection` | Object or null | Detection method, raw OCR text, optional warning and layout fields |
| `answers` | Object mapping question-number strings to strings | Extracted answers, including MCQ and free-response values |
| `drawing_questions` | Object mapping strings to strings, or null | Legacy/additional drawing text; not the image itself |
| `extra_fields` | Object or null | Additional header fields, trust, drawing metadata, and manual-edit history |
| `marking` | Weighted marking object or null | This candidate's result from the latest run, if covered |

Full example, showing all five outcome states:

```json
{
  "submission_id": 1,
  "filename": "example-exam.pdf",
  "status": "completed",
  "created_at": "2026-09-09T12:00:00",
  "processed_at": "2026-09-09T12:00:00",
  "candidates": [
    {
      "id": 1,
      "candidate_name": "Sample Student",
      "candidate_number": "000123",
      "country": "VN",
      "paper_type": "A",
      "page_number": 1,
      "template_id": "seamo_2025_a",
      "detection": {
        "method": "footer_ocr",
        "raw_text": "SEAMO 2025 Paper A",
        "brand": "seamo",
        "year": "2025",
        "paper": "a"
      },
      "extra_fields": {
        "answer_trust": {
          "1": "trusted",
          "2": "trusted",
          "3": "trusted",
          "4": "trusted",
          "5": "needs_review"
        },
        "needs_review_questions": [
          "5"
        ]
      },
      "answers": {
        "1": "A",
        "2": "B",
        "3": "BL",
        "4": "IN",
        "5": "D"
      },
      "drawing_questions": null,
      "marking": {
        "candidate_result_id": 1,
        "candidate_number": "000123",
        "awarded_marks": 2.0,
        "max_marks": 10.0,
        "percentage": 20.0,
        "outcomes": [
          {
            "question_number": 1,
            "status": "correct",
            "response": "A",
            "awarded_marks": 2.0,
            "max_marks": 2.0,
            "normalizer": "uppercase",
            "judge_source": "deterministic",
            "judge_verdict": null,
            "judge_reason": null
          },
          {
            "question_number": 2,
            "status": "incorrect",
            "response": "B",
            "awarded_marks": 0.0,
            "max_marks": 3.0,
            "normalizer": "uppercase",
            "judge_source": "deterministic",
            "judge_verdict": null,
            "judge_reason": null
          },
          {
            "question_number": 3,
            "status": "blank",
            "response": "BL",
            "awarded_marks": 0.0,
            "max_marks": 1.0,
            "normalizer": "uppercase",
            "judge_source": "deterministic",
            "judge_verdict": null,
            "judge_reason": null
          },
          {
            "question_number": 4,
            "status": "invalid",
            "response": "IN",
            "awarded_marks": 0.0,
            "max_marks": 2.0,
            "normalizer": "uppercase",
            "judge_source": "deterministic",
            "judge_verdict": null,
            "judge_reason": null
          },
          {
            "question_number": 5,
            "status": "needs_review",
            "response": "D",
            "awarded_marks": 0.0,
            "max_marks": 2.0,
            "normalizer": "uppercase",
            "judge_source": "extraction_trust",
            "judge_verdict": "needs_review",
            "judge_reason": "flagged during extraction"
          }
        ]
      }
    }
  ],
  "latest_marking": {
    "id": 1,
    "status": "completed",
    "answer_key_id": 1,
    "provenance": {
      "answer_key_id": 1,
      "template_id": "seamo_2025_a",
      "version": 1,
      "source_filename": "example-key.pdf",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "total_marks": 10
    },
    "error_message": null,
    "created_at": "2026-09-09T12:00:00",
    "started_at": "2026-09-09T12:00:00",
    "completed_at": "2026-09-09T12:00:00",
    "updated_at": "2026-09-09T12:00:00"
  }
}
```

### GET /submission/{submission_id}/marked-json

Returns **200**, `Content-Type: application/json`, and an attachment `Content-Disposition` using the uploaded filename with `.marked.json`. Both ASCII `filename` and UTF-8 `filename*` may appear.

| Top-level field | Type | Contents |
| --- | --- | --- |
| `submission` | Object | `id`, `filename`, nullable `template_id`, `status`, `pages_count`, `created_at`, nullable `processed_at` |
| `marking` | Object | Latest marking-run metadata described in section 5 |
| `candidates` | Array | `id`, `page_number`, identity strings, `extra_fields`, `answers`, `drawing_questions`, and required `marking` |

Unlike `/submission/{id}`, exported candidate objects do **not** contain `template_id` or `detection`. Join by candidate `id` with the submission-detail response when you need those fields.

A full [marked-export response example](https://aimarker.seamo-official.org/integration/examples/marked_export.json) is included in the integration pack.

The latest marking run must be completed and cover every candidate. The endpoint returns **409** when there are no candidates, the latest run is not completed, or any candidate is missing a marking. A partially marked mixed-layout submission can therefore have a completed run and still be ineligible for export. Retrieve `/submission/{id}` or `/submission/{id}/marking` to inspect available results and missing coverage.

### GET /submission/{submission_id}/json

Returns the **original extraction JSON snapshot**, not the marked export. Its shape matches `/extract/json` in section 7. It is not refreshed after manual answer edits or re-marking. It has no persisted candidate IDs and removes each candidate's `page_number`.

Use `/submission/{id}` for current corrected answers and `/marked-json` for current scores. A missing result reference returns **404**; a referenced file that cannot be read can currently return **500**. Do not use availability of this raw snapshot as the task-completion signal.

## 5. Scoring, marking runs, and review flags

### Answer values versus grading outcomes

| Extracted value | Interpretation |
| --- | --- |
| `"A"`, `"B"`, etc. | Candidate's selected MCQ answer; allowed letters depend on the layout |
| `"42"`, `"3/4"`, other text | Transcribed numeric or free-response answer; retain it as a string |
| `"BL"` | Blank/unread text |
| `"IN"` | Invalid or multiple marks |
| `"DR"` | Drawing marker in legacy/compact payloads; not a grade or image URL |

Read `marking.outcomes` for grades. The current weighted API does not replace answers with legacy `P`/`IM` correctness codes. A `BL` text transcription on a diagram question can still have a visible drawing and receive marks from image comparison.

### Weighted marking object

| Field | Type | Meaning |
| --- | --- | --- |
| `candidate_result_id` | Integer | Persisted candidate ID (absent from synchronous marking output) |
| `candidate_number` | String | Extracted candidate number (persisted marking responses) |
| `awarded_marks` | Number | Sum of awarded question marks |
| `max_marks` | Number | Sum of marks in the selected key |
| `percentage` | Number | `100 × awarded_marks / max_marks`; do not assume fixed rounding |
| `outcomes` | Array | One outcome per key question |

| Outcome field | Type | Meaning |
| --- | --- | --- |
| `question_number` | Integer | Key question number |
| `status` | String | `correct`, `incorrect`, `blank`, `invalid`, `needs_review` |
| `response` | JSON scalar or null | Original response associated with the outcome |
| `awarded_marks`, `max_marks` | Numbers | Awarded and available weight |
| `normalizer` | String | Comparison normalization, e.g. `uppercase`, `integer`, `text`, `time_12_24` |
| `judge_source` | String or null | Origin, e.g. `deterministic`, `extraction_trust`, `llm`, `diagram_vision`, `diagram_cv` |
| `judge_verdict`, `judge_reason` | String or null | Decision and explanation where provided |

Correct answers receive the question's full weight; other outcomes currently receive zero. **`needs_review` is unresolved, not a final incorrect answer.** Totals containing it are provisional and should be reviewed before final publication. Do not implement grading by counting correct questions: weights may differ.

Collect review items from both `extra_fields.needs_review_questions` and outcomes with `status: "needs_review"`. The former tracks extraction uncertainty; later AI judging can introduce additional review outcomes. Treat judge-source/reason values as explanatory metadata rather than a closed enum or a correctness guarantee.

### GET /submission/{submission_id}/marking

No body. **200** with:

| Field | Type | Meaning |
| --- | --- | --- |
| `submission_id` | Integer | Task ID |
| `latest_run` | Run detail or null | Latest attempt, with a `candidates` array of weighted marking objects |
| `history` | Array of run metadata | Newest first; includes the latest attempt, without candidate outcomes |

No marking yet is represented by `{"submission_id":1,"latest_run":null,"history":[]}`. There is no public endpoint to fetch an arbitrary historical run's candidate outcomes by run ID; archive exports in your system if you need them.

| Run metadata field | Type | Meaning |
| --- | --- | --- |
| `id` | Integer | Marking-run ID |
| `status` | String | `unavailable`, `processing`, `completed`, or `failed` |
| `answer_key_id` | Integer or null | Representative stored key ID |
| `provenance` | Object or null | `answer_key_id`, `template_id`, `version`, `source_filename`, `source_sha256`, `total_marks` (each nullable) |
| `error_message` | String or null | Error or partial-coverage explanation |
| `created_at`, `updated_at` | UTC timestamps | Run timestamps |
| `started_at`, `completed_at` | Nullable UTC timestamps | Processing boundaries |

| Marking state | Client action |
| --- | --- |
| `unavailable` | No usable active key; coordinate key provisioning with the operator |
| `processing` | Poll marking state; do not start a duplicate re-mark |
| `completed` | Inspect review outcomes, `error_message`, and candidate coverage |
| `failed` | Preserve extracted answers; investigate and deliberately retry marking if appropriate |

The public provenance object exposes the **first key only** for a mixed-key run. It is not a complete per-candidate key audit. The latest run is returned even if it failed; the API does not automatically substitute an older successful run. Stale processing runs may be recovered as failed by status/detail reads.

### POST /submission/{submission_id}/mark

Run marking again on a completed submission's persisted answers without re-extracting the PDF. This call is synchronous and can take minutes.

**Input:** no body, `{}`, or `application/json`:

```json
{
  "answer_key_id": 1
}
```

Omitting the key selects active stored keys by each candidate's layout. The persisted workflow supports configured layout-to-key aliases. In a mixed submission, an explicit key overrides its matching layout's key; other layouts still use their own active keys.

**Output — 200:** `status: "success"`, `submission_id`, `total_candidates_marked`, and `run` (the run-detail object). **Inspect `run.status`**: an HTTP 200 / `status: "success"` wrapper can contain a failed or unavailable marking run.

Non-completed submissions, concurrent marking, and submissions with no candidates return **409**. Missing submission or requested key returns **404**. Each deliberate re-mark can create a new run; this POST is not idempotent. If its response is lost, query marking state before retrying.

## 6. Human corrections and image I/O

### POST /submission/{submission_id}/candidates/{candidate_id}/confirm-review

**Input — `application/json`**. The body object is required; every field inside it is optional:

| Field | Type | Default / behavior |
| --- | --- | --- |
| `answers` | Object mapping question strings to answer strings | `{}`; merge only supplied answers, preserve other questions |
| `candidate_name`, `candidate_number`, `country`, `paper_type` | String or null | Omitted/null preserves the value; `""` clears it |
| `clear_all_review` | Boolean | `false`; when true, clears all extraction review flags |
| `remark` | Boolean | `false`; when true, re-mark the edited candidate and carry other candidate marks forward |
| `edited_by` | String or null | Audit label supplied by your application; not an authenticated user identity |

Example: confirm an uncertain answer after inspecting the scan and refresh its grade:

```json
{
  "answers": {
    "5": "D"
  },
  "remark": true,
  "edited_by": "reviewer-42"
}
```

**Output — 200:**

```json
{
  "status": "success",
  "candidate_result_id": 1,
  "needs_review_questions": [],
  "answers": {
    "1": "A",
    "2": "B",
    "3": "BL",
    "4": "IN",
    "5": "D"
  },
  "candidate_name": "Sample Student",
  "candidate_number": "000123",
  "country": "VN",
  "paper_type": "A",
  "edits_recorded": 0,
  "remarked": true,
  "marking_run_id": 2,
  "remark_error": null,
  "remark_job_id": null
}
```

The response includes the full current answer map, identity strings, remaining `needs_review_questions`, number of actual edits, and re-mark outcome. Supplying an answer also clears its extraction review flag, even when the value is unchanged. `clear_all_review` confirms all flagged answers, so use it only after actual review.

Edits are committed before re-marking. **HTTP 200 can mean the edit succeeded but re-marking did not**: check `remarked`, `marking_run_id`, and `remark_error`. With `remark: false`, existing scores remain unchanged and can be stale relative to edited answers. Re-fetch submission details after a successful re-mark.

Actual value changes append `extra_fields.manual_edits` entries with `target`, `from`, `to`, `at`, and `by`. Clearing a review flag without changing the value does not add a value-change entry. Missing submission or a candidate belonging to another submission returns **404**. Wait until submission processing and marking are complete before editing.

### Image endpoints

All return PNG bytes (`Content-Type: image/png`), not JSON or base64:

| GET path | Input / purpose | Errors |
| --- | --- | --- |
| `/submission/{submission_id}/page/{page_number}.png` | One-based source page for human review | 404 unknown submission/out-of-range page; 410 source PDF missing; 500 render failure |
| `/submission/{submission_id}/candidates/{candidate_id}/diagram/{question}.png` | Stored candidate drawing crop for an integer question number | 404 candidate or crop missing |
| `/answer-keys/reference/{template_id}/{question}.png` | Reference drawing for the template/question | 404 reference missing |
| `/templates/{template_id}/preview` | Example preview of a supported template | 404 if no preview is available |

Use the same authentication headers as JSON requests. Page and candidate-diagram responses use `Cache-Control: private, max-age=3600`. Some templates have no preview; a missing preview does not imply the layout is unsupported. Use `/templates/all` for layout discovery.

When present, `extra_fields.diagram_crops` maps question strings to crop metadata, for example `{"12":{"file":"p1_q12.png","source":"template_region"}}`. The `file` is an internal filename; retrieve its bytes through the candidate-diagram endpoint. Crop metadata can include additional fields depending on the extraction provider. Editing an answer string does not replace the stored drawing image; there is no public endpoint for uploading a replacement drawing.

## 7. Synchronous extraction contracts

### POST /extract/json

**Input:** multipart `file` (required PDF), query `template_id` (optional). No JSON body. **Output — 200**, `application/json`:

```json
{
  "document_information": {
    "filename": "example-exam.pdf",
    "extraction_timestamp": "2026-09-09T12:00:00",
    "total_candidates": 1,
    "pages_processed": 1,
    "pages_with_data": 1,
    "processing_time": 1.25
  },
  "candidates": [
    {
      "candidate_name": "Sample Student",
      "candidate_number": "000123",
      "country": "VN",
      "paper_type": "A",
      "template_id": "seamo_2025_a",
      "detection": {
        "method": "footer_ocr",
        "raw_text": "SEAMO 2025 Paper A",
        "brand": "seamo",
        "year": "2025",
        "paper": "a"
      },
      "answers": {
        "1": "A",
        "2": "B",
        "3": "BL",
        "4": "IN",
        "5": "D"
      },
      "drawing_questions": null,
      "extra_fields": {
        "answer_trust": {
          "1": "trusted",
          "2": "trusted",
          "3": "trusted",
          "4": "trusted",
          "5": "needs_review"
        },
        "needs_review_questions": [
          "5"
        ]
      }
    }
  ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `document_information.filename` | String | Original filename |
| `document_information.extraction_timestamp` | UTC timestamp string | Export-generation time |
| `document_information.total_candidates` | Integer | Number of returned candidate objects |
| `document_information.pages_processed`, `pages_with_data` | Integers | Extraction page counts |
| `document_information.processing_time` | Number | Extractor-reported seconds; not a full upload-to-final-marking duration |
| `candidates` | Array | Dynamic raw candidate objects; no persisted ID or `page_number` |
| `logs` | Optional object | May contain `validation` and/or `extraction_errors` if the generator receives them |

The current route does not provide a top-level `validation` or `extraction_errors` field. It normally omits `logs` because no validation result is supplied. Do not expect the old `document_information` example's `validation.is_valid` guarantee.

In queue-enabled deployments, this synchronous compatibility route waits for a worker job and preserves the extraction response shape. An internal submission and extraction checkpoints are retained. `X-Job-ID` identifies the job; a wait timeout returns HTTP 504 with `job_id` and `status_url`. Use `GET /jobs/{job_id}` to retrieve the eventual result, or `POST /jobs/{job_id}/cancel` to cancel it. No automatic AI marking runs on this route.

```bash
curl --fail-with-body --max-time 900 \
  "$API_BASE/extract/json?template_id=seamo_2025_a" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@example-exam.pdf;type=application/pdf"
```

### POST /extract/json/mark

**Input:** same PDF and optional query `template_id`, plus an optional multipart **text field** `mark_request` containing JSON:

```bash
curl --fail-with-body --max-time 900 "$API_BASE/extract/json/mark?template_id=seamo_2025_a" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@example-exam.pdf;type=application/pdf" \
  -F 'mark_request={"answer_key_id":1}'
```

Only `{"answer_key_id": <integer>}` is accepted in `mark_request`. Inline answers/keys, extra fields, malformed JSON, or other shapes return **400**. A missing selected key returns **404**; a selected key whose template differs from an explicitly forced template returns **409**. Selection is validated after extraction in the current implementation, so check key IDs before uploading.

When omitted, marking resolves active keys by the exact template ID. Unlike persisted marking, this path does not apply layout-to-key alias resolution. If no key matches, that candidate remains in the response **without a `marking` block**; the overall HTTP status is still 200. If an explicit key is selected without forcing a template, it is applied to all candidates, so use that combination only when the PDF is known to match that key.

The response is a different envelope from `/extract/json`:

| Field | Type | Meaning |
| --- | --- | --- |
| `filename` | String | Uploaded filename |
| `template_id` | String or null | Forced layout, null in auto mode |
| `mode` | String | `forced` or `auto` |
| `total_candidates` | Integer | Count including unmarked candidates |
| `candidates` | Array | Raw extracted candidates (including `page_number`), with optional deterministic `marking` |

Each synchronous `marking` object contains `awarded_marks`, `max_marks`, `percentage`, `answer_key_id`, and `outcomes`. It has no persisted candidate/run ID. See the [complete response example](https://aimarker.seamo-official.org/integration/examples/sync_mark.json).

This endpoint does not run extraction-trust gating, AI free-response equivalence, or diagram vision judging. For example, the demonstration candidate's uncertain Q5 is marked correct here but remains `needs_review` in the persisted AI marking workflow. It must not be treated as an interchangeable shortcut for that workflow.

## 8. Templates, keys, logs, and administration

### Template discovery

`GET /templates/all` returns an array. Each element has:

| Field | Type |
| --- | --- |
| `id`, `name` | String |
| `brand`, `paper` | String or null |
| `year` | Integer or null |
| `has_mcq`, `has_free_response` | Boolean |
| `total_questions` | Integer |
| `variant_of` | String or null |

The list includes variants and may change as layouts are added. `GET /templates` provides a display-oriented subset with the same core metadata plus `preview_url`, and may deduplicate variants. Use `/templates/all` to validate `template_id`; do not hard-code the number of supported layouts or infer the answer-key ID from the template ID.

### Answer-key discovery

`GET /answer-keys` returns a metadata array; `GET /answer-keys/{key_id}` returns one object. Metadata includes `id`, `name`, nullable `template_id`, `version`, nullable `source_filename`, nullable `source_sha256`, nullable `total_questions` and `total_marks`, `is_active`, `created_at`, and `updated_at`.

```json
[
  {
    "id": 1,
    "name": "Example Paper A v1",
    "template_id": "seamo_2025_a",
    "version": 1,
    "source_filename": "example-key.pdf",
    "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "total_questions": 5,
    "total_marks": 10,
    "is_active": true,
    "created_at": "2026-09-09T12:00:00",
    "updated_at": "2026-09-09T12:00:00"
  }
]
```

No accepted answers, question specifications, or drawing-key text are included. The separate reference-diagram image endpoint is available for visual review. Answer-key creation, update, and deletion are **not public API operations**; unsupported methods return 405. Coordinate key and new-layout provisioning with the operator. Uploading a correction PDF through the older `/exams` API does not activate a marking key.

### Listing and logs

| GET path | Optional query inputs | Output |
| --- | --- | --- |
| `/submissions` | `skip` integer (0), `limit` integer (100), `status` string | Array of status-shaped objects, newest first; no total-count envelope |
| `/submission/{submission_id}/logs` | `limit` integer (10) | Newest-first array of log entries |

Use nonnegative `skip` and positive bounded `limit` values. A log entry has `id`, `action`, `status`, `message`, nullable `extra_data`, and nullable `created_at`. Logs are diagnostic and can contain internal paths or provider error text; action names, timing metadata, and per-page updates are not a stable progress protocol. Use `/status` for task state.

### Deletion

`DELETE /submission/{submission_id}` returns **202** in queued deployments:

```json
{"status":"deleting","submission_id":123,"job_id":456}
```

Poll `/jobs/456` for completion. Cleanup includes local and archived files and
associated results. Unknown IDs return **404**; active work returns **409**.
Cancel or finish active work first. Deletion is destructive and has no undelete
operation. In local development with the queue disabled, the older synchronous
response is HTTP 200 with `status` and `message`.

### Health and service metadata

`GET /health` returns `{"status":"healthy","database":"connected","storage":"connected"}`. This is a liveness response; the database/storage labels are not active dependency or provider-quota probes.

`GET /` returns `message`, `version`, `status`, `docs`, `openapi`, `primary_endpoint`, and `api_key_required`. The `primary_endpoint` identifies `POST /upload`, the queued workflow for full AI marking.

### Separate multi-document /exams API

These existing routes belong to a separate extraction-oriented model. An `exam_id`, `document_id`, or generated `json_id` is not a submission ID and cannot be passed to submission-marking endpoints.

| Method and path | Input | Success output |
| --- | --- | --- |
| `POST /exams` | JSON `{"name":"Example exam"}` | Exam object |
| `GET /exams` | None | Exam array, newest first |
| `GET /exams/{exam_id}` | Integer path ID | Exam with `documents` and `generated_jsons` arrays |
| `POST /exams/{exam_id}/correction` | Multipart `file` PDF | Updated exam; stores PDF only |
| `POST /exams/{exam_id}/student-pdfs` | Multipart `file` PDF; optional **query** `country` | Document object after page conversion |
| `POST /exams/{exam_id}/extract/{document_id}` | Required **query** `template_id` | Generated-JSON metadata after synchronous extraction |
| `GET /exams/{exam_id}/jsons` | Path ID | Array of generated-JSON metadata |
| `GET /jsons/{json_id}` | Path ID | Extraction JSON; object or compact candidate array depending on deployment settings |
| `DELETE /jsons/{json_id}` | Path ID | `{"status":"deleted"}` |

Exam: `id` integer, `name` string, nullable `correction_pdf_path` string, `created_at` timestamp. Document: `id`, `exam_id` integers, nullable `country` string, `file_path` string, nullable `pages_count` integer, `uploaded_at` timestamp. Generated JSON: `id`, `exam_id` integers, `filename` string, nullable `file_path` string, `created_at` timestamp. Success is HTTP 200. Missing entities/files generally return 404; invalid PDF/template returns 400; a missing required query or form field returns 422; conversion/extraction failures return 500. This family does not produce the persisted AI marking/review contract documented above.

## 9. Errors, limits, and reliable client behavior

### Error bodies

Application errors normally use `{"detail":"message"}`. FastAPI request validation uses an array in `detail`:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": [
        "body",
        "file"
      ],
      "msg": "Field required",
      "input": null
    }
  ]
}
```

The legacy `ErrorResponse` schema (`error`, `detail`, `timestamp`) is not the common runtime error wrapper. Proxy/network failures can return HTML, plain text, or no body; handle non-JSON errors too.

| HTTP status | Meaning / action |
| --- | --- |
| 400 | Invalid PDF filename, unknown forced layout, malformed `mark_request`, or details requested before completion; correct the input/state |
| 401 | Missing or invalid API credential |
| 404 | Unknown ID, unavailable image/reference, or missing result reference |
| 405 | Unsupported method, including public answer-key mutations |
| 409 | Operation conflicts with current task/marking state or marked-export coverage requirements |
| 410 | Retained source PDF is no longer available for page preview |
| 413 | Upload rejected by deployment/proxy body-size limits, when configured |
| 422 | Missing/invalid multipart field, path/query value, or typed JSON body |
| 500 | Extraction, rendering, storage, or other server error |
| 502 / 503 / 504 | Deployment/proxy availability or timeout failures; outcome may be unknown for a POST |

Endpoint-specific rules in the sections above take precedence. HTTP 200 can still contain unreadable pages, unmarked candidates, an unsuccessful marking run, provisional review scores, or an edit whose requested re-mark failed. Validate the response body as well as the status code.

### Timeouts and upload limits

- Prefer upload-and-poll for large PDFs. Give the upload enough time for the client's uplink, then poll about every three seconds with a separate overall deadline.
- Synchronous extraction and manual re-marking can take minutes. A 10–15 minute client timeout may be appropriate, but **the shortest proxy/load-balancer timeout still wins**. The example nginx configuration uses a 600-second upstream timeout; confirm the deployed limit with the operator.
- The PDF limit is **3 GiB (3,221,225,472 bytes)**. The API counts received file bytes; nginx permits 3080 MiB for the full multipart request. `GET /capabilities` is the authoritative current configuration. Default document limits are 10,000 pages and 25,000,000 rendered pixels per page. Two uploads may be received concurrently, subject to disk reservations; capacity rejection returns 429 or 503 with `Retry-After`. Validation/render limits can produce a failed queued job after initial upload acceptance.
- No completion-time SLA is implied by benchmark timings. OCR layout detection, full extraction, and AI marking are separate stages. Provider latency, quota, page count, and review/diagram content affect total time.

### Retries and persistence

- Save `submission_id` immediately after an accepted upload and associate it with your own exam/user record. Resume polling by ID after a client restart.
- There is no upload deduplication or `Idempotency-Key` support. Repeating `/upload` creates another task and can repeat billable processing. If the upload response is lost, reconcile recent submissions with your records/operator before retrying; filenames alone are not unique.
- Retrying a GET after a network error is normally appropriate. Use bounded backoff for timeouts and transient 429/502/503/504 responses. Do not retry every 4xx blindly.
- After a lost manual-mark response, fetch `/marking` before deciding to start another run. Edits and re-marks are separate commit boundaries.
- Retain source PDFs and final exports in your own system according to your retention requirements. The API has no source-PDF download endpoint, durable webhook delivery, automatic resume, or result-finalization acknowledgment.
- Keep human review in the integration: display the scan, corrections, question outcomes, and provisional scores before publishing final results.

## 10. Handoff acceptance checks

Before connecting the client application's production workflow:

1. Confirm the API base URL, authentication mode, and installed template/key versions with the operator.
2. Upload a representative PDF, retain the returned ID, and complete the status/marking/export flow.
3. Verify leading-zero candidate numbers, mixed templates, absent header fields, and one-based page links.
4. Exercise blank/invalid answers and `needs_review`; confirm an edit with `remark: true` changes the latest scores while preserving the raw snapshot.
5. Exercise unavailable/partial key coverage, a failed marking attempt, a cancelled task, and a lost polling connection.
6. Confirm file-size and timeout settings against the client application's infrastructure. Reconcile errors without blindly re-uploading.

### Contract verification and maintenance

The request/response examples in this guide are captured from the real FastAPI handlers using an isolated database, a synthetic answer key, and mocked extraction. No production student data is included. The examples test the distinction between deterministic synchronous marking and extraction-trust-aware persisted marking.

The downloadable OpenAPI file is a snapshot of the live schema taken during this review. Some dynamic JSON endpoints currently have unconstrained response schemas in OpenAPI; their concrete field contracts and examples are supplied here. The live schema remains useful for parameter locations and typed request/response models. Do not assume generated clients alone cover undocumented dynamic envelopes.

Maintainers: edit `docs/api/guide.md`, update examples through `tests/test_api_documentation.py` when the contract changes, and run `python scripts/build_api_docs.py`. `docs/API.md`, the standalone HTML, and the frontend's `/api-docs` view are generated from the same source.


## 11. Queue, large uploads, and archived page images

### Capabilities and upload retries

`GET /capabilities` returns `max_file_bytes`, `max_pdf_pages`, `max_page_pixels`, `max_concurrent_uploads`, `queue_enabled`, `archive_enabled`, `local_eviction_enabled`, `candidate_page_exam_id`, and `candidate_page_exams`. Page lookup currently advertises `candidate_page_exam_id: "external_exam_id_or_paper_code"` and `candidate_page_exams: {"31": "seamo_2026"}`.

`POST /upload` still returns HTTP 200 with the original acknowledgment fields, with `X-Job-ID` on a new acceptance. Send an optional `Idempotency-Key` (maximum 200 characters) to safely retry the same PDF and processing options. Identical completed uploads return the original submission ID. Different content/options or an upload still receiving bytes returns 409. Optional `X-Content-SHA256` must be the file's 64-character hexadecimal SHA-256 digest; mismatch returns 400. Do not reuse a key across endpoints or different PDFs.

The uploader streams to persistent local storage before queue acceptance. Legacy `/exams/{id}/student-pdfs` and `/exams/{id}/correction` uploads share the same byte and disk admission limits; they store documents without starting extraction and do not accept `Idempotency-Key`. PDF processing does not depend on keeping the upload connection open. Closing the connection after acceptance does not cancel the queued task. Large uploads have a six-hour application deadline, plus proxy inactivity limits.

### Queue status and operations

`GET /status/{submission_id}` adds `job_id`, `stage`, nullable `queue_position`, `pages_completed`, `attempt`, `archive_status`, and nullable `archive_error`. Existing statuses remain unchanged. Queue position is approximate. `pages_completed` measures extracted pages; marking still follows extraction.

| Endpoint | Input | Output |
| --- | --- | --- |
| `GET /jobs/{job_id}` | Integer job ID | `job_id`, `submission_id`, `kind`, `status`, `stage`, `attempt`, `error`, timestamps and terminal `result` |
| `POST /jobs/{job_id}/cancel` | No body | Updated job; 409 if already completed/failed |
| `POST /jobs/{job_id}/retry` | No body | HTTP 202, requeued failed job; resumes checkpoints; 409 unless failed |
| `POST /submission/{submission_id}/mark-jobs` | Optional JSON `{"answer_key_id":1}` | HTTP 202, marking job; poll `/jobs/{id}` |
| `POST /submission/{submission_id}/archive` | No body | HTTP 202, archive job; 409 while Spaces is disabled or submission incomplete |

Job states are `pending`, `running`, `completed`, `failed`, or `cancelled`. Storage cleanup jobs (`delete` and `evict`) cannot be cancelled halfway through; retry failed cleanup instead. A completed marking job can still contain failed/unavailable marking; inspect its `result` exactly as for the synchronous marking response. Synchronous re-marking uses the same queue; a 504 includes the recoverable job ID and does not imply the task was cancelled. Review-edit responses include nullable `remark_job_id`, so the edit remains successful while its re-mark is still queued or running.

### Candidate page by exam ID and candidate number

```http
GET /candidate-page?exam_id=31&candidate_number=000123
X-API-Key: YOUR_API_KEY
```

Required `exam_id=31` identifies the client’s **SEAMO 2026 series**, across all papers (K, A–F) and supported layout variants. This is the current production exam mapping, independent of the separate `/exams` database IDs. Matching uses explicit exam brand/year metadata. Existing paper codes from `/templates/all` remain accepted for compatibility.

`candidate_number` is a string; leading zeros are significant and only surrounding whitespace is trimmed. Optional integer `submission_id` and one-based `page_number` disambiguate repeated scans or matches across papers.

Page lookup does **not** require answer keys, scores, or completed marking. SEAMO 2026 answer keys are pending; extraction and image retrieval remain available, but marking cannot be completed until the appropriate keys are provisioned.

Success is HTTP 200 with PNG bytes for the full source page and `X-Submission-ID`, `X-Candidate-ID`, `X-Page-Number` headers. No match returns 404; unknown exam ID/paper code or malformed input returns 422. Multiple matches return 409 with `detail.code: "ambiguous_candidate_page"` and `detail.matches` containing submission, candidate and page IDs. The API never chooses a scan silently. A discriminator must belong to the matching candidate and exam series (or the selected paper when using a paper code).

### Spaces archival

After processing, the archive worker copies the original PDF, all retained converted page images, diagram crops and available result exports into a private `aimarker/` namespace. Large files use resumable multipart transfers and an end-to-end checksum readback. Image endpoints keep the same authentication and return bytes from local storage or Spaces.

Processing and archiving have independent statuses. Archive failure preserves local files and does not re-run extraction. `archive_status` is `disabled`, `not_started`, `pending`, `running`, `completed`, `failed` or `cancelled`. **Pre-rollout submissions remain pinned locally** and are not automatically modified or backfilled. New submissions archive immediately after processing and retain a 24-hour local cache before verified cleanup. `local_eviction_enabled` reports whether this cleanup is enabled.

The database remains authoritative for corrected answers and current scores. Marked snapshots are archived by marking-run ID. Human re-marking queues another archive when enabled. Temporary remote-storage errors return 503; an unavailable retained image returns 410.

### Explicit deletion and local retention

In queued deployments, `DELETE /submission/{submission_id}` returns HTTP 202:

```json
{"status":"deleting","submission_id":123,"job_id":456}
```

Poll `GET /jobs/456` until completed. Deletion rejects active work with HTTP 409;
finish or cancel that work first. The cleanup covers the submission's source,
converted pages, diagram crops, exports, private Spaces namespace and associated
database records. Failures retain a retryable cleanup job; other submissions
and shared answer-key assets are untouched. Upload idempotency receipts survive
deletion so replaying a deleted upload returns HTTP 410.

For new submissions, a successful archive schedules cleanup after the configured
cache interval (24 hours in production). Pre-rollout submissions are explicitly
pinned by `ARCHIVE_PRESERVE_THROUGH_SUBMISSION_ID` and cannot be evicted by this cleanup. Cleanup requires all artifacts verified, matching
local hashes, available remote copies and an idle submission. It removes only
local copies; database results and authenticated Spaces-backed image access
remain available. Historical submissions are not automatically backfilled.
