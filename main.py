"""
Main FastAPI application
Exam Answer Sheet Extraction System
"""
import multiprocessing
import sys

# Windows multiprocessing fix - must be before other imports
if sys.platform == 'win32':
    multiprocessing.freeze_support()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager

from backend.api import marking_routes, routes
from backend.db.database import init_db
from backend.config import get_settings
from backend.services.api_auth import ApiKeyMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_DESCRIPTION = """
PDF exam answer-sheet extraction API for server-to-server integrations.

## Primary integration path

1. **`POST /extract/json`** — upload a PDF, get structured candidate JSON in the response
   (synchronous; no DB submission record). Omit `template_id` to auto-detect layout per page.
2. **`GET /templates/all`** — list valid `template_id` values when you want to force a layout.
3. Optional: set env `API_KEY` and send `X-API-Key` on every request.

## Async alternative (UI / long PDFs)

`POST /upload` → poll `GET /status/{submission_id}` → `GET /submission/{submission_id}/json`.

## Docs

- Interactive OpenAPI: `/docs`
- Integrator guide: `docs/API.md` in the repo
- In-app reference UI: React frontend `/api-docs` (testing only)

**Client timeout:** sync extraction of multi-page PDFs can take several minutes.
Use a client timeout of at least 10 minutes (or prefer `/upload` + poll for large jobs).
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting Exam Answer Sheet Extraction System...")
    init_db()
    logger.info("Database initialized")
    yield
    # Shutdown
    logger.info("Shutting down application...")


# Create FastAPI app
app = FastAPI(
    title="Exam Answer Sheet Extraction API",
    description=API_DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    contact={"name": "Exam Extractor API"},
)

# Configure CORS for browser UIs / cross-origin tools. Server-to-server
# callers are unaffected by CORS.
settings = get_settings()
_raw_origins = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()]
_allow_all = _raw_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _raw_origins,
    # Starlette rejects allow_credentials=True with allow_origins=["*"].
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Optional API key (no-op when API_KEY unset)
app.add_middleware(ApiKeyMiddleware)

# Include routers
app.include_router(routes.router, tags=["Exam Processing"])
app.include_router(marking_routes.router, tags=["Marking"])


@app.get("/", tags=["Meta"])
async def root():
    """Service metadata and doc links."""
    return {
        "message": "Exam Answer Sheet Extraction API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "primary_endpoint": "POST /extract/json",
        "api_key_required": bool((settings.api_key or "").strip()),
    }


@app.get("/health", tags=["Meta"])
async def health_check():
    """Liveness probe for load balancers and uptime checks."""
    return {
        "status": "healthy",
        "database": "connected",
        "storage": "connected"
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
