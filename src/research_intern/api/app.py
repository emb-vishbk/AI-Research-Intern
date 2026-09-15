"""Thin localhost API and packaged browser assets for the offline research loop."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from research_intern.controller.mission import MissionControl, RunNotFound
from research_intern.domain.experiments import SliceError
from research_intern.workspace.lock import RunLock
from research_intern.workspace.paths import child_path

ASSETS = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_experiments: int = Field(default=4, ge=0, le=50)
    max_attempts: int | None = Field(default=None, ge=0, le=100)
    scenario: Literal["mixed", "goal", "runtime-failure", "invalid-first", "invalid-always", "protected-first"] = "mixed"


async def stream_status(request: Request, mission: MissionControl, run_id: str, initial: dict):
    """Send fresh snapshots on change; persisted events survive reconnection."""
    previous = None
    current = initial
    while not await request.is_disconnected():
        payload = json.dumps(current, allow_nan=False, separators=(",", ":"))
        if payload != previous:
            yield f"event: snapshot\ndata: {payload}\n\n"
            previous = payload
        else:
            yield ": keepalive\n\n"
        await asyncio.sleep(0.75)
        try:
            current = await asyncio.to_thread(mission.status, run_id)
        except (SliceError, OSError, ValueError, sqlite3.Error) as exc:
            yield f"event: unavailable\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return


def create_app(workspace: Path, *, mission: MissionControl | None = None) -> FastAPI:
    mission = mission or MissionControl(workspace)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        server_root = child_path(workspace, ".runtime", "web")
        server_root.mkdir(parents=True, exist_ok=True)
        # One web server owns the serial mission driver for this workspace.
        with RunLock(server_root):
            try:
                yield
            finally:
                await asyncio.to_thread(mission.close)

    app = FastAPI(title="AI Research Intern — offline mission control", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.state.mission = mission

    @app.middleware("http")
    async def local_browser_boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            origin = request.headers.get("origin")
            if (request.headers.get("x-research-intern") != "1"
                    or (origin is not None and origin != expected_origin)
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"detail": "Use the localhost dashboard or its same-origin API header"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"],
                       www_redirect=False)

    @app.exception_handler(SliceError)
    async def boundary_error(request: Request, exc: SliceError):
        return JSONResponse({"detail": str(exc)}, status_code=404 if isinstance(exc, RunNotFound) else 409)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, exc: sqlite3.Error):
        log.error("Local run ledger unavailable", exc_info=exc)
        return JSONResponse({"detail": "The run ledger is unavailable; inspect the server log and retry"}, status_code=503)

    @app.exception_handler(OSError)
    async def filesystem_error(request: Request, exc: OSError):
        log.error("Local workspace unavailable", exc_info=exc)
        return JSONResponse({"detail": "The local workspace is unavailable; inspect its path and permissions"}, status_code=409)

    @app.get("/")
    async def index():
        return FileResponse(ASSETS / "index.html")

    @app.get("/assets/{name}")
    async def asset(name: Literal["app.css", "app.js"]):
        return FileResponse(ASSETS / name)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "mode": "simulated", **mission.activity()}

    @app.get("/api/project")
    async def project():
        return await asyncio.to_thread(mission.project)

    @app.get("/api/runs")
    async def runs():
        return await asyncio.to_thread(mission.runs)

    @app.post("/api/runs", status_code=201)
    async def create_run(body: NewRun):
        return await asyncio.to_thread(mission.create, **body.model_dump())

    @app.get("/api/runs/{run_id}")
    async def status(run_id: str):
        return await asyncio.to_thread(mission.status, run_id)

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str):
        return await asyncio.to_thread(mission.start, run_id)

    @app.post("/api/runs/{run_id}/stop")
    async def stop(run_id: str):
        return await asyncio.to_thread(mission.stop, run_id)

    @app.get("/api/runs/{run_id}/experiments/{experiment_id}")
    async def experiment(run_id: str, experiment_id: str):
        return await asyncio.to_thread(mission.experiment, run_id, experiment_id)

    @app.get("/api/runs/{run_id}/events")
    async def events(request: Request, run_id: str):
        initial = await asyncio.to_thread(mission.status, run_id)
        return StreamingResponse(stream_status(request, mission, run_id, initial), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    return app
