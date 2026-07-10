"""Unit tests for the pluggable circuit-breaker framework (T-x8v4d3, epic E-rc7k2v).

Uses test-only stub `Breaker` subclasses registered into `BREAKER_REGISTRY` for the
duration of each test (never one of the six MVP conditions -- those land in
T-q5n7k2). Covers TASK.md acceptance criteria 1-6 at the pure `evaluate_breakers()` /
`record_trip()` level; the task-boundary engine wiring is covered separately in
tests/test_engine_breakers.py.
"""

from __future__ import annotations

import logging
import time as time_module
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.breakers import (
    BREAKER_REGISTRY,
    Breaker,
    BreakerContext,
    TripResult,
    evaluate_breakers,
    map_action,
    record_trip,
)
from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.logging_setup import get_run_logger
from agent_orchestrator.models import CircuitBreakerSpec, RunState, WorkflowSpec

_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _clock_at(offset: float = 0.0):
    """Return a clock that always returns _BASE_EPOCH + offset (fixed, deterministic)."""
    target = datetime.fromtimestamp(_BASE_EPOCH + offset, tz=UTC)
    return lambda: target


class _AlwaysTripBreaker(Breaker):
    """Test-only stub that trips unconditionally. Not one of the six MVP conditions."""

    def __init__(self, condition: str, detail: dict | None = None) -> None:
        self.condition = condition
        self._detail = detail if detail is not None else {}
        self.calls = 0
        self.last_ctx: BreakerContext | None = None

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        self.calls += 1
        self.last_ctx = ctx
        return TripResult(detail=self._detail)


class _NeverTripBreaker(Breaker):
    """Test-only stub that never trips."""

    def __init__(self, condition: str) -> None:
        self.condition = condition

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        return None


@pytest.fixture
def registry():
    """Register test-only stub breakers into the module-global BREAKER_REGISTRY.

    BREAKER_REGISTRY is process-global (mirrors the LLD's registry design), so tests
    must clean up after themselves to avoid leaking test-only conditions into other
    test modules.
    """
    registered: list[str] = []

    def _register(breaker: Breaker) -> Breaker:
        BREAKER_REGISTRY[breaker.condition] = breaker
        registered.append(breaker.condition)
        return breaker

    yield _register

    for cond in registered:
        BREAKER_REGISTRY.pop(cond, None)


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
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=[],
        circuit_breakers=circuit_breakers,
    )


def _store(tmp_path) -> LocalFsArtifactStore:
    return LocalFsArtifactStore(str(tmp_path))


# ---------------------------------------------------------------------------
# map_action
# ---------------------------------------------------------------------------


class TestMapAction:
    @pytest.mark.parametrize("action", ["fail", "stop", "pause"])
    def test_valid_actions_pass_through(self, action: str) -> None:
        assert map_action(action) == action

    def test_unknown_action_raises_spec_validation_error(self) -> None:
        with pytest.raises(SpecValidationError):
            map_action("warn")


# ---------------------------------------------------------------------------
# evaluate_breakers -- no breakers declared (the common / default case today)
# ---------------------------------------------------------------------------


class TestEvaluateBreakersNoneDeclared:
    def test_empty_circuit_breakers_returns_none_no_registry_lookup(self, tmp_path) -> None:
        wf = _workflow([])
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        result = evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)

        assert result is None
        assert state.tripped_breakers == []


# ---------------------------------------------------------------------------
# evaluate_breakers -- trip -> record -> act -> latch (AC 2, 3, 4)
# ---------------------------------------------------------------------------


