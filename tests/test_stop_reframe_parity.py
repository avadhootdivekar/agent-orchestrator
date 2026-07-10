"""Characterization + re-frame parity tests for T-r3j9b6 (epic E-rc7k2v).

Context (LLD `docs-md/lld-run-control-routing-breakers.md` §8, ADR-RC-003): engine.py has
three (really four -- LLD §8.1 groups two under one builtin id) hard-coded terminal "stop"
sites. T-r3j9b6 re-frames each additively onto the trip->record->act path: at each site,
`record_trip(...)` (breakers.py, T-x8v4d3's `trip_builtin`) now ALSO appends a
`TrippedBreaker` to `state.tripped_breakers` and emits a `breaker.trip` event, while the
pre-existing `run_log.*` call, `state.status = "failed"`, `failed = True`, and `break` stay
byte-identical.

Per the ticket's explicit sequencing:
  1. These tests are committed FIRST capturing CURRENT (pre-refactor) behaviour -- the
     pinned assertions below must pass against today's engine.py, proving they are a real
     characterization pin and not a tautology.
  2. engine.py is then changed additively.
  3. The SAME pinned assertions are re-run (must still pass, byte-identical) and extended
     with the new tripped_breakers/breaker.trip assertions.

Both engine-API (`Orchestrator.run` directly) and CliRunner (`ao run`) levels are exercised
per memory `engine-api-tests-dont-cover-cli` -- a defect can hide in the CLI wiring even when
the engine API test is green.

Site map (current engine.py line numbers; see TASK.md for the ticket's own verified refs):
  1. Unsatisfiable estimate               -> BUILTIN_BUDGET_UNSATISFIABLE
  2. Budget-exhaustion stop (gate path)    -> BUILTIN_BUDGET_EXHAUSTED
  3. Quota max-wait exceeded               -> BUILTIN_QUOTA_MAX_WAIT
  4. Provider-429 budget-exhaustion stop   -> BUILTIN_BUDGET_EXHAUSTED (shared with site 2)

Transient wait/retry loops (429 wait, quota poll, budget wait) are explicitly NOT trips --
`TestTransientWaitsAreNotTrips` proves no `breaker.trip` event / `tripped_breakers` entry is
ever added on those paths.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_module
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    BUILTIN_BUDGET_EXHAUSTED,
    BUILTIN_BUDGET_UNSATISFIABLE,
    BUILTIN_QUOTA_MAX_WAIT,
    AgentSpec,
    BudgetSpec,
    EstimatorConfig,
    RateLimit,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

runner = CliRunner()

REPO_ROOT = Path(__file__).parent.parent
SPECS_EXAMPLES = REPO_ROOT / "specs" / "examples"

_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _clock_at(offset: float = 0.0) -> Callable[[], datetime]:
    """Return a clock that always returns _BASE_EPOCH + offset (NFR-2 determinism)."""
    target = datetime.fromtimestamp(_BASE_EPOCH + offset, tz=UTC)
    return lambda: target


# ---------------------------------------------------------------------------
# Engine-API helpers (mirrors tests/test_engine_budget.py conventions)
# ---------------------------------------------------------------------------


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
    tid: str, depends_on: list[str] | None = None, outputs: list[str] | None = None
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        depends_on=depends_on or [],
        inputs=[],
        outputs=outputs or [],
    )


def _workflow(tasks: list[TaskSpec], budget: BudgetSpec | None = None) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=tasks,
        defaults=WorkflowDefaults(),
        budget=budget,
    )


def _run_dir(tmp_path: Path, run_id: str) -> Path:
    return tmp_path / ".orchestrator" / "runs" / run_id


def _tripped_ids(run_dir: Path) -> list[str]:
    """Read state.json (not just the log) for the persisted tripped_breakers list."""
    state_json = json.loads((run_dir / "state.json").read_text())
    return [tb["id"] for tb in state_json.get("tripped_breakers", [])]


# ---------------------------------------------------------------------------
# CLI-level helpers (mirrors tests/test_e2e_cli.py conventions)
# ---------------------------------------------------------------------------


def _copy_examples_with_fake_agents(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Copy example workflow + reposet to tmp; write a fake-executor agents.json."""
    wf = tmp_path / "workflow.json"
    rs = tmp_path / "reposets.json"
    ag = tmp_path / "agents.json"

    shutil.copy(SPECS_EXAMPLES / "workflow.json", wf)
    shutil.copy(SPECS_EXAMPLES / "reposet.json", rs)

    orig_agents = json.loads((SPECS_EXAMPLES / "agents.json").read_text())
    fake_agents: dict = {"version": "1.0", "agents": {}}
    for name in orig_agents["agents"]:
        fake_agents["agents"][name] = {"executor": "fake"}
    ag.write_text(json.dumps(fake_agents))

    instr_src = SPECS_EXAMPLES / "instructions"
    instr_dst = tmp_path / "specs" / "examples" / "instructions"
    instr_dst.mkdir(parents=True)
    for f in instr_src.iterdir():
        if f.is_file():
            shutil.copy(f, instr_dst / f.name)

    return wf, rs, ag


