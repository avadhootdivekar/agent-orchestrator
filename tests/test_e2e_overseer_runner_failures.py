"""E2E tests for overseer-runner failure scenarios (E-YAAGhk, T-vmI0jI).

Tests for:
(e) Backstop + G5 + unit gate
(e1) close-out path (FR-19)
(e2) continue path (FR-16)
(f) Signal response
(g) Cancel
(h) Parallel execution

All scenarios run deterministically and assert on real end-state files.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from tests.overseer_runner_harness import (
    ScriptedOverseerExecutor,
    ScriptedOverseerExecutorWithHaltFlag,
    ScriptEntry,
    build_ask,
    build_breadcrumb,
    build_brief,
    build_budget_override,
    build_charter,
    build_checkpoint_task,
    build_tail_tasks,
    build_unit_task,
    build_verdict,
)

runner = CliRunner()

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL_PATH = (
    _REPO_ROOT
    / "src"
    / "agent_orchestrator"
    / "templates"
    / "builtin"
    / "overseer-runner"
    / "tools"
    / "overseer_tool.py"
)


def _load_overseer_tool():
    """Load overseer_tool.py module for access to verify_ledger_chain and other functions."""
    spec = importlib.util.spec_from_file_location(
        "overseer_tool_under_test_e2e_failures", _TOOL_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()


@pytest.fixture(autouse=True)
def _no_workspace_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from the parent repo's own workspace config."""
    monkeypatch.delenv("AO_WORKFLOW", raising=False)
    monkeypatch.delenv("AO_REPOSETS", raising=False)
    monkeypatch.delenv("AO_AGENTS", raising=False)


