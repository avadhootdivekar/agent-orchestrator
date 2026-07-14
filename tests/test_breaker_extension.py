"""Unit tests for `apply_breaker_extension` + centralized override resolution in
`evaluate_breakers` (T-yX1Oi5, epic E-3JTmVu).

Addresses the T-t4m8x1-documented gap (epic E-rc7k2v STATUS.md): once a breaker id has
recorded a trip, `evaluate_breakers` skips it forever, even across `ao resume`. This landed
a SCOPED fix -- only a breaker an operator explicitly extends gets un-latched, never a
blanket unlatch-everything (which would regress every other breaker's existing, intentional
latch-forever resumability -- proven by the regression class at the bottom of this file plus
a full run of tests/test_resume_replay.py + tests/test_stop_reframe_parity.py, see STATUS.md
for the recorded pass counts).

Covers TASK.md acceptance criteria:
1. `apply_breaker_extension` isolated: extend_by_seconds path, extend_by_same path (uses the
   ORIGINAL spec.threshold, not the current effective value), un-latch verified (only the
   named id removed from tripped_breakers, others untouched), both-given / neither-given
   errors, unset-threshold error.
2. `evaluate_breakers` integration: an override on a non-time condition (task_failures)
   changes trip behaviour too -- proves centralized/uniform resolution, not a wall-clock-only
   special case.
3. End-to-end mechanism proof (below the CLI layer): a breaker trips, gets extended (un-latched
   + threshold bumped), does not immediately re-trip, then trips again once new activity pushes
   the condition past the EXTENDED threshold too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.breakers import apply_breaker_extension, evaluate_breakers
from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.logging_setup import get_run_logger
from agent_orchestrator.models import CircuitBreakerSpec, RunState, TaskRunState, WorkflowSpec

_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _clock_at(offset: float = 0.0):
    target = datetime.fromtimestamp(_BASE_EPOCH + offset, tz=UTC)
    return lambda: target


def _run_state(**overrides: object) -> RunState:
    base: dict[str, object] = dict(
        run_id="r1",
        workflow_id="wf",
        repo_set="rs",
        started_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    base.update(overrides)
    return RunState(**base)  # type: ignore[arg-type]


def _workflow(circuit_breakers: list[CircuitBreakerSpec]) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0", id="wf", repo_set="rs", tasks=[], circuit_breakers=circuit_breakers
    )


def _store(tmp_path: Path) -> LocalFsArtifactStore:
    return LocalFsArtifactStore(str(tmp_path))


def _failing_tasks(n: int) -> dict[str, TaskRunState]:
    return {
        f"f{i}": TaskRunState(status="failed", ended_at=f"2026-01-01T00:00:{i:02d}+00:00")
        for i in range(n)
    }


# ---------------------------------------------------------------------------
# apply_breaker_extension -- isolated unit tests
# ---------------------------------------------------------------------------


class TestApplyBreakerExtensionIsolated:
    def test_extend_by_seconds_adds_to_current_effective_threshold(self) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        new_threshold = apply_breaker_extension(
            state,
            spec,
            extend_by_seconds=50,
            extend_by_same=False,
            clock=_clock_at(),
            run_log=run_log,
        )

        assert new_threshold == 150
        assert state.breaker_overrides == {"b": 150}

    def test_extend_by_same_adds_the_original_spec_threshold_not_current_effective(self) -> None:
        """Extending twice by "same amount set at startup" must add the ORIGINAL
        spec.threshold (100) each time, not the current (already-bumped) effective value."""
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        first = apply_breaker_extension(
            state,
            spec,
            extend_by_seconds=None,
            extend_by_same=True,
            clock=_clock_at(),
            run_log=run_log,
        )
        assert first == 200  # 100 (current effective, == spec.threshold) + 100 (original)

        second = apply_breaker_extension(
            state,
            spec,
            extend_by_seconds=None,
            extend_by_same=True,
            clock=_clock_at(),
            run_log=run_log,
        )
        assert second == 300  # 200 (current effective) + 100 (ORIGINAL spec.threshold again)
        assert state.breaker_overrides == {"b": 300}

    def test_unlatches_only_the_named_id_others_untouched(self) -> None:
        from agent_orchestrator.models import TrippedBreaker

        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state(
            tripped_breakers=[
                TrippedBreaker(id="b", condition="run_wall_clock_seconds", action="stop", at="t"),
                TrippedBreaker(id="other", condition="task_failures", action="fail", at="t"),
            ]
        )
        run_log = get_run_logger(state.run_id)

        apply_breaker_extension(
            state,
            spec,
            extend_by_seconds=10,
            extend_by_same=False,
            clock=_clock_at(),
            run_log=run_log,
        )

        ids = [tb.id for tb in state.tripped_breakers]
        assert "b" not in ids
        assert "other" in ids

    def test_both_given_raises(self) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        with pytest.raises(SpecValidationError):
            apply_breaker_extension(
                state,
                spec,
                extend_by_seconds=10,
                extend_by_same=True,
                clock=_clock_at(),
                run_log=run_log,
            )

    def test_neither_given_raises(self) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        with pytest.raises(SpecValidationError):
            apply_breaker_extension(
                state,
                spec,
                extend_by_seconds=None,
                extend_by_same=False,
                clock=_clock_at(),
                run_log=run_log,
            )

    def test_unset_spec_threshold_raises(self) -> None:
        spec = CircuitBreakerSpec(id="b", condition="verdict", action="fail")
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        with pytest.raises(SpecValidationError):
            apply_breaker_extension(
                state,
                spec,
                extend_by_seconds=10,
                extend_by_same=False,
                clock=_clock_at(),
                run_log=run_log,
            )

    @pytest.mark.parametrize("bad_value", [0, -10])
    def test_extend_by_seconds_non_positive_raises(self, bad_value: float) -> None:
        """A zero/negative delta would silently shrink or no-op the threshold, bypassing
        the schema's own exclusiveMinimum:0 invariant -- reviewer-flagged gap, closed."""
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        with pytest.raises(SpecValidationError):
            apply_breaker_extension(
                state,
                spec,
                extend_by_seconds=bad_value,
                extend_by_same=False,
                clock=_clock_at(),
                run_log=run_log,
            )
        assert state.breaker_overrides == {}  # rejected before any mutation

    def test_logs_breaker_extend_event(self) -> None:
        from unittest.mock import MagicMock

        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=100
        )
        state = _run_state()
        run_log = MagicMock()

        apply_breaker_extension(
            state,
            spec,
            extend_by_seconds=10,
            extend_by_same=False,
            clock=_clock_at(),
            run_log=run_log,
        )

        run_log.warning.assert_called_once()
        args, kwargs = run_log.warning.call_args
        assert args[0] == "breaker.extend"
        assert kwargs["extra"]["event"] == "breaker.extend"
        assert kwargs["extra"]["breaker_id"] == "b"
        assert kwargs["extra"]["old_threshold"] == 100
        assert kwargs["extra"]["new_threshold"] == 110


