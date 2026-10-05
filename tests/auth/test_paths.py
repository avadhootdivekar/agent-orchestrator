"""T-8NQP8J: ``auth/paths.py`` (HLD 11.4): default dirs, ``StorePaths``, ``is_within`` and the
permission gates that map ``fsutil.UnsafePathError`` to ``UnsafePermissionsError``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_orchestrator.auth import constants
from agent_orchestrator.auth.errors import AuthConfigError, UnsafePermissionsError
from agent_orchestrator.auth.paths import (
    AO_AUTH_DIR_ENV,
    AO_AUTH_STATE_DIR_ENV,
    StorePaths,
    check_private_paths,
    check_state_dir,
    is_within,
    xdg_default_state_dir,
    xdg_default_store_dir,
)


class TestDefaultDirs:
    def test_env_names(self) -> None:
        assert (AO_AUTH_DIR_ENV, AO_AUTH_STATE_DIR_ENV) == ("AO_AUTH_DIR", "AO_AUTH_STATE_DIR")

    def test_store_dir_ignores_ao_auth_dir_and_reads_only_the_passed_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/from-os-environ")
        env = {"AO_AUTH_DIR": "/override", "XDG_CONFIG_HOME": "/x"}
        assert xdg_default_store_dir(env) == Path("/x/ao/auth")

    def test_state_dir_ignores_ao_auth_state_dir_and_reads_only_the_passed_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", "/from-os-environ")
        env = {"AO_AUTH_STATE_DIR": "/override", "XDG_STATE_HOME": "/s"}
        assert xdg_default_state_dir(env) == Path("/s/ao/auth")

    def test_home_fallbacks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert xdg_default_store_dir({}) == tmp_path / ".config" / "ao" / "auth"
        assert xdg_default_state_dir({}) == tmp_path / ".local" / "state" / "ao" / "auth"

    def test_env_none_reads_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/live-config")
        monkeypatch.setenv("XDG_STATE_HOME", "/live-state")
        assert xdg_default_store_dir() == Path("/live-config/ao/auth")
        assert xdg_default_state_dir() == Path("/live-state/ao/auth")


class TestStorePaths:
    def test_at_uses_the_named_files(self) -> None:
        sp = StorePaths.at(Path("/s"), Path("/t"))
        assert sp.store_dir == Path("/s") and sp.state_dir == Path("/t")
        assert sp.users_file == Path("/s") / constants.USERS_FILENAME == Path("/s/users.json")
        assert sp.users_lock == Path("/s/users.lock")
        assert sp.lockouts_file == Path("/t/lockouts.json")
        assert sp.lockouts_lock == Path("/t/lockouts.lock")
        assert sp.audit_file == Path("/t/audit.jsonl")
        assert sp.audit_lock == Path("/t/audit.lock")

    def test_frozen(self) -> None:
        sp = StorePaths.at(Path("/s"), Path("/t"))
        with pytest.raises(AttributeError):
            sp.store_dir = Path("/other")  # type: ignore[misc]


class TestIsWithin:
    def test_same_path_and_child(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b").mkdir(parents=True)
        assert is_within(tmp_path / "a", tmp_path / "a")
        assert is_within(tmp_path / "a" / "b", tmp_path / "a")
        assert is_within(tmp_path / "a" / "missing" / "deeper", tmp_path / "a")

    def test_shared_prefix_sibling_is_outside(self, tmp_path: Path) -> None:
        (tmp_path / "b").mkdir()
        (tmp_path / "bc").mkdir()
        assert not is_within(tmp_path / "bc", tmp_path / "b")
        assert not is_within(Path("/a/bc"), Path("/a/b"))
        assert not is_within(tmp_path, tmp_path / "b")  # the parent is not within the child

    def test_symlink_is_resolved_first(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        assert is_within(tmp_path / "link" / "x", real)

    def test_a_file_root_matches_only_itself(self, tmp_path: Path) -> None:
        f = tmp_path / "service.env"
        f.write_text("x")
        assert is_within(f, f)
        assert not is_within(tmp_path / "service.env.bak", f)

    def test_normcase_is_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Model a case-insensitive platform: normcase lower-cases.
        monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())
        assert is_within(Path("/Tmp/Foo/Bar"), Path("/tmp/foo"))


def _private_store(tmp_path: Path) -> tuple[Path, Path]:
    store_dir = tmp_path / "store"
    store_dir.mkdir(mode=0o700)
    users = store_dir / "users.json"
    users.write_text("{}")
    os.chmod(users, 0o600)
    return store_dir, users


class TestCheckPrivatePaths:
    def test_private_store_has_no_notices(self, tmp_path: Path) -> None:
        store_dir, users = _private_store(tmp_path)
        assert check_private_paths(store_dir, users) == []

    def test_loose_dir_raises_unsafe_permissions_with_chmod(self, tmp_path: Path) -> None:
        store_dir, users = _private_store(tmp_path)
        os.chmod(store_dir, 0o755)
        with pytest.raises(UnsafePermissionsError, match=f"chmod 700 {store_dir}") as err:
            check_private_paths(store_dir, users)
        assert isinstance(err.value, AuthConfigError)
        assert err.value.__cause__ is not None  # the neutral UnsafePathError is chained

    def test_loose_file_raises(self, tmp_path: Path) -> None:
        store_dir, users = _private_store(tmp_path)
        os.chmod(users, 0o644)
        with pytest.raises(UnsafePermissionsError, match=f"chmod 600 {users}"):
            check_private_paths(store_dir, users)

    def test_symlinked_users_file_raises(self, tmp_path: Path) -> None:
        store_dir, users = _private_store(tmp_path)
        real = tmp_path / "elsewhere.json"
        users.rename(real)
        users.symlink_to(real)
        with pytest.raises(UnsafePermissionsError, match="symlink"):
            check_private_paths(store_dir, users)

    def test_symlinked_store_dir_raises(self, tmp_path: Path) -> None:
        store_dir, _users = _private_store(tmp_path)
        link = tmp_path / "link"
        link.symlink_to(store_dir)
        with pytest.raises(UnsafePermissionsError):
            check_private_paths(link, link / "users.json")

    def test_unsafe_parent_raises(self, tmp_path: Path) -> None:
        store_dir, users = _private_store(tmp_path)
        os.chmod(tmp_path, 0o777)
        try:
            with pytest.raises(UnsafePermissionsError, match="chmod o-w"):
                check_private_paths(store_dir, users)
        finally:
            os.chmod(tmp_path, 0o700)

    def test_group_writable_euid_parent_is_returned_as_a_warning(self, tmp_path: Path) -> None:
        store_dir, users = _private_store(tmp_path)
        os.chmod(tmp_path, 0o775)
        try:
            notices = check_private_paths(store_dir, users)
        finally:
            os.chmod(tmp_path, 0o700)
        assert len(notices) == 1 and "chmod g-w" in notices[0]

    def test_missing_directory_or_file_is_not_a_permission_problem(self, tmp_path: Path) -> None:
        assert check_private_paths(tmp_path / "nope", tmp_path / "nope" / "users.json") == []
        store_dir = tmp_path / "empty"
        store_dir.mkdir(mode=0o700)
        assert check_private_paths(store_dir, store_dir / "users.json") == []


class TestCheckStateDir:
    def test_creates_a_missing_state_dir_0700_without_fixing_anything(self, tmp_path: Path) -> None:
        state = tmp_path / "x" / "state"
        assert check_state_dir(state) == []
        assert os.stat(state).st_mode & 0o777 == 0o700

    def test_loose_state_dir_is_refused_not_fixed(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        os.chmod(state, 0o755)
        with pytest.raises(UnsafePermissionsError, match=f"chmod 700 {state}"):
            check_state_dir(state)
        assert os.stat(state).st_mode & 0o777 == 0o755

    def test_symlinked_state_dir_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        link = tmp_path / "state"
        link.symlink_to(real)
        with pytest.raises(UnsafePermissionsError):
            check_state_dir(link)

    def test_group_writable_parent_warns(self, tmp_path: Path) -> None:
        os.chmod(tmp_path, 0o775)
        try:
            notices = check_state_dir(tmp_path / "state")
        finally:
            os.chmod(tmp_path, 0o700)
        assert len(notices) == 1 and "chmod g-w" in notices[0]


class TestOsErrorsBecomeConfigErrors:
    """T-2wE08U M-code-1: a PermissionError must exit 78 (AuthConfigError), never escape raw."""

    def test_check_state_dir_under_a_read_only_parent(self, tmp_path: Path) -> None:
        parent = tmp_path / "ro"
        parent.mkdir(mode=0o700)
        parent.chmod(0o500)
        try:
            with pytest.raises(UnsafePermissionsError, match="Permission denied") as info:
                check_state_dir(parent / "state")
        finally:
            parent.chmod(0o700)
        assert isinstance(info.value, AuthConfigError)
        assert str(parent / "state") in str(info.value)

    def test_check_private_paths_when_the_store_cannot_be_inspected(self, tmp_path: Path) -> None:
        outer = tmp_path / "locked"
        store_dir = outer / "store"
        store_dir.mkdir(parents=True, mode=0o700)
        outer.chmod(0o000)  # stat() of anything inside raises PermissionError
        try:
            with pytest.raises(UnsafePermissionsError):
                check_private_paths(store_dir, store_dir / "users.json")
        finally:
            outer.chmod(0o700)
