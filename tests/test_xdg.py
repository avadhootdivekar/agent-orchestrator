"""Unit tests for `agent_orchestrator.xdg.resolve_state_dir` (E-Wk9Tz3 T-Gt4Pw8).

Mirrors `tests/service/test_paths.py`'s style for the same precedence shape
(`service/paths.py::default_state_dir`), generalized here to arbitrary
override-env/xdg-subdir/default-subdir arguments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.xdg import resolve_state_dir


@pytest.fixture(autouse=True)
def _no_xdg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class TestResolveStateDir:
    def test_override_env_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        override = tmp_path / "custom-state"
        monkeypatch.setenv("AO_STATE_DIR", str(override))
        result = resolve_state_dir("AO_STATE_DIR", "ao", "ao")
        assert result == override

    def test_xdg_state_home_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        result = resolve_state_dir("AO_STATE_DIR", "ao", "ao")
        assert result == tmp_path / "xdg-state" / "ao"

    def test_xdg_subdir_and_default_subdir_can_differ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        result = resolve_state_dir("AO_STATE_DIR", "ao/service", "ao/other")
        assert result == tmp_path / "xdg-state" / "ao" / "service"

    def test_home_fallback_when_nothing_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        result = resolve_state_dir("AO_STATE_DIR", "ao", "ao")
        assert result == tmp_path / "home" / ".local" / "state" / "ao"

    def test_different_override_env_name_is_honored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AO_STATE_DIR", raising=False)
        override = tmp_path / "svc-state"
        monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(override))
        result = resolve_state_dir("AO_SERVICE_STATE_DIR", "ao/service", "ao/service")
        assert result == override
