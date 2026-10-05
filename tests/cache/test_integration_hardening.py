"""T-JCOAsq Part 2: integration hardening of the result cache (HLD 18.1, test ids I-*).

Covers I-5b, I-9, I-10, I-11, I-12, I-13, I-14, I-15, I-16, I-17, I-19, I-20, I-23, I-25, I-26.
(I-24, the Rev 2 `refresh` test, is retired.) The adversarial ids live in `test_adversarial.py`.

Every test drives a REAL `Orchestrator` + `ResultCache` + `LocalFsCacheStore` in a temp workspace
through `_hardening_rig.Rig`. The assertions are on observable behaviour: dispatch counts through
the counting executor (`CostlyFakeExecutor`: `FakeExecutor` reports no cost), restored bytes,
`status.json`, the structured events of the run's `run.log` and the cache directory itself.
Clocks are deterministic (a ticking clock; TTL tests jump it explicitly); there is no sleeping.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.report import (
    current_records,
    run_block,
    usage_counters,
)
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import CacheEntry
from agent_orchestrator.models import RunState, WorkflowDefaults
from agent_orchestrator.outcomes import _settle_reason
from agent_orchestrator.usage import aggregate_usage
from tests.cache._hardening_rig import (
    HookedExecutor,
    Rig,
    SteppingClock,
    git,
)
from tests.cache.test_engine_result_cache import (
    COST_USD,
    IN_TOKENS,
    OUT_TOKENS,
    CostlyFakeExecutor,
    SpyCache,
    single,
    task,
    workflow,
)

BARRIER_TIMEOUT_SECONDS = 10.0
JOIN_TIMEOUT_SECONDS = 60.0
PARALLEL_WIDTH = 3
BODY = b"fake output for a\n"
DAY = timedelta(days=1)
LATE = datetime(2026, 10, 6, tzinfo=UTC)
REAL_PUT_ENTRY = LocalFsCacheStore.put_entry  # captured before any test patches it


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each module controls `AO_CACHE` itself (conftest.py is frozen by the NFR-2 gate)."""
    monkeypatch.delenv(c.ENV_CACHE, raising=False)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    ws = tmp_path / "ws"
    ws.mkdir()
    return Rig(ws)


def entry_of(rig: Rig, key: str | None = None) -> CacheEntry:
    """The parsed entry (the store's own parse boundary), for the only entry or *key*."""
    return rig.cache_store.get_entry(key or rig.only_key()) or pytest.fail("no entry")


def hit_records(state: RunState) -> dict[str, Any]:
    return {t: r for t, r in state.result_cache.items() if r.outcome == "hit"}


