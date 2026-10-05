"""E-7 (T-6tRKml): the `ao cache` admin commands, end to end through `CliRunner`.

Every test starts at the outermost boundary (`ao cache ...` on the real Typer app) against a temp
workspace populated through the store API, and validates `--json` output against the HLD 13.4
schemas stored under `tests/fixtures/result_cache/schemas/`. The wall clock is the module-level
`cache.cli._now`, patched to `NOW`; the only real-time dependence is the age of files the tests
create (ctime cannot be set), which the restore-sweep tests handle by moving the patched clock
forward.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from typer.testing import CliRunner

from agent_orchestrator.cache import cli as cache_cli
from agent_orchestrator.cache import cli_ops
from agent_orchestrator.cache.constants import (
    CLI_MAX_CANDIDATES,
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MAX_ENTRY_BYTES,
    DEFAULT_CACHE_TTL_DAYS,
    SCHEMA_CLEAR,
    SCHEMA_LS,
    SCHEMA_PRUNE,
    SCHEMA_RM,
    SCHEMA_SHOW,
    SCHEMA_STATS,
    SCHEMA_VERIFY,
    TMP_SWEEP_GRACE_SECONDS,
)
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import CacheEntry
from agent_orchestrator.cli import app
from tests.cache._wiring_fixture import write_fixture
from tests.cache.fakes import key_of, make_entry, sha_of

NOW = datetime.now(UTC).replace(microsecond=0)
OLD_SECONDS = 4 * TMP_SWEEP_GRACE_SECONDS  # blob mtime: well past every grace period
BLOB_BOUND = 1024 * 1024
SCHEMA_DIR = Path(__file__).parent / "fixtures" / "result_cache" / "schemas"
ESC = "\x1b"
BEL = "\x07"
BIDI_OVERRIDE = "‮"
ZERO_WIDTH = "​"

runner = CliRunner()
_REAL_NOW = cache_cli._now  # captured before the autouse fixture patches it


# --------------------------------------------------------------------------- schemas
def _load_schemas() -> dict[str, dict[str, object]]:
    return {
        s["$id"]: s for s in (json.loads(p.read_text()) for p in sorted(SCHEMA_DIR.glob("*.json")))
    }


SCHEMAS = _load_schemas()
# The show schema's `$ref` is the bare id "ao.result-cache.entry/v1", which resolves against the
# `ao.result-cache.show/` "directory" of its own id; register that spelling too.
_REGISTRY = Registry().with_resources(
    [(sid, Resource.from_contents(s)) for sid, s in SCHEMAS.items()]
    + [
        (
            "ao.result-cache.show/ao.result-cache.entry/v1",
            Resource.from_contents(SCHEMAS["ao.result-cache.entry/v1"]),
        )
    ]
)


def validate(doc: object, schema_id: str) -> None:
    Draft202012Validator(SCHEMAS[schema_id], registry=_REGISTRY).validate(doc)


# --------------------------------------------------------------------------- environment
@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("AO_CACHE", "AO_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no project config is discoverable from here
    monkeypatch.setattr(cache_cli, "_now", lambda: NOW)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture
def store(ws: Path) -> LocalFsCacheStore:
    return LocalFsCacheStore.for_workspace(
        ws, max_bytes=DEFAULT_CACHE_MAX_BYTES, ttl_days=DEFAULT_CACHE_TTL_DAYS
    )


def cache(ws: Path, *args: str, input_text: str | None = None):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["cache", *args, "-w", str(ws)], input=input_text)


def cache_json(ws: Path, *args: str, input_text: str | None = None):  # type: ignore[no-untyped-def]
    return cache(ws, *args, "--json", input_text=input_text)


def doc_of(result) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """The ONE JSON document on stdout (stderr is separate)."""
    assert result.stdout.count("\n") == 1, result.stdout
    doc = json.loads(result.stdout)
    assert isinstance(doc, dict)
    return doc


def write_config(directory: Path, body: str) -> None:
    (directory / ".ao").mkdir(exist_ok=True)
    (directory / ".ao" / "config.yaml").write_text(body, encoding="utf-8")


def root_of(ws: Path) -> Path:
    return ws / ".orchestrator" / "cache"


def entry_file(ws: Path, key: str) -> Path:
    return root_of(ws) / "entries" / "v1" / key[:2] / f"{key}.json"


def blob_file(ws: Path, sha: str) -> Path:
    return root_of(ws) / "blobs" / sha[:2] / sha


def add_entry(
    ws: Path,
    store: LocalFsCacheStore,
    name: str,
    *,
    key: str | None = None,
    files: dict[str, bytes] | None = None,
    age_days: float = 1.0,
    last_used_seconds_ago: float = 0.0,
    cost: float = 0.5,
    task_id: str | None = None,
) -> CacheEntry:
    """A complete entry: blobs (aged past the sweep grace), the entry, its LRU stamp."""
    contents = files if files is not None else {f"out/{name}.md": name.encode() * 20}
    for data in contents.values():
        ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
        stamp = NOW.timestamp() - OLD_SECONDS
        os.utime(blob_file(ws, ref.sha256), (stamp, stamp))
    entry = make_entry(
        key or key_of(name),
        outputs=list(contents.items()),
        created_at=NOW - timedelta(days=age_days),
        cost_usd=cost,
    )
    if task_id is not None:
        source = entry.source.model_copy(update={"task_id": task_id})
        entry = entry.model_copy(update={"source": source})
    store.put_entry(entry)
    store.touch_entry(entry.key, NOW - timedelta(seconds=last_used_seconds_ago))
    return entry


def tree(root: Path) -> dict[str, tuple[int, int, int]]:
    """Everything under `root`, links not followed: type, size, mtime_ns."""
    out: dict[str, tuple[int, int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            st = path.lstat()
            out[path.relative_to(root).as_posix()] = (
                stat.S_IFMT(st.st_mode),
                st.st_size,
                st.st_mtime_ns,
            )
    return out


def has_control_chars(text: str) -> bool:
    return any(c != "\n" and (ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F) for c in text)


# =========================================================================== AC-1: ls
class TestLs:
    def test_text_columns(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "alpha", cost=1.25, task_id="writer")
        result = cache(ws, "ls")
        assert result.exit_code == 0, result.output
        row = next(line for line in result.stdout.splitlines() if line.startswith(entry.key[:12]))
        assert entry.key[:13] not in row  # key[:12] only
        assert "writer/wf-20261005T101450Z" in row  # source task/run
        assert "$1.2500" in row  # cost
        assert str(entry.outputs[0].size) in row  # bytes
        assert (NOW - timedelta(days=1)).isoformat(timespec="seconds") in row  # created
        assert NOW.isoformat(timespec="seconds") in row  # last used (touched to NOW)
        header = result.stdout.splitlines()[0]
        for column in ("KEY", "LAST USED", "CREATED", "OUT", "BYTES", "SOURCE", "COST"):
            assert column in header

    def test_json_validates_and_carries_the_listing_fields(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        entry = add_entry(ws, store, "alpha", cost=0.75)
        result = cache_json(ws, "ls")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_LS)
        (row,) = doc["entries"]  # type: ignore[misc]
        assert row["key"] == entry.key and row["status"] == "ok"
        assert row["outputs"] == 1 and row["bytes"] == entry.outputs[0].size
        assert row["cost_usd"] == 0.75 and row["source"]["task_id"] == "t"
        assert doc["root"] == str(root_of(ws))

    def _three(self, ws: Path, store: LocalFsCacheStore) -> None:
        # last used (most recent first): b, a, c;  created (newest first): b, c, a;
        # size (largest first): c, a, b
        add_entry(ws, store, "a", age_days=3, last_used_seconds_ago=50, files={"o/a": b"x" * 100})
        add_entry(ws, store, "b", age_days=1, last_used_seconds_ago=10, files={"o/b": b"x" * 10})
        add_entry(ws, store, "c", age_days=2, last_used_seconds_ago=90, files={"o/c": b"x" * 500})

    @pytest.mark.parametrize(
        ("sort", "expected"),
        [("lru", "bac"), ("created", "bca"), ("size", "cab")],
    )
    def test_sort_orders(
        self, ws: Path, store: LocalFsCacheStore, sort: str, expected: str
    ) -> None:
        self._three(ws, store)
        result = cache_json(ws, "ls", "--sort", sort)
        assert result.exit_code == 0
        keys = [e["key"] for e in doc_of(result)["entries"]]  # type: ignore[attr-defined]
        assert keys == [key_of(name) for name in expected]

    def test_default_sort_is_lru(self, ws: Path, store: LocalFsCacheStore) -> None:
        self._three(ws, store)
        keys = [e["key"] for e in doc_of(cache_json(ws, "ls"))["entries"]]  # type: ignore[attr-defined]
        assert keys == [key_of(name) for name in "bac"]

    def test_limit_keeps_the_first_n(self, ws: Path, store: LocalFsCacheStore) -> None:
        self._three(ws, store)
        keys = [e["key"] for e in doc_of(cache_json(ws, "ls", "--limit", "2"))["entries"]]  # type: ignore[attr-defined]
        assert keys == [key_of("b"), key_of("a")]

    @pytest.mark.parametrize("limit", ["0", "-3", "99999999999"])
    def test_limit_out_of_range_exits_2(self, ws: Path, limit: str) -> None:
        result = cache(ws, "ls", "--limit", limit)
        assert result.exit_code == 2
        assert "ERROR:" in result.stderr

    @pytest.mark.parametrize("sort", ["bogus", "LRU", "", "size;rm", f"lru{ESC}[31m"])
    def test_invalid_sort_exits_2_in_text_and_json(self, ws: Path, sort: str) -> None:
        text = cache(ws, "ls", "--sort", sort)
        assert text.exit_code == 2
        assert "ERROR: invalid --sort" in text.stderr and not has_control_chars(text.stderr)
        as_json = cache_json(ws, "ls", "--sort", sort)
        assert as_json.exit_code == 2
        doc = doc_of(as_json)
        assert doc["schema"] == SCHEMA_LS and "invalid --sort" in str(doc["error"])

    def test_empty_cache_exits_0(self, ws: Path) -> None:
        result = cache(ws, "ls")
        assert result.exit_code == 0
        assert f"(result cache is empty: {root_of(ws)})" in result.stdout
        doc = doc_of(cache_json(ws, "ls"))
        validate(doc, SCHEMA_LS)
        assert doc["entries"] == []
        assert not root_of(ws).exists()  # a read never creates the cache directory

    def test_invalid_expired_and_ok_statuses(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "fresh", age_days=1)
        add_entry(ws, store, "old", age_days=DEFAULT_CACHE_TTL_DAYS + 5)
        junk_key = key_of("junk")
        junk = entry_file(ws, junk_key)
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("{not json", encoding="utf-8")
        doc = doc_of(cache_json(ws, "ls", "--limit", "10"))
        validate(doc, SCHEMA_LS)
        status = {e["key"]: e["status"] for e in doc["entries"]}  # type: ignore[attr-defined]
        assert status == {key_of("fresh"): "ok", key_of("old"): "expired", junk_key: "invalid"}
        bad = next(e for e in doc["entries"] if e["key"] == junk_key)  # type: ignore[attr-defined]
        assert bad["error"] == "corrupt_entry" and bad["created_at"] is None
        text = cache(ws, "ls").stdout
        assert "[invalid: corrupt_entry]" in text and "[expired]" in text

    def test_junk_files_are_not_listed_as_entries(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "real")
        shard = root_of(ws) / "entries" / "v1" / "ab"
        shard.mkdir(parents=True, exist_ok=True)
        (shard / "notes.txt").write_text("x", encoding="utf-8")
        (root_of(ws) / "entries" / "v1" / "cd").symlink_to(ws)
        doc = doc_of(cache_json(ws, "ls"))
        assert [e["key"] for e in doc["entries"]] == [key_of("real")]  # type: ignore[attr-defined]


# =========================================================================== AC-2: stats
class TestStats:
    G0_FIELDS = ("entries", "expired_entries", "oldest_created_at", "newest_created_at")

    def test_json_validates_and_has_the_g0_fields(self, ws: Path, store: LocalFsCacheStore) -> None:
        a = add_entry(ws, store, "a", age_days=3, files={"o/a": b"x" * 100})
        b = add_entry(ws, store, "b", age_days=1, files={"o/b": b"y" * 50})
        add_entry(ws, store, "stale", age_days=DEFAULT_CACHE_TTL_DAYS + 1)
        result = cache_json(ws, "stats")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_STATS)
        for field in self.G0_FIELDS:
            assert field in doc
        assert doc["exists"] is True and doc["entries"] == 3 and doc["expired_entries"] == 1
        sizes = doc["bytes"]
        assert isinstance(sizes, dict)
        assert sizes["total"] == sizes["entries"] + sizes["blobs"] > 0
        assert sizes["blobs"] >= len(a.outputs[0].path) and sizes["orphan_blobs"] == 0
        assert (
            doc["oldest_created_at"]
            == (NOW - timedelta(days=DEFAULT_CACHE_TTL_DAYS + 1)).isoformat()
        )
        assert doc["newest_created_at"] == b.created_at.isoformat()
        assert doc["limits"] == {
            "max_bytes": DEFAULT_CACHE_MAX_BYTES,
            "max_entry_bytes": DEFAULT_CACHE_MAX_ENTRY_BYTES,
            "ttl_days": DEFAULT_CACHE_TTL_DAYS,
        }

    def test_foreign_version_dirs_lists_a_planted_v2(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        add_entry(ws, store, "a")
        (root_of(ws) / "entries" / "v2" / "ab").mkdir(parents=True)
        doc = doc_of(cache_json(ws, "stats"))
        validate(doc, SCHEMA_STATS)
        assert doc["foreign_version_dirs"] == ["v2"]
        assert doc["entries"] == 1  # the v2 tree is never read as v1

    def test_missing_cache_directory(self, ws: Path) -> None:
        result = cache_json(ws, "stats")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_STATS)
        assert doc["exists"] is False and doc["entries"] == 0
        assert doc["oldest_created_at"] is None and doc["newest_created_at"] is None
        assert not root_of(ws).exists()
        text = cache(ws, "stats")
        assert text.exit_code == 0 and "result cache is empty" in text.stdout

    def test_workspace_that_does_not_exist_is_an_empty_cache(self, tmp_path: Path) -> None:
        doc = doc_of(cache_json(tmp_path / "nope", "stats"))
        assert doc["exists"] is False

    def test_text_output(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        (root_of(ws) / "entries" / "v2").mkdir()
        out = cache(ws, "stats").stdout
        for label in ("entries:     1", "limits:", "oldest:", "newest:", "foreign versions: v2"):
            assert label in out

    def test_limits_follow_the_project_config_and_ttl_null_never_expires(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        write_config(tmp_path, "cache:\n  max_bytes: 4096\n  ttl_days: null\n")
        add_entry(ws, store, "ancient", age_days=900)
        doc = doc_of(cache_json(ws, "stats"))
        assert doc["limits"] == {"max_bytes": 4096, "max_entry_bytes": 4096, "ttl_days": None}
        assert doc["expired_entries"] == 0
        assert "ttl=never" in cache(ws, "stats").stdout


# =========================================================================== AC-3: show
class TestShow:
    def test_unique_prefix_text(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "alpha")
        result = cache(ws, "show", entry.key[:8])
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert f"key:        {entry.key}" in out
        assert "out/alpha.md" in out and "present" in out
        assert "components:" in out

    def test_json_validates_with_blob_presence_and_components(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        entry = add_entry(ws, store, "alpha", files={"o/a": b"aaa", "o/b": b"bbbb"})
        blob_file(ws, entry.outputs[1].sha256).unlink()  # one blob is gone
        result = cache_json(ws, "show", entry.key)
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_SHOW)
        assert doc["entry"]["key"] == entry.key  # type: ignore[index]
        blobs = {b["sha256"]: b for b in doc["blobs"]}  # type: ignore[attr-defined]
        assert blobs[entry.outputs[0].sha256]["present"] is True
        assert blobs[entry.outputs[0].sha256]["size_on_disk"] == 3
        assert blobs[entry.outputs[1].sha256] == {
            "sha256": entry.outputs[1].sha256,
            "present": False,
            "size_on_disk": None,
        }
        assert doc["expired"] is False
        assert "components" in doc["entry"]["key_summary"]  # type: ignore[index]
        assert "MISSING" in cache(ws, "show", entry.key).stdout

    def test_expired_flag(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "old", age_days=DEFAULT_CACHE_TTL_DAYS + 3)
        assert doc_of(cache_json(ws, "show", entry.key[:6]))["expired"] is True

    def test_unknown_prefix_exits_1(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "alpha")
        result = cache(ws, "show", "ffff")
        assert result.exit_code == 1
        assert "ERROR: no cache entry matches 'ffff'" in result.stderr
        doc = doc_of(cache_json(ws, "show", "ffff"))
        assert doc["schema"] == SCHEMA_SHOW and "no cache entry" in str(doc["error"])

    def test_unknown_prefix_in_an_empty_store_exits_1(self, ws: Path) -> None:
        assert cache(ws, "show", "abcd").exit_code == 1
        assert not root_of(ws).exists()

    def _two_with_shared_prefix(self, ws: Path, store: LocalFsCacheStore) -> tuple[str, str]:
        k1 = "abcd" + sha_of(b"one")[:60]
        k2 = "abcd" + sha_of(b"two")[:60]
        add_entry(ws, store, "one", key=k1)
        add_entry(ws, store, "two", key=k2)
        return k1, k2

    def test_ambiguous_prefix_exits_1_and_lists_candidates(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        k1, k2 = self._two_with_shared_prefix(ws, store)
        result = cache(ws, "show", "abcd")
        assert result.exit_code == 1
        assert "ambiguous" in result.stderr and k1 in result.stderr and k2 in result.stderr
        doc = doc_of(cache_json(ws, "show", "abcd"))
        assert doc["candidates"] == sorted([k1, k2])
        # a longer prefix disambiguates
        assert cache(ws, "show", k1[:12]).exit_code == 0

    def test_many_candidates_are_capped(self, ws: Path, store: LocalFsCacheStore) -> None:
        for i in range(CLI_MAX_CANDIDATES + 5):
            add_entry(ws, store, f"n{i}", key="abcd" + sha_of(str(i).encode())[:60])
        doc = doc_of(cache_json(ws, "show", "abcd"))
        assert len(doc["candidates"]) == CLI_MAX_CANDIDATES  # type: ignore[arg-type]
        assert "more matches exist" in str(doc["error"])

    @pytest.mark.parametrize(
        "prefix",
        ["XYZ", "../x", "abc", "ABCD", "abcg", "a" * 65, "ab cd", "..", "ab/cd", f"abcd{ESC}[31m"],
    )
    def test_malformed_prefix_exits_2(
        self, ws: Path, store: LocalFsCacheStore, prefix: str
    ) -> None:
        add_entry(ws, store, "alpha")
        result = cache(ws, "show", prefix)
        assert result.exit_code == 2, result.output
        assert "malformed key prefix" in result.stderr and not has_control_chars(result.stderr)
        doc = doc_of(cache_json(ws, "show", prefix))
        assert doc["schema"] == SCHEMA_SHOW and "malformed" in str(doc["error"])

    def test_option_like_argument_is_a_usage_error(self, ws: Path) -> None:
        assert cache(ws, "show", "--bogus").exit_code == 2

    def test_missing_argument_is_a_usage_error(self, ws: Path) -> None:
        assert runner.invoke(app, ["cache", "show", "-w", str(ws)]).exit_code == 2

    def test_invalid_entry_exits_1(self, ws: Path, store: LocalFsCacheStore) -> None:
        key = key_of("junk")
        path = entry_file(ws, key)
        path.parent.mkdir(parents=True)
        path.write_text("[]", encoding="utf-8")
        result = cache(ws, "show", key[:8])
        assert result.exit_code == 1 and "is invalid (corrupt_entry)" in result.stderr

    def test_a_symlinked_entry_is_never_followed(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        real = add_entry(ws, store, "real")
        key = key_of("linked")
        link = entry_file(ws, key)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(entry_file(ws, real.key))
        assert cache(ws, "show", key[:8]).exit_code == 1  # not found: a link is not an entry


# =========================================================================== AC-4: rm
class TestRm:
    def test_removes_exactly_one_entry(self, ws: Path, store: LocalFsCacheStore) -> None:
        a = add_entry(ws, store, "a")
        b = add_entry(ws, store, "b")
        result = cache_json(ws, "rm", a.key[:10])
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_RM)
        assert doc["removed"] == [a.key] and doc["error"] is None
        assert not entry_file(ws, a.key).exists() and entry_file(ws, b.key).exists()
        assert blob_file(ws, a.outputs[0].sha256).exists()  # blobs go with `prune`

    def test_text_output(self, ws: Path, store: LocalFsCacheStore) -> None:
        a = add_entry(ws, store, "a")
        result = cache(ws, "rm", a.key)
        assert result.exit_code == 0 and f"removed {a.key}" in result.stdout

    def test_unknown_prefix_exits_1_and_changes_nothing(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        add_entry(ws, store, "a")
        before = tree(root_of(ws))
        result = cache_json(ws, "rm", "ffff")
        assert result.exit_code == 1
        doc = doc_of(result)
        assert doc["removed"] == [] and "no cache entry" in str(doc["error"])
        assert tree(root_of(ws)) == before
        assert cache(ws, "rm", "ffff").exit_code == 1

    def test_ambiguous_prefix_exits_1_and_removes_nothing(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        for name in ("one", "two"):
            add_entry(ws, store, name, key="abcd" + sha_of(name.encode())[:60])
        before = tree(root_of(ws))
        result = cache(ws, "rm", "abcd")
        assert result.exit_code == 1 and "ambiguous" in result.stderr
        assert tree(root_of(ws)) == before

    @pytest.mark.parametrize("prefix", ["XYZ", "../x", "abc", "ABCD", "../../etc/passwd", "a/b"])
    def test_malformed_prefix_exits_2(
        self, ws: Path, store: LocalFsCacheStore, prefix: str
    ) -> None:
        add_entry(ws, store, "a")
        before = tree(root_of(ws))
        result = cache_json(ws, "rm", prefix)
        assert result.exit_code == 2
        assert doc_of(result)["removed"] == []
        assert tree(root_of(ws)) == before

    def test_rm_run_is_not_an_option(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        result = cache(ws, "rm", "--run", "r1", "--task", "t")
        assert result.exit_code == 2
        assert "No such option" in result.output
        assert len(list((root_of(ws) / "entries" / "v1").rglob("*.json"))) == 1

    def test_removes_a_corrupt_entry_by_prefix(self, ws: Path, store: LocalFsCacheStore) -> None:
        key = key_of("junk")
        path = entry_file(ws, key)
        path.parent.mkdir(parents=True)
        path.write_text("garbage", encoding="utf-8")
        assert cache(ws, "rm", key[:12]).exit_code == 0
        assert not path.exists()

    def test_never_removes_a_symlink_or_directory_slot(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        victim = tmp_path / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        key = key_of("linked")
        slot = entry_file(ws, key)
        slot.parent.mkdir(parents=True)
        slot.symlink_to(victim)
        assert cache(ws, "rm", key).exit_code == 1
        assert victim.read_text(encoding="utf-8") == "keep" and slot.is_symlink()
        dir_key = key_of("dirslot")
        dir_slot = entry_file(ws, dir_key)
        dir_slot.parent.mkdir(parents=True, exist_ok=True)
        dir_slot.mkdir()
        assert cache(ws, "rm", dir_key).exit_code == 1 and dir_slot.is_dir()


# =========================================================================== AC-5: prune
class TestPrune:
    def test_counts_by_reason_and_bytes(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "fresh", age_days=1)
        old = add_entry(ws, store, "old", age_days=DEFAULT_CACHE_TTL_DAYS + 5)
        junk_key = key_of("junk")
        entry_file(ws, junk_key).parent.mkdir(parents=True, exist_ok=True)
        entry_file(ws, junk_key).write_text("{bad", encoding="utf-8")
        result = cache_json(ws, "prune")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_PRUNE)
        assert doc["removed_entries"] == {"expired": 1, "lru": 0, "invalid": 1}
        assert doc["removed_blobs"] == 1 and doc["dry_run"] is False
        assert doc["bytes_after"] < doc["bytes_before"]  # type: ignore[operator]
        assert not entry_file(ws, old.key).exists() and not entry_file(ws, junk_key).exists()
        assert entry_file(ws, key_of("fresh")).exists()

    def test_text_output(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "old", age_days=DEFAULT_CACHE_TTL_DAYS + 5)
        out = cache(ws, "prune").stdout
        assert "removed 1 entry (1 expired, 0 lru, 0 invalid)" in out and "bytes:" in out

    def test_older_than_zero_expires_every_entry(self, ws: Path, store: LocalFsCacheStore) -> None:
        for name in ("a", "b", "c"):
            add_entry(ws, store, name, age_days=0.5)
        doc = doc_of(cache_json(ws, "prune", "--older-than", "0"))
        assert doc["removed_entries"] == {"expired": 3, "lru": 0, "invalid": 0}
        assert not list((root_of(ws) / "entries" / "v1").rglob("*.json"))

    def test_older_than_overrides_the_config_ttl(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a", age_days=5)
        add_entry(ws, store, "b", age_days=1)
        doc = doc_of(cache_json(ws, "prune", "--older-than", "3"))
        assert doc["removed_entries"]["expired"] == 1  # type: ignore[index]
        assert entry_file(ws, key_of("b")).exists() and not entry_file(ws, key_of("a")).exists()

    @pytest.mark.parametrize(
        "args", [["--older-than", "-1"], ["--older-than=-5"], ["--max-bytes", "-1"]]
    )
    def test_negative_values_exit_2_and_delete_nothing(
        self, ws: Path, store: LocalFsCacheStore, args: list[str]
    ) -> None:
        add_entry(ws, store, "old", age_days=400)
        before = tree(root_of(ws))
        result = cache(ws, "prune", *args)
        assert result.exit_code == 2 and "ERROR:" in result.stderr
        as_json = cache_json(ws, "prune", *args)
        assert as_json.exit_code == 2 and doc_of(as_json)["schema"] == SCHEMA_PRUNE
        assert tree(root_of(ws)) == before

    @pytest.mark.parametrize(
        "args",
        [
            ["--older-than", "99999999999999999999"],
            ["--max-bytes", "99999999999999999999999"],
            ["--older-than", "36501"],
        ],
    )
    def test_absurdly_large_values_exit_2(self, ws: Path, args: list[str]) -> None:
        assert cache(ws, "prune", *args).exit_code == 2

    @pytest.mark.parametrize(
        "args", [["--older-than", "soon"], ["--max-bytes", "1.5"], ["--dry-run=maybe"]]
    )
    def test_non_numeric_values_are_usage_errors(self, ws: Path, args: list[str]) -> None:
        assert cache(ws, "prune", *args).exit_code == 2

    def test_max_bytes_override_evicts_least_recently_used_first(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        for i, name in enumerate(("a", "b", "c")):
            add_entry(ws, store, name, last_used_seconds_ago=100 * (3 - i))  # a is the oldest use
        doc = doc_of(cache_json(ws, "prune", "--max-bytes", "0"))
        assert doc["removed_entries"] == {"expired": 0, "lru": 3, "invalid": 0}

    def test_max_bytes_override_beats_the_config(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        write_config(tmp_path, "cache:\n  max_bytes: 1\n")  # would evict everything
        add_entry(ws, store, "a")
        doc = doc_of(cache_json(ws, "prune", "--max-bytes", str(DEFAULT_CACHE_MAX_BYTES)))
        assert doc["removed_entries"] == {"expired": 0, "lru": 0, "invalid": 0}
        assert entry_file(ws, key_of("a")).exists()

    def test_config_max_bytes_applies_without_the_flag(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        write_config(tmp_path, "cache:\n  max_bytes: 1\n  max_entry_bytes: 1\n")
        add_entry(ws, store, "a")
        add_entry(ws, store, "b")
        doc = doc_of(cache_json(ws, "prune"))
        assert doc["removed_entries"]["lru"] == 2  # type: ignore[index]

    def test_dry_run_deletes_nothing(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "old", age_days=DEFAULT_CACHE_TTL_DAYS + 5)
        add_entry(ws, store, "fresh")
        orphan = store.put_blob(io.BytesIO(b"orphan"), max_bytes=BLOB_BOUND)
        stamp = NOW.timestamp() - OLD_SECONDS
        os.utime(blob_file(ws, orphan.sha256), (stamp, stamp))
        before = tree(root_of(ws))
        result = cache_json(ws, "prune", "--dry-run")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_PRUNE)
        assert doc["dry_run"] is True and doc["removed_entries"]["expired"] == 1  # type: ignore[index]
        assert (
            doc["removed_blobs"] >= 2
        )  # the old entry's blob and the orphan  # type: ignore[operator]
        assert doc["bytes_after"] < doc["bytes_before"]  # type: ignore[operator]
        assert tree(root_of(ws)) == before
        text = cache(ws, "prune", "--dry-run")
        assert "would remove 1 entry" in text.stdout and "nothing deleted" in text.stdout
        assert tree(root_of(ws)) == before

    def test_empty_and_missing_stores_prune_cleanly(self, ws: Path) -> None:
        doc = doc_of(cache_json(ws, "prune"))
        validate(doc, SCHEMA_PRUNE)
        assert doc["removed_entries"] == {"expired": 0, "lru": 0, "invalid": 0}
        assert not root_of(ws).exists()

    def test_sweeps_stale_temp_files_in_the_cache_tmp_dir(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        add_entry(ws, store, "a")
        tmp_file = root_of(ws) / "tmp" / "left.tmp"
        tmp_file.write_bytes(b"x")
        stamp = NOW.timestamp() - OLD_SECONDS
        os.utime(tmp_file, (stamp, stamp))
        assert doc_of(cache_json(ws, "prune"))["removed_tmp"] == 1
        assert not tmp_file.exists()

    def test_an_unsafe_cache_root_is_an_error_not_a_deletion(
        self, ws: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        (outside / "entries" / "v1").mkdir(parents=True)
        (ws / ".orchestrator").mkdir()
        root_of(ws).symlink_to(outside)
        marker = outside / "entries" / "v1" / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        result = cache_json(ws, "prune", "--older-than", "0")
        assert result.exit_code == 1
        assert doc_of(result)["error"]
        assert marker.read_text(encoding="utf-8") == "keep"


# =========================================================================== restore-leftover sweep
class TestPruneRestoreLeftoverSweep:
    """`ao cache prune` also removes STALE restore staging files (G1a SEC-19, G1b S-1), but only
    regular files with the exact staging names, directly inside the output directories of valid
    entries, never a sole-copy backup, never through a link, never outside the workspace."""

    TMP = ".ao-result-cache-111-222-aaaa.tmp"
    BAK = ".ao-result-cache-111-222-bbbb.tmp.bak"

    @pytest.fixture
    def later(self, monkeypatch: pytest.MonkeyPatch) -> datetime:
        stamp = datetime.now(UTC) + timedelta(seconds=2 * TMP_SWEEP_GRACE_SECONDS)
        monkeypatch.setattr(cache_cli, "_now", lambda: stamp)
        return stamp

    def _entry_in_out_dir(self, ws: Path, store: LocalFsCacheStore) -> Path:
        add_entry(ws, store, "a", files={"out/a.md": b"alpha"})
        out = ws / "out"
        out.mkdir(exist_ok=True)
        return out

    def test_stale_staging_file_is_removed(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        (out / self.TMP).write_bytes(b"cached copy")
        (out / "a.md").write_text("user file", encoding="utf-8")
        doc = doc_of(cache_json(ws, "prune"))
        validate(doc, SCHEMA_PRUNE)
        assert doc["removed_restore_tmp"] == 1
        assert not (out / self.TMP).exists()
        assert (out / "a.md").read_text(encoding="utf-8") == "user file"

    def test_text_mentions_the_sweep(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        (out / self.TMP).write_bytes(b"x")
        assert (
            "removed 1 stale restore temp file(s) in output directories"
            in cache(ws, "prune").stdout
        )

    def test_a_young_staging_file_may_belong_to_a_live_restore_and_is_kept(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        (out / self.TMP).write_bytes(b"in flight")
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 0
        assert (out / self.TMP).exists()

    def test_backup_hard_link_is_removed_but_a_sole_copy_backup_is_kept(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        dest = out / "a.md"
        dest.write_text("old content", encoding="utf-8")
        os.link(dest, out / self.BAK)  # restore had not replaced the destination yet
        sole = out / ".ao-result-cache-9-9-cccc.tmp.bak"
        sole.write_text("the only copy of replaced content", encoding="utf-8")
        doc = doc_of(cache_json(ws, "prune"))
        assert doc["removed_restore_tmp"] == 1 and doc["kept_restore_backups"] == 1
        assert not (out / self.BAK).exists() and dest.read_text(encoding="utf-8") == "old content"
        assert sole.exists()
        assert "kept 1 sole-copy backup(s)" in cache(ws, "prune").stdout

    def test_only_exact_staging_names_are_touched(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        keep = [
            ".ao-result-cache-x.txt",
            "ao-result-cache-1.tmp",
            ".ao-result-cache-1.tmp.bak.old",
            "notes.tmp",
            ".ao-result-cache-.bak",
        ]
        for name in keep:
            (out / name).write_text("user data", encoding="utf-8")
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 0
        assert all((out / name).exists() for name in keep)

    def test_symlinks_and_directories_with_a_staging_name_are_kept(
        self, ws: Path, store: LocalFsCacheStore, later: datetime, tmp_path: Path
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        victim = tmp_path / "victim.txt"
        victim.write_text("precious", encoding="utf-8")
        (out / self.TMP).symlink_to(victim)
        (out / self.BAK).mkdir()
        (out / self.BAK / "inner.txt").write_text("x", encoding="utf-8")
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 0
        assert (out / self.TMP).is_symlink() and victim.read_text(encoding="utf-8") == "precious"
        assert (out / self.BAK / "inner.txt").exists()

    def test_other_directories_and_subdirectories_are_not_swept(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        (ws / "docs").mkdir()
        (ws / "docs" / self.TMP).write_bytes(b"x")  # not an output directory of any entry
        (out / "deeper").mkdir()
        (out / "deeper" / self.TMP).write_bytes(b"x")  # only direct children are swept
        (ws / self.TMP).write_bytes(b"x")  # the workspace root is not an output directory here
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 0
        assert (ws / "docs" / self.TMP).exists() and (out / "deeper" / self.TMP).exists()
        assert (ws / self.TMP).exists()

    def test_a_workspace_root_output_sweeps_the_root(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        add_entry(ws, store, "r", files={"top.md": b"top"})
        (ws / self.TMP).write_bytes(b"x")
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 1

    def test_dry_run_counts_but_removes_nothing(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        out = self._entry_in_out_dir(ws, store)
        (out / self.TMP).write_bytes(b"x")
        doc = doc_of(cache_json(ws, "prune", "--dry-run"))
        assert doc["removed_restore_tmp"] == 1 and (out / self.TMP).exists()

    def test_the_sweep_sees_directories_of_entries_the_same_prune_evicts(
        self, ws: Path, store: LocalFsCacheStore, later: datetime
    ) -> None:
        add_entry(ws, store, "gone", age_days=DEFAULT_CACHE_TTL_DAYS + 9, files={"gone/x.md": b"x"})
        (ws / "gone").mkdir()
        (ws / "gone" / self.TMP).write_bytes(b"x")
        doc = doc_of(cache_json(ws, "prune"))
        assert doc["removed_entries"]["expired"] == 1 and doc["removed_restore_tmp"] == 1  # type: ignore[index]

    def test_a_symlinked_output_directory_is_never_followed(
        self, ws: Path, store: LocalFsCacheStore, later: datetime, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / self.TMP).write_bytes(b"x")
        (ws / "out").symlink_to(elsewhere)
        add_entry(ws, store, "a", files={"out/a.md": b"alpha"})
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 0
        assert (elsewhere / self.TMP).exists()

    def test_hostile_output_paths_in_an_entry_are_never_scanned(
        self, ws: Path, store: LocalFsCacheStore, later: datetime, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / self.TMP).write_bytes(b"x")
        (ws / ".git").mkdir()
        (ws / ".git" / self.TMP).write_bytes(b"x")
        hostile = {
            "../outside/a.md": b"1",
            f"{outside}/b.md": b"2",
            ".git/c.md": b"3",
            "out/../../outside/d.md": b"4",
            "./e.md": b"5",
        }
        add_entry(ws, store, "evil", files=hostile)
        assert doc_of(cache_json(ws, "prune"))["removed_restore_tmp"] == 0
        assert (outside / self.TMP).exists() and (ws / ".git" / self.TMP).exists()


# =========================================================================== AC-6: clear
class TestClear:
    def test_yes_empties_the_store(self, ws: Path, store: LocalFsCacheStore) -> None:
        a = add_entry(ws, store, "a")
        add_entry(ws, store, "b")
        result = cache_json(ws, "clear", "--yes")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_CLEAR)
        assert doc["removed_entries"] == 2 and doc["removed_blobs"] == 2
        assert doc["bytes_freed"] > 0  # type: ignore[operator]
        assert not entry_file(ws, a.key).exists()
        assert not any(root_of(ws).glob("trash-*"))
        assert doc_of(cache_json(ws, "stats"))["entries"] == 0

    def test_text_output(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        out = cache(ws, "clear", "--yes").stdout
        assert "cleared 1 entry, 1 blob(s)" in out

    def test_without_yes_and_without_a_tty_it_refuses(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        add_entry(ws, store, "a")
        before = tree(root_of(ws))
        result = cache(ws, "clear")
        assert result.exit_code == 1
        assert "refusing to clear without --yes" in result.stderr
        assert tree(root_of(ws)) == before
        as_json = cache_json(ws, "clear")
        assert as_json.exit_code == 1
        doc = doc_of(as_json)
        validate(doc, SCHEMA_CLEAR)
        assert "refusing to clear without --yes" in str(doc["error"])
        assert tree(root_of(ws)) == before

    def test_interactive_confirmation(
        self, ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli_ops, "_stdin_is_tty", lambda: True)
        add_entry(ws, store, "a")
        declined = cache(ws, "clear", input_text="n\n")
        assert declined.exit_code == 1 and "aborted" in declined.stderr
        assert entry_file(ws, key_of("a")).exists()
        accepted = cache(ws, "clear", input_text="y\n")
        assert accepted.exit_code == 0
        assert not entry_file(ws, key_of("a")).exists()

    def test_json_never_prompts_even_on_a_tty(
        self, ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli_ops, "_stdin_is_tty", lambda: True)
        add_entry(ws, store, "a")
        result = cache_json(ws, "clear", input_text="y\n")
        assert result.exit_code == 1 and doc_of(result)["error"]
        assert entry_file(ws, key_of("a")).exists()

    def test_a_failed_removal_exits_1_with_an_error_document(
        self, ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_entry(ws, store, "a")

        def boom(_path: str) -> None:
            raise OSError("disk on fire")

        monkeypatch.setattr("agent_orchestrator.cache.store.shutil.rmtree", boom)
        result = cache_json(ws, "clear", "--yes")
        assert result.exit_code == 1
        doc = doc_of(result)
        validate(doc, SCHEMA_CLEAR)
        assert "store_error" in str(doc["error"])
        text = cache(ws, "clear", "--yes")
        assert text.exit_code == 1 and "ERROR: CacheError: store_error" in text.stderr

    def test_empty_and_missing_stores_clear_cleanly(self, ws: Path) -> None:
        doc = doc_of(cache_json(ws, "clear", "--yes"))
        validate(doc, SCHEMA_CLEAR)
        assert doc["removed_entries"] == 0 and not root_of(ws).exists()

    def test_clear_does_not_follow_a_symlinked_root(self, ws: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        (outside / "entries").mkdir(parents=True)
        marker = outside / "entries" / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        (ws / ".orchestrator").mkdir()
        root_of(ws).symlink_to(outside)
        result = cache_json(ws, "clear", "--yes")
        assert result.exit_code == 1 and doc_of(result)["error"]
        assert marker.read_text(encoding="utf-8") == "keep"

    def test_only_the_cache_is_cleared_not_the_rest_of_orchestrator_state(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        add_entry(ws, store, "a")
        runs = ws / ".orchestrator" / "runs"
        runs.mkdir()
        (runs / "state.json").write_text("{}", encoding="utf-8")
        assert cache(ws, "clear", "--yes").exit_code == 0
        assert (runs / "state.json").exists()


# =========================================================================== AC-7: verify
class TestVerify:
    def test_clean_store_exits_0(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        result = cache_json(ws, "verify")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_VERIFY)
        assert doc["ok"] is True and doc["problems"] == []
        assert doc["entries_checked"] == 1 and doc["blobs_checked"] == 1
        assert "verify: OK (1 entries, 1 blobs checked)" in cache(ws, "verify").stdout

    def test_missing_store_is_clean(self, ws: Path) -> None:
        doc = doc_of(cache_json(ws, "verify"))
        validate(doc, SCHEMA_VERIFY)
        assert doc["ok"] is True and not root_of(ws).exists()

    def test_orphan_blobs_and_foreign_versions_alone_are_clean(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        add_entry(ws, store, "a")
        store.put_blob(io.BytesIO(b"nobody references me"), max_bytes=BLOB_BOUND)
        (root_of(ws) / "entries" / "v2" / "ab").mkdir(parents=True)
        result = cache_json(ws, "verify")
        assert result.exit_code == 0
        doc = doc_of(result)
        validate(doc, SCHEMA_VERIFY)
        kinds = {p["kind"] for p in doc["problems"]}  # type: ignore[attr-defined]
        assert kinds == {"orphan_blob", "foreign_version"} and doc["ok"] is True
        assert cache(ws, "verify").exit_code == 0

    def test_corrupt_entry_exits_1(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        key = key_of("junk")
        entry_file(ws, key).parent.mkdir(parents=True, exist_ok=True)
        entry_file(ws, key).write_text("{nope", encoding="utf-8")
        result = cache_json(ws, "verify")
        assert result.exit_code == 1
        doc = doc_of(result)
        validate(doc, SCHEMA_VERIFY)
        assert doc["ok"] is False
        assert {
            "kind": "corrupt_entry",
            "key": key,
            "blob": None,
            "detail": "corrupt_entry",
        } in doc["problems"]  # type: ignore[operator]
        text = cache(ws, "verify")
        assert (
            text.exit_code == 1
            and "PROBLEMS FOUND" in text.stdout
            and "corrupt_entry" in text.stdout
        )

    def test_key_mismatch_exits_1(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "a")
        wrong = key_of("other")
        target = entry_file(ws, wrong)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(entry_file(ws, entry.key).read_bytes())  # content key != file name
        result = cache_json(ws, "verify")
        assert result.exit_code == 1
        assert any(p["kind"] == "key_mismatch" for p in doc_of(result)["problems"])  # type: ignore[attr-defined]

    def test_missing_blob_exits_1(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "a")
        blob_file(ws, entry.outputs[0].sha256).unlink()
        result = cache_json(ws, "verify")
        assert result.exit_code == 1
        problem = doc_of(result)["problems"][0]  # type: ignore[index]
        assert problem["kind"] == "missing_blob" and problem["blob"] == entry.outputs[0].sha256

    def test_corrupt_blob_exits_1(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "a")
        path = blob_file(ws, entry.outputs[0].sha256)
        path.write_bytes(b"X" * path.stat().st_size)  # same size, wrong content
        result = cache_json(ws, "verify")
        assert result.exit_code == 1
        assert any(p["kind"] == "corrupt_blob" for p in doc_of(result)["problems"])  # type: ignore[attr-defined]

    def test_a_symlinked_entry_exits_1(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        entry = add_entry(ws, store, "a")
        link = entry_file(ws, key_of("link"))
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(entry_file(ws, entry.key))
        result = cache_json(ws, "verify")
        assert result.exit_code == 1
        assert any(p["kind"] == "symlink" for p in doc_of(result)["problems"])  # type: ignore[attr-defined]

    def test_verify_deletes_and_modifies_nothing(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = add_entry(ws, store, "a")
        add_entry(ws, store, "b")
        blob_file(ws, entry.outputs[0].sha256).write_bytes(b"corrupted!")
        junk = entry_file(ws, key_of("junk"))
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("garbage", encoding="utf-8")
        (root_of(ws) / "entries" / "v2").mkdir()
        orphan = store.put_blob(io.BytesIO(b"orphan"), max_bytes=BLOB_BOUND)
        stamp = NOW.timestamp() - OLD_SECONDS
        os.utime(blob_file(ws, orphan.sha256), (stamp, stamp))  # prune WOULD sweep this
        before = tree(root_of(ws))
        assert cache(ws, "verify").exit_code == 1
        assert cache_json(ws, "verify").exit_code == 1
        assert tree(root_of(ws)) == before

    def test_repair_is_not_an_option(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        result = cache(ws, "verify", "--repair")
        assert result.exit_code == 2 and "No such option" in result.output

    def test_unsafe_root_exits_1_with_a_document(self, ws: Path, tmp_path: Path) -> None:
        (ws / ".orchestrator").mkdir()
        root_of(ws).symlink_to(tmp_path)
        result = cache_json(ws, "verify")
        assert result.exit_code == 1
        doc = doc_of(result)
        assert doc["ok"] is False and doc["problems"] == [] and doc["error"]


# =========================================================================== AC-8: M-15
class TestControlCharactersAreNeverPrinted:
    HOSTILE_TASK = f"w{ESC}[31mred{BEL}{BIDI_OVERRIDE}gnp{ZERO_WIDTH}.exe"

    def _plant(self, ws: Path, store: LocalFsCacheStore) -> CacheEntry:
        return add_entry(ws, store, "evil", task_id=self.HOSTILE_TASK)

    def test_ls_text(self, ws: Path, store: LocalFsCacheStore) -> None:
        self._plant(ws, store)
        out = cache(ws, "ls").stdout
        assert not has_control_chars(out)
        assert BIDI_OVERRIDE not in out and ZERO_WIDTH not in out
        assert "w[31mredgnp.exe/" in out  # the visible remainder survives

    def test_show_text(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = self._plant(ws, store)
        out = cache(ws, "show", entry.key[:10]).stdout
        assert not has_control_chars(out) and BIDI_OVERRIDE not in out
        assert "task=w[31mredgnp.exe" in out

    def test_json_escapes_instead_of_printing(self, ws: Path, store: LocalFsCacheStore) -> None:
        entry = self._plant(ws, store)
        for args in (["ls"], ["show", entry.key[:10]]):
            raw = cache_json(ws, *args).stdout
            assert ESC not in raw and BEL not in raw and BIDI_OVERRIDE not in raw
            assert "\\u001b" in raw
        assert doc_of(cache_json(ws, "ls"))["entries"][0]["source"]["task_id"] == self.HOSTILE_TASK  # type: ignore[index]

    def test_hostile_file_names_in_the_store(self, ws: Path, store: LocalFsCacheStore) -> None:
        add_entry(ws, store, "a")
        shard = root_of(ws) / "entries" / "v1" / "ab"
        shard.mkdir(parents=True, exist_ok=True)
        (shard / f"{ESC}[2Jboom{BEL}.json").write_text("x", encoding="utf-8")
        (root_of(ws) / "entries" / f"v{ESC}[31m9").mkdir()
        (root_of(ws) / "blobs").mkdir(exist_ok=True)
        (root_of(ws) / "blobs" / f"{ESC}[1mjunk").write_text("x", encoding="utf-8")
        for args in (["verify"], ["stats"], ["ls"]):
            out = cache(ws, *args)
            assert not has_control_chars(out.stdout + out.stderr), (args, out.output)
        verify = cache(ws, "verify").stdout
        assert "unexpected_file" in verify and "[2Jboom" in verify

    def test_error_messages_never_echo_control_characters(self, ws: Path) -> None:
        for args in (
            ["show", f"{ESC}[31m{BEL}zz"],
            ["rm", f"..{ESC}"],
            ["ls", "--sort", f"{ESC}c{BEL}"],
        ):
            result = cache(ws, *args)
            assert result.exit_code == 2
            assert not has_control_chars(result.stderr), result.stderr

    def test_a_very_long_hostile_argument_is_clipped(self, ws: Path) -> None:
        result = cache(ws, "ls", "--sort", "x" * 10_000)
        assert result.exit_code == 2 and len(result.stderr) < 400


# =========================================================================== AC-9: workspace
class TestWorkspaceResolution:
    @pytest.fixture
    def three(self, tmp_path: Path) -> dict[str, Path]:
        """Workspaces with 1 (flag), 2 (env) and 3 (config discovery) entries."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spaces = {
            "flag": tmp_path / "w-flag",
            "env": tmp_path / "w-env",
            "disc": tmp_path / "w-disc",
        }
        for count, (name, path) in enumerate(spaces.items(), start=1):
            path.mkdir()
            ws_store = LocalFsCacheStore.for_workspace(
                path, max_bytes=DEFAULT_CACHE_MAX_BYTES, ttl_days=30
            )
            for i in range(count):
                add_entry(path, ws_store, f"{name}-{i}")
        write_fixture(spec_dir, workspace=spaces["disc"])
        write_config(
            tmp_path,
            f"workflow: {spec_dir / 'wf.json'}\nreposets: {spec_dir / 'rs.json'}\n"
            f"agents: {spec_dir / 'ag.json'}\n",
        )
        return spaces

    def _entries(self, *args: str, env: dict[str, str] | None = None) -> tuple[int, int]:
        result = runner.invoke(app, ["cache", "stats", "--json", *args], env=env)
        assert result.exit_code == 0, result.output
        doc = json.loads(result.stdout)
        return int(doc["entries"]), doc["root"]

    def test_flag_beats_env_beats_discovery(self, three: dict[str, Path]) -> None:
        env = {"AO_WORKSPACE_ROOT": str(three["env"])}
        assert self._entries("-w", str(three["flag"]), env=env)[0] == 1  # short flag
        assert self._entries("--workspace", str(three["flag"]), env=env)[0] == 1
        assert self._entries(env=env)[0] == 2  # env beats discovery
        count, root = self._entries()
        assert count == 3 and root == str(three["disc"] / ".orchestrator" / "cache")

    def test_unresolvable_workspace_exits_1(self) -> None:
        result = runner.invoke(app, ["cache", "stats"])
        assert result.exit_code == 1 and "ERROR:" in result.stderr
        as_json = runner.invoke(app, ["cache", "stats", "--json"])
        assert as_json.exit_code == 1
        assert doc_of(as_json)["schema"] == SCHEMA_STATS and doc_of(as_json)["error"]

    def test_a_workspace_that_is_a_file_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        file_path = tmp_path / "plainfile"
        file_path.write_text("x", encoding="utf-8")
        result = cache_json(file_path, "ls")
        assert result.exit_code == 1 and doc_of(result)["error"]
        assert "Traceback" not in result.output


