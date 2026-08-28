"""Tests for `service.hub.build_hub_app` (T-Hb3x7q AC10, AC14/AC15 early-gate corrections).

Uses a fake `status_provider` -- no real `Supervisor`, no subprocesses -- exactly the
pattern `tests/ui/test_security.py`'s `hardened_client` fixture uses for the dashboard app:
`TestClient(app, base_url="http://localhost")` so the default `Host`/`Origin` the client
sends land inside `ui.security.DEFAULT_ALLOWED_HOSTS` without needing an env override.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="hub app needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.service.hub import build_hub_app  # noqa: E402


def _fake_payload() -> dict[str, Any]:
    return {
        "workspaces": [
            {
                "root": "/home/user/proj1",
                "port": 9001,
                "pid": 4242,
                "state": "running",
                "restart_count": 0,
                "last_error": None,
                "log_path": "/state/logs/proj1.log",
                "reassignment_reason": None,
                "run_summary": {"total_runs": 3, "runs_by_status": {"succeeded": 3}},
            },
            {
                "root": "/home/user/proj2",
                "port": 9002,
                "pid": None,
                "state": "stopped",
                "restart_count": 2,
                "last_error": "child exited with code 1",
                "log_path": "/state/logs/proj2.log",
                "reassignment_reason": None,
            },
        ],
        "conflicts": [],
        "boot_resume_decisions": [
            {"workspace_root": "/home/user/proj1", "run_id": "r1", "decision": "approve"}
        ],
        "supervisor_pid": 999,
        "hub_port": 8770,
        "uptime_seconds": 42.0,
    }


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = build_hub_app(_fake_payload)
    with TestClient(app, base_url="http://localhost") as c:
        yield c


class TestIndexHtml:
    def test_returns_html_containing_each_workspace_root_and_port(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "/home/user/proj1" in response.text
        assert "9001" in response.text
        assert "/home/user/proj2" in response.text
        assert "9002" in response.text

    def test_includes_a_clickable_dashboard_link(self, client: TestClient) -> None:
        response = client.get("/")
        assert 'href="http://127.0.0.1:9001/"' in response.text

    def test_includes_the_run_summary_from_the_status_provider_payload(
        self, client: TestClient
    ) -> None:
        # AC2/AC15: the run-count summary is whatever the status_provider payload carries
        # (a `run_summary` key here) -- hub.py must not recompute it a second way.
        response = client.get("/")
        assert "3" in response.text  # proj1's total_runs

    def test_workspace_without_a_run_summary_renders_gracefully(self, client: TestClient) -> None:
        # proj2 has no `run_summary` key at all -- hub.py must not crash or assume it exists.
        response = client.get("/")
        assert response.status_code == 200

    def test_empty_workspace_list_renders_without_error(self) -> None:
        app = build_hub_app(lambda: {"workspaces": [], "hub_port": 8770, "supervisor_pid": 1})
        with TestClient(app, base_url="http://localhost") as c:
            response = c.get("/")
        assert response.status_code == 200
        assert "No workspaces registered" in response.text


class TestApiStatus:
    def test_returns_the_status_provider_payload_verbatim_as_json(self, client: TestClient) -> None:
        response = client.get("/api/service/status")
        assert response.status_code == 200
        assert response.json() == _fake_payload()


class TestSecurityMiddlewareMounted:
    """AC14: `build_hub_app` must actually MOUNT `SecurityMiddleware`, not just import its
    constants -- proven here by asserting a disallowed `Host` header gets HTTP 421 from a
    real `TestClient` request, not by inspecting import statements."""

    def test_allowed_host_passes(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200

    def test_disallowed_host_header_is_rejected_with_421(self, client: TestClient) -> None:
        response = client.get("/", headers={"Host": "evil.example.com"})
        assert response.status_code == 421

    def test_disallowed_host_also_rejects_the_json_status_endpoint(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/service/status", headers={"Host": "attacker.test"})
        assert response.status_code == 421

    def test_security_headers_present_on_a_normal_response(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_hub_module_imports_cleanly_even_when_fastapi_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: a core `ao` install without the `[ui]` extra must still be able to import
    `service/hub.py` itself without raising `ImportError` -- proven here by making a real
    `import fastapi` raise (simulating the extra being absent) and reimporting the module
    fresh, rather than just eyeballing that the import statement sits inside a function body.
    """
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def _blocking_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError("simulated: [ui] extra not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    monkeypatch.delitem(sys.modules, "agent_orchestrator.service.hub", raising=False)
    try:
        module = importlib.import_module("agent_orchestrator.service.hub")
        # Importing the module must succeed; only *calling* build_hub_app should fail.
        with pytest.raises(ImportError):
            module.build_hub_app(lambda: {})
    finally:
        monkeypatch.delitem(sys.modules, "agent_orchestrator.service.hub", raising=False)
        importlib.import_module("agent_orchestrator.service.hub")


class TestHostAwareLinks:
    """A workspace bound to 0.0.0.0 gets a connectable loopback link plus a bind
    annotation; an explicit LAN host is linked as-is."""

    def test_wildcard_bind_links_loopback_and_annotates(self) -> None:
        from agent_orchestrator.service.hub import _render_workspace_row

        row = _render_workspace_row(
            {"root": "/w", "port": 8767, "host": "0.0.0.0", "state": "running"}
        )
        assert "http://127.0.0.1:8767/" in row
        assert "bound 0.0.0.0" in row

    def test_explicit_host_is_linked_directly(self) -> None:
        from agent_orchestrator.service.hub import _render_workspace_row

        row = _render_workspace_row(
            {"root": "/w", "port": 9001, "host": "192.168.1.50", "state": "running"}
        )
        assert "http://192.168.1.50:9001/" in row
        assert "bound" not in row

    def test_missing_host_falls_back_to_loopback(self) -> None:
        from agent_orchestrator.service.hub import _render_workspace_row

        row = _render_workspace_row({"root": "/w", "port": 9002, "state": "running"})
        assert "http://127.0.0.1:9002/" in row
