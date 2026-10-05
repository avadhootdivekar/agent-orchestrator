"""AC-3 (U-IO1..IO7): safeio against symlinks, FIFOs, bounds, ownership and chain checks.

FIFO tests run in a thread joined with a timeout so a regression cannot hang the suite. Permission
errors are simulated by patching os.open (CI may run as root, where EACCES never happens).
"""

from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import CACHE_DIR_MODE, SENSITIVE_PATH_COMPONENTS
from agent_orchestrator.cache.safeio import (
    NotRegularFileError,
    SafeIOError,
    TooLargeError,
    UnsafePathError,
)

FIFO_TIMEOUT_SECONDS = 5

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks/uid are POSIX-only")


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


def _run_with_timeout(fn: Any, timeout: float = FIFO_TIMEOUT_SECONDS) -> Any:
    """Run fn in a daemon thread; fail (not hang) if it does not return within *timeout*."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "call blocked (FIFO?)"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def test_error_hierarchy() -> None:
    for sub in (NotRegularFileError, TooLargeError, UnsafePathError):
        assert issubclass(sub, SafeIOError)
    assert issubclass(SafeIOError, Exception)


# ---------------------------------------------------------------- open_regular_read (U-IO1)
def test_open_regular_read_returns_fd_for_regular_file(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b"hello")
    fd = safeio.open_regular_read(str(p))
    try:
        assert os.read(fd, 10) == b"hello"
    finally:
        os.close(fd)


def test_open_regular_read_missing_passes_through(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        safeio.open_regular_read(str(tmp_path / "nope"))


@needs_posix
def test_open_regular_read_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "t"
    target.write_text("x")
    link = tmp_path / "l"
    link.symlink_to(target)
    with pytest.raises(NotRegularFileError):
        safeio.open_regular_read(str(link))


def test_open_regular_read_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(NotRegularFileError):
        safeio.open_regular_read(str(tmp_path))


@needs_fifo
def test_open_regular_read_fifo_does_not_block(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    def attempt() -> None:
        with pytest.raises(NotRegularFileError):
            safeio.open_regular_read(str(fifo))

    _run_with_timeout(attempt)


def test_open_regular_read_propagates_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*_a: Any, **_k: Any) -> int:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(safeio.os, "open", deny)
    with pytest.raises(PermissionError):
        safeio.open_regular_read(str(tmp_path / "f"))


def test_open_regular_read_does_not_leak_fd_on_rejection(tmp_path: Path) -> None:
    closed: list[int] = []
    real_close = os.close

    def spy_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(safeio.os, "close", spy_close)
        with pytest.raises(NotRegularFileError):
            safeio.open_regular_read(str(tmp_path))
    assert len(closed) == 1


# ---------------------------------------------------------------- read_bounded (U-IO2)
def test_read_bounded_at_and_over_the_bound(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b"a" * 10)
    assert safeio.read_bounded(str(p), 10) == b"a" * 10
    with pytest.raises(TooLargeError):
        safeio.read_bounded(str(p), 9)
    p.write_bytes(b"")
    assert safeio.read_bounded(str(p), 0) == b""


def test_read_bounded_raises_at_max_plus_one(tmp_path: Path) -> None:
    p = tmp_path / "f"
    p.write_bytes(b"x" * 101)
    with pytest.raises(TooLargeError):
        safeio.read_bounded(str(p), 100)


def test_read_bounded_never_reads_more_than_max_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that grows after the size check is caught by the bounded read loop."""
    p = tmp_path / "f"
    p.write_bytes(b"y" * 5000)
    real_fstat, real_read = os.fstat, os.read
    calls = {"fstat": 0}
    requested: list[int] = []

    def lying_fstat(fd: int) -> Any:
        real = real_fstat(fd)
        calls["fstat"] += 1
        if calls["fstat"] >= 2:  # the size check inside read_bounded: claim the file is tiny
            return SimpleNamespace(st_size=0, st_mode=real.st_mode, st_uid=real.st_uid)
        return real

    def spy_read(fd: int, n: int) -> bytes:
        requested.append(n)
        return real_read(fd, n)

    monkeypatch.setattr(safeio.os, "fstat", lying_fstat)
    monkeypatch.setattr(safeio.os, "read", spy_read)
    with pytest.raises(TooLargeError):
        safeio.read_bounded(str(p), 100)
    assert sum(requested) <= 101


@needs_posix
def test_read_bounded_rejects_symlink(tmp_path: Path) -> None:
    (tmp_path / "t").write_text("x")
    (tmp_path / "l").symlink_to(tmp_path / "t")
    with pytest.raises(NotRegularFileError):
        safeio.read_bounded(str(tmp_path / "l"), 10)


