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
from backend.services.upload_middleware import QueuedUploadMiddleware
from backend.api import queue_routes
from backend.services.cpu_limits import configure_cpu_limits

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_DESCRIPTION = """
PDF answer-sheet extraction and AI marking with a durable processing queue.

1. POST /upload with multipart file (up to 3 GiB) and optional template_id query.
2. Poll GET /status/{submission_id}; inspect queue stage and page progress.
3. GET /submission/{submission_id}, inspect marking, and GET /submission/{submission_id}/marked-json.

GET /candidate-page accepts exam_id=31 for the SEAMO 2026 series and a string candidate_number.
Page lookup does not require answer keys; existing paper codes remain accepted.
GET /capabilities reports upload and queue limits. Jobs survive API restarts.
Synchronous extraction routes share the worker queue and return a job reference on timeout.

Send X-API-Key or Authorization: Bearer when authentication is enabled.
Full guide: https://aimarker.seamo-official.org/integration/
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
configure_cpu_limits()
_raw_origins = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()]
_allow_all = _raw_origins == ["*"]
# CORS must wrap authentication so preflight and 401 responses remain readable
# by the frontend when it sends X-API-Key across origins.
app.add_middleware(QueuedUploadMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _raw_origins,
    # Starlette rejects allow_credentials=True with allow_origins=["*"].
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Include routers
app.include_router(routes.router, tags=["Exam Processing"])
app.include_router(marking_routes.router, tags=["Marking"])
app.include_router(queue_routes.router, tags=["Queue and storage"])


@app.get("/", tags=["Meta"])
async def root():
    """Service metadata and doc links."""
    return {
        "message": "Exam Answer Sheet Extraction API",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "primary_endpoint": "POST /upload",
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

    # Never enable reload in production: SQLite WAL writes under ./data (and
    # pipeline logs under ./storage) would restart the process mid-extraction.
    use_reload = bool(settings.debug) and (settings.app_env or "").lower() in {
        "development",
        "dev",
        "local",
    }
    reload_kwargs = {}
    if use_reload:
        reload_kwargs = {
            "reload": True,
            "reload_excludes": [
                "data/*",
                "storage/*",
                "*.sqlite",
                "*.sqlite-*",
                "*.log",
            ],
        }

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
        **reload_kwargs,
    )
