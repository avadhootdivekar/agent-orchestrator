"""Tests for the wave/barrier scheduler (T-j8YLGd, epic E-IasNXu, ADR-0007).

New file -- no existing test bodies are edited here (the AC-1 gate is that every
test in tests/test_engine*.py, and the rest of the suite, passes UNEDITED at the
default max_parallel=1; this file adds the extra coverage T-j8YLGd's own
acceptance criteria call for).

Covers:
- AC-1 (dedicated golden test): a representative diamond + router + emit_tasks +
  loop + budget workflow run at N=1, asserting explicit expected values for the
  final RunState and the ordered run.log event sequence -- captured from an
  actual run of the (now-verified byte-identical) N=1 path, encoding today's
  serial behavior as a regression trap.
- AC-2: parallel dispatch proof -- two independent tasks observed simultaneously
  in flight at max_parallel=2 (task B's execute() entering before task A is
  released), using a local thread-safe gated executor (the full harness is
  T-TNleFt; this is a minimal double, matching the epic's dependency note).
- AC-3: unit tests for `_predecessors`, `_is_barrier`, `_ready_ids`.
- AC-4: barrier isolation -- a barrier's execute() never overlaps a sibling's,
  and two barriers ready together run strictly sequentially.
- AC-6: no thread leak -- a worker exception still leaves
  `threading.active_count()` at baseline (pool.shutdown(wait=True) via `with`).
- AC-5: emit_tasks + loop still complete correctly at max_parallel=4 (the
  existing N=1-only integration tests live in test_dynamic_injection.py /
  test_loop_construct.py; this adds N=4 variants without touching those files).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.dag import build_dag
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    LoopSpec,
    RepoRef,
    RepoSet,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskContext,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests/test_engine.py, tests/test_engine_routing.py)
# ---------------------------------------------------------------------------

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}  # type: ignore[arg-type]


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _task(
    tid: str,
    depends_on: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    emit_tasks: bool = False,
    task_manifest_path: str | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/instructions/design.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
        emit_tasks=emit_tasks,
        task_manifest_path=task_manifest_path,
    )


def _workflow(
    tasks: list[TaskSpec],
    wf_id: str = "wf",
    loops: list[LoopSpec] | None = None,
    branches: list[RouterSpec] | None = None,
    budget: BudgetSpec | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
        loops=loops or [],
        branches=branches or [],
        budget=budget,
    )


def _bare_state(tasks: dict[str, TaskRunState]) -> RunState:
    """A minimal RunState for pure `_ready_ids` unit tests (no engine run)."""
    return RunState(
        run_id="r1",
        workflow_id="wf",
        repo_set="rs",
        started_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        tasks=tasks,
    )


def _orch(tmp_path: Path, executor: Executor | None = None, max_parallel: int = 1) -> Orchestrator:
    store, rs_store = _make_workspace(tmp_path)
    return Orchestrator(executor or FakeExecutor(), store, rs_store, max_parallel=max_parallel)


# ---------------------------------------------------------------------------
# AC-3: _predecessors
# ---------------------------------------------------------------------------


class TestPredecessors:
    def test_inverts_adjacency(self, tmp_path: Path) -> None:
        # a -> b, a -> c, b -> d, c -> d  (diamond)
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"]),
                _task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
                _task("c", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/c.txt"]),
                _task(
                    "d",
                    depends_on=["b", "c"],
                    inputs=["out/b.txt", "out/c.txt"],
                    outputs=["out/d.txt"],
                ),
            ]
        )
        graph = build_dag(wf)
        orch = _orch(tmp_path)
        preds = orch._predecessors(graph)
        assert preds["a"] == set()
        assert preds["b"] == {"a"}
        assert preds["c"] == {"a"}
        assert preds["d"] == {"b", "c"}

    def test_task_with_no_predecessors_has_empty_set(self, tmp_path: Path) -> None:
        wf = _workflow([_task("solo")])
        graph = build_dag(wf)
        orch = _orch(tmp_path)
        preds = orch._predecessors(graph)
        assert preds == {"solo": set()}


# ---------------------------------------------------------------------------
# AC-3: _is_barrier
# ---------------------------------------------------------------------------


class TestIsBarrier:
    def test_plain_task_is_not_a_barrier(self, tmp_path: Path) -> None:
        task = _task("a")
        wf = _workflow([task])
        orch = _orch(tmp_path)
        assert orch._is_barrier(task, wf) is False

    def test_emit_tasks_is_a_barrier(self, tmp_path: Path) -> None:
        task = _task("emitter", emit_tasks=True, task_manifest_path="out/m.json")
        wf = _workflow([task])
        orch = _orch(tmp_path)
        assert orch._is_barrier(task, wf) is True

    def test_loop_gate_task_is_a_barrier_but_body_task_is_not(self, tmp_path: Path) -> None:
        dev = _task("dev")
        review = _task("review", depends_on=["dev"])
        wf = _workflow(
            [dev, review],
            loops=[
                LoopSpec(
                    id="lp",
                    body=["dev", "review"],
                    gate_task_id="review",
                    gate_output_path="out/gate.json",
                )
            ],
        )
        orch = _orch(tmp_path)
        assert orch._is_barrier(review, wf) is True
        assert orch._is_barrier(dev, wf) is False

    def test_loop_gate_iter_clone_is_a_barrier(self, tmp_path: Path) -> None:
        """Reuses `_loop_for_gate`, which already strips the __iterN suffix --
        a cloned gate task (e.g. review__iter2) must still be recognized."""
        dev = _task("dev")
        review = _task("review", depends_on=["dev"])
        wf = _workflow(
            [dev, review],
            loops=[
                LoopSpec(
                    id="lp",
                    body=["dev", "review"],
                    gate_task_id="review",
                    gate_output_path="out/gate.json",
                )
            ],
        )
        orch = _orch(tmp_path)
        review_iter2 = _task("review__iter2")
        assert orch._is_barrier(review_iter2, wf) is True

    def test_router_task_id_is_a_barrier(self, tmp_path: Path) -> None:
        classify = _task("classify")
        leaf_a = _task("leaf-a", depends_on=["classify"])
        wf = _workflow(
            [classify, leaf_a],
            branches=[
                RouterSpec(
                    id="r1",
                    router_task_id="classify",
                    verdict_path="out/v.json",
                    routes={"x": RouteSpec(entry=["leaf-a"])},
                )
            ],
        )
        orch = _orch(tmp_path)
        assert orch._is_barrier(classify, wf) is True
        assert orch._is_barrier(leaf_a, wf) is False


# ---------------------------------------------------------------------------
# AC-3: _ready_ids
# ---------------------------------------------------------------------------


class TestReadyIds:
    def _diamond_order_preds(
        self, tmp_path: Path
    ) -> tuple[list[str], dict[str, set[str]], Orchestrator]:
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"]),
                _task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
                _task("c", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/c.txt"]),
                _task(
                    "d",
                    depends_on=["b", "c"],
                    inputs=["out/b.txt", "out/c.txt"],
                    outputs=["out/d.txt"],
                ),
            ]
        )
        graph = build_dag(wf)
        order = graph.topological_order()
        orch = _orch(tmp_path)
        preds = orch._predecessors(graph)
        return order, preds, orch

    def test_ready_only_when_all_predecessors_settled(self, tmp_path: Path) -> None:
        order, preds, orch = self._diamond_order_preds(tmp_path)
        state = _bare_state(
            {
                "a": TaskRunState(status="succeeded"),
                "b": TaskRunState(status="pending"),
                "c": TaskRunState(status="pending"),
                "d": TaskRunState(status="pending"),
            }
        )
        ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids=set())
        # b and c are both ready (their sole predecessor a succeeded); d is not
        # (its predecessors b/c haven't settled).
        assert ready == ["b", "c"]

    def test_running_predecessor_excludes_dependent_task(self, tmp_path: Path) -> None:
        order, preds, orch = self._diamond_order_preds(tmp_path)
        state = _bare_state(
            {
                "a": TaskRunState(status="running"),
                "b": TaskRunState(status="pending"),
                "c": TaskRunState(status="pending"),
                "d": TaskRunState(status="pending"),
            }
        )
        ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids={"a"})
        assert ready == []

    def test_pending_predecessor_excludes_dependent_task(self, tmp_path: Path) -> None:
        order, preds, orch = self._diamond_order_preds(tmp_path)
        state = _bare_state(
            {
                "a": TaskRunState(status="pending"),
                "b": TaskRunState(status="pending"),
                "c": TaskRunState(status="pending"),
                "d": TaskRunState(status="pending"),
            }
        )
        ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids=set())
        assert ready == ["a"]  # only the root is ready; b/c/d all wait on it

    def test_not_taken_predecessor_counts_as_settled(self, tmp_path: Path) -> None:
        order, preds, orch = self._diamond_order_preds(tmp_path)
        state = _bare_state(
            {
                "a": TaskRunState(status="not_taken"),
                "b": TaskRunState(status="pending"),
                "c": TaskRunState(status="pending"),
                "d": TaskRunState(status="pending"),
            }
        )
        ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids=set())
        assert ready == ["b", "c"]

    def test_results_follow_deterministic_order(self, tmp_path: Path) -> None:
        """Three independent roots, all ready simultaneously -- result order must
        match `order` (sorted-Kahn tie-break), not insertion/dict order."""
        wf = _workflow([_task("z"), _task("a"), _task("m")])
        graph = build_dag(wf)
        order = graph.topological_order()
        assert order == ["a", "m", "z"]  # sorted-Kahn: alphabetical for indeg-0 nodes
        orch = _orch(tmp_path)
        preds = orch._predecessors(graph)
        state = _bare_state({tid: TaskRunState() for tid in order})
        ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids=set())
        assert ready == ["a", "m", "z"]

    def test_done_and_in_flight_ids_are_excluded(self, tmp_path: Path) -> None:
        order, preds, orch = self._diamond_order_preds(tmp_path)
        state = _bare_state(
            {
                "a": TaskRunState(status="succeeded"),
                "b": TaskRunState(status="running"),
                "c": TaskRunState(status="succeeded"),
                "d": TaskRunState(status="pending"),
            }
        )
        # a is in `done`; b is in-flight (mirrors ts.status="running" too); c
        # already settled but not yet reflected in `done` (shouldn't happen in
        # practice, but the explicit in_flight_ids/done checks are independent
        # guards) -- ready must never re-offer any of the three.
        ready = orch._ready_ids(order, preds, state, done={"a", "c"}, in_flight_ids={"b"})
        assert ready == []  # d still blocked on b (running)

    def test_own_terminal_status_excluded_from_ready(self, tmp_path: Path) -> None:
        wf = _workflow([_task("a")])
        graph = build_dag(wf)
        order = graph.topological_order()
        orch = _orch(tmp_path)
        preds = orch._predecessors(graph)
        for status in ("succeeded", "skipped", "not_taken", "running"):
            state = _bare_state({"a": TaskRunState(status=status)})  # type: ignore[arg-type]
            ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids=set())
            assert ready == [], f"status={status} must not be ready"
        for status in ("pending", "failed", "cancelled", "timed_out"):
            state = _bare_state({"a": TaskRunState(status=status)})  # type: ignore[arg-type]
            ready = orch._ready_ids(order, preds, state, done=set(), in_flight_ids=set())
            assert ready == ["a"], f"status={status} should still be a ready candidate"


# ---------------------------------------------------------------------------
# AC-1: N=1 dedicated golden workflow (diamond + router + emit_tasks + loop +
# budget) -- explicit expected values captured from the verified N=1 path.
# ---------------------------------------------------------------------------


class TestN1GoldenWorkflow:
    """A representative workflow exercising every structural element the wave
    scheduler must special-case at N=1: a diamond fan-out/fan-in, a router
    (with a not-taken losing route), an emit_tasks injection, a 2-iteration
    loop, and a budget gate/charge/reconcile on every dispatched task.

    The expected RunState fields and the ordered run.log event list below are
    captured from an actual N=1 run (not hand-derived) -- this is the
    "explicit expected values encoding today's serial behavior" golden the
    ticket calls for. The authoritative regression gate remains "the existing
    suite passes unedited" (proven separately); this test locks in one
    additional, combined scenario the individual per-feature test files don't
    exercise together.
    """

    def _build(
        self, tmp_path: Path
    ) -> tuple[WorkflowSpec, Orchestrator, dict, dict, RunStateStore]:
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)
        (instr_dir / "design.md").write_text("do the task")

        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
            _task("c", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/c.txt"]),
            _task(
                "classify",
                depends_on=["b", "c"],
                inputs=["out/b.txt", "out/c.txt"],
                outputs=["out/classify-done.txt"],
            ),
            _task(
                "emitter",
                depends_on=["classify"],
                emit_tasks=True,
                task_manifest_path="out/emitter-manifest.json",
                outputs=["out/emitter-done.txt"],
            ),
            _task("dead_end", depends_on=["classify"], outputs=["out/dead_end.txt"]),
            _task(
                "dev",
                depends_on=["loopstart"],
                inputs=["out/loopstart.txt"],
                outputs=["out/dev.txt"],
            ),
            _task("review", depends_on=["dev"], inputs=["out/dev.txt"], outputs=["out/review.txt"]),
            _task("finalize", depends_on=["loop1"], outputs=["out/finalize.txt"]),
        ]
        router = RouterSpec(
            id="router1",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={"go": RouteSpec(entry=["emitter"]), "stop": RouteSpec(entry=["dead_end"])},
        )
        loop = LoopSpec(
            id="loop1",
            body=["dev", "review"],
            gate_task_id="review",
            gate_output_path="out/gate.json",
            max_iterations=3,
        )
        budget = BudgetSpec(total_tokens=100_000)
        wf = _workflow(tasks, wf_id="golden-wf", loops=[loop], branches=[router], budget=budget)

        (tmp_path / "out").mkdir(parents=True, exist_ok=True)
        (tmp_path / "out" / "verdict.json").write_text(json.dumps({"routes": ["go"]}))

        store, rs_store = _make_workspace(tmp_path)
        emitted_task = {
            "id": "loopstart",
            "agent": "ag",
            "instruction": "specs/instructions/design.md",
            "depends_on": ["emitter"],
            "outputs": ["out/loopstart.txt"],
        }
        executor = FakeExecutor(
            emit_payloads={"emitter": {"tasks": [emitted_task]}},
            gate_payloads={"review": [True, False]},
        )

        def clock() -> datetime:
            return _FIXED_DT

        budget_manager = DefaultBudgetManager(budget, clock)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            budget_manager=budget_manager,
            estimator=estimator,
            clock=clock,
            max_parallel=1,
        )
        return wf, orch, _fake_reposets(str(tmp_path)), _fake_agents(), rs_store

    def test_final_run_state_matches_captured_baseline(self, tmp_path: Path) -> None:
        wf, orch, reposets, agents, _rs_store = self._build(tmp_path)
        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"

        expected = {
            "a": ("succeeded", 1, "static"),
            "b": ("succeeded", 1, "static"),
            "c": ("succeeded", 1, "static"),
            "classify": ("succeeded", 1, "static"),
            "emitter": ("succeeded", 1, "static"),
            "dead_end": ("not_taken", 0, "static"),
            "dev": ("succeeded", 1, "static"),
            "review": ("succeeded", 1, "static"),
            "finalize": ("succeeded", 1, "static"),
            "loopstart": ("succeeded", 1, "injected"),
            "dev__iter2": ("succeeded", 1, "loop"),
            "review__iter2": ("succeeded", 1, "loop"),
        }
        actual = {tid: (ts.status, ts.attempts, ts.origin) for tid, ts in state.tasks.items()}
        assert actual == expected

        assert state.route_decisions == {"router1": ["go"]}
        assert state.loop_iterations == {"loop1": 2}
        assert {t.id for t in state.injected_tasks} == {"loopstart", "dev__iter2", "review__iter2"}
        assert state.tripped_breakers == []

        # Budget: every dispatched task charged then fully reconciled (nothing
        # left outstanding); the not-taken task never touches the budget gate.
        assert state.budget_counters.charged_estimate == {}
        assert state.budget_counters.reconciled_tasks == [
            "a",
            "b",
            "c",
            "classify",
            "emitter",
            "loopstart",
            "dev",
            "review",
            "dev__iter2",
            "review__iter2",
            "finalize",
        ]
        assert "dead_end" not in state.budget_counters.reconciled_tasks
        # Captured from an actual run (deterministic: fixed instruction content +
        # fixed FakeExecutor output naming) -- a regression here means the
        # dispatch-order/estimate math genuinely changed, not just this number.
        assert state.budget_counters.consumed_tokens == 14384

    def test_run_log_event_sequence_matches_captured_baseline(
        self, tmp_path: Path, read_jsonl
    ) -> None:
        wf, orch, reposets, agents, rs_store = self._build(tmp_path)
        state = orch.run(wf, reposets, agents)
        assert state.status == "succeeded"

        run_dir = Path(rs_store._path(state.run_id).parent)
        records = read_jsonl(run_dir / "run.log")
        events = [r.get("event") for r in records]

        expected_events = (
            ["run.start"]
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"] * 4  # a,b,c,classify
            + ["branch.route"]
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # emitter
            + ["task.injected"]
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # loopstart
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # dev
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # review
            + ["loop.iterate"]
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # dev__iter2
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # review__iter2
            + ["budget.charge", "task.start", "budget.reconcile", "task.end"]  # finalize
            + ["run.end"]
        )
        assert events == expected_events

        # dead_end (not_taken) never dispatches -- no task.start/task.end for it.
        dead_end_events = [r for r in records if r.get("task_id") == "dead_end"]
        assert dead_end_events == []


# ---------------------------------------------------------------------------
# Local thread-safe gated executor (T-j8YLGd minimal double; the full harness
# is T-TNleFt) -- used by AC-2 / AC-4 / AC-6 below.
# ---------------------------------------------------------------------------


class _GatedExecutor(Executor):
    """Records, per task id, the SET of other task ids already in flight the
    instant this task's `execute()` was entered (a point-in-time snapshot),
    plus a running high-water mark of concurrent in-flight tasks. Optionally
    blocks a named task on a `threading.Event` until released, and signals a
    second `threading.Event` the instant it enters -- lets a test prove "B
    entered before A was released" without relying on timing/sleeps.

    Thread-safe (a single `threading.Lock` guards the shared bookkeeping) --
    unlike `FakeExecutor`, which the epic ADR flags as unsafe for N>1 tests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self.entered_snapshot: dict[str, frozenset[str]] = {}
        self.entered_order: list[str] = []
        self.max_concurrent = 0
        self._release: dict[str, threading.Event] = {}
        self._entered: dict[str, threading.Event] = {}
        self._raise_for: set[str] = set()

    def gate(self, task_id: str) -> threading.Event:
        """Register *task_id* as gated; returns the release Event the test
        must `.set()` to let this task's `execute()` proceed."""
        release = threading.Event()
        self._release[task_id] = release
        return release

    def entered_event(self, task_id: str) -> threading.Event:
        """Returns an Event set the instant *task_id*'s `execute()` is entered."""
        ev = threading.Event()
        self._entered[task_id] = ev
        return ev

    def raise_for(self, task_id: str) -> None:
        self._raise_for.add(task_id)

    def execute(self, ctx: TaskContext) -> TaskResult:
        with self._lock:
            self.entered_snapshot[ctx.task_id] = frozenset(self._in_flight)
            self.entered_order.append(ctx.task_id)
            self._in_flight.add(ctx.task_id)
            self.max_concurrent = max(self.max_concurrent, len(self._in_flight))
        entered_ev = self._entered.get(ctx.task_id)
        if entered_ev is not None:
            entered_ev.set()
        try:
            if ctx.task_id in self._raise_for:
                raise RuntimeError(f"_GatedExecutor: forced worker exception for {ctx.task_id}")
            release_ev = self._release.get(ctx.task_id)
            if release_ev is not None:
                assert release_ev.wait(timeout=10), f"{ctx.task_id} was never released"
            for p in ctx.output_paths:
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                Path(p).write_text(f"output for {ctx.task_id}\n")
            if ctx.task_manifest_path:
                # emit_tasks barrier: write a valid, empty manifest -- these tests
                # only care about barrier scheduling, not what gets injected.
                Path(ctx.task_manifest_path).parent.mkdir(parents=True, exist_ok=True)
                Path(ctx.task_manifest_path).write_text(json.dumps({"tasks": []}))
            return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1, exit_code=0)
        finally:
            with self._lock:
                self._in_flight.discard(ctx.task_id)


