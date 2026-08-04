from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.application.job_manager import JobManager
from src.application.job_repository import JobRepository
from src.config import ROOT_DIR, WORK_DIR
from src.logging_utils import log_lifecycle_event, sanitize

from .routes_jobs import router


WEB_DB_PATH = WORK_DIR / "web_jobs.sqlite3"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


def create_app(
    *,
    db_path: Path = WEB_DB_PATH,
    job_manager: JobManager | None = None,
    frontend_dist: Path = FRONTEND_DIST,
) -> FastAPI:
    owns_manager = job_manager is None
    repository = job_manager.repository if job_manager else JobRepository(db_path)
    manager = job_manager or JobManager(repository, recover_on_start=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        lifecycle_logger = logging.getLogger("bigkinds_lifecycle")
        log_lifecycle_event(
            lifecycle_logger, "api_server", "started", pid=os.getpid(),
        )
        if owns_manager:
            repository.recover_interrupted_jobs()
        termination_reason = "lifespan_shutdown"
        try:
            yield
        except BaseException as exc:
            termination_reason = type(exc).__name__
            log_lifecycle_event(
                lifecycle_logger, "api_server", "shutdown_error",
                pid=os.getpid(), error=sanitize(exc),
            )
            raise
        finally:
            log_lifecycle_event(
                lifecycle_logger, "api_server", "stopping",
                pid=os.getpid(), termination_reason=termination_reason,
            )
            if owns_manager:
                manager.shutdown()
            log_lifecycle_event(
                lifecycle_logger, "api_server", "stopped",
                pid=os.getpid(), termination_reason=termination_reason,
            )

    app = FastAPI(
        title="빅카인즈 링크 점검",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.job_repository = repository
    app.state.job_manager = manager
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)

    static_assets = frontend_dist / "assets"
    if static_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=static_assets), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        index = frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "프런트엔드 빌드가 없습니다. frontend에서 npm install 후 npm run build를 실행해 주세요."
            },
        )

    return app


app = create_app()
