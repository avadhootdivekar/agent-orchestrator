"""Tests for E-Wk9Tz3 T-Ac6Vd9 -- requeue accounting (R-1a/R-1b/R-21).

ADR-0013 D9 says T2 (resolver)/T3 (rerun) are just extra ATTEMPTS of the same task, so
the existing budget/cost machinery bounds them "for free". The design gate found two
defects that had to be fixed for that claim to actually hold, both instances of bug
classes this codebase already found and fixed once elsewhere (E-9h3m7k):

- R-1a: a T2/T3 requeue is a brand-new call to ``_run_with_retries`` (its own ``cum_*``
  accumulators reset to zero every call). Unless settle accumulates a finished cycle's
  actuals into ``ts.cumulative_*`` BEFORE returning "requeue", that cycle's cost/tokens
  vanish. T-En8Hd4 already placed the (frozen, self-heal-shared) accumulation call
  before the conflict switch -- this file's job is to PROVE it via a live 3-cycle
  ladder, not to add a second implementation.
- R-1b: ``DefaultBudgetManager.reconcile()`` used to latch one-shot per bare ``task_id``,
  so a T2/T3 redispatch's reconcile was silently a no-op and its real spend never
  reached ``consumed_tokens``/the rate window -- exactly the "conflict spend is bounded
  for free" gap the ladder assumes away. Fixed in ``budget.py`` by keying
  ``charged_estimate``/``reconciled_cycles`` by ``"<task_id>#<dispatch_cycle>"``.
- R-21: capture directories are now cycle-keyed for cycle >= 2
  (``<run_dir>/<task_id>/cycle-<n>/attempt-<m>/``) so a requeue never overwrites an
  earlier cycle's already-captured transcript. Cycle 1 (the common, never-requeued
  case) keeps the legacy flat ``<run_dir>/<task_id>/attempt-<m>/`` layout, confirmed
  byte-identical against ``tests/playground/test_sum_of_array_deterministic.py``'s own
  pre-existing, hardcoded assertions on that exact path shape.

Drives the conflict ladder via an injected ``_ScriptedIntegrator`` test-double exactly
like ``tests/test_engine_isolation.py::TestConflictSwitch`` (T-Rm2Lx7/T-Lr6Ka3's real
resolver/escalation hooks have not landed) and a local ``_UsageExecutor`` that reports
FIXED, per-call token/cost usage so cross-cycle accumulation is exactly verifiable.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager, cycle_key
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.isolation.integrator import IntegrationResult
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    CircuitBreakerSpec,
    EstimatorConfig,
    IntegrationSpec,
    RateLimit,
    RepoRef,
    RepoSet,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Small local git/workspace fixture helpers -- the SAME "small local copy" pattern
# tests/test_engine_isolation.py's own module docstring documents and authorizes
# (that file's own conftest-mirroring fixture only applies within its own module).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.name", "ao-test"], repo)
    _git(["config", "user.email", "ao-test@example.invalid"], repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    return repo


def _workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
    return store, rs_store


def _agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}  # type: ignore[arg-type]


def _reposet(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path="repo", role="primary")],
        )
    }


def _task(tid: str, outputs: list[str] | None = None, isolation: str = "inherit") -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/instructions/design.md",
        outputs=outputs or [],
        isolation=isolation,  # type: ignore[arg-type]
    )


def _workflow(
    tasks: list[TaskSpec],
    isolation_default: str = "worktree",
    circuit_breakers: list[CircuitBreakerSpec] | None = None,
    budget: BudgetSpec | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        defaults=WorkflowDefaults(isolation=isolation_default),  # type: ignore[arg-type]
        tasks=tasks,
        circuit_breakers=circuit_breakers or [],
        # sync_checkout=never: these tests only assert on RunState/budget/capture-dir
        # accounting, never on the shared checkout's contents -- keeps them fast and
        # avoids an unrelated dirty-tree failure mode.
        integration=IntegrationSpec(sync_checkout="never"),
        budget=budget,
    )


def _instructions(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "specs" / "instructions" / "design.md").write_text("# Do it\n")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedIntegrator:
    """Local copy of tests/test_engine_isolation.py's own `_ScriptedIntegrator` (that
    file's is module-private and this file must not import from another test module) --
    returns a scripted queue of `IntegrationResult`s, one per `integrate()` call, driving
    `_settle_completed_task`'s conflict switch deterministically without a real git
    conflict or the not-yet-landed T-Rm2Lx7/T-Lr6Ka3 resolver/escalation hooks.
    """

    def __init__(self, results: list[IntegrationResult]) -> None:
        self._results = list(results)
        self.calls = 0

    def integrate(self, task_iso, run_integration, task_integration, attempt, *, agent_id):  # noqa: ANN001, ARG002
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return IntegrationResult(status="failed", reason="scripted_exhausted")

    def resume_integration(self, task_iso, run_integration, task_integration, attempt):  # noqa: ANN001, ARG002
        return self.integrate(task_iso, run_integration, task_integration, attempt, agent_id="x")


# One scripted usage entry per dispatch call: (input_tokens, output_tokens, cost_usd,
# status, error). The last entry repeats once its task's queue is exhausted.
_UsageEntry = tuple[int, int, float, str, str | None]


class _UsageExecutor(Executor):
    """Reports FIXED, per-call token/cost usage -- lets a test assert cross-cycle
    accumulation exactly, independent of `FakeExecutor` (which this ticket's file scope
    does not include -- `executors/fake.py` is not one of T-Ac6Vd9's files).

    Also records the REAL `ctx.output_dir` per call (``self.output_dirs``) and writes
    distinguishable capture files into it, so a test can assert R-21's capture
    directories are cycle-keyed AND hold distinct content per cycle (AC-8), not merely
    that they exist.
    """

    def __init__(self, usage_by_task: dict[str, list[_UsageEntry]]) -> None:
        self._usage = {k: list(v) for k, v in usage_by_task.items()}
        self.calls: dict[str, int] = {}
        self.output_dirs: dict[str, list[str]] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        n = self.calls.get(ctx.task_id, 0)
        self.calls[ctx.task_id] = n + 1
        self.output_dirs.setdefault(ctx.task_id, []).append(ctx.output_dir)

        queue = self._usage.get(ctx.task_id, [(0, 0, 0.0, "succeeded", None)])
        input_tokens, output_tokens, cost_usd, status, error = queue[min(n, len(queue) - 1)]

        if ctx.output_dir:
            out = Path(ctx.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "transcript.jsonl").write_text(
                json.dumps({"type": "result", "call": n, "task_id": ctx.task_id}) + "\n"
            )
            (out / "stdout.txt").write_text(f"call {n} for {ctx.task_id}\n")

        if status == "succeeded":
            for path in ctx.output_paths:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w") as f:
                    f.write(f"output for {ctx.task_id} call {n}\n")

        return TaskResult(
            task_id=ctx.task_id,
            status=status,  # type: ignore[arg-type]
            attempts=1,
            exit_code=0 if status == "succeeded" else 1,
            error=error,
            output_artifact_path=ctx.output_dir or None,
            actuals_available=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )


# ---------------------------------------------------------------------------
# R-1a: cross-cycle cumulative accumulation, proven via a live conflict-ladder run.
# ---------------------------------------------------------------------------


class TestCumulativeAccumulationAcrossTheLadder:
    def test_conflict_resolver_then_integrated_accumulates_two_cycles(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        usage = _UsageExecutor(
            {"a": [(100, 50, 1.0, "succeeded", None), (80, 40, 0.8, "succeeded", None)]}
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(usage, store, rs_store, integrator=scripted)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "succeeded"
        ts = state.tasks["a"]
        assert ts.dispatch_cycle == 2
        assert ts.cumulative_input_tokens == 180  # 100 + 80
        assert ts.cumulative_output_tokens == 90  # 50 + 40
        assert ts.cumulative_cost_usd == pytest.approx(1.8)
        assert usage.calls["a"] == 2

    def test_conflict_resolver_then_rerun_then_integrated_accumulates_three_cycles(
        self, tmp_path: Path
    ) -> None:
        """T0 conflicts -> T2 resolver cycle also conflicts (rerun) -> T3 rerun cycle
        lands. All three cycles' actuals must be in the final cumulative total."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        usage = _UsageExecutor(
            {
                "a": [
                    (100, 50, 1.0, "succeeded", None),
                    (60, 30, 0.6, "succeeded", None),
                    (40, 20, 0.4, "succeeded", None),
                ]
            }
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="conflict_rerun", reason="verify_failed"),
                IntegrationResult(status="integrated"),
            ]
        )
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(usage, store, rs_store, integrator=scripted)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "succeeded"
        ts = state.tasks["a"]
        assert ts.dispatch_cycle == 3
        assert ts.cumulative_input_tokens == 200  # 100 + 60 + 40
        assert ts.cumulative_output_tokens == 100  # 50 + 30 + 20
        assert ts.cumulative_cost_usd == pytest.approx(2.0)
        assert usage.calls["a"] == 3
        ti = state.task_integration["a"]
        assert ti.status == "integrated"
        assert ti.resolver_attempts == 1
        assert ti.reruns == 1

    def test_self_heal_requeue_path_accounted(self, tmp_path: Path) -> None:
        """R-1a also covers self-heal's cross-call redispatch (the ORIGINAL
        reviewer-caught Critical this ticket's helper now shares one implementation
        with) -- a task that fails transiently, self-heals, then succeeds must report
        cumulative usage across BOTH cycles, not just the winning one.

        AC-9 investigation (R-21): also proves/records the self-heal transcript-clobber
        question. Confirmed: BEFORE this ticket, self-heal's requeue re-dispatched
        through the SAME `_run_with_retries` call as any other redispatch, whose
        `output_dir` was keyed by task_id ALONE (no cycle component) -- so a self-heal
        retry's `attempt-1/` transcript WOULD have overwritten the original failed
        cycle's `attempt-1/` transcript (the exact same mechanism R-21 fixes for the
        conflict ladder). This ticket's fix is NOT self-heal-specific: `cycle` is
        threaded through `_run_and_integrate`/`_run_with_retries` for EVERY dispatch
        (`DispatchPrep.cycle` is populated unconditionally in
        `_prepare_and_maybe_dispatch`, regardless of why the previous cycle requeued),
        so self-heal's capture directories are ALSO cycle-keyed now, as a natural
        consequence of the general fix rather than a second, self-heal-specific patch.
        Verified below by asserting the two cycles' capture directories are distinct
        and hold different content -- NOT assumed.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow(
            [_task("h", outputs=["out/h.txt"], isolation="none")], isolation_default="none"
        )
        usage = _UsageExecutor(
            {
                "h": [
                    (50, 20, 0.5, "failed", "connection reset by peer"),
                    (30, 10, 0.3, "succeeded", None),
                ]
            }
        )
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(usage, store, rs_store, self_heal_enabled=True, sleeper=lambda _s: None)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "succeeded"
        ts = state.tasks["h"]
        assert ts.status == "succeeded"
        assert ts.cumulative_input_tokens == 80  # 50 + 30
        assert ts.cumulative_output_tokens == 30  # 20 + 10
        assert ts.cumulative_cost_usd == pytest.approx(0.8)
        assert usage.calls["h"] == 2
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "retry"
        assert ts.dispatch_cycle == 2

        # AC-9: distinct, cycle-keyed capture directories for the failed cycle and the
        # self-healed retry -- neither transcript overwrote the other. Cycle 1 keeps the
        # LEGACY FLAT layout (no "cycle-1" segment, NFR-2/AC-7); cycle 2+ nests under
        # "cycle-<n>/" so it can never collide with cycle 1's own "attempt-<n>/".
        dirs = usage.output_dirs["h"]
        assert len(dirs) == 2
        cycle1_dir, cycle2_dir = dirs
        assert cycle1_dir != cycle2_dir
        assert "cycle-" not in cycle1_dir
        assert "cycle-2" in cycle2_dir
        stdout1 = Path(cycle1_dir, "stdout.txt").read_text()
        stdout2 = Path(cycle2_dir, "stdout.txt").read_text()
        assert stdout1 != stdout2
        assert os.path.isdir(cycle1_dir)  # the failed cycle's transcript still exists
        assert os.path.isdir(cycle2_dir)


# ---------------------------------------------------------------------------
# R-21: capture directories are cycle-keyed and hold distinct content per cycle.
# ---------------------------------------------------------------------------


class TestCaptureDirectoriesAreCycleKeyed:
    def test_two_cycles_get_distinct_capture_directories(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        usage = _UsageExecutor(
            {"a": [(10, 5, 0.1, "succeeded", None), (10, 5, 0.1, "succeeded", None)]}
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(usage, store, rs_store, integrator=scripted)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "succeeded"
        dirs = usage.output_dirs["a"]
        assert len(dirs) == 2
        cycle1_dir, cycle2_dir = dirs
        assert cycle1_dir != cycle2_dir
        # Cycle 1 keeps the legacy flat layout (no "cycle-1" segment -- byte-identical
        # to a pre-this-ticket run, NFR-2/AC-7); cycle 2+ nests under "cycle-<n>/".
        assert "cycle-" not in cycle1_dir
        assert "cycle-2" in cycle2_dir
        assert os.path.isdir(cycle1_dir)
        assert os.path.isdir(cycle2_dir)

        # Neither cycle's transcript overwrote the other's -- distinct file CONTENT,
        # not just distinct existence (AC-8).
        stdout1 = Path(cycle1_dir, "stdout.txt").read_text()
        stdout2 = Path(cycle2_dir, "stdout.txt").read_text()
        assert stdout1 != stdout2
        assert "call 0" in stdout1
        assert "call 1" in stdout2

        transcript1 = json.loads(Path(cycle1_dir, "transcript.jsonl").read_text().splitlines()[0])
        transcript2 = json.loads(Path(cycle2_dir, "transcript.jsonl").read_text().splitlines()[0])
        assert transcript1["call"] == 0
        assert transcript2["call"] == 1


# ---------------------------------------------------------------------------
# task_cost_usd breaker sees the CUMULATIVE sum, including resolver/rerun cycles.
# ---------------------------------------------------------------------------


class TestCostBreakerSeesCumulativeSpend:
    def test_task_cost_usd_breaker_trips_only_on_the_cumulative_sum(self, tmp_path: Path) -> None:
        """The LAST cycle's cost alone (2.0) is below the threshold (5.0) -- only the
        cumulative sum across all three cycles (6.0) trips it. A test that only checked
        `result.cost_usd` on the final attempt would pass even if R-1a/R-1b were both
        still broken; this is exactly the differential the ticket's Risks section warns
        against ("a cost test that passes on cumulative fields while the ledger is
        wrong is exactly how this defect survives review" -- covered together with the
        BudgetCounters-level test below, per AC-4)."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"])],
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="cost-cap", condition="task_cost_usd", action="fail", threshold=5.0
                )
            ],
        )
        usage = _UsageExecutor(
            {
                "a": [
                    (10, 10, 2.0, "succeeded", None),
                    (10, 10, 2.0, "succeeded", None),
                    (10, 10, 2.0, "succeeded", None),
                ]
            }
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="conflict_rerun", reason="verify_failed"),
                IntegrationResult(status="integrated"),
            ]
        )
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(usage, store, rs_store, integrator=scripted)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "failed"
        assert any(tb.id == "cost-cap" for tb in state.tripped_breakers)
        assert state.tasks["a"].cumulative_cost_usd == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# R-1b: the BUDGET LEDGER (not just cumulative_*) is keyed by cycle and independently