def _make_workspace_for_overseer_runner(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Set up a minimal workspace for overseer-runner.

    Returns (workspace, reposets.json, agents.json)
    """
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Reposet with one named set
    rs = ws / "reposets.json"
    rs.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "main": {
                        "repos": [{"id": "target", "path": str(ws), "role": "primary"}],
                        "workspace_root": str(ws),
                    }
                },
            }
        )
    )

    # Agents: all 6 required by overseer-runner, using fake executor
    ag = ws / "agents.json"
    agents = {
        agent_name: {"executor": "fake"}
        for agent_name in [
            "architect",
            "developer",
            "git-operator",
            "manager",
            "reviewer",
            "tester",
        ]
    }
    ag.write_text(json.dumps({"version": "1.0", "agents": agents}))

    # Empty .ao/config.yaml to isolate from parent
    ao_dir = ws / ".ao"
    ao_dir.mkdir()
    (ao_dir / "config.yaml").write_text("")

    return ws, rs, ag


class TestOverseerRunnerFailures:
    """End-to-end failure scenarios for overseer-runner template."""

    def test_scenario_e_backstop_g5_unit_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (e): Backstop + G5 + unit gate.

        Run crosses budget at ck-01 settle.
        Expect: run-budget-backstop trips, w02-* and ck-02 injected as pending (G5).
        Plain resume: w02-* fail with BUDGET error (unit gate blocks at $0).
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-e",
                "--param",
                "repo_set=main",
                "--param",
                "run_budget_usd=50",
                "--param",
                # wave_size=1 keeps the STAGE machine's own projection (which uses
                # min(wave_size, time_cap) units, not the actual emitted count) inside the
                # "explore" band at both intake and ck-01's prep time -- the default wave_size=6
                # alone makes even intake's first projected wave exceed the closeout threshold
                # (compute_allowed_wave_size forces 0 at "closeout"), rejecting the manifest
                # before the scenario's real budget-crossing-at-settle behavior can be exercised.
                "wave_size=1",
                "--param",
                f"python_bin={sys.executable}",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Test budget",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json"
                ): build_brief(
                    unit_id="w01-01-work",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="Do work",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-work",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-work.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-work.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-work"],
                        input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w01-01-work.json"],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-work.json"): build_breadcrumb(
                    unit_id="w01-01-work", outcome="done", verdict="pass"
                ),
            },
            # Deliberately small: this becomes the median est_unit_cost_usd for ck-01's OWN
            # prep-time stage projection (real ledger history now exists). A small value here
            # keeps that projection in "explore" so ck-01 can validly emit wave 2, while the
            # actual budget-backstop trip comes from ck-01's own settle cost below.
            cost_usd=5.0,
        )

        # ck-01: cost=40 -> run_cost_usd = 5(intake) + 5(w01-01-work) + 40(ck-01) = 50,
        # meets/crosses the run-budget-backstop threshold (run_budget_usd=50) exactly at ck-01's
        # own settle -- the engine evaluates breakers AFTER G5 injection (T-pYt478), so w02-01-work
        # and ck-02 are already injected as pending before the trip stops the run.
        ck01_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-work",
                    wave=2,
                    agent="developer",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-work.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-work.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-work.json",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-work"],
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w02-01-work.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="continue",
                    alignment=[{"ask_id": "A1", "status": "on_track", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json"
                ): build_brief(
                    unit_id="w02-01-work",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="More work",
                    acceptance=["Done"],
                ),
            },
            manifest=ck01_manifest,
            cost_usd=40.0,
        )

        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json").write_text(
            json.dumps(
                ck01_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json")
                ]
            )
        )

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-work": w01_01_script,
            "ck-01": ck01_script,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)
        ScriptedOverseerExecutor.CALL_LOG = []

        # Run should fail at backstop trip
        result1 = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result1.exit_code != 0, result1.output

        run_id_match = re.search(r"Run:\s+(\S+)", result1.output)
        assert run_id_match, result1.output
        run_id = run_id_match.group(1)

        # AC1: Check w02-* and ck-02 are pending (G5). `state.injected_tasks` entries are
        # TaskSpec-shaped (id/agent/instruction/inputs/outputs/...) and carry NO status field --
        # per-task runtime status (status/attempts/cumulative_cost_usd/...) lives in the separate
        # `state.tasks` mapping, keyed by task id.
        state_path = ws / ".orchestrator" / "runs" / run_id / "state.json"
        state_data = json.loads(state_path.read_text())
        injected_ids = {t["id"] for t in state_data.get("injected_tasks", [])}
        assert "w02-01-work" in injected_ids
        assert "ck-02" in injected_ids
        tasks = state_data["tasks"]
        assert tasks["w02-01-work"]["status"] == "pending"
        assert tasks["w02-01-work"]["cumulative_cost_usd"] == 0.0
        assert tasks["ck-02"]["status"] == "pending"
        assert tasks["ck-02"]["cumulative_cost_usd"] == 0.0
        # The trip happened exactly at ck-01's settle: run_cost_usd = 5 (intake) + 5
        # (w01-01-work) + 40 (ck-01) = 50, meeting the run-budget-backstop threshold.
        assert tasks["ck-01"]["status"] == "succeeded"
        assert tasks["ck-01"]["cumulative_cost_usd"] == 40.0

        # AC2 (remaining part): a PLAIN `ao resume` (no --extend-breaker) must fail w02-01-work's
        # `ov-unit-gate` pre_hook with a BUDGET refusal at $0 -- `unit_gate()` (overseer_tool.py)
        # raises Violation("BUDGET", ...) whenever `state.spent >= run_budget_usd`, which is
        # exactly the state left by the trip above (spent=50 >= run_budget_usd=50). This never
        # calls the executor (cost stays $0), and ck-02 is never reached since it depends on
        # w02-01-work.
        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result2.exit_code != 0, result2.output

        state_data_2 = json.loads(state_path.read_text())
        tasks_2 = state_data_2["tasks"]
        assert tasks_2["w02-01-work"]["status"] == "failed"
        assert tasks_2["w02-01-work"]["cumulative_cost_usd"] == 0.0
        pre_hook_result = tasks_2["w02-01-work"]["pre_hook_result"]
        assert pre_hook_result is not None
        assert pre_hook_result["status"] == "failed"
        assert pre_hook_result["hook_name"] == "ov-unit-gate"
        # `unit_gate()` is documented to write NOTHING at all (success or failure), and its
        # "BUDGET:" `Violation` message isn't captured into any state.json field or result file
        # (confirmed empirically: `detail`/`error` are both empty/null on a real failed run) --
        # so the concrete, directly-checkable claim from AC2's "(executor never called for that
        # unit)" is verified via the harness's own CALL_LOG instead of string-matching CLI output:
        # w02-01-work's pre_hook rejected it before `ScriptedOverseerExecutor.execute()` ever ran
        # for it, in EITHER the first run or this resume.
        assert "w02-01-work" not in ScriptedOverseerExecutor.CALL_LOG
        # ck-02 is never reached: it depends on w02-01-work, which just failed again.
        assert tasks_2["ck-02"]["status"] == "pending"
        assert tasks_2["ck-02"]["cumulative_cost_usd"] == 0.0

    def test_scenario_e1_closeout_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (e1): Operator-driven closeout via request-closeout.

        After backstop trip, invoke request-closeout.
        Pending units skipped, run succeeds via tail.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-e1",
                "--param",
                "repo_set=main",
                "--param",
                "run_budget_usd=50",
                "--param",
                # See test_scenario_e_backstop_g5_unit_gate's identical param for why this is
                # needed: wave_size=1 keeps the stage projection in "explore" at intake/ck-01
                # prep time so the manifests validate; the same cost split (small unit cost,
                # large ck-01 cost) reproduces the exact same budget trip below.
                "wave_size=1",
                "--param",
                f"python_bin={sys.executable}",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Test",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json"
                ): build_brief(
                    unit_id="w01-01-work",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="Do work",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-work",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-work.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-work.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-work"],
                        input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w01-01-work.json"],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-work.json"): build_breadcrumb(
                    unit_id="w01-01-work", outcome="done", verdict="pass"
                ),
            },
            # Small on purpose -- see test_scenario_e_backstop_g5_unit_gate's identical comment.
            cost_usd=5.0,
        )

        # ck-01: cost=40 -> run_cost_usd = 5+5+40 = 50, crosses the backstop at ck-01's settle.
        ck01_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-work",
                    wave=2,
                    agent="developer",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-work.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-work.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-work.json",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-work"],
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w02-01-work.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="continue",
                    alignment=[{"ask_id": "A1", "status": "on_track", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json"
                ): build_brief(
                    unit_id="w02-01-work",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="More work",
                    acceptance=["Done"],
                ),
            },
            manifest=ck01_manifest,
            cost_usd=40.0,
        )

        # For ck-02 after closeout
        ck02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-02",
                    stage="explore",
                    decision="closeout",
                    alignment=[{"ask_id": "A1", "status": "met", "evidence": "Forced closeout"}],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"): "# ck-02\n",
            },
            manifest={"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))},
            cost_usd=1.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "verify.md"): "# Verify\n",
            },
            cost_usd=2.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "closeout.md"): "# Closeout\n",
            },
            cost_usd=2.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "push-report.md"): "# Push\n",
            },
            cost_usd=1.0,
        )

        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json").write_text(
            json.dumps(
                ck01_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json")
                ]
            )
        )

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-work": w01_01_script,
            "ck-01": ck01_script,
            "ck-02": ck02_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)
        ScriptedOverseerExecutor.CALL_LOG = []

        # Run should fail at backstop
        result1 = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result1.exit_code != 0, result1.output

        run_id_match = re.search(r"Run:\s+(\S+)", result1.output)
        assert run_id_match, result1.output
        run_id = run_id_match.group(1)

        # AC1: Invoke request-closeout
        overseer_tool_path = (
            _REPO_ROOT
            / "src"
            / "agent_orchestrator"
            / "templates"
            / "builtin"
            / "overseer-runner"
            / "tools"
            / "overseer_tool.py"
        )
        closeout_result = subprocess.run(
            [
                sys.executable,
                str(overseer_tool_path),
                "request-closeout",
                "--workspace-root",
                str(ws),
                "--instance-dir",
                str(instance_dir),
                "--reason",
                "test closeout",
            ],
            capture_output=True,
            text=True,
        )
        assert closeout_result.returncode == 0, f"request-closeout failed: {closeout_result.stderr}"

        # AC2: Resume should succeed
        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result2.exit_code == 0, result2.output

        # AC3: closeout.md should exist
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()

    def test_scenario_h_parallel_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (h): Parallel - two units in wave 1 execute in parallel.

        Both settle before ck-01 dispatches (with max_parallel=2).
        Ledger has two unit lines.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-h",
                "--param",
                "repo_set=main",
                "--param",
                f"python_bin={sys.executable}",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Test parallel",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json"
                ): build_brief(
                    unit_id="w01-01-work",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="Work 1",
                    acceptance=["Done"],
                ),
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-work.json"
                ): build_brief(
                    unit_id="w01-02-work",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    # kind=verify (not implement): ck-01 decides closeout below, which requires
                    # OV-R14's early-closeout evidence -- a passing verify-kind ledger unit
                    # recorded after A1's last implement/fix/stabilize/document unit. This unit
                    # still runs in the SAME wave 1, in parallel with w01-01-work (the scenario's
                    # actual point), and settles after it in ledger seq order (manifest
                    # declaration order == engine dispatch order for this scripted executor).
                    kind="verify",
                    goal="Work 2",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-work",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-work.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-work.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                    ),
                    build_unit_task(
                        unit_id="w01-02-work",
                        wave=1,
                        # kind=verify pins agent="tester" per kind_map (OV-R6) -- see the brief's
                        # own comment above for why this unit is verify-kind.
                        agent="tester",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-02-work.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-02-work.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-02-work.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-work", "w01-02-work"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                            f"{instance_dir_rel}/outputs/progress/w01-02-work.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-work.json"): build_breadcrumb(
                    unit_id="w01-01-work", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        w01_02_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-02-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w01-02-work.json"): build_breadcrumb(
                    unit_id="w01-02-work", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="closeout",
                    alignment=[{"ask_id": "A1", "status": "met", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
            },
            manifest={"tasks": build_tail_tasks("ck-01", str(instance_dir_rel))},
            cost_usd=5.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "verify.md"): "# Verify\n",
            },
            cost_usd=2.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "closeout.md"): "# Closeout\n",
            },
            cost_usd=2.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "push-report.md"): "# Push\n",
            },
            cost_usd=1.0,
        )

        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-work.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-work.json")
                ]
            )
        )

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-work": w01_01_script,
            "w01-02-work": w01_02_script,
            "ck-01": ck01_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)
        ScriptedOverseerExecutor.CALL_LOG = []

        # Run with max_parallel=2
        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--max-parallel",
                "2",
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result.exit_code == 0, result.output

        # AC1: closeout.md exists
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()

        # AC2: Ledger has 2 unit lines
        ledger_lines = ov.read_ledger_lines(instance_dir)
        unit_lines = [line for line in ledger_lines if line.get("type") == "unit"]
        assert len(unit_lines) == 2, f"Expected 2 unit lines, got {len(unit_lines)}"

        # Verify both units are present
        unit_ids = {line.get("unit_id") for line in unit_lines}
        assert "w01-01-work" in unit_ids
        assert "w01-02-work" in unit_ids

        # AC5: ordering from the executor call log (no timestamps) -- both wave-1 units were
        # actually dispatched (called) before ck-01, regardless of wall-clock/settle timing.
        call_log = ScriptedOverseerExecutor.CALL_LOG
        assert "w01-01-work" in call_log
        assert "w01-02-work" in call_log
        assert "ck-01" in call_log
        ck01_index = call_log.index("ck-01")
        assert call_log.index("w01-01-work") < ck01_index
        assert call_log.index("w01-02-work") < ck01_index

    def test_scenario_e2_continue_override_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (e2): Continue path with budget override (FR-16).

        Fresh workspace with same trip as (e): run_budget_usd=50, cost crosses at ck-01.
        Write budget-override.json without extending -> still fails.
        Resume with --extend-breaker run-budget-backstop -> units run -> ck-02 computes.
        Ledger has exactly one budget_override event.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-e2",
                "--param",
                "repo_set=main",
                "--param",
                "run_budget_usd=50",
                "--param",
                "wave_size=1",
                "--param",
                f"python_bin={sys.executable}",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Test budget override",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json"
                ): build_brief(
                    unit_id="w01-01-work",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="Do work",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-work",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-work.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-work.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-work"],
                        input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w01-01-work.json"],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-work.json"): build_breadcrumb(
                    unit_id="w01-01-work", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        # ck-01: cost=40 -> run_cost_usd = 5+5+40 = 50, crosses backstop
        ck01_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-work",
                    wave=2,
                    agent="developer",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-work.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-work.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-work.json",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-work"],
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w02-01-work.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="continue",
                    alignment=[{"ask_id": "A1", "status": "on_track", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json"
                ): build_brief(
                    unit_id="w02-01-work",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="More work",
                    acceptance=["Done"],
                ),
            },
            manifest=ck01_manifest,
            cost_usd=40.0,
        )

        w02_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w02" / "w02-01-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w02-01-work.json"): build_breadcrumb(
                    unit_id="w02-01-work", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        ck02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-02",
                    stage="stabilize",
                    decision="closeout",
                    alignment=[{"ask_id": "A1", "status": "met", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"): "# ck-02\n",
            },
            manifest={"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))},
            cost_usd=1.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "verify.md"): "# Verify\n",
            },
            cost_usd=2.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "closeout.md"): "# Closeout\n",
            },
            cost_usd=2.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "push-report.md"): "# Push\n",
            },
            cost_usd=1.0,
        )

        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json").write_text(
            json.dumps(
                ck01_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-work.json")
                ]
            )
        )

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-work": w01_01_script,
            "ck-01": ck01_script,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)
        ScriptedOverseerExecutor.CALL_LOG = []

        # Run should fail at backstop
        result1 = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result1.exit_code != 0, result1.output

        run_id_match = re.search(r"Run:\s+(\S+)", result1.output)
        assert run_id_match, result1.output
        run_id = run_id_match.group(1)

        state_path = ws / ".orchestrator" / "runs" / run_id / "state.json"

        # AC1: Write budget-override.json without extending -- should still fail
        (instance_dir / "control").mkdir(parents=True, exist_ok=True)
        (instance_dir / "control" / "budget-override.json").write_text(
            json.dumps(build_budget_override(run_budget_usd=100, reason="test override"))
        )

        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        # Without extend-breaker, resume should still fail
        assert result2.exit_code != 0, result2.output

        state_data_2 = json.loads(state_path.read_text())
        tasks_2 = state_data_2["tasks"]
        # w02-01-work should still fail with BUDGET error (never executed)
        assert tasks_2["w02-01-work"]["status"] == "failed"
        assert "w02-01-work" not in ScriptedOverseerExecutor.CALL_LOG
        # ck-02 still pending since w02-01-work depends on it
        assert tasks_2["ck-02"]["status"] == "pending"

        # AC2: Now resume with --extend-breaker and the override should take effect
        # Reset the script to include the units that will now run
        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-work": w01_01_script,
            "ck-01": ck01_script,
            "w02-01-work": w02_01_script,
            "ck-02": ck02_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        ScriptedOverseerExecutor.CALL_LOG = []

        result3 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--extend-breaker",
                "run-budget-backstop",
                "--extend-by-seconds",
                "100",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result3.exit_code == 0, result3.output

        state_data_3 = json.loads(state_path.read_text())
        tasks_3 = state_data_3["tasks"]
        # Now w02-01-work and ck-02 should have succeeded
        assert tasks_3["w02-01-work"]["status"] == "succeeded"
        assert tasks_3["ck-02"]["status"] == "succeeded"
        assert "w02-01-work" in ScriptedOverseerExecutor.CALL_LOG

        # AC3: Ledger should have exactly one budget_override event
        ledger_lines = ov.read_ledger_lines(instance_dir)
        budget_override_lines = [
            line
            for line in ledger_lines
            if line.get("type") == "event" and line.get("event") == "budget_override"
        ]
        assert len(budget_override_lines) == 1, (
            f"Expected 1 budget_override event, got {len(budget_override_lines)}"
        )
        assert budget_override_lines[0]["run_budget_usd"] == 100
        assert budget_override_lines[0]["reason"] == "test override"

    def test_scenario_f_signal_response(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (f): Signal response handling (FR-13, FR-18).

        Script ledger to have [review:fail, fix:pass, review:fail, fix:pass] pattern
        on one work item -> period_repeat signal fires.
        Variant 1: verdict omits signal_responses entry -> OV-R12 failure.
        Variant 2: verdict responds with redirect+approach_change -> passes.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-f",
                "--param",
                "repo_set=main",
                "--param",
                f"python_bin={sys.executable}",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Test signal response",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        # Set up wave 1 with 2 units that will build a ledger pattern
        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-review.json"
                ): build_brief(
                    unit_id="w01-01-review",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    kind="review",
                    goal="Review code",
                    acceptance=["Done"],
                ),
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-fix.json"
                ): build_brief(
                    unit_id="w01-02-fix",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    kind="fix",
                    goal="Fix issues",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-review",
                        wave=1,
                        agent="reviewer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-review.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-review.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-review.json",
                        kind="review",
                    ),
                    build_unit_task(
                        unit_id="w01-02-fix",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-02-fix.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-02-fix.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-02-fix.json",
                        kind="fix",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-review", "w01-02-fix"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-review.json",
                            f"{instance_dir_rel}/outputs/progress/w01-02-fix.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_review_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-review.md"): "# Review\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-review.json"): build_breadcrumb(
                    unit_id="w01-01-review", outcome="done", verdict="fail"
                ),
            },
            cost_usd=2.0,
        )

        w01_02_fix_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-02-fix.md"): "# Fix\n",
                str(instance_dir / "outputs" / "progress" / "w01-02-fix.json"): build_breadcrumb(
                    unit_id="w01-02-fix", outcome="done", verdict="pass"
                ),
            },
            cost_usd=2.0,
        )

        # ck-01 will emit wave 2 with the same two units to create the period pattern, PLUS one
        # verify-kind unit on a DIFFERENT work_item ("A1/verify", not "A1/impl") so it satisfies
        # OV-R14's early-closeout evidence requirement (a passing verify-kind unit for ask A1,
        # recorded after A1's last implement/fix/stabilize/document unit) for ck-02's eventual
        # `closeout` decision, WITHOUT disturbing the "A1/impl" work_item's own
        # [review:fail, fix:pass, review:fail, fix:pass] trailing sequence that `detect_period`
        # needs to see (a 3rd unit on the SAME work_item would break the exact trailing-4 match).
        ck01_manifest_w2 = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-review",
                    wave=2,
                    agent="reviewer",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-review.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-review.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-review.json",
                    kind="review",
                ),
                build_unit_task(
                    unit_id="w02-02-fix",
                    wave=2,
                    agent="developer",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-02-fix.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-02-fix.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-02-fix.json",
                    kind="fix",
                ),
                build_unit_task(
                    unit_id="w02-03-verify",
                    wave=2,
                    agent="tester",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-03-verify.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-03-verify.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-03-verify.json",
                    kind="verify",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-review", "w02-02-fix", "w02-03-verify"],
                    input_breadcrumbs=[
                        f"{instance_dir_rel}/outputs/progress/w02-01-review.json",
                        f"{instance_dir_rel}/outputs/progress/w02-02-fix.json",
                        f"{instance_dir_rel}/outputs/progress/w02-03-verify.json",
                    ],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="continue",
                    alignment=[{"ask_id": "A1", "status": "on_track", "evidence": "Code review"}],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],  # Will fail OV-R12 when signal fires in wave 2
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-review.json"
                ): build_brief(
                    unit_id="w02-01-review",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    kind="review",
                    goal="Review again",
                    acceptance=["Done"],
                ),
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-02-fix.json"
                ): build_brief(
                    unit_id="w02-02-fix",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    kind="fix",
                    goal="Fix again",
                    acceptance=["Done"],
                ),
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-03-verify.json"
                ): build_brief(
                    unit_id="w02-03-verify",
                    wave=2,
                    ask_ids=["A1"],
                    # Different work_item on purpose -- see the manifest comment above.
                    work_item="A1/verify",
                    kind="verify",
                    goal="Verify A1 for OV-R14 early-closeout evidence",
                    acceptance=["Done"],
                ),
            },
            manifest=ck01_manifest_w2,
            cost_usd=3.0,
        )

        w02_01_review_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w02" / "w02-01-review.md"): "# Review\n",
                str(instance_dir / "outputs" / "progress" / "w02-01-review.json"): build_breadcrumb(
                    unit_id="w02-01-review", outcome="done", verdict="fail"
                ),
            },
            cost_usd=2.0,
        )

        w02_02_fix_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w02" / "w02-02-fix.md"): "# Fix\n",
                str(instance_dir / "outputs" / "progress" / "w02-02-fix.json"): build_breadcrumb(
                    unit_id="w02-02-fix", outcome="done", verdict="pass"
                ),
            },
            cost_usd=2.0,
        )

        w02_03_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w02" / "w02-03-verify.md"): "# Verify\n",
                str(instance_dir / "outputs" / "progress" / "w02-03-verify.json"): build_breadcrumb(
                    unit_id="w02-03-verify", outcome="done", verdict="pass"
                ),
            },
            cost_usd=2.0,
        )

        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-review.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(
                        instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-review.json"
                    )
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-fix.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-fix.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-review.json").write_text(
            json.dumps(
                ck01_script.output_map[
                    str(
                        instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-review.json"
                    )
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-02-fix.json").write_text(
            json.dumps(
                ck01_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-02-fix.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-03-verify.json").write_text(
            json.dumps(
                ck01_script.output_map[
                    str(
                        instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-03-verify.json"
                    )
                ]
            )
        )

        # ck-02 IS scripted (not left as a FakeExecutor default stub) with an otherwise
        # fully-valid closeout (tail emitted) so the ONLY defect is the missing
        # signal_responses entry -- an unscripted ck-02 would instead fail with "verdict.json
        # is unreadable or malformed" (also OV-R12, but the wrong sub-case: this doesn't prove
        # AC1's actual claim, that a verdict OMITTING a response to a real, fired signal is
        # rejected). `_check_early_closeout_evidence` (R14) is not triggered here because the
        # real digest.json at ck-02 (2 review+fix pairs already settled) reports stage=explore
        # for a small default run_budget_usd -- deciding closeout at "explore" DOES require
        # verify evidence too, so ck-02 will ALSO carry an R14 violation alongside R12; AC1 only
        # requires OV-R12 to be present with the right message, not that it's the sole violation.
        ck02_script_v1 = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-02",
                    stage="explore",
                    decision="closeout",
                    alignment=[
                        {"ask_id": "A1", "status": "on_track", "evidence": "Periodic pattern"}
                    ],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],  # Deliberately missing the real fired signal's response
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"): "# ck-02\n",
            },
            manifest={"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))},
            cost_usd=1.0,
        )

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-review": w01_01_review_script,
            "w01-02-fix": w01_02_fix_script,
            "ck-01": ck01_script,
            "w02-01-review": w02_01_review_script,
            "w02-02-fix": w02_02_fix_script,
            "w02-03-verify": w02_03_verify_script,
            "ck-02": ck02_script_v1,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)
        ScriptedOverseerExecutor.CALL_LOG = []

        # Run wave 1 + wave 2; ck-02's scripted verdict has an empty signal_responses, so it
        # must fail with OV-R12 once the real period_repeat signal has fired in its own digest.
        result1 = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result1.exit_code != 0, result1.output

        run_id_match = re.search(r"Run:\s+(\S+)", result1.output)
        assert run_id_match, result1.output
        run_id = run_id_match.group(1)

        # NOTE: the workflow's own output directory (`instance_dir`, from the `ao new` glob
        # above) never changes across a resume -- only `.orchestrator/runs/<run_id>` (the
        # engine's internal, timestamped run-state directory, used only for the --run-id flag
        # here) is a distinct, separately-named path. Re-deriving a path from `run_id` for
        # workflow OUTPUT files is wrong (the ids don't match) -- reuse `instance_dir` directly.
        instance_dir_run = instance_dir

        # AC3: assert the EXACT OV-R12 rule id (not just "something failed").
        ck02_check = json.loads(
            (
                instance_dir_run / "outputs" / "checkpoints" / "ck-02" / "check-result.json"
            ).read_text()
        )
        assert ck02_check["ok"] is False
        r12_violations = [v for v in ck02_check["violations"] if v["rule"] == "OV-R12"]
        assert r12_violations, ck02_check
        assert any(
            "signal_responses" in v["message"] and "signal" in v["message"] for v in r12_violations
        ), r12_violations

        # Read the real digest from ck-02 to get every signal id that actually fired -- the
        # real detector run here fires BOTH `period_repeat` (the scenario's intent) AND
        # `attempt_cap` (work_item "A1/impl" now has 4 units, >= the default
        # max_attempts_per_item=3), and OV-R12 requires EVERY digest signal to be answered, not
        # just the one the scenario is nominally about. Guessing a single hardcoded id would
        # have produced a variant-2 verdict that still fails R12 for the other, unanswered
        # signal -- so this reads the digest and answers all of them for real.
        digest_path = instance_dir_run / "outputs" / "checkpoints" / "ck-02" / "digest.json"
        assert digest_path.is_file(), "ck-02's pre_hook must have run and written a real digest"
        digest_data = json.loads(digest_path.read_text())
        signals = digest_data.get("signals", [])

        period_signals = [s for s in signals if s.get("type") == "period_repeat"]
        assert len(period_signals) >= 1, (
            f"Expected at least 1 period_repeat signal, got {period_signals}"
        )

        # AC2: Now run again with a variant 2 that responds to the signal
        # Create a new verdict with the proper signal response. decision=closeout (matching the
        # tail manifest already below) rather than "continue" (which would require a real next
        # checkpoint + unit instead); the w02-03-verify unit above already satisfies OV-R14's
        # early-closeout evidence requirement for ask A1.
        ck02_verdict_v2 = build_verdict(
            checkpoint="ck-02",
            stage="converge",
            decision="closeout",
            alignment=[
                {"ask_id": "A1", "status": "met", "evidence": "Periodic pattern acknowledged"}
            ],
            criteria=[{"id": "A1.1", "status": "met"}],
            signal_responses=[
                {
                    "signal_id": sig["id"],
                    "response": "redirect",
                    "rationale": "Changing approach due to a repeating review/fix pattern",
                }
                for sig in signals
            ],
        )

        ck02_script_v2 = ScriptEntry(
            output_map={
                str(
                    instance_dir_run / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): ck02_verdict_v2,
                str(
                    instance_dir_run / "outputs" / "checkpoints" / "ck-02" / "report.md"
                ): "# ck-02\n",
            },
            manifest={"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))},
            cost_usd=1.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(instance_dir_run / "outputs" / "final" / "verify.md"): "# Verify\n",
            },
            cost_usd=2.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(instance_dir_run / "outputs" / "final" / "closeout.md"): "# Closeout\n",
            },
            cost_usd=2.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(instance_dir_run / "outputs" / "final" / "push-report.md"): "# Push\n",
            },
            cost_usd=1.0,
        )

        ScriptedOverseerExecutor.SCRIPT["ck-02"] = ck02_script_v2
        ScriptedOverseerExecutor.SCRIPT["final-verify"] = final_verify_script
        ScriptedOverseerExecutor.SCRIPT["closeout"] = closeout_script
        ScriptedOverseerExecutor.SCRIPT["final-push"] = final_push_script
        ScriptedOverseerExecutor.CALL_LOG = []

        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(instance_dir_run / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        # AC2: Should succeed with proper signal response
        assert result2.exit_code == 0, result2.output
        assert (instance_dir_run / "outputs" / "final" / "closeout.md").is_file()

    def test_scenario_g_cancel(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Scenario (g): Operator cancel via halt.flag.

        Create halt.flag during wave 1 execution (w01-01 side effect).
        Expect run to halt at next boundary.
        Remove flag, resume; settled units not re-executed.
        Run completes after resume.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-g",
                "--param",
                "repo_set=main",
                "--param",
                f"python_bin={sys.executable}",
                "--workspace",
                str(ws),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--validate-only",
            ],
        )
        assert result.exit_code == 0, result.output

        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Test cancel",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json"
                ): build_brief(
                    unit_id="w01-01-work",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="implement",
                    goal="Do work",
                    acceptance=["Done"],
                ),
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json"
                ): build_brief(
                    unit_id="w01-02-verify",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/work",
                    kind="verify",
                    goal="Verify work",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-work",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-work.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-work.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                    ),
                    build_unit_task(
                        unit_id="w01-02-verify",
                        wave=1,
                        agent="tester",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-02-verify.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-02-verify.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-02-verify.json",
                        kind="verify",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-work", "w01-02-verify"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-work.json",
                            f"{instance_dir_rel}/outputs/progress/w01-02-verify.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=2.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-work.md"): "# Work\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-work.json"): build_breadcrumb(
                    unit_id="w01-01-work", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        w01_02_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-02-verify.md"): "# Verify\n",
                str(instance_dir / "outputs" / "progress" / "w01-02-verify.json"): build_breadcrumb(
                    unit_id="w01-02-verify", outcome="done", verdict="pass"
                ),
            },
            cost_usd=2.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "verify.md"): "# Verify\n",
            },
            cost_usd=2.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "closeout.md"): "# Closeout\n",
            },
            cost_usd=2.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "push-report.md"): "# Push\n",
            },
            cost_usd=1.0,
        )

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="closeout",
                    alignment=[{"ask_id": "A1", "status": "met", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
            },
            manifest={"tasks": build_tail_tasks("ck-01", str(instance_dir_rel))},
            cost_usd=5.0,
        )

        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-work.json")
                ]
            )
        )
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json").write_text(
            json.dumps(
                intake_script.output_map[
                    str(
                        instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json"
                    )
                ]
            )
        )

        halt_flag_path = instance_dir / "control" / "halt.flag"

        # Use custom executor that writes halt.flag during w01-01-work execution
        ScriptedOverseerExecutorWithHaltFlag.SCRIPT = {
            "intake": intake_script,
            "w01-01-work": w01_01_script,
            "w01-02-verify": w01_02_verify_script,
            "ck-01": ck01_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        ScriptedOverseerExecutorWithHaltFlag.halt_flag_task_id = "w01-01-work"
        ScriptedOverseerExecutorWithHaltFlag.halt_flag_path = halt_flag_path
        monkeypatch.setattr(
            "agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutorWithHaltFlag
        )
        ScriptedOverseerExecutorWithHaltFlag.CALL_LOG = []

        # Run should halt when w01-01-work's executor writes the flag
        result1 = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        # Should exit with failure due to halt
        assert result1.exit_code != 0, result1.output

        run_id_match = re.search(r"Run:\s+(\S+)", result1.output)
        assert run_id_match, result1.output
        run_id = run_id_match.group(1)

        state_path = ws / ".orchestrator" / "runs" / run_id / "state.json"
        state_data = json.loads(state_path.read_text())
        tasks = state_data["tasks"]

        # w01-01-work should have succeeded (it's the one that wrote the flag)
        assert tasks["w01-01-work"]["status"] == "succeeded"
        initial_w01_attempts = tasks["w01-01-work"]["attempts"]

        # AC1: Remove the flag
        if halt_flag_path.is_file():
            halt_flag_path.unlink()

        # AC2: Resume; settled units should NOT be re-executed
        ScriptedOverseerExecutorWithHaltFlag.CALL_LOG = []

        result2 = runner.invoke(
            app,
            [
                "resume",
                "--run-id",
                run_id,
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        # Should succeed after resume
        assert result2.exit_code == 0, result2.output

        state_data_2 = json.loads(state_path.read_text())
        tasks_2 = state_data_2["tasks"]

        # AC2: w01-01-work should NOT be re-executed (same attempts as before)
        assert tasks_2["w01-01-work"]["status"] == "succeeded"
        final_attempts = tasks_2["w01-01-work"]["attempts"]
        assert final_attempts == initial_w01_attempts, (
            f"w01-01-work was re-executed: {initial_w01_attempts} -> {final_attempts}"
        )

        # Verify w01-01-work is NOT in the call log from resume
        assert "w01-01-work" not in ScriptedOverseerExecutorWithHaltFlag.CALL_LOG

        # AC3: Run should complete successfully with closeout.md
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()
