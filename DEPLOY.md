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

There is **no** DB file in git. Defaults:

| How you run | Where SQLite lives |
|-------------|--------------------|
| `python main.py` locally | `./exam_db.sqlite` (cwd) |
| `docker compose up` | `./data/exam_db.sqlite` on the host → `/app/data/` in the container |

Compose **must** bind a **directory** (`./data:/app/data`), not a single `.sqlite` file — SQLite WAL mode also writes `*-wal` / `*-shm` next to the DB, and those would otherwise die with the container.

| Env | Behaviour |
|-----|-----------|
| `DATABASE_URL` empty / unset | SQLite → `./exam_db.sqlite` |
| Compose default | `sqlite:////app/data/exam_db.sqlite` (persistent) |
| `DATABASE_URL=postgresql://user:pass@host:5432/exam_db` | Postgres (recommended for multi-replica) |

`./data/` and `exam_db.sqlite*` are gitignored.

## Required secrets / env

Copy `.env.example` → `.env` (or inject env vars in the orchestrator).

| Variable | Required | Notes |
|----------|----------|-------|
| `GEMINI_API_KEY` | **yes** | Extraction + FR judge |
| `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` | optional | Supplies a tighter diagram crop; a template-region crop is always produced, so diagram marking works without it |
| `DIAGRAM_VISION_ENABLED` | no (default `true`) | Kill switch reverting diagram marking to the deterministic text path |
| `DIAGRAM_VISION_MODEL` | no (empty) | Inherits `GEMINI_MODEL` unless set |
| `PAGE_PREVIEW_DPI` | no (default `150`) | Render DPI for the results UI page viewer |
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

## Frontend (optional test UI)

The React app lives in `frontend/`. It is **not** required for API consumers.

### Critical: API base URL

Production frontend host: `https://aimarker.seamo-official.org`  
Production API host: `https://aimarker-bk.seamo-official.org`

The frontend Docker build bakes in:

```
VITE_API_BASE_URL=https://aimarker-bk.seamo-official.org
```

(`frontend/Dockerfile`). Rebuild/redeploy the frontend image after changing it.

Local UI against local API:

```bash
echo 'VITE_API_BASE_URL=http://localhost:8000' > frontend/.env
cd frontend && npm run dev
```

If the HTTPS UI still calls `localhost:8000`, the frontend dist was built without
`VITE_API_BASE_URL` — rebuild with the Dockerfile above (or set the ARG in CI).

## Known startup warning (harmless)

```
FutureWarning: All support for the google.generativeai package has ended...
```

Expected until the codebase migrates to `google.genai`. Not a crash.


## Diagram marking notes

- Diagram marking adds **one Gemini call per candidate that has diagram
  questions**. All Gemini traffic shares a process-wide sliding-window token
  bucket, so this costs paced wall-clock rather than 429s. At the shipped
  `GEMINI_MAX_RPM=4` a 20-page batch gains roughly five minutes; set
  `GEMINI_MAX_RPM` to your provider tier's real limit (60+ on paid Tier 1).
- `storage/diagrams/` holds one small PNG per diagram question per page and is
  deleted with its submission. It lives under `STORAGE_ROOT`, so the existing
  `/app/storage` volume already persists it — no new mount.
- The answer key's reference drawings are committed under
  `answer_keys/reference_diagrams/`. `init_db()` refuses to activate a key whose
  diagram question has no reference, so a missing file fails at startup rather
  than silently sending every diagram to review.
- The new image endpoints live under `/submission/...` and `/answer-keys/...`,
  both already matched by the nginx proxy regex. No nginx change is required.

## Common failures

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: boto3` | `pip install -r requirements.txt` (includes boto3). Or leave `ARCHIVE_IMAGES_TO_SPACES=false` — Spaces is optional. |
| `source PDF not found: Paper A key.pdf` | Ensure `answer_keys/**/*.pdf` are in the image (they are committed). App also warns and continues if a PDF is missing. |
| No sqlite file in repo | Normal — created at runtime (see above). |
| Frontend vs backend confusion | Deploy **`main.py` + `backend/`**. Frontend is optional. |
