"""Supervisor handling of auth (T-PDGw9p AC-31; HLD 14.8, 16 row 7).

`child_env` reaches the default spawner's child; an empty relay changes nothing about the
`Popen` call; a child exiting `EXIT_CONFIG` is terminal (no restart, no port reassignment).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent_orchestrator.errors import EXIT_CONFIG
from agent_orchestrator.service.registry import ServiceRegistry, ServiceRegistryFile, WorkspaceEntry
from agent_orchestrator.service.supervisor import (
    SUPERVISOR_SNAPSHOT_FILENAME,
    Supervisor,
    default_child_spawner,
)
from tests.service.test_supervisor import (
    _FakeMonotonic,
    _free_port,
    _no_sleep,
    _sleep_child_spawner,
    _wait_until,
)

SENTINEL_VAR = "AO_TEST_INHERITED_SENTINEL"
TICKS_AFTER_EXIT = 5
DUMP_ENV_SCRIPT = "import json, os, sys\nsys.stdout.write(json.dumps(dict(os.environ)))\n"


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "service.yaml"


def _registry(registry_path: Path, ws: Path, port: int) -> ServiceRegistryFile:
    data = ServiceRegistryFile(workspaces=[WorkspaceEntry(root=str(ws.resolve()), port=port)])
    ServiceRegistry(registry_path).save(data)
    return data


# --- (a)/(b) child_env -> default spawner ----------------------------------------------------


def test_default_spawner_layers_child_env_over_the_inherited_environment(
    tmp_path: Path, state_dir: Path, registry_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SENTINEL_VAR, "inherited")
    ws = tmp_path / "ws"
    ws.mkdir()
    script = tmp_path / "dump_env.py"
    script.write_text(DUMP_ENV_SCRIPT, encoding="utf-8")
    supervisor = Supervisor(
        _registry(registry_path, ws, _free_port()),
        ao_executable=[sys.executable, str(script)],  # argv tail (ui --workspace ...) ignored
        state_dir=state_dir,
        hub_port=8770,
        registry_path=registry_path,
        child_env={"AO_TEST_FLAG": "x"},
        sleeper=_no_sleep,
    )
    supervisor.start()
    try:
        child = next(iter(supervisor._children.values()))
        assert child.popen is not None
        assert _wait_until(lambda: child.popen is not None and child.popen.poll() is not None)
        assert child.log_path is not None
        dumped = json.loads(Path(child.log_path).read_text(encoding="utf-8"))
    finally:
        supervisor.shutdown(grace_seconds=2.0)
    assert dumped["AO_TEST_FLAG"] == "x"
    assert dumped[SENTINEL_VAR] == "inherited"


@pytest.mark.parametrize("extra_env", [None, {}])
def test_no_env_kwarg_reaches_popen_without_a_relay(
    tmp_path: Path, extra_env: dict[str, str] | None
) -> None:
    spawn = default_child_spawner(["ao"], extra_env=extra_env)
    with patch("agent_orchestrator.service.supervisor.subprocess.Popen") as popen:
        spawn(str(tmp_path), 9000, "127.0.0.1", tmp_path / "logs" / "c.log")
    assert "env" not in popen.call_args.kwargs


def test_a_relay_passes_a_merged_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SENTINEL_VAR, "inherited")
    spawn = default_child_spawner(["ao"], extra_env={"AO_UI_AUTH": "1"})
    with patch("agent_orchestrator.service.supervisor.subprocess.Popen") as popen:
        spawn(str(tmp_path), 9000, "127.0.0.1", tmp_path / "logs" / "c.log")
    env: dict[str, Any] = popen.call_args.kwargs["env"]
    assert env["AO_UI_AUTH"] == "1" and env[SENTINEL_VAR] == "inherited"
    assert env["PATH"] == os.environ["PATH"]


def test_an_injected_spawner_ignores_child_env(
    tmp_path: Path, state_dir: Path, registry_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    calls: list[tuple[Any, ...]] = []
    inner = _sleep_child_spawner()

    def _spawner(root: str, port: int, host: str, log_path: Path) -> subprocess.Popen:
        calls.append((root, port, host, log_path))
        return inner(root, port, host, log_path)

    supervisor = Supervisor(
        _registry(registry_path, ws, _free_port()),
        state_dir=state_dir,
        hub_port=8770,
        registry_path=registry_path,
        child_spawner=_spawner,
        child_env={"AO_UI_AUTH": "1"},
        sleeper=_no_sleep,
    )
    supervisor.start()
    try:
        assert len(calls) == 1
    finally:
        supervisor.shutdown(grace_seconds=2.0)


# --- (c) exit 78 is terminal -----------------------------------------------------------------


def test_a_child_exiting_with_the_config_code_is_never_restarted(
    tmp_path: Path, state_dir: Path, registry_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    port = _free_port()
    clock = _FakeMonotonic(start=0.0)
    spawns: list[int] = []
    inner = _sleep_child_spawner(f"exit {EXIT_CONFIG}")

    def _spawner(root: str, p: int, host: str, log_path: Path) -> subprocess.Popen:
        spawns.append(p)
        return inner(root, p, host, log_path)

    supervisor = Supervisor(
        _registry(registry_path, ws, port),
        state_dir=state_dir,
        hub_port=8770,
        registry_path=registry_path,
        child_spawner=_spawner,
        monotonic=clock,
        sleeper=_no_sleep,
    )
    supervisor.start()
    try:
        child = next(iter(supervisor._children.values()))
        assert _wait_until(lambda: child.popen is not None and child.popen.poll() is not None)
        for _ in range(TICKS_AFTER_EXIT):
            supervisor.tick()
            clock.advance(60.0)  # far beyond any backoff: a restart would have happened
    finally:
        supervisor.shutdown(grace_seconds=2.0)

    assert len(spawns) == 1
    assert child.restart_count == 0 and child.fast_fail_count == 0
    assert child.port == port and child.reassignment_reason is None
    assert child.next_retry_at is None


def test_the_terminal_state_is_reported_as_stopped_with_a_config_error(
    tmp_path: Path, state_dir: Path, registry_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    port = _free_port()
    clock = _FakeMonotonic(start=0.0)
    supervisor = Supervisor(
        _registry(registry_path, ws, port),
        state_dir=state_dir,
        hub_port=8770,
        registry_path=registry_path,
        child_spawner=_sleep_child_spawner(f"exit {EXIT_CONFIG}"),
        monotonic=clock,
        sleeper=_no_sleep,
    )
    supervisor.start()
    try:
        child = next(iter(supervisor._children.values()))
        assert _wait_until(lambda: child.popen is not None and child.popen.poll() is not None)
        for _ in range(TICKS_AFTER_EXIT):
            supervisor.tick()
            clock.advance(60.0)
        [workspace] = supervisor.status_snapshot()["workspaces"]
        persisted = json.loads((state_dir / SUPERVISOR_SNAPSHOT_FILENAME).read_text("utf-8"))
    finally:
        supervisor.shutdown(grace_seconds=2.0)

    assert workspace["state"] == "stopped"
    assert workspace["port"] == port and workspace["restart_count"] == 0
    assert "configuration error" in workspace["last_error"]
    assert str(EXIT_CONFIG) in workspace["last_error"]
    # The persisted snapshot lists live children only (it feeds orphan reclamation): the
    # terminal child has no pid to reclaim, so it is absent rather than shown as running.
    assert persisted["children"] == []


def test_other_exit_codes_still_restart(
    tmp_path: Path, state_dir: Path, registry_path: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    clock = _FakeMonotonic(start=0.0)
    supervisor = Supervisor(
        _registry(registry_path, ws, _free_port()),
        state_dir=state_dir,
        hub_port=8770,
        registry_path=registry_path,
        child_spawner=_sleep_child_spawner("exit 1"),
        monotonic=clock,
        sleeper=_no_sleep,
    )
    supervisor.start()
    try:
        child = next(iter(supervisor._children.values()))
        assert _wait_until(lambda: child.popen is not None and child.popen.poll() is not None)
        supervisor.tick()
        assert child.next_retry_at is not None and child.restart_count == 1
    finally:
        supervisor.shutdown(grace_seconds=2.0)
