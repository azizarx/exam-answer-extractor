# AI Marker frontend

React 18 + Vite + Tailwind application for uploading PDFs, tracking/cancelling
tasks, inspecting marked results, and correcting candidates for re-marking.

## Development

```bash
npm ci
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

The Vite development server runs at `http://localhost:3000`. Start the backend
separately from the repository root with `python main.py`.

```bash
npm run build
npm run preview
npm run lint
```

Production API calls use `VITE_API_BASE_URL`, baked in at build time. The
committed `.env.production` points to `https://aimarker-bk.seamo-official.org`.
See [deployment instructions](../DEPLOY.md) before publishing the build.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Upload PDFs with per-page layout detection or an optional override |
| `/track/:submissionId` | Processing/cancellation, results, review, and re-marking |
| `/api-docs` | Embedded developer integration guide |
| `/integration/` | Standalone guide, examples, and downloadable integration pack |

The API client is in `src/services/api.js`. The developer guide is generated
from `../docs/api/guide.md`; do not edit a second copy inside the React page.
Regenerate it from the repository root with `python scripts/build_api_docs.py`
and then run the frontend build. Generated files in `public/integration/` are
copied directly into `dist/`, so no Python dependency is required in the
frontend Docker build.

For the external request/response contract, see the
[Integrator Guide](../docs/API.md).