# ======================================================================================= I-5b
class TestStoreAtMaxParallel:
    """G1b carry-over (rev S-2): a miss -> pending -> STORE at `max_parallel > 1`."""

    def test_i5b_independent_tasks_all_store_then_all_hit_with_zero_dispatches(
        self, rig: Rig
    ) -> None:
        names = [f"t{i}" for i in range(4)]
        outs = [f"out/{n}.txt" for n in names]
        wf = workflow([task(n, outputs=[f"out/{n}.txt"]) for n in names])
        # t0 and t1 only pass their barrier if they are IN FLIGHT AT THE SAME TIME: a serial
        # scheduler would break the barrier and fail the task (non-vacuous concurrency proof).
        barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_SECONDS)
        first = HookedExecutor(
            hooks={"t0": lambda ctx: barrier.wait(), "t1": lambda ctx: barrier.wait()}
        )

        s1 = rig.run(wf, first, max_parallel=PARALLEL_WIDTH)

        assert s1.status == "succeeded"
        assert sorted(first.executed) == names
        main = threading.current_thread().name
        assert all(first.threads[n] != main for n in names)  # dispatched on worker threads
        assert {n: s1.result_cache[n].outcome for n in names} == dict.fromkeys(names, "miss")
        assert {n: s1.result_cache[n].stored for n in names} == dict.fromkeys(names, True)
        assert len(rig.entry_files()) == 4 and len(rig.blob_files()) == 4
        assert len(rig.log_events(s1, "cache.store")) == 4
        bodies = {o: rig.out(o).read_bytes() for o in outs}
        rig.delete_outputs(*outs)

        second = HookedExecutor()
        s2 = rig.run(wf, second, max_parallel=PARALLEL_WIDTH)

        assert second.executed == []  # zero dispatches
        assert {n: s2.result_cache[n].outcome for n in names} == dict.fromkeys(names, "hit")
        assert {o: rig.out(o).read_bytes() for o in outs} == bodies
        assert all(s2.tasks[n].status == "succeeded" and s2.tasks[n].attempts == 0 for n in names)
        assert len(rig.log_events(s2, "cache.hit")) == 4
        block = rig.status_json(s2)["result_cache"]
        assert (block["hits"], block["misses"]) == (4, 0)
        assert block["saved_cost_usd"] == pytest.approx(4 * COST_USD)

    def test_i5b_concurrent_stores_of_the_same_key_leave_one_valid_entry(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Two runs of the same workflow over one workspace both miss, both dispatch and then BOTH
        enter `put_entry` for the same key at the same time (a barrier inside the wrapper proves
        the overlap). Both succeed; one entry file results and it is valid; a third run hits."""
        wf = single()
        lookups_done = threading.Barrier(2, timeout=BARRIER_TIMEOUT_SECONDS)
        writes_done = threading.Barrier(2, timeout=BARRIER_TIMEOUT_SECONDS)
        # Both lookups precede both dispatches, and both output writes finish before either
        # settle captures: no run can see the other's half-written output.
        executors = [
            HookedExecutor(
                hooks={"a": lambda ctx: lookups_done.wait()},
                after={"a": lambda ctx: writes_done.wait()},
            )
            for _ in range(2)
        ]
        in_put = threading.Barrier(2, timeout=BARRIER_TIMEOUT_SECONDS)
        real_put = LocalFsCacheStore.put_entry
        overlapped: list[str] = []

        def put_together(self: LocalFsCacheStore, entry: CacheEntry) -> int:
            in_put.wait()  # BOTH writers are inside put_entry before either proceeds
            overlapped.append(entry.key)
            return real_put(self, entry)

        monkeypatch.setattr(LocalFsCacheStore, "put_entry", put_together)
        states: list[RunState | BaseException] = [RuntimeError("did not run")] * 2
        caches = [rig.cache, SpyCache(rig._coordinator())]  # two coordinators, like two processes

        def go(i: int) -> None:
            try:
                states[i] = rig.run(wf, executors[i], cache=caches[i])
            except BaseException as exc:  # reported by the assertion below
                states[i] = exc

        threads = [threading.Thread(target=go, args=(i,), name=f"run-{i}") for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(JOIN_TIMEOUT_SECONDS)
        assert not any(t.is_alive() for t in threads)
        assert all(isinstance(s, RunState) for s in states), states
        done = [s for s in states if isinstance(s, RunState)]

        assert [s.status for s in done] == ["succeeded", "succeeded"]
        assert [len(e.executed) for e in executors] == [1, 1]
        assert all(s.result_cache["a"].outcome == "miss" for s in done)
        assert all(s.result_cache["a"].stored is True for s in done)
        assert len(overlapped) == 2 and len(set(overlapped)) == 1  # the same key, twice, together
        key = rig.only_key()
        assert key == overlapped[0] and len(rig.blob_files()) == 1
        assert rig.cache_store.verify().ok
        monkeypatch.setattr(LocalFsCacheStore, "put_entry", REAL_PUT_ENTRY)
        body = rig.out("out/a.txt").read_bytes()
        rig.delete_outputs("out/a.txt")

        third = HookedExecutor()
        s3 = rig.run(wf, third)

        assert third.executed == [] and s3.result_cache["a"].hit
        assert rig.out("out/a.txt").read_bytes() == body
        assert entry_of(rig, key).key == key


# ======================================================================================== I-9
def test_i9_cache_off_resume_after_a_deleted_output_hit_redispatches_and_the_record_is_stale(
    rig: Rig,
) -> None:
    """FR-10 / D14: a hit, its output deleted, `resume` with the cache OFF: the task is
    re-dispatched and the old hit record, now stale, is omitted from every read surface."""
    wf = single()
    rig.run(wf, CostlyFakeExecutor())
    rig.delete_outputs("out/a.txt")
    s2 = rig.run(wf, CostlyFakeExecutor())
    assert s2.result_cache["a"].hit and current_records(s2).keys() == {"a"}  # current while fresh
    rig.delete_outputs("out/a.txt")
    rig.rs_store.prepare_resume(s2, wf)
    assert s2.tasks["a"].status == "pending"

    off = CostlyFakeExecutor()
    s3 = rig.run(wf, off, run_state=s2, cache=None)  # cache OFF

    assert off.executed == ["a"] and s3.tasks["a"].status == "succeeded"  # really re-dispatched
    assert rig.out("out/a.txt").read_bytes() == BODY
    stale = s3.result_cache["a"]  # the record is still in state.json ...
    assert stale.outcome == "hit" and stale.dispatch_cycle != s3.tasks["a"].dispatch_cycle
    # ... but every surface ignores it (derived staleness, D14):
    assert current_records(s3) == {} and run_block(s3) is None and usage_counters(s3) is None
    status = rig.status_json(s3)
    assert "result_cache" not in status
    assert all("result_cache" not in t for t in status["tasks"])
    assert _settle_reason(s3.tasks["a"], state=s3, tid="a") == "dispatched"
    report = aggregate_usage([s3], rig.store)
    assert report.result_cache is None
    assert sum(g.tasks for g in report.groups) == 1  # counted as the dispatch it really was
    assert sum(g.succeeded for g in report.groups) == 1


# ======================================================================================= I-10
class TestPurityGuards:
    """FR-6: only a clean final success is stored. Each guard names its reason; each test has a
    clean positive control in the same workflow shape so the skip cannot be an accident."""

    @staticmethod
    def with_input(rig: Rig) -> Any:
        (rig.ws / "data").mkdir(exist_ok=True)
        (rig.ws / "data" / "in.txt").write_text("v1\n")
        return workflow([task("a", inputs=["data/in.txt"], outputs=["out/a.txt"])])

    def test_i10_an_input_mutated_during_the_run_is_key_changed_during_run(self, rig: Rig) -> None:
        wf = self.with_input(rig)
        mutate = HookedExecutor(
            hooks={"a": lambda ctx: (rig.ws / "data" / "in.txt").write_text("v2\n")}
        )
        s1 = rig.run(wf, mutate)

        rec = s1.result_cache["a"]
        assert s1.tasks["a"].status == "succeeded" and mutate.executed == ["a"]
        assert (rec.outcome, rec.stored, rec.store_reason) == (
            "miss",
            False,
            c.REASON_KEY_CHANGED_DURING_RUN,
        )
        assert rig.entry_files() == []  # nothing stored
        skips = rig.log_events(s1, "cache.skip")
        assert [e["reason"] for e in skips if e.get("phase") == "store"] == [
            c.REASON_KEY_CHANGED_DURING_RUN
        ]
        # control: the same task with an untouched input IS stored (the guard is the cause)
        (rig.ws / "data" / "in.txt").write_text("v1\n")
        rig.delete_outputs("out/a.txt")
        s2 = rig.run(wf, CostlyFakeExecutor())
        assert s2.result_cache["a"].stored is True and len(rig.entry_files()) == 1

    def test_i10_a_commit_during_the_run_is_repo_head_moved(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path / "ws", real_git=True)
        wf = single()
        commit = HookedExecutor(
            hooks={"a": lambda ctx: git(rig.ws, "commit", "-q", "--allow-empty", "-m", "moved")}
        )
        s1 = rig.run(wf, commit)

        rec = s1.result_cache["a"]
        assert s1.tasks["a"].status == "succeeded"
        assert (rec.stored, rec.store_reason) == (False, c.REASON_REPO_HEAD_MOVED)
        assert rig.entry_files() == []
        # control: with no commit the same task stores
        rig.delete_outputs("out/a.txt")
        assert rig.run(wf, CostlyFakeExecutor()).result_cache["a"].stored is True

    def test_i10_head_moved_is_guarded_even_with_include_repo_heads_false(
        self, tmp_path: Path
    ) -> None:
        """Guard 2 is independent of the key: with HEADs out of the key a moved HEAD must still
        stop the store (otherwise the entry would describe a different tree)."""
        rig = Rig(tmp_path / "ws", real_git=True, include_repo_heads=False)
        wf = single()
        commit = HookedExecutor(
            hooks={"a": lambda ctx: git(rig.ws, "commit", "-q", "--allow-empty", "-m", "moved")}
        )
        s1 = rig.run(wf, commit)

        assert s1.result_cache["a"].store_reason == c.REASON_REPO_HEAD_MOVED
        assert rig.entry_files() == []

    def test_i10_an_undeclared_tracked_edit_is_repo_worktree_changed(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path / "ws", real_git=True)
        wf = single()
        edit = HookedExecutor(
            hooks={"a": lambda ctx: (rig.ws / "tracked.txt").write_text("edited by the agent\n")}
        )
        s1 = rig.run(wf, edit)

        rec = s1.result_cache["a"]
        assert s1.tasks["a"].status == "succeeded"
        assert (rec.stored, rec.store_reason) == (False, c.REASON_REPO_WORKTREE_CHANGED)
        assert rig.entry_files() == []
        assert git(rig.ws, "status", "--porcelain", "--untracked-files=no").endswith("tracked.txt")


# ======================================================================================= I-11
class TestPriorOutputRule:
    """D6: the prior content of every declared output is part of the key, and the settle-time
    recompute reuses the lookup-time priors (preseed)."""

    def test_i11_the_prior_of_an_output_selects_the_entry(self, rig: Rig) -> None:
        wf = single()
        s1 = rig.run(wf, CostlyFakeExecutor())  # prior: absent
        # `stored` is the preseed proof: by settle time out/a.txt EXISTS, so a recompute without
        # the lookup-time prior would see a different key and refuse with key_changed_during_run.
        assert s1.result_cache["a"].stored is True
        key_absent = rig.only_key()

        rig.out("out/a.txt").write_text("hand edit\n")  # prior: different content
        ex = CostlyFakeExecutor()
        s2 = rig.run(wf, ex)
        assert ex.executed == ["a"]  # NOT served from the entry whose prior was "absent"
        assert s2.result_cache["a"].outcome == "miss"
        assert s2.result_cache["a"].reason == c.REASON_NOT_FOUND and s2.result_cache["a"].stored
        assert len(rig.entry_files()) == 2  # a second key

        rig.delete_outputs("out/a.txt")  # prior absent again: the first entry
        ex = CostlyFakeExecutor()
        s3 = rig.run(wf, ex)
        assert ex.executed == [] and s3.result_cache["a"].hit
        assert s3.result_cache["a"].key == key_absent

        rig.out("out/a.txt").write_text("hand edit\n")  # the second prior: the second entry
        ex = CostlyFakeExecutor()
        s4 = rig.run(wf, ex)
        assert ex.executed == [] and s4.result_cache["a"].hit
        assert s4.result_cache["a"].key != key_absent
        assert rig.out("out/a.txt").read_bytes() == BODY  # the hit overwrote the hand edit

    def test_i11_an_in_place_update_task_stores_and_replays(self, rig: Rig) -> None:
        """An input that is also an output (N-5): the key uses the PRIOR digest, so the settle
        recompute (after the agent overwrote the file) matches and the entry is stored."""
        (rig.ws / "data").mkdir()
        (rig.ws / "data" / "state.txt").write_text("v0\n")
        wf = workflow([task("a", inputs=["data/state.txt"], outputs=["data/state.txt"])])

        s1 = rig.run(wf, CostlyFakeExecutor())
        assert s1.result_cache["a"].stored is True
        assert (rig.ws / "data" / "state.txt").read_bytes() == BODY  # the agent rewrote it

        (rig.ws / "data" / "state.txt").write_text("v0\n")  # same prior again
        ex = CostlyFakeExecutor()
        s2 = rig.run(wf, ex)
        assert ex.executed == [] and s2.result_cache["a"].hit
        assert (rig.ws / "data" / "state.txt").read_bytes() == BODY  # restored over the prior

        # the result of the update is itself a NEW prior: v1 -> a different key, not the v0 entry
        ex = CostlyFakeExecutor()
        s3 = rig.run(wf, ex)
        assert ex.executed == ["a"] and s3.result_cache["a"].reason == c.REASON_NOT_FOUND


# ======================================================================================= I-12
def test_i12_isolation_with_the_cache_on_is_all_ineligible_and_commits_are_identical(
    tmp_path: Path,
) -> None:
    """FR-4: worktree isolation + integration with every task opted in. Nothing is looked up or
    stored, and the integration branch is byte-for-byte what the cache-off run produces (same
    commit subjects, same trees)."""
    from agent_orchestrator.models import IntegrationSpec

    def run_variant(name: str, cache_on: bool) -> tuple[RunState, Rig]:
        r = Rig(tmp_path / name, real_git=True, repo_path="repo")
        wf = workflow(
            [
                task("a", outputs=["out/a.txt"]),
                task("b", depends_on=["a"], outputs=["out/b.txt"]),
            ],
            defaults=WorkflowDefaults(isolation="worktree", cache=True),
            integration=IntegrationSpec(sync_checkout="never"),
        )
        ex = HookedExecutor(
            repo_writes={
                "a": {"core": {"a.txt": "from a\n"}},
                "b": {"core": {"b.txt": "from b\n"}},
            }
        )
        state = r.run(wf, ex, cache=... if cache_on else None)
        assert state.status == "succeeded"
        assert state.integration.active, state.integration.degraded_reason
        assert sorted(ex.executed) == ["a", "b"]
        return state, r

    on_state, on = run_variant("on", True)
    off_state, off = run_variant("off", False)

    assert on.cache.requests, "the coordinator was never consulted: the test would be vacuous"
    recs = on_state.result_cache
    assert set(recs) == {"a", "b"} and {r.outcome for r in recs.values()} == {"ineligible"}
    # the first eligibility rule wins; both name isolation (run-wide or per task)
    assert {r.reason for r in recs.values()} <= {
        c.REASON_RUN_INTEGRATION_ACTIVE,
        c.REASON_ISOLATION_WORKTREE,
    }
    assert on.entry_files() == [] and not on.cache_root.exists()  # nothing created
    assert off_state.result_cache == {}

    def history(r: Rig, branch: str) -> list[tuple[str, str]]:
        repo = r.ws / "repo"
        out = git(repo, "log", "--format=%s%x09%T", branch)
        pairs = [line.split("\t") for line in out.splitlines()]
        return [(subject, tree) for subject, tree in pairs]

    assert on_state.integration.branch == off_state.integration.branch
    branch = on_state.integration.branch
    assert branch
    assert history(on, branch) == history(off, branch)
    assert len(history(on, branch)) > 1  # the integration really committed something
    assert {t: v.status for t, v in on_state.task_integration.items()} == {
        t: v.status for t, v in off_state.task_integration.items()
    }


# ======================================================================================= I-13
def test_i13_a_corrupt_blob_is_a_miss_on_run_2_and_restored_on_run_3(rig: Rig) -> None:
    wf = single()
    s1 = rig.run(wf, CostlyFakeExecutor())
    assert s1.result_cache["a"].stored
    (blob,) = rig.blob_files()
    corrupt = bytearray(blob.read_bytes())
    corrupt[0] ^= 0xFF  # same size, different bytes
    blob.write_bytes(bytes(corrupt))
    rig.delete_outputs("out/a.txt")

    seen_at_dispatch: list[bool] = []
    ex2 = HookedExecutor(
        hooks={"a": lambda ctx: seen_at_dispatch.append(rig.out("out/a.txt").exists())}
    )
    s2 = rig.run(wf, ex2)

    rec = s2.result_cache["a"]
    assert ex2.executed == ["a"] and s2.tasks["a"].status == "succeeded"  # a miss: dispatched
    assert (rec.outcome, rec.reason) == ("miss", c.REASON_BLOB_CORRUPT)
    assert rec.stored is False  # a restore-time miss is never storable (run 2)
    assert seen_at_dispatch == [False]  # the corrupt bytes never reached the destination
    assert rig.out("out/a.txt").read_bytes() == BODY
    assert rig.entry_files() == [] and rig.blob_files() == []  # evicted, and the blob deleted
    corrupt_events = rig.log_events(s2, "cache.corrupt")
    assert [e["reason"] for e in corrupt_events] == [c.REASON_BLOB_CORRUPT]

    rig.delete_outputs("out/a.txt")
    ex3 = CostlyFakeExecutor()
    s3 = rig.run(wf, ex3)  # run 3: not_found -> dispatch -> RE-STORED
    assert ex3.executed == ["a"] and s3.result_cache["a"].reason == c.REASON_NOT_FOUND
    assert s3.result_cache["a"].stored is True and len(rig.entry_files()) == 1

    rig.delete_outputs("out/a.txt")
    ex4 = CostlyFakeExecutor()
    s4 = rig.run(wf, ex4)  # and it serves again
    assert ex4.executed == [] and s4.result_cache["a"].hit
    assert rig.out("out/a.txt").read_bytes() == BODY


# ======================================================================================= I-14
def test_i14_a_dot_dot_manifest_path_writes_nothing_outside_the_workspace(
    rig: Rig,
) -> None:
    """M-1: an entry that names `../escape.txt` (with a real blob behind it, so a vulnerable
    restore WOULD write the file) is a miss: nothing outside the workspace is created."""
    wf = single()
    rig.run(wf, CostlyFakeExecutor())
    path, key = rig.only_entry(), rig.only_key()
    entry = entry_of(rig)
    victim_dir = rig.ws.parent
    before = sorted(p.name for p in victim_dir.iterdir())
    escape = victim_dir / "escape.txt"
    assert not escape.exists()
    forged = entry.model_copy(
        update={"outputs": [entry.outputs[0].model_copy(update={"path": "../escape.txt"})]}
    )
    path.write_bytes(forged.to_canonical_bytes())
    rig.delete_outputs("out/a.txt")

    ex = CostlyFakeExecutor()
    s2 = rig.run(wf, ex)

    assert not escape.exists()
    assert sorted(p.name for p in victim_dir.iterdir()) == before  # nothing new beside the ws
    rec = s2.result_cache["a"]
    assert (rec.outcome, rec.reason) == ("miss", c.REASON_MANIFEST_MISMATCH)
    assert ex.executed == ["a"] and rig.out("out/a.txt").read_bytes() == BODY
    assert [e["reason"] for e in rig.log_events(s2, "cache.corrupt")] == [
        c.REASON_MANIFEST_MISMATCH
    ]
    assert key in {e["key"] for e in rig.log_events(s2, "cache.evict")}  # the forged entry went


# ======================================================================================= I-15
def _i15_child(ws: str, index: int, barrier: Any, queue: Any) -> None:  # pragma: no cover
    """A child process of I-15 (runs under coverage-less `spawn`): one cached run."""
    from datetime import datetime as _dt

    from tests.cache._hardening_rig import Rig as _Rig
    from tests.cache._hardening_rig import SteppingClock as _Clock
    from tests.cache.test_engine_result_cache import CostlyFakeExecutor as _Ex
    from tests.cache.test_engine_result_cache import single as _single

    try:
        # distinct run ids per child: the run id has 1 s granularity and the clock is fixed
        start = _dt(2026, 10, 5 + index, 9, 0, 0, tzinfo=UTC)
        r = _Rig(Path(ws), clock=_Clock(start))
        barrier.wait(BARRIER_TIMEOUT_SECONDS)
        state = r.run(_single(), _Ex())
        rec = state.result_cache["a"]
        queue.put((index, state.status, rec.outcome, rec.stored))
    except BaseException as exc:  # shipped to the parent
        queue.put((index, "error", repr(exc), False))


@pytest.mark.skipif(sys.platform == "win32", reason="spawned interpreters + POSIX paths")
@pytest.mark.skipif((os.cpu_count() or 1) < 2, reason="needs 2 CPUs to overlap")
def test_i15_two_processes_on_one_workspace_both_succeed_and_verify_is_ok(rig: Rig) -> None:
    """BEST-EFFORT SMOKE (NFR-5): two interpreters start behind a barrier and run the same cached
    workflow on one workspace. It does NOT prove the stores overlapped; contention itself is
    proved by U-SM13 (tests/cache/test_store_race.py). It asserts only that both runs succeed
    and that the shared cache is intact and serves a later run."""
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    procs = [
        ctx.Process(target=_i15_child, args=(str(rig.ws), i, barrier, queue)) for i in range(2)
    ]
    for p in procs:
        p.start()
    results = sorted(queue.get(timeout=JOIN_TIMEOUT_SECONDS) for _ in procs)
    for p in procs:
        p.join(JOIN_TIMEOUT_SECONDS)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    assert [r[1] for r in results] == ["succeeded", "succeeded"], results
    assert rig.cache_store.verify().ok
    assert len(rig.entry_files()) == 1
    rig.delete_outputs("out/a.txt")
    ex = CostlyFakeExecutor()
    assert rig.run(single(), ex).result_cache["a"].hit and ex.executed == []


# ======================================================================================= I-16
class TestEmitTasksChildren:
    """FR-2: an `emit_tasks`-injected child can only NARROW the author's policy."""

    @staticmethod
    def child(tid: str, **extra: Any) -> dict[str, Any]:
        return {
            "id": tid,
            "agent": "ag",
            "instruction": "specs/instructions/stub.md",
            "outputs": [f"out/{tid}.txt"],
            "depends_on": ["emitter"],
            "skip_if_outputs_exist": False,
            **extra,
        }

    @staticmethod
    def emitter_wf(*extra: Any, **kw: Any) -> Any:
        emitter = task(
            "emitter",
            emit_tasks=True,
            task_manifest_path="out/manifest.json",
            outputs=["out/emitter.txt"],
            cache=False,
        )
        return workflow([emitter, *extra], **kw)

    @staticmethod
    def run_twice(
        rig: Rig,
        make_wf: Callable[[], Any],
        children: list[dict[str, Any]],
        *also: str,
    ) -> tuple[Any, Any, Any]:
        """Run, delete every output, run again. A fresh spec each time: injection mutates it."""
        payload = {"emitter": {"tasks": children}}
        s1 = rig.run(make_wf(), HookedExecutor(emit_payloads=payload))
        for t in children:
            rig.delete_outputs(t["outputs"][0])
        rig.delete_outputs("out/emitter.txt", *also)
        ex2 = HookedExecutor(emit_payloads=payload)
        s2 = rig.run(make_wf(), ex2)
        return s1, s2, ex2

    def test_i16_an_injected_true_cannot_opt_in_but_a_static_true_can(self, rig: Rig) -> None:
        # defaults.cache is unset: an author opt-in is per task, and only `static` has one
        def make_wf() -> Any:
            return self.emitter_wf(task("static", outputs=["out/static.txt"]))

        s1, s2, ex2 = self.run_twice(
            rig, make_wf, [self.child("kid", cache=True)], "out/static.txt"
        )

        assert s1.status == s2.status == "succeeded"
        assert s2.tasks["kid"].status == "succeeded"
        assert "kid" in ex2.executed  # the injected `cache: true` was ignored: dispatched again
        assert "kid" not in s1.result_cache and "kid" not in s2.result_cache  # no lookup/record
        assert "static" not in ex2.executed and s2.result_cache["static"].hit
        assert {r.task.id: r.injected for r in rig.cache.requests if r.task.id == "kid"} == {
            "kid": True
        }

    def test_i16_an_injected_false_narrows_a_true_default(self, rig: Rig) -> None:
        children = [self.child("narrow", cache=False), self.child("plain")]
        s1, s2, ex2 = self.run_twice(
            rig, lambda: self.emitter_wf(defaults=WorkflowDefaults(cache=True)), children
        )

        assert "narrow" in ex2.executed and "narrow" not in s2.result_cache  # opted out
        assert "plain" not in ex2.executed  # the default (author) opt-in still applies
        assert s2.result_cache["plain"].hit and s2.result_cache["plain"].outcome == "hit"
        assert s1.result_cache["plain"].stored is True
        assert "narrow" not in s1.result_cache


# ======================================================================================= I-17
class TestTtl:
    """FR-7: TTL with a stepping clock (`created_at` and the lookup `now` are both engine time)."""

    def test_i17_an_entry_is_served_inside_the_ttl_and_expires_after_it(
        self, tmp_path: Path
    ) -> None:
        clock = SteppingClock(datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC))
        rig = Rig(tmp_path / "ws", ttl_days=2, clock=clock)
        wf = single()
        s1 = rig.run(wf, CostlyFakeExecutor())
        created = entry_of(rig).created_at
        assert s1.result_cache["a"].stored is True

        clock.jump(DAY)  # 1 day old < 2 days: still served
        rig.delete_outputs("out/a.txt")
        ex = CostlyFakeExecutor()
        s2 = rig.run(wf, ex)
        assert ex.executed == [] and s2.result_cache["a"].hit

        clock.jump(3 * DAY)  # 4 days old > 2 days: expired
        rig.delete_outputs("out/a.txt")
        ex = CostlyFakeExecutor()
        s3 = rig.run(wf, ex)
        rec = s3.result_cache["a"]
        assert ex.executed == ["a"] and (rec.outcome, rec.reason) == ("miss", c.REASON_EXPIRED)
        evicts = rig.log_events(s3, "cache.evict")
        assert [(e["reason"], e["key"]) for e in evicts][:1] == [(c.REASON_EXPIRED, rec.key)]
        assert rec.stored is True  # an expired miss is storable: refreshed under the same key
        assert entry_of(rig).created_at > created + 3 * DAY  # the fresh entry, not the old one

        rig.delete_outputs("out/a.txt")
        ex = CostlyFakeExecutor()
        assert rig.run(wf, ex).result_cache["a"].hit and ex.executed == []

    def test_i17_ttl_none_never_expires(self, tmp_path: Path) -> None:
        clock = SteppingClock(datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC))
        rig = Rig(tmp_path / "ws", ttl_days=None, clock=clock)
        wf = single()
        rig.run(wf, CostlyFakeExecutor())
        clock.jump(36500 * DAY)  # a century
        rig.delete_outputs("out/a.txt")
        ex = CostlyFakeExecutor()
        assert rig.run(wf, ex).result_cache["a"].hit and ex.executed == []


