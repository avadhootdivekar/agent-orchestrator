"""T-88wSLC (w22-05): AC6 backward-compat baseline and AC9b CLI tier for scheduled-run governance.

Scope: only what the lower tiers do not already pin.
- AC6: an ungoverned `ao run` (no `--cost-cap-usd`, no schedule) writes a `state.json` equal to a
  golden captured from pre-governance commit `af24202`, apart from the HLD 18.3 defaulted fields.
- AC9b: a run whose attempt reported no actuals is ``partial`` at the ``ao schedule usage`` boundary
  and is charged conservatively (the per-run cost, not the observed spend).
Fixed injected clock, temp workspaces, scripted executor, no sleeps, no network, no real ``$HOME``.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator.executors as executors_pkg
from agent_orchestrator.executors.fake import FakeExecutor

from ..fixtures.governance.baseline_state import run_ungoverned
from ..schedules import test_cost_cap_cli_e2e as cap
from ..schedules.test_cli_e2e import Env, env  # noqa: F401  # shared fixture
from ..schedules.test_cost_cap_cli_e2e import capenv  # noqa: F401  # shared fixture

GOLDEN = Path(__file__).parent.parent / "fixtures" / "governance" / "ungoverned_state.pre_epic.json"
# HLD §18.3: the only fields an ungoverned run may gain over the pre-epic state.json.
NEW_TOP_LEVEL = {"cost_cap_usd"}
NEW_PER_TASK = {
    "attempts_without_actuals",
    "actuals_available",
}  # actuals_available: omitted if False
CLI_TASKS = ("t1", "t2", "t3", "t4")


INERT_SETTING = {"source": "unset", "value": None}


def _strip_defaulted(state: dict[str, Any]) -> dict[str, Any]:
    """Drop the fields governance added, asserting each is at its inert value first.

    HLD 18.3 names ``cost_cap_usd`` and per-task ``attempts_without_actuals``; in practice
    ``effective_settings`` (and each history entry) also records ``cost_cap_usd`` as ``unset``.
    A cost-less executor leaves ``actuals_available`` False, and the single attempt per task is
    counted blind (``attempts_without_actuals == attempts``; HLD text says 0, see report).
    """
    out = {k: v for k, v in state.items() if k not in NEW_TOP_LEVEL}
    assert state["cost_cap_usd"] is None
    settings = [state["effective_settings"]] + [
        h["settings"] for h in state["effective_settings_history"]
    ]
    assert all(s["cost_cap_usd"] == INERT_SETTING for s in settings)
    out["effective_settings"] = {
        k: v for k, v in state["effective_settings"].items() if k != "cost_cap_usd"
    }
    out["effective_settings_history"] = [
        {**h, "settings": {k: v for k, v in h["settings"].items() if k != "cost_cap_usd"}}
        for h in state["effective_settings_history"]
    ]
    out["tasks"] = {}
    for tid, task in state["tasks"].items():
        assert task["attempts_without_actuals"] == task["attempts"], tid
        assert task.get("actuals_available", False) is False, tid
        out["tasks"][tid] = {k: v for k, v in task.items() if k not in NEW_PER_TASK}
    return out


def test_ac6_ungoverned_run_state_equals_pre_epic_baseline_except_defaulted_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden = json.loads(GOLDEN.read_text())
    current = run_ungoverned(tmp_path, monkeypatch)
    assert _strip_defaulted(current) == golden


def test_ac6_baseline_golden_has_no_governance_fields() -> None:
    """The golden really is pre-epic: it must not already contain a defaulted field."""
    golden = json.loads(GOLDEN.read_text())
    assert not NEW_TOP_LEVEL & set(golden)
    assert all(not NEW_PER_TASK & set(t) for t in golden["tasks"].values())


def test_ac9b_blind_attempt_run_is_partial_and_charged_conservatively_at_the_cli_tier(
    capenv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """t2 reports no actuals (a killed / unreadable attempt): the run is `partial`, not `actual`."""

    class _Blind(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            # t2 omitted -> TaskResult without cost_usd -> actuals_available False.
            self._fake = FakeExecutor(token_outputs={t: {"cost_usd": 0.1} for t in ("t1", "t3")})

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _Blind)
    capenv.run("add", "nightly", "--interval", str(cap.INTERVAL), "--workflow", "w.json",
               "--overlap", "allow", "--quota", "day=1", "--cost-per-run", "1.00")  # fmt: skip
    cap._arm(capenv, cap.T0)
    assert cap._tick(capenv, monkeypatch, cap.T0) == 1
    cap._tick(capenv, monkeypatch, cap.SETTLE)
    rid = str(capenv.sup.launch_calls[0]["run_id"])  # type: ignore[attr-defined]

    st = cap._state(capenv, rid)
    assert st["tasks"]["t2"]["attempts_without_actuals"] >= 1
    assert st["tasks"]["t1"]["attempts_without_actuals"] == 0

    doc = cap._usage()
    day = next(w for w in doc["windows"] if w["period"] == "day")
    observed = sum(t.get("cumulative_cost_usd", 0.0) for t in st["tasks"].values())
    assert observed < 1.0
    # Conservative charge: the per-run reservation, never the (under-reported) observed spend.
    assert day["runs"] == 1 and day["actual_usd"] >= 1.0
