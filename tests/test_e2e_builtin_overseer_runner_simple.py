"""Simplified E2E test for overseer-runner to verify the harness mechanism works.

This is a minimal test to verify that:
1. ScriptedOverseerExecutor can inject outputs
2. The workflow completes end-to-end with scripted data
3. Ledger validation passes

Scenarios:
(a) Two waves with early closeout
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from tests.overseer_runner_harness import (
    ScriptedOverseerExecutor,
    ScriptEntry,
    build_ask,
    build_breadcrumb,
    build_charter,
    build_checkpoint_task,
    build_digest,
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
    """Load overseer_tool.py for ledger verification."""
    spec = importlib.util.spec_from_file_location("overseer_tool_test_simple", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ov = _load_overseer_tool()


@pytest.fixture(autouse=True)
def _no_workspace_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from parent repo config."""
    monkeypatch.delenv("AO_WORKFLOW", raising=False)
    monkeypatch.delenv("AO_REPOSETS", raising=False)
    monkeypatch.delenv("AO_AGENTS", raising=False)


def _make_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Set up minimal workspace for overseer-runner."""
    ws = tmp_path / "workspace"
    ws.mkdir()

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

    ag = ws / "agents.json"
    ag.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {
                    agent: {"executor": "fake"}
                    for agent in [
                        "architect",
                        "developer",
                        "git-operator",
                        "manager",
                        "reviewer",
                        "tester",
                    ]
                },
            }
        )
    )

    ao_dir = ws / ".ao"
    ao_dir.mkdir()
    (ao_dir / "config.yaml").write_text("")

    return ws, rs, ag


class TestOverseerRunnerScenarios:
    """Simplified end-to-end scenarios."""

    def test_scenario_a_minimal_two_waves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Minimal scenario (a): scaffold, scaffold fixture, run, assert end-state.

        This is a simplified version that just proves the mechanism works:
        - Scaffold an instance
        - Create minimal scripts for each task
        - Run the workflow
        - Assert it completes with valid ledger
        """
        ws, rs, ag = _make_workspace(tmp_path)

        # Scaffold
        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "minimal-test",
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
        assert result.exit_code == 0, f"Scaffolding failed: {result.output}"

        # Find instance
        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]

        # Build minimal script data
        # Get the instance_dir relative to workspace root
        instance_dir_rel = instance_dir.relative_to(ws)

        charter_ask = build_ask(
            ask_id="A1",
            statement="Do the work",
            deliverable_type="code",
            acceptance=["Work done"],
            usable_bar="Basic work",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        # Minimal scripts: just enough to pass validation
        intake_script = ScriptEntry(
            output_map={
                "charter.json": charter,
                "charter.md": "# Charter\n",
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-impl",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path="outputs/waves/w01/briefs/w01-01-impl.json",
                        output_report="outputs/waves/w01/w01-01-impl.md",
                        output_breadcrumb="outputs/progress/w01-01-impl.json",
                        instance_dir=str(instance_dir_rel),
                    ),
                    # A verify-kind unit alongside the implement unit: real digest.json now
                    # survives (harness no longer clobbers it -- T-WruPiv review), so OV-R14's
                    # early-closeout evidence check genuinely fires (stage=explore is an "early"
                    # closeout stage) and requires a passing verify-kind ledger unit for ask A1
                    # before ck-01 may decide closeout.
                    build_unit_task(
                        unit_id="w01-02-verify",
                        wave=1,
                        agent="tester",
                        depends_on=["intake"],
                        brief_path="outputs/waves/w01/briefs/w01-02-verify.json",
                        output_report="outputs/waves/w01/w01-02-verify.md",
                        output_breadcrumb="outputs/progress/w01-02-verify.json",
                        instance_dir=str(instance_dir_rel),
                        kind="verify",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-impl", "w01-02-verify"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-impl.json",
                            f"{instance_dir_rel}/outputs/progress/w01-02-verify.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        # Create briefs for both units
        brief_w01_01 = json.dumps(
            {
                "schema": "ao.overseer.brief/v1",
                "unit_id": "w01-01-impl",
                "wave": 1,
                "ask_ids": ["A1"],
                "work_item": "A1/impl",
                "kind": "implement",
                "goal": "Do the work",
                "acceptance": ["Work done"],
                "approach_change": None,
                "prior_attempts": [],
                "context_paths": [],
                "touches": [],
            }
        )
        brief_w01_02 = json.dumps(
            {
                "schema": "ao.overseer.brief/v1",
                "unit_id": "w01-02-verify",
                "wave": 1,
                "ask_ids": ["A1"],
                "work_item": "A1/impl",
                "kind": "verify",
                "goal": "Verify the work",
                "acceptance": ["Work done"],
                "approach_change": None,
                "prior_attempts": [],
                "context_paths": [],
                "touches": [],
            }
        )

        # Unit scripts
        w01_01_script = ScriptEntry(
            output_map={
                "w01-01-impl.md": "# Unit Report\n",
                "w01-01-impl.json": build_breadcrumb(
                    unit_id="w01-01-impl", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )
        w01_02_script = ScriptEntry(
            output_map={
                "w01-02-verify.md": "# Unit Report\n",
                "w01-02-verify.json": build_breadcrumb(
                    unit_id="w01-02-verify", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        # Checkpoint script: close out
        ck01_script = ScriptEntry(
            output_map={
                "ck-01/digest.json": build_digest(
                    checkpoint="ck-01", stage="explore", allowed_wave_size=6
                ),
                "ck-01/verdict.json": build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="closeout",
                    alignment=[{"ask_id": "A1", "status": "met", "evidence": "Done"}],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                "ck-01/report.md": "# Checkpoint Report\n",
            },
            manifest={"tasks": build_tail_tasks("ck-01", str(instance_dir_rel))},
            cost_usd=5.0,
        )

        # Tail scripts
        final_verify_script = ScriptEntry(
            output_map={"verify.md": "# Verify\n"},
            cost_usd=2.0,
        )
        closeout_script = ScriptEntry(
            output_map={"closeout.md": "# Closeout\n"},
            cost_usd=2.0,
        )
        final_push_script = ScriptEntry(
            output_map={"push-report.md": "# Push\n"},
            cost_usd=1.0,
        )

        # Install scripted executor
        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-impl": w01_01_script,
            "w01-02-verify": w01_02_script,
            "ck-01": ck01_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)

        # Pre-create the briefs so intake can read them
        brief_path = instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-impl.json"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief_w01_01)
        brief_path_verify = (
            instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json"
        )
        brief_path_verify.write_text(brief_w01_02)

        # Run
        result = runner.invoke(
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

        # If failed, show the output for debugging
        if result.exit_code != 0:
            print("Run output:")
            print(result.output)

        assert result.exit_code == 0, f"Run failed: {result.output}"

        # Assertions
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()
        ov.verify_ledger_chain(instance_dir)
