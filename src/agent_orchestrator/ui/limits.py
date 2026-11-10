"""Run-level "Limits & settings" rows for the run detail (E-aYG5vn FR-3/FR-4, T-xNFBww).

Derived on demand from ``state.json`` (+ the run's workflow snapshot for breaker/budget caps and
the overseer adapter for its budget) -- no new persisted state. A pre-upgrade run has neither
recorded settings nor, possibly, a snapshot: it simply yields fewer rows (never invented ones),
and the dashboard shows a dash with an explanatory tooltip.

Row shape (``RunLimit``): ``value`` is the effective value + ``source`` its layer
(``cli|env|config|default|unset|workflow|overseer``); ``used``/``cap`` are set where consumption
against a cap is measurable and ``unit`` says how to format them (``usd|s|tokens|tasks|""``).
The frontend renders *any* row it is given, so later producers (e.g. sentinel caps/live
overrides) only need to append rows -- they appear when present and are absent otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from ..models import CircuitBreakerSpec, RunState, WorkflowSnapshot
from ..run_options import OPTIONS

SOURCE_WORKFLOW: Final = "workflow"
SOURCE_RUN: Final = "run"

UNIT_USD: Final = "usd"
UNIT_SECONDS: Final = "s"
UNIT_TOKENS: Final = "tokens"
UNIT_TASKS: Final = "tasks"

# Registry options whose value is a duration in seconds (so the UI formats them as time).
_SECONDS_OPTIONS: Final = frozenset({"quota_max_wait", "quota_poll_interval"})


@dataclass(frozen=True)
class RunLimit:
    key: str
    label: str
    value: Any = None
    source: str = ""
    used: float | None = None
    cap: float | None = None
    unit: str = ""
    note: str | None = None


@dataclass(frozen=True)
class RunMeasures:
    """Current run-wide consumption, as already computed for the summary."""

    cost_usd: float
    wall_seconds: float
    active_seconds: float
    injected_tasks: int
    max_task_cost_usd: float


# breaker condition -> (label, unit, accessor over the measures)
_BREAKER_MEASURES: Final[dict[str, tuple[str, str, Callable[[RunMeasures], float]]]] = {
    "run_cost_usd": ("Run cost cap", UNIT_USD, lambda m: m.cost_usd),
    "task_cost_usd": ("Task cost cap (max task so far)", UNIT_USD, lambda m: m.max_task_cost_usd),
    "run_wall_clock_seconds": ("Wall clock cap", UNIT_SECONDS, lambda m: m.wall_seconds),
    "run_active_seconds": ("Active time cap", UNIT_SECONDS, lambda m: m.active_seconds),
    "injected_task_count": ("Injected tasks cap", UNIT_TASKS, lambda m: float(m.injected_tasks)),
}


def _breaker_row(b: CircuitBreakerSpec, state: RunState, m: RunMeasures) -> RunLimit | None:
    spec = _BREAKER_MEASURES.get(b.condition)
    if spec is None:
        return None
    label, unit, used = spec
    # An operator `--extend-breaker` override replaces the declared threshold (E-3JTmVu).
    cap = state.breaker_overrides.get(b.id, b.threshold)
    return RunLimit(
        key=f"breaker.{b.id}",
        label=f"{label} ({b.id})",
        source=SOURCE_WORKFLOW,
        used=used(m),
        cap=cap,
        unit=unit,
        note=f"action: {b.action}" + (" · extended" if b.id in state.breaker_overrides else ""),
    )


def build_run_limits(
    state: RunState, snapshot: WorkflowSnapshot | None, measures: RunMeasures
) -> list[RunLimit]:
    """Settings (with source) first, then consumption-vs-cap rows."""
    rows: list[RunLimit] = []
    for opt in OPTIONS:
        eff = state.effective_settings.get(opt.name)
        if eff is None:
            continue
        rows.append(
            RunLimit(
                key=f"setting.{opt.name}",
                label=opt.ui_label or opt.name,
                value=eff.value,
                source=eff.source,
                unit=UNIT_SECONDS if opt.name in _SECONDS_OPTIONS else "",
            )
        )

    rows.append(
        RunLimit(
            key="run.cost",
            label="Run cost",
            source=SOURCE_RUN,
            used=measures.cost_usd,
            unit=UNIT_USD,
        )
    )
    if snapshot is not None:
        wf = snapshot.workflow
        if wf.budget is not None and wf.budget.total_tokens is not None:
            rows.append(
                RunLimit(
                    key="budget.tokens",
                    label="Token budget",
                    source=SOURCE_WORKFLOW,
                    used=state.budget_counters.consumed_tokens,
                    cap=wf.budget.total_tokens,
                    unit=UNIT_TOKENS,
                )
            )
        rows.append(
            RunLimit(
                key="workflow.timeout_seconds",
                label="Default task timeout",
                value=wf.defaults.timeout_seconds,
                source=SOURCE_WORKFLOW,
                unit=UNIT_SECONDS,
            )
        )
        rows.extend(r for b in wf.circuit_breakers if (r := _breaker_row(b, state, measures)))
    return rows