# ---------------------------------------------------------------------------
# AC-2: parallel dispatch proof
# ---------------------------------------------------------------------------


class TestParallelDispatchProof:
    def test_two_independent_tasks_overlap_at_max_parallel_two(self, tmp_path: Path) -> None:
        wf = _workflow(
            [
                _task("a", outputs=["out/a.txt"]),
                _task("b", outputs=["out/b.txt"]),
            ]
        )
        gated = _GatedExecutor()
        a_release = gated.gate("a")
        b_entered = gated.entered_event("b")
        orch = _orch(tmp_path, gated, max_parallel=2)

        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            # b's execute() must be entered WHILE a is still blocked -- proves
            # both were genuinely dispatched together, not one-after-the-other.
            assert b_entered.wait(timeout=5), "task b never entered execute() -- no overlap"
            assert "a" in gated._in_flight or gated.max_concurrent >= 2
        finally:
            a_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        state = result["state"]
        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
        assert gated.max_concurrent >= 2
        # b entered while a was already in flight (a was submitted first, in
        # sorted-Kahn order, and hadn't been released yet).
        assert "a" in gated.entered_snapshot["b"]


# ---------------------------------------------------------------------------
# AC-4: barrier isolation
# ---------------------------------------------------------------------------


class TestBarrierIsolation:
    def test_barrier_never_overlaps_a_sibling(self, tmp_path: Path) -> None:
        """3 independent siblings (ids sorted before the barrier) + 1 emit_tasks
        barrier, all ready from the start, at max_parallel=4. The siblings must
        run concurrently (proving N=4 fan-out genuinely happens); the barrier
        must never start until every sibling has fully drained."""
        wf = _workflow(
            [
                _task("s1_sib", outputs=["out/s1.txt"]),
                _task("s2_sib", outputs=["out/s2.txt"]),
                _task("s3_sib", outputs=["out/s3.txt"]),
                _task(
                    "z_barrier",
                    emit_tasks=True,
                    task_manifest_path="out/z-manifest.json",
                    outputs=["out/z.txt"],
                ),
            ]
        )
        gated = _GatedExecutor()
        s1_release = gated.gate("s1_sib")
        s2_release = gated.gate("s2_sib")
        s3_release = gated.gate("s3_sib")
        all_siblings_entered = threading.Event()

        # Watch for all three siblings having entered concurrently before we
        # release any of them (proves real overlap, not a lucky race).
        s1_entered = gated.entered_event("s1_sib")
        s2_entered = gated.entered_event("s2_sib")
        s3_entered = gated.entered_event("s3_sib")

        orch = _orch(tmp_path, gated, max_parallel=4)
        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert s1_entered.wait(timeout=5)
            assert s2_entered.wait(timeout=5)
            assert s3_entered.wait(timeout=5)
            all_siblings_entered.set()
            # The barrier must NOT have started while siblings are still gated.
            assert "z_barrier" not in gated.entered_snapshot
        finally:
            s1_release.set()
            s2_release.set()
            s3_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        state = result["state"]
        assert state.status == "succeeded"
        assert gated.max_concurrent >= 3  # the 3 siblings genuinely overlapped
        # The barrier's snapshot at entry must show an EMPTY in-flight set --
        # nothing else was running when it started.
        assert gated.entered_snapshot["z_barrier"] == frozenset()
        assert state.tasks["z_barrier"].status == "succeeded"

    def test_two_barriers_ready_together_run_strictly_sequentially(self, tmp_path: Path) -> None:
        wf = _workflow(
            [
                _task(
                    "barrier_a",
                    emit_tasks=True,
                    task_manifest_path="out/a-manifest.json",
                    outputs=["out/ba.txt"],
                ),
                _task(
                    "barrier_b",
                    emit_tasks=True,
                    task_manifest_path="out/b-manifest.json",
                    outputs=["out/bb.txt"],
                ),
            ]
        )
        gated = _GatedExecutor()
        orch = _orch(tmp_path, gated, max_parallel=4)
        reposets = _fake_reposets(str(tmp_path))
        agents = _fake_agents()

        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert gated.max_concurrent == 1  # never more than one barrier at a time
        # Whichever ran second must have seen an EMPTY in-flight snapshot too.
        assert gated.entered_snapshot["barrier_a"] == frozenset()
        assert gated.entered_snapshot["barrier_b"] == frozenset()
        assert set(gated.entered_order) == {"barrier_a", "barrier_b"}