def _cli_run_id(output: str) -> str:
    match = re.search(r"Run:\s+(\S+)", output)
    assert match, f"Could not extract run_id from CLI output:\n{output}"
    return match.group(1)


def _cli_events(run_dir: Path) -> list[dict]:
    log_path = run_dir / "run.log"
    records = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Site 1: Unsatisfiable estimate (engine.py ~337-351) -> BUILTIN_BUDGET_UNSATISFIABLE
# ---------------------------------------------------------------------------


class TestUnsatisfiableEstimateParity:
    def test_unsatisfiable_estimate_parity(self, tmp_path: Path, read_jsonl) -> None:
        """estimate > total_tokens => stop immediately, even with on_exhaustion='wait'.

        on_exhaustion is deliberately 'wait' (not 'stop') so this pins the UNSATISFIABLE
        branch specifically, not the on_exhaustion=='stop' gate-path branch (site 2).
        """
        budget_spec = BudgetSpec(
            total_tokens=10,
            on_exhaustion="wait",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=500
            ),
        )
        store, rs_store = _make_workspace(tmp_path)
        clock = _clock_at()
        mgr = DefaultBudgetManager(budget_spec, clock)
        estimator = HeuristicTokenEstimator(store)
        sleep_calls: list[float] = []
        orch = Orchestrator(
            executor=FakeExecutor(),
            artifact_store=store,
            runstate_store=rs_store,
            sleeper=sleep_calls.append,
            budget_manager=mgr,
            estimator=estimator,
            clock=clock,
        )
        wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # ---- PIN: byte-identical before/after the re-frame ----
        assert state.status == "failed"
        assert sleep_calls == []  # unsatisfiable => stop immediately, no wait loop
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert any(e.get("event") == "budget.exhausted" for e in events), events

        # ---- NEW: only true after the re-frame ----
        assert BUILTIN_BUDGET_UNSATISFIABLE in _tripped_ids(run_dir)
        assert any(
            e.get("event") == "breaker.trip" and e.get("breaker_id") == BUILTIN_BUDGET_UNSATISFIABLE
            for e in events
        ), events

    def test_unsatisfiable_estimate_parity_cli(self, tmp_path: Path) -> None:
        """CliRunner-level counterpart (memory: engine-api-tests-dont-cover-cli)."""
        wf, rs, ag = _copy_examples_with_fake_agents(tmp_path)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--budget-total",
                "100",  # below the "design" task's estimate (~1395) => unsatisfiable
                "--on-exhaustion",
                "wait",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        # ---- PIN ----
        assert result.exit_code == 1, result.output
        run_id = _cli_run_id(result.output)
        run_dir = _run_dir(tmp_path, run_id)
        events = _cli_events(run_dir)
        assert any(e.get("event") == "budget.exhausted" for e in events), events

        # ---- NEW ----
        assert BUILTIN_BUDGET_UNSATISFIABLE in _tripped_ids(run_dir)


# ---------------------------------------------------------------------------
# Site 2: Budget-exhaustion stop, gate path (engine.py ~357-369) -> BUILTIN_BUDGET_EXHAUSTED
# ---------------------------------------------------------------------------


class TestBudgetExhaustionStopParity:
    def test_budget_exhaustion_stop_parity(self, tmp_path: Path, read_jsonl) -> None:
        """First task admits (satisfiable); second is blocked and on_exhaustion='stop'
        ends the run -- distinct from the single-task unsatisfiable case above (site 1):
        each individual estimate (~673) stays under total_tokens (1000), so
        `_is_unsatisfiable` is False and this hits the on_exhaustion=='stop' branch."""
        budget_spec = BudgetSpec(
            total_tokens=1000,
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=600
            ),
        )
        store, rs_store = _make_workspace(tmp_path)
        clock = _clock_at()
        mgr = DefaultBudgetManager(budget_spec, clock)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor=FakeExecutor(),
            artifact_store=store,
            runstate_store=rs_store,
            budget_manager=mgr,
            estimator=estimator,
            clock=clock,
        )
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
        ]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # ---- PIN ----
        assert state.status == "failed"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "pending"
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert any(e.get("event") == "budget.exhausted" for e in events), events

        # ---- NEW ----
        assert BUILTIN_BUDGET_EXHAUSTED in _tripped_ids(run_dir)
        assert any(
            e.get("event") == "breaker.trip" and e.get("breaker_id") == BUILTIN_BUDGET_EXHAUSTED
            for e in events
        ), events

    def test_budget_exhaustion_stop_parity_cli(self, tmp_path: Path) -> None:
        """CliRunner-level counterpart: 'design' admits, 'implement' is gate-blocked+stopped."""
        wf, rs, ag = _copy_examples_with_fake_agents(tmp_path)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--budget-total",
                "2000",  # admits "design" (~1395) but not "implement" (~1383) after it
                "--on-exhaustion",
                "stop",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        # ---- PIN ----
        assert result.exit_code == 1, result.output
        run_id = _cli_run_id(result.output)
        run_dir = _run_dir(tmp_path, run_id)
        events = _cli_events(run_dir)
        assert any(
            e.get("event") == "budget.exhausted" and e.get("task_id") == "implement" for e in events
        ), events

        # ---- NEW ----
        assert BUILTIN_BUDGET_EXHAUSTED in _tripped_ids(run_dir)


