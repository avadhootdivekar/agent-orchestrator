"""Tests for `service.systemd` (T-Hb3x7q AC11, AC16/AC17 early-gate corrections).

No test here invokes real `systemctl` (locked design decision, HLD §7) -- unit-file
generation is asserted as text, `resolve_ao_executable` branches are driven by monkeypatched
`sys.argv`/`shutil.which`, and `start_via_systemctl`/`stop_via_systemctl` are exercised with
an injected fake runner that records the argv it was called with.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_orchestrator.service.systemd import (
    DEFAULT_HUB_PORT,
    SYSTEMCTL_UNIT_NAME,
    ServiceError,
    install_unit,
    render_unit,
    resolve_ao_executable,
    start_via_systemctl,
    stop_via_systemctl,
    systemctl_available,
)


class TestRenderUnit:
    def test_contains_kill_mode_process(self) -> None:
        # ADR-0012 D2: the single most safety-critical line -- a reviewer will specifically
        # check for its presence. Never omit or default this.
        text = render_unit("/usr/local/bin/ao")
        assert "KillMode=process" in text

    def test_contains_the_exec_start_line_with_hub_port(self) -> None:
        text = render_unit("/usr/local/bin/ao", hub_port=9999)
        assert "ExecStart=/usr/local/bin/ao service run --hub-port 9999" in text

    def test_exec_start_uses_the_default_hub_port_when_unspecified(self) -> None:
        text = render_unit("/usr/local/bin/ao")
        assert f"--hub-port {DEFAULT_HUB_PORT}" in text
        assert DEFAULT_HUB_PORT == 8770

    def test_contains_restart_on_failure_and_wanted_by(self) -> None:
        text = render_unit("/usr/local/bin/ao")
        assert "Restart=on-failure" in text
        assert "WantedBy=default.target" in text

    def test_contains_restart_storm_guard_lines(self) -> None:
        # AC16 early-gate correction: systemd's own default restart-storm guard is tighter
        # than the supervisor's internal backoff -- these must be explicit, not defaulted.
        text = render_unit("/usr/local/bin/ao")
        assert "RestartSec=5" in text
        assert "StartLimitIntervalSec=120" in text
        assert "StartLimitBurst=5" in text

    def test_contains_timeout_stop_sec(self) -> None:
        text = render_unit("/usr/local/bin/ao")
        assert "TimeoutStopSec=30" in text

    def test_contains_optional_environment_file(self) -> None:
        # AC16: leading "-" makes a missing file non-fatal to startup.
        text = render_unit("/usr/local/bin/ao")
        assert "EnvironmentFile=-%h/.config/ao/service.env" in text


class TestResolveAoExecutable:
    def test_absolute_argv0_named_ao_is_used_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/opt/ao-tool/bin/ao", "service", "install"])
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert resolve_ao_executable() == "/opt/ao-tool/bin/ao"

    def test_relative_argv0_named_ao_is_rejected_even_though_basename_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # AC17: systemd requires an absolute ExecStart -- a relative argv[0] must fall
        # through to shutil.which, not be accepted just because its basename is "ao".
        monkeypatch.setattr(sys, "argv", ["./ao", "service", "install"])
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/ao")
        assert resolve_ao_executable() == "/usr/local/bin/ao"

    def test_relative_argv0_and_no_which_match_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["./ao", "service", "install"])
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(ServiceError):
            resolve_ao_executable()

    def test_non_ao_argv0_falls_through_to_shutil_which(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/usr/bin/python3", "-m", "agent_orchestrator.cli"])
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/ao")
        assert resolve_ao_executable() == "/usr/local/bin/ao"

    def test_neither_source_available_raises_clear_service_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/usr/bin/python3", "-m", "agent_orchestrator.cli"])
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(ServiceError, match="no absolute `ao` executable"):
            resolve_ao_executable()


class TestInstallUnit:
    def test_print_only_returns_rendered_text_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/ao", "service", "install"])
        before = set(tmp_path.rglob("*"))

        result = install_unit(print_only=True, unit_dir=tmp_path / "systemd" / "user")

        assert isinstance(result, str)
        assert "KillMode=process" in result
        assert result == render_unit("/usr/local/bin/ao")
        after = set(tmp_path.rglob("*"))
        assert before == after  # zero filesystem writes

    def test_writes_the_unit_file_and_returns_its_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/ao", "service", "install"])
        unit_dir = tmp_path / "config" / "systemd" / "user"

        result = install_unit(print_only=False, unit_dir=unit_dir, hub_port=8888)

        assert isinstance(result, Path)
        assert result == unit_dir / "ao.service"
        assert result.is_file()
        text = result.read_text()
        assert "KillMode=process" in text
        assert "--hub-port 8888" in text

    def test_raises_service_error_when_ao_executable_cannot_be_resolved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["/usr/bin/python3", "-m", "agent_orchestrator.cli"])
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(ServiceError):
            install_unit(print_only=True)


class TestSystemctlAvailable:
    def test_true_when_which_finds_it(self) -> None:
        assert systemctl_available(which=lambda name: "/usr/bin/systemctl") is True

    def test_false_when_which_does_not_find_it(self) -> None:
        assert systemctl_available(which=lambda name: None) is False


class TestSystemctlRunnerInjection:
    """No test here shells out to real systemctl -- a fake runner records the argv built."""

    def test_start_via_systemctl_builds_the_expected_argv(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(argv: list[str]) -> subprocess.CompletedProcess:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        result = start_via_systemctl(runner=fake_runner)

        assert calls == [["systemctl", "--user", "start", SYSTEMCTL_UNIT_NAME]]
        assert result.returncode == 0

    def test_stop_via_systemctl_builds_the_expected_argv(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(argv: list[str]) -> subprocess.CompletedProcess:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        result = stop_via_systemctl(runner=fake_runner)

        assert calls == [["systemctl", "--user", "stop", SYSTEMCTL_UNIT_NAME]]
        assert result.returncode == 0

    def test_start_via_systemctl_surfaces_a_nonzero_returncode(self) -> None:
        def failing_runner(argv: list[str]) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                argv, returncode=1, stdout="", stderr="unit not found"
            )

        result = start_via_systemctl(runner=failing_runner)
        assert result.returncode == 1
        assert result.stderr == "unit not found"
