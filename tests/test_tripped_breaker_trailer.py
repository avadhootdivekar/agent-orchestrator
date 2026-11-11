"""Unit tests for the `ao status` tripped-breaker trailer collapse (T-et7Edf AC17, HLD L2)."""

from __future__ import annotations

from datetime import UTC, datetime
from logging import getLogger

from agent_orchestrator.breakers import record_trip
from agent_orchestrator.cli import _tripped_breaker_lines
from agent_orchestrator.models import RunState

_NOW = "2026-01-01T00:00:00+00:00"


def _t(i: str, c: str = "run_cost_usd", a: str = "pause") -> dict:
    return {"id": i, "condition": c, "action": a}


def test_empty_has_no_lines() -> None:
    assert _tripped_breaker_lines([]) == []


def test_single_trip_has_no_suffix() -> None:
    assert _tripped_breaker_lines([_t("builtin.cost_cap")]) == [
        "Tripped breakers: builtin.cost_cap (run_cost_usd, action=pause)"
    ]


def test_repeats_collapse_keeping_first_seen_order() -> None:
    trips = [_t("builtin.cost_cap"), _t("stop"), _t("builtin.cost_cap"), _t("builtin.cost_cap")]
    assert _tripped_breaker_lines(trips) == [
        "Tripped breakers: builtin.cost_cap (run_cost_usd, action=pause) x3",
        "Tripped breakers: stop (run_cost_usd, action=pause)",
    ]


def test_same_id_different_action_not_merged() -> None:
    assert len(_tripped_breaker_lines([_t("b", a="pause"), _t("b", a="fail")])) == 2


def test_record_trip_does_not_latch_repeated_calls_append() -> None:
    """The docstring contract: record_trip itself never dedupes; the caller owns the latch."""
    state = RunState(
        run_id="r", workflow_id="w", status="running", tasks={}, started_at=_NOW, updated_at=_NOW
    )
    for _ in range(2):
        record_trip(
            state,
            "builtin.cost_cap",
            "run_cost_usd",
            "pause",
            {},
            lambda: datetime.now(UTC),
            getLogger("t"),  # type: ignore[arg-type]
        )
    assert [tb.id for tb in state.tripped_breakers] == ["builtin.cost_cap"] * 2