# ======================================================================================= I-19
def test_i19_a_committing_sibling_at_max_parallel_2_means_the_cacheable_task_is_not_stored(
    tmp_path: Path,
) -> None:
    """D13: two independent tasks dispatch together; one commits to the shared repository while
    the other (opted in) is running. The cacheable result may describe a different HEAD, so it is
    NOT stored (safe); the same task stores when no sibling commits."""
    rig = Rig(tmp_path / "ws", real_git=True)
    committed = threading.Event()
    cacheable_started = threading.Event()

    def commit(ctx: Any) -> None:
        if not cacheable_started.wait(BARRIER_TIMEOUT_SECONDS):
            raise AssertionError("the cacheable sibling never started: no concurrency")
        git(rig.ws, "commit", "-q", "--allow-empty", "-m", "sibling commit")
        committed.set()

    def cacheable(ctx: Any) -> None:
        cacheable_started.set()
        if not committed.wait(BARRIER_TIMEOUT_SECONDS):
            raise AssertionError("the sibling never committed")

    wf = workflow(
        [
            task("pure", outputs=["out/pure.txt"]),
            task("committer", outputs=["out/c.txt"], cache=False),
        ]
    )
    head_before = git(rig.ws, "rev-parse", "HEAD")
    ex = HookedExecutor(hooks={"committer": commit, "pure": cacheable})
    s1 = rig.run(wf, ex, max_parallel=2)

    assert s1.status == "succeeded" and sorted(ex.executed) == ["committer", "pure"]
    assert git(rig.ws, "rev-parse", "HEAD") != head_before  # the commit really happened
    rec = s1.result_cache["pure"]
    assert (rec.outcome, rec.stored, rec.store_reason) == ("miss", False, c.REASON_REPO_HEAD_MOVED)
    assert rig.entry_files() == [] and "committer" not in s1.result_cache

    # control: the same cacheable task, no committing sibling -> stored
    rig.delete_outputs("out/pure.txt", "out/c.txt")
    s2 = rig.run(wf, HookedExecutor(), max_parallel=2)
    assert s2.result_cache["pure"].stored is True and len(rig.entry_files()) == 1