# gateable/chargeable/reconcilable per redispatch.
# ---------------------------------------------------------------------------


class TestBudgetLedgerKeyedByCycle:
    def test_redispatched_task_is_gated_and_charged_again(self, tmp_path: Path) -> None:
        """AC-4: asserted against BudgetCounters directly (consumed_tokens,
        charged_estimate, reconciled_cycles) -- NOT only against
        TaskRunState.cumulative_*, per the ticket's own warning that a cost test
        passing on cumulative fields alone is exactly how the R-1b defect survives
        review."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=0)
        budget_spec = BudgetSpec(total_tokens=100_000, estimator=cfg)
        wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget_spec)
        usage = _UsageExecutor(
            {"a": [(100, 0, 1.0, "succeeded", None), (200, 0, 2.0, "succeeded", None)]}
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        store, rs_store = _workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, lambda: _FIXED_DT)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            usage, store, rs_store, integrator=scripted, budget_manager=mgr, estimator=estimator
        )
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "succeeded"
        bc = state.budget_counters
        # Each cycle independently reconciled -- both keys present, exactly once each.
        assert bc.reconciled_cycles.count(cycle_key("a", 1)) == 1
        assert bc.reconciled_cycles.count(cycle_key("a", 2)) == 1
        # Nothing left outstanding once both cycles have settled.
        assert bc.charged_estimate == {}
        # consumed_tokens reflects BOTH cycles' real actuals (100 + 200), not just the
        # last one and not a stale double-count from the first.
        assert bc.consumed_tokens == 300
        # Backward-compat dual-write: reconciled_tasks records "a" once (not per cycle).
        assert bc.reconciled_tasks.count("a") == 1

    def test_budget_charge_and_reconcile_events_carry_the_correct_cycle(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """W-2 (review): the `cycle` field this ticket added to the `budget.charge`/
        `budget.reconcile` log events is asserted on the ACTUAL captured log record, not
        just read-and-trusted from the source -- a future refactor that drops the kwarg
        would fail this test."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=0)
        budget_spec = BudgetSpec(total_tokens=100_000, estimator=cfg)
        wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget_spec)
        usage = _UsageExecutor(
            {"a": [(100, 0, 1.0, "succeeded", None), (200, 0, 2.0, "succeeded", None)]}
        )
        scripted = _ScriptedIntegrator(
            [
                IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"]),
                IntegrationResult(status="integrated"),
            ]
        )
        store, rs_store = _workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, lambda: _FIXED_DT)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            usage, store, rs_store, integrator=scripted, budget_manager=mgr, estimator=estimator
        )
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            state = orch.run(wf, _reposet(str(tmp_path)), _agents())
        assert state.status == "succeeded"

        charge_cycles = [
            r.cycle
            for r in caplog.records
            if getattr(r, "event", None) == "budget.charge" and getattr(r, "task_id", None) == "a"
        ]
        reconcile_cycles = [
            r.cycle
            for r in caplog.records
            if getattr(r, "event", None) == "budget.reconcile"
            and getattr(r, "task_id", None) == "a"
        ]
        assert charge_cycles == [1, 2]
        assert reconcile_cycles == [1, 2]

    def test_rate_window_gate_sees_the_first_cycles_reconciled_actual(self, tmp_path: Path) -> None:
        """AC-5: a rate window just large enough for cycle 1's real actual usage
        BLOCKS cycle 2's redispatch -- proving the spend is visible to gate() (i.e.
        reconciled into window_consumed_tokens BEFORE the next cycle's charge), not
        merely recorded after the fact. Deterministic (on_exhaustion=stop, no sleep)."""
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        # Tiny instruction file -> a near-zero ESTIMATE (output_allowance_tokens=0), so
        # admission at charge time is governed almost entirely by window_consumed_tokens
        # from the PRIOR cycle's reconciled ACTUAL, not by this cycle's own estimate.
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=0)
        budget_spec = BudgetSpec(
            rate=RateLimit(tokens=100, window_seconds=3600),
            on_exhaustion="stop",
            estimator=cfg,
        )
        wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget_spec)
        # Cycle 1's actual (99) is far above its own tiny estimate but still under the
        # rate cap alone; cycle 2's own (small) estimate pushes the window over 100.
        usage = _UsageExecutor({"a": [(99, 0, 0.99, "succeeded", None)]})
        scripted = _ScriptedIntegrator(
            [IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"])]
        )
        store, rs_store = _workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, lambda: _FIXED_DT)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            usage, store, rs_store, integrator=scripted, budget_manager=mgr, estimator=estimator
        )
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        # Cycle 2 never actually dispatched -- the gate blocked it before the executor
        # ran (on_exhaustion=stop halts the run rather than waiting).
        assert usage.calls["a"] == 1
        assert state.status == "failed"
        assert any(tb.condition == "rate_window" for tb in state.tripped_breakers), [
            tb.condition for tb in state.tripped_breakers
        ]
        # Cycle 1's real actual (99) DID reach window_consumed_tokens -- that's what
        # made cycle 2's gate check fail.
        assert state.budget_counters.window_consumed_tokens == 99


