"""Workflow scenarios for the NFR-1 no-op proof (I-1; HLD 8.7.5).

Plain functions, no pytest: they run inside the poisoned-import subprocess
(`_noop_subprocess.py`). Every scenario builds a fresh workspace, drives a real `Orchestrator`
with the cache OFF (`result_cache=None`, passed only when the engine accepts that argument)
and returns the final `RunState`. The workflow shapes are lifted from the engine tests that
already cover each feature (see each docstring) rather than inventing new APIs.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.breakers import BREAKER_REGISTRY, Breaker, BreakerContext, TripResult
from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    CircuitBreakerSpec,
    EstimatorConfig,
    HookRef,
    HookSpec,
    IntegrationSpec,
    LoopSpec,
    RateLimit,
    RepoRef,
    RepoSet,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)
_STUB_INSTRUCTION = "specs/instructions/stub.md"
_AGENT_ID = "ag"
_REPO_SET_ID = "rs"
_PARALLEL_WIDTH = 3
_GIT_REPO_DIR = "repo"
_STUB_BREAKER_CONDITION = "noop_proof.always_trip"
_RESULT_CACHE_KWARG = "result_cache"


def fixed_clock() -> datetime:
    return _FIXED_DT


@dataclass(frozen=True)
class Outcome:
    """What a scenario hands back: the final state and the status it must have reached."""

    state: RunState
    expected_status: str


def _task(tid: str, **kwargs: Any) -> TaskSpec:
    return TaskSpec(id=tid, agent=_AGENT_ID, instruction=_STUB_INSTRUCTION, **kwargs)


def _workflow(tasks: list[TaskSpec], wf_id: str = "noop-wf", **kwargs: Any) -> WorkflowSpec:
    return WorkflowSpec(version="1.0", id=wf_id, repo_set=_REPO_SET_ID, tasks=tasks, **kwargs)


def _stores(ws: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(ws))
    return store, RunStateStore(str(ws), store, clock=fixed_clock)


def _reposets(ws: Path, repo_path: str = ".") -> dict[str, RepoSet]:
    repo = RepoRef(id="core", path=repo_path, role="primary")
    return {_REPO_SET_ID: RepoSet(workspace_root=str(ws), repos=[repo])}


def _agents() -> dict[str, AgentSpec]:
    return {_AGENT_ID: AgentSpec(executor="fake")}


def _write_stub_instruction(ws: Path) -> None:
    path = ws / _STUB_INSTRUCTION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# stub instruction\n")


def build_orchestrator(
    ws: Path,
    executor: FakeExecutor,
    *,
    store: LocalFsArtifactStore | None = None,
    rs_store: RunStateStore | None = None,
    **kwargs: Any,
) -> Orchestrator:
    """An `Orchestrator` with the result cache explicitly OFF.

    `result_cache=None` is passed only when the constructor has that parameter (T-XpF1pF), so
    the same scenarios are valid on base code and after the engine hooks land.
    """
    if store is None or rs_store is None:
        store, rs_store = _stores(ws)
    kwargs.setdefault("clock", fixed_clock)  # engine and run-state share one fixed clock
    if _RESULT_CACHE_KWARG in inspect.signature(Orchestrator.__init__).parameters:
        kwargs[_RESULT_CACHE_KWARG] = None
    return Orchestrator(executor, store, rs_store, **kwargs)


def _run(
    ws: Path,
    wf: WorkflowSpec,
    executor: FakeExecutor | None = None,
    reposets: dict[str, RepoSet] | None = None,
    run_state: RunState | None = None,
    **kwargs: Any,
) -> RunState:
    _write_stub_instruction(ws)
    orch = build_orchestrator(ws, executor or FakeExecutor(), **kwargs)
    return orch.run(wf, reposets or _reposets(ws), _agents(), run_state=run_state)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def serial(ws: Path) -> Outcome:
    """Chain a -> b -> c plus an independent d, max_parallel=1 (default engine)."""
    tasks = [
        _task("a", outputs=["out/a.txt"]),
        _task("b", depends_on=["a"], inputs=["out/a.txt"], outputs=["out/b.txt"]),
        _task("c", depends_on=["b"], inputs=["out/b.txt"], outputs=["out/c.txt"]),
        _task("d", outputs=["out/d.txt"]),
    ]
    state = _run(ws, _workflow(tasks), max_parallel=1)
    assert all(ts.status == "succeeded" for ts in state.tasks.values()), state.tasks
    return Outcome(state, "succeeded")


def parallel(ws: Path) -> Outcome:
    """Three independent tasks fan in to a fourth, max_parallel=3 (wave scheduler)."""
    leaves = [_task(f"leaf{i}", outputs=[f"out/leaf{i}.txt"]) for i in range(_PARALLEL_WIDTH)]
    join = _task(
        "join",
        depends_on=[t.id for t in leaves],
        inputs=[o for t in leaves for o in t.outputs],
        outputs=["out/join.txt"],
    )
    state = _run(ws, _workflow([*leaves, join]), max_parallel=_PARALLEL_WIDTH)
    assert all(ts.status == "succeeded" for ts in state.tasks.values()), state.tasks
    return Outcome(state, "succeeded")


def emit(ws: Path) -> Outcome:
    """`emit_tasks` injection (tests/test_dynamic_injection.py::TestEmitTasksIntegration)."""
    emitter = _task("emitter", emit_tasks=True, task_manifest_path="output/task-manifest.json")
    injected = {
        "id": "injected-a",
        "agent": _AGENT_ID,
        "instruction": _STUB_INSTRUCTION,
        "outputs": ["output/injected.txt"],
        "depends_on": ["emitter"],
    }
    executor = FakeExecutor(emit_payloads={"emitter": {"tasks": [injected]}})
    state = _run(ws, _workflow([emitter]), executor)
    assert state.tasks["injected-a"].status == "succeeded", "injected task did not run"
    assert state.tasks["injected-a"].origin == "injected"
    return Outcome(state, "succeeded")


def loop(ws: Path) -> Outcome:
    """Gate-driven loop (tests/test_loop_construct.py::TestLoopIntegration): iter1..iter3."""
    gate_path = "output/review-verdict.json"
    tasks = [
        _task("develop", outputs=["output/impl.md"]),
        _task("review", inputs=["output/impl.md"], depends_on=["develop"]),
        _task("finalize", depends_on=["dev-review-loop"], outputs=["output/final.md"]),
    ]
    loop_spec = LoopSpec(
        id="dev-review-loop",
        body=["develop", "review"],
        gate_task_id="review",
        gate_output_path=gate_path,
        max_iterations=5,
    )
    executor = FakeExecutor(gate_payloads={"review": [True, True, False]})
    state = _run(ws, _workflow(tasks, loops=[loop_spec]), executor)
    assert "review__iter3" in state.tasks and "review__iter4" not in state.tasks, list(state.tasks)
    assert state.tasks["finalize"].status == "succeeded"
    return Outcome(state, "succeeded")


def router_join_any(ws: Path) -> Outcome:
    """Router with `join: any` (tests/test_engine_routing.py::TestJoinHandling)."""
    verdict_path = ws / "out" / "verdict.json"
    verdict_path.parent.mkdir(parents=True, exist_ok=True)
    verdict_path.write_text(json.dumps({"routes": ["bug"]}))
    tasks = [
        _task("classify", outputs=["out/classify-done.txt"]),
        _task("bug-fix", depends_on=["classify"], outputs=["out/bug.txt"]),
        _task("doc-fix", depends_on=["classify"], outputs=["out/doc.txt"]),
        _task(
            "converge",
            depends_on=["bug-fix", "doc-fix"],
            inputs=["out/bug.txt", "out/doc.txt"],
            outputs=["out/converge.txt"],
            join="any",
        ),
    ]
    router = RouterSpec(
        id="classify-router",
        router_task_id="classify",
        verdict_path="out/verdict.json",
        routes={
            "bug": RouteSpec(entry=["bug-fix"]),
            "documentation": RouteSpec(entry=["doc-fix"]),
        },
    )
    state = _run(ws, _workflow(tasks, branches=[router]))
    assert state.tasks["doc-fix"].status == "not_taken"
    assert state.tasks["converge"].status == "succeeded"
    return Outcome(state, "succeeded")


def budget_wait(ws: Path) -> Outcome:
    """Budget `on_exhaustion=wait` (tests/test_engine_budget.py::TestRateWindowWait).

    The rate window starts full; the clock rolls it after a few reads, so the engine must call
    the (recording, non-sleeping) sleeper and then admit the task.
    """
    base = _FIXED_DT.timestamp()
    window_tokens = 200
    budget = BudgetSpec(
        rate=RateLimit(tokens=window_tokens, window_seconds=60),
        on_exhaustion="wait",
        estimator=EstimatorConfig(
            chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=10
        ),
    )
    reads = [0]

    def advancing_clock() -> datetime:
        reads[0] += 1
        offset = 0 if reads[0] <= 5 else 61  # window rolls after the first few reads
        return datetime.fromtimestamp(base + offset, tz=UTC)

    sleeps: list[float] = []
    store, rs_store = _stores(ws)
    wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget)
    initial = rs_store.new_run(wf)
    initial.budget_counters.window_start_epoch = base
    initial.budget_counters.window_consumed_tokens = window_tokens
    rs_store.save(initial)
    state = _run(
        ws,
        wf,
        store=store,
        rs_store=rs_store,
        run_state=initial,
        sleeper=sleeps.append,
        budget_manager=DefaultBudgetManager(budget, advancing_clock),
        estimator=HeuristicTokenEstimator(store),
        clock=advancing_clock,
    )
    assert sleeps, "budget wait never slept: the scenario did not exercise the wait path"
    return Outcome(state, "succeeded")


def breakers_quiet(ws: Path) -> Outcome:
    """Real MVP breakers are evaluated at each boundary but never trip."""
    tasks = [
        _task("a", outputs=["out/a.txt"]),
        _task("b", depends_on=["a"], outputs=["out/b.txt"]),
    ]
    breakers = [
        CircuitBreakerSpec(
            id="few-failures", condition="task_failures", action="fail", threshold=5
        ),
        CircuitBreakerSpec(
            id="wall-clock", condition="run_wall_clock_seconds", action="stop", threshold=3600
        ),
    ]
    state = _run(ws, _workflow(tasks, circuit_breakers=breakers))
    assert state.tripped_breakers == []
    return Outcome(state, "succeeded")


class _AlwaysTripBreaker(Breaker):
    """Trips at the first boundary it is asked about (tests/test_engine_breakers.py)."""

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        return TripResult(detail={"reason": "noop-proof"})


def breakers_trip(ws: Path) -> Outcome:
    """A tripping breaker halts the run cleanly: terminal `failed`, one recorded trip."""
    BREAKER_REGISTRY[_STUB_BREAKER_CONDITION] = _AlwaysTripBreaker()
    try:
        tasks = [_task("a", outputs=["out/a.txt"]), _task("b", depends_on=["a"])]
        breaker = CircuitBreakerSpec(id="trip", condition=_STUB_BREAKER_CONDITION, action="fail")
        state = _run(ws, _workflow(tasks, circuit_breakers=[breaker]))
    finally:
        BREAKER_REGISTRY.pop(_STUB_BREAKER_CONDITION, None)
    assert [t.id for t in state.tripped_breakers] == ["trip"]
    assert state.tasks["b"].status == "pending"
    return Outcome(state, "failed")


def hooks(ws: Path) -> Outcome:
    """Passing pre_hook and post_hook around a task (tests/test_engine_hooks.py)."""
    script = ws / "hook_ok.py"
    script.write_text("raise SystemExit(0)\n")
    registry = {"ok": HookSpec(command=[sys.executable, str(script)])}
    task = _task(
        "t1",
        outputs=["out/t1.txt"],
        pre_hook=HookRef(use="ok"),
        post_hook=HookRef(use="ok"),
    )
    state = _run(ws, _workflow([task], hooks=registry, defaults=WorkflowDefaults()))
    ts = state.tasks["t1"]
    assert ts.pre_hook_result is not None and ts.pre_hook_result.status == "passed"
    assert ts.post_hook_result is not None and ts.post_hook_result.status == "passed"
    return Outcome(state, "succeeded")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_git_repo(ws: Path) -> Path:
    """A real repository at `<ws>/repo` with a repo-local identity (HOME is sandboxed)."""
    repo = ws / _GIT_REPO_DIR
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "noop-proof"], repo)
    _git(["config", "user.email", "noop-proof@example.invalid"], repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def isolation(ws: Path) -> Outcome:
    """Worktree isolation + integration on a real git repo (tests/test_engine_isolation.py)."""
    _init_git_repo(ws)
    (ws / "out").mkdir()
    tasks = [
        _task("a", outputs=["out/a.txt"]),
        _task("b", depends_on=["a"], outputs=["out/b.txt"]),
    ]
    wf = _workflow(
        tasks,
        defaults=WorkflowDefaults(isolation="worktree"),
        integration=IntegrationSpec(sync_checkout="never"),
    )
    executor = FakeExecutor(
        repo_writes={
            "a": {"core": {"a.txt": "from a\n"}},
            "b": {"core": {"b.txt": "from b\n"}},
        }
    )
    state = _run(ws, wf, executor, reposets=_reposets(ws, _GIT_REPO_DIR))
    assert state.integration.active, f"isolation degraded: {state.integration.degraded_reason}"
    assert all(ti.status == "integrated" for ti in state.task_integration.values())
    return Outcome(state, "succeeded")


SCENARIOS: dict[str, Callable[[Path], Outcome]] = {
    fn.__name__: fn
    for fn in (
        serial,
        parallel,
        emit,
        loop,
        router_join_any,
        budget_wait,
        breakers_quiet,
        breakers_trip,
        hooks,
        isolation,
    )
}
