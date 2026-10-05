"""``ao service run`` with auth (T-PDGw9p; AC-3 service part incl. the v2.1 port-0 refusal,
AC-31 run wiring, invariant S14; HLD 14.8, 16 row 6).

Drives the real CLI through ``CliRunner`` with stand-ins for ``uvicorn`` and ``Supervisor``
(recording only), a ``threading.Event`` whose loop exits at once and a no-op ``signal.signal``,
so what is asserted is how the daemon is wired, not that anything serves or spawns.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.errors import EXIT_CONFIG
from agent_orchestrator.service import cli as service_cli
from agent_orchestrator.service import hub as hub_module
from tests.auth.helpers.launch import LaunchEnv, make_launch_env

runner = CliRunner()
HUB_PORT = 8770
ENV_KEYS = ("AO_UI_AUTH", "AO_UI_AUTH_TOTP", "AO_AUTH_DIR", "AO_AUTH_STATE_DIR")


class _StubUvicorn:
    """Records ``Config`` kwargs; ``Server.run`` does nothing."""

    def __init__(self) -> None:
        self.config_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        outer = self

        class Config:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                outer.config_calls.append((args, kwargs))

        class Server:
            def __init__(self, config: Any) -> None:
                self.config = config
                self.should_exit = False

            def run(self) -> None:
                return None

        self.Config = Config
        self.Server = Server


class _StubSupervisor:
    def __init__(self, registry: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def tick(self) -> None:
        self.calls.append("tick")

    def shutdown(self) -> None:
        self.calls.append("shutdown")


class _Recorder:
    """Stands in for ``Supervisor`` and ``build_hub_app``; keeps what it was given."""

    def __init__(self) -> None:
        self.supervisors: list[_StubSupervisor] = []
        self.hub_calls: list[dict[str, Any]] = []

    def supervisor_factory(self) -> type:
        outer = self

        class Supervisor(_StubSupervisor):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                outer.supervisors.append(self)

        return Supervisor

    def build_hub_app(self, status_provider: Any, *, auth: Any = None) -> object:
        self.hub_calls.append({"auth": auth})
        return object()


class _ImmediateStopEvent:
    """``is_set()`` is already True, so ``run``'s monitor loop exits without waiting."""

    def set(self) -> None:
        return None

    def is_set(self) -> bool:
        return True

    def wait(self, timeout: float | None = None) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture()
def uvicorn_stub(monkeypatch: pytest.MonkeyPatch) -> _StubUvicorn:
    stub = _StubUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", stub)
    return stub


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(service_cli, "Supervisor", rec.supervisor_factory())
    monkeypatch.setattr(hub_module, "build_hub_app", rec.build_hub_app)
    monkeypatch.setattr(
        service_cli,
        "threading",
        SimpleNamespace(Event=_ImmediateStopEvent, Thread=threading.Thread),
    )
    monkeypatch.setattr(service_cli.signal, "signal", lambda *_a, **_k: None)
    return rec


@pytest.fixture()
def lenv(tmp_path: Path) -> LaunchEnv:
    return make_launch_env(tmp_path / "launch")


def service_run(*args: str) -> Any:
    return runner.invoke(app, ["service", "run", *args])


def _state_files(tmp_path: Path) -> list[Path]:
    state = tmp_path / "state"
    return sorted(state.rglob("*")) if state.exists() else []


# --- AC 1: refusal happens before anything is constructed -------------------------------


