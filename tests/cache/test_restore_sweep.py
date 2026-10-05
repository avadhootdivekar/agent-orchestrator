"""T-6tRKml: unit tests for the stale restore-staging sweep (`cache/restore_sweep.py`).

The CLI-level behaviour is covered end to end in `tests/test_e2e_cli_result_cache_admin.py`; this
file pins the path validation, the bounds and the per-file rules directly, with a fixed clock.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator.cache import restore_sweep
from agent_orchestrator.cache.constants import TMP_SWEEP_GRACE_SECONDS
from agent_orchestrator.cache.restore_sweep import (
    collect_output_dirs,
    safe_parent_dir,
    sweep_restore_leftovers,
)
from tests.cache.fakes import key_of, make_entry

TMP = ".ao-result-cache-1-2-abc.tmp"
BAK = TMP + ".bak"
# ctime is the file's real creation time, so "later" is relative to the real clock
LATER = datetime.now(UTC) + timedelta(seconds=2 * TMP_SWEEP_GRACE_SECONDS)


@pytest.mark.parametrize(
    ("path", "parent"),
    [
        ("out/a.md", "out"),
        ("a.md", ""),
        ("deep/er/dir/a.md", "deep/er/dir"),
        ("sp ace/ü.md", "sp ace"),
    ],
)
def test_safe_parent_dir_accepts_plain_relative_paths(path: str, parent: str) -> None:
    assert safe_parent_dir(path) == parent


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs/a.md",
        "../a.md",
        "out/../a.md",
        "out/./a.md",
        "./a.md",
        "out//a.md",
        "out\\a.md",
        "out/a\x1b[31m.md",
        "out/a‮.md",
        ".git/hooks/a",
        "x/.claude/a.md",
        ".orchestrator/cache/a",
        "CLAUDE.md",
        "docs/.ao/a",
    ],
)
def test_safe_parent_dir_rejects_everything_else(path: str) -> None:
    assert safe_parent_dir(path) is None


def test_collect_dedupes_sorts_and_skips_hostile_paths() -> None:
    entry = make_entry(
        key_of("a"),
        outputs=[("z/a.md", b"1"), ("a/b.md", b"2"), ("a/c.md", b"3"), ("../x.md", b"4")],
    )
    other = make_entry(key_of("b"), outputs=[("top.md", b"5"), ("/etc/p", b"6")])
    dirs, truncated = collect_output_dirs([entry, other])
    assert dirs == ["", "a", "z"] and truncated is False


def test_collect_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore_sweep, "RESTORE_SWEEP_MAX_DIRS", 2)
    entry = make_entry(key_of("a"), outputs=[(f"d{i}/f.md", b"x") for i in range(5)])
    dirs, truncated = collect_output_dirs([entry])
    assert len(dirs) == 2 and truncated is True
    again, cut = collect_output_dirs([entry, entry])  # a repeat does not count as a new dir
    assert (len(again), cut) == (2, True)


def test_sweep_removes_stale_files_and_counts_them(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / TMP).write_bytes(b"x")
    report = sweep_restore_leftovers(str(tmp_path), ["out"], now=LATER, dry_run=False)
    assert report == restore_sweep.RestoreSweepReport(removed=1, kept_backups=0, dirs_scanned=1)
    assert not (out / TMP).exists()


def test_sweep_skips_young_files(tmp_path: Path) -> None:
    (tmp_path / TMP).write_bytes(b"x")
    report = sweep_restore_leftovers(str(tmp_path), [""], now=datetime.now(UTC), dry_run=False)
    assert report.removed == 0 and (tmp_path / TMP).exists()


def test_sweep_ignores_a_stale_mtime_when_the_ctime_is_fresh(tmp_path: Path) -> None:
    """A `.bak` hard link keeps the OLD file's mtime; only the ctime says when it was linked."""
    path = tmp_path / BAK
    other = tmp_path / "dest.md"
    other.write_text("old", encoding="utf-8")
    os.link(other, path)
    ancient = datetime(2001, 1, 1, tzinfo=UTC).timestamp()
    os.utime(other, (ancient, ancient))
    report = sweep_restore_leftovers(str(tmp_path), [""], now=datetime.now(UTC), dry_run=False)
    assert report.removed == 0 and path.exists()


def test_sweep_never_deletes_a_sole_copy_backup(tmp_path: Path) -> None:
    (tmp_path / BAK).write_bytes(b"only copy")
    report = sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=False)
    assert (report.removed, report.kept_backups) == (0, 1) and (tmp_path / BAK).exists()


def test_sweep_skips_missing_symlinked_and_non_directories(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / TMP).write_bytes(b"x")
    (tmp_path / "link").symlink_to(real)
    (tmp_path / "plain").write_text("not a dir", encoding="utf-8")
    report = sweep_restore_leftovers(
        str(tmp_path),
        ["missing", "link", "plain", "plain/sub", "link/sub"],
        now=LATER,
        dry_run=False,
    )
    assert report.removed == 0 and report.dirs_scanned == 0 and (real / TMP).exists()


def test_sweep_works_through_a_symlinked_workspace_root(tmp_path: Path) -> None:
    real = tmp_path / "real-ws"
    (real / "out").mkdir(parents=True)
    (real / "out" / TMP).write_bytes(b"x")
    link = tmp_path / "ws-link"
    link.symlink_to(real)
    assert sweep_restore_leftovers(str(link), ["out"], now=LATER, dry_run=False).removed == 1


def test_sweep_only_deletes_files_owned_by_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / TMP).write_bytes(b"x")
    someone_else = os.geteuid() + 1
    monkeypatch.setattr(restore_sweep.os, "geteuid", lambda: someone_else)
    assert sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=False).removed == 0
    assert (tmp_path / TMP).exists()


def test_sweep_listing_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore_sweep, "CLI_MAX_DIR_ITEMS", 3)
    for i in range(6):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    (tmp_path / TMP).write_bytes(b"x")
    report = sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=False)
    assert report.truncated is True


def test_sweep_survives_a_file_removed_underneath_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / TMP).write_bytes(b"x")
    real_lstat = os.lstat

    def vanishing(path: str, *, dir_fd: int | None = None):
        if path == TMP:
            raise FileNotFoundError(path)
        return real_lstat(path, dir_fd=dir_fd)

    monkeypatch.setattr(restore_sweep.os, "lstat", vanishing)
    assert sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=False).removed == 0
    monkeypatch.undo()
    real_unlink = os.unlink

    def raced(path: str, *, dir_fd: int | None = None) -> None:
        real_unlink(path, dir_fd=dir_fd)
        raise FileNotFoundError(path)

    monkeypatch.setattr(restore_sweep.os, "unlink", raced)
    assert sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=False).removed == 0


def test_a_directory_that_cannot_be_listed_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / TMP).write_bytes(b"x")

    def broken(fd: int, *, cutoff: float, dry_run: bool):
        raise PermissionError("denied")

    monkeypatch.setattr(restore_sweep, "_sweep_dir", broken)
    report = sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=False)
    assert report == restore_sweep.RestoreSweepReport()


def test_dry_run_counts_without_deleting(tmp_path: Path) -> None:
    (tmp_path / TMP).write_bytes(b"x")
    report = sweep_restore_leftovers(str(tmp_path), [""], now=LATER, dry_run=True)
    assert report.removed == 1 and (tmp_path / TMP).exists()
