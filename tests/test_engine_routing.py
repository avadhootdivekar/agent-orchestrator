"""Engine-level integration tests for routing execution & run-success (T-m2h5t7,
epic E-rc7k2v). Exercises `Orchestrator.run()` end-to-end via the engine API +
FakeExecutor: the router-success hook (LLD §5.2), the not-taken skip (§5.3), join
handling + the `any`-join input relaxation (§5.4/§5.4a), injected-task route
inheritance (§5.5), and run-success/determinism (§5.6, NFR-2).

A CliRunner E2E companion lives in tests/test_e2e_cli.py::TestE2ERouting (memory
`engine-api-tests-dont-cover-cli` — engine-only tests have missed CLI wiring bugs
before on this project).

Covers TASK.md acceptance criteria 1-6 (AC7, the dual engine/CLI test requirement,
is satisfied by this file + the CliRunner companion together).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.dag import build_dag
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    RepoRef,
    RepoSet,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests/test_engine_breakers.py, test_route_cones.py)
# ---------------------------------------------------------------------------

_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _clock_at(offset: float = 0.0) -> Callable[[], datetime]:
    """Return a fixed clock (NFR-2: deterministic, no wall-clock reads)."""
    target = datetime.fromtimestamp(_BASE_EPOCH + offset, tz=UTC)
    return lambda: target


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


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
    join: str = "all",
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
        join=join,  # type: ignore[arg-type]
    )


def _write_verdict(tmp_path: Path, path: str, routes: list[str]) -> None:
    full = tmp_path / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps({"routes": routes}))


def _classify_router(default_route: str | None = None) -> RouterSpec:
    return RouterSpec(
        id="classify-router",
        router_task_id="classify",
        verdict_path="out/verdict.json",
        routes={
            "bug": RouteSpec(entry=["bug-fix"]),
            "documentation": RouteSpec(entry=["doc-fix"]),
        },
        default_route=default_route,
    )


def _two_route_tasks() -> list[TaskSpec]:
    """classify -> {bug-fix, doc-fix}; classify's own output is NOT the verdict
    path so FakeExecutor's stub write never clobbers the pre-seeded verdict."""
    return [
        _task("classify", outputs=["out/classify-done.txt"]),
        _task("bug-fix", depends_on=["classify"], outputs=["out/bug.txt"]),
        _task("doc-fix", depends_on=["classify"], outputs=["out/doc.txt"]),
    ]


# ---------------------------------------------------------------------------
# AC1: single-route selection
# ---------------------------------------------------------------------------


class TestSingleRouteSelection:
    def test_bug_route_only_activates_bug_cone(self, tmp_path: Path) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=_two_route_tasks(),
            branches=[_classify_router()],
        )
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["classify"].status == "succeeded"
        assert state.tasks["bug-fix"].status == "succeeded"
        assert state.tasks["bug-fix"].route == "classify-router:bug"

        # Unselected cone: not_taken, never dispatched (attempts == 0, no output written).
        assert state.tasks["doc-fix"].status == "not_taken"
        assert state.tasks["doc-fix"].attempts == 0
        assert state.tasks["doc-fix"].route == "classify-router:documentation"
        assert state.tasks["doc-fix"].not_taken_reason is not None
        assert not (tmp_path / "out" / "doc.txt").exists()

        assert state.route_decisions == {"classify-router": ["bug"]}

    def test_not_taken_task_never_charged_budget(self, tmp_path: Path) -> None:
        """AC1: no budget charge for the untaken cone — proven with a real
        BudgetManager/estimator wired in (not just the no-budget-manager default)."""
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=_two_route_tasks(),
            branches=[_classify_router()],
        )
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        clock = _clock_at()
        budget_manager = DefaultBudgetManager(BudgetSpec(total_tokens=1_000_000), clock)
        estimator = HeuristicTokenEstimator(store)

        orch = Orchestrator(
            FakeExecutor(),
            store,
            rs_store,
            budget_manager=budget_manager,
            estimator=estimator,
            clock=clock,
        )
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["doc-fix"].status == "not_taken"
        assert "doc-fix" not in state.budget_counters.charged_estimate
        assert "doc-fix" not in state.budget_counters.reconciled_tasks
        # Sanity: the activated task WAS charged/reconciled.
        assert "bug-fix" in state.budget_counters.reconciled_tasks


# ---------------------------------------------------------------------------
# AC2: multi-select
# ---------------------------------------------------------------------------


