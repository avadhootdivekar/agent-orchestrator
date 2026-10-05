"""T-Hd4wQ2: ``default_denied_paths`` and ``entry_is_denied`` (HLD 11.4, security L7)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_orchestrator.auth import paths
from agent_orchestrator.auth.constants import SERVICE_ENV_RELATIVE_PATH
from agent_orchestrator.auth.paths import default_denied_paths, entry_is_denied


def _entries(directory: Path) -> dict[str, os.DirEntry[str]]:
    with os.scandir(directory) as it:
        return {e.name: e for e in it}


class TestDefaultDeniedPaths:
    @pytest.fixture(autouse=True)
    def home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        return home

    def test_six_sources_resolved_and_in_order(self, tmp_path: Path, home: Path) -> None:
        env = {
            "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "AO_AUTH_DIR": str(tmp_path / "store"),
            "AO_AUTH_STATE_DIR": str(tmp_path / "ostate"),
        }
        assert default_denied_paths(env) == (
            (tmp_path / "cfg" / "ao" / "auth").resolve(),
            (tmp_path / "state" / "ao" / "auth").resolve(),
            (tmp_path / "store").resolve(),
            (tmp_path / "ostate").resolve(),
            (home / SERVICE_ENV_RELATIVE_PATH).resolve(),
            (tmp_path / "cfg" / "ao" / "service.env").resolve(),
        )

    def test_overrides_and_xdg_service_env_only_when_set(self, home: Path) -> None:
        assert default_denied_paths({}) == (
            (home / ".config" / "ao" / "auth").resolve(),
            (home / ".local" / "state" / "ao" / "auth").resolve(),
            (home / ".config" / "ao" / "service.env").resolve(),
        )

    def test_empty_override_is_treated_as_unset(self) -> None:
        assert len(default_denied_paths({"AO_AUTH_DIR": "", "XDG_CONFIG_HOME": ""})) == 3

    def test_service_env_is_built_from_the_constant(self, home: Path) -> None:
        assert default_denied_paths({})[-1] == (home / SERVICE_ENV_RELATIVE_PATH).resolve()

    def test_deduplicates_when_xdg_config_home_is_the_home_config(self, home: Path) -> None:
        env = {"XDG_CONFIG_HOME": str(home / ".config")}
        assert len(default_denied_paths(env)) == 3

    def test_deduplicates_when_ao_auth_dir_is_the_default(self, home: Path) -> None:
        env = {"AO_AUTH_DIR": str(home / ".config" / "ao" / "auth")}
        assert len(default_denied_paths(env)) == 3

    def test_expands_a_tilde_override(self, home: Path) -> None:
        assert (home / "mystore").resolve() in default_denied_paths({"AO_AUTH_DIR": "~/mystore"})

    def test_resolves_symlinked_locations(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        env = {"AO_AUTH_DIR": str(tmp_path / "link" / "s")}
        assert (real / "s").resolve() in default_denied_paths(env)

    def test_env_none_reads_os_environ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AO_AUTH_DIR", str(tmp_path / "live"))
        assert (tmp_path / "live").resolve() in default_denied_paths()


class TestEntryIsDenied:
    def test_regular_entries_never_call_resolve(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for i in range(100):
            (tmp_path / f"f{i}").write_text("x")
        denied = [(tmp_path / "f5").resolve()]
        entries = list(_entries(tmp_path).values())
        parent = tmp_path.resolve()
        calls: list[Path] = []
        real_resolve = Path.resolve

        def counting(self: Path, strict: bool = False) -> Path:
            calls.append(self)
            return real_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", counting)
        results = [entry_is_denied(parent, e, denied) for e in entries]
        assert calls == []
        assert sum(results) == 1

    def test_directory_and_nested_denial(self, tmp_path: Path) -> None:
        (tmp_path / "store").mkdir()
        (tmp_path / "other").mkdir()
        denied = [(tmp_path / "store").resolve()]
        entries = _entries(tmp_path)
        assert entry_is_denied(tmp_path.resolve(), entries["store"], denied)
        assert not entry_is_denied(tmp_path.resolve(), entries["other"], denied)
        # an entry *inside* a denied directory is denied too
        (tmp_path / "store" / "users.json").write_text("{}")
        inner = _entries(tmp_path / "store")["users.json"]
        assert entry_is_denied((tmp_path / "store").resolve(), inner, denied)

    def test_symlink_into_the_store_is_denied(self, tmp_path: Path) -> None:
        (tmp_path / "store").mkdir()
        (tmp_path / "store" / "users.json").write_text("{}")
        (tmp_path / "ws").mkdir()
        (tmp_path / "ws" / "dirlink").symlink_to(tmp_path / "store")
        (tmp_path / "ws" / "filelink").symlink_to(tmp_path / "store" / "users.json")
        (tmp_path / "ws" / "fine").symlink_to(tmp_path / "ws")
        denied = [(tmp_path / "store").resolve()]
        entries = _entries(tmp_path / "ws")
        parent = (tmp_path / "ws").resolve()
        assert entry_is_denied(parent, entries["dirlink"], denied)
        assert entry_is_denied(parent, entries["filelink"], denied)
        assert not entry_is_denied(parent, entries["fine"], denied)

    def test_shared_prefix_sibling_is_not_denied(self, tmp_path: Path) -> None:
        (tmp_path / "b").mkdir()
        (tmp_path / "bc").mkdir()
        denied = [(tmp_path / "b").resolve()]
        entries = _entries(tmp_path)
        assert entry_is_denied(tmp_path.resolve(), entries["b"], denied)
        assert not entry_is_denied(tmp_path.resolve(), entries["bc"], denied)

    def test_denied_file_only_matches_that_exact_file(self, tmp_path: Path) -> None:
        (tmp_path / "service.env").write_text("K=V")
        (tmp_path / "service.env.bak").write_text("K=V")
        denied = [(tmp_path / "service.env").resolve()]
        entries = _entries(tmp_path)
        assert entry_is_denied(tmp_path.resolve(), entries["service.env"], denied)
        assert not entry_is_denied(tmp_path.resolve(), entries["service.env.bak"], denied)

    def test_empty_denied_list_denies_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_text("x")
        assert not entry_is_denied(tmp_path.resolve(), _entries(tmp_path)["a"], [])

    def test_symlink_loop_is_not_denied_and_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "loop").symlink_to(tmp_path / "loop")
        denied = [(tmp_path / "store").resolve()]
        assert not entry_is_denied(tmp_path.resolve(), _entries(tmp_path)["loop"], denied)

    def test_dangling_symlink_into_denied_path_is_denied(self, tmp_path: Path) -> None:
        (tmp_path / "ws").mkdir()
        (tmp_path / "ws" / "ghost").symlink_to(tmp_path / "store" / "users.json")  # not created
        denied = [(tmp_path / "store").resolve()]
        ghost = _entries(tmp_path / "ws")["ghost"]
        assert entry_is_denied((tmp_path / "ws").resolve(), ghost, denied)


def test_is_within_still_resolves_symlinks(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert paths.is_within(tmp_path / "link" / "x", tmp_path / "real")