# =========================================================================== cross-cutting
class TestEdgesAndRaces:
    def test_the_real_clock_is_timezone_aware_utc(self) -> None:
        moment = _REAL_NOW()
        assert moment.tzinfo is not None and moment.utcoffset() == timedelta(0)
        assert abs((datetime.now(UTC) - moment).total_seconds()) < 60

    def test_a_forged_out_of_range_mtime_does_not_crash_a_listing(
        self, ws: Path, store: LocalFsCacheStore
    ) -> None:
        # a real filesystem clamps what `utime` can store, so the guard is driven directly
        for forged in (1e30, -1e30, float("inf"), float("nan")):
            assert cli_ops._iso_mtime(forged) == "unknown"
        entry = add_entry(ws, store, "a")
        os.utime(entry_file(ws, entry.key), (10**11, 10**11))  # year 5138: representable
        assert cache(ws, "ls").exit_code == 0

    def test_an_oversized_shard_is_refused_not_scanned_forever(
        self, ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for i in range(4):
            add_entry(ws, store, f"n{i}", key="abcd" + sha_of(str(i).encode())[:60])
        monkeypatch.setattr(cli_ops, "CLI_MAX_DIR_ITEMS", 2)
        result = cache(ws, "show", "abcd")
        assert result.exit_code == 1 and "too many files in one entry shard" in result.stderr

    def test_an_entry_removed_between_resolution_and_use_is_not_found(
        self, ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = add_entry(ws, store, "a")
        monkeypatch.setattr(LocalFsCacheStore, "get_entry", lambda self, key: None)
        assert cache(ws, "show", entry.key[:8]).exit_code == 1
        monkeypatch.setattr(LocalFsCacheStore, "delete_entry", lambda self, key: False)
        assert cache(ws, "rm", entry.key[:8]).exit_code == 1

    def test_blobs_that_are_links_are_reported_absent(
        self, ws: Path, store: LocalFsCacheStore, tmp_path: Path
    ) -> None:
        entry = add_entry(ws, store, "a")
        blob = blob_file(ws, entry.outputs[0].sha256)
        blob.unlink()
        blob.symlink_to(tmp_path / "elsewhere")
        doc = doc_of(cache_json(ws, "show", entry.key))
        assert doc["blobs"][0]["present"] is False  # type: ignore[index]


class TestCommandsWorkWhateverTheRunMode:
    @pytest.mark.parametrize(
        "config",
        ["cache:\n  enabled: false\n", "cache:\n  enabled: true\n  mode: shadow\n", ""],
    )
    def test_every_command_works_with_the_cache_off_or_on(
        self,
        ws: Path,
        store: LocalFsCacheStore,
        tmp_path: Path,
        config: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if config:
            write_config(tmp_path, config)
        monkeypatch.setenv("AO_CACHE", "0")
        entry = add_entry(ws, store, "a")
        assert doc_of(cache_json(ws, "ls"))["entries"][0]["key"] == entry.key  # type: ignore[index]
        assert doc_of(cache_json(ws, "stats"))["entries"] == 1
        assert cache(ws, "show", entry.key[:8]).exit_code == 0
        assert cache(ws, "verify").exit_code == 0
        assert cache(ws, "prune", "--dry-run").exit_code == 0
        assert cache(ws, "rm", entry.key[:8]).exit_code == 0
        assert cache(ws, "clear", "--yes").exit_code == 0

    def test_a_malformed_project_config_exits_1_with_one_document(
        self, ws: Path, tmp_path: Path
    ) -> None:
        write_config(tmp_path, "cache:\n  ttl_days: 0\n")
        result = cache_json(ws, "stats")
        assert result.exit_code == 1
        doc = doc_of(result)
        assert doc["schema"] == SCHEMA_STATS and "invalid project config" in str(doc["error"])
        assert cache(ws, "stats").exit_code == 1

    def test_help_lists_exactly_the_mvp_commands(self) -> None:
        result = runner.invoke(app, ["cache", "--help"])
        assert result.exit_code == 0
        for command in ("ls", "stats", "show", "rm", "prune", "clear", "verify"):
            assert re.search(rf"^\s*│?\s*{command}\s", result.output, re.MULTILINE), command
        for deferred in ("refresh", "repair"):
            assert deferred not in result.output
        assert "RESULT cache" in " ".join(result.output.split())

    def test_cache_without_a_subcommand_prints_help(self) -> None:
        result = runner.invoke(app, ["cache"])
        assert "Usage:" in result.output

    @pytest.mark.parametrize(
        "command", ["ls", "stats", "show abcd", "rm abcd", "prune", "clear", "verify"]
    )
    def test_every_command_accepts_workspace_json_and_help(self, command: str) -> None:
        result = runner.invoke(app, ["cache", *command.split(), "--help"])
        assert result.exit_code == 0
        assert "--workspace" in result.output and "--json" in result.output

    def test_importing_the_cli_does_not_load_the_store(self) -> None:
        heavy = ("cli_ops", "store", "restore_sweep", "types", "report", "coordinator")
        code = (
            "import sys; import agent_orchestrator.cli; "
            f"print([m for m in {heavy!r} if f'agent_orchestrator.cache.{{m}}' in sys.modules])"
        )
        out = subprocess.run(  # noqa: S603 - fixed argv, the current interpreter
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
        )
        assert out.stdout.strip() == "[]", out.stdout