class TestMultiSelectRoutes:
    def test_both_cones_activate_third_stays_not_taken(self, tmp_path: Path) -> None:
        tasks = [
            _task("classify", outputs=["out/classify-done.txt"]),
            _task("bug-fix", depends_on=["classify"], outputs=["out/bug.txt"]),
            _task("doc-fix", depends_on=["classify"], outputs=["out/doc.txt"]),
            _task("perf-fix", depends_on=["classify"], outputs=["out/perf.txt"]),
        ]
        router = RouterSpec(
            id="classify-router",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={
                "bug": RouteSpec(entry=["bug-fix"]),
                "documentation": RouteSpec(entry=["doc-fix"]),
                "performance": RouteSpec(entry=["perf-fix"]),
            },
        )
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks, branches=[router])
        _write_verdict(tmp_path, "out/verdict.json", ["bug", "documentation"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["bug-fix"].status == "succeeded"
        assert state.tasks["doc-fix"].status == "succeeded"
        assert state.tasks["perf-fix"].status == "not_taken"
        assert state.route_decisions == {"classify-router": ["bug", "documentation"]}


# ---------------------------------------------------------------------------
# AC3: join handling
# ---------------------------------------------------------------------------


class TestJoinHandling:
    def test_join_all_convergence_propagates_not_taken(self, tmp_path: Path) -> None:
        tasks = [
            *_two_route_tasks(),
            _task(
                "converge",
                depends_on=["bug-fix", "doc-fix"],
                outputs=["out/converge.txt"],
                join="all",
            ),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            branches=[_classify_router()],
        )
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # A not_taken dependency never fails the run (§5.6) -- only propagates
        # not_taken to the join="all" convergence task.
        assert state.status == "succeeded"
        assert state.tasks["doc-fix"].status == "not_taken"
        assert state.tasks["converge"].status == "not_taken"
        assert "join=all" in (state.tasks["converge"].not_taken_reason or "")
        assert state.tasks["converge"].attempts == 0

    def test_join_any_runs_with_one_live_dep_and_tolerates_missing_input(
        self, tmp_path: Path
    ) -> None:
        tasks = [
            *_two_route_tasks(),
            _task(
                "converge",
                depends_on=["bug-fix", "doc-fix"],
                # Declared inputs matching each branch's output -- doc-fix's
                # output never gets written (it's not_taken); §5.4a must
                # exclude it from the missing-input check.
                inputs=["out/bug.txt", "out/doc.txt"],
                outputs=["out/converge.txt"],
                join="any",
            ),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            branches=[_classify_router()],
        )
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["doc-fix"].status == "not_taken"
        assert state.tasks["converge"].status == "succeeded"
        assert (tmp_path / "out" / "converge.txt").exists()

    def test_join_any_not_taken_when_every_dep_not_taken(self, tmp_path: Path) -> None:
        """A join="any" task whose every effective dependency is not_taken must
        itself become not_taken (never fails the run). Uses a THIRD route so the
        router can select something other than the two routes converge depends
        on -- both of converge's deps go not_taken simultaneously."""
        tasks = [
            _task("classify", outputs=["out/classify-done.txt"]),
            _task("bug-fix", depends_on=["classify"], outputs=["out/bug.txt"]),
            _task("doc-fix", depends_on=["classify"], outputs=["out/doc.txt"]),
            _task("other-fix", depends_on=["classify"], outputs=["out/other.txt"]),
            _task(
                "converge",
                depends_on=["bug-fix", "doc-fix"],
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
                "other": RouteSpec(entry=["other-fix"]),
            },
        )
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks, branches=[router])
        _write_verdict(tmp_path, "out/verdict.json", ["other"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.route_decisions == {"classify-router": ["other"]}
        assert state.tasks["other-fix"].status == "succeeded"
        assert state.tasks["bug-fix"].status == "not_taken"
        assert state.tasks["doc-fix"].status == "not_taken"
        assert state.tasks["converge"].status == "not_taken"
        assert state.tasks["converge"].attempts == 0


# ---------------------------------------------------------------------------
# AC4: empty/unknown verdict + default_route
# ---------------------------------------------------------------------------


class TestEmptyOrUnknownVerdict:
    def test_empty_verdict_no_default_route_fails_run(self, tmp_path: Path, read_jsonl) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=_two_route_tasks(),
            branches=[_classify_router(default_route=None)],
        )
        _write_verdict(tmp_path, "out/verdict.json", [])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.route_decisions == {}

        log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        records = read_jsonl(log_path)
        route_events = [r for r in records if r.get("event") == "branch.route"]
        assert route_events, "expected a branch.route event in the run log"
        assert any("error" in r for r in route_events)

    def test_unknown_route_id_no_default_route_fails_run(self, tmp_path: Path) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=_two_route_tasks(),
            branches=[_classify_router(default_route=None)],
        )
        _write_verdict(tmp_path, "out/verdict.json", ["not-a-real-route"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"

    def test_empty_verdict_with_default_route_activates_default(self, tmp_path: Path) -> None:
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=_two_route_tasks(),
            branches=[_classify_router(default_route="documentation")],
        )
        _write_verdict(tmp_path, "out/verdict.json", [])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.route_decisions == {"classify-router": ["documentation"]}
        assert state.tasks["doc-fix"].status == "succeeded"
        assert state.tasks["bug-fix"].status == "not_taken"


# ---------------------------------------------------------------------------
# AC5: injected tasks inherit route; not_taken emitter never injects
# ---------------------------------------------------------------------------


class TestInjectedTaskRouteInheritance:
    def test_injected_child_inherits_activated_route_untaken_emitter_never_injects(
        self, tmp_path: Path
    ) -> None:
        tasks = [
            _task("classify", outputs=["out/classify-done.txt"]),
            TaskSpec(
                id="bug-emit",
                agent="ag",
                instruction="specs/examples/instructions/design.md",
                depends_on=["classify"],
                outputs=["out/bug-emit-done.txt"],
                emit_tasks=True,
                task_manifest_path="out/bug-manifest.json",
            ),
            TaskSpec(
                id="doc-emit",
                agent="ag",
                instruction="specs/examples/instructions/design.md",
                depends_on=["classify"],
                outputs=["out/doc-emit-done.txt"],
                emit_tasks=True,
                task_manifest_path="out/doc-manifest.json",
            ),
        ]
        router = RouterSpec(
            id="classify-router",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={
                "bug": RouteSpec(entry=["bug-emit"]),
                "documentation": RouteSpec(entry=["doc-emit"]),
            },
        )
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks, branches=[router])
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        executor = FakeExecutor(
            emit_payloads={
                "bug-emit": {
                    "tasks": [
                        {
                            "id": "bug-child",
                            "agent": "ag",
                            "instruction": "specs/examples/instructions/design.md",
                            "outputs": ["out/bug-child.txt"],
                        }
                    ]
                },
                "doc-emit": {
                    "tasks": [
                        {
                            "id": "doc-child",
                            "agent": "ag",
                            "instruction": "specs/examples/instructions/design.md",
                            "outputs": ["out/doc-child.txt"],
                        }
                    ]
                },
            }
        )
        orch = Orchestrator(executor, store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        # Emitter on the activated route: ran, its child was injected and
        # inherits the SAME route as the emitter.
        assert state.tasks["bug-emit"].status == "succeeded"
        assert state.tasks["bug-emit"].route == "classify-router:bug"
        assert "bug-child" in state.tasks
        assert state.tasks["bug-child"].status == "succeeded"
        assert state.tasks["bug-child"].route == "classify-router:bug"
        assert state.tasks["bug-child"].origin == "injected"

        # Emitter on the UNSELECTED route: never dispatched, so it never injects.
        assert state.tasks["doc-emit"].status == "not_taken"
        assert "doc-child" not in state.tasks


# ---------------------------------------------------------------------------
# AC6: determinism (NFR-2)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def _run_once(self, ws: Path) -> tuple[WorkflowSpec, RunState]:
        tasks = [
            *_two_route_tasks(),
            _task(
                "converge",
                depends_on=["bug-fix", "doc-fix"],
                outputs=["out/converge.txt"],
                join="any",
                inputs=["out/bug.txt", "out/doc.txt"],
            ),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            branches=[_classify_router()],
        )
        _write_verdict(ws, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(ws)
        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(ws)), _fake_agents())
        return wf, state

    def test_identical_verdict_produces_identical_activation_set_and_order(
        self, tmp_path: Path
    ) -> None:
        ws1 = tmp_path / "run1"
        ws2 = tmp_path / "run2"
        ws1.mkdir()
        ws2.mkdir()

        wf1, state1 = self._run_once(ws1)
        wf2, state2 = self._run_once(ws2)

        statuses1 = {tid: ts.status for tid, ts in state1.tasks.items()}
        statuses2 = {tid: ts.status for tid, ts in state2.tasks.items()}
        assert statuses1 == statuses2
        assert state1.route_decisions == state2.route_decisions
        assert state1.status == state2.status == "succeeded"

        # Same workflow shape -> identical topological order across both runs,
        # independent of the runtime routing verdict (NFR-2).
        assert build_dag(wf1).topological_order() == build_dag(wf2).topological_order()
