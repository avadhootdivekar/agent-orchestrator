"""CliRunner e2e for ``--cost-cap-usd`` on ``ao run`` / ``ao resume`` (T-et7Edf wiring, T-nI4DBf).

Real CLI, real engine, FakeExecutor reporting ``cost_usd`` per task (AC15). The sticky cap is the
mechanism a capped-run resume relies on: ``ao resume`` without the flag keeps the persisted cap
(halts again), an explicit value replaces it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agent_orchestrator.executors as executors_pkg
from agent_orchestrator.cli import app
from agent_orchestrator.executors.fake import FakeExecutor

from .test_e2e_cli_max_parallel import (
    _write_instructions,
    _write_simple_agents,
    _write_simple_reposet,
    _write_small_workflow,
)

runner = CliRunner()
RUN_ID = "capped-run"
COST_PER_TASK = 0.6


@pytest.fixture()
def specs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    wf = _write_small_workflow(tmp_path)
    rs = _write_simple_reposet(tmp_path)
    ag = _write_simple_agents(tmp_path)
    _write_instructions(tmp_path)

    class _Costed(executors_pkg.DispatchExecutor):
        def __init__(self) -> None:
            super().__init__()
            self._fake = FakeExecutor(
                token_outputs={
                    t: {"cost_usd": COST_PER_TASK} for t in ("task_a", "task_b", "task_c")
                }
            )

    monkeypatch.setattr(executors_pkg, "DispatchExecutor", _Costed)
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return ["--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)]


def _state(tmp_path: Path) -> dict:
    return json.loads(next(tmp_path.rglob(f"{RUN_ID}/state.json")).read_text())


def _cost(st: dict) -> float:
    return round(sum(t.get("cumulative_cost_usd", 0.0) for t in st["tasks"].values()), 6)


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf"])
def test_invalid_cap_exits_2_before_any_work(tmp_path: Path, specs: list[str], bad: str) -> None:
    for cmd in (["run"], ["resume", "--run-id", "x"]):
        r = runner.invoke(app, [*cmd, *specs, "--cost-cap-usd", bad])
        assert r.exit_code == 2, r.output
    assert not list(tmp_path.rglob("state.json"))


def test_run_halts_resumably_at_the_cap_and_records_a_trip(
    tmp_path: Path, specs: list[str]
) -> None:
    r = runner.invoke(app, ["run", *specs, "--run-id", RUN_ID, "--cost-cap-usd", "1.0"])
    assert r.exit_code == 1, r.output
    st = _state(tmp_path)
    assert st["status"] == "failed" and st["cost_cap_usd"] == 1.0
    assert [t["id"] for t in st["tripped_breakers"]] == ["builtin.cost_cap"]
    assert st["tasks"]["task_c"]["status"] != "succeeded" and _cost(st) == 2 * COST_PER_TASK


def test_resume_is_sticky_until_an_explicit_new_cap(tmp_path: Path, specs: list[str]) -> None:
    r = runner.invoke(app, ["run", *specs, "--run-id", RUN_ID, "--cost-cap-usd", "1.0"])
    assert r.exit_code == 1, r.output
    # no flag: the persisted cap (1.0) still stops the run at its first dispatch
    r = runner.invoke(app, ["resume", "--run-id", RUN_ID, *specs])
    assert r.exit_code == 1, r.output
    st = _state(tmp_path)
    assert st["cost_cap_usd"] == 1.0 and st["tasks"]["task_c"]["status"] != "succeeded"
    assert _cost(st) == 2 * COST_PER_TASK  # nothing was dispatched: no new spend
    assert [t["id"] for t in st["tripped_breakers"]] == ["builtin.cost_cap"] * 2  # re-armed
    # an explicit absolute cap replaces it and the run finishes
    r = runner.invoke(app, ["resume", "--run-id", RUN_ID, *specs, "--cost-cap-usd", "5.0"])
    assert r.exit_code == 0, r.output
    st = _state(tmp_path)
    assert st["status"] == "succeeded" and st["cost_cap_usd"] == 5.0
    assert _cost(st) == pytest.approx(3 * COST_PER_TASK)


def test_run_without_a_cap_is_unchanged(tmp_path: Path, specs: list[str]) -> None:
    r = runner.invoke(app, ["run", *specs, "--run-id", RUN_ID])
    assert r.exit_code == 0, r.output
    assert _state(tmp_path)["cost_cap_usd"] is None
