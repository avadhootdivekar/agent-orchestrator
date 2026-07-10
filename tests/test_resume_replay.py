"""Tests for T-t4m8x1-resume-replay (epic E-rc7k2v): routing + breaker facts survive
``ao resume``. Covers TASK.md acceptance criteria 1-6:

  AC1: router not re-run on resume; recorded route replays; previously-not_taken
       tasks stay not_taken (never reset to pending).
  AC2: a task still pending at stop but belonging to an unselected route's cone is
       re-derived to not_taken on resume, deterministically from persisted
       ``state.route_decisions`` (never a fresh verdict read) -- idempotent across
       repeated ``prepare_resume`` calls.
  AC3: ``stop_file`` breaker re-evaluates fresh on resume: removed -> not re-tripped;
       left in place -> trips (no production change -- LLD §9 says the existing
       breaker-evaluation-every-iteration design already delivers this).
  AC4: ``injected_task_count`` re-trips immediately when the persisted injected set
       is still over cap; failure-count breakers (``task_failures``/
       ``consecutive_failures``) start clean on resume because ``prepare_resume``'s
       existing failed->pending reset already zeroes their counters.
  AC5: an old ``state.json`` without ``route_decisions``/``tripped_breakers`` resumes
       fine via pydantic defaults (NFR-5).
  AC6: engine-API tests below + a CliRunner E2E companion
       (``TestE2EResumeReplayRouting``), per memory `engine-api-tests-dont-cover-cli`.

Helpers mirror tests/test_engine_routing.py and tests/test_engine_breakers.py.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    RepoRef,
    RepoSet,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

runner = CliRunner()

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
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
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
    )


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


def _write_verdict(tmp_path: Path, path: str, routes: list[str]) -> None:
    full = tmp_path / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps({"routes": routes}))


def _routing_workflow_with_missing_input() -> WorkflowSpec:
    """classify -> {bug-fix (route "bug", requires an input that doesn't exist yet),
    doc-fix (route "documentation")}. Lets a run stop mid-way deterministically via a
    missing-required-input failure (engine.py's dispatch-time input check) without
    needing FakeExecutor behavior injection -- the CLI's DispatchExecutor wires a
    plain, un-configured FakeExecutor (executors/__init__.py), so this is also what
    the CliRunner E2E test below reuses.
    """
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=[
            _task("classify", outputs=["out/classify-done.txt"]),
            _task(
                "bug-fix",
                depends_on=["classify"],
                inputs=["out/bug-input.txt"],
                outputs=["out/bug.txt"],
            ),
            _task("doc-fix", depends_on=["classify"], outputs=["out/doc.txt"]),
        ],
        branches=[_classify_router()],
    )


# ---------------------------------------------------------------------------
# AC1: router not re-run; recorded route replays; not_taken stays not_taken.
# ---------------------------------------------------------------------------


class TestRouterNotRerunAndNotTakenPersists:
    def test_resume_replays_route_keeps_not_taken_and_completes(self, tmp_path: Path) -> None:
        wf = _routing_workflow_with_missing_input()
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        orch1 = Orchestrator(FakeExecutor(), store, rs_store)
        state1 = orch1.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state1.status == "failed"
        assert state1.tasks["classify"].status == "succeeded"
        classify_attempts_before = state1.tasks["classify"].attempts
        assert state1.tasks["doc-fix"].status == "not_taken"
        assert state1.tasks["bug-fix"].status == "failed"
        assert state1.route_decisions == {"classify-router": ["bug"]}
        run_id = state1.run_id

        # Provide the missing input so bug-fix can succeed this time, then resume.
        (tmp_path / "out" / "bug-input.txt").write_text("now present")

        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)

        # Router untouched -- kept succeeded, same attempts (never re-dispatched).
        assert existing.tasks["classify"].status == "succeeded"
        assert existing.tasks["classify"].attempts == classify_attempts_before
        # Not-taken task never reset to pending by prepare_resume.
        assert existing.tasks["doc-fix"].status == "not_taken"
        # Failed task reset to pending for a retry.
        assert existing.tasks["bug-fix"].status == "pending"
        # route_decisions replay unchanged -- prepare_resume never rewrites it.
        assert existing.route_decisions == {"classify-router": ["bug"]}

        orch2 = Orchestrator(FakeExecutor(), store, rs_store)
        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)

        assert state2.status == "succeeded"
        assert state2.tasks["classify"].attempts == classify_attempts_before
        assert state2.tasks["bug-fix"].status == "succeeded"
        assert state2.tasks["doc-fix"].status == "not_taken"
        assert not (tmp_path / "out" / "doc.txt").exists()


# ---------------------------------------------------------------------------
# AC2: pending task in an unselected cone is re-derived to not_taken; idempotent.
# ---------------------------------------------------------------------------


class TestPendingTaskInUnselectedConeRederived:
    def test_pending_unselected_cone_task_rederived_and_idempotent(self, tmp_path: Path) -> None:
        wf = _routing_workflow_with_missing_input()
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.tasks["doc-fix"].status == "not_taken"
        assert state.route_decisions == {"classify-router": ["bug"]}

        # Simulate a crash between `_on_router_success` persisting route_decisions
        # and marking doc-fix's cone not_taken (a real narrow race in engine.py) by
        # rolling doc-fix back to "pending" while leaving route_decisions (the
        # source of truth) untouched -- exactly the scenario prepare_resume's
        # re-derivation step exists for.
        state.tasks["doc-fix"] = TaskRunState(status="pending")
        rs_store.save(state)

        reloaded = rs_store.load(state.run_id)
        resumed_once = rs_store.prepare_resume(reloaded, wf)

        assert resumed_once.tasks["doc-fix"].status == "not_taken"
        assert resumed_once.tasks["doc-fix"].route == "classify-router:documentation"
        reason = resumed_once.tasks["doc-fix"].not_taken_reason
        assert reason is not None
        assert "router=classify" in reason
        assert "route=documentation" in reason

        # Idempotent: calling prepare_resume again changes nothing further.
        first_pass = resumed_once.tasks["doc-fix"].model_copy()
        resumed_twice = rs_store.prepare_resume(resumed_once, wf)
        assert resumed_twice.tasks["doc-fix"] == first_pass

    def test_rederivation_never_overrides_a_settled_task(self, tmp_path: Path) -> None:
        """Risk guard (TASK.md Risks): re-deriving not_taken must not clobber a task
        that legitimately succeeded before deactivation."""
        wf = _routing_workflow_with_missing_input()
        _write_verdict(tmp_path, "out/verdict.json", ["bug"])
        store, rs_store = _make_workspace(tmp_path)

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # Pretend doc-fix actually completed successfully before the router settled
        # on "bug" (an edge case the guard must protect against).
        (tmp_path / "out" / "doc.txt").write_text("already done")
        state.tasks["doc-fix"] = TaskRunState(status="succeeded", outputs_present=True)
        rs_store.save(state)

        reloaded = rs_store.load(state.run_id)
        resumed = rs_store.prepare_resume(reloaded, wf)

        assert resumed.tasks["doc-fix"].status == "succeeded"


# ---------------------------------------------------------------------------
# AC3: stop_file breaker re-evaluates fresh on resume.
# ---------------------------------------------------------------------------


class TestStopFileBreakerResume:
    """No production change needed here (LLD §9) -- proves the existing
    breaker-evaluation-every-iteration design already delivers the behavior."""

    def _stopped_run(
        self, tmp_path: Path
    ) -> tuple[WorkflowSpec, RunStateStore, LocalFsArtifactStore, str]:
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
            _task("c", depends_on=["b"], outputs=["out/c.txt"]),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="halt", condition="stop_file", action="stop", path="control/stop"
                ),
            ],
        )
        store, rs_store = _make_workspace(tmp_path)
        # Task "b" fails for a reason unrelated to the breaker (no stop_file present
        # yet) so run1 halts before the breaker ever gets a chance to trip/record.
        orch1 = Orchestrator(FakeExecutor(behaviors={"b": "fail"}), store, rs_store)
        state1 = orch1.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state1.status == "failed"
        assert state1.tasks["a"].status == "succeeded"
        assert state1.tasks["b"].status == "failed"
        assert state1.tripped_breakers == []
        return wf, rs_store, store, state1.run_id

    def test_removed_stop_file_does_not_retrip(self, tmp_path: Path) -> None:
        wf, rs_store, store, run_id = self._stopped_run(tmp_path)
        # No stop file ever created -- confirm resume runs clean to completion.
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        orch2 = Orchestrator(FakeExecutor(), store, rs_store)
        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)

        assert state2.status == "succeeded"
        assert state2.tripped_breakers == []

    def test_present_stop_file_trips_on_resume(self, tmp_path: Path) -> None:
        wf, rs_store, store, run_id = self._stopped_run(tmp_path)
        (tmp_path / "control").mkdir(parents=True, exist_ok=True)
        (tmp_path / "control" / "stop").write_text("halt")

        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        orch2 = Orchestrator(FakeExecutor(), store, rs_store)
        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)

        assert state2.status == "failed"
        assert len(state2.tripped_breakers) == 1
        assert state2.tripped_breakers[0].id == "halt"
        assert state2.tripped_breakers[0].condition == "stop_file"
        # "c" never dispatched -- the breaker halted before its dispatch.
        assert state2.tasks["c"].status == "pending"


# ---------------------------------------------------------------------------
# AC4: injected_task_count re-trips if still over cap; failure-count breakers
# start clean on resume.
# ---------------------------------------------------------------------------


class TestInjectedTaskCountBreakerResume:
    def test_still_over_cap_after_resume_retrips_immediately(self, tmp_path: Path) -> None:
        """Models a crash between task injection and the next boundary's breaker
        evaluation (engine.py never gets a chance to record the trip before the
        process stops) -- exactly the gap a fresh `evaluate_breakers` call on
        resume must still catch."""
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="cap", condition="injected_task_count", action="stop", threshold=1
                ),
            ],
        )
        store, rs_store = _make_workspace(tmp_path)

        injected = [
            _task("injected-1", outputs=["out/i1.txt"]),
            _task("injected-2", outputs=["out/i2.txt"]),
        ]
        now = _clock_at()()
        state = RunState(
            run_id="wf-20260101T000000Z",
            workflow_id="wf",
            repo_set="rs",
            started_at=now.isoformat(),
            updated_at=now.isoformat(),
            tasks={"a": TaskRunState(status="succeeded"), "b": TaskRunState()},
            injected_tasks=injected,
        )
        (tmp_path / "out").mkdir(parents=True, exist_ok=True)
        (tmp_path / "out" / "a.txt").write_text("done")
        rs_store.save(state)

        existing = rs_store.load(state.run_id)
        existing = rs_store.prepare_resume(existing, wf)
        assert existing.tripped_breakers == []  # never recorded before resume

        orch = Orchestrator(FakeExecutor(), store, rs_store)
        state2 = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)

        assert state2.status == "failed"
        assert len(state2.tripped_breakers) == 1
        assert state2.tripped_breakers[0].id == "cap"
        assert state2.tripped_breakers[0].condition == "injected_task_count"


class TestFailureCountBreakersStartCleanOnResume:
    def test_task_failures_breaker_starts_clean_after_resume(self, tmp_path: Path) -> None:
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="fails", condition="task_failures", action="stop", threshold=1
                ),
            ],
        )
        store, rs_store = _make_workspace(tmp_path)
        orch1 = Orchestrator(FakeExecutor(behaviors={"a": "fail"}), store, rs_store)
        state1 = orch1.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state1.status == "failed"
        assert len(state1.tripped_breakers) == 1  # task_failures tripped at a's boundary
        assert state1.tasks["a"].status == "failed"

        existing = rs_store.load(state1.run_id)
        existing = rs_store.prepare_resume(existing, wf)

        # prepare_resume already reset the failed task to pending -- the failure
        # count genuinely starts at 0 (LLD §9), no new code needed here.
        assert existing.tasks["a"].status == "pending"
        failures = sum(1 for ts in existing.tasks.values() if ts.status in ("failed", "timed_out"))
        assert failures == 0

        # Resume completes cleanly -- no new failure occurs, so task_failures never
        # gets a reason to trip again; the audit trail keeps exactly run1's record.
        orch2 = Orchestrator(FakeExecutor(), store, rs_store)
        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)
        assert state2.status == "succeeded"
        assert len(state2.tripped_breakers) == 1


# ---------------------------------------------------------------------------
# AC5 / NFR-5: old state.json without route_decisions/tripped_breakers resumes fine.
# ---------------------------------------------------------------------------


class TestOldStateJsonFixtureResumes:
    def test_pre_routing_breakers_fixture_resumes_via_prepare_resume(self, tmp_path: Path) -> None:
        fixture_path = _FIXTURES_DIR / "state_pre_routing_breakers.json"
        raw = fixture_path.read_text()
        assert "route_decisions" not in raw
        assert "tripped_breakers" not in raw

        state = RunState.model_validate_json(raw)
        assert state.route_decisions == {}
        assert state.tripped_breakers == []

        wf = WorkflowSpec(
            version="1.0",
            id="test-wf",
            repo_set="rs",
            tasks=[
                _task("a", outputs=["output/a"]),
                _task("b", depends_on=["a"], outputs=["output/b"]),
            ],
        )
        store, rs_store = _make_workspace(tmp_path)
        # Both declared outputs must exist for prepare_resume to keep them succeeded.
        (tmp_path / "output").mkdir(parents=True, exist_ok=True)
        (tmp_path / "output" / "a").write_text("a")
        (tmp_path / "output" / "b").write_text("b")

        resumed = rs_store.prepare_resume(state, wf)

        assert resumed.status == "running"
        assert resumed.tasks["a"].status == "succeeded"
        assert resumed.tasks["b"].status == "succeeded"
        assert resumed.route_decisions == {}
        assert resumed.tripped_breakers == []

        # Full resume-and-run also completes cleanly (both tasks already done).
        orch = Orchestrator(FakeExecutor(), store, rs_store)
        final = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=resumed)
        assert final.status == "succeeded"


# ---------------------------------------------------------------------------
# AC6: CliRunner E2E companion (memory `engine-api-tests-dont-cover-cli`).
# ---------------------------------------------------------------------------


class TestE2EResumeReplayRouting:
    """Proves the not_taken persistence + route replay through the real `ao run` /
    `ao resume` CLI entry points, not just the engine API."""

    def test_resume_via_cli_keeps_not_taken_and_replays_route(self, tmp_path: Path) -> None:
        wf_path = tmp_path / "routing-wf.json"
        wf_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "id": "routing-resume-wf",
                    "repo_set": "default-set",
                    "tasks": [
                        {
                            "id": "classify",
                            "agent": "ag",
                            "instruction": "specs/examples/instructions/design.md",
                            "outputs": ["out/classify-done.txt"],
                        },
                        {
                            "id": "bug-fix",
                            "agent": "ag",
                            "instruction": "specs/examples/instructions/design.md",
                            "depends_on": ["classify"],
                            "inputs": ["out/bug-input.txt"],
                            "outputs": ["out/bug.txt"],
                        },
                        {
                            "id": "doc-fix",
                            "agent": "ag",
                            "instruction": "specs/examples/instructions/design.md",
                            "depends_on": ["classify"],
                            "outputs": ["out/doc.txt"],
                        },
                    ],
                    "branches": [
                        {
                            "id": "classify-router",
                            "router_task_id": "classify",
                            "verdict_path": "out/verdict.json",
                            "routes": {
                                "bug": {"entry": ["bug-fix"]},
                                "documentation": {"entry": ["doc-fix"]},
                            },
                        }
                    ],
                }
            )
        )
        rs_path = tmp_path / "reposets.json"
        rs_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "repo_sets": {
                        "default-set": {
                            "workspace_root": str(tmp_path),
                            "repos": [{"id": "core", "path": ".", "role": "primary"}],
                        }
                    },
                }
            )
        )
        ag_path = tmp_path / "agents.json"
        ag_path.write_text(json.dumps({"version": "1.0", "agents": {"ag": {"executor": "fake"}}}))

        out_dir = tmp_path / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "verdict.json").write_text(json.dumps({"routes": ["bug"]}))

        # First run: bug-fix fails (missing required input) -- deterministic stop
        # without needing FakeExecutor behavior injection.
        result1 = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf_path),
                "--reposets",
                str(rs_path),
                "--agents",
                str(ag_path),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result1.exit_code != 0, f"expected run1 to fail:\n{result1.output}"
        run_id_match = re.search(r"Run:\s+(\S+)", result1.output)
        assert run_id_match, f"could not extract run_id:\n{result1.output}"
        run_id = run_id_match.group(1)

        state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
        state1 = json.loads(state_path.read_text())
        assert state1["tasks"]["classify"]["status"] == "succeeded"
        assert state1["tasks"]["doc-fix"]["status"] == "not_taken"
        assert state1["tasks"]["bug-fix"]["status"] == "failed"
        assert state1["route_decisions"] == {"classify-router": ["bug"]}
        classify_attempts_before = state1["tasks"]["classify"]["attempts"]

        # Provide the missing input, then resume via the real CLI entry point.
        (out_dir / "bug-input.txt").write_text("now present")

        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(wf_path),
                "--reposets",
                str(rs_path),
                "--agents",
                str(ag_path),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result2.exit_code == 0, f"CLI resume failed:\n{result2.output}"
        assert "not_taken" in result2.output
        assert "doc-fix" in result2.output

        state2 = json.loads(state_path.read_text())
        assert state2["tasks"]["classify"]["status"] == "succeeded"
        assert state2["tasks"]["classify"]["attempts"] == classify_attempts_before
        assert state2["tasks"]["doc-fix"]["status"] == "not_taken"
        assert state2["tasks"]["bug-fix"]["status"] == "succeeded"
        assert state2["route_decisions"] == {"classify-router": ["bug"]}
        assert not (out_dir / "doc.txt").exists()