# ---------------------------------------------------------------------------
# AC-6 / Risks: idempotency preserved across a crash-and-resume mid-cycle-2 -- both
# TaskRunState.cumulative_* AND BudgetCounters must reconcile each cycle exactly once.
# ---------------------------------------------------------------------------


class TestResumeMidCycleTwo:
    def test_resume_after_crash_mid_cycle_two_reconciles_each_cycle_exactly_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Phase 1 (live, real git): cycle 1 dispatches, conflicts (conflict_resolver),
        settles (reconciled, cumulative_* accumulated) -- the run then stops via
        cancellation BEFORE cycle 2 is ever prepared, leaving a real, on-disk, resumable
        state exactly like T-En8Hd4's own resume tests use.

        Phase 2 (hand-simulated crash + resume): the persisted state is mutated to look
        like "cycle 2 was charged and dispatched, then the process crashed before
        settle" -- the SAME direct-construction technique
        tests/test_engine_budget.py::TestResumeDoubleChargeGuard uses for the identical
        scenario, since a genuine process crash cannot be induced from inside a live,
        single-process test. `prepare_resume` is the real resume entry point (mirrors
        the CLI's own resume path): it carries `dispatch_cycle` AND the cumulative
        actual-usage counters forward verbatim (review Major-1 fix) while resetting
        status to "pending", and leaves `state.integration`/`state.task_integration`
        untouched (HLD's own documented reason those live on `RunState`, not
        `TaskRunState`).

        Then a SECOND live run resumes: the stale cycle-2 charge must be reversed (not
        double-counted), a fresh cycle-3 dispatch charges/reconciles independently, and
        the task lands. Both `RunState.budget_counters` (never rebuilt by
        `prepare_resume`) AND `TaskRunState.cumulative_*` (now also carried forward for
        a requeued task, review Major-1) must reflect exactly cycle 1 + cycle 3 -- cycle
        2 never actually ran, so it contributes nothing, and neither must be
        double-charged/double-reconciled either.
        """
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        _git_repo(tmp_path)
        wf = _workflow([_task("a", outputs=["out/a.txt"])])
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=0)
        budget_spec = BudgetSpec(total_tokens=100_000, estimator=cfg)
        wf.budget = budget_spec

        # ---- Phase 1: live run, stops right after cycle 1's conflict_resolver settle ----
        usage1 = _UsageExecutor({"a": [(100, 0, 1.0, "succeeded", None)]})
        scripted1 = _ScriptedIntegrator(
            [IntegrationResult(status="conflict_resolver", conflicted_paths=["f.txt"])]
        )
        store, rs_store = _workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, lambda: _FIXED_DT)
        estimator = HeuristicTokenEstimator(store)

        def _cancel_after_cycle_one() -> bool:
            # Only True once the executor has actually run cycle 1 to completion (its
            # own call count is bumped INSIDE execute(), so the cancel check inside
            # _run_with_retries' attempt loop -- BEFORE execute() -- still sees 0 and
            # lets cycle 1 dispatch). The run loop's own top-of-wave check (AFTER
            # cycle 1 settles, BEFORE cycle 2 is ever prepared/charged) is what
            # actually stops the run here.
            return usage1.calls.get("a", 0) >= 1

        orch1 = Orchestrator(
            usage1,
            store,
            rs_store,
            integrator=scripted1,
            budget_manager=mgr,
            estimator=estimator,
            cancel_fn=_cancel_after_cycle_one,
        )
        state = orch1.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "cancelled"
        assert usage1.calls["a"] == 1  # cycle 2 never dispatched
        assert state.tasks["a"].dispatch_cycle == 1
        assert state.tasks["a"].cumulative_input_tokens == 100
        assert cycle_key("a", 1) in state.budget_counters.reconciled_cycles
        assert state.budget_counters.consumed_tokens == 100
        assert state.budget_counters.charged_estimate == {}

        # ---- Phase 2: hand-simulate "cycle 2 was charged, then the process crashed
        # before settle" on top of the real, already-activated state from phase 1. ----
        state.tasks["a"].status = "running"
        state.tasks["a"].dispatch_cycle = 2
        state.budget_counters.charged_estimate[cycle_key("a", 2)] = 25
        state.budget_counters.consumed_tokens += 25
        state.budget_counters.window_consumed_tokens += 25
        rs_store.prepare_resume(state, wf)
        assert state.tasks["a"].status == "pending"
        assert state.tasks["a"].dispatch_cycle == 2  # carried forward verbatim (R-21)
        # Review Major-1 (fixed in runstate.py::prepare_resume, this ticket): the
        # cumulative ACTUAL usage counters survive resume for a requeued (non-terminal)
        # task, the same way dispatch_cycle already did -- cycle 1's real actuals
        # (accumulated before the simulated crash) are NOT wiped here.
        assert state.tasks["a"].cumulative_input_tokens == 100
        assert state.tasks["a"].cumulative_cost_usd == pytest.approx(1.0)
        rs_store.save(state)

        usage2 = _UsageExecutor({"a": [(50, 0, 0.5, "succeeded", None)]})
        scripted2 = _ScriptedIntegrator([IntegrationResult(status="integrated")])
        orch2 = Orchestrator(
            usage2, store, rs_store, integrator=scripted2, budget_manager=mgr, estimator=estimator
        )
        with caplog.at_level(logging.INFO, logger="agent_orchestrator"):
            resumed = orch2.run(wf, _reposet(str(tmp_path)), _agents(), run_state=state)

        assert resumed.status == "succeeded"
        ts = resumed.tasks["a"]
        assert ts.dispatch_cycle == 3  # the stale cycle-2 charge was reversed, never run
        assert usage2.calls["a"] == 1

        # W-2 (review): the `budget.resume_reverse` event's `cycle` field names the
        # STALE cycle that was reversed (2), not the new cycle (3) it's about to charge.
        resume_reverse_cycles = [
            r.cycle
            for r in caplog.records
            if getattr(r, "event", None) == "budget.resume_reverse"
            and getattr(r, "task_id", None) == "a"
        ]
        assert resume_reverse_cycles == [2]
        # cumulative_* now reflects cycle 1 (100, preserved across the crash+resume) +
        # cycle 3 (50, this resumed run's own attempts) -- cycle 2 never executed,
        # contributes nothing. Still increasing after the next cycle settles, per the
        # review's own regression-test requirement.
        assert ts.cumulative_input_tokens == 150
        assert ts.cumulative_cost_usd == pytest.approx(1.5)

        bc = resumed.budget_counters
        assert cycle_key("a", 1) in bc.reconciled_cycles
        assert cycle_key("a", 3) in bc.reconciled_cycles
        assert cycle_key("a", 2) not in bc.reconciled_cycles  # never actually reconciled
        assert cycle_key("a", 2) not in bc.charged_estimate  # reversed as stale, not left
        assert bc.charged_estimate == {}
        # THE central R-1b assertion: BudgetCounters -- unlike TaskRunState, never
        # rebuilt by prepare_resume -- carries the full, durable cross-cycle total.
        # Net consumed_tokens = cycle 1 (100) + cycle 3 (50) only -- the stale cycle-2
        # charge (25) was reversed before it could inflate the total (no double-charge).
        assert bc.consumed_tokens == 150


# ---------------------------------------------------------------------------
# Golden compare: a non-isolated, single-cycle run's status.json cost block is exactly
# what a pre-this-ticket run would have produced (byte-identical superset, per the
# ticket's own Risks section).
# ---------------------------------------------------------------------------


class TestNonIsolatedGoldenCostBlock:
    def test_status_json_cost_block_for_a_plain_single_cycle_task(self, tmp_path: Path) -> None:
        (tmp_path / "out").mkdir()
        _instructions(tmp_path)
        wf = _workflow(
            [_task("a", outputs=["out/a.txt"], isolation="none")], isolation_default="none"
        )
        usage = _UsageExecutor({"a": [(42, 17, 0.25, "succeeded", None)]})
        store, rs_store = _workspace(tmp_path)
        orch = Orchestrator(usage, store, rs_store)
        state = orch.run(wf, _reposet(str(tmp_path)), _agents())

        assert state.status == "succeeded"
        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snapshot = json.loads(status_path.read_text())
        task_entry = next(t for t in snapshot["tasks"] if t["id"] == "a")

        # The exact golden shape: a never-requeued task's cost block is untouched by
        # this ticket -- dispatch_cycle is 1, cumulative_* equals the single call's
        # actuals, no cycle-2/-3 artifact of any kind.
        assert task_entry["dispatch_cycle"] == 1
        assert task_entry["input_tokens"] == 42
        assert task_entry["output_tokens"] == 17
        assert task_entry["cost_usd"] == pytest.approx(0.25)
        assert task_entry["integration_status"] == "none"
        usage_totals = snapshot["usage_totals"]
        assert usage_totals["input_tokens"] == 42
        assert usage_totals["output_tokens"] == 17
        assert usage_totals["cost_usd"] == pytest.approx(0.25)