# ---------------------------------------------------------------------------
# Site 3: Quota max-wait exceeded (engine.py ~466-482) -> BUILTIN_QUOTA_MAX_WAIT
# ---------------------------------------------------------------------------


class TestQuotaMaxWaitParity:
    def test_quota_max_wait_parity(self, tmp_path: Path, read_jsonl) -> None:
        """quota_max_wait_seconds expires => run fails instead of waiting forever."""
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []

        # A clock that advances 1000s on every call, so the second quota check
        # always observes elapsed > max_wait (deterministic, no real sleeping).
        calls = [0]

        def _fast_clock() -> datetime:
            calls[0] += 1
            return datetime.fromtimestamp(_BASE_EPOCH + calls[0] * 1000, tz=UTC)

        executor = FakeExecutor(quota_exhausted_tasks={"a": 99})  # exhausted indefinitely
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            clock=_fast_clock,
            quota_max_wait_seconds=500,  # expires after the first 1000s advance
            quota_poll_seconds=10,
        )
        wf = _workflow([_task("a", outputs=["out/a.txt"])])

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # ---- PIN ----
        assert state.status == "failed"
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert any(e.get("event") == "quota.max_wait_exceeded" for e in events), events

        # ---- NEW ----
        assert BUILTIN_QUOTA_MAX_WAIT in _tripped_ids(run_dir)
        assert any(
            e.get("event") == "breaker.trip" and e.get("breaker_id") == BUILTIN_QUOTA_MAX_WAIT
            for e in events
        ), events

    def test_quota_max_wait_parity_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CliRunner-level counterpart.

        DispatchExecutor (the CLI's only executor) always builds a bare `FakeExecutor()`
        with no quota-exhaustion config and the CLI has no flag to inject one, so this
        monkeypatches the `FakeExecutor` name inside `agent_orchestrator.executors`
        (looked up at `DispatchExecutor.__init__` call time) to return a preconfigured
        instance -- the run still goes through the real `ao run` CLI command end-to-end.

        `--quota-max-wait -1` deterministically expires the max-wait on the very FIRST
        quota-exhaustion occurrence (elapsed is exactly 0 the instant it's first observed;
        remaining = -1 - 0 <= 0) without needing any real sleep or clock injection -- the
        CLI always uses `Orchestrator`'s default (real) sleeper/clock, which can't be
        swapped for a fixed one, so this is the only deterministic way to hit this site
        from the CLI. (`--quota-max-wait 0` would NOT work: cli.py's three-layer merge
        uses `x or default`, so a falsy 0 silently falls through to the 21600s default --
        a separate, out-of-scope defect; -1 is truthy and avoids it.)
        """
        wf, rs, ag = _copy_examples_with_fake_agents(tmp_path)
        monkeypatch.setattr(
            executors_module,
            "FakeExecutor",
            lambda: FakeExecutor(quota_exhausted_tasks={"design": 5}),
        )

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--quota-max-wait",
                "-1",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        # ---- PIN ----
        assert result.exit_code == 1, result.output
        run_id = _cli_run_id(result.output)
        run_dir = _run_dir(tmp_path, run_id)
        events = _cli_events(run_dir)
        assert any(e.get("event") == "quota.max_wait_exceeded" for e in events), events

        # ---- NEW ----
        assert BUILTIN_QUOTA_MAX_WAIT in _tripped_ids(run_dir)


# ---------------------------------------------------------------------------
# Site 4 (bonus -- not one of the LLD's three named pins, but also a re-frame target,
# LLD §8.1: "same builtin id as site 2"): Provider-429 budget-exhaustion stop
# (engine.py ~575-584) -> BUILTIN_BUDGET_EXHAUSTED
# ---------------------------------------------------------------------------


class TestProvider429StopParity:
    def test_provider_429_stop_parity(self, tmp_path: Path, read_jsonl) -> None:
        """Provider 429 + on_exhaustion='stop' => run fails (distinct code path from site 2,
        same builtin id)."""
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        store, rs_store = _make_workspace(tmp_path)
        clock = _clock_at()
        mgr = DefaultBudgetManager(budget_spec, clock)
        estimator = HeuristicTokenEstimator(store)
        executor = FakeExecutor(rate_limit_tasks={"a": None})
        orch = Orchestrator(
            executor=executor,
            artifact_store=store,
            runstate_store=rs_store,
            budget_manager=mgr,
            estimator=estimator,
            clock=clock,
        )
        wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # ---- PIN ----
        assert state.status == "failed"
        assert state.budget_counters.consumed_tokens == 0  # estimate reversed after 429
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert any(e.get("event") == "budget.exhausted" for e in events), events

        # ---- NEW ----
        assert BUILTIN_BUDGET_EXHAUSTED in _tripped_ids(run_dir)
        assert any(
            e.get("event") == "breaker.trip" and e.get("breaker_id") == BUILTIN_BUDGET_EXHAUSTED
            for e in events
        ), events


# ---------------------------------------------------------------------------
# AC-3: transient wait/retry loops are NOT trips
# ---------------------------------------------------------------------------


class TestTransientWaitsAreNotTrips:
    """429 wait, quota poll wait, and budget wait must never record a trip.

    Each sub-test drives the run to eventual success through a transient wait branch and
    asserts state.tripped_breakers stays empty and no breaker.trip event is ever logged --
    both before AND after the re-frame (these branches are explicitly untouched).
    """

    def test_provider_429_wait_is_not_a_trip(self, tmp_path: Path, read_jsonl) -> None:
        retry_epoch = _BASE_EPOCH + 5.0
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            on_exhaustion="wait",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        executor = FakeExecutor(rate_limit_tasks={"a": retry_epoch})
        store, rs_store = _make_workspace(tmp_path)
        clock = _clock_at()
        mgr = DefaultBudgetManager(budget_spec, clock)
        estimator = HeuristicTokenEstimator(store)
        sleep_calls: list[float] = []
        orch = Orchestrator(
            executor=executor,
            artifact_store=store,
            runstate_store=rs_store,
            sleeper=sleep_calls.append,
            budget_manager=mgr,
            estimator=estimator,
            clock=clock,
        )
        wf = _workflow([_task("a", outputs=["out/a.txt"])], budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded", f"expected succeeded, got {state.status}"
        assert len(sleep_calls) >= 1  # the 429 wait really happened
        assert state.tripped_breakers == []
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert not any(e.get("event") == "breaker.trip" for e in events), events

    def test_quota_poll_wait_is_not_a_trip(self, tmp_path: Path, read_jsonl) -> None:
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []
        executor = FakeExecutor(quota_exhausted_tasks={"a": 1})
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            quota_max_wait_seconds=3600,
            quota_poll_seconds=10,
        )
        wf = _workflow([_task("a", outputs=["out/a.txt"])])

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert len(sleeps) == 1  # the quota poll wait really happened
        assert state.tripped_breakers == []
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert not any(e.get("event") == "breaker.trip" for e in events), events

    def test_budget_wait_is_not_a_trip(self, tmp_path: Path, read_jsonl) -> None:
        """Rate-window exhaustion + on_exhaustion='wait': window rolls, task admits."""
        window_tokens = 200
        budget_spec = BudgetSpec(
            rate=RateLimit(tokens=window_tokens, window_seconds=60),
            on_exhaustion="wait",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=10
            ),
        )
        call_count = [0]
        base = _BASE_EPOCH

        def advancing_clock() -> datetime:
            call_count[0] += 1
            if call_count[0] <= 5:
                return datetime.fromtimestamp(base, tz=UTC)
            return datetime.fromtimestamp(base + 61, tz=UTC)

        sleep_calls: list[float] = []
        store, rs_store = _make_workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, advancing_clock)
        estimator = HeuristicTokenEstimator(store)
        executor = FakeExecutor()
        orch = Orchestrator(
            executor=executor,
            artifact_store=store,
            runstate_store=rs_store,
            sleeper=sleep_calls.append,
            budget_manager=mgr,
            estimator=estimator,
            clock=advancing_clock,
        )

        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)
        initial_state = rs_store.new_run(wf)
        initial_state.budget_counters.window_start_epoch = base
        initial_state.budget_counters.window_consumed_tokens = window_tokens  # full
        rs_store.save(initial_state)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial_state)

        assert state.status == "succeeded", f"expected succeeded, got {state.status}"
        assert len(sleep_calls) >= 1  # the budget wait really happened
        assert state.tripped_breakers == []
        run_dir = _run_dir(tmp_path, state.run_id)
        events = read_jsonl(run_dir / "run.log")
        assert not any(e.get("event") == "breaker.trip" for e in events), events