# ---------------------------------------------------------------------------
# AC-6: no thread leak
# ---------------------------------------------------------------------------


class TestNoThreadLeak:
    def test_worker_exception_leaves_no_dangling_threads(self, tmp_path: Path) -> None:
        wf = _workflow(
            [
                _task("ok1", outputs=["out/ok1.txt"]),
                _task("ok2", outputs=["out/ok2.txt"]),
                _task("boom", outputs=["out/boom.txt"]),
            ]
        )
        gated = _GatedExecutor()
        gated.raise_for("boom")
        orch = _orch(tmp_path, gated, max_parallel=3)

        baseline = threading.active_count()
        raised: list[BaseException] = []
        try:
            orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        except RuntimeError as exc:  # noqa: BLE001 -- deliberately broad: proving propagation
            raised.append(exc)

        assert len(raised) == 1
        assert "boom" in str(raised[0])
        # pool.shutdown(wait=True) via the `with` block in run() is the
        # structural guarantee (NFR-4) -- it runs even though the exception
        # propagated through the whole call stack.
        assert threading.active_count() == baseline


# ---------------------------------------------------------------------------
# AC-5: emit_tasks and loop still complete correctly at max_parallel=4
# ---------------------------------------------------------------------------


class TestEmitAndLoopAtMaxParallelFour:
    """N=4 variants of the existing N=1 emit_tasks/loop integration coverage
    (tests/test_dynamic_injection.py::TestEmitTasksIntegration,
    tests/test_loop_construct.py::TestLoopIntegration) -- added here rather
    than editing those files. Both scenarios are single-chain (each task
    depends on the previous), so N=4 exercises "barrier correctly drains solo
    even though extra concurrency headroom is available," not literal
    fan-out -- the fan-out case is covered by TestParallelDispatchProof and
    TestBarrierIsolation above.
    """

    def test_emit_injects_and_completes_at_n4(self, tmp_path: Path) -> None:
        manifest_path = "out/task-manifest.json"
        injected_output = "out/injected.txt"
        wf = _workflow(
            [_task("emitter", emit_tasks=True, task_manifest_path=manifest_path, outputs=[])],
            wf_id="dynamic-wf-n4",
        )
        emitted_task = {
            "id": "injected-a",
            "agent": "ag",
            "instruction": "specs/instructions/design.md",
            "outputs": [injected_output],
            "depends_on": ["emitter"],
        }
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "instructions" / "design.md").write_text("do the task")
        executor = FakeExecutor(emit_payloads={"emitter": {"tasks": [emitted_task]}})
        orch = _orch(tmp_path, executor, max_parallel=4)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["emitter"].status == "succeeded"
        assert state.tasks["injected-a"].status == "succeeded"
        assert state.tasks["injected-a"].origin == "injected"
        assert (tmp_path / injected_output).exists()

    def test_loop_runs_until_gate_false_at_n4(self, tmp_path: Path) -> None:
        gate_path = "out/review-verdict.json"
        (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "instructions" / "design.md").write_text("do the task")
        wf = _workflow(
            [
                _task("develop", outputs=["out/impl.md"]),
                _task("review", depends_on=["develop"], inputs=["out/impl.md"]),
                _task("finalize", depends_on=["dev-review-loop"], outputs=["out/final.md"]),
            ],
            wf_id="loop-wf-n4",
            loops=[
                LoopSpec(
                    id="dev-review-loop",
                    body=["develop", "review"],
                    gate_task_id="review",
                    gate_output_path=gate_path,
                    max_iterations=5,
                )
            ],
        )
        executor = FakeExecutor(gate_payloads={"review": [True, True, False]})
        orch = _orch(tmp_path, executor, max_parallel=4)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert "review__iter2" in state.tasks
        assert "review__iter3" in state.tasks
        assert "review__iter4" not in state.tasks
        assert state.tasks["finalize"].status == "succeeded"
        assert state.loop_iterations.get("dev-review-loop", 1) >= 3
