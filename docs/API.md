# Exam Extractor API — Integrator Guide

This service is designed to be called by another backend. The React UI is for
manual testing only.

Interactive OpenAPI: `GET /docs` · Machine-readable schema: `GET /openapi.json`

## Auth

| Env | Effect |
|-----|--------|
| `API_KEY` unset (default) | Open access (fine for private networks / local) |
| `API_KEY=secret` | Every non-public route requires `X-API-Key: secret` or `Authorization: Bearer secret` |

Public without a key even when `API_KEY` is set: `/`, `/health`, `/docs`, `/redoc`, `/openapi.json`.

## CORS

Set `CORS_ORIGINS` to a comma-separated list of browser origins that may call
the API (e.g. your test UI). Server-to-server HTTP clients ignore CORS.

```
CORS_ORIGINS=https://your-tool.example.com,http://localhost:3000
```

Default is `*` (any browser origin; credentials disabled).

## Primary endpoint (recommended)

### `POST /extract/json`

Upload a PDF → receive extraction JSON in the same response. No submission DB row.

**Request:** `multipart/form-data` with field `file` (PDF).

**Query:**

| Param | Required | Description |
|-------|----------|-------------|
| `template_id` | no | Force one layout for every page. Omit to auto-detect from the printed footer. |

```bash
# Auto layout (preferred for mixed / unknown papers)
curl -X POST "$API_BASE/extract/json" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@exam_sheet.pdf"

# Forced layout
curl -X POST "$API_BASE/extract/json?template_id=seamo_2025_b" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@exam_sheet.pdf"
```

List valid ids: `GET /templates/all`.

**Client timeout:** multi-page jobs often take minutes. Use **≥ 10 minutes**.

**Response (shape):**

```json
{
  "document_information": {
    "filename": "exam_sheet.pdf",
    "pages_processed": 5,
    "total_candidates": 5
  },
  "candidates": [
    {
      "candidate_name": "…",
      "candidate_number": "…",
      "country": "…",
      "paper_type": "…",
      "template_id": "seamo_2025_b",
      "answers": { "1": "A", "2": "BL", "3": "IN" }
    }
  ],
  "validation": { "is_valid": true, "warnings": [] }
}
```

Answer codes: letter (`A`–`E`), `BL` (blank), `IN` (invalid / multi-mark).

## Optional: extract + mark

### `POST /extract/json/mark`

Same as above, then marks against a **stored** answer key.

- Form field `mark_request` (optional): `{"answer_key_id": 12}` only — inline keys are rejected.
- Without `mark_request`, uses the active key for the forced `template_id`, or (in auto mode) the active key matching each candidate’s `template_id`.

## Async path (large PDFs / UI)

1. `POST /upload` → `{ submission_id }` (optional `?template_id=`)
2. Poll `GET /status/{submission_id}` until `status` is `completed` / `failed`
3. `GET /submission/{submission_id}/json`

## Health

`GET /health` → `{ "status": "healthy", … }`

## Deploy notes

- Bind `0.0.0.0` (already the default in `Procfile` / `railway.json`).
- Required secrets: `GEMINI_API_KEY`; `MATHPIX_*` if any diagram questions are used.
- Optional: `API_KEY`, `CORS_ORIGINS`, `DATABASE_URL` (Postgres in production recommended).
