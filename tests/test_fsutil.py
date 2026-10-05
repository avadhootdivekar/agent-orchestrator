"""`agent_orchestrator.fsutil` (E-Da5Tn9 T-8NQP8J; HLD 11.5, v2.1 security M6 / L6).

Covers `FileLock` (real spawn-process contention + injected-clock polling), `atomic_write_bytes`,
`remove_stale_temp_files`, and the fd-based private directory/file checks. `fsutil.os` is
replaced by a delegating proxy where a test must intercept `os.fstat` / `os.stat` / `os.chmod`,
so nothing is patched process-wide.
"""

from __future__ import annotations

import multiprocessing
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator import fsutil
from agent_orchestrator.fsutil import (
    FileLock,
    LockTimeoutError,
    UnsafePathError,
    atomic_write_bytes,
    check_private_file,
    ensure_private_dir,
    remove_stale_temp_files,
)

SPAWN = multiprocessing.get_context("spawn")
JOIN_TIMEOUT = 20.0
EUID = os.geteuid()
FOREIGN_UID = EUID + 4242


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def hold_lock(lock_path: str, ready: Any, release: Any) -> None:
    """Spawn target: hold the lock until told to release (or the parent dies)."""
    with FileLock(Path(lock_path), timeout=5.0):
        ready.set()
        release.wait(30.0)


class _OsProxy:
    """Delegates to the real ``os`` except for explicit overrides (patched per test, not global)."""

    def __init__(self, **overrides: Callable[..., Any]) -> None:
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(os, name)


@contextmanager
def umask(value: int) -> Iterator[None]:
    old = os.umask(value)
    try:
        yield
    finally:
        os.umask(old)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def forbid_chmod(monkeypatch: pytest.MonkeyPatch, **overrides: Callable[..., Any]) -> list[str]:
    """Make any path-based chmod from fsutil fail; returns the list of recorded fchmod modes."""
    fchmods: list[str] = []

    def no_chmod(*_a: Any, **_k: Any) -> None:
        raise AssertionError("path-based chmod is forbidden in fsutil")

    def spy_fchmod(fd: int, mode: int) -> None:
        fchmods.append(f"{mode:o}")
        os.fchmod(fd, mode)

    monkeypatch.setattr(
        fsutil, "os", _OsProxy(chmod=no_chmod, lchmod=no_chmod, fchmod=spy_fchmod, **overrides)
    )
    monkeypatch.setattr(Path, "chmod", no_chmod)
    return fchmods


# ---------------------------------------------------------------------------
# neutrality + constants
# ---------------------------------------------------------------------------


def test_constants_match_auth_constants() -> None:
    from agent_orchestrator.auth import constants

    assert fsutil.PRIVATE_DIR_MODE == constants.STORE_DIR_MODE
    assert fsutil.PRIVATE_FILE_MODE == constants.STORE_FILE_MODE
    assert fsutil.DEFAULT_LOCK_POLL_SECONDS == constants.LOCK_POLL_SECONDS


