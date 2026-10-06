"""Unit tests for `agent_orchestrator.xdg.resolve_state_dir` (E-Wk9Tz3 T-Gt4Pw8).

Mirrors `tests/service/test_paths.py`'s style for the same precedence shape
(`service/paths.py::default_state_dir`), generalized here to arbitrary
override-env/xdg-subdir/default-subdir arguments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.xdg import resolve_config_dir, resolve_state_dir


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


class TestResolveConfigDir:
    """E-Da5Tn9 T-8NQP8J (v2.1 signature, cross-epic row X1): one implementation, both epics."""

    @pytest.fixture(autouse=True)
    def _no_xdg_config_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("AO_AUTH_DIR", raising=False)

    def test_override_env_wins(self) -> None:
        env = {"AO_AUTH_DIR": "/a", "XDG_CONFIG_HOME": "/x"}
        assert resolve_config_dir("AO_AUTH_DIR", "ao/auth", "ao/auth", environ=env) == Path("/a")

    def test_empty_override_is_ignored(self) -> None:
        env = {"AO_AUTH_DIR": "", "XDG_CONFIG_HOME": "/x"}
        assert resolve_config_dir("AO_AUTH_DIR", "ao/auth", "ao/auth", environ=env) == Path(
            "/x/ao/auth"
        )

    def test_xdg_config_home(self) -> None:
        env = {"XDG_CONFIG_HOME": "/x"}
        assert resolve_config_dir("AO_AUTH_DIR", "ao/auth", "ao/auth", environ=env) == Path(
            "/x/ao/auth"
        )

    def test_home_fallback_with_explicit_home(self) -> None:
        got = resolve_config_dir("AO_AUTH_DIR", "ao/auth", "ao/auth", environ={}, home=Path("/h"))
        assert got == Path("/h/.config/ao/auth")

    def test_no_home_uses_path_home(self, tmp_path: Path) -> None:
        got = resolve_config_dir("AO_AUTH_DIR", "ao/auth", "ao/auth", environ={})
        assert got == tmp_path / "home" / ".config" / "ao" / "auth"

    def test_none_override_env_ignores_any_override_variable(self) -> None:
        env = {"AO_AUTH_DIR": "/a", "XDG_CONFIG_HOME": "/x"}
        assert resolve_config_dir(None, "ao/auth", "ao/auth", environ=env) == Path("/x/ao/auth")

    def test_distinct_xdg_and_default_subdirs_each_used_in_their_branch(self) -> None:
        assert resolve_config_dir(
            None, "ao/xdg-sub", "ao/home-sub", environ={"XDG_CONFIG_HOME": "/x"}
        ) == Path("/x/ao/xdg-sub")
        assert resolve_config_dir(
            None, "ao/xdg-sub", "ao/home-sub", environ={}, home=Path("/h")
        ) == Path("/h/.config/ao/home-sub")

    def test_environ_none_reads_os_environ_at_call_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "first"))
        assert resolve_config_dir(None, "ao", "ao") == tmp_path / "first" / "ao"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "second"))
        assert resolve_config_dir(None, "ao", "ao") == tmp_path / "second" / "ao"

    def test_passed_environ_is_the_only_environment_consulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/from-os-environ")
        got = resolve_config_dir(None, "ao", "ao", environ={}, home=Path("/h"))
        assert got == Path("/h/.config/ao")


class TestResolveStateDirExtended:
    """The additive keyword-only ``environ`` / ``home`` and ``override_env=None``."""

    def test_xdg_state_home_from_environ(self) -> None:
        got = resolve_state_dir(None, "ao/auth", "ao/auth", environ={"XDG_STATE_HOME": "/s"})
        assert got == Path("/s/ao/auth")

    def test_home_is_honoured(self) -> None:
        got = resolve_state_dir(None, "ao/auth", "ao/auth", environ={}, home=Path("/h"))
        assert got == Path("/h/.local/state/ao/auth")

    def test_none_override_ignores_override_variable(self) -> None:
        env = {"AO_AUTH_STATE_DIR": "/o", "XDG_STATE_HOME": "/s"}
        assert resolve_state_dir(None, "ao/auth", "ao/auth", environ=env) == Path("/s/ao/auth")

    def test_override_from_environ(self) -> None:
        env = {"AO_AUTH_STATE_DIR": "/o"}
        got = resolve_state_dir("AO_AUTH_STATE_DIR", "ao/auth", "ao/auth", environ=env)
        assert got == Path("/o")
