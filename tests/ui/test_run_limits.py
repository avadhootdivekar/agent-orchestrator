"""Run limits & effective per-task settings on the run detail / runs list (T-xNFBww).

Covers the additive backend fields: ``RunDetail.limits[]`` (+ ``settings_recorded``),
``TaskStat.limits`` / ``RunningTaskBrief`` effective fields, the overseer adapter, and that a
pre-upgrade ``state.json`` (none of the new keys) still loads and renders "nothing recorded".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.artifacts import LocalFsArtifactStore  # noqa: E402
from agent_orchestrator.models import (  # noqa: E402
    BudgetSpec,
    CircuitBreakerSpec,
    EffectiveSetting,
    RunState,
    TaskLimits,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.overseer_limits import overseer_limits  # noqa: E402
from agent_orchestrator.ui.runs import RunRepository  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run, write_run_raw  # noqa: E402

RUN = "demo-20260724T100000Z"


def _by_key(limits: list) -> dict:
    return {lim.key if hasattr(lim, "key") else lim["key"]: lim for lim in limits}


def _workflow(**kw: object) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="demo",
        repo_set="demo-repos",
        tasks=[TaskSpec(id="build", agent="dev", instruction="i.md")],
        **kw,  # type: ignore[arg-type]
    )


def _write_with_snapshot(workspace: Path, state: RunState, wf: WorkflowSpec) -> None:
    pinned = datetime.fromisoformat(state.updated_at)
    store = RunStateStore(
        str(workspace), LocalFsArtifactStore(str(workspace)), clock=lambda: pinned
    )
    store.record_spec_session(state, wf)
    store.save(state)


class TestPreUpgradeRun:
    def test_old_state_json_has_no_settings_and_nulls(self, workspace: Path) -> None:
        state = make_run_state()
        run_dir = write_run_raw(workspace, state)
        raw = json.loads((run_dir / "state.json").read_text())
        for key in ("effective_settings", "effective_settings_history", "spec_sessions"):
            raw.pop(key, None)
        for t in raw["tasks"].values():
            t.pop("effective_limits", None)
        (run_dir / "state.json").write_text(json.dumps(raw))

        detail = RunRepository(str(workspace)).detail(RUN)
        assert detail.settings_recorded is False
        # no snapshot, no recorded settings: only the always-measurable run cost, nothing invented
        assert [lim.key for lim in detail.limits] == ["run.cost"]
        assert all(t.limits is None for t in detail.tasks)

    def test_old_running_task_brief_has_null_effective_fields(self, workspace: Path) -> None:
        tasks = {"a": TaskRunState(status="running", started_at="2026-07-24T10:00:00+00:00")}
        write_run(workspace, make_run_state(status="running", tasks=tasks))
        brief = RunRepository(str(workspace)).summarize(RUN).running_tasks[0]
        assert brief.limits is None and brief.attempts == 0 and brief.cost_usd == 0.0


class TestRunLimits:
    def test_settings_carry_value_and_source(self, workspace: Path) -> None:
        state = make_run_state()
        state.effective_settings = {
            "max_parallel": EffectiveSetting(value=4, source="cli"),
            "turn_budget": EffectiveSetting(value="high", source="config"),
            "autocompact": EffectiveSetting(value=None, source="unset"),
        }
        write_run(workspace, state)
        detail = RunRepository(str(workspace)).detail(RUN)
        assert detail.settings_recorded is True
        lim = _by_key(detail.limits)
        assert lim["setting.max_parallel"].value == 4
        assert lim["setting.max_parallel"].source == "cli"
        assert lim["setting.max_parallel"].label == "Max parallel"
        assert lim["setting.turn_budget"].source == "config"
        assert lim["setting.autocompact"].value is None

    def test_breakers_budget_and_consumption_against_caps(self, workspace: Path) -> None:
        state = make_run_state()  # cost 0.75, tokens 1875, wall 300s
        state.budget_counters.consumed_tokens = 900
        state.breaker_overrides = {"cost-cap": 5.0}
        wf = _workflow(
            budget=BudgetSpec(total_tokens=1000),
            circuit_breakers=[
                CircuitBreakerSpec(
                    id="cost-cap", condition="run_cost_usd", action="stop", threshold=2.0
                ),
                CircuitBreakerSpec(
                    id="wall", condition="run_wall_clock_seconds", action="fail", threshold=600
                ),
                CircuitBreakerSpec(
                    id="active", condition="run_active_seconds", action="fail", threshold=200
                ),
                CircuitBreakerSpec(
                    id="inj", condition="injected_task_count", action="stop", threshold=10
                ),
            ],
        )
        _write_with_snapshot(workspace, state, wf)
        lim = _by_key(RunRepository(str(workspace)).detail(RUN).limits)
        assert (lim["breaker.cost-cap"].used, lim["breaker.cost-cap"].cap) == (
            0.75,
            5.0,
        )  # override
        assert lim["breaker.cost-cap"].unit == "usd"
        assert (lim["breaker.wall"].used, lim["breaker.wall"].cap) == (300.0, 600.0)
        assert lim["breaker.wall"].unit == "s"
        assert lim["breaker.active"].cap == 200.0 and lim["breaker.active"].used is not None
        assert (lim["breaker.inj"].used, lim["breaker.inj"].cap) == (0, 10.0)
        assert (lim["budget.tokens"].used, lim["budget.tokens"].cap) == (900, 1000)
        assert lim["workflow.timeout_seconds"].value == 1800

    def test_run_cost_always_reported_without_cap(self, workspace: Path) -> None:
        _write_with_snapshot(workspace, make_run_state(), _workflow())
        lim = _by_key(RunRepository(str(workspace)).detail(RUN).limits)
        assert lim["run.cost"].used == 0.75 and lim["run.cost"].cap is None

    def test_task_limits_and_running_brief(self, workspace: Path) -> None:
        tl = TaskLimits(
            thinking_effort="high", turn_budget="medium", max_turns=30, autocompact="auto",
            timeout_seconds=1800, max_attempts=3,
        )  # fmt: skip
        tasks = {
            "a": TaskRunState(
                status="running", attempts=2, started_at="2026-07-24T10:00:00+00:00",
                cumulative_cost_usd=1.5, model="sonnet", effort="medium", effective_limits=tl,
            ),
        }  # fmt: skip
        write_run(workspace, make_run_state(status="running", tasks=tasks))
        repo = RunRepository(str(workspace))
        brief = repo.summarize(RUN).running_tasks[0]
        assert brief.limits is not None and brief.limits["max_turns"] == 30
        assert (brief.attempts, brief.cost_usd) == (2, 1.5)
        stat = repo.detail(RUN).tasks[0]
        assert stat.limits is not None
        assert stat.limits["thinking_effort"] == "high" and stat.limits["timeout_seconds"] == 1800

    def test_api_payload_is_json(self, workspace: Path, stub_supervisor: StubSupervisor) -> None:
        state = make_run_state()
        state.effective_settings = {"max_parallel": EffectiveSetting(value=2, source="default")}
        write_run(workspace, state)
        service = DashboardService(str(workspace), supervisor=stub_supervisor)  # type: ignore[arg-type]
        with TestClient(create_app(service)) as c:
            body = c.get(f"{API_PREFIX}/runs/{RUN}").json()
        assert body["settings_recorded"] is True
        row = next(r for r in body["limits"] if r["key"] == "setting.max_parallel")
        assert row == {
            "key": "setting.max_parallel", "label": "Max parallel", "value": 2,
            "source": "default", "used": None, "cap": None, "unit": "", "note": None,
        }  # fmt: skip
        assert body["tasks"][0]["limits"] is None


@pytest.fixture()
def overseer_ws(tmp_path: Path) -> Iterator[Path]:
    run = tmp_path / "workflows" / "overseer-runner" / "runs" / "o-abc"
    (run / "outputs" / "checkpoints" / "ck-01").mkdir(parents=True)
    (run / "outputs" / "checkpoints" / "ck-02").mkdir(parents=True)
    (run / "overseer-config.json").write_text(
        json.dumps({"run_budget_usd": 300, "max_injected_tasks": 160, "max_waves": 12})
    )
    old = {"budget": {"spent_usd": 1.0, "run_budget_usd": 300.0, "stage": "explore"}}
    new = {"budget": {"spent_usd": 6.5, "run_budget_usd": 300.0, "stage": "converge"}}
    (run / "outputs" / "checkpoints" / "ck-01" / "digest.json").write_text(json.dumps(old))
    (run / "outputs" / "checkpoints" / "ck-02" / "digest.json").write_text(json.dumps(new))
    yield tmp_path


class TestOverseerAdapter:
    def test_budget_and_stage_from_latest_digest(self, overseer_ws: Path) -> None:
        lim = _by_key(overseer_limits(overseer_ws, "o-abc", injected_count=7))
        assert (lim["overseer.run_budget_usd"].used, lim["overseer.run_budget_usd"].cap) == (
            6.5,
            300.0,
        )
        assert lim["overseer.stage"].value == "converge"
        assert (
            lim["overseer.max_injected_tasks"].used,
            lim["overseer.max_injected_tasks"].cap,
        ) == (7, 160)

    def test_config_only_without_digest(self, overseer_ws: Path) -> None:
        import shutil

        shutil.rmtree(overseer_ws / "workflows/overseer-runner/runs/o-abc/outputs")
        lim = _by_key(overseer_limits(overseer_ws, "o-abc", injected_count=0))
        assert (
            lim["overseer.run_budget_usd"].used is None
            and lim["overseer.run_budget_usd"].cap == 300
        )
        assert "overseer.stage" not in lim

    @pytest.mark.parametrize("wf_id", ["../evil", "a/b", "", "..", "missing"])
    def test_unsafe_or_unknown_workflow_id_yields_nothing(
        self, overseer_ws: Path, wf_id: str
    ) -> None:
        assert overseer_limits(overseer_ws, wf_id, injected_count=0) == []

    def test_corrupt_files_are_tolerated(self, overseer_ws: Path) -> None:
        base = overseer_ws / "workflows/overseer-runner/runs/o-abc"
        (base / "overseer-config.json").write_text("{not json")
        (base / "outputs/checkpoints/ck-02/digest.json").write_text("[]")
        assert overseer_limits(overseer_ws, "o-abc", injected_count=0) == []

    def test_non_overseer_run_has_no_overseer_rows(self, workspace: Path) -> None:
        write_run(workspace, make_run_state())
        keys = [lim.key for lim in RunRepository(str(workspace)).detail(RUN).limits]
        assert not any(k.startswith("overseer.") for k in keys)

    def test_detail_includes_overseer_rows(self, overseer_ws: Path) -> None:
        state = make_run_state(run_id="o-abc-20261109T000000Z", workflow_id="o-abc")
        write_run(overseer_ws, state)
        keys = {lim.key for lim in RunRepository(str(overseer_ws)).detail(state.run_id).limits}
        assert "overseer.stage" in keys
