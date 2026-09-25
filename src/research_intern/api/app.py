"""Thin localhost API and packaged browser assets for research mission control."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from research_intern.api.uploads import folder_files
from research_intern.controller.mission import MissionControl, RunNotFound
from research_intern.connections import Connections
from research_intern.onboarding import Onboarding
from research_intern.execution.discovery import DiscoveryError
from research_intern.workspace.discovery import MAX_PROJECT_BYTES, MAX_PROJECT_FILES
from research_intern.domain.experiments import SliceError
from research_intern.workspace.lock import RunLock, own_run
from research_intern.workspace.paths import child_path
from research_intern.workspace.project import LoopBudget

ASSETS = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_experiments: int = Field(default=4, ge=0, le=50)
    max_attempts: int | None = Field(default=None, ge=0, le=100)
    scenario: Literal["mixed", "goal", "runtime-failure", "invalid-first", "invalid-always", "protected-first"] = "mixed"


class BudgetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    max_experiments: int = Field(ge=0, le=50)
    max_gpu_hours: float = Field(ge=0)
    max_ai_credits: float = Field(ge=0)
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PrepareProject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ConfirmEvaluation(PrepareProject):
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    evaluation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


class LiveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    settings: dict
    authorized: Literal[True]


class ProviderSignIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host: str | None = Field(default=None, max_length=253)


class CodingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1, max_length=200)


class UseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authorized: Literal[True]


class ProjectChoices(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    goal: str = Field(min_length=1, max_length=4000)
    job_config: str = Field(min_length=1, max_length=512)
    metric: str = Field(default="", max_length=128)
    direction: Literal["maximize", "minimize"] = "maximize"
    evaluation_file: str = Field(default="", max_length=512)
    validation_file: str = Field(default="", max_length=512)
    evaluation_command: str = Field(default="", max_length=4000)
    evaluation_metrics: str = Field(default="metrics.json", max_length=512)
    editable: list[str] = Field(default_factory=list, max_length=2000)
    constraints: dict = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)
    scoring_python: str = Field(default="", max_length=1024)
    job_timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    existing_compatible: bool = False
    existing_output: str = Field(default="", max_length=512)


class AzureChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subscription_id: str = Field(default="", max_length=36)
    resource_group: str = Field(default="", max_length=200)
    workspace_name: str = Field(default="", max_length=200)
    compute: str = Field(default="", max_length=200)


class JobSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    experiment_name: str = Field(default="", max_length=200)
    job_name: str = Field(default="", max_length=200)


class BaselineReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    compatible: bool
    output: str = Field(default="", max_length=512)


async def stream_dashboard(request: Request, mission: MissionControl, connections=None, onboarding=None):
    previous = None
    while not await request.is_disconnected():
        current = await asyncio.to_thread(mission.dashboard)
        if connections is not None:
            current["connections"] = connections.snapshot()
        if onboarding is not None:
            current["onboarding"] = onboarding.snapshot()
        payload = json.dumps(current, allow_nan=False, separators=(",", ":"))
        if payload != previous:
            yield f"event: snapshot\ndata: {payload}\n\n"
            previous = payload
        else:
            yield ": keepalive\n\n"
        await asyncio.sleep(1)


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


def create_app(workspace: Path, *, mission: MissionControl | None = None,
               server_lock: RunLock | None = None, connections: Connections | None = None,
               onboarding: Onboarding | None = None) -> FastAPI:
    mission = mission or MissionControl(workspace)
    connections = connections or Connections(workspace, mission)
    onboarding = onboarding or Onboarding(workspace, mission, connections)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        server_root = child_path(workspace, ".runtime", "web")
        server_root.mkdir(parents=True, exist_ok=True)
        # One web server owns the serial mission driver for this workspace.
        with own_run(server_root, server_lock):
            # Session credentials do not survive process restart. A prior receipt
            # must not make a new dashboard claim its connections are verified.
            await asyncio.to_thread(mission.live.invalidate_service_check)
            onboarding.start()
            try:
                yield
            finally:
                await asyncio.to_thread(onboarding.close)
                await asyncio.to_thread(mission.close)
                await asyncio.to_thread(connections.close)

    app = FastAPI(title="AI Research Intern — local research workspace", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.state.mission = mission
    app.state.connections = connections
    app.state.onboarding = onboarding

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
        payload = {"detail": str(exc)}
        if isinstance(exc, DiscoveryError):
            payload["category"] = exc.category
        return JSONResponse(payload, status_code=404 if isinstance(exc, RunNotFound) else 409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Do not echo request values; NaN/Infinity cannot be serialized as JSON.
        messages = [f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                    for issue in exc.errors()]
        return JSONResponse({"detail": "; ".join(messages)}, status_code=422)

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
    async def asset(name: Literal["app.css", "app.js", "onboarding.js", "onboarding.css"]):
        return FileResponse(ASSETS / name)

    @app.get("/api/health")
    async def health():
        live = await asyncio.to_thread(mission.live.inspect)
        return {"status": "ok", "mode": "live" if live["configured"] else "simulated", **mission.activity()}

    @app.get("/api/project")
    async def project():
        return await asyncio.to_thread(mission.project)

    @app.post("/api/project/upload", status_code=201)
    async def upload_project(request: Request):
        with mission.project_operation():
            async with folder_files(request) as files:
                return await run_in_threadpool(mission.projects.import_folder, files)

    @app.post("/api/project/budget")
    async def save_budget(body: BudgetSettings):
        budget = LoopBudget(body.max_experiments, body.max_gpu_hours, body.max_ai_credits)
        return await run_in_threadpool(mission.save_budget, budget, body.contract_sha256)

    @app.get("/api/onboarding")
    async def onboarding_status():
        return await asyncio.to_thread(onboarding.snapshot)

    @app.post("/api/onboarding/upload", status_code=201)
    async def upload_ordinary_project(request: Request, archive: bool = False):
        async with folder_files(request, max_bytes=MAX_PROJECT_BYTES + MAX_PROJECT_FILES * 1024,
                                max_files=MAX_PROJECT_FILES) as files:
            return await run_in_threadpool(onboarding.upload, files, archive=archive)

    @app.post("/api/onboarding/inspect")
    async def inspect_ordinary_project():
        return await run_in_threadpool(onboarding.scan)

    @app.post("/api/onboarding/choices")
    async def save_project_choices(body: ProjectChoices):
        return await run_in_threadpool(onboarding.save, body.model_dump())

    @app.post("/api/onboarding/azure/{kind}")
    async def list_azure_resources(kind: Literal["subscriptions", "resource_groups", "workspaces", "computes"], body: AzureChoice):
        return await run_in_threadpool(onboarding.cloud.resources, kind, body.model_dump())

    @app.post("/api/onboarding/target")
    async def select_azure_target(body: AzureChoice):
        return await run_in_threadpool(onboarding.validate_target, body.model_dump())

    @app.post("/api/onboarding/jobs")
    async def find_existing_jobs(body: JobSearch):
        return await run_in_threadpool(onboarding.find_jobs, **body.model_dump())

    @app.post("/api/onboarding/job")
    async def select_existing_job(body: JobSearch):
        if not body.job_name:
            raise SliceError("Select or enter the Azure ML job name / run ID")
        return await run_in_threadpool(onboarding.choose_job, body.job_name)

    @app.post("/api/onboarding/new-baseline")
    async def new_baseline(body: UseDraft):
        return await run_in_threadpool(onboarding.new_baseline)

    @app.post("/api/onboarding/baseline-review")
    async def review_baseline(body: BaselineReview):
        return await run_in_threadpool(onboarding.review_baseline, body.compatible, body.output)

    @app.post("/api/onboarding/prepare")
    async def prepare_discovered_project(body: UseDraft):
        from research_intern.workspace.setup import prepare_discovered
        return await run_in_threadpool(prepare_discovered, onboarding, authorized=body.authorized)

    @app.post("/api/project/start")
    async def start_project():
        return await run_in_threadpool(mission.start_project)

    @app.post("/api/project/live")
    async def configure_live(body: LiveSettings):
        result = await run_in_threadpool(mission.configure_live, body.settings, authorized=body.authorized)
        connections.providers.bind(body.settings)
        return result

    @app.post("/api/project/live/draft")
    async def configure_draft(body: UseDraft):
        from research_intern.signin import service_settings, SignInError
        try:
            settings, _, _ = service_settings(workspace)
        except SignInError as exc:
            raise SliceError(str(exc)) from None
        result = await run_in_threadpool(mission.configure_live, settings, authorized=body.authorized)
        connections.providers.bind(settings)
        return result

    @app.get("/api/connections")
    async def connection_status():
        return connections.snapshot()

    @app.post("/api/connections/{provider}/signin", status_code=202)
    async def sign_in_provider(provider: Literal["azure", "copilot"], body: ProviderSignIn):
        return await run_in_threadpool(connections.start, provider, host=body.host)

    @app.post("/api/connections/{provider}/cancel")
    async def cancel_sign_in(provider: Literal["azure", "copilot"]):
        return connections.cancel(provider)

    @app.post("/api/connections/copilot/model")
    async def select_model(body: CodingModel):
        return await run_in_threadpool(connections.select_model, body.model)

    @app.post("/api/project/baseline", status_code=202)
    async def baseline():
        return await run_in_threadpool(mission.measure_baseline)

    @app.post("/api/project/live/verify")
    async def verify_live():
        return await run_in_threadpool(mission.verify_live)

    @app.get("/api/runs/{run_id}/report")
    async def report(run_id: str):
        return await asyncio.to_thread(mission.report, run_id)

    @app.post("/api/project/prepare")
    async def prepare_project(body: PrepareProject):
        return await run_in_threadpool(mission.prepare_project, body.contract_sha256)

    @app.post("/api/project/evaluation/confirm")
    async def confirm_evaluation(body: ConfirmEvaluation):
        return await run_in_threadpool(mission.confirm_evaluation, body.contract_sha256,
                                       body.source_commit, body.evaluation_fingerprint)

    @app.get("/api/workspace")
    async def workspace_status():
        result = await asyncio.to_thread(mission.dashboard)
        result["connections"] = connections.snapshot()
        result["onboarding"] = onboarding.snapshot()
        return result

    @app.get("/api/workspace/events")
    async def workspace_events(request: Request):
        return StreamingResponse(stream_dashboard(request, mission, connections, onboarding), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

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
