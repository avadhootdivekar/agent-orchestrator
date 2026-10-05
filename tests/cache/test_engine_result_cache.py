"""T-XpF1pF: engine integration of the result cache (HLD 8.7).

Covers I-3..I-8, I-18, I-21, I-22, I-27 and U-AST-E.

A real `Orchestrator` drives a real `ResultCache` over a real `LocalFsCacheStore` in a temp
workspace. Only the three environmental collaborators are faked (HEAD reader, worktree probe, CLI
version) and the executor (`FakeExecutor` subclasses, which also spy on dispatches). The clock is a
deterministic ticking clock shared by the engine and the run-state store, so every run gets a
distinct run id and every timestamp replays identically.

I-1 and I-2 (the cache-off proof) live in `test_noop_proof.py` and are NOT repeated here.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator.engine as engine_mod
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager, cycle_key
from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.coordinator import ResultCache
from agent_orchestrator.cache.settings import ResultCacheSettings
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import LookupOutcome, LookupRequest, PendingStore, StoreResult
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    CircuitBreakerSpec,
    EstimatorConfig,
    RepoRef,
    RepoSet,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.ui.activity import locate_attempt_dirs
from tests.cache.fakes import FakeRepoHeadReader, FakeWorktreeProbe, fake_cli_version_of

ENGINE_SOURCE = Path(engine_mod.__file__)
HEAD = "0123456789abcdef0123456789abcdef01234567"
INSTRUCTION = "specs/instructions/stub.md"
COST_USD = 0.75
IN_TOKENS = 1200
OUT_TOKENS = 340
START = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)
TICK = timedelta(seconds=1)  # each clock read; run ids are second-granular, so runs stay distinct
PARALLEL_WIDTH = 4


# ------------------------------------------------------------------------------ test doubles
class TickingClock:
    """Deterministic: every read returns the previous value plus one second."""

    def __init__(self) -> None:
        self._now = START
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            self._now += TICK
            return self._now


class CostlyFakeExecutor(FakeExecutor):
    """`FakeExecutor` reports no cost; this one does, and records every dispatch it receives."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.executed: list[str] = []
        self.threads: dict[str, str] = {}

    def execute(self, ctx: Any) -> TaskResult:
        self.executed.append(ctx.task_id)
        self.threads[ctx.task_id] = threading.current_thread().name
        result = super().execute(ctx)
        if result.status != "succeeded":
            return result
        return result.model_copy(
            update={
                "actuals_available": True,
                "input_tokens": IN_TOKENS,
                "output_tokens": OUT_TOKENS,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_usd": COST_USD,
            }
        )


class SpyCache:
    """A `ResultCacheHook` that delegates to the real coordinator and records the calls."""

    def __init__(self, inner: ResultCache) -> None:
        self.inner = inner
        self.requests: list[LookupRequest] = []
        self.outcomes: list[LookupOutcome] = []
        self.stores: list[str] = []

    @property
    def looked_up(self) -> list[str]:
        return [r.task.id for r in self.requests]

    def lookup(self, request: LookupRequest, log: logging.LoggerAdapter) -> LookupOutcome:
        self.requests.append(request)
        outcome = self.inner.lookup(request, log)
        self.outcomes.append(outcome)
        return outcome

    def store_success(
        self, pending: PendingStore, *, ts: TaskRunState, now: datetime, log: logging.LoggerAdapter
    ) -> StoreResult:
        self.stores.append(pending.request.task.id)
        return self.inner.store_success(pending, ts=ts, now=now, log=log)


