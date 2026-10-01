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
Primary integration endpoint: `POST /upload` (then poll `/status/{id}`)

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
| `DIAGRAM_VISION_ENABLED` | no (default `true`) | Kill switch reverting diagram marking to the text equivalence judge |
| `DIAGRAM_VISION_MODEL` | no (empty) | Inherits `GEMINI_MODEL` unless set |
| `PAGE_PREVIEW_DPI` | no (default `150`) | Render DPI for the results UI page viewer |
| `DATABASE_URL` | no | Blank = SQLite |
| `API_KEY` | recommended | When set, callers send `X-API-Key` |
| `CORS_ORIGINS` | no | Default `*`; set explicit origins for browser UIs |
| `ARCHIVE_ENABLED` | no | Queue-based private archival; requires `SPACES_ENDPOINT`, `SPACES_BUCKET`, `SPACES_REGION`, `SPACES_KEY`, `SPACES_SECRET` |
| `ARCHIVE_EVICT_LOCAL` | no | Default `false`; production enables verified cleanup of new uploads after 24 hours |
| `ARCHIVE_PRESERVE_THROUGH_SUBMISSION_ID` | no | Production `24`; lower/equal submission IDs remain pinned locally |
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
  questions**. All Gemini traffic shares a Redis-backed sliding-window limit in queued deployments, so this costs paced wall-clock rather than 429s. At the shipped
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
| `ModuleNotFoundError: boto3` | `pip install -r requirements.txt` (includes boto3). Or leave `ARCHIVE_ENABLED=false` — Spaces is optional. |
| `source PDF not found: Paper A key.pdf` | Ensure `answer_keys/**/*.pdf` are in the image (they are committed). App also warns and continues if a PDF is missing. |
| No sqlite file in repo | Normal — created at runtime (see above). |
| Frontend vs backend confusion | Deploy **`main.py` + `backend/`**. Frontend is optional. |

## CPU budget and OCR throughput

On the dedicated 4-vCPU host, Compose allocates at most 2.5 CPUs to the heavy
worker, 0.5 to the API, and 0.25 each to Redis, the dispatcher and the archive
worker. Each heavy worker has one execution slot; page-level parallelism stays
bounded. The API no longer performs production extraction work. `OMP_THREAD_LIMIT=1` is
critical: each Tesseract process otherwise creates its own thread team on top
of parallel page workers. On eight pages from the production scan, layout OCR
fell from 93.05 seconds (three band timeouts) to 1.70 seconds (no timeouts),
with identical template IDs, by changing only this setting.

`MAX_OCR_WORKERS=3` bounds OCR concurrency across submissions in each API
process. `MAX_CLASSIFY_WORKERS=3`, `MAX_PDF_RENDER_WORKERS=3`, and
`OPENCV_THREADS=1` avoid nested CPU oversubscription. `OCR_TIMEOUT_SECONDS=15`
bounds a single Tesseract band; existing header/Gemini fallback handles misses.
These changes keep the original image resolution and classification rules.
With the final three-core limit, all 100 production pages classified in 23.31
seconds, with identical layout IDs and warnings to the unrestricted reference.
CPU-heavy stages may still use the full container budget; that is expected.
Gemini extraction/marking time remains dependent on model latency and your
configured `GEMINI_MAX_RPM`.

## Durable queue and Spaces rollout (19 September 2026)

Use `docker compose up -d --build` for production. Compose enables
`QUEUE_ENABLED=true`; bare `python main.py` defaults to the development path.
Redis 7.4 retains its AOF in the `queue-data` volume. SQLite is authoritative:
accepted jobs and publication state commit together, and the dispatcher replays
missing deliveries. Never use `docker compose down -v` on production.

Workers use late acknowledgment, one prefetched task and 120-second database
leases renewed every 10 seconds. Extraction commits ten pages at a time and
then yields to other submissions. A crash can redo the unfinished batch, not
committed batches. Candidate marking checkpoints avoid repeating completed
judgments. Three attempts use bounded backoff; failed jobs remain inspectable
and can be retried with `POST /jobs/{id}/retry`. Shared Gemini pacing fails
closed when Redis is unavailable. External calls already sent cannot be revoked.

Production limits: 3,221,225,472 file bytes; two concurrent receivers; 10,000
pages; 25 million rendered pixels per page; 20 GiB free disk floor. Active
upload and batch workspace reservations share SQLite admission transactions.
The proxy permits 3080 MiB multipart bodies and disables request buffering.
See `deploy/nginx-aimarker.conf`; validate with `nginx -t` before reload.

1. Back up `.env`, Compose, the current image tag and frontend files. Use the
   SQLite backup API for a consistent snapshot, not a copy of a live WAL file.
2. Temporarily pause new uploads and let existing work drain. For subsequent
   queue releases, stop dispatch and workers gracefully before schema changes.
3. Run `python scripts/migrate_queue.py --backup /app/data/<unique-backup>.sqlite`
   in the application environment. The migration adds tables and indexes; it
   never replaces existing data. Keep both DB and storage snapshots.
4. Start the services, verify `/health`, `/capabilities`, and a synthetic upload,
   then resume admission. Inspect `docker compose ps` and service logs.
5. Publish frontend files into the existing dist directory; replace index.html
   last and retain old hashed assets. The production dist is bind-mounted.

Normalize Spaces credentials into the existing protected `.env`. Use the origin
endpoint (for example `https://sgp1.digitaloceanspaces.com`), never the CDN
endpoint. `.env.spaces` is ignored by Git and Docker. The current namespace is
`aimarker/submissions/<id>/`; objects and manifests are private. Multipart parts
use Content-MD5 and every completed object receives a full SHA-256 readback.
Retries preserve the original local files and resume uploaded parts.

`ARCHIVE_EVICT_LOCAL=true` is enabled after the live archive checks, with
`ARCHIVE_PRESERVE_THROUGH_SUBMISSION_ID=24` pinning every pre-rollout submission.
Existing submissions are not automatically backfilled or deleted. Explicit
`POST /submission/{id}/archive` can archive a historical completed submission.
New successful archives schedule cleanup after `ARCHIVE_CACHE_HOURS` (24 in
production). Restoring a remote file into the local cache renews that interval. Cleanup requires verified artifacts,
a completed archive, unchanged local hashes, available remote copies and no
other active work. File locks fence workers; open image responses pin their
file descriptors. Local and remote reads use the same authenticated endpoints.

Explicit `DELETE /submission/{id}` now returns 202 and a cleanup job ID. It
rejects active work with 409, tombstones the submission, removes its own remote
namespace and local artifacts, then deletes associated DB data. A failed cleanup
remains retryable; it cannot be cancelled halfway through. Nothing invokes this
endpoint as part of startup or deployment.

Operational checks: inspect pending/running/failed rows in `processing_jobs`,
oldest pending `available_at`, lease freshness, page progress, archive errors,
`docker stats`, free disk and Redis connectivity. Treat archive failures and
free space approaching 20 GiB as actionable; admission returns 503 rather than
deleting retained data. Check `/status/{id}` for archive failure separately
from successful extraction. SQLite deployment supports one host; migrate to
PostgreSQL and shared staging before adding processing hosts.

Rollback: pause admission and dispatch, retain the new job tables and queued
PDFs, and restore the previous queue-capable image/configuration. Do not run
accepted jobs through both the queue and the legacy background path. Rolling
back to a local-only reader is unsafe after eviction has been enabled.
