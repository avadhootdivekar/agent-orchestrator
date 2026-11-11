"""Shared builders for schedule tests (T-tPlK1T AC10).

D-2 makes a limit mandatory, so any fixture that builds a binding must carry one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.schedules.governance import LimitsSpec, QuotaSpec
from agent_orchestrator.schedules.models import ScheduleBinding


def minimal_limits() -> LimitsSpec:
    """The smallest valid limits block: one generous daily quota."""
    return LimitsSpec(quotas=[QuotaSpec(period="day", max_runs=10_000)])


def minimal_limits_data() -> dict[str, Any]:
    """YAML/dict equivalent of :func:`minimal_limits`."""
    return {"quotas": [{"period": "day", "max_runs": 10_000}]}


def governed_binding(**kw: Any) -> ScheduleBinding:
    """A valid cron binding with ``minimal_limits()``; keyword args override any field."""
    fields: dict[str, Any] = {
        "id": "gov",
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": minimal_limits(),
    }
    fields.update(kw)
    return ScheduleBinding(**fields)


def make_schedule_workspace(tmp: Path) -> tuple[Path, Path]:
    """A temp schedule workspace + state dir: ``(ws, state)``.

    ``ws`` is resolved, holds ``.ao/`` and a trivial ``w.json`` workflow; ``state`` (not created)
    is where the service/fire stores should be redirected. Shared by the schedule e2e ``Env``.
    """
    ws = (tmp / "ws").resolve()
    (ws / ".ao").mkdir(parents=True)
    (ws / "w.json").write_text("{}")
    return ws, tmp / "state"


def pin_engine_monotonic(monkeypatch: pytest.MonkeyPatch, value: float = 1000.0) -> None:
    """Give CLI-built schedule engines a frozen monotonic clock (FU-7).

    ``schedules.cli._build_engine`` has no clock seam, so file-watch scans would run their 0.25 s
    budget against the real clock: on a loaded box an over-budget scan silently means "no fire".
    A frozen clock makes the scan budget unreachable and the tests deterministic.
    """
    from agent_orchestrator.schedules import cli as sched_cli

    real = sched_cli.ScheduleEngine

    def build(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("monotonic", lambda: value)
        return real(*args, **kwargs)

    monkeypatch.setattr(sched_cli, "ScheduleEngine", build)