class SpyBudget(DefaultBudgetManager):
    """Counts the three mutating/gating calls per task id."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.calls: list[tuple[str, str]] = []

    def gate(self, task_id: str, estimate: int, counters: Any) -> Any:
        self.calls.append(("gate", task_id))
        return super().gate(task_id, estimate, counters)

    def charge_estimate(self, task_id: str, estimate: int, counters: Any, cycle: int = 1) -> None:
        self.calls.append(("charge", task_id))
        super().charge_estimate(task_id, estimate, counters, cycle=cycle)

    def reconcile(self, task_id: str, actual: int, counters: Any, cycle: int = 1) -> None:
        self.calls.append(("reconcile", task_id))
        super().reconcile(task_id, actual, counters, cycle=cycle)


# --------------------------------------------------------------------------------- the world
@dataclass
class World:
    ws: Path
    clock: TickingClock = field(default_factory=TickingClock)

    def __post_init__(self) -> None:
        self.store = LocalFsArtifactStore(str(self.ws))
        self.rs_store = RunStateStore(str(self.ws), self.store, clock=self.clock)
        instr = self.ws / INSTRUCTION
        instr.parent.mkdir(parents=True, exist_ok=True)
        instr.write_text("# stub instruction\n")
        settings = ResultCacheSettings(
            mode=c.MODE_ON,
            source=c.SOURCE_ENV,
            max_bytes=10**9,
            max_entry_bytes=10**8,
            ttl_days=30,
            include_repo_heads=True,
            max_input_bytes=10**9,
            max_input_files=10**6,
        )
        self.cache = SpyCache(
            ResultCache(
                LocalFsCacheStore.for_workspace(
                    str(self.ws), max_bytes=settings.max_bytes, ttl_days=30
                ),
                settings,
                workspace_root=str(self.ws),
                cache_root=str(self.ws / ".orchestrator" / "cache"),
                heads=FakeRepoHeadReader({"core": HEAD}),
                worktree=FakeWorktreeProbe(frozenset()),
                cli_versions=fake_cli_version_of("9.9.9 (Claude Code)"),
                environ={},
            )
        )

    # -- builders ---------------------------------------------------------------------------
    def orchestrator(self, executor: FakeExecutor, *, cache: Any = ..., **kw: Any) -> Orchestrator:
        hook = self.cache if cache is ... else cache
        return Orchestrator(
            executor, self.store, self.rs_store, clock=self.clock, result_cache=hook, **kw
        )

    def run(
        self,
        wf: WorkflowSpec,
        executor: FakeExecutor,
        *,
        run_state: RunState | None = None,
        cache: Any = ...,
        **kw: Any,
    ) -> RunState:
        orch = self.orchestrator(executor, cache=cache, **kw)
        return orch.run(wf, self.reposets(), self.agents(), run_state=run_state)

    def reposets(self) -> dict[str, RepoSet]:
        return {
            "rs": RepoSet(
                workspace_root=str(self.ws), repos=[RepoRef(id="core", path=".", role="primary")]
            )
        }

    @staticmethod
    def agents() -> dict[str, AgentSpec]:
        return {"ag": AgentSpec(executor="claude_cli", model="sonnet", effort="medium")}

    # -- inspection -------------------------------------------------------------------------
    def out(self, rel: str) -> Path:
        return self.ws / rel

    def delete_outputs(self, *rels: str) -> None:
        for rel in rels:
            self.out(rel).unlink()

    def run_dir(self, state: RunState) -> Path:
        return self.ws / ".orchestrator" / "runs" / state.run_id


def task(tid: str, **kw: Any) -> TaskSpec:
    kw.setdefault("cache", True)
    kw.setdefault("skip_if_outputs_exist", False)  # a hit is only reachable past should_skip
    return TaskSpec(id=tid, agent="ag", instruction=INSTRUCTION, **kw)


def workflow(tasks: list[TaskSpec], **kw: Any) -> WorkflowSpec:
    return WorkflowSpec(version="1.0", id="rc-wf", repo_set="rs", tasks=tasks, **kw)


def single(tid: str = "a", **kw: Any) -> WorkflowSpec:
    return workflow([task(tid, outputs=[f"out/{tid}.txt"], **kw)])


def chain() -> WorkflowSpec:
    return workflow(
        [
            task("a", outputs=["out/a.txt"]),
            task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
        ]
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.delenv(c.ENV_CACHE, raising=False)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    return World(tmp_path.resolve())


def events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == name]


# ======================================================================================== I-3
class TestMissStoreHit:
    def test_i3_hit_dispatches_nothing_and_restores_identical_bytes(
        self, world: World, caplog: pytest.LogCaptureFixture
    ) -> None:
        wf = single()
        first = CostlyFakeExecutor()
        s1 = world.run(wf, first)
        assert first.executed == ["a"] and s1.status == "succeeded"
        rec1 = s1.result_cache["a"]
        assert (rec1.outcome, rec1.stored) == ("miss", True) and rec1.hit is False
        body = world.out("out/a.txt").read_bytes()
        world.delete_outputs("out/a.txt")

        second = CostlyFakeExecutor()
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            s2 = world.run(wf, second)

        assert second.executed == []  # 0 dispatches
        assert world.out("out/a.txt").read_bytes() == body
        ts = s2.tasks["a"]
        assert ts.status == "succeeded" and ts.outputs_present
        rec = s2.result_cache["a"]
        assert rec.outcome == "hit" and rec.hit is True
        assert rec.ended_at == ts.ended_at  # D14 binding: the record is current
        assert rec.saved_cost_usd == pytest.approx(COST_USD)
        assert rec.saved_tokens == IN_TOKENS + OUT_TOKENS > 0
        assert len(events(caplog, "cache.hit")) == 1
        ends = [r for r in events(caplog, "task.end") if getattr(r, "cached", False)]
        assert len(ends) == 1 and ends[0].status == "succeeded"  # type: ignore[attr-defined]
        assert s2.status == "succeeded"

    def test_a_changed_input_misses_and_dispatches(self, world: World) -> None:
        wf = single()
        world.run(wf, CostlyFakeExecutor())
        world.delete_outputs("out/a.txt")
        (world.ws / INSTRUCTION).write_text("# a different instruction\n")
        ex = CostlyFakeExecutor()
        s2 = world.run(wf, ex)
        assert ex.executed == ["a"] and s2.result_cache["a"].outcome == "miss"


# ======================================================================================== I-4
def test_i4_chain_both_tasks_hit_on_the_second_run(world: World) -> None:
    wf = chain()
    world.run(wf, CostlyFakeExecutor())
    bodies = {p: world.out(p).read_bytes() for p in ("out/a.txt", "out/b.txt")}
    world.delete_outputs("out/a.txt", "out/b.txt")
    ex = CostlyFakeExecutor()
    s2 = world.run(wf, ex)
    assert ex.executed == []
    assert {t: s2.result_cache[t].outcome for t in ("a", "b")} == {"a": "hit", "b": "hit"}
    assert {p: world.out(p).read_bytes() for p in bodies} == bodies


# ======================================================================================== I-5
def test_i5_parallel_hits_never_reach_a_worker_and_match_serial(world: World) -> None:
    leaves = [
        task(f"t{i}", outputs=[f"out/t{i}.txt"], cache=(i < 2)) for i in range(PARALLEL_WIDTH)
    ]
    wf = workflow(leaves)
    outs = [f"out/t{i}.txt" for i in range(PARALLEL_WIDTH)]
    serial = world.run(wf, CostlyFakeExecutor())  # max_parallel=1
    serial_bytes = {o: world.out(o).read_bytes() for o in outs}
    serial_status = {t: ts.status for t, ts in serial.tasks.items()}
    world.delete_outputs(*outs)

    ex = CostlyFakeExecutor()
    par = world.run(wf, ex, max_parallel=PARALLEL_WIDTH)

    assert sorted(ex.executed) == ["t2", "t3"]  # the hits t0, t1 took no worker slot
    assert all(ex.threads[t] != threading.current_thread().name for t in ex.executed)
    assert {t: par.result_cache[t].outcome for t in ("t0", "t1")} == {"t0": "hit", "t1": "hit"}
    assert "t2" not in par.result_cache and "t3" not in par.result_cache  # opted out
    assert {t: ts.status for t, ts in par.tasks.items()} == serial_status
    assert {o: world.out(o).read_bytes() for o in outs} == serial_bytes


# ================================================================================ I-6 / I-6b
def budgeted(wf: WorkflowSpec, world: World) -> tuple[WorkflowSpec, SpyBudget, Any]:
    cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=0)
    spec = BudgetSpec(total_tokens=1_000_000, estimator=cfg)
    wf.budget = spec
    return wf, SpyBudget(spec, world.clock), HeuristicTokenEstimator(world.store)


class TestBudget:
    def test_i6_a_hit_never_gates_charges_or_reconciles(self, world: World) -> None:
        wf, mgr, est = budgeted(single(), world)
        s1 = world.run(wf, CostlyFakeExecutor(), budget_manager=mgr, estimator=est)
        assert [k for k, _ in mgr.calls] == ["gate", "charge", "reconcile"]  # the spy sees a miss
        before = s1.budget_counters.model_copy(deep=True)
        mgr.calls.clear()
        world.delete_outputs("out/a.txt")

        ex = CostlyFakeExecutor()
        s2 = world.run(wf, ex, budget_manager=mgr, estimator=est)

        assert ex.executed == [] and s2.result_cache["a"].hit
        assert mgr.calls == []
        assert s2.budget_counters.consumed_tokens == 0 and before.consumed_tokens > 0
        assert s2.budget_counters.charged_estimate == {}

    def test_i6b_a_stale_charge_is_reversed_on_a_hit(
        self, world: World, caplog: pytest.LogCaptureFixture
    ) -> None:
        wf, mgr, est = budgeted(single(), world)
        s1 = world.run(wf, CostlyFakeExecutor(), budget_manager=mgr, estimator=est)
        assert s1.tasks["a"].dispatch_cycle == 1
        # Hand-simulate "cycle 2 was charged, then the process crashed before the worker" on
        # top of the real state (the pattern of tests/test_engine_budget.py::TestResumeDouble...).
        stale = 25
        s1.tasks["a"].status = "running"
        s1.tasks["a"].dispatch_cycle = 2
        s1.budget_counters.charged_estimate[cycle_key("a", 2)] = stale
        s1.budget_counters.consumed_tokens += stale
        s1.budget_counters.window_consumed_tokens += stale
        consumed_before = s1.budget_counters.consumed_tokens
        world.delete_outputs("out/a.txt")
        world.rs_store.prepare_resume(s1, wf)
        world.rs_store.save(s1)
        mgr.calls.clear()

        ex = CostlyFakeExecutor()
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            s2 = world.run(wf, ex, run_state=s1, budget_manager=mgr, estimator=est)

        assert ex.executed == []
        assert s2.result_cache["a"].hit and s2.tasks["a"].dispatch_cycle == 3
        bc = s2.budget_counters
        assert cycle_key("a", 2) not in bc.charged_estimate and bc.charged_estimate == {}
        assert bc.consumed_tokens == consumed_before - stale  # the counters are restored
        reversed_cycles = [
            r.cycle  # type: ignore[attr-defined]
            for r in events(caplog, "budget.resume_reverse")
            if getattr(r, "task_id", None) == "a"
        ]
        assert reversed_cycles == [2]  # the stale cycle, not the new one
        assert [k for k, _ in mgr.calls] == []  # and still no gate/charge/reconcile


# ======================================================================================== I-7
def test_i7_a_hit_is_never_evaluated_by_breakers(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = single()
    wf.circuit_breakers = [
        CircuitBreakerSpec(
            id="cap", condition="task_cost_usd", threshold=COST_USD * 10, action="fail"
        )
    ]
    evaluated: list[str] = []
    real = engine_mod.evaluate_breakers

    def spy(*a: Any, **kw: Any) -> Any:
        evaluated.append("call")
        return real(*a, **kw)

    monkeypatch.setattr(engine_mod, "evaluate_breakers", spy)
    world.run(wf, CostlyFakeExecutor())
    assert evaluated  # the spy is live: a miss IS evaluated
    evaluated.clear()
    world.delete_outputs("out/a.txt")

    s2 = world.run(wf, CostlyFakeExecutor())

    assert s2.result_cache["a"].hit and evaluated == []
    ts = s2.tasks["a"]
    assert (ts.cumulative_cost_usd, ts.cumulative_input_tokens) == (0.0, 0)
    assert ts.cumulative_output_tokens == 0 and s2.tripped_breakers == []


# ======================================================================================== I-8
def test_i8_after_resume_the_hit_stays_succeeded_and_is_not_looked_up_again(world: World) -> None:
    wf = single()
    world.run(wf, CostlyFakeExecutor())
    world.delete_outputs("out/a.txt")
    s2 = world.run(wf, CostlyFakeExecutor())
    assert s2.result_cache["a"].hit
    record = s2.result_cache["a"].model_copy()
    looked_up = list(world.cache.looked_up)

    world.rs_store.prepare_resume(s2, wf)  # outputs are present again: the hit is kept
    assert s2.tasks["a"].status == "succeeded"
    ex = CostlyFakeExecutor()
    s3 = world.run(wf, ex, run_state=s2)

    assert world.cache.looked_up == looked_up  # no new lookup
    assert ex.executed == [] and s3.tasks["a"].status == "succeeded"
    assert s3.result_cache["a"] == record  # untouched


# ======================================================================================= I-18
def test_i18_a_hit_keeps_its_cycle_has_no_capture_dir_and_a_later_dispatch_uses_cycle_2(
    world: World,
) -> None:
    wf = single()
    s1 = world.run(wf, CostlyFakeExecutor())
    assert locate_attempt_dirs(world.run_dir(s1), "a", 1)  # a real dispatch left a capture dir
    world.delete_outputs("out/a.txt")
    s2 = world.run(wf, CostlyFakeExecutor())
    assert s2.result_cache["a"].hit and s2.tasks["a"].dispatch_cycle == 1  # increment kept
    assert locate_attempt_dirs(world.run_dir(s2), "a", s2.tasks["a"].dispatch_cycle) == []

    # Outputs removed again: resume resets the hit to pending; with the cache OFF it dispatches.
    world.delete_outputs("out/a.txt")
    world.rs_store.prepare_resume(s2, wf)
    assert s2.tasks["a"].status == "pending" and s2.tasks["a"].dispatch_cycle == 1
    ex = CostlyFakeExecutor()
    s3 = world.run(wf, ex, run_state=s2, cache=None)
    assert ex.executed == ["a"] and s3.tasks["a"].dispatch_cycle == 2
    dirs = locate_attempt_dirs(world.run_dir(s3), "a", 2)
    assert dirs and all(d.parent.name == "cycle-2" for _, d in dirs)


# ================================================================================ I-21 / I-22
def test_i21_a_not_taken_task_never_reaches_the_lookup(world: World) -> None:
    verdict = world.out("out/verdict.json")
    verdict.parent.mkdir(parents=True, exist_ok=True)
    verdict.write_text(json.dumps({"routes": ["bug"]}))
    wf = workflow(
        [
            task("classify", outputs=["out/classify.txt"], cache=False),
            task("bug-fix", depends_on=["classify"], outputs=["out/bug.txt"]),
            task("doc-fix", depends_on=["classify"], outputs=["out/doc.txt"]),
            task(
                "converge",
                depends_on=["bug-fix", "doc-fix"],
                inputs=["out/bug.txt", "out/doc.txt"],
                outputs=["out/converge.txt"],
                join="any",
            ),
        ],
        branches=[
            RouterSpec(
                id="r",
                router_task_id="classify",
                verdict_path="out/verdict.json",
                routes={
                    "bug": RouteSpec(entry=["bug-fix"]),
                    "documentation": RouteSpec(entry=["doc-fix"]),
                },
            )
        ],
    )
    state = world.run(wf, CostlyFakeExecutor())
    assert state.tasks["doc-fix"].status == "not_taken"
    assert "doc-fix" not in world.cache.looked_up and "doc-fix" not in state.result_cache
    assert {"bug-fix", "converge"} <= set(world.cache.looked_up)  # live tasks DO look up


def test_i22_a_missing_required_input_fails_before_the_lookup(world: World) -> None:
    wf = workflow([task("a", inputs=["docs/absent.md"], outputs=["out/a.txt"])])
    ex = CostlyFakeExecutor()
    state = world.run(wf, ex)
    assert state.tasks["a"].status == "failed" and ex.executed == []
    assert world.cache.looked_up == [] and state.result_cache == {}


# ======================================================================================= I-27
class TestFirstPassAndOptOut:
    def test_i27_a_first_pass_hit_has_zero_attempts(self, world: World) -> None:
        wf = single()
        world.run(wf, CostlyFakeExecutor())
        world.delete_outputs("out/a.txt")
        s2 = world.run(wf, CostlyFakeExecutor())
        assert s2.result_cache["a"].hit and s2.tasks["a"].attempts == 0

    def test_i27_cache_false_under_defaults_true_gets_no_record_and_no_entry(
        self, world: World, caplog: pytest.LogCaptureFixture
    ) -> None:
        wf = workflow(
            [task("a", outputs=["out/a.txt"], cache=False)],
            defaults=WorkflowDefaults(cache=True),
        )
        ex = CostlyFakeExecutor()
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            state = world.run(wf, ex)
        assert ex.executed == ["a"] and state.result_cache == {}
        # The hook is consulted (it owns the policy) but performs no store lookup or store.
        assert not any(
            getattr(r, "event", "").startswith("cache.") and r.levelno >= logging.INFO
            for r in caplog.records
        )
        assert not (world.ws / ".orchestrator" / "cache").exists()


# ================================================================================== seam wiring
class TestSeams:
    def test_the_request_carries_the_engines_own_store_and_the_incremented_cycle(
        self, world: World
    ) -> None:
        world.run(single(), CostlyFakeExecutor())
        (req,) = world.cache.requests
        assert req.artifact_store is world.store and req.run_id
        assert req.dispatch_cycle == 1 and req.injected is False
        assert req.integration_active is False and req.repo_paths == {"core": str(world.ws)}

    def test_an_emit_injected_task_is_flagged_injected_and_a_static_one_is_not(
        self, world: World
    ) -> None:
        emitter = task("emitter", emit_tasks=True, task_manifest_path="out/manifest.json")
        injected = {
            "id": "child",
            "agent": "ag",
            "instruction": INSTRUCTION,
            "outputs": ["out/child.txt"],
            "depends_on": ["emitter"],
            "cache": True,
        }
        ex = CostlyFakeExecutor(emit_payloads={"emitter": {"tasks": [injected]}})
        state = world.run(workflow([emitter]), ex)
        assert state.tasks["child"].status == "succeeded"
        flags = {r.task.id: r.injected for r in world.cache.requests}
        assert flags["child"] is True and flags.get("emitter", False) is False

    def test_store_outcome_is_recorded_on_the_miss_record_after_the_settle(
        self, world: World
    ) -> None:
        state = world.run(single(), CostlyFakeExecutor())
        assert world.cache.stores == ["a"]
        rec = state.result_cache["a"]
        assert rec.stored is True and rec.store_reason is None

    def test_a_failed_task_is_never_stored(self, world: World) -> None:
        wf = single(retries=None)
        state = world.run(wf, CostlyFakeExecutor(behaviors={"a": "fail"}))
        assert state.tasks["a"].status == "failed" and world.cache.stores == []
        assert state.result_cache["a"].stored is False

    def test_the_default_engine_has_no_hook(self, world: World) -> None:
        orch = Orchestrator(CostlyFakeExecutor(), world.store, world.rs_store)
        assert orch._result_cache is None


# =================================================================================== U-AST-E
CACHE_IMPORT_HOME = "_result_cache_lookup"


def cache_import_homes(source: str) -> list[tuple[int, str]]:
    """(line, where) for every relative import of a `cache` module; where is `TYPE_CHECKING` (the
    guarded body of an `if TYPE_CHECKING:`), the enclosing function's name, or `module`."""

    def is_cache(node: ast.ImportFrom) -> bool:
        mods = [node.module or ""] if node.module else [a.name for a in node.names]
        return node.level >= 1 and any(m == "cache" or m.startswith("cache.") for m in mods)

    found: list[tuple[int, str]] = []

    def walk(node: ast.AST, where: str) -> None:
        if isinstance(node, ast.ImportFrom) and is_cache(node):
            found.append((node.lineno, where))
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name):
            if node.test.id == "TYPE_CHECKING":
                for stmt in node.body:  # the guarded body only, never its `else`
                    walk(stmt, "TYPE_CHECKING")
                for stmt in node.orelse:
                    walk(stmt, where)
                return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            where = node.name
        for child in ast.iter_child_nodes(node):
            walk(child, where)

    walk(ast.parse(source), "module")
    return found


