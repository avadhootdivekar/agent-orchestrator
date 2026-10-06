"""AC U-H1..U-H10: bounded hashing of files and directories (HLD 8.2.3).

FIFO tests run in a thread joined with a timeout so a regression cannot hang the suite. A
permission error is SIMULATED by patching os.open (CI may run as root, where EACCES never fires).
"""

from __future__ import annotations

import errno
import hashlib
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache import hashing
from agent_orchestrator.cache.constants import (
    DIR_DIGEST_SCHEMA,
    HASH_CHUNK_BYTES,
    KIND_DIR,
    KIND_FILE,
    REASON_INPUT_MISSING,
    REASON_INPUT_NOT_REGULAR,
    REASON_INPUT_TOO_LARGE,
    REASON_INPUT_UNREADABLE,
    REASON_INPUT_UNSTABLE,
)
from agent_orchestrator.cache.hashing import (
    HashBudget,
    digest_path,
    hash_directory,
    hash_regular_file,
)
from agent_orchestrator.cache.types import UncacheableError, canonical_json

FIFO_TIMEOUT_SECONDS = 5
BIG = 10**9

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks are POSIX-only here")


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


def budget(max_bytes: int = BIG, max_files: int = BIG) -> HashBudget:
    return HashBudget(max_bytes, max_files)


def dir_digest(path: Path, **kw: Any) -> Any:
    kw.setdefault("exclude_abs", frozenset())
    kw.setdefault("skip_abs", frozenset())
    return hash_directory(str(path), budget(), **kw)


def reason_of(fn: Callable[[], Any]) -> UncacheableError:
    with pytest.raises(UncacheableError) as info:
        fn()
    return info.value


