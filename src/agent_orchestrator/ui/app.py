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

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .._version import get_version_string
from ..feedback import MAX_NOTE_CHARS, REASONS, Rating, Reason, Scope
from .files import PathNotAllowedError, PathNotFoundError
from .htmlpreview import NotMarkupError
from .security import SecurityMiddleware, resolve_allowed_hosts
from .service import (
    PROMPT_CONFLICT_PREFIX,
    TEMPLATE_NOT_FOUND_PREFIX,
    DashboardBusyError,
    DashboardConflictError,
    DashboardError,
    DashboardNotFoundError,
    DashboardService,
    DashboardStoreError,
    DashboardValidationError,
)

# Where the built frontend lands. Populated by `npm run build` in ui/ (see
# ui/vite.config.ts, whose outDir points here) and shipped inside the wheel.
STATIC_DIR = Path(__file__).parent / "static"

API_PREFIX = "/api"


class _AllowedHostsUnset:
    """Sentinel for `create_app`'s `allowed_hosts` parameter.

    An explicit `allowed_hosts=None` means "Host check disabled" (see
    `security.resolve_allowed_hosts`'s return contract) — a plain `None` default would make
    that indistinguishable from "the caller didn't pass this at all, resolve it from the
    environment at call time". This sentinel keeps the two apart.
    """


_ALLOWED_HOSTS_UNSET = _AllowedHostsUnset()


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


class FeedbackRequest(BaseModel):
    """Body for ``POST /api/runs/{run_id}/feedback``. The source is NOT client-settable."""

    model_config = ConfigDict(extra="forbid")

    scope: Scope
    task_id: str | None = Field(None, max_length=256)
    rating: Rating
    reasons: list[Reason] = Field(default_factory=list, max_length=len(REASONS))
    note: str | None = Field(None, max_length=MAX_NOTE_CHARS)


logger = logging.getLogger(__name__)

# Store failures carry server-side paths/exception text: log it, tell the client nothing more.
STORE_ERROR_CLIENT_DETAIL = "internal store error; see the server log"

# Status per service error type; first isinstance match wins. Plain DashboardError -> 400.
_ERROR_STATUS: tuple[tuple[type[DashboardError], int], ...] = (
    (DashboardNotFoundError, 404),
    (DashboardConflictError, 409),
    (DashboardBusyError, 429),
    (DashboardStoreError, 500),
    (DashboardValidationError, 400),
)


def _http_error(exc: DashboardError) -> HTTPException:
    status = next((code for kind, code in _ERROR_STATUS if isinstance(exc, kind)), 400)
    if isinstance(exc, DashboardStoreError):
        logger.error("dashboard store error: %s", exc)
        return HTTPException(status_code=status, detail=STORE_ERROR_CLIENT_DETAIL)
    return HTTPException(status_code=status, detail=str(exc))


def create_app(
    service: DashboardService,
    *,
    allowed_hosts: frozenset[str] | None | _AllowedHostsUnset = _ALLOWED_HOSTS_UNSET,
) -> FastAPI:
    """Build the dashboard app around an already-constructed *service*.

    Taking the service as an argument (rather than building one from env vars inside)
    is what lets the integration tests drive every route against a temp workspace with a
    stub supervisor — no subprocesses, no monkeypatching.

    Args:
        allowed_hosts: Host allowlist for `SecurityMiddleware` (see `security.py`). Left
            unset, this resolves `resolve_allowed_hosts()` (env `AO_UI_ALLOWED_HOSTS` +
            the loopback defaults) at call time — `ao ui` instead passes its own bound
            host explicitly so that value is always included too. Pass `None` explicitly
            to disable the Host check outright (equivalent to the `AO_UI_ALLOWED_HOSTS=*`
            escape hatch); this is deliberately distinct from leaving the argument unset.
    """
    app = FastAPI(
        title="Agent Orchestrator Dashboard",
        version=get_version_string(),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.service = service
    effective_hosts = (
        resolve_allowed_hosts() if isinstance(allowed_hosts, _AllowedHostsUnset) else allowed_hosts
    )
    app.add_middleware(SecurityMiddleware, allowed_hosts=effective_hosts)

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

    @app.get(f"{API_PREFIX}/files/html")
    def file_html(
        path: str = Query(..., description="Path relative to the root"),
        root: str | None = Query(None),
    ) -> dict:
        try:
            return service.read_html_preview(root=root, path=path)
        except PathNotAllowedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PathNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except NotMarkupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/graph")
    def run_graph(run_id: str) -> dict:
        try:
            return service.run_graph(run_id)
        except DashboardError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/activity")
    def run_activity(run_id: str) -> dict:
        try:
            return service.run_activity(run_id)
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

    # -- usage / feedback / signals (E-Us9Kd4) ---------------------------------------
    # Sync `def` routes: Starlette runs them in its threadpool, so git-backed survival work
    # never blocks the event loop; the service bounds concurrency/size of that work.

    @app.get(f"{API_PREFIX}/usage")
    def usage(
        run_id: list[str] | None = Query(None, description="Repeatable; omit for all runs"),
        survival: bool = Query(False),
        ref: str | None = Query(None),
    ) -> dict:
        try:
            return service.usage_report(run_id, survival=survival, ref=ref)
        except DashboardError as exc:
            raise _http_error(exc) from exc

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/feedback")
    def get_feedback(run_id: str) -> dict:
        try:
            return service.get_feedback(run_id)
        except DashboardError as exc:
            raise _http_error(exc) from exc

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/feedback", status_code=201)
    def post_feedback(run_id: str, body: FeedbackRequest) -> dict:
        try:
            return service.add_run_feedback(
                run_id,
                scope=body.scope,
                task_id=body.task_id,
                rating=body.rating,
                reasons=list(body.reasons),
                note=body.note,
            )
        except DashboardError as exc:
            raise _http_error(exc) from exc

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/signals")
    def run_signals(
        run_id: str, survival: bool = Query(False), ref: str | None = Query(None)
    ) -> dict:
        try:
            return service.run_signals(run_id, survival=survival, ref=ref)
        except DashboardError as exc:
            raise _http_error(exc) from exc

    @app.get(f"{API_PREFIX}/launches")
    def launches(
        status: str | None = Query(None, max_length=32),
        since_hours: float | None = Query(None, gt=0, le=24 * 365),
    ) -> list[dict]:
        return service.list_launches(status=status, since_hours=since_hours)

    @app.get(f"{API_PREFIX}/launches/{{launch_id}}")
    def launch_detail(launch_id: str) -> dict:
        try:
            return service.get_launch(launch_id)
        except DashboardError as exc:
            raise _http_error(exc) from exc

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
    """Build an app from ``AO_UI_WORKSPACE`` (or the cwd) — the uvicorn factory entrypoint.

    Used by ``ao ui --reload``, where uvicorn re-imports the app in a fresh process, so
    ``cli.py`` hands the bound host over via ``AO_UI_BOUND_HOST`` (same env-relay pattern
    as ``AO_UI_WORKSPACE``) rather than a live object — this keeps the reload path's Host
    allowlist consistent with the non-reload path's, which passes the bound host directly.
    """
    import os

    workspace = os.environ.get("AO_UI_WORKSPACE") or os.getcwd()
    bound_host = os.environ.get("AO_UI_BOUND_HOST")
    return create_app(
        DashboardService(workspace), allowed_hosts=resolve_allowed_hosts(bound_host=bound_host)
    )


__all__ = ["API_PREFIX", "STATIC_DIR", "create_app", "create_app_from_env"]
