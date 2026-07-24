"""FastAPI adapter for the dashboard (E-Ui7Kq2).

Thin by design: every route maps one HTTP call onto one
:class:`~agent_orchestrator.ui.service.DashboardService` method and translates the
service's exceptions into status codes. All behaviour lives in the service layer, which
holds no framework imports — see that module's docstring.

Requires the optional ``[ui]`` extra (``pip install 'agent-orchestrator[ui]'`` /
``uv sync --extra ui``); nothing else in the package imports this module at module scope.

Security posture for this release: **no authentication**. The server therefore binds to
loopback by default (see ``ao ui``) — it exposes the filesystem and can spend money by
launching runs, so it must not be reachable off-box until the deferred auth work lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .._version import get_version_string
from .files import PathNotAllowedError, PathNotFoundError
from .service import (
    PROMPT_CONFLICT_PREFIX,
    TEMPLATE_NOT_FOUND_PREFIX,
    DashboardError,
    DashboardService,
)

# Where the built frontend lands. Populated by `npm run build` in ui/ (see
# ui/vite.config.ts, whose outDir points here) and shipped inside the wheel.
STATIC_DIR = Path(__file__).parent / "static"

API_PREFIX = "/api"


class StartRunRequest(BaseModel):
    """Body for ``POST /api/runs`` (FR-R1)."""

    workflow_path: str | None = None
    prompt: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ResumeRunRequest(BaseModel):
    """Body for ``POST /api/runs/{run_id}/resume`` (FR-R2)."""

    options: dict[str, Any] = Field(default_factory=dict)


class CreateInstanceRequest(BaseModel):
    """Body for ``POST /api/templates/{name}/instances`` (HLD §2.6)."""

    slug_or_id: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    prompt: str | None = None
    start: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


def create_app(service: DashboardService) -> FastAPI:
    """Build the dashboard app around an already-constructed *service*.

    Taking the service as an argument (rather than building one from env vars inside)
    is what lets the integration tests drive every route against a temp workspace with a
    stub supervisor — no subprocesses, no monkeypatching.
    """
    app = FastAPI(
        title="Agent Orchestrator Dashboard",
        version=get_version_string(),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.service = service

    # -- health / workspace ----------------------------------------------------

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict:
        return {"status": "ok", "version": get_version_string()}

    @app.get(f"{API_PREFIX}/workspace")
    def workspace() -> dict:
        from dataclasses import asdict

        return asdict(service.workspace_info())

    @app.get(f"{API_PREFIX}/general-instructions")
    def general_instructions() -> list[dict]:
        return service.general_instructions()

    # -- file browsing ---------------------------------------------------------

    @app.get(f"{API_PREFIX}/files")
    def list_files(
        path: str = Query("", description="Path relative to the root"),
        root: str | None = Query(None, description="Root name; defaults to the first root"),
    ) -> dict:
        try:
            return service.list_dir(root=root, path=path)
        except PathNotAllowedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PathNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/files/content")
    def file_content(
        path: str = Query(..., description="Path relative to the root"),
        root: str | None = Query(None),
    ) -> dict:
        try:
            return service.read_file(root=root, path=path)
        except PathNotAllowedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PathNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- workflows -------------------------------------------------------------

    @app.get(f"{API_PREFIX}/workflows")
    def workflows() -> list[dict]:
        from dataclasses import asdict

        return [asdict(w) for w in service.list_workflows()]

    # -- templates (E-Tpl3x9, HLD §2.6) -----------------------------------------

    @app.get(f"{API_PREFIX}/templates")
    def list_templates() -> list[dict]:
        try:
            return service.list_templates()
        except DashboardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/templates/{{name}}/instances", status_code=201)
    def create_instance(name: str, body: CreateInstanceRequest) -> dict:
        try:
            return service.create_instance(
                name,
                slug_or_id=body.slug_or_id,
                params=body.params,
                prompt=body.prompt,
                start=body.start,
                options=body.options,
            )
        except DashboardError as exc:
            message = str(exc)
            if message.startswith(TEMPLATE_NOT_FOUND_PREFIX):
                status = 404
            elif message.startswith(PROMPT_CONFLICT_PREFIX):
                status = 409
            else:
                status = 400
            raise HTTPException(status_code=status, detail=message) from exc

    # -- runs ------------------------------------------------------------------

    @app.get(f"{API_PREFIX}/runs")
    def list_runs() -> list[dict]:
        return service.list_runs()

    # Registered before /runs/{run_id} so the literal path wins over the parameterized one.
    @app.get(f"{API_PREFIX}/runs/stats")
    def run_stats() -> dict:
        return service.run_stats()

    @app.get(f"{API_PREFIX}/runs/{{run_id}}")
    def run_detail(run_id: str) -> dict:
        try:
            return service.run_detail(run_id)
        except DashboardError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/log")
    def run_log(run_id: str, max_bytes: int = Query(200_000, ge=1, le=5_000_000)) -> dict:
        return service.run_log(run_id, max_bytes=max_bytes)

    @app.delete(f"{API_PREFIX}/runs/{{run_id}}")
    def delete_run(run_id: str) -> dict:
        try:
            return service.delete_run(run_id)
        except DashboardError as exc:
            # 409: the run exists but is not in a deletable state (still running).
            status = 409 if "still running" in str(exc) else 404
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/runs", status_code=201)
    def start_run(body: StartRunRequest) -> dict:
        try:
            return service.start_run(
                workflow_path=body.workflow_path,
                prompt=body.prompt,
                options=body.options,
            )
        except DashboardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/resume")
    def resume_run(run_id: str, body: ResumeRunRequest | None = None) -> dict:
        try:
            return service.resume_run(run_id, options=(body.options if body else {}))
        except DashboardError as exc:
            status = 404 if "not found" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/cancel")
    def cancel_run(run_id: str) -> dict:
        try:
            return service.cancel_run(run_id)
        except DashboardError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/launches")
    def launches() -> list[dict]:
        return service.list_launches()

    # -- static frontend -------------------------------------------------------

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA, or a helpful placeholder when it has not been built.

    A missing ``static/`` is a normal state in a source checkout (the frontend is a
    separate ``npm run build``), so this degrades to an instruction page rather than a
    stack trace — the API stays fully usable either way.
    """
    index = STATIC_DIR / "index.html"

    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def missing_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "dashboard frontend is not built",
                    "fix": "cd ui && npm install && npm run build",
                    "api_docs": f"{API_PREFIX}/docs",
                },
            )

        return

    # SPA fallback: unknown non-/api paths return index.html so client-side routes
    # (deep links, refreshes) resolve instead of 404ing.
    @app.get("/", include_in_schema=False)
    def spa_root() -> FileResponse:
        return FileResponse(index)

    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        # An unknown API path must 404, never fall through to index.html: returning HTML
        # for a typo'd or removed endpoint turns a clear client error into a confusing
        # "why is my JSON parse failing" hunt.
        if full_path.startswith(API_PREFIX.lstrip("/") + "/"):
            raise HTTPException(status_code=404, detail=f"no such endpoint: /{full_path}")

        candidate = (STATIC_DIR / full_path).resolve()
        # Containment check: a crafted path must not turn the SPA fallback into an
        # arbitrary-file read of the machine hosting the dashboard.
        if STATIC_DIR.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def create_app_from_env() -> FastAPI:
    """Build an app from ``AO_UI_WORKSPACE`` (or the cwd) — the uvicorn factory entrypoint."""
    import os

    workspace = os.environ.get("AO_UI_WORKSPACE") or os.getcwd()
    return create_app(DashboardService(workspace))


__all__ = ["API_PREFIX", "STATIC_DIR", "create_app", "create_app_from_env"]
