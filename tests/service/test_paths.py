"""Unit tests for `service/paths.py`'s resolution precedence (coverage top-up).

The rest of the service suite always sets `AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR`, so
the XDG and home fallback branches were untested. `Path.home()` follows `HOME` on POSIX,
so redirecting it keeps every branch off the real `~`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.service.paths import default_registry_path, default_state_dir


@pytest.fixture(autouse=True)
def _no_service_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_SERVICE_CONFIG", raising=False)
    monkeypatch.delenv("AO_SERVICE_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class TestDefaultRegistryPath:
    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        override = tmp_path / "custom" / "svc.yaml"
        monkeypatch.setenv("AO_SERVICE_CONFIG", str(override))
        assert default_registry_path() == override

    def test_xdg_config_home_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        assert default_registry_path() == tmp_path / "xdg-config" / "ao" / "service.yaml"

    def test_home_fallback_when_nothing_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert default_registry_path() == tmp_path / "home" / ".config" / "ao" / "service.yaml"


class TestDefaultStateDir:
    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path / "state-here"))
        assert default_state_dir() == tmp_path / "state-here"

    def test_xdg_state_home_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        assert default_state_dir() == tmp_path / "xdg-state" / "ao" / "service"

    def test_home_fallback_when_nothing_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert default_state_dir() == tmp_path / "home" / ".local" / "state" / "ao" / "service"
