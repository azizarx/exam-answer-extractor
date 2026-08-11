# Deploy guide — AI Marker / Exam Answer Extractor
#
# For DevOps. Product is an **API**. The React UI is optional (manual testing).

## What to run

| Path | Role |
|------|------|
| `main.py` | **Entrypoint** — FastAPI app (`uvicorn main:app`) |
| `backend/` | API routes, extraction pipeline, templates, DB |
| `frontend/` | Optional Vite/React UI for testing uploads — **not required** for API-only deploy |
| `answer_keys/` | Bundled marking keys (JSON + source PDFs) |
| `requirements.txt` | Python deps |
| `.env` / `.env.example` | Runtime config (copy example → `.env`) |

Do **not** treat `frontend/` or loose scripts as the service. The container/process should start:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Interactive API docs after start: `GET /docs`  
Integrator notes: `docs/API.md`  
Primary integration endpoint: `POST /extract/json`

## Database / SQLite “missing file”

There is **no** `exam_db.sqlite` in git. The DB file is **created on first start** at:

```
./exam_db.sqlite
```

(relative to the process working directory, usually `/app` in Docker).

| Env | Behaviour |
|-----|-----------|
| `DATABASE_URL` empty / unset | SQLite → `./exam_db.sqlite` |
| `DATABASE_URL=postgresql://user:pass@host:5432/exam_db` | Postgres (recommended for production) |

Mount a volume on `/app` (or the SQLite path) if you need the SQLite file to persist across container restarts. Prefer Postgres in multi-replica deploys.

## Required secrets / env

Copy `.env.example` → `.env` (or inject env vars in the orchestrator).

| Variable | Required | Notes |
|----------|----------|-------|
| `GEMINI_API_KEY` | **yes** | Extraction + FR judge |
| `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` | if diagram questions | Templates with `type=diagram` |
| `DATABASE_URL` | no | Blank = SQLite |
| `API_KEY` | recommended | When set, callers send `X-API-Key` |
| `CORS_ORIGINS` | no | Default `*`; set explicit origins for browser UIs |
| `ARCHIVE_IMAGES_TO_SPACES` | no | Default `false`. If `true`, also set `SPACES_*` and ensure `boto3` is installed |
| `GEMINI_MAX_RPM` | no | Raise on paid Gemini tier |

## System packages (image / host)

- Python 3.10+ (3.12 OK)
- `tesseract-ocr` (+ English traineddata) — footer layout OCR
- Optional: `poppler-utils` (only if you reintroduce pdf2image; current code uses PyMuPDF)

`nixpacks.toml` already pulls `tesseract` + `poppler_utils` for Nixpacks/Railway-style builds.

## Docker (example)

```bash
docker build -t aimarker .
docker run --rm -p 8000:8000 --env-file .env \
  -v aimarker-data:/app/storage \
  -v aimarker-db:/app/data \
  -e DATABASE_URL=sqlite:////app/data/exam_db.sqlite \
  aimarker
```

See `Dockerfile` in repo root.

## Health check

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/
```

## Known startup warning (harmless)

```
FutureWarning: All support for the google.generativeai package has ended...
```

Expected until the codebase migrates to `google.genai`. Not a crash.

## Common failures

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: boto3` | `pip install -r requirements.txt` (includes boto3). Or leave `ARCHIVE_IMAGES_TO_SPACES=false` — Spaces is optional. |
| `source PDF not found: Paper A key.pdf` | Ensure `answer_keys/**/*.pdf` are in the image (they are committed). App also warns and continues if a PDF is missing. |
| No sqlite file in repo | Normal — created at runtime (see above). |
| Frontend vs backend confusion | Deploy **`main.py` + `backend/`**. Frontend is optional. |
