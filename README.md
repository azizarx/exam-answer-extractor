# AI Marker / Exam Answer Extractor

A FastAPI service and React application for extracting exam answer sheets from
PDFs, marking them against stored answer keys, and reviewing uncertain answers.

## Integrating with the API

Start with the [Integrator Guide](docs/API.md). It covers request fields,
complete response examples, weighted scoring, human corrections, images,
authentication, errors, cancellation, and reliable polling.

- [Shareable developer guide](https://aimarker.seamo-official.org/integration/)
- [Integration pack: guide, OpenAPI, examples, Python client](https://aimarker.seamo-official.org/integration/integration-pack.zip)
- [Live Swagger UI](https://aimarker-bk.seamo-official.org/docs)
- [Live OpenAPI schema](https://aimarker-bk.seamo-official.org/openapi.json)

For full AI-assisted marking, use:

```text
POST /upload
  → poll GET /status/{submission_id}
  → GET /submission/{submission_id}
  → inspect GET /submission/{submission_id}/marking
  → GET /submission/{submission_id}/marked-json
```

Automatic marking includes extraction-trust checks, free-response equivalence,
and diagram-image judging where configured. A completed extraction can still
have failed, unavailable, or partial marking; inspect the marking state and
question-level `needs_review` outcomes before publishing scores.

`POST /extract/json` provides synchronous extraction. The separate
`POST /extract/json/mark` endpoint adds deterministic marking only; it does not
run the full AI marking workflow. See the guide's capability comparison before
choosing an endpoint.

## Run locally

The backend uses Python 3.10+ (the container uses 3.12), PyMuPDF, Tesseract,
OpenCV, and Gemini. The frontend uses React 18, Vite, and Tailwind CSS.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set GEMINI_API_KEY and review the remaining settings in .env.
python main.py
```

Install Tesseract with English language data on the host, or use the supplied
Docker image. The API listens on port 8000; interactive docs are at `/docs`.
Database tables and bundled answer-key versions are initialized at startup.
SQLite is the default local database. Local uploads and results live under
`storage/`. The local development default runs without Redis. Production uses
`docker compose up -d --build` to start the API, Redis, a dispatcher, one processing
worker and a separate Spaces archive worker.

```bash
cd frontend
npm ci
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

Open `http://localhost:3000`. Production frontend builds use the API URL in
`frontend/.env.production` or the `VITE_API_BASE_URL` build variable.

## Deployment and operation

See [DEPLOY.md](DEPLOY.md) for Docker, persistence, environment variables,
optional authentication, proxy configuration, and the four-vCPU deployment's
CPU budget. Mathpix is optional; template-based diagram crops remain available
without it. New layout templates and answer keys are provisioned by the
operator; public answer-key endpoints expose metadata only.

Production uploads are durable before acknowledgment and resume from committed
page batches after worker restarts. Files up to 3 GiB are streamed to disk, with
upload admission and disk reservations. `/submission/{id}/cancel` cancels work;
`/jobs/{id}` exposes retries and results. Processed PDFs, converted pages, crops
and result snapshots are verified in private Spaces storage. New uploads retain a 24-hour local cache after verified archival; pre-rollout
submissions remain pinned locally.

`GET /candidate-page?exam_id=31&candidate_number=000123` returns an
authenticated PNG for the SEAMO 2026 exam series, without requiring answer keys.
Existing paper codes remain accepted. Repeated candidate/exam matches return 409 and require a
`submission_id` (and sometimes `page_number`) to select the intended scan.

## Documentation maintenance

The guide has one source, [docs/api/guide.md](docs/api/guide.md). Its examples
are checked against real API handlers using synthetic data in
[tests/test_api_documentation.py](tests/test_api_documentation.py).

```bash
python -m pip install -r scripts/requirements-docs.txt
python scripts/build_api_docs.py
python scripts/build_api_docs.py --check
DEBUG=false .venv/bin/python -m pytest tests/test_api_documentation.py tests/test_integration_client.py -q
cd frontend && npm run build
```

The generator updates `docs/API.md`, the standalone site under
`frontend/public/integration/`, and the downloadable ZIP. The frontend's
`/api-docs` page embeds that same guide. Generated static files are committed,
so building the frontend image does not require Python or a documentation
package. Refresh `docs/api/openapi.json` from the live API when routes or
schemas change; it is a reviewed snapshot, not a replacement for the live URL.

For an intentional response-contract change, regenerate the example fixture,
review the diff, and rebuild the guide:

```bash
UPDATE_API_DOC_EXAMPLES=1 DEBUG=false .venv/bin/python -m pytest tests/test_api_documentation.py -q
```

## Code and tests

- `main.py`: service entry point and middleware.
- `backend/api/`: HTTP routes and typed request/response models.
- `backend/services/`: extraction, layout detection, marking, review, and storage.
- `backend/templates/`: supported layouts and geometric regions.
- `answer_keys/`: versioned marking manifests and reference drawings.
- `frontend/`: upload, tracking, results/review UI, and developer documentation.
- `tests/`: endpoint, scoring, extraction, cancellation, and CPU-budget checks.

Representative checks:

```bash
DEBUG=false .venv/bin/python -m pytest \
  tests/test_api_documentation.py tests/test_marking_api.py \
  tests/test_candidate_edit.py tests/test_marking_workflow.py \
  tests/test_cancellation.py tests/test_cpu_limits.py -q
```

Older session reports and design documents describe their historical context.
Use the integrator guide for the current external contract and `CLAUDE.md`
for implementation notes.