def test_auth_with_an_empty_store_exits_78_before_any_supervisor(
    tmp_path: Path, uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    result = service_run("--auth", "--auth-dir", str(empty.store_dir))
    assert result.exit_code == EXIT_CONFIG == 78
    assert "ERROR:" in result.output and "ao auth add-user" in result.output
    assert recorder.supervisors == []  # S14: no singleton lock, no child
    assert uvicorn_stub.config_calls == []
    assert recorder.hub_calls == []
    assert _state_files(tmp_path) == []  # no lock file, no supervisor.json


def test_h_code_1_a_read_only_store_exits_78_before_any_supervisor(
    tmp_path: Path, uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    ro = make_launch_env(tmp_path / "ro", users=("alice",))
    ro.state_dir.rmdir()  # the derived <store>/state must be created -> PermissionError
    ro.store_dir.chmod(0o500)
    try:
        result = service_run("--auth", "--auth-dir", str(ro.store_dir))
    finally:
        ro.store_dir.chmod(0o700)
    assert result.exit_code == EXIT_CONFIG == 78, result.output
    assert "Traceback" not in result.output
    assert recorder.supervisors == [] and uvicorn_stub.config_calls == []


def test_the_same_refusal_through_the_environment(
    tmp_path: Path,
    uvicorn_stub: _StubUvicorn,
    recorder: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    for key, value in empty.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AO_UI_AUTH", "1")
    result = service_run()
    assert result.exit_code == 78 and "ao auth add-user" in result.output
    assert recorder.supervisors == [] and uvicorn_stub.config_calls == []


# --- AC 1b: port 0 (security L4) -----------------------------------------------------------


def test_auth_with_port_zero_exits_78_and_spawns_nothing(
    lenv: LaunchEnv, uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    result = service_run("--auth", "--auth-dir", str(lenv.store_dir), "--hub-port", "0")
    assert result.exit_code == 78
    assert "--port 0" in result.output
    assert recorder.supervisors == [] and uvicorn_stub.config_calls == []


def test_port_zero_without_auth_behaves_as_before(
    uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    result = service_run("--hub-port", "0")
    assert result.exit_code == 0, result.output
    [(_args, kwargs)] = uvicorn_stub.config_calls
    assert kwargs["port"] == 0


# --- AC 2: wiring ----------------------------------------------------------------------------


def test_cli_sourced_settings_are_relayed_to_the_children(
    lenv: LaunchEnv, uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    result = service_run(
        "--auth", "--auth-totp", "required", "--auth-dir", str(lenv.store_dir), "--hub-port", "8771"
    )
    assert result.exit_code == 0, result.output
    [supervisor] = recorder.supervisors
    assert supervisor.kwargs["child_env"] == {
        "AO_UI_AUTH": "1",
        "AO_UI_AUTH_TOTP": "required",
        "AO_AUTH_DIR": str(lenv.store_dir),
    }
    [hub_call] = recorder.hub_calls
    runtime = hub_call["auth"]
    assert runtime.realm.id == "hub"
    assert runtime.realm.cookie_name(secure=False) == "ao_sid_8771"
    [(_args, kwargs)] = uvicorn_stub.config_calls
    assert kwargs["proxy_headers"] is False
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8771
    assert supervisor.calls.count("shutdown") == 1


def test_env_only_settings_are_not_relayed(
    lenv: LaunchEnv,
    uvicorn_stub: _StubUvicorn,
    recorder: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The children inherit the environment themselves; the relay carries CLI flags only.
    for key, value in lenv.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AO_UI_AUTH", "1")
    result = service_run()
    assert result.exit_code == 0, result.output
    [supervisor] = recorder.supervisors
    assert supervisor.kwargs["child_env"] == {}
    assert recorder.hub_calls[0]["auth"] is not None
    assert supervisor.calls.count("shutdown") == 1


def test_auth_off_is_exactly_the_pre_auth_wiring(
    uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    result = service_run()
    assert result.exit_code == 0, result.output
    [(_args, kwargs)] = uvicorn_stub.config_calls
    assert kwargs == {"host": "127.0.0.1", "port": HUB_PORT, "log_level": "warning"}
    assert recorder.hub_calls == [{"auth": None}]
    [supervisor] = recorder.supervisors
    assert supervisor.kwargs["child_env"] == {}
    assert supervisor.calls.count("shutdown") == 1


def test_warnings_are_printed_to_stderr(
    lenv: LaunchEnv, uvicorn_stub: _StubUvicorn, recorder: _Recorder
) -> None:
    result = service_run("--auth", "--auth-dir", str(lenv.store_dir), "--hub-host", "0.0.0.0")
    assert result.exit_code == 0, result.output
    assert "not encrypted" in result.output
