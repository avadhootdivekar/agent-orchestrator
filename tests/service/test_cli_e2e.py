"""End-to-end `ao service` CLI tests via `CliRunner` (T-Hb3x7q AC12).

Every test points `AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR` at a `tmp_path` -- never the
real `~` -- via `monkeypatch.setenv`. `install`'s non-`--print` path additionally needs
`XDG_CONFIG_HOME` redirected (that is `service/systemd.py`'s own unit-dir env override, kept
deliberately independent of the registry/state env vars) and a monkeypatched `sys.argv` so
`resolve_ao_executable()` is deterministic regardless of whether a real `ao` happens to be on
this machine's PATH.

No test here invokes `ao service run` (it blocks and binds a real port) or shells out to real
`systemctl` -- `run`'s internals are covered by `Supervisor`'s own tests (`T-Sv9d4k`) plus
`test_hub.py`/`test_systemd.py` in this task.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.service.cli import build_status_provider

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_service_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this module gets its own registry/state dirs under `tmp_path`."""
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws1"
    ws.mkdir()
    return ws


class TestHelp:
    def test_lists_all_seven_subcommands(self) -> None:
        result = runner.invoke(app, ["service", "--help"])
        assert result.exit_code == 0, result.output
        for name in ("add", "remove", "list", "status", "install", "start", "stop", "run"):
            assert name in result.output, f"missing subcommand: {name}"