class TestAstGuardEngine:
    def test_u_ast_e_every_cache_import_is_type_checking_or_the_lookup_method(self) -> None:
        homes = cache_import_homes(ENGINE_SOURCE.read_text())
        assert homes, "the engine imports no cache module at all: the guard would be vacuous"
        offenders = [(ln, w) for ln, w in homes if w not in ("TYPE_CHECKING", CACHE_IMPORT_HOME)]
        assert offenders == []
        assert any(w == CACHE_IMPORT_HOME for _, w in homes)  # the one lazy runtime import
        assert any(w == "TYPE_CHECKING" for _, w in homes)

    @pytest.mark.parametrize(
        "source",
        [
            "from .cache.types import X\n",  # module level
            "def other():\n    from .cache.keys import build\n",  # wrong function
            "if TYPE_CHECKING:\n    pass\nelse:\n    from .cache.types import X\n",  # else branch
            "class K:\n    def run(self):\n        from . import cache\n",
        ],
    )
    def test_u_ast_e_the_guard_detects_a_stray_import(self, source: str) -> None:
        homes = cache_import_homes(source)
        assert homes and all(w not in ("TYPE_CHECKING", CACHE_IMPORT_HOME) for _, w in homes)

    def test_u_ast_e_the_guard_accepts_the_two_sanctioned_homes(self) -> None:
        source = (
            "if TYPE_CHECKING:\n    from .cache.types import X\n"
            "class O:\n    def _result_cache_lookup(self):\n        from .cache.types import Y\n"
        )
        assert [w for _, w in cache_import_homes(source)] == ["TYPE_CHECKING", CACHE_IMPORT_HOME]

    def test_the_engine_text_has_no_raw_open_or_read_call_added_by_the_cache_seams(self) -> None:
        """Static-audit rule (HLD 8.7.1): no text of the cache seams may match open( / .read(."""
        text = ENGINE_SOURCE.read_text()
        start = text.index("    def _reverse_stale_charge(")
        end = text.index("    def _settle_completed_task(")
        seam_block = text[start:end]
        assert re.search(r"\bopen\s*\(|\.read\s*\(", seam_block) is None