def test_fsutil_does_not_import_auth() -> None:
    code = (
        "import sys, agent_orchestrator.fsutil;"
        "bad = [m for m in sys.modules if m.startswith('agent_orchestrator.auth')];"
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


# ---------------------------------------------------------------------------
# FileLock
# ---------------------------------------------------------------------------


class TestFileLock:
    def test_second_process_times_out_then_lock_released_on_holder_exit(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / "x.lock"
        ready, release = SPAWN.Event(), SPAWN.Event()
        holder = SPAWN.Process(target=hold_lock, args=(str(lock_path), ready, release))
        holder.start()
        try:
            assert ready.wait(JOIN_TIMEOUT)
            started = time.monotonic()
            with pytest.raises(LockTimeoutError), FileLock(lock_path, timeout=0.2):
                pass
            assert time.monotonic() - started < 1.0
        finally:
            release.set()
            holder.join(timeout=JOIN_TIMEOUT)
            if holder.is_alive():  # pragma: no cover - cleanup on failure
                holder.terminate()
        assert holder.exitcode == 0
        with FileLock(lock_path, timeout=1.0):  # released when the holder exited
            pass

    def test_lock_is_released_when_holder_is_killed(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "x.lock"
        ready, release = SPAWN.Event(), SPAWN.Event()
        holder = SPAWN.Process(target=hold_lock, args=(str(lock_path), ready, release))
        holder.start()
        assert ready.wait(JOIN_TIMEOUT)
        holder.kill()
        holder.join(timeout=JOIN_TIMEOUT)
        assert holder.exitcode is not None and holder.exitcode != 0
        with FileLock(lock_path, timeout=1.0):
            pass

    def test_symlinked_lock_path_fails(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.write_text("keep")
        link = tmp_path / "x.lock"
        link.symlink_to(target)
        with pytest.raises(OSError), FileLock(link, timeout=0.1):
            pass
        assert target.read_text() == "keep"

    def test_polls_with_injected_clock_then_times_out(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "x.lock"
        now = [0.0]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        with FileLock(lock_path, timeout=5.0):  # same process, distinct fd: still contends
            with pytest.raises(LockTimeoutError) as err:
                with FileLock(
                    lock_path, timeout=0.3, poll=0.1, monotonic=lambda: now[0], sleep=sleep
                ):
                    pass
            assert str(lock_path) in str(err.value)
        assert sleeps == [0.1, 0.1, 0.1]

    def test_acquires_after_contention_clears(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "x.lock"
        first = FileLock(lock_path, timeout=1.0)
        first.__enter__()
        released = [False]

        def sleep(_s: float) -> None:
            if not released[0]:
                released[0] = True
                first.__exit__(None, None, None)

        with FileLock(lock_path, timeout=5.0, poll=0.0, sleep=sleep):
            assert released[0]

    def test_not_reentrant_and_released_on_exception(self, tmp_path: Path) -> None:
        lock = FileLock(tmp_path / "x.lock", timeout=0.1)
        with lock:
            with pytest.raises(RuntimeError):
                lock.__enter__()
        with pytest.raises(ValueError), FileLock(tmp_path / "x.lock", timeout=0.1):
            raise ValueError("boom")
        with FileLock(tmp_path / "x.lock", timeout=0.1):  # not left locked
            pass
        lock.__exit__(None, None, None)  # idempotent

    def test_missing_directory_is_a_plain_oserror(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError), FileLock(tmp_path / "nope" / "x.lock", timeout=0.1):
            pass

    def test_lock_file_is_private(self, tmp_path: Path) -> None:
        with umask(0o022), FileLock(tmp_path / "x.lock", timeout=0.1):
            pass
        assert mode_of(tmp_path / "x.lock") == 0o600


# ---------------------------------------------------------------------------
# atomic_write_bytes / remove_stale_temp_files
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    @pytest.mark.parametrize("mask", [0o022, 0o077, 0o002])
    def test_file_is_0600_under_any_umask(self, tmp_path: Path, mask: int) -> None:
        target = tmp_path / "f.json"
        with umask(mask):
            atomic_write_bytes(target, b"hello")
        assert target.read_bytes() == b"hello"
        assert mode_of(target) == 0o600

    def test_replaces_existing_and_leaves_no_temp(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        target.write_bytes(b"old")
        atomic_write_bytes(target, b"x" * 1_000_000)  # exercises the partial-write loop path
        assert target.read_bytes() == b"x" * 1_000_000
        assert [p.name for p in tmp_path.iterdir()] == ["f.json"]

    def test_replace_failure_keeps_original_and_cleans_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "f.json"
        target.write_bytes(b"original")

        def boom(*_a: Any, **_k: Any) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(fsutil, "os", _OsProxy(replace=boom))
        with pytest.raises(OSError, match="replace failed"):
            atomic_write_bytes(target, b"new")
        assert target.read_bytes() == b"original"
        assert [p.name for p in tmp_path.iterdir()] == ["f.json"]

    def test_preplanted_symlink_at_temp_name_fails_without_touching_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "f.json"
        target.write_bytes(b"original")
        victim = tmp_path / "victim"
        victim.write_bytes(b"victim-data")
        monkeypatch.setattr(fsutil.secrets, "token_hex", lambda _n: "deadbeefdeadbeef")
        (tmp_path / ".f.json.deadbeefdeadbeef.tmp").symlink_to(victim)
        with pytest.raises(OSError):
            atomic_write_bytes(target, b"new")
        assert victim.read_bytes() == b"victim-data"
        assert target.read_bytes() == b"original"

    def test_directory_fsync_failure_surfaces_after_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "f.json"
        calls: list[int] = []

        def failing_fsync(fd: int) -> None:
            calls.append(fd)
            if len(calls) == 2:  # 1st = the file, 2nd = the directory
                raise OSError("dir fsync failed")
            os.fsync(fd)

        monkeypatch.setattr(fsutil, "os", _OsProxy(fsync=failing_fsync))
        with pytest.raises(OSError, match="dir fsync failed"):
            atomic_write_bytes(target, b"data")
        assert target.read_bytes() == b"data"  # the rename had already happened

    def test_remove_stale_temp_files_only_matching_siblings(self, tmp_path: Path) -> None:
        target = tmp_path / "users.json"
        target.write_text("keep")
        stale = [tmp_path / ".users.json.aaaa.tmp", tmp_path / ".users.json.bbbb.tmp"]
        for p in stale:
            p.write_text("old secrets")
        keep = [
            tmp_path / ".other.json.aaaa.tmp",  # another file's temp
            tmp_path / "users.json.aaaa.tmp",  # no leading dot
            tmp_path / ".users.json.tmp",  # no token between
            tmp_path / ".users.json.aaaa",  # wrong suffix
        ]
        for p in keep:
            p.write_text("keep")
        victim = tmp_path / "victim"
        victim.write_text("victim")
        (tmp_path / ".users.json.link.tmp").symlink_to(victim)  # removed itself, never its target
        assert remove_stale_temp_files(target) == 3
        assert not any(p.exists() for p in stale)
        assert all(p.exists() for p in keep)
        assert victim.read_text() == "victim" and target.read_text() == "keep"

    def test_remove_stale_temp_files_missing_directory(self, tmp_path: Path) -> None:
        assert remove_stale_temp_files(tmp_path / "gone" / "users.json") == 0

    def test_remove_stale_temp_files_skips_undeletable_entries(self, tmp_path: Path) -> None:
        (tmp_path / ".users.json.dir.tmp").mkdir()  # unlink fails on a directory
        assert remove_stale_temp_files(tmp_path / "users.json") == 0


# ---------------------------------------------------------------------------
# ensure_private_dir
# ---------------------------------------------------------------------------


class TestEnsurePrivateDir:
    @pytest.mark.parametrize("mask", [0o022, 0o077, 0o002])
    def test_create_makes_every_component_0700_with_mkdir_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: int
    ) -> None:
        calls: list[tuple[Path, int | None]] = []

        def spy_mkdir(path: Any, mode: int = 0o777, **_k: Any) -> None:
            calls.append((Path(path), mode))
            os.mkdir(path, mode)

        def no_path_mkdir(*_a: Any, **_k: Any) -> None:
            raise AssertionError("Path.mkdir must not be used")

        fchmods = forbid_chmod(monkeypatch, mkdir=spy_mkdir)
        monkeypatch.setattr(Path, "mkdir", no_path_mkdir)
        leaf = tmp_path / "a" / "b" / "c"
        with umask(mask):
            notices = ensure_private_dir(leaf, create=True, fix=False)
        assert notices == []
        assert [p for p, _m in calls] == [tmp_path / "a", tmp_path / "a" / "b", leaf]
        assert all(m == 0o700 for _p, m in calls)
        for p in (tmp_path / "a", tmp_path / "a" / "b", leaf):
            assert mode_of(p) == 0o700
        assert fchmods == ["700"]  # once, on the created leaf

    def test_losing_the_mkdir_race_is_tolerated_and_the_directory_verified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        leaf = tmp_path / "raced"

        def racing_mkdir(path: Any, mode: int = 0o777, **_k: Any) -> None:
            os.mkdir(path, 0o755)  # another creator wins with a loose mode
            raise FileExistsError(path)

        monkeypatch.setattr(fsutil, "os", _OsProxy(mkdir=racing_mkdir))
        with pytest.raises(UnsafePathError, match="chmod 700"):
            ensure_private_dir(leaf, create=True, fix=False)

    def test_existing_private_dir_is_ok_and_idempotent(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        d.mkdir(mode=0o700)
        assert ensure_private_dir(d, create=True, fix=False) == []
        assert ensure_private_dir(d, create=False, fix=False) == []

    def test_missing_without_create_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ensure_private_dir(tmp_path / "nope", create=False, fix=False)

    def test_loose_mode_without_fix_raises_with_exact_chmod(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        d.mkdir()
        os.chmod(d, 0o755)
        with pytest.raises(UnsafePathError) as err:
            ensure_private_dir(d, create=False, fix=False)
        assert f"chmod 700 {d}" in str(err.value)
        assert mode_of(d) == 0o755  # untouched

    def test_loose_mode_with_fix_uses_fchmod_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = tmp_path / "d"
        d.mkdir()
        os.chmod(d, 0o755)
        fchmods = forbid_chmod(monkeypatch)
        notices = ensure_private_dir(d, create=False, fix=True)
        assert fchmods == ["700"]
        assert mode_of(d) == 0o700
        assert len(notices) == 1 and "0700" in notices[0]

    def test_symlinked_directory_raises(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(UnsafePathError, match="symlink"):
            ensure_private_dir(link, create=True, fix=True)

    def test_regular_file_in_place_of_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "f"
        f.write_text("x")
        with pytest.raises(UnsafePathError):
            ensure_private_dir(f, create=False, fix=False)

    def test_other_oserror_on_open_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = tmp_path / "d"
        d.mkdir(mode=0o700)
        d.chmod(0)
        try:
            if EUID == 0:
                pytest.skip("root bypasses directory permissions")
            # opening a 0000 directory read-only: EACCES is not a symlink problem
            with pytest.raises(PermissionError):
                ensure_private_dir(d, create=False, fix=False)
        finally:
            d.chmod(0o700)

    def test_foreign_owner_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        d = tmp_path / "d"
        d.mkdir(mode=0o700)

        def fake_fstat(fd: int) -> Any:
            real = os.fstat(fd)
            return SimpleNamespace(st_uid=FOREIGN_UID, st_mode=real.st_mode)

        monkeypatch.setattr(fsutil, "os", _OsProxy(fstat=fake_fstat))
        with pytest.raises(UnsafePathError, match=str(FOREIGN_UID)):
            ensure_private_dir(d, create=False, fix=True)


class TestParentRule:
    """HLD 11.5 / security L6: the RESOLVED parent is judged by os.stat."""

    def _store(self, parent: Path) -> Path:
        return parent / "store"

    def test_other_writable_parent_raises(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        os.chmod(parent, 0o757)
        with pytest.raises(UnsafePathError) as err:
            ensure_private_dir(self._store(parent), create=True, fix=False)
        assert f"chmod o-w {parent}" in str(err.value)

    def test_group_writable_parent_owned_by_another_user_raises_naming_owner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path / "p"
        parent.mkdir()
        os.chmod(parent, 0o775)
        real_stat = os.stat
        resolved = parent.resolve()

        def fake_stat(path: Any, *a: Any, **k: Any) -> Any:
            st = real_stat(path, *a, **k)
            if Path(path) == resolved:
                return SimpleNamespace(st_uid=FOREIGN_UID, st_mode=st.st_mode)
            return st

        monkeypatch.setattr(fsutil, "os", _OsProxy(stat=fake_stat))
        with pytest.raises(UnsafePathError) as err:
            ensure_private_dir(self._store(parent), create=True, fix=False)
        assert str(FOREIGN_UID) in str(err.value)
        assert f"chmod g-w {parent}" in str(err.value)

    def test_group_writable_parent_owned_by_euid_warns_under_umask_002(
        self, tmp_path: Path
    ) -> None:
        with umask(0o002):
            parent = tmp_path / "home"
            parent.mkdir()  # 0775: a Debian/Ubuntu-style home
            notices = ensure_private_dir(self._store(parent), create=True, fix=False)
        assert mode_of(parent) == 0o775
        assert len(notices) == 1
        assert f"{parent} is group-writable; run chmod g-w {parent}" == notices[0]
        assert mode_of(self._store(parent)) == 0o700

    def test_symlinked_parent_is_judged_at_its_target(self, tmp_path: Path) -> None:
        real_cfg = tmp_path / "real_cfg"
        real_cfg.mkdir()
        cfg = tmp_path / "cfg"
        cfg.symlink_to(real_cfg)
        store = cfg / "ao"
        os.chmod(real_cfg, 0o755)
        assert ensure_private_dir(store, create=True, fix=False) == []
        os.chmod(real_cfg, 0o775)
        notices = ensure_private_dir(store, create=False, fix=False)
        assert notices == [f"{real_cfg} is group-writable; run chmod g-w {real_cfg}"]
        os.chmod(real_cfg, 0o777)
        with pytest.raises(UnsafePathError) as err:
            ensure_private_dir(store, create=False, fix=False)
        assert str(real_cfg) in str(err.value)

    def test_root_owned_sticky_1777_parent_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path / "tmpish"
        parent.mkdir()
        os.chmod(parent, 0o755)
        real_stat = os.stat
        resolved = parent.resolve()

        def fake_stat(path: Any, *a: Any, **k: Any) -> Any:
            st = real_stat(path, *a, **k)
            if Path(path) == resolved:
                return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | stat.S_ISVTX | 0o777)
            return st

        monkeypatch.setattr(fsutil, "os", _OsProxy(stat=fake_stat))
        assert ensure_private_dir(self._store(parent), create=True, fix=False) == []

    def test_private_parent_gives_no_notice(self, tmp_path: Path) -> None:
        parent = tmp_path / "p"
        parent.mkdir(mode=0o700)
        assert ensure_private_dir(self._store(parent), create=True, fix=False) == []


# ---------------------------------------------------------------------------
# check_private_file
# ---------------------------------------------------------------------------


class TestCheckPrivateFile:
    def test_private_file_ok(self, tmp_path: Path) -> None:
        f = tmp_path / "users.json"
        f.write_text("{}")
        os.chmod(f, 0o600)
        assert check_private_file(f, fix=False) == []

    def test_loose_mode_raises_with_exact_chmod_and_is_untouched(self, tmp_path: Path) -> None:
        f = tmp_path / "users.json"
        f.write_text("{}")
        os.chmod(f, 0o644)
        with pytest.raises(UnsafePathError) as err:
            check_private_file(f, fix=False)
        assert f"chmod 600 {f}" in str(err.value)
        assert mode_of(f) == 0o644

    def test_fix_uses_fchmod_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "users.json"
        f.write_text("{}")
        os.chmod(f, 0o644)
        fchmods = forbid_chmod(monkeypatch)
        notices = check_private_file(f, fix=True)
        assert fchmods == ["600"]
        assert mode_of(f) == 0o600
        assert len(notices) == 1 and "0600" in notices[0]

    def test_symlink_raises_and_target_is_untouched(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.write_text("x")
        os.chmod(target, 0o644)
        link = tmp_path / "users.json"
        link.symlink_to(target)
        with pytest.raises(UnsafePathError, match="symlink"):
            check_private_file(link, fix=True)
        assert mode_of(target) == 0o644

    def test_directory_and_fifo_are_not_regular_files(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePathError, match="regular file"):
            check_private_file(tmp_path, fix=False)
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo, 0o600)
        with pytest.raises(UnsafePathError, match="regular file"):
            check_private_file(fifo, fix=False)

    def test_foreign_owner_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "users.json"
        f.write_text("{}")
        os.chmod(f, 0o600)

        def fake_fstat(fd: int) -> Any:
            real = os.fstat(fd)
            return SimpleNamespace(st_uid=FOREIGN_UID, st_mode=real.st_mode)

        monkeypatch.setattr(fsutil, "os", _OsProxy(fstat=fake_fstat))
        with pytest.raises(UnsafePathError, match=str(FOREIGN_UID)):
            check_private_file(f, fix=False)

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            check_private_file(tmp_path / "nope", fix=False)

    def test_owner_name_falls_back_to_uid_for_unknown_users(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_user(_uid: int) -> Any:
            raise KeyError

        monkeypatch.setattr(fsutil.pwd, "getpwuid", no_user)
        assert fsutil._owner_name(7) == "uid 7"
