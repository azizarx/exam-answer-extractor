"""Optional API-key gate for server-to-server callers.

When ``API_KEY`` is unset, all requests pass (local/dev default).
When set, every request except health/docs/openapi must send
``X-API-Key: <API_KEY>`` (or ``Authorization: Bearer <API_KEY>``).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.config import get_settings

_PUBLIC_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        expected = (settings.api_key or "").strip()
        if not expected:
            return await call_next(request)

        path = request.url.path or "/"
        if path == "/" or any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("x-api-key", "").strip()
        if not provided:
            auth = request.headers.get("authorization", "").strip()
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()

        if provided != expected:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Missing or invalid API key. Send X-API-Key or Authorization: Bearer.",
                },
            )
        return await call_next(request)