class TestAddListRemove:
    def test_add_then_list_shows_the_workspace(self, workspace: Path) -> None:
        add_result = runner.invoke(app, ["service", "add", str(workspace)])
        assert add_result.exit_code == 0, add_result.output

        list_result = runner.invoke(app, ["service", "list"])
        assert list_result.exit_code == 0, list_result.output
        assert str(workspace.resolve()) in list_result.output

    def test_add_with_port_pin_is_reflected_by_list(self, workspace: Path) -> None:
        result = runner.invoke(app, ["service", "add", str(workspace), "--port", "9123"])
        assert result.exit_code == 0, result.output

        list_result = runner.invoke(app, ["service", "list"])
        assert "9123" in list_result.output

    def test_add_nonexistent_directory_errors_clearly(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        result = runner.invoke(app, ["service", "add", str(missing)])
        assert result.exit_code != 0
        assert "not a directory" in result.output

    def test_remove_then_list_no_longer_shows_it(self, workspace: Path) -> None:
        runner.invoke(app, ["service", "add", str(workspace)])

        remove_result = runner.invoke(app, ["service", "remove", str(workspace)])
        assert remove_result.exit_code == 0, remove_result.output

        list_result = runner.invoke(app, ["service", "list"])
        assert str(workspace.resolve()) not in list_result.output
        assert "No workspaces registered" in list_result.output

    def test_remove_unregistered_workspace_errors_clearly(self, workspace: Path) -> None:
        result = runner.invoke(app, ["service", "remove", str(workspace)])
        assert result.exit_code != 0
        assert "not registered" in result.output


class TestStatusFallback:
    def test_status_with_no_daemon_and_empty_registry_falls_back_cleanly(self) -> None:
        result = runner.invoke(app, ["service", "status"])
        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output
        assert "not running" in result.output.lower() or "not reachable" in result.output.lower()

    def test_status_with_no_daemon_and_registered_entries_falls_back_cleanly(
        self, workspace: Path
    ) -> None:
        runner.invoke(app, ["service", "add", str(workspace)])
        result = runner.invoke(app, ["service", "status"])
        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output

    def test_list_with_no_daemon_notes_it_is_unconfirmed(self, workspace: Path) -> None:
        runner.invoke(app, ["service", "add", str(workspace)])
        result = runner.invoke(app, ["service", "list"])
        assert result.exit_code == 0, result.output
        assert "not confirmed running" in result.output


class TestInstall:
    def test_print_prints_unit_text_and_writes_nothing_to_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/opt/ao/bin/ao", "service", "install"])
        fake_home_config = tmp_path / "config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home_config))

        result = runner.invoke(app, ["service", "install", "--print"])

        assert result.exit_code == 0, result.output
        assert "KillMode=process" in result.output
        assert not fake_home_config.exists()  # zero filesystem writes

    def test_install_writes_the_unit_and_prints_next_step_guidance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/opt/ao/bin/ao", "service", "install"])
        fake_home_config = tmp_path / "config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home_config))

        result = runner.invoke(app, ["service", "install"])

        assert result.exit_code == 0, result.output
        unit_path = fake_home_config / "systemd" / "user" / "ao.service"
        assert unit_path.is_file()
        assert "KillMode=process" in unit_path.read_text()

        # HLD §7 guidance verbatim.
        assert "systemctl --user daemon-reload" in result.output
        assert "systemctl --user enable --now ao" in result.output
        assert "loginctl enable-linger" in result.output
        # AC18 early-gate additions: service.env credentials note + stale-snapshot risk.
        assert "service.env" in result.output
        assert "install.sh --force" in result.output

    def test_install_with_hub_port_bakes_it_into_the_written_unit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/opt/ao/bin/ao", "service", "install"])
        fake_home_config = tmp_path / "config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home_config))

        result = runner.invoke(app, ["service", "install", "--hub-port", "9321"])

        assert result.exit_code == 0, result.output
        unit_path = fake_home_config / "systemd" / "user" / "ao.service"
        assert "--hub-port 9321" in unit_path.read_text()

    def test_install_errors_clearly_when_ao_executable_cannot_be_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/usr/bin/python3", "-m", "agent_orchestrator.cli"])
        monkeypatch.setattr("shutil.which", lambda name: None)

        result = runner.invoke(app, ["service", "install", "--print"])

        assert result.exit_code != 0
        assert "ERROR" in result.output


class TestStartStopWithoutSystemd:
    def test_start_without_systemctl_prints_guidance_instead_of_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("agent_orchestrator.service.cli.systemctl_available", lambda: False)
        result = runner.invoke(app, ["service", "start"])
        assert result.exit_code == 0, result.output
        assert "ao service run" in result.output

    def test_stop_without_systemctl_prints_guidance_instead_of_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("agent_orchestrator.service.cli.systemctl_available", lambda: False)
        result = runner.invoke(app, ["service", "stop"])
        assert result.exit_code == 0, result.output
        assert "systemd" in result.output.lower()


class _FakeSupervisor:
    """Stand-in for `Supervisor` -- just needs `.status_snapshot()` (AC15's contract)."""

    def __init__(self, workspaces: list[dict]) -> None:
        self.calls = 0
        self._workspaces = workspaces

    def status_snapshot(self) -> dict:
        self.calls += 1
        return {
            "workspaces": [dict(w) for w in self._workspaces],
            "conflicts": [],
            "boot_resume_decisions": [],
            "supervisor_pid": 1,
            "hub_port": 8770,
            "uptime_seconds": 1.0,
        }


class _FakeRunRepository:
    def __init__(self, root: str) -> None:
        self.root = root

    def aggregate(self) -> SimpleNamespace:
        return SimpleNamespace(total_runs=5, runs_by_status={"succeeded": 5})


class TestBuildStatusProvider:
    """`service/cli.py::build_status_provider` -- AC2/AC15: hub.py itself never calls
    `RunRepository`; this is "whatever provides status_provider" that call belongs in
    instead, cached behind a short, clock-injectable TTL."""

    def test_enriches_each_workspace_with_a_run_summary(self) -> None:
        supervisor = _FakeSupervisor([{"root": "/w1", "port": 1}])
        provider = build_status_provider(
            supervisor, clock=lambda: 0.0, run_repository_factory=_FakeRunRepository
        )
        payload = provider()
        assert payload["workspaces"][0]["run_summary"] == {
            "total_runs": 5,
            "runs_by_status": {"succeeded": 5},
        }

    def test_caches_within_the_ttl_without_recomputing(self) -> None:
        supervisor = _FakeSupervisor([{"root": "/w1", "port": 1}])
        clock_value = [100.0]
        provider = build_status_provider(
            supervisor,
            clock=lambda: clock_value[0],
            run_repository_factory=_FakeRunRepository,
            ttl_seconds=5.0,
        )
        provider()
        clock_value[0] += 1.0  # still inside the TTL
        provider()
        assert supervisor.calls == 1

    def test_recomputes_once_the_ttl_expires(self) -> None:
        supervisor = _FakeSupervisor([{"root": "/w1", "port": 1}])
        clock_value = [0.0]
        provider = build_status_provider(
            supervisor,
            clock=lambda: clock_value[0],
            run_repository_factory=_FakeRunRepository,
            ttl_seconds=5.0,
        )
        provider()
        clock_value[0] += 10.0  # past the TTL
        provider()
        assert supervisor.calls == 2

    def test_a_bad_workspace_does_not_break_the_whole_payload(self) -> None:
        supervisor = _FakeSupervisor([{"root": "/does/not/exist", "port": 1}])

        def _raising_factory(root: str) -> _FakeRunRepository:
            raise OSError("boom")

        payload = build_status_provider(
            supervisor, clock=lambda: 0.0, run_repository_factory=_raising_factory
        )()

        assert "run_summary" not in payload["workspaces"][0]


class TestStartStopWithSystemctl:
    """The systemctl-present branches (coverage top-up): `systemctl_available` and the
    `*_via_systemctl` helpers are monkeypatched at the CLI module's imported names, so no
    real systemd is touched."""

    @staticmethod
    def _completed(rc: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=rc, stderr=stderr)

    def test_start_success_echoes_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_orchestrator.service.cli.systemctl_available", lambda: True)
        monkeypatch.setattr(
            "agent_orchestrator.service.cli.start_via_systemctl", lambda: self._completed(0)
        )
        result = runner.invoke(app, ["service", "start"])
        assert result.exit_code == 0, result.output
        assert "Started" in result.output

    def test_start_failure_exits_nonzero_with_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_orchestrator.service.cli.systemctl_available", lambda: True)
        monkeypatch.setattr(
            "agent_orchestrator.service.cli.start_via_systemctl",
            lambda: self._completed(1, stderr="unit not found"),
        )
        result = runner.invoke(app, ["service", "start"])
        assert result.exit_code == 1
        assert "unit not found" in result.output

    def test_stop_success_echoes_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_orchestrator.service.cli.systemctl_available", lambda: True)
        monkeypatch.setattr(
            "agent_orchestrator.service.cli.stop_via_systemctl", lambda: self._completed(0)
        )
        result = runner.invoke(app, ["service", "stop"])
        assert result.exit_code == 0, result.output
        assert "Stopped" in result.output

    def test_stop_failure_exits_nonzero_with_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_orchestrator.service.cli.systemctl_available", lambda: True)
        monkeypatch.setattr(
            "agent_orchestrator.service.cli.stop_via_systemctl",
            lambda: self._completed(1, stderr="stop failed"),
        )
        result = runner.invoke(app, ["service", "stop"])
        assert result.exit_code == 1
        assert "stop failed" in result.output


class TestStatusPaths:
    """`status`'s live-hub, corrupted-snapshot, and probe-failure branches."""

    def test_live_hub_json_is_printed_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        live = {"workspaces": [], "hub_port": 9999}
        monkeypatch.setattr("agent_orchestrator.service.cli._probe_hub_status", lambda port: live)
        result = runner.invoke(app, ["service", "status"])
        assert result.exit_code == 0, result.output
        assert '"hub_port": 9999' in result.output

    def test_corrupted_supervisor_snapshot_falls_back_to_guessed_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "supervisor.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr("agent_orchestrator.service.cli._probe_hub_status", lambda port: None)
        result = runner.invoke(app, ["service", "status"])
        assert result.exit_code == 0, result.output
        assert "guessed default port" in result.output

    def test_probe_returns_none_on_unreachable_hub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_orchestrator.service.cli import _probe_hub_status

        def _raise(*args: object, **kwargs: object) -> object:
            raise urllib.error.URLError("refused")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        assert _probe_hub_status(65001) is None

    def test_probe_returns_none_on_non_dict_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_orchestrator.service.cli import _probe_hub_status

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b'["a", "list"]'

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
        assert _probe_hub_status(65001) is None


class TestAddWithHost:
    def test_add_with_host_persists_the_pin(self, workspace: Path, tmp_path: Path) -> None:
        result = runner.invoke(app, ["service", "add", str(workspace), "--host", "0.0.0.0"])
        assert result.exit_code == 0, result.output

        from agent_orchestrator.service.registry import ServiceRegistry

        loaded = ServiceRegistry().load()
        assert loaded.workspaces[0].host == "0.0.0.0"
