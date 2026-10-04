from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.errors import TooManyRequests, install_handlers
from app.security import ratelimit
from app.security.context import Ctx, set_ctx

log = logging.getLogger("pulse")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


def create_app() -> FastAPI:
    s = get_settings()  # validates configuration (fails fast in production)
    logging.basicConfig(level=s.log_level, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log.info("PULSE starting env=%s demo_mode=%s", s.app_env, s.demo_mode)
        yield

    app = FastAPI(title="PULSE API", version="1.0.0", lifespan=lifespan,
                  description="Campus support & early-intervention platform. Cookie sessions (httpOnly) with CSRF header; "
                              "Bearer tokens are accepted for API clients. All authorization is enforced server-side.",
                  docs_url="/api/docs" if s.docs_enabled else None, redoc_url="/api/redoc" if s.docs_enabled else None,
                  openapi_url="/api/openapi.json" if s.docs_enabled else None)
    install_handlers(app)
    if s.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                           allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:40] or uuid.uuid4().hex[:16]
        ip = request.client.host if request.client else "unknown"
        set_ctx(Ctx(request_id=rid, ip=ip, user_agent=request.headers.get("user-agent")))
        t0 = time.perf_counter()
        if request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/docs", "/api/openapi")):
            try:
                ratelimit.hit(f"ip:{ip}", s.rate_limit_per_minute)
            except TooManyRequests as e:
                return JSONResponse({"error": {"code": e.code, "message": e.message, "details": None, "request_id": rid}},
                                    status_code=429, headers={**(e.headers or {}), "X-Request-ID": rid})
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code, (time.perf_counter() - t0) * 1000, rid)
        return response

    from app.api import admin, auth, cases, catalog, itsm, meta, misc, requests as req_api, tasks

    api = APIRouter(prefix="/api/v1")
    for r in (meta.router, auth.router, catalog.read, catalog.admin, req_api.router, tasks.router, itsm.router, cases.router,
              misc.notif, misc.kb, misc.rep, admin.router):
        api.include_router(r)
    if s.demo_mode and not s.is_production:
        from app.api import demo

        api.include_router(demo.router)
    app.include_router(api)

    fe = (Path(__file__).resolve().parent.parent / s.frontend_dir).resolve()
    if s.serve_frontend and fe.is_dir():
        app.mount("/", StaticFiles(directory=fe, html=True), name="frontend")
    return app


app = create_app()