class TestEvaluateBreakersTripRecordAct:
    def test_new_trip_appends_one_record_and_emits_one_event(
        self, tmp_path, caplog, registry
    ) -> None:
        registry(_AlwaysTripBreaker("test.always_trip", detail={"foo": "bar"}))
        spec = CircuitBreakerSpec(id="b1", condition="test.always_trip", action="fail")
        wf = _workflow([spec])
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            result = evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)

        assert result == "fail"
        assert len(state.tripped_breakers) == 1
        rec = state.tripped_breakers[0]
        assert rec.id == "b1"
        assert rec.condition == "test.always_trip"
        assert rec.action == "fail"
        assert rec.detail == {"foo": "bar"}
        assert rec.at  # ISO timestamp populated from the injected clock

        trip_events = [r for r in caplog.records if getattr(r, "event", None) == "breaker.trip"]
        assert len(trip_events) == 1
        evt = trip_events[0]
        assert evt.breaker_id == "b1"
        assert evt.condition == "test.always_trip"
        assert evt.action == "fail"
        assert evt.detail == {"foo": "bar"}

    def test_latched_id_not_duplicated_on_later_reevaluation(
        self, tmp_path, caplog, registry
    ) -> None:
        registry(_AlwaysTripBreaker("test.always_trip_latch"))
        spec = CircuitBreakerSpec(id="b1", condition="test.always_trip_latch", action="stop")
        wf = _workflow([spec])
        state = _run_state()
        run_log = get_run_logger(state.run_id)
        clock = _clock_at()

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            first = evaluate_breakers(wf, state, clock, _store(tmp_path), run_log)
            # Simulate a later boundary re-evaluating the same (already-tripped) spec.
            second = evaluate_breakers(wf, state, clock, _store(tmp_path), run_log)

        assert first == "stop"
        assert second is None  # already latched: no NEW trip, so no action returned
        assert len(state.tripped_breakers) == 1  # not duplicated

        trip_events = [r for r in caplog.records if getattr(r, "event", None) == "breaker.trip"]
        assert len(trip_events) == 1  # exactly one event across both evaluations

    def test_two_breakers_trip_same_boundary_both_recorded_first_declared_wins(
        self, tmp_path, registry
    ) -> None:
        registry(_AlwaysTripBreaker("test.trip_a"))
        registry(_AlwaysTripBreaker("test.trip_b"))
        spec_a = CircuitBreakerSpec(id="a", condition="test.trip_a", action="pause")
        spec_b = CircuitBreakerSpec(id="b", condition="test.trip_b", action="fail")
        run_log = get_run_logger("r1")

        # a declared before b -> a's action ("pause") wins even though both trip.
        wf = _workflow([spec_a, spec_b])
        state = _run_state()
        result = evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)

        assert result == "pause"
        assert {tb.id for tb in state.tripped_breakers} == {"a", "b"}  # BOTH recorded

        # Declared order flipped -> b's action ("fail") wins instead (order-driven, not
        # registry-iteration-driven).
        wf2 = _workflow([spec_b, spec_a])
        state2 = _run_state()
        result2 = evaluate_breakers(wf2, state2, _clock_at(), _store(tmp_path), run_log)

        assert result2 == "fail"
        assert {tb.id for tb in state2.tripped_breakers} == {"a", "b"}

    def test_never_trip_breaker_returns_none_and_records_nothing(self, tmp_path, registry) -> None:
        registry(_NeverTripBreaker("test.never_trip"))
        spec = CircuitBreakerSpec(id="n", condition="test.never_trip", action="fail")
        wf = _workflow([spec])
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        result = evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)

        assert result is None
        assert state.tripped_breakers == []

    def test_unregistered_condition_raises_spec_validation_error(self, tmp_path) -> None:
        spec = CircuitBreakerSpec(id="x", condition="not_a_registered_condition", action="fail")
        wf = _workflow([spec])
        state = _run_state()
        run_log = get_run_logger(state.run_id)

        with pytest.raises(SpecValidationError):
            evaluate_breakers(wf, state, _clock_at(), _store(tmp_path), run_log)


# ---------------------------------------------------------------------------
# Determinism / purity w.r.t. the injected clock (AC 5)
# ---------------------------------------------------------------------------


class TestEvaluateBreakersPureClock:
    def test_ctx_clock_epoch_derives_only_from_injected_clock(
        self, tmp_path, monkeypatch, registry
    ) -> None:
        stub = registry(_AlwaysTripBreaker("test.clock_probe"))
        spec = CircuitBreakerSpec(id="c", condition="test.clock_probe", action="fail")
        wf = _workflow([spec])
        state = _run_state()
        # A mock run_log (not a real Logger) sidesteps stdlib logging's own internal
        # time.time() call (LogRecord.__init__ stamps `created` from it) so the guard
        # below only catches a bare time.time() call from breakers.py itself.
        run_log = MagicMock()

        def _boom(*_a: object, **_kw: object) -> float:
            raise AssertionError("breaker evaluation must never call time.time() directly")

        monkeypatch.setattr(time_module, "time", _boom)

        fixed_clock = _clock_at(offset=123.0)
        evaluate_breakers(wf, state, fixed_clock, _store(tmp_path), run_log)

        assert stub.last_ctx is not None
        assert stub.last_ctx.clock_epoch == fixed_clock().timestamp()


# ---------------------------------------------------------------------------
# record_trip -- the reusable primitive (LLD §8.2's future trip_builtin call site)
# ---------------------------------------------------------------------------


class TestRecordTrip:
    def test_appends_and_emits_outside_the_eval_loop(self, caplog) -> None:
        state = _run_state()
        run_log = get_run_logger(state.run_id)
        clock = _clock_at()

        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            rec = record_trip(
                state=state,
                breaker_id="builtin.budget_exhausted",
                condition="projected_cost_exceeds",
                action="fail",
                detail={"total_tokens": 100},
                clock=clock,
                run_log=run_log,
            )

        assert rec in state.tripped_breakers
        assert len(state.tripped_breakers) == 1
        assert rec.id == "builtin.budget_exhausted"

        trip_events = [r for r in caplog.records if getattr(r, "event", None) == "breaker.trip"]
        assert len(trip_events) == 1
        assert trip_events[0].breaker_id == "builtin.budget_exhausted"
        assert trip_events[0].condition == "projected_cost_exceeds"

    def test_does_not_latch_by_itself_caller_owns_dedup(self) -> None:
        """record_trip is a pure primitive: latching-by-id is evaluate_breakers's job
        today, and will be the future trip_builtin call site's job too -- calling it
        twice for the same id records twice."""
        state = _run_state()
        run_log = get_run_logger(state.run_id)
        clock = _clock_at()

        record_trip(state, "dup", "cond", "fail", {}, clock, run_log)
        record_trip(state, "dup", "cond", "fail", {}, clock, run_log)

        assert len(state.tripped_breakers) == 2