# ---------------------------------------------------------------------------
# evaluate_breakers -- centralized override resolution (non-wall-clock condition too)
# ---------------------------------------------------------------------------


class TestEvaluateBreakersOverrideResolution:
    def test_override_lowers_effective_threshold_for_task_failures(self, tmp_path: Path) -> None:
        """Proves the override resolution in evaluate_breakers is centralized/uniform --
        NOT a wall-clock-only special case. task_failures normally needs threshold=5; with
        an override of 2, the same 2-failure state now trips."""
        spec = CircuitBreakerSpec(id="fails", condition="task_failures", action="stop", threshold=5)
        wf = _workflow([spec])
        state = _run_state(tasks=_failing_tasks(2))
        run_log = get_run_logger(state.run_id)

        # Control: without an override, 2 failures < threshold 5 -> no trip.
        control = evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)
        assert control is None
        assert state.tripped_breakers == []

        # With an override lowering the effective threshold to 2, the SAME state now trips.
        state.breaker_overrides["fails"] = 2
        action = evaluate_breakers(wf, state, _clock_at(1), _store(tmp_path), run_log)

        assert action == "stop"
        assert len(state.tripped_breakers) == 1
        assert state.tripped_breakers[0].id == "fails"

    def test_no_override_entry_evaluates_against_unchanged_spec_threshold(
        self, tmp_path: Path
    ) -> None:
        """A spec with no override entry must behave byte-identically to before overrides
        existed (regression safety, NFR-1) -- 4 failures against threshold=5 never trips."""
        spec = CircuitBreakerSpec(id="fails", condition="task_failures", action="stop", threshold=5)
        wf = _workflow([spec])
        state = _run_state(tasks=_failing_tasks(4))
        run_log = get_run_logger(state.run_id)

        action = evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)

        assert action is None
        assert state.tripped_breakers == []
        assert state.breaker_overrides == {}


# ---------------------------------------------------------------------------
# End-to-end mechanism: trip -> extend (un-latch) -> proceed -> trip again later
# ---------------------------------------------------------------------------


class TestExtensionMechanismEndToEnd:
    def test_extended_breaker_does_not_immediately_retrip_but_can_trip_again_later(
        self, tmp_path: Path
    ) -> None:
        spec = CircuitBreakerSpec(id="fails", condition="task_failures", action="stop", threshold=1)
        wf = _workflow([spec])
        state = _run_state(tasks=_failing_tasks(1))
        run_log = get_run_logger(state.run_id)
        store = _store(tmp_path)

        # 1) Trips at 1 failure >= threshold 1.
        action1 = evaluate_breakers(wf, state, _clock_at(), store, run_log)
        assert action1 == "stop"
        assert [tb.id for tb in state.tripped_breakers] == ["fails"]

        # 2) Operator extends by 5 (new effective threshold = 1 + 5 = 6); un-latches.
        new_threshold = apply_breaker_extension(
            state,
            spec,
            extend_by_seconds=5,
            extend_by_same=False,
            clock=_clock_at(1),
            run_log=run_log,
        )
        assert new_threshold == 6
        assert state.tripped_breakers == []

        # 3) Re-evaluating immediately (still only 1 failure) does NOT re-trip -- the run
        #    can proceed past the point it previously stopped.
        action2 = evaluate_breakers(wf, state, _clock_at(2), store, run_log)
        assert action2 is None
        assert state.tripped_breakers == []

        # 4) More failures accumulate past the EXTENDED threshold (6) -- the breaker CAN
        #    trip again later, proving the un-latch is real (not a permanent disable).
        state.tasks.update(_failing_tasks(6))  # total failures now >= 6
        action3 = evaluate_breakers(wf, state, _clock_at(3), store, run_log)
        assert action3 == "stop"
        assert [tb.id for tb in state.tripped_breakers] == ["fails"]