# ======================================================================================= I-20
def test_i20_a_quota_requeue_then_a_hit_keeps_the_real_spend_and_counts_as_a_hit(
    rig: Rig,
) -> None:
    """D12 / FR-9. Run 1 pays (cost, tokens) and stores. The run is then resumed with the entry
    unavailable: the first pass misses and dispatches, the executor reports quota exhaustion and
    the engine requeues the task; during the quota wait the entry reappears (another process
    stored it), so the SECOND pass is a hit. The hit must keep the real spend `prepare_resume`
    carried into `cumulative_*`, must still be a current hit, and `aggregate_usage` (site A)
    must count that real spend without counting the hit as a dispatched task."""
    wf = single()
    s1 = rig.run(wf, CostlyFakeExecutor())
    assert s1.tasks["a"].cumulative_cost_usd == pytest.approx(COST_USD)
    entry_path = rig.only_entry()
    saved_bytes = entry_path.read_bytes()
    entry_path.unlink()  # the entry is "not there yet"
    rig.delete_outputs("out/a.txt")
    rig.rs_store.prepare_resume(s1, wf)
    assert s1.tasks["a"].cumulative_cost_usd == pytest.approx(COST_USD)  # carried over resume

    def entry_reappears(_seconds: float) -> None:  # the quota wait: another process stored it
        entry_path.write_bytes(saved_bytes)

    ex = HookedExecutor(quota_exhausted_tasks={"a": 1})
    s2 = rig.run(wf, ex, run_state=s1, sleeper=entry_reappears, quota_poll_seconds=7)

    ts, rec = s2.tasks["a"], s2.result_cache["a"]
    assert ex.executed == ["a"]  # exactly one real dispatch: the one that hit the quota wall
    assert [e["reason"] for e in rig.log_events(s2, "cache.miss")][-1] == c.REASON_NOT_FOUND
    assert len(rig.log_events(s2, "quota.wait")) == 1
    assert (rec.outcome, rec.hit) == ("hit", True) and ts.status == "succeeded"
    assert rec.dispatch_cycle == ts.dispatch_cycle == 3 and rec.ended_at == ts.ended_at
    assert rig.out("out/a.txt").read_bytes() == BODY
    # real spend preserved (not reset, not doubled by the hit):
    assert ts.cumulative_cost_usd == pytest.approx(COST_USD)
    assert (ts.cumulative_input_tokens, ts.cumulative_output_tokens) == (IN_TOKENS, OUT_TOKENS)
    assert rec.saved_cost_usd == pytest.approx(COST_USD)  # the avoided spend is reported apart

    report = aggregate_usage([s2], rig.store)
    (group,) = [g for g in report.groups if g.cost_usd > 0]
    assert group.cost_usd == pytest.approx(COST_USD)  # site A keeps the real spend ...
    assert (group.input_tokens, group.output_tokens) == (IN_TOKENS, OUT_TOKENS)
    assert (group.tasks, group.succeeded, group.failed, group.retried) == (0, 0, 0, 0)  # ... not
    assert report.result_cache is not None and report.result_cache.hits == 1  # a dispatch


