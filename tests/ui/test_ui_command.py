"""Tests for the `ao ui` command body and the app-construction helpers (E-Ui7Kq2).

`ao ui` ends in a blocking ``uvicorn.run`` call, so these tests substitute a recording stub
for uvicorn: what matters is *how* the server is configured (bind address, factory vs live
app, workspace hand-off), not that a socket really opens — the live-server e2e test covers
that.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("fastapi", reason="dashboard needs the optional [ui] extra")

from agent_orchestrator.cli import UI_DEFAULT_HOST, UI_DEFAULT_PORT, app  # noqa: E402

runner = CliRunner()


class RecordingUvicorn:
    """Stand-in for the uvicorn module that records `run()` calls instead of serving."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def run(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


@pytest.fixture()
def fake_uvicorn(monkeypatch: pytest.MonkeyPatch) -> RecordingUvicorn:
    stub = RecordingUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", stub)
    return stub


class TestServerConfiguration:
    def test_binds_loopback_and_the_default_port(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        # The dashboard is unauthenticated and can spend money, so the default bind must
        # stay on loopback.
        result = runner.invoke(app, ["ui", "--workspace", str(tmp_path)])
        assert result.exit_code == 0, result.output

        _, kwargs = fake_uvicorn.calls[0]
        assert kwargs["host"] == UI_DEFAULT_HOST == "127.0.0.1"
        assert kwargs["port"] == UI_DEFAULT_PORT

    def test_honours_explicit_host_and_port(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        result = runner.invoke(
            app, ["ui", "--workspace", str(tmp_path), "--host", "0.0.0.0", "--port", "9999"]
        )
        assert result.exit_code == 0
        _, kwargs = fake_uvicorn.calls[0]
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 9999

    def test_warns_when_binding_a_non_loopback_address(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        result = runner.invoke(app, ["ui", "--workspace", str(tmp_path), "--host", "0.0.0.0"])
        assert "UNAUTHENTICATED" in result.output

    def test_does_not_warn_for_loopback(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        result = runner.invoke(app, ["ui", "--workspace", str(tmp_path)])
        assert "UNAUTHENTICATED" not in result.output

    def test_prints_the_url_and_workspace(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        result = runner.invoke(app, ["ui", "--workspace", str(tmp_path), "--port", "8123"])
        assert "http://127.0.0.1:8123" in result.output
        assert str(tmp_path.resolve()) in result.output

    def test_passes_a_live_app_object_when_not_reloading(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        runner.invoke(app, ["ui", "--workspace", str(tmp_path)])
        args, kwargs = fake_uvicorn.calls[0]
        assert args, "the non-reload path serves an already-constructed app"
        assert "factory" not in kwargs

    def test_reload_mode_uses_an_import_string_factory(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The reloader re-imports in a fresh process, so it needs an import string plus the
        # workspace handed over through the environment, not a live object.
        monkeypatch.delenv("AO_UI_WORKSPACE", raising=False)
        runner.invoke(app, ["ui", "--workspace", str(tmp_path), "--reload"])

        args, kwargs = fake_uvicorn.calls[0]
        assert args[0] == "agent_orchestrator.ui.app:create_app_from_env"
        assert kwargs["factory"] is True and kwargs["reload"] is True
        import os

        assert os.environ["AO_UI_WORKSPACE"] == str(tmp_path.resolve())


class TestWorkspaceResolution:
    def test_defaults_to_the_current_directory(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_WORKSPACE_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        assert str(tmp_path.resolve()) in result.output

    def test_reads_the_workspace_env_var(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        assert str(tmp_path.resolve()) in result.output

    def test_flag_beats_the_env_var(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
        result = runner.invoke(app, ["ui", "--workspace", str(other)])
        assert str(other.resolve()) in result.output

    def test_a_file_is_not_a_valid_workspace(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn
    ) -> None:
        target = tmp_path / "afile.txt"
        target.write_text("x", encoding="utf-8")
        result = runner.invoke(app, ["ui", "--workspace", str(target)])
        assert result.exit_code == 1
        assert "not a directory" in result.output


class TestOpenBrowser:
    def test_schedules_a_browser_open(
        self, tmp_path: Path, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

        # Fire the timer immediately instead of waiting out its real delay.
        import threading

        real_timer = threading.Timer

        def _immediate(_interval: float, function, *args, **kwargs):
            return real_timer(0, function, *args, **kwargs)

        monkeypatch.setattr(threading, "Timer", _immediate)

        result = runner.invoke(
            app, ["ui", "--workspace", str(tmp_path), "--open", "--port", "8321"]
        )
        assert result.exit_code == 0

        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not opened:
            time.sleep(0.01)
        assert opened == ["http://127.0.0.1:8321"]


class TestMissingExtra:
    def test_reports_how_to_install_the_extra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate a core-only install: importing uvicorn must fail with actionable advice
        # rather than a raw traceback.
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object):
            if name == "uvicorn":
                raise ImportError("No module named 'uvicorn'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked)
        monkeypatch.delitem(sys.modules, "uvicorn", raising=False)

        result = runner.invoke(app, ["ui", "--workspace", str(tmp_path)])
        assert result.exit_code == 1
        assert "extra" in result.output
        assert "uv sync --extra ui" in result.output


class TestAppHelpers:
    def test_create_app_from_env_uses_the_env_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from agent_orchestrator.ui.app import create_app_from_env

        monkeypatch.setenv("AO_UI_WORKSPACE", str(tmp_path))
        with TestClient(create_app_from_env()) as client:
            body = client.get("/api/workspace").json()
        assert body["workspace_root"] == str(tmp_path.resolve())

    def test_create_app_from_env_falls_back_to_the_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from agent_orchestrator.ui.app import create_app_from_env

        monkeypatch.delenv("AO_UI_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        with TestClient(create_app_from_env()) as client:
            body = client.get("/api/workspace").json()
        assert body["workspace_root"] == str(tmp_path.resolve())

    def test_missing_frontend_serves_a_503_with_build_instructions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A source checkout without `npm run build` must degrade to actionable guidance,
        # with the API still fully usable.
        from fastapi.testclient import TestClient

        import agent_orchestrator.ui.app as app_module
        from agent_orchestrator.ui.service import DashboardService

        monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path / "no-such-static")

        with TestClient(app_module.create_app(DashboardService(str(tmp_path)))) as client:
            response = client.get("/")
            assert response.status_code == 503
            assert "npm" in response.json()["fix"]
            # The API keeps working regardless.
            assert client.get("/api/health").status_code == 200


class TestLazyPackageExport:
    def test_dashboard_service_is_re_exported_lazily(self) -> None:
        import agent_orchestrator.ui as ui_pkg
        from agent_orchestrator.ui.service import DashboardService

        assert ui_pkg.DashboardService is DashboardService

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        import agent_orchestrator.ui as ui_pkg

        with pytest.raises(AttributeError):
            _ = ui_pkg.NoSuchThing
