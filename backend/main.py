"""Application entry point — assembly only.

Everything with behaviour lives in `core`, `models`, `services`, `routers`;
this module just wires them together and starts the server.
"""
import asyncio
import logging
import os
import sys
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

from core.config import get_settings
from core.errors import AppError
from core.paths import PROJECT_ROOT
from models.migrations import init_database
from routers import api_router
from services.feed_service import has_cached_feeds, refresh_all, start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()

FRONTEND_DIR = PROJECT_ROOT / "frontend"
NO_CACHE_SUFFIXES = (".js", ".css", ".html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Preparing database…")
    init_database()

    for directory in settings.runtime_dirs:
        os.makedirs(directory, exist_ok=True)

    if not has_cached_feeds():
        logger.info("No feed data found, running initial scrape…")
        asyncio.create_task(refresh_all())

    scheduler = start_scheduler()
    logger.info("🚀 Apocalypse backend started (data dir: %s)", settings.var_dir)
    yield

    scheduler.shutdown(wait=False)
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

    app.include_router(api_router)

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"status": "ok", "version": app.version}

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