# ======================================================================================= I-23
class TestShadowEndToEnd:
    """FR-16: shadow mode measures; it never restores and never touches the workspace."""

    def test_i23_shadow_records_would_hit_runs_the_agent_and_refreshes_the_entry(
        self, tmp_path: Path
    ) -> None:
        rig = Rig(tmp_path / "ws", mode=c.MODE_SHADOW)
        wf = single()
        ex1 = CostlyFakeExecutor()
        s1 = rig.run(wf, ex1)
        r1 = s1.result_cache["a"]
        assert ex1.executed == ["a"] and (r1.outcome, r1.mode) == ("miss", c.MODE_SHADOW)
        assert r1.stored is True  # shadow stores: that is how the next run can measure
        created_1 = entry_of(rig).created_at
        key = rig.only_key()
        rig.delete_outputs("out/a.txt")

        present_at_dispatch: list[bool] = []
        ex2 = HookedExecutor(
            hooks={"a": lambda ctx: present_at_dispatch.append(rig.out("out/a.txt").exists())}
        )
        s2 = rig.run(wf, ex2)

        rec = s2.result_cache["a"]
        assert ex2.executed == ["a"]  # the agent STILL runs
        assert present_at_dispatch == [False]  # and the cache restored nothing beforehand
        assert (rec.outcome, rec.hit, rec.mode, rec.key) == ("would_hit", False, c.MODE_SHADOW, key)
        assert rec.saved_cost_usd == pytest.approx(COST_USD)  # what it WOULD have saved
        assert len(rig.log_events(s2, "cache.would_hit")) == 1
        assert rig.log_events(s2, "cache.hit") == []
        assert rig.out("out/a.txt").read_bytes() == BODY  # written by the agent, not the cache
        assert s2.tasks["a"].attempts == 1 and s2.tasks["a"].cumulative_cost_usd == pytest.approx(
            COST_USD
        )
        assert rec.stored is True and entry_of(rig).created_at > created_1  # refreshed
        block = rig.status_json(s2)["result_cache"]
        assert (block["hits"], block["would_hits"]) == (0, 1)
        assert block["avoidable_cost_usd"] == pytest.approx(COST_USD)
        assert block["saved_cost_usd"] == 0
        report = aggregate_usage([s2], rig.store)  # the dispatch is a real dispatch
        assert sum(g.tasks for g in report.groups) == 1
        assert report.result_cache is not None and report.result_cache.would_hits == 1


