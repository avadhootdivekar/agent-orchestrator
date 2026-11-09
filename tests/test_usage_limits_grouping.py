"""Usage groups by (agent, model, turn budget, thinking effort), mixed old/new rows (T-xNFBww)."""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import RunState, TaskLimits, TaskRunState
from agent_orchestrator.usage import NO_EFFORT_LABEL, aggregate_usage, usage_report_payload


def _task(effort: str | None, limits: TaskLimits | None) -> TaskRunState:
    return TaskRunState(
        status="succeeded", attempts=1, dispatch_cycle=1, agent="dev", model="sonnet",
        effort=effort, effective_limits=limits, cumulative_cost_usd=1.0,
    )  # fmt: skip


def _report(tmp_path: Path, tasks: dict[str, TaskRunState]):
    state = RunState(
        run_id="r1", workflow_id="w", started_at="2026-11-09T00:00:00+00:00",
        updated_at="2026-11-09T00:01:00+00:00", status="succeeded", tasks=tasks,
    )  # fmt: skip
    return aggregate_usage([state], LocalFsArtifactStore(str(tmp_path)))


def test_same_turn_budget_different_thinking_effort_split(tmp_path: Path) -> None:
    rep = _report(
        tmp_path,
        {
            "a": _task("medium", TaskLimits(turn_budget="medium", thinking_effort="high")),
            "b": _task("medium", TaskLimits(turn_budget="medium", thinking_effort="low")),
            "c": _task("medium", TaskLimits(turn_budget="medium", thinking_effort="high")),
        },
    )
    got = {(g.turn_budget, g.thinking_effort): g.tasks for g in rep.groups}
    assert got == {("medium", "high"): 2, ("medium", "low"): 1}


def test_mixed_old_and_new_rows(tmp_path: Path) -> None:
    rep = _report(
        tmp_path,
        {
            "new": _task("medium", TaskLimits(turn_budget="medium", thinking_effort="high")),
            "old": _task("medium", None),  # pre-upgrade: effort was the turn budget
            "bare": _task(None, None),
        },
    )
    got = {(g.turn_budget, g.thinking_effort): g.tasks for g in rep.groups}
    assert got == {
        ("medium", "high"): 1,
        ("medium", NO_EFFORT_LABEL): 1,  # old row keeps its turn budget, thinking unknown
        (NO_EFFORT_LABEL, NO_EFFORT_LABEL): 1,
    }
    payload = usage_report_payload(rep)
    assert {"turn_budget", "thinking_effort", "effort"} <= set(payload["groups"][0])


def test_legacy_effort_field_still_reports_turn_budget(tmp_path: Path) -> None:
    rep = _report(tmp_path, {"a": _task("high", None)})
    assert rep.groups[0].effort == "high" == rep.groups[0].turn_budget
