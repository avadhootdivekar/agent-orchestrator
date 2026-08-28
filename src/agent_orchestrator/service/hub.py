"""The hub: a small FastAPI app serving an HTML index of every registered workspace plus
`GET /api/service/status` JSON (HLD §8).

Thin adapter only, mirroring the `ui/app.py` (thin, framework glue) vs `ui/service.py`
(behavior) split ADR-0010 established: every route here reads `status_provider()`'s dict
verbatim (or renders it as HTML) and does nothing else. In particular this module never
itself calls `RunRepository` -- the per-workspace run-count summary `GET /` renders comes
from whatever `status_provider` callable this module is handed (see
`service/cli.py::build_status_provider`), which is also what `GET /api/service/status`
serves, so there is exactly one code path computing that data, not two (AC2/AC15).

`fastapi` is imported lazily, inside `build_hub_app` only -- never at module scope -- so a
core `ao` install without the optional `[ui]` extra can still import this module (e.g. from
`service/cli.py`'s module scope, which does exactly that) without raising `ImportError` at
import time (AC1).

Security posture: loopback-only, unauthenticated (same posture as `ao ui`, see
`ui/security.py`'s module docstring for the full threat model). This is enforced, not
aspirational -- `build_hub_app` actually MOUNTS `ui.security.SecurityMiddleware` on the app
via `app.add_middleware`, identically to `ui/app.py::create_app` (AC3/AC14 -- early-gate
correction #6: importing the allowlist constants without attaching the middleware to the
ASGI app enforces nothing).
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

# What `status_provider()` returns -- verbatim, the same shape `Supervisor.status_snapshot()`
# produces (optionally enriched with a per-workspace `run_summary`, see
# `service/cli.py::build_status_provider`).
StatusProvider = Callable[[], dict[str, Any]]


def build_hub_app(status_provider: StatusProvider) -> FastAPI:
    """Build the hub app around *status_provider* (HLD §8).

    Args:
        status_provider: Zero-arg callable returning the status payload both `GET /` and
            `GET /api/service/status` render. Typically `Supervisor.status_snapshot` wrapped
            by `service/cli.py::build_status_provider` (adds the run-count summary + a TTL
            cache) -- this module has no opinion on that, it just calls whatever it is
            handed.
    """
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    from ..ui.security import SecurityMiddleware, resolve_allowed_hosts

    app = FastAPI(title="Agent Orchestrator Service Hub")
    # Same posture as `ao ui`: loopback-only bind is the caller's job (uvicorn host arg in
    # `service/cli.py`'s `run` command); this middleware is the enforced backstop against a
    # DNS-rebinding-style Host header regardless of what the process actually bound to.
    app.add_middleware(SecurityMiddleware, allowed_hosts=resolve_allowed_hosts())

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(_render_index_html(status_provider()))

    @app.get("/api/service/status")
    def api_status() -> JSONResponse:
        return JSONResponse(status_provider())

    return app


def _render_index_html(payload: dict[str, Any]) -> str:
    workspaces = payload.get("workspaces") or []
    rows = "\n".join(_render_workspace_row(ws) for ws in workspaces)
    if not rows:
        rows = '<tr><td colspan="4">No workspaces registered.</td></tr>'

    hub_port = escape(str(payload.get("hub_port", "?")))
    supervisor_pid = escape(str(payload.get("supervisor_pid", "?")))
    uptime = payload.get("uptime_seconds")
    uptime_str = f"{uptime:.0f}s" if isinstance(uptime, int | float) else "?"

    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>Agent Orchestrator Service</title></head>
<body>
<h1>Agent Orchestrator Service</h1>
<p>Hub port: {hub_port} &middot; Supervisor pid: {supervisor_pid} &middot; \
Uptime: {uptime_str}</p>
<table border="1" cellpadding="4" cellspacing="0">
<thead><tr><th>Workspace</th><th>Dashboard</th><th>State</th><th>Runs</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</body>
</html>
"""


def _render_workspace_row(ws: dict[str, Any]) -> str:
    root = escape(str(ws.get("root", "")))
    port = ws.get("port")
    state = escape(str(ws.get("state", "unknown")))
    if port:
        url = f"http://127.0.0.1:{port}/"
        link = f'<a href="{escape(url)}">{escape(url)}</a>'
    else:
        link = "n/a"

    run_summary = ws.get("run_summary")
    runs_cell = (
        escape(str(run_summary.get("total_runs", "?"))) if isinstance(run_summary, dict) else "n/a"
    )
    return f"<tr><td>{root}</td><td>{link}</td><td>{state}</td><td>{runs_cell}</td></tr>"


__all__ = ["StatusProvider", "build_hub_app"]