# ---------------------------------------------------------------- create_exclusive (U-IO3)
def test_create_exclusive_creates_0600_and_refuses_existing(tmp_path: Path) -> None:
    p = tmp_path / "new"
    old = os.umask(0o022)
    try:
        fd = safeio.create_exclusive(str(p))
    finally:
        os.umask(old)
    try:
        os.write(fd, b"data")
    finally:
        os.close(fd)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_bytes() == b"data"
    with pytest.raises(FileExistsError):
        safeio.create_exclusive(str(p))


@needs_posix
def test_create_exclusive_never_writes_through_a_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "l"
    link.symlink_to(victim)
    with pytest.raises(FileExistsError):
        safeio.create_exclusive(str(link))
    assert victim.read_text() == "keep"


# ---------------------------------------------------------------- dir chains (U-IO4)
def test_check_dir_chain_ok_and_missing_tail(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    safeio.check_dir_chain(str(tmp_path), str(tmp_path / "a" / "b"))
    safeio.check_dir_chain(str(tmp_path), str(tmp_path / "a" / "missing" / "deeper"))
    safeio.check_dir_chain(str(tmp_path / "gone"), str(tmp_path / "gone" / "x"))


@needs_posix
def test_check_dir_chain_rejects_symlinked_component(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "link").symlink_to(tmp_path / "real")
    with pytest.raises(UnsafePathError):
        safeio.check_dir_chain(str(tmp_path), str(tmp_path / "a" / "link" / "x"))
    with pytest.raises(UnsafePathError):
        safeio.check_dir_chain(str(tmp_path), str(tmp_path / "a" / "link"))


def test_check_dir_chain_rejects_file_component_and_outside_path(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    with pytest.raises(UnsafePathError):
        safeio.check_dir_chain(str(tmp_path), str(tmp_path / "f" / "x"))
    with pytest.raises(UnsafePathError):
        safeio.check_dir_chain(str(tmp_path / "a"), str(tmp_path / "b"))
    with pytest.raises(UnsafePathError):
        safeio.check_dir_chain(str(tmp_path), str(tmp_path / ".." / "elsewhere"))


def test_ensure_dir_chain_creates_components_with_mode(tmp_path: Path) -> None:
    target = tmp_path / "x" / "y" / "z"
    old = os.umask(0o022)
    try:
        safeio.ensure_dir_chain(str(tmp_path), str(target), CACHE_DIR_MODE)
        safeio.ensure_dir_chain(str(tmp_path), str(target), CACHE_DIR_MODE)  # idempotent
    finally:
        os.umask(old)
    assert target.is_dir()
    assert stat.S_IMODE((tmp_path / "x").stat().st_mode) == CACHE_DIR_MODE


@needs_posix
def test_ensure_dir_chain_rejects_symlinked_component(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (tmp_path / "a").symlink_to(victim)
    with pytest.raises(UnsafePathError):
        safeio.ensure_dir_chain(str(tmp_path), str(tmp_path / "a" / "b"), CACHE_DIR_MODE)
    assert list(victim.iterdir()) == []  # nothing created through the link


def test_ensure_dir_chain_rejects_outside_path(tmp_path: Path) -> None:
    with pytest.raises(UnsafePathError):
        safeio.ensure_dir_chain(str(tmp_path / "a"), str(tmp_path / "b"), CACHE_DIR_MODE)


# ---------------------------------------------------------------- check_root_dir (U-IO5)
@pytest.fixture
def ws_and_root(tmp_path: Path) -> tuple[str, str]:
    ws = tmp_path / "ws"
    root = ws / ".orchestrator" / "cache"
    root.mkdir(parents=True)
    root.chmod(CACHE_DIR_MODE)
    return str(ws), str(root)


def test_check_root_dir_accepts_good_and_missing_root(
    ws_and_root: tuple[str, str], tmp_path: Path
) -> None:
    ws, root = ws_and_root
    safeio.check_root_dir(root, workspace_root=ws)
    empty_ws = tmp_path / "empty"
    empty_ws.mkdir()
    safeio.check_root_dir(str(empty_ws / ".orchestrator" / "cache"), workspace_root=str(empty_ws))


@needs_posix
def test_check_root_dir_rejects_symlinked_root(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".orchestrator").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    (ws / ".orchestrator" / "cache").symlink_to(victim)
    with pytest.raises(UnsafePathError):
        safeio.check_root_dir(str(ws / ".orchestrator" / "cache"), workspace_root=str(ws))


@needs_posix
def test_check_root_dir_rejects_symlinked_dot_orchestrator(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    victim = tmp_path / "victim"
    (victim / "cache").mkdir(parents=True)
    (ws / ".orchestrator").symlink_to(victim)
    with pytest.raises(UnsafePathError):
        safeio.check_root_dir(str(ws / ".orchestrator" / "cache"), workspace_root=str(ws))


def test_check_root_dir_rejects_root_that_is_a_file(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".orchestrator").mkdir(parents=True)
    (ws / ".orchestrator" / "cache").write_text("x")
    with pytest.raises(UnsafePathError):
        safeio.check_root_dir(str(ws / ".orchestrator" / "cache"), workspace_root=str(ws))


def test_check_root_dir_rejects_root_outside_workspace(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    (tmp_path / "other").mkdir()
    with pytest.raises(UnsafePathError):
        safeio.check_root_dir(str(tmp_path / "other"), workspace_root=str(tmp_path / "ws"))


@needs_posix
def test_check_root_dir_rejects_foreign_uid(
    ws_and_root: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, root = ws_and_root
    real_uid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_uid + 1)
    with pytest.raises(UnsafePathError):
        safeio.check_root_dir(root, workspace_root=ws)


@needs_posix
def test_check_root_dir_rejects_group_writable_root_it_does_not_own(
    ws_and_root: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, root = ws_and_root
    os.chmod(root, 0o770)
    real_uid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_uid + 1)
    with pytest.raises(UnsafePathError):
        safeio.check_root_dir(root, workspace_root=ws)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o770  # not ours: left untouched


@needs_posix
@pytest.mark.parametrize("bad_mode", [0o770, 0o707, 0o777, 0o720])
def test_check_root_dir_chmods_group_writable_root_it_owns(
    ws_and_root: tuple[str, str], bad_mode: int
) -> None:
    ws, root = ws_and_root
    os.chmod(root, bad_mode)
    safeio.check_root_dir(root, workspace_root=ws)
    assert stat.S_IMODE(os.stat(root).st_mode) == CACHE_DIR_MODE


# ---------------------------------------------------------------- is_sensitive_rel_path (U-IO6)
@pytest.mark.parametrize("component", sorted(SENSITIVE_PATH_COMPONENTS))
def test_sensitive_components_anywhere_in_the_path(component: str) -> None:
    assert safeio.is_sensitive_rel_path(component)
    assert safeio.is_sensitive_rel_path(f"{component}/x")
    assert safeio.is_sensitive_rel_path(f"a/b/{component}/c.txt")
    assert safeio.is_sensitive_rel_path(f"a/{component}")


@pytest.mark.parametrize(
    "rel", ["CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".mcp.json", ".envrc", "a/b/CLAUDE.md"]
)
def test_sensitive_basenames(rel: str) -> None:
    assert safeio.is_sensitive_rel_path(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "docs/claude.md",
        "out/.gitkeep",
        "out/summary.md",
        ".gitignore",
        "a/.github-notes/x",
        "src/CLAUDE.md/inner.txt",
        "notes/AGENTS.md.bak",
        "x.envrc",
        "",
        ".",
    ],
)
def test_non_sensitive_paths(rel: str) -> None:
    assert not safeio.is_sensitive_rel_path(rel)


# ---------------------------------------------------------------- posix_rel / strip (U-IO7)
@pytest.mark.parametrize(
    ("path", "base", "expected"),
    [
        ("/ws/out/a.md", "/ws", "out/a.md"),
        ("/ws/out/../in/b.md", "/ws", "in/b.md"),
        ("/ws", "/ws", "."),
        ("/ws/a//b/./c", "/ws", "a/b/c"),
        ("/other/x", "/ws/sub", "../../other/x"),
    ],
)
def test_posix_rel(path: str, base: str, expected: str) -> None:
    if sys.platform == "win32":  # pragma: no cover
        pytest.skip("posix absolute paths")
    assert safeio.posix_rel(path, base) == expected


def test_posix_rel_uses_forward_slashes_on_any_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "sep", "\\")
    monkeypatch.setattr(
        os.path, "normpath", lambda p: p.replace("/", "\\") if isinstance(p, str) else p
    )
    monkeypatch.setattr(os.path, "relpath", lambda p, b: "a/b")
    assert safeio.posix_rel("x", "y") == "a/b"


def test_strip_control_chars_removes_c0_c1_and_del() -> None:
    c0 = "".join(chr(i) for i in range(0x20))
    c1 = "".join(chr(i) for i in range(0x80, 0xA0))
    assert safeio.strip_control_chars(f"a{c0}b\x7f{c1}c") == "abc"
    assert safeio.strip_control_chars("line1\nline2\x1b[31mred") == "line1line2[31mred"


def test_strip_control_chars_keeps_printable_and_unicode() -> None:
    text = "plain text ~   é 中文 \U0001f600"
    assert safeio.strip_control_chars(text) == text