def run_with_timeout(fn: Callable[[], Any]) -> Any:
    """Run fn in a daemon thread; fail (not hang) if it does not return in time."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(FIFO_TIMEOUT_SECONDS)
    assert not t.is_alive(), "call blocked (FIFO?)"
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ------------------------------------------------------------------------------------ U-H1
def test_file_digest_equals_sha256_of_content(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_bytes(b"alpha\n")
    dg = hash_regular_file(str(f), budget())
    assert (dg.kind, dg.sha256, dg.size) == (KIND_FILE, hashlib.sha256(b"alpha\n").hexdigest(), 6)


def test_empty_file_digest(tmp_path: Path) -> None:
    f = tmp_path / "e"
    f.write_bytes(b"")
    dg = hash_regular_file(str(f), budget())
    assert dg.sha256 == hashlib.sha256(b"").hexdigest() and dg.size == 0


def test_multi_chunk_file_digest(tmp_path: Path) -> None:
    content = os.urandom(3) * (HASH_CHUNK_BYTES + 12345)  # spans several reads
    f = tmp_path / "big.bin"
    f.write_bytes(content)
    dg = hash_regular_file(str(f), budget())
    assert dg.sha256 == hashlib.sha256(content).hexdigest() and dg.size == len(content)


def test_digest_path_dispatches_on_type(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"x")
    d = tmp_path / "d"
    d.mkdir()
    assert digest_path(str(f), budget()).kind == KIND_FILE
    assert digest_path(str(d), budget()).kind == KIND_DIR


# ------------------------------------------------------------------------------------ U-H2
def test_directory_digest_is_independent_of_creation_order(tmp_path: Path) -> None:
    names = ["b.txt", "a.txt", "sub/z.txt", "sub/c.txt", "m/n/o.txt"]
    digests = set()
    for i, order in enumerate((names, list(reversed(names)), sorted(names))):
        root = tmp_path / f"root{i}"
        for name in order:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content of {name}")
        digests.add(dir_digest(root))
    assert len(digests) == 1


def test_renaming_one_file_changes_the_directory_digest(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.txt").write_text("same")
    before = dir_digest(root)
    (root / "a.txt").rename(root / "b.txt")
    after = dir_digest(root)
    assert before.sha256 != after.sha256


def test_changing_content_changes_the_directory_digest(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.txt").write_text("one")
    before = dir_digest(root)
    (root / "a.txt").write_text("two")
    assert dir_digest(root).sha256 != before.sha256


def test_directory_manifest_is_the_documented_canonical_document(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.txt").write_bytes(b"hi")
    expected = canonical_json(
        {
            "schema": DIR_DIGEST_SCHEMA,
            "entries": [["D", "sub"], ["F", "sub/f.txt", 2, hashlib.sha256(b"hi").hexdigest()]],
        }
    )
    dg = dir_digest(root)
    assert dg.sha256 == hashlib.sha256(expected.encode("ascii")).hexdigest()
    assert (dg.kind, dg.size) == (KIND_DIR, 2)


# ------------------------------------------------------------------------------------ U-H3
def test_empty_directory_has_a_stable_digest_and_zero_size(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    da, db = dir_digest(a), dir_digest(b)
    assert da == db and da.size == 0
    expected = canonical_json({"schema": DIR_DIGEST_SCHEMA, "entries": []})
    assert da.sha256 == hashlib.sha256(expected.encode("ascii")).hexdigest()


def test_non_utf8_names_hash_deterministically(tmp_path: Path) -> None:
    digests = []
    for i in range(2):
        root = tmp_path / f"r{i}"
        root.mkdir()
        raw = os.fsencode(str(root)) + b"/caf\xe9-\xff.txt"  # not valid UTF-8
        try:
            with open(raw, "wb") as fh:
                fh.write(b"data")
        except OSError:
            pytest.skip("filesystem rejects non-UTF-8 names")
        digests.append(dir_digest(root))
    assert digests[0] == digests[1] and digests[0].size == 1


# ------------------------------------------------------------------------------------ U-H4
@needs_posix
def test_symlink_inside_a_directory_is_not_regular(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (tmp_path / "target.txt").write_text("t")
    (root / "link").symlink_to(tmp_path / "target.txt")
    err = reason_of(lambda: dir_digest(root))
    assert err.reason == REASON_INPUT_NOT_REGULAR and "link" in err.detail


@needs_posix
def test_dangling_symlink_inside_a_directory_is_not_regular(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "dangling").symlink_to(tmp_path / "nowhere")
    assert reason_of(lambda: dir_digest(root)).reason == REASON_INPUT_NOT_REGULAR


@needs_posix
def test_symlink_given_directly_is_not_regular(tmp_path: Path) -> None:
    (tmp_path / "t").write_text("x")
    (tmp_path / "l").symlink_to(tmp_path / "t")
    assert reason_of(lambda: digest_path(str(tmp_path / "l"), budget())).reason == (
        REASON_INPUT_NOT_REGULAR
    )
    assert reason_of(lambda: hash_regular_file(str(tmp_path / "l"), budget())).reason == (
        REASON_INPUT_NOT_REGULAR
    )


# ------------------------------------------------------------------------------------ U-H5
@needs_fifo
def test_fifo_inside_a_directory_is_not_regular_and_does_not_hang(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    os.mkfifo(root / "pipe")
    err = reason_of(lambda: run_with_timeout(lambda: dir_digest(root)))
    assert err.reason == REASON_INPUT_NOT_REGULAR


@needs_fifo
def test_fifo_given_directly_is_not_regular_and_does_not_hang(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    for fn in (digest_path, hash_regular_file):
        err = reason_of(lambda fn=fn: run_with_timeout(lambda: fn(str(fifo), budget())))  # type: ignore[misc]
        assert err.reason == REASON_INPUT_NOT_REGULAR


# ------------------------------------------------------------------------------------ U-H6
def test_too_many_bytes_is_refused_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"x" * 100)
    reads: list[int] = []
    real_read = os.read

    def spy(fd: int, n: int) -> bytes:
        reads.append(n)
        return real_read(fd, n)

    monkeypatch.setattr(os, "read", spy)
    err = reason_of(lambda: hash_regular_file(str(f), HashBudget(99, 10)))
    assert err.reason == REASON_INPUT_TOO_LARGE
    assert "bytes=100" in err.detail and "files=1" in err.detail
    assert reads == []  # checked BEFORE any byte was read


def test_too_many_files_is_refused_with_counts(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    for i in range(5):
        (root / f"f{i}").write_text("x")
    err = reason_of(
        lambda: hash_directory(
            str(root), HashBudget(BIG, 3), exclude_abs=frozenset(), skip_abs=frozenset()
        )
    )
    assert err.reason == REASON_INPUT_TOO_LARGE and "files=4" in err.detail


def test_directories_count_against_the_file_budget(tmp_path: Path) -> None:
    root = tmp_path / "r"
    for i in range(4):
        (root / f"d{i}").mkdir(parents=True)
    err = reason_of(
        lambda: hash_directory(
            str(root), HashBudget(BIG, 3), exclude_abs=frozenset(), skip_abs=frozenset()
        )
    )
    assert err.reason == REASON_INPUT_TOO_LARGE


def test_budget_is_cumulative_across_calls(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"x" * 60)
    shared = HashBudget(100, 10)
    hash_regular_file(str(f), shared)
    assert reason_of(lambda: hash_regular_file(str(f), shared)).reason == REASON_INPUT_TOO_LARGE


def test_budget_at_the_limit_is_allowed(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"x" * 100)
    assert hash_regular_file(str(f), HashBudget(100, 1)).size == 100


# ------------------------------------------------------------------------------------ U-H7
def test_file_that_grows_while_being_read_is_unstable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"abcd")
    real_read = os.read
    calls: list[int] = []

    def growing(fd: int, n: int) -> bytes:
        data = real_read(fd, n)
        calls.append(1)
        return data + b"EXTRA" if len(calls) == 1 else data

    monkeypatch.setattr(os, "read", growing)
    assert reason_of(lambda: hash_regular_file(str(f), budget())).reason == REASON_INPUT_UNSTABLE


def test_file_that_shrinks_while_being_read_is_unstable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"abcdefgh")
    real_read = os.read
    first = {"done": False}

    def short_then_eof(fd: int, n: int) -> bytes:
        if first["done"]:
            return b""
        first["done"] = True
        return real_read(fd, n)[:3]

    monkeypatch.setattr(os, "read", short_then_eof)
    assert reason_of(lambda: hash_regular_file(str(f), budget())).reason == REASON_INPUT_UNSTABLE


# ------------------------------------------------------------------------------------ U-H8
def test_missing_path_is_input_missing(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope")
    for fn in (hash_regular_file, digest_path):
        err = reason_of(lambda fn=fn: fn(missing, budget()))  # type: ignore[misc]
        assert err.reason == REASON_INPUT_MISSING and "nope" in err.detail


def test_missing_directory_is_input_missing(tmp_path: Path) -> None:
    assert reason_of(lambda: dir_digest(tmp_path / "nope")).reason == REASON_INPUT_UNREADABLE


def test_simulated_permission_error_on_open_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "f"
    f.write_text("x")

    def deny(path: Any, flags: int, *a: Any, **k: Any) -> int:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "open", deny)
    assert reason_of(lambda: hash_regular_file(str(f), budget())).reason == REASON_INPUT_UNREADABLE


def test_simulated_read_error_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "f"
    f.write_text("x")

    def eio(fd: int, n: int) -> bytes:
        raise OSError(errno.EIO, "io error")

    monkeypatch.setattr(os, "read", eio)
    assert reason_of(lambda: hash_regular_file(str(f), budget())).reason == REASON_INPUT_UNREADABLE


def test_simulated_scandir_error_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "r"
    root.mkdir()

    def deny(path: Any) -> Any:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "scandir", deny)
    assert reason_of(lambda: dir_digest(root)).reason == REASON_INPUT_UNREADABLE


def test_simulated_lstat_error_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(path: Any, *a: Any, **k: Any) -> Any:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "lstat", deny)
    assert reason_of(lambda: digest_path(str(tmp_path), budget())).reason == REASON_INPUT_UNREADABLE


# ------------------------------------------------------------------------------------ U-H9
def test_dot_git_is_skipped_at_every_depth(tmp_path: Path) -> None:
    clean, noisy = tmp_path / "clean", tmp_path / "noisy"
    for root in (clean, noisy):
        (root / "sub").mkdir(parents=True)
        (root / "a.txt").write_text("a")
    (noisy / ".git").mkdir()
    (noisy / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (noisy / "sub" / ".git").write_text("gitdir: ../elsewhere")  # worktree-style file
    assert dir_digest(noisy).sha256 == dir_digest(clean).sha256


def test_the_orchestrator_state_dir_is_skipped_via_skip_abs(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "a.md").write_text("a")
    before = dir_digest(ws, skip_abs=frozenset({str(ws / ".orchestrator")}))
    (ws / ".orchestrator" / "cache").mkdir(parents=True)
    (ws / ".orchestrator" / "cache" / "entry.json").write_text("{}")
    (ws / ".orchestrator" / "state.json").write_text("{}")
    after = dir_digest(ws, skip_abs=frozenset({str(ws / ".orchestrator")}))
    assert before == after
    # without the skip it is hashed (the walk itself does not know the name)
    assert dir_digest(ws) != before


@needs_posix
def test_skipped_entries_are_never_inspected(tmp_path: Path) -> None:
    """A symlink inside a skipped location must not trip input_not_regular."""
    ws = tmp_path / "ws"
    (ws / ".orchestrator").mkdir(parents=True)
    (ws / ".orchestrator" / "link").symlink_to(tmp_path)
    (ws / ".git").symlink_to(tmp_path)
    assert dir_digest(ws, skip_abs=frozenset({str(ws / ".orchestrator")})).size == 0


# ----------------------------------------------------------------------------------- U-H10
def test_declared_outputs_are_excluded_from_a_directory_walk(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "out").mkdir(parents=True)
    (root / "in.txt").write_text("input")
    out = root / "out" / "summary.md"
    before = dir_digest(root, exclude_abs=frozenset({str(out)}))
    out.write_text("an output written later")
    after = dir_digest(root, exclude_abs=frozenset({str(out)}))
    assert before == after
    assert dir_digest(root) != before  # not excluded: it counts


def test_digest_path_forwards_exclude_and_skip(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "in.txt").write_text("x")
    base = digest_path(str(root), budget())
    (root / "o.txt").write_text("o")
    (root / ".state").mkdir()
    (root / ".state" / "s").write_text("s")
    got = digest_path(
        str(root),
        budget(),
        exclude_abs=frozenset({str(root / "o.txt")}),
        skip_abs=frozenset({str(root / ".state")}),
    )
    assert got == base


def test_module_exposes_the_documented_names() -> None:
    assert {"HashBudget", "hash_regular_file", "hash_directory", "digest_path"} <= set(dir(hashing))


# ------------------------------------------- restore temp names are NOT exempt (G2-S1)
RESTORE_TMP_NAMES = [
    ".ao-result-cache-1234-5678-" + "a" * 32 + ".tmp",
    ".ao-result-cache-1234-5678-" + "a" * 32 + ".tmp.bak",
]


@pytest.mark.parametrize("name", RESTORE_TMP_NAMES)
def test_a_file_with_a_restore_temp_name_changes_the_directory_digest(
    tmp_path: Path, name: str
) -> None:
    """G2-S1 (supersedes the G1b S-1 exemption): a name-based skip would let any writer hide a
    file from the key. A crash leftover costs a false miss, never a false hit."""
    root = tmp_path / "in"
    root.mkdir()
    (root / "a.md").write_text("a")
    before = dir_digest(root)
    (root / name).write_text("planted instructions the task will see")
    with_file = dir_digest(root)
    assert with_file != before
    (root / name).write_text("different content")  # content is part of the key too
    assert dir_digest(root) not in (before, with_file)
    (root / name).unlink()
    assert dir_digest(root) == before  # removing it restores the clean key (no stale poisoning)


@pytest.mark.parametrize("name", RESTORE_TMP_NAMES)
def test_a_restore_temp_name_nested_deep_is_hashed_too(tmp_path: Path, name: str) -> None:
    root = tmp_path / "in"
    (root / "x" / "y").mkdir(parents=True)
    before = dir_digest(root)
    (root / "x" / "y" / name).write_text("deep")
    assert dir_digest(root) != before


def test_a_directory_carrying_a_temp_name_is_walked_as_a_real_input(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    base = dir_digest(root)
    (root / RESTORE_TMP_NAMES[0]).mkdir()
    assert dir_digest(root) != base


@needs_posix
def test_a_symlink_with_a_temp_name_is_still_not_regular(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    (root / RESTORE_TMP_NAMES[0]).symlink_to(tmp_path)
    with pytest.raises(UncacheableError) as err:
        dir_digest(root)
    assert err.value.reason == REASON_INPUT_NOT_REGULAR


def test_a_temp_leftover_given_directly_as_an_input_is_hashed(tmp_path: Path) -> None:
    """An explicitly declared file is hashed like any other."""
    path = tmp_path / RESTORE_TMP_NAMES[0]
    path.write_text("declared")
    assert digest_path(str(path), budget()).size == len("declared")
