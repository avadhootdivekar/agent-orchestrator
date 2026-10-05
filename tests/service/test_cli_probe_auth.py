"""`ao service list/status` against an authenticated hub (T-PDGw9p AC-18; HLD 15 row 1).

The hub answers an anonymous status probe with 401: `_probe_hub_status` turns that into
`HubLoginRequired`, and `list`/`status` fall back to the persisted state (exit 0).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.service import cli as service_cli
from agent_orchestrator.service.cli import HubLoginRequired, _probe_hub_status

runner = CliRunner()
PORT = 8770


@pytest.fixture(autouse=True)
def _isolated_service_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path / "state"))


def _raising_urlopen(exc: BaseException) -> Any:
    def _urlopen(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _urlopen


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://127.0.0.1/x", code, "msg", Message(), None)


def _raise_login_required(port: int) -> Any:
    raise HubLoginRequired(port)


# --- the probe ---------------------------------------------------------------------------


def test_a_401_raises_hub_login_required_with_the_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen(_http_error(401)))
    with pytest.raises(HubLoginRequired) as excinfo:
        _probe_hub_status(PORT)
    assert excinfo.value.hub_port == PORT


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(500),
        _http_error(403),
        urllib.error.URLError("refused"),
        TimeoutError("slow"),
    ],
)
def test_every_other_failure_is_none(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen(exc))
    assert _probe_hub_status(PORT) is None


# --- list / status fallbacks ----------------------------------------------------------------


def _seed_state(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "supervisor.json").write_text(json.dumps({"hub_port": PORT}), encoding="utf-8")
    (state / "port_resolution.json").write_text(json.dumps({"ports": {}}), encoding="utf-8")


def test_status_with_login_required_prints_persisted_state_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_state(tmp_path)
    monkeypatch.setattr(service_cli, "_probe_hub_status", _raise_login_required)
    result = runner.invoke(app, ["service", "status"])
    assert result.exit_code == 0, result.output
    assert (
        f"Service daemon running on port {PORT} (login required for the live view); "
        "persisted state:"
    ) in result.output
    assert '"supervisor"' in result.output and '"port_resolution"' in result.output
    assert "not running" not in result.output


def test_status_with_login_required_and_no_state_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_cli, "_probe_hub_status", _raise_login_required)
    result = runner.invoke(app, ["service", "status"])
    assert result.exit_code == 0, result.output
    assert "login required for the live view" in result.output
    assert "No persisted service state found" in result.output


def test_list_with_login_required_prints_rows_and_the_auth_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws1"
    ws.mkdir()
    assert runner.invoke(app, ["service", "add", str(ws), "--port", "9123"]).exit_code == 0
    monkeypatch.setattr(service_cli, "_probe_hub_status", _raise_login_required)
    result = runner.invoke(app, ["service", "list"])
    assert result.exit_code == 0, result.output
    assert str(ws.resolve()) in result.output and "9123" in result.output
    assert "hub running with dashboard authentication enabled" in result.output
    assert "not confirmed running" not in result.output


def test_a_real_401_flows_through_list_and_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_state(tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen(_http_error(401)))
    status = runner.invoke(app, ["service", "status"])
    listing = runner.invoke(app, ["service", "list"])
    assert status.exit_code == 0 and "login required for the live view" in status.output
    assert listing.exit_code == 0 and "authentication enabled" in listing.output


# --- install hint ---------------------------------------------------------------------------


def test_install_next_steps_mention_enabling_dashboard_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["/opt/ao/bin/ao", "service", "install"])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0, result.output
    assert "AO_UI_AUTH=1" in result.output
    assert "ao auth add-user" in result.output
    assert "systemctl --user restart ao" in result.output
