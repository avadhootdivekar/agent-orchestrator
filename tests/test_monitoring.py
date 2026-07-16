"""Unit tests for the `monitoring.py` module (T-h2XLxe, epic E-XyfjuZ).

Covers TASK.md acceptance criteria:
1. `Monitor` is an ABC; `RuleBasedMonitor`/`AgentMonitor` implement both methods.
2. `RuleBasedMonitor.decide_breaker_trip`: prior_extensions=0 -> extend; >=1 -> halt.
3. `RuleBasedMonitor.decide_task_failure`: transient+first -> retry; else accept_failure.
4. `AgentMonitor` round-trips through a test-double Executor: valid verdict parsed for
   both shapes; invalid/missing verdict or non-succeeded result -> safe default, never raises.
5. `build_task_failure_summary` introduces NO new file-content read (revised post
   early-gate review): terminal_reason/errors/stderr_tail all derive from the executor's
   own already-bounded `TaskResult.error`, re-capped at `STDERR_TAIL_MAX_CHARS`.
6. No import of `engine.py` from `monitoring.py` (one-directional dependency).

A small LOCAL test-double `Executor` is used for AgentMonitor tests rather than extending
the shared `FakeExecutor` (which has no hook to write arbitrary JSON content to a plain
output path) -- keeps this epic's blast radius off widely-used shared test infrastructure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import ArtifactStore
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.models import AgentSpec, TaskContext, TaskResult
from agent_orchestrator.monitoring import (
    DEFAULT_TRANSIENT_PATTERNS,
    SAFE_DEFAULT_BREAKER_VERDICT,
    SAFE_DEFAULT_HEAL_VERDICT,
    STDERR_TAIL_MAX_CHARS,
    AgentMonitor,
    BreakerTripSummary,
    Monitor,
    RuleBasedMonitor,
    TaskFailureSummary,
    build_task_failure_summary,
)

# ---------------------------------------------------------------------------
# AC1 / AC6: shape of the module
# ---------------------------------------------------------------------------


class TestModuleShape:
    def test_monitor_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            Monitor()  # type: ignore[abstract]

    def test_rule_based_and_agent_monitor_are_monitors(self) -> None:
        assert issubclass(RuleBasedMonitor, Monitor)
        assert issubclass(AgentMonitor, Monitor)

    def test_no_engine_import_in_monitoring_module(self) -> None:
        """monitoring.py must not import engine.py (one-directional dependency --
        engine.py imports FROM monitoring.py, never the reverse)."""
        import agent_orchestrator.monitoring as mod

        assert "agent_orchestrator.engine" not in mod.__dict__.get("__loader__", "").__str__()
        source = Path(mod.__file__).read_text()
        assert "from .engine" not in source
        assert "import engine" not in source


# ---------------------------------------------------------------------------
# AC2/AC3: RuleBasedMonitor
# ---------------------------------------------------------------------------


class TestRuleBasedMonitorBreakerPolicy:
    def test_first_extension_recommends_extend_by_same(self) -> None:
        monitor = RuleBasedMonitor()
        trip = BreakerTripSummary(
            breaker_id="b", condition="task_failures", action="stop", prior_extensions=0
        )
        verdict = monitor.decide_breaker_trip(trip, run_id="r1")
        assert verdict.decision == "extend"
        assert verdict.extend_by_seconds is None  # "same" -- caller uses extend_by_same=True

    @pytest.mark.parametrize("prior", [1, 2, 5])
    def test_already_extended_once_recommends_halt(self, prior: int) -> None:
        monitor = RuleBasedMonitor()
        trip = BreakerTripSummary(
            breaker_id="b", condition="task_failures", action="stop", prior_extensions=prior
        )
        verdict = monitor.decide_breaker_trip(trip, run_id="r1")
        assert verdict.decision == "halt"

    def test_pure_function_same_input_same_output(self) -> None:
        """No internal mutable state: calling twice with prior_extensions=0 both times
        gives 'extend' both times (RuleBasedMonitor itself never tracks a call count --
        that bookkeeping lives in the engine's RunState, passed back in via the DTO)."""
        monitor = RuleBasedMonitor()
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")
        assert monitor.decide_breaker_trip(trip, run_id="r1").decision == "extend"
        assert monitor.decide_breaker_trip(trip, run_id="r1").decision == "extend"


class TestRuleBasedMonitorFailurePolicy:
    def test_transient_pattern_matched_first_time_retries(self) -> None:
        monitor = RuleBasedMonitor(wait_seconds=42.0)
        summary = TaskFailureSummary(
            task_id="t",
            attempt_count=2,
            terminal_reason="Connection reset by peer",
            prior_heal_retries=0,
        )
        verdict = monitor.decide_task_failure(summary, run_id="r1")
        assert verdict.decision == "retry"
        assert verdict.wait_seconds == 42.0

    def test_non_transient_error_accepts_failure(self) -> None:
        monitor = RuleBasedMonitor()
        summary = TaskFailureSummary(
            task_id="t",
            attempt_count=2,
            terminal_reason="AssertionError: bad output shape",
            prior_heal_retries=0,
        )
        verdict = monitor.decide_task_failure(summary, run_id="r1")
        assert verdict.decision == "accept_failure"

    def test_transient_but_already_retried_accepts_failure(self) -> None:
        monitor = RuleBasedMonitor()
        summary = TaskFailureSummary(
            task_id="t",
            attempt_count=2,
            terminal_reason="connection timed out",
            prior_heal_retries=1,
        )
        verdict = monitor.decide_task_failure(summary, run_id="r1")
        assert verdict.decision == "accept_failure"

    @pytest.mark.parametrize(
        "text",
        [
            "network is unreachable",
            "Bad Gateway (502)",
            "Service Unavailable",
            "json.decoder.JSONDecodeError: Expecting value",
            "Broken pipe",
            "HTTP 503 from upstream",
        ],
    )
    def test_default_transient_patterns_cover_network_timeout_5xx_json(self, text: str) -> None:
        monitor = RuleBasedMonitor()
        summary = TaskFailureSummary(task_id="t", attempt_count=1, terminal_reason=text)
        assert monitor.decide_task_failure(summary, run_id="r1").decision == "retry"

    def test_extra_transient_patterns_are_additive_not_replacing(self) -> None:
        monitor = RuleBasedMonitor(extra_transient_patterns=[r"my-custom-flaky-error"])
        # A DEFAULT pattern still matches (proves additive, not replacing):
        default_summary = TaskFailureSummary(
            task_id="t", attempt_count=1, terminal_reason="timeout"
        )
        assert monitor.decide_task_failure(default_summary, run_id="r1").decision == "retry"
        # The CUSTOM pattern also matches:
        custom_summary = TaskFailureSummary(
            task_id="t", attempt_count=1, terminal_reason="my-custom-flaky-error occurred"
        )
        assert monitor.decide_task_failure(custom_summary, run_id="r1").decision == "retry"

    def test_stderr_tail_is_also_scanned_for_transient_patterns(self) -> None:
        monitor = RuleBasedMonitor()
        summary = TaskFailureSummary(
            task_id="t",
            attempt_count=1,
            terminal_reason="generic failure",
            stderr_tail="...\nEConnReset at socket layer\n",
        )
        assert monitor.decide_task_failure(summary, run_id="r1").decision == "retry"

    def test_default_transient_patterns_list_is_exposed_and_nonempty(self) -> None:
        assert len(DEFAULT_TRANSIENT_PATTERNS) > 5


# ---------------------------------------------------------------------------
# AC4: AgentMonitor via a local test-double Executor
# ---------------------------------------------------------------------------


class _VerdictWritingExecutor(Executor):
    """Local test double: on `status="succeeded"`, writes *verdict* (if given) as JSON to
    the single output path; otherwise leaves it unwritten. Lets tests exercise every
    AgentMonitor fallback path (non-succeeded result, missing verdict file, malformed
    verdict) without touching the shared `FakeExecutor` (which has no hook for writing
    arbitrary JSON content to a plain, non-manifest output path)."""

    def __init__(self, *, verdict: object = None, status: str = "succeeded") -> None:
        self._verdict = verdict
        self._status = status
        self.calls: list[TaskContext] = []

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.calls.append(ctx)
        if self._status == "succeeded" and self._verdict is not None:
            Path(ctx.output_paths[0]).write_text(json.dumps(self._verdict))
        return TaskResult(task_id=ctx.task_id, status=self._status, attempts=1, exit_code=0)


class _RaisingExecutor(Executor):
    def execute(self, ctx: TaskContext) -> TaskResult:
        raise RuntimeError("boom")


@pytest.fixture()
def monitor_agent() -> AgentSpec:
    return AgentSpec(executor="fake")


class TestAgentMonitorBreakerTrip:
    def test_valid_extend_verdict_parsed(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(
            verdict={"decision": "extend", "extend_by_seconds": 120, "reason": "looks fine"}
        )
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")

        assert verdict.decision == "extend"
        assert verdict.extend_by_seconds == 120.0
        assert verdict.reason == "looks fine"

    def test_valid_extend_verdict_with_null_extend_by_seconds(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(
            verdict={"decision": "extend", "extend_by_seconds": None, "reason": "same"}
        )
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")

        assert verdict.decision == "extend"
        assert verdict.extend_by_seconds is None

    def test_valid_halt_verdict_parsed(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(verdict={"decision": "halt", "reason": "looks bad"})
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")

        assert verdict.decision == "halt"
        assert verdict.reason == "looks bad"

    @pytest.mark.parametrize(
        "bad_verdict",
        [
            {"decision": "bogus"},
            {"extend_by_seconds": 10},  # missing decision
            {"decision": "extend", "extend_by_seconds": "not-a-number"},
            "not even an object",
            42,
        ],
    )
    def test_malformed_verdict_falls_back_to_safe_default(
        self, store: ArtifactStore, monitor_agent: AgentSpec, bad_verdict: object
    ) -> None:
        executor = _VerdictWritingExecutor(verdict=bad_verdict)
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")

        assert verdict == SAFE_DEFAULT_BREAKER_VERDICT

    def test_missing_verdict_file_falls_back_to_safe_default(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(verdict=None, status="succeeded")  # never writes
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")

        assert verdict == SAFE_DEFAULT_BREAKER_VERDICT

    @pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
    def test_non_succeeded_executor_result_falls_back_to_safe_default(
        self, store: ArtifactStore, monitor_agent: AgentSpec, status: str
    ) -> None:
        executor = _VerdictWritingExecutor(
            verdict={"decision": "extend", "reason": "x"}, status=status
        )
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")

        assert verdict == SAFE_DEFAULT_BREAKER_VERDICT

    def test_executor_raising_falls_back_to_safe_default_never_propagates(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        monitor = AgentMonitor(monitor_agent, _RaisingExecutor(), store, name="my-monitor")
        trip = BreakerTripSummary(breaker_id="b", condition="task_failures", action="stop")

        verdict = monitor.decide_breaker_trip(trip, run_id="r1")  # must not raise

        assert verdict == SAFE_DEFAULT_BREAKER_VERDICT

    def test_writes_instruction_and_context_under_run_scoped_monitor_dir(
        self, store: ArtifactStore, monitor_agent: AgentSpec, workspace: Path
    ) -> None:
        executor = _VerdictWritingExecutor(verdict={"decision": "halt", "reason": "x"})
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        trip = BreakerTripSummary(
            breaker_id="cost-cap", condition="run_cost_usd", action="fail", detail={"cost_usd": 5}
        )

        monitor.decide_breaker_trip(trip, run_id="run-123")

        call_dir = (
            workspace / ".orchestrator" / "runs" / "run-123" / "monitor" / "call-0-breaker-cost-cap"
        )
        assert (call_dir / "instruction.md").exists()
        context = json.loads((call_dir / "context.json").read_text())
        assert context["breaker_id"] == "cost-cap"
        assert context["condition"] == "run_cost_usd"


class TestAgentMonitorTaskFailure:
    def test_valid_retry_verdict_parsed(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(
            verdict={"decision": "retry", "wait_seconds": 15, "reason": "transient"}
        )
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        summary = TaskFailureSummary(task_id="t", attempt_count=2, terminal_reason="timeout")

        verdict = monitor.decide_task_failure(summary, run_id="r1")

        assert verdict.decision == "retry"
        assert verdict.wait_seconds == 15.0

    def test_missing_verdict_file_falls_back_to_safe_default(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        """Mirrors TestAgentMonitorBreakerTrip's equivalent case for the task-failure
        consult path -- a non-succeeded/missing-verdict executor result must fall back to
        SAFE_DEFAULT_HEAL_VERDICT (accept_failure) here too, not just for breaker trips."""
        executor = _VerdictWritingExecutor(verdict=None, status="succeeded")  # never writes
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        summary = TaskFailureSummary(task_id="t", attempt_count=1)

        verdict = monitor.decide_task_failure(summary, run_id="r1")

        assert verdict == SAFE_DEFAULT_HEAL_VERDICT

    def test_valid_accept_failure_verdict_parsed(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(
            verdict={"decision": "accept_failure", "reason": "not transient"}
        )
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        summary = TaskFailureSummary(task_id="t", attempt_count=2, terminal_reason="bad output")

        verdict = monitor.decide_task_failure(summary, run_id="r1")

        assert verdict.decision == "accept_failure"

    def test_malformed_decision_falls_back_to_safe_default(
        self, store: ArtifactStore, monitor_agent: AgentSpec
    ) -> None:
        executor = _VerdictWritingExecutor(verdict={"decision": "retry_forever"})
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        summary = TaskFailureSummary(task_id="t", attempt_count=1)

        verdict = monitor.decide_task_failure(summary, run_id="r1")

        assert verdict == SAFE_DEFAULT_HEAL_VERDICT

    def test_context_written_reflects_prior_heal_retries(
        self, store: ArtifactStore, monitor_agent: AgentSpec, workspace: Path
    ) -> None:
        executor = _VerdictWritingExecutor(verdict={"decision": "accept_failure"})
        monitor = AgentMonitor(monitor_agent, executor, store, name="my-monitor")
        summary = TaskFailureSummary(
            task_id="flaky-task", attempt_count=3, prior_heal_retries=1, exit_code=1
        )

        monitor.decide_task_failure(summary, run_id="run-abc")

        call_dir = (
            workspace / ".orchestrator" / "runs" / "run-abc" / "monitor" / "call-0-heal-flaky-task"
        )
        context = json.loads((call_dir / "context.json").read_text())
        assert context["prior_heal_retries"] == 1
        assert context["exit_code"] == 1


# ---------------------------------------------------------------------------
# AC5 (revised post early-gate review): build_task_failure_summary introduces NO new
# file-content read -- terminal_reason/errors/stderr_tail all derive from the executor's
# own already-bounded TaskResult.error.
# ---------------------------------------------------------------------------


class TestBuildTaskFailureSummary:
    def test_reuses_task_result_error_as_terminal_reason_errors_and_stderr_tail(self) -> None:
        result = TaskResult(
            task_id="t", status="failed", attempts=2, exit_code=1, error="boom: connection reset"
        )
        summary = build_task_failure_summary(result, prior_heal_retries=0)

        assert summary.task_id == "t"
        assert summary.attempt_count == 2
        assert summary.terminal_reason == "boom: connection reset"
        assert summary.errors == ["boom: connection reset"]
        assert summary.stderr_tail == "boom: connection reset"
        assert summary.exit_code == 1
        assert summary.prior_heal_retries == 0

    def test_no_error_gives_empty_errors_list_and_empty_stderr_tail(self) -> None:
        result = TaskResult(task_id="t", status="failed", attempts=1)
        summary = build_task_failure_summary(result, prior_heal_retries=0)
        assert summary.errors == []
        assert summary.terminal_reason is None
        assert summary.stderr_tail == ""

    def test_stderr_tail_capped_even_if_error_were_somehow_longer(self) -> None:
        """Belt-and-suspenders: even though executors/claude_cli.py already bounds
        TaskResult.error to <=500 chars, stderr_tail re-applies its own
        STDERR_TAIL_MAX_CHARS cap so the DTO's own documented bound is never violated
        regardless of what any executor implementation puts in `.error`."""
        long_error = "e" * (STDERR_TAIL_MAX_CHARS + 500)
        result = TaskResult(task_id="t", status="failed", attempts=1, error=long_error)
        summary = build_task_failure_summary(result, prior_heal_retries=0)
        assert len(summary.stderr_tail) == STDERR_TAIL_MAX_CHARS
        assert summary.stderr_tail == long_error[-STDERR_TAIL_MAX_CHARS:]

    def test_never_touches_output_artifact_path_no_file_io(self, tmp_path: Path) -> None:
        """NFR-7 / Design Decision D5 (revised): building the summary must not read ANY
        file, including the task's own capture directory -- prove this by pointing
        output_artifact_path at a directory that doesn't even exist; construction must
        still succeed using only TaskResult.error."""
        missing_dir = str(tmp_path / "does-not-exist-at-all")
        result = TaskResult(
            task_id="t",
            status="failed",
            attempts=1,
            error="network is unreachable",
            output_artifact_path=missing_dir,
        )
        summary = build_task_failure_summary(result, prior_heal_retries=0)
        assert summary.stderr_tail == "network is unreachable"