# ======================================================================================= I-25
class TestCoordinatorFailureBoundary:
    """D32 / M-16: a cache bug must not kill a paid run."""

    @staticmethod
    def two_tasks() -> Any:
        return workflow(
            [task("a", outputs=["out/a.txt"]), task("b", outputs=["out/b.txt"], depends_on=["a"])]
        )

    def test_i25_an_unexpected_lookup_exception_disables_the_cache_and_the_run_completes(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.cache.coordinator import ResultCache

        calls: list[str] = []

        def boom(self: Any, req: Any, log: Any) -> Any:
            calls.append(req.task.id)
            raise RuntimeError("injected coordinator bug")

        monkeypatch.setattr(ResultCache, "_lookup", boom)
        ex = CostlyFakeExecutor()
        state = rig.run(self.two_tasks(), ex)

        assert state.status == "succeeded" and ex.executed == ["a", "b"]  # the run completed
        assert all(ts.status == "succeeded" for ts in state.tasks.values())
        assert rig.out("out/a.txt").read_bytes() and rig.out("out/b.txt").read_bytes()
        assert calls == ["a"]  # disabled for the REST of the run: b never reached the lookup
        disabled = rig.log_events(state, "cache.disabled")
        assert len(disabled) == 1 and disabled[0]["error_type"] == "RuntimeError"
        assert state.result_cache == {} and "result_cache" not in rig.status_json(state)
        assert rig.entry_files() == []

    def test_i25_an_unexpected_store_exception_loses_neither_the_result_nor_the_run(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.cache.coordinator import ResultCache

        def boom(self: Any, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("injected store bug")

        monkeypatch.setattr(ResultCache, "_store_success", boom)
        ex = CostlyFakeExecutor()
        state = rig.run(self.two_tasks(), ex)

        assert state.status == "succeeded" and ex.executed == ["a", "b"]
        assert rig.out("out/a.txt").read_bytes() == BODY  # the paid result is intact
        rec = state.result_cache["a"]  # the lookup already succeeded: its miss record stays
        assert (rec.outcome, rec.stored, rec.store_reason) == (
            "miss",
            False,
            c.REASON_CACHE_DISABLED,
        )
        assert len(rig.log_events(state, "cache.disabled")) == 1
        assert "b" not in state.result_cache  # b ran with the cache already disabled
        assert rig.entry_files() == []

    def test_i25_strict_mode_reraises_into_the_caller(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.cache.coordinator import ResultCache

        rig = Rig(tmp_path / "ws", strict=True)

        def boom(self: Any, req: Any, log: Any) -> Any:
            raise RuntimeError("injected coordinator bug")

        monkeypatch.setattr(ResultCache, "_lookup", boom)
        with pytest.raises(RuntimeError, match="injected coordinator bug"):
            rig.run(single(), CostlyFakeExecutor())

    def test_i25_an_expected_error_is_a_store_error_miss_and_the_cache_stays_enabled(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contrast: an OSError is an environmental failure, not a bug: a `store_error` miss with
        one ERROR log; the cache is NOT disabled, so the next task still looks up."""
        real = LocalFsCacheStore.get_entry

        def flaky(self: LocalFsCacheStore, key: str) -> Any:
            if not seen:
                seen.append(key)
                raise OSError("simulated EIO")
            return real(self, key)

        seen: list[str] = []
        monkeypatch.setattr(LocalFsCacheStore, "get_entry", flaky)
        ex = CostlyFakeExecutor()
        state = rig.run(self.two_tasks(), ex)

        assert state.status == "succeeded" and ex.executed == ["a", "b"]
        assert (state.result_cache["a"].outcome, state.result_cache["a"].reason) == (
            "miss",
            c.REASON_STORE_ERROR,
        )
        assert rig.log_events(state, "cache.disabled") == []
        assert state.result_cache["b"].reason == c.REASON_NOT_FOUND  # b DID look up


# ======================================================================================= I-26
class TestPruneRacesStore:
    """NFR-5: `ao cache prune` racing a store is benign. The window: `capture_outputs` has written
    the blobs, the ENTRY file is not there yet (orphan blobs). A `put_entry` wrapper runs a
    prune in that exact window, from another thread, joined with a timeout."""

    @staticmethod
    def prune_in_the_window(
        rig: Rig, monkeypatch: pytest.MonkeyPatch, *, past_grace: bool
    ) -> list[Any]:
        real_put = LocalFsCacheStore.put_entry
        reports: list[Any] = []

        def put_after_prune(self: LocalFsCacheStore, entry: CacheEntry) -> int:
            blobs = rig.blob_files()
            assert blobs and rig.entry_files() == []  # we ARE inside the window
            newest = max(b.stat().st_mtime for b in blobs)
            skew = c.BLOB_SWEEP_GRACE_SECONDS + 60 if past_grace else 10
            now = datetime.fromtimestamp(newest + skew, UTC)
            other = LocalFsCacheStore.for_workspace(str(rig.ws), max_bytes=10**9, ttl_days=30)
            err: list[BaseException] = []

            def prune() -> None:
                try:
                    reports.append(other.prune(now=now, max_bytes=None, ttl_days=None))
                except BaseException as exc:  # asserted below
                    err.append(exc)

            t = threading.Thread(target=prune, name="prune")
            t.start()
            t.join(JOIN_TIMEOUT_SECONDS)
            assert not t.is_alive() and err == []
            return real_put(self, entry)

        monkeypatch.setattr(LocalFsCacheStore, "put_entry", put_after_prune)
        return reports

    def test_i26_a_prune_inside_the_grace_window_never_removes_the_new_blob(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reports = self.prune_in_the_window(rig, monkeypatch, past_grace=False)
        wf = single()
        s1 = rig.run(wf, CostlyFakeExecutor())
        monkeypatch.setattr(LocalFsCacheStore, "put_entry", REAL_PUT_ENTRY)

        assert s1.result_cache["a"].stored is True
        assert len(reports) == 1 and reports[0].removed_blobs == 0
        assert rig.cache_store.verify().ok and len(rig.blob_files()) == 1
        rig.delete_outputs("out/a.txt")
        ex = CostlyFakeExecutor()
        s2 = rig.run(wf, ex)
        assert ex.executed == [] and s2.result_cache["a"].hit
        assert rig.out("out/a.txt").read_bytes() == BODY

    def test_i26_a_prune_that_does_remove_the_orphan_blob_costs_a_miss_never_wrong_bytes(
        self, rig: Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reports = self.prune_in_the_window(rig, monkeypatch, past_grace=True)
        wf = single()
        s1 = rig.run(wf, CostlyFakeExecutor())
        monkeypatch.setattr(LocalFsCacheStore, "put_entry", REAL_PUT_ENTRY)

        assert reports[0].removed_blobs == 1  # the race WAS lost: the blob is gone
        assert s1.status == "succeeded" and s1.result_cache["a"].stored is True
        assert rig.entry_files() and rig.blob_files() == []  # a dangling entry
        assert not rig.cache_store.verify().ok  # which `verify` reports
        rig.delete_outputs("out/a.txt")

        ex = CostlyFakeExecutor()
        s2 = rig.run(wf, ex)  # benign: a miss, correct bytes, the dangling entry evicted

        rec = s2.result_cache["a"]
        assert ex.executed == ["a"] and (rec.outcome, rec.reason) == ("miss", c.REASON_BLOB_MISSING)
        assert rig.out("out/a.txt").read_bytes() == BODY
        assert rig.entry_files() == []
        rig.delete_outputs("out/a.txt")
        s3 = rig.run(wf, CostlyFakeExecutor())  # self-heals: stored again, then served
        assert s3.result_cache["a"].stored is True
        rig.delete_outputs("out/a.txt")
        ex4 = CostlyFakeExecutor()
        assert rig.run(wf, ex4).result_cache["a"].hit and ex4.executed == []

    def test_i26_a_prune_loop_running_beside_a_parallel_workflow_is_benign(self, rig: Rig) -> None:
        """BEST-EFFORT: a bounded prune loop in a thread while a 4-wide workflow stores at
        `max_parallel=3`. It cannot prove an interleaving (the two tests above do); it asserts
        that nothing raises, the run succeeds and the cache stays valid and serves."""
        names = [f"t{i}" for i in range(4)]
        outs = [f"out/{n}.txt" for n in names]
        wf = workflow([task(n, outputs=[o]) for n, o in zip(names, outs, strict=True)])
        stop = threading.Event()
        errors: list[BaseException] = []
        passes: list[int] = []

        def loop() -> None:
            other = LocalFsCacheStore.for_workspace(str(rig.ws), max_bytes=10**9, ttl_days=30)
            while not stop.is_set() and len(passes) < 200:
                try:
                    if other.root and os.path.isdir(other.root):
                        # ttl 0 + a late `now`: every entry is expired and every blob is past the
                        # sweep grace, so the prune is as destructive as it can be
                        other.prune(now=LATE, max_bytes=None, ttl_days=0)
                    passes.append(1)
                except BaseException as exc:
                    errors.append(exc)
                    return

        t = threading.Thread(target=loop, name="prune-loop")
        t.start()
        try:
            s1 = rig.run(wf, CostlyFakeExecutor(), max_parallel=PARALLEL_WIDTH)
        finally:
            stop.set()
            t.join(JOIN_TIMEOUT_SECONDS)
        assert not t.is_alive() and errors == []
        assert s1.status == "succeeded"
        assert passes, "the prune loop never ran"
        rig.delete_outputs(*outs)
        s2 = rig.run(wf, CostlyFakeExecutor(), max_parallel=PARALLEL_WIDTH)
        assert s2.status == "succeeded"
        corrupt = {e["reason"] for e in rig.log_events(s2, "cache.corrupt")}
        assert corrupt <= {c.REASON_BLOB_MISSING}  # the only damage a lost race can do
        assert {rig.out(o).read_bytes() for o in outs} == {
            f"fake output for {n}\n".encode() for n in names
        }
