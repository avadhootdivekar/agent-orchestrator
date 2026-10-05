"""Route-enumeration harness shared by the ``test_route_enumeration_*.py`` files (owner: T-G7qByZ).

HLD sections 13.1-13.2 and 20.3 #1/#2. Contexts come from ``fastapi.routing.iter_route_contexts``,
which exists only in recent FastAPI releases (0.139+) while ``pyproject.toml`` allows
``fastapi>=0.110``; :func:`require_route_contexts` skips the calling test or module with a clear
reason when it is missing. The FastAPI floor is deliberately not bumped (NFR-2).
Test-only: never imported by ``src/``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.routing import Mount
from starlette.testclient import TestClient

from agent_orchestrator.auth.http.middleware import classify
from agent_orchestrator.auth.policy import MOUNT_METHOD, RouteKey, RoutePolicy
from tests.auth.helpers.core import same_origin_headers

ENUMERATED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SKIP_REASON = (
    "fastapi.routing.iter_route_contexts is unavailable in this FastAPI release "
    "(it needs 0.139+); the route enumeration gate runs in CI against a recent FastAPI"
)
_PATH_PARAM = re.compile(r"\{[^}]+\}")
PARAM_SAMPLE = "x"


def require_route_contexts() -> Callable[[list[Any]], Iterator[Any]]:
    """``iter_route_contexts``, or skip with a reason (importorskip style)."""
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:
        pytest.skip(SKIP_REASON, allow_module_level=True)
    return iter_route_contexts  # type: ignore[no-any-return]


@dataclass(frozen=True)
class RouteEntry:
    """One enumerated route: its template and the method keys it can be classified under."""

    template: str
    methods: tuple[str, ...]  # declared methods; (MOUNT_METHOD,) for a Mount
    name: str


def route_entries(app: FastAPI) -> list[RouteEntry]:
    """Every route of ``app`` (nested ones too), in registration order."""
    iter_route_contexts = require_route_contexts()
    entries: list[RouteEntry] = []
    for ctx in iter_route_contexts(list(app.router.routes)):
        if isinstance(ctx.original_route, Mount):
            entries.append(RouteEntry(ctx.path or "", (MOUNT_METHOD,), ctx.name or ""))
            continue
        methods = tuple(sorted(ctx.methods or ()))
        entries.append(RouteEntry(ctx.path or "", methods, ctx.name or ""))
    return entries


def concrete_path(template: str, *, mount: bool = False) -> str:
    """A request path that matches ``template`` (``{param}`` -> ``x``; a mount gets ``/x``)."""
    path = _PATH_PARAM.sub(PARAM_SAMPLE, template)
    return path.rstrip("/") + "/" + PARAM_SAMPLE if mount else path


def build_scope(app: FastAPI, method: str, path: str, *, root_path: str = "") -> dict[str, Any]:
    """The minimum ASGI scope ``classify`` needs (the router walks ``scope['app']``)."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": root_path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "app": app,
    }


def classify_request(
    app: FastAPI,
    method: str,
    path: str,
    policies: Mapping[RouteKey, RoutePolicy],
    cookie_only: frozenset[RouteKey] = frozenset(),
    *,
    root_path: str = "",
) -> tuple[Any, RoutePolicy, bool]:
    return classify(build_scope(app, method, path, root_path=root_path), policies, cookie_only)


def non_authenticated_set(
    app: FastAPI,
    policies: Mapping[RouteKey, RoutePolicy],
    cookie_only: frozenset[RouteKey] = frozenset(),
) -> set[RouteKey]:
    """The ``(method, template)`` pairs ``classify`` leaves non-AUTHENTICATED, per route context.

    Each route is classified for its *declared* methods only (a Mount under ``MOUNT``); the
    PARTIAL (405) behaviour of other methods is covered by the anonymous matrix.
    """
    found: set[RouteKey] = set()
    for entry in route_entries(app):
        for method in entry.methods:
            path = concrete_path(entry.template, mount=method == MOUNT_METHOD)
            request_method = "GET" if method == MOUNT_METHOD else method
            _route, policy, _cookie_only = classify_request(
                app, request_method, path, policies, cookie_only
            )
            if policy is not RoutePolicy.AUTHENTICATED:
                found.add((method, entry.template))
    return found


def anonymous_matrix(
    client: TestClient,
    app: FastAPI,
    headers: Mapping[str, str] | None = None,
) -> Iterator[tuple[str, str, int, dict[str, Any] | None]]:
    """Send each of GET/POST/PUT/PATCH/DELETE to every route template.

    Yields ``(method, template, status, json_body_or_None)``. Mutations carry ``{}`` and a
    same-origin ``Origin`` (plus ``headers``, e.g. a session proof).
    """
    base = {**same_origin_headers(client), **(headers or {})}
    for entry in route_entries(app):
        path = concrete_path(entry.template, mount=entry.methods == (MOUNT_METHOD,))
        for method in ENUMERATED_METHODS:
            kwargs: dict[str, Any] = {"headers": base}
            if method in MUTATING:
                kwargs["json"] = {}
            response = client.request(method, path, **kwargs)
            try:
                body = response.json()
            except ValueError:
                body = None
            yield method, entry.template, response.status_code, body
