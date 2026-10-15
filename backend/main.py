"""Application entry point — assembly only.

Everything with behaviour lives in `core`, `models`, `services`, `routers`;
this module just wires them together and starts the server.
"""
import asyncio
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

if sys.version_info < (3, 10):
    raise RuntimeError(
        "Apocalypse backend requires Python 3.10+ (current: "
        f"{sys.version.split()[0]}). Please switch interpreter and reinstall dependencies."
    )

from core.runtime import acquire_runtime_lock, check_storage
from core.database import engine
from core.config import get_settings
from core.errors import AppError
from core.paths import PROJECT_ROOT
from harness.session.manager import reset_running_sessions
from models.migrations import init_database
from routers import api_router
from services.feed_service import has_cached_feeds, refresh_all, start_scheduler, stop_refreshes

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
settings = get_settings()

FRONTEND_DIR = PROJECT_ROOT / "frontend"
NO_CACHE_SUFFIXES = (".js", ".css", ".html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime_lock = acquire_runtime_lock(settings.var_dir)
    logger.info("Preparing database…")
    init_database()

    for directory in settings.runtime_dirs:
        os.makedirs(directory, exist_ok=True)

    # No agent turn can outlive the process that drove it, so anything still
    # marked running was cut off by the last shutdown.
    reset_running_sessions()

    check_storage(settings, engine)

    if settings.feeds_enabled and not has_cached_feeds():
        logger.info("No feed data found, running initial scrape…")
        asyncio.create_task(refresh_all())

    scheduler = start_scheduler() if settings.feeds_enabled else None
    logger.info("🚀 Apocalypse backend started (data dir: %s)", settings.var_dir)
    try:
        yield
    finally:
        from routers.harness import stop_turns
        await stop_turns()
        await stop_refreshes()
        if scheduler:
            scheduler.shutdown(wait=False)
        runtime_lock.close()
    logger.info("Apocalypse backend stopped.")


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Keep the un-versioned frontend assets from being cached by the browser."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.endswith(NO_CACHE_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="Apocalypse API",
        description="Personal knowledge space — memos, feeds and an AI assistant",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(NoCacheMiddleware)

    # Service-layer failures carry their own status code; the HTTP layer only
    # decides on the wire format.
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        category = "none"
        try:
            response = await call_next(request)
        except Exception as exc:
            category = type(exc).__name__
            response = JSONResponse(status_code=500, content={"detail": "服务暂时不可用", "request_id": request_id})
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        route = getattr(request.scope.get("route"), "path", "unmatched")
        logger.info("request id=%s method=%s route=%s status=%s duration_ms=%d error=%s",
                    request_id, request.method, route, response.status_code,
                    (time.monotonic() - started) * 1000, category)
        return response

    app.include_router(api_router)

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"status": "ok", "version": app.version}

    @app.get("/readyz", tags=["ops"])
    def readyz():
        try:
            check_storage(settings, engine)
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    # Mounted last: a Mount on "/" matches every path and would shadow any
    # route declared after it.
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # reload=True spawns a file watcher and a second process — development only.
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.dev_reload,
        log_level="info",
    )
