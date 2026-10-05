"""Shared fixtures for the HTTP-edge auth tests (owner: T-G7qByZ; HLD section 20.2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

import agent_orchestrator.ui.app as ui_app
from agent_orchestrator.ui.service import DashboardService
from tests.auth.helpers.core import FakeClock
from tests.auth.helpers.real_runtime import RealRuntime, RuntimeImpl, make_runtime
from tests.auth.helpers.stub_runtime import StubRuntime, stub_install_auth_routes
from tests.ui.conftest import StubSupervisor

INDEX_HTML = "<!doctype html><title>spa shell</title>"
ASSET_JS = "console.log('asset');"


@pytest.fixture(autouse=True)
def _allow_testclient_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TestClient`` sends ``Host: testserver``; widen the Host allowlist like tests/ui does."""
    monkeypatch.setenv("AO_UI_ALLOWED_HOSTS", "testserver")


@pytest.fixture()
def dashboard_service(tmp_path: Path) -> DashboardService:
    """A ``DashboardService`` over an empty workspace with the ``StubSupervisor`` (no processes)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return DashboardService(str(workspace), supervisor=StubSupervisor(workspace))  # type: ignore[arg-type]


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=["stub", "real"])
def stub_runtime(
    request: pytest.FixtureRequest, tmp_path: Path, fake_clock: FakeClock
) -> StubRuntime | RealRuntime:
    """The runtime the HTTP-edge tests run against, parametrized (T-XchniS final wiring).

    ``stub``: the duck-typed ``StubRuntime`` (T-G7qByZ, S1). ``real``: ``build_auth_runtime(...)``
    with a real ``LocalPasswordProvider`` over a real ``users.json``. Same helper surface, so every
    test using this fixture proves the middleware against both. (The name is kept: many tests
    request it.)
    """
    impl: RuntimeImpl = request.param
    return make_runtime(impl, tmp_path, clock=fake_clock)


@pytest.fixture()
def static_dir(tmp_path: Path) -> Path:
    """A built-frontend stand-in: ``index.html`` plus ``assets/``."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "app.js").write_text(ASSET_JS, encoding="utf-8")
    return root


@pytest.fixture()
def build_dashboard(
    monkeypatch: pytest.MonkeyPatch, dashboard_service: DashboardService, static_dir: Path
) -> Callable[..., FastAPI]:
    """Factory ``(runtime=None, *, built=True) -> FastAPI`` for the real ``create_app``.

    ``built=False`` points ``STATIC_DIR`` at a missing directory (the unbuilt frontend). With a
    runtime the stub auth routes stand in for ``install_auth_routes`` (T-rpKCjP lands the real
    ones); with ``None`` the real ``install_auth_routes`` runs, exactly as in production.
    """

    def factory(runtime: StubRuntime | RealRuntime | None = None, *, built: bool = True) -> FastAPI:
        monkeypatch.setattr(ui_app, "STATIC_DIR", static_dir if built else static_dir / "missing")
        if runtime is not None:
            monkeypatch.setattr(ui_app, "install_auth_routes", stub_install_auth_routes)
        return ui_app.create_app(dashboard_service, auth=runtime)

    return factory
