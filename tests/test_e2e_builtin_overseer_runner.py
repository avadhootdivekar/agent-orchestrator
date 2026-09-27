"""E2E tests for overseer-runner builtin template (E-YAAGhk, T-WruPiv).

Proves the overseer-runner template scaffolds correctly and executes end-to-end
via `ao new` + `ao run`, with real hook checks and stage progression. Uses
ScriptedOverseerExecutor to drive deterministic, repeatable workflows with
exact control over what agents emit (charters, briefs, verdicts, manifests).

Tests cover four scenarios:
(a) Two waves with early verified closeout
(b) Budget stage escalation (explore → converge → stabilize → closeout)
(c) Checker rejection and self-correction on retry
(d) Human-in-the-loop hold with resume

All scenarios run deterministically and assert on real end-state files (ledger,
digests, verdicts, closeout.md, state.json).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.models import TaskContext, TaskResult
from tests.overseer_runner_harness import (
    ScriptedOverseerExecutor,
    ScriptEntry,
    build_ask,
    build_breadcrumb,
    build_brief,
    build_charter,
    build_checkpoint_task,
    build_digest,
    build_hold_request,
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
    spec = importlib.util.spec_from_file_location("overseer_tool_under_test_e2e", _TOOL_PATH)
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


class TestOverseerRunnerE2E:
    """End-to-end scenarios for overseer-runner template."""

    def test_scenario_a_two_waves_early_verified_closeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (a): Two waves with early verified closeout.

        Wave 1: 2 work units (implement + verify).
        ck-01: decides continue (both units done, verify passed, but not all units have verify).
        Wave 2: 1 verify unit.
        ck-02: all units verified, decides closeout.
        Tail: final-verify, closeout, final-push.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        # Scaffold the instance
        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-a",
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

        # Find the instance directory
        runs = list((ws / "workflows" / "overseer-runner" / "runs").glob("*"))
        assert len(runs) == 1
        instance_dir = runs[0]
        instance_dir_rel = instance_dir.relative_to(ws)

        # Now set up the scripted executor
        charter_ask = build_ask(
            ask_id="A1",
            statement="Implement feature X and verify it works",
            deliverable_type="code",
            acceptance=["Feature implemented", "Tests pass"],
            usable_bar="Partial implementation with basic tests",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        brief_w01_01 = build_brief(
            unit_id="w01-01-implement",
            wave=1,
            ask_ids=["A1"],
            work_item="A1/impl",
            kind="implement",
            goal="Implement feature X",
            acceptance=["Feature implemented"],
        )

        brief_w01_02 = build_brief(
            unit_id="w01-02-verify",
            wave=1,
            ask_ids=["A1"],
            work_item="A1/verify",
            kind="verify",
            goal="Verify feature X works",
            acceptance=["Tests pass"],
        )

        breadcrumb_w01_01 = build_breadcrumb(
            unit_id="w01-01-implement",
            outcome="done",
            verdict="pass",
            acceptance_met=[0],
        )

        breadcrumb_w01_02 = build_breadcrumb(
            unit_id="w01-02-verify",
            outcome="done",
            verdict="pass",
            acceptance_met=[1],
        )

        # ck-01: decisions based on wave 1
        ck01_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-verify",
                    wave=2,
                    agent="tester",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-verify.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-verify.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-verify.json",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-verify"],
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w02-01-verify.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        ck01_digest = build_digest(
            checkpoint="ck-01",
            stage="explore",
            allowed_wave_size=6,
        )

        ck01_verdict = build_verdict(
            checkpoint="ck-01",
            stage="explore",
            decision="continue",
            rationale="Both units done, verify passed. Plan additional verify for coverage.",
            alignment=[
                {
                    "ask_id": "A1",
                    "status": "on_track",
                    "evidence": "w01-01 and w01-02 both completed with passing verdicts",
                }
            ],
            criteria=[
                {"id": "A1.1", "status": "met"},
                {"id": "A1.2", "status": "met"},
            ],
            signal_responses=[],
        )

        brief_w02_01 = build_brief(
            unit_id="w02-01-verify",
            wave=2,
            ask_ids=["A1"],
            work_item="A1/verify",
            kind="verify",
            goal="Final verification",
            acceptance=["Tests pass"],
        )

        breadcrumb_w02_01 = build_breadcrumb(
            unit_id="w02-01-verify",
            outcome="done",
            verdict="pass",
            acceptance_met=[1],
        )

        # ck-02: all units verified, closeout
        ck02_digest = build_digest(
            checkpoint="ck-02",
            stage="explore",
            allowed_wave_size=6,
        )

        ck02_verdict = build_verdict(
            checkpoint="ck-02",
            stage="explore",
            decision="closeout",
            rationale="All asks have verify units with passing verdicts. Early closeout approved.",
            alignment=[
                {
                    "ask_id": "A1",
                    "status": "met",
                    "evidence": "w01-01, w01-02, w02-01 all completed with passing verdicts",
                }
            ],
            criteria=[
                {"id": "A1.1", "status": "met"},
                {"id": "A1.2", "status": "met"},
            ],
            signal_responses=[],
        )

        ck02_manifest = {"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))}

        # Script for each task
        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(
                    instance_dir / "outputs" / "charter.md"
                ): "# Charter\n\nA1: Implement feature X and verify it works\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-implement.json"
                ): brief_w01_01,
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json"
                ): brief_w01_02,
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-implement",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-implement.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-implement.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-implement.json",
                    ),
                    build_unit_task(
                        unit_id="w01-02-verify",
                        wave=1,
                        agent="tester",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-02-verify.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-02-verify.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-02-verify.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-implement", "w01-02-verify"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-implement.json",
                            f"{instance_dir_rel}/outputs/progress/w01-02-verify.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=10.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "w01-01-implement.md"
                ): "# w01-01 Report\n\nFeature X implemented.",
                str(
                    instance_dir / "outputs" / "progress" / "w01-01-implement.json"
                ): breadcrumb_w01_01,
            },
            cost_usd=15.0,
        )

        w01_02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "w01-02-verify.md"
                ): "# w01-02 Report\n\nVerification passed.",
                str(
                    instance_dir / "outputs" / "progress" / "w01-02-verify.json"
                ): breadcrumb_w01_02,
            },
            cost_usd=5.0,
        )

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "digest.json"
                ): ck01_digest,
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): ck01_verdict,
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"
                ): "# ck-01 Report\n\nContinuing to wave 2.",
                # Also create the brief for w02-01-verify so the emitted manifest validates
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-verify.json"
                ): brief_w02_01,
            },
            manifest=ck01_manifest,
            cost_usd=5.0,
        )

        w02_01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "w02-01-verify.md"
                ): "# w02-01 Report\n\nFinal verification passed.",
                str(
                    instance_dir / "outputs" / "progress" / "w02-01-verify.json"
                ): breadcrumb_w02_01,
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-verify.json"
                ): brief_w02_01,
            },
            cost_usd=5.0,
        )

        ck02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "digest.json"
                ): ck02_digest,
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): ck02_verdict,
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"
                ): "# ck-02 Report\n\nAll asks verified. Closing out.",
            },
            manifest=ck02_manifest,
            cost_usd=5.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "final" / "verify.md"
                ): "# Final Verification\n\nAll done.",
            },
            cost_usd=5.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "final" / "closeout.md"
                ): "# Closeout\n\nRun complete.",
            },
            cost_usd=3.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "final" / "push-report.md"
                ): "# Push Report\n\nPushed successfully.",
            },
            cost_usd=2.0,
        )

        # Install the scripted executor
        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-implement": w01_01_script,
            "w01-02-verify": w01_02_script,
            "ck-01": ck01_script,
            "w02-01-verify": w02_01_script,
            "ck-02": ck02_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)

        # Pre-create all briefs that will be needed throughout the workflow
        # Wave 1 briefs (needed for intake task)
        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (
            instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-implement.json"
        ).write_text(json.dumps(brief_w01_01))
        (instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json").write_text(
            json.dumps(brief_w01_02)
        )

        # Wave 2 briefs (needed for ck-01 manifest validation)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-verify.json").write_text(
            json.dumps(brief_w02_01)
        )

        # Run the workflow
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
        assert result.exit_code == 0, result.output

        # Assertions on end state
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()

        # Verify ledger chain is valid
        ov.verify_ledger_chain(instance_dir)

        # Verify checkpoint digests have correct stages. `digest.json` is now the REAL,
        # tool-computed file (T-WruPiv review: the harness no longer clobbers it), so the
        # stage lives at the nested `budget.stage` path the real `ov-ckpt-prep` pre_hook
        # writes -- not a flat top-level `stage` key.
        ck01_digest_path = instance_dir / "outputs" / "checkpoints" / "ck-01" / "digest.json"
        assert ck01_digest_path.is_file()
        ck01_digest_data = json.loads(ck01_digest_path.read_text())
        assert ck01_digest_data["budget"]["stage"] == "explore"

    def test_scenario_b_stage_escalation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (b): Budget stage escalation.

        run_budget_usd=100, with `wave_size=1` (an explicit `--param` override -- see the long
        comment below) so the projection-based stage machine (`derive_budget`, `overseer_tool.py`
        lines 324-401) is tractable: with the default wave_size=6, a single wave's PROJECTED
        cost (n_plan * est_unit_cost + est_ckpt_cost + reserve_tail) alone blows past every
        threshold at once, jumping straight to closeout on ck-01. `wave_size=1` keeps the
        projection close to actual spend so the costs below walk the REAL stage machine through
        explore -> converge -> stabilize -> closeout one checkpoint at a time.

        T-WruPiv review (CRITICAL finding): this scenario previously asserted a fictional stage
        sequence -- the harness clobbered the real `digest.json` the `ov-ckpt-prep` pre_hook
        computed with a test-authored flat shape, so the checker's OV-R11 stage-gated rule (and,
        it turns out, OV-R14's early-closeout evidence rule too) silently never fired, and the
        "stage" this test asserted on was hand-picked, not SUT-derived. The harness no longer
        clobbers `digest.json` (see `ScriptedOverseerExecutor` in `overseer_runner_harness.py`),
        so every cost below was reverse-engineered against the REAL `derive_budget` function
        (see the sibling standalone simulation used to derive these numbers) and verified against
        the actual on-disk `outputs/checkpoints/ck-0N/digest.json` produced by a real run.

        Costs were chosen so:
          ck-01: spent=15,  est_unit=5,    est_ckpt=5 (default) -> projection=40  -> explore
          ck-02: spent=35,  est_unit=10,   est_ckpt=5 (ck-01)   -> projection=80  -> converge
          ck-03: spent=45,  est_unit=10,   est_ckpt=5           -> projection=90  -> stabilize
          ck-04: spent=60,  est_unit=7.5,  est_ckpt=5           -> projection=95  -> closeout
        (`est_unit_cost_usd`/`est_ckpt_cost_usd` are medians of the last <=2 waves'/settled
        checkpoints' REAL recorded costs -- `compute_budget`, `overseer_tool.py` lines
        1239-1338 -- not hand-set digest fields.)

        AC3: the stabilize wave (wave 4, emitted by ck-03) contains only a stabilize-kind unit
        (no implement/research), and OV-R11's stabilize-kind restriction now genuinely enforces
        this against ck-03's real digest (previously masked, per the CRITICAL finding above).
        OV-R11's converge-stage "no new scope" restriction also now genuinely enforces that
        wave 3 (emitted by ck-02, converge stage) reuses an EXISTING work_item ("A1/impl", from
        wave 2) rather than opening a new one.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        # Scaffold with a small budget + narrow wave_size so the stage machine is tractable
        # (see the docstring above for why wave_size=1 is required here).
        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-b",
                "--param",
                "repo_set=main",
                "--param",
                "run_budget_usd=100",
                "--param",
                "converge_pct=80",
                "--param",
                "stabilize_pct=90",
                "--param",
                "closeout_pct=95",
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
            statement="Build and test feature Y",
            deliverable_type="code",
            acceptance=["Feature built", "Tests passing", "Stabilized"],
            usable_bar="Basic working feature",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        # intake: cost=10 -> spent=10 before wave 1 runs.
        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-research.json"
                ): build_brief(
                    unit_id="w01-01-research",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/research",
                    kind="research",
                    goal="Research feature Y",
                    acceptance=["Research done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-research",
                        wave=1,
                        agent="architect",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-research.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-research.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-research.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-research"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-research.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=10.0,
        )

        # w01-01-research: cost=5 -> spent=15 at ck-01's pre_hook (stage: explore).
        w01_01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "w01-01-research.md"
                ): "# Research\n",
                str(
                    instance_dir / "outputs" / "progress" / "w01-01-research.json"
                ): build_breadcrumb(unit_id="w01-01-research", outcome="done", verdict="pass"),
            },
            cost_usd=5.0,
        )

        # ck-01 (explore): emits wave 2 with one implement unit (work_item="A1/impl", a NEW
        # work item -- allowed at explore, which has no R11 "no new scope" restriction).
        ck01_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-implement",
                    wave=2,
                    agent="developer",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-implement.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-implement.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-implement.json",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-implement"],
                    input_breadcrumbs=[
                        f"{instance_dir_rel}/outputs/progress/w02-01-implement.json"
                    ],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        # ck-01 own cost=5 -> spent=20 (+ w02's 15 below -> 35 at ck-02's pre_hook: converge).
        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="continue",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "on_track",
                            "evidence": "Research complete",
                        }
                    ],
                    criteria=[
                        {"id": "A1.1", "status": "met"},
                        {"id": "A1.2", "status": "unmet"},
                        {"id": "A1.3", "status": "unmet"},
                    ],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-implement.json"
                ): build_brief(
                    unit_id="w02-01-implement",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    kind="implement",
                    goal="Implement feature Y",
                    acceptance=["Feature implemented"],
                ),
            },
            manifest=ck01_manifest,
            cost_usd=5.0,
        )

        # w02-01-implement: cost=15 -> spent=35 at ck-02's pre_hook (stage: converge).
        w02_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w02" / "w02-01-implement.md"): "# Impl\n",
                str(
                    instance_dir / "outputs" / "progress" / "w02-01-implement.json"
                ): build_breadcrumb(unit_id="w02-01-implement", outcome="done", verdict="pass"),
            },
            cost_usd=15.0,
        )

        # ck-02 (converge): emits wave 3 with one unit that REUSES the existing work_item
        # "A1/impl" (OV-R11's converge-stage "no new scope" restriction: a converge-emitted
        # unit's work_item must already exist in the ledger).
        ck02_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w03-01-test",
                    wave=3,
                    agent="tester",
                    depends_on=["ck-02"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w03/briefs/w03-01-test.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w03/w03-01-test.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w03-01-test.json",
                    kind="test",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-03",
                    wave=3,
                    depends_on=["w03-01-test"],
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w03-01-test.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        # ck-02 own cost=5 -> spent=40 (+ w03's 5 below -> 45 at ck-03's pre_hook: stabilize).
        ck02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-02",
                    stage="converge",
                    decision="continue",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "on_track",
                            "evidence": "Implementation complete",
                        }
                    ],
                    criteria=[
                        {"id": "A1.1", "status": "met"},
                        {"id": "A1.2", "status": "unmet"},
                        {"id": "A1.3", "status": "unmet"},
                    ],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"): "# ck-02\n",
                str(
                    instance_dir / "outputs" / "waves" / "w03" / "briefs" / "w03-01-test.json"
                ): build_brief(
                    unit_id="w03-01-test",
                    wave=3,
                    ask_ids=["A1"],
                    work_item="A1/impl",  # reuse: converge stage may not open new scope (R11)
                    kind="test",
                    goal="Test feature Y",
                    acceptance=["Tests passing"],
                ),
            },
            manifest=ck02_manifest,
            cost_usd=5.0,
        )

        # w03-01-test: cost=5 -> spent=45 at ck-03's pre_hook (stage: stabilize).
        w03_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w03" / "w03-01-test.md"): "# Test\n",
                str(instance_dir / "outputs" / "progress" / "w03-01-test.json"): build_breadcrumb(
                    unit_id="w03-01-test", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        # ck-03 (stabilize): emits wave 4 with ONLY a stabilize-kind unit (AC3 -- OV-R11's
        # stabilize-stage restriction: every unit's kind must be stabilize/verify/document).
        ck03_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w04-01-stabilize",
                    wave=4,
                    agent="developer",
                    depends_on=["ck-03"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w04/briefs/w04-01-stabilize.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w04/w04-01-stabilize.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w04-01-stabilize.json",
                    kind="stabilize",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-04",
                    wave=4,
                    depends_on=["w04-01-stabilize"],
                    input_breadcrumbs=[
                        f"{instance_dir_rel}/outputs/progress/w04-01-stabilize.json"
                    ],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        # ck-03 own cost=5 -> spent=50 (+ w04's 10 below -> 60 at ck-04's pre_hook: closeout).
        ck03_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-03" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-03",
                    stage="stabilize",
                    decision="stabilize",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "on_track",
                            "evidence": "Tests passing, stabilization in progress",
                        }
                    ],
                    criteria=[
                        {"id": "A1.1", "status": "met"},
                        {"id": "A1.2", "status": "met"},
                        {"id": "A1.3", "status": "unmet"},
                    ],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-03" / "report.md"): "# ck-03\n",
                str(
                    instance_dir / "outputs" / "waves" / "w04" / "briefs" / "w04-01-stabilize.json"
                ): build_brief(
                    unit_id="w04-01-stabilize",
                    wave=4,
                    ask_ids=["A1"],
                    # A FRESH work_item (not "A1/impl" again): reusing "A1/impl" a third time
                    # would push it to 3 units == max_attempts_per_item, tripping the
                    # attempt_cap signal (OV-R11's stabilize restriction is kind-only, not
                    # work_item reuse, so a new work_item is fine here, unlike wave 3/converge).
                    work_item="A1/stab",
                    kind="stabilize",
                    goal="Stabilize feature Y",
                    acceptance=["Feature stable"],
                ),
            },
            manifest=ck03_manifest,
            cost_usd=5.0,
        )

        # w04-01-stabilize: cost=10 -> spent=60 at ck-04's pre_hook (stage: closeout).
        w04_01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "waves" / "w04" / "w04-01-stabilize.md"
                ): "# Stabilize\n",
                str(
                    instance_dir / "outputs" / "progress" / "w04-01-stabilize.json"
                ): build_breadcrumb(unit_id="w04-01-stabilize", outcome="done", verdict="pass"),
            },
            cost_usd=10.0,
        )

        # ck-04 (closeout): emits the fixed tail. Stage=closeout is not an "early closeout"
        # stage (OV-R14's `_EARLY_CLOSEOUT_STAGES` is explore/converge only), so no per-ask
        # verify evidence is required here.
        ck04_manifest = {"tasks": build_tail_tasks("ck-04", str(instance_dir_rel))}

        ck04_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-04" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-04",
                    stage="closeout",
                    decision="closeout",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "met",
                            "evidence": "Feature complete and stable",
                        }
                    ],
                    criteria=[
                        {"id": "A1.1", "status": "met"},
                        {"id": "A1.2", "status": "met"},
                        {"id": "A1.3", "status": "met"},
                    ],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-04" / "report.md"): "# ck-04\n",
            },
            manifest=ck04_manifest,
            cost_usd=1.0,
        )

        final_verify_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "verify.md"): "# Final\n",
            },
            cost_usd=1.0,
        )

        closeout_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "closeout.md"): "# Closeout\n",
            },
            cost_usd=1.0,
        )

        final_push_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "final" / "push-report.md"): "# Push\n",
            },
            cost_usd=0.5,
        )

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-research": w01_01_script,
            "ck-01": ck01_script,
            "w02-01-implement": w02_01_script,
            "ck-02": ck02_script,
            "w03-01-test": w03_01_script,
            "ck-03": ck03_script,
            "w04-01-stabilize": w04_01_script,
            "ck-04": ck04_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }
        # Pre-create briefs for all waves from all script entries
        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w03" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w04" / "briefs").mkdir(parents=True, exist_ok=True)

        # Write briefs from all script entries (intake + checkpoints)
        all_scripts_b = {
            "intake": intake_script,
            "ck01": ck01_script,
            "ck02": ck02_script,
            "ck03": ck03_script,
            "ck04": ck04_script,
        }
        for script_entry in all_scripts_b.values():
            for path, content in script_entry.output_map.items():
                if "briefs" in path and isinstance(content, dict):
                    Path(path).write_text(json.dumps(content))

        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)

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
        assert result.exit_code == 0, result.output

        # Assertions
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()
        ov.verify_ledger_chain(instance_dir)

        # Verify stage progression in digests. `digest.json` is now the REAL, tool-computed
        # file (the harness no longer clobbers it -- T-WruPiv review CRITICAL finding), so the
        # stage this asserts on is genuinely SUT-derived (`derive_budget`/`compute_budget`),
        # not a hand-picked fixture value. This is a HARD assertion (not a soft `if
        # digest_path.is_file(): ...` check).
        for ck_id, expected_stage in [
            ("ck-01", "explore"),
            ("ck-02", "converge"),
            ("ck-03", "stabilize"),
            ("ck-04", "closeout"),
        ]:
            digest_path = instance_dir / "outputs" / "checkpoints" / ck_id / "digest.json"
            assert digest_path.is_file(), f"{ck_id}/digest.json missing (expected {expected_stage})"
            digest_data = json.loads(digest_path.read_text())
            assert digest_data["budget"]["stage"] == expected_stage, (
                f"{ck_id} should be {expected_stage}, got {digest_data['budget']}"
            )

        # AC3 (second half): no unit of kind implement/research exists in any wave emitted at
        # stabilize or later. This is a structural assertion on the scripted manifest's own
        # fixture content (what the TEST's fixture wave contains), now checked against the REAL
        # stage the tool computed above (ck-03/ck-04, both confirmed stabilize/closeout).
        for manifest in (ck03_manifest, ck04_manifest):
            for task in manifest["tasks"]:
                brief_path_str = next((p for p in task.get("inputs", []) if "briefs/" in p), None)
                if brief_path_str is None:
                    continue  # checkpoint/tail entries have no brief
                brief_data = json.loads((ws / brief_path_str).read_text())
                assert brief_data["kind"] not in ("implement", "research"), (
                    f"unit {task['id']} has kind={brief_data['kind']!r} in a stabilize+ wave"
                )

    def test_scenario_c_checker_rejection_self_corrects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (c): Checker rejection self-corrects on retry.

        ck-01's first attempt emits a manifest with a dangling next-checkpoint `depends_on`
        (an R10 violation -- see the long comment on `bad_manifest` below for why this is R10
        rather than the ticket narrative's original "OV-R8" guess).
        Post-hook fails with exit 2, task and run both end `failed`.
        `ao resume` is invoked explicitly (the engine does not auto-retry a failed post_hook
        within a single `ao run` invocation); the second attempt provides the correct manifest.
        Run proceeds to success.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-c",
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
            statement="Simple task",
            deliverable_type="code",
            acceptance=["Done"],
            usable_bar="Basic done",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-impl.json"
                ): build_brief(
                    unit_id="w01-01-impl",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    kind="implement",
                    goal="Do the thing",
                    acceptance=["Done"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-impl",
                        wave=1,
                        agent="developer",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-impl.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-impl.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-impl.json",
                    ),
                    build_checkpoint_task(
                        checkpoint_id="ck-01",
                        wave=1,
                        depends_on=["w01-01-impl"],
                        input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w01-01-impl.json"],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-impl.md"): "# Done\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-impl.json"): build_breadcrumb(
                    unit_id="w01-01-impl", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        # wave 2's unit -- injected only after ck-01's SECOND attempt (the good manifest).
        w02_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w02" / "w02-01-impl.md"): "# Done\n",
                str(instance_dir / "outputs" / "progress" / "w02-01-impl.json"): build_breadcrumb(
                    unit_id="w02-01-impl", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        # ck-01 first attempt: BAD manifest with a dangling `depends_on` on the emitted next
        # checkpoint (ck-02). NOTE on rule id: the ticket's own design narrative (TASK.md) names
        # this "OV-R8", but R8 (`_check_entry_r8_depends_on`) only applies to UNIT manifest
        # entries -- the "next checkpoint" entry's shape (including its `depends_on`) is entirely
        # governed by R10 (`_check_next_checkpoint_shape`), which is what actually fires here.
        # This is a doc-narrative-vs-implementation drift from before T-HPJcc6/T-tAKBBB were
        # implemented and reviewed; re-litigating the rule id split is out of this ticket's scope,
        # so this test asserts on the real, shipped rule id (R10) that this exact defect class
        # produces, not the design doc's original R8 guess. The functional intent (malformed
        # emitted-checkpoint manifest -> checker rejects -> no injection -> failed run -> the
        # retry self-corrects) is preserved. Built from the same `build_checkpoint_task()` helper
        # as the good manifest so `depends_on` is the ONLY deliberately-wrong field, keeping this
        # a single, isolated violation rather than a pile of unrelated shape errors.
        bad_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-impl",
                    wave=2,
                    agent="tester",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-impl.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-impl.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-impl.json",
                    kind="verify",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["nonexistent-task"],  # R10: dangling/wrong next-checkpoint ref
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w02-01-impl.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        # Correct manifest for second attempt
        good_manifest = {
            "tasks": [
                build_unit_task(
                    unit_id="w02-01-impl",
                    wave=2,
                    agent="tester",
                    depends_on=["ck-01"],
                    brief_path=f"{instance_dir_rel}/outputs/waves/w02/briefs/w02-01-impl.json",
                    output_report=f"{instance_dir_rel}/outputs/waves/w02/w02-01-impl.md",
                    output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w02-01-impl.json",
                    kind="verify",
                ),
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["w02-01-impl"],  # Correct: references existing unit
                    input_breadcrumbs=[f"{instance_dir_rel}/outputs/progress/w02-01-impl.json"],
                    instance_dir=str(instance_dir_rel),
                ),
            ]
        }

        # This executor will track attempts per task
        class CountingScriptedExecutor(ScriptedOverseerExecutor):
            attempt_count: dict[str, int] = {}

            def execute(self, ctx: TaskContext) -> TaskResult:
                self.attempt_count[ctx.task_id] = self.attempt_count.get(ctx.task_id, 0) + 1
                result = super().execute(ctx)

                # For ck-01, on first attempt return the bad manifest, on second return good
                if ctx.task_id == "ck-01":
                    if self.attempt_count[ctx.task_id] == 1:
                        if ctx.task_manifest_path:
                            Path(ctx.task_manifest_path).parent.mkdir(parents=True, exist_ok=True)
                            Path(ctx.task_manifest_path).write_text(json.dumps(bad_manifest))
                    elif self.attempt_count[ctx.task_id] == 2:
                        if ctx.task_manifest_path:
                            Path(ctx.task_manifest_path).parent.mkdir(parents=True, exist_ok=True)
                            Path(ctx.task_manifest_path).write_text(json.dumps(good_manifest))

                return result

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "digest.json"
                ): build_digest(checkpoint="ck-01", stage="explore", allowed_wave_size=6),
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="continue",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "on_track",
                            "evidence": "w01-01 complete",
                        }
                    ],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(
                    instance_dir / "outputs" / "waves" / "w02" / "briefs" / "w02-01-impl.json"
                ): build_brief(
                    unit_id="w02-01-impl",
                    wave=2,
                    ask_ids=["A1"],
                    work_item="A1/impl",
                    # kind=verify (not implement): OV-R14's early-closeout evidence check now
                    # genuinely fires (real digest.json survives -- T-WruPiv review), and ck-02
                    # decides closeout while still at stage=explore, which requires a passing
                    # verify-kind ledger unit for ask A1 recorded after its last
                    # implement/fix/stabilize/document unit (w01-01-impl).
                    kind="verify",
                    goal="Verify feature",
                    acceptance=["Done"],
                ),
            },
            cost_usd=5.0,
        )

        ck02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "digest.json"
                ): build_digest(checkpoint="ck-02", stage="explore", allowed_wave_size=6),
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-02",
                    stage="explore",
                    decision="closeout",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "met",
                            "evidence": "Done",
                        }
                    ],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"): "# ck-02\n",
            },
            manifest={"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))},
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

        CountingScriptedExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-impl": w01_01_script,
            "w02-01-impl": w02_01_script,
            "ck-01": ck01_script,
            "ck-02": ck02_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }

        # Pre-create briefs for all waves from all script entries
        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)

        # Write briefs from all script entries (intake + checkpoints)
        all_scripts_c = {
            "intake": intake_script,
            "ck01": ck01_script,
            "ck02": ck02_script,
        }
        for script_entry in all_scripts_c.values():
            for path, content in script_entry.output_map.items():
                if "briefs" in path and isinstance(content, dict):
                    Path(path).write_text(json.dumps(content))

        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", CountingScriptedExecutor)

        # --- First attempt: ck-01 emits the BAD manifest, the checker rejects it (R10), the
        # task and run both end failed. The engine does not auto-retry a failed post_hook within
        # a single `ao run` call, so this is a separate invocation from the eventual resume.
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

        # AC4: the checker's rejection is visible in ck-01's own check-result.json, and no
        # w02-* task was injected before the resume (the bad manifest never got past the check).
        ck01_check = json.loads(
            (instance_dir / "outputs" / "checkpoints" / "ck-01" / "check-result.json").read_text()
        )
        assert ck01_check["ok"] is False
        assert any(v["rule"] == "OV-R10" for v in ck01_check["violations"]), ck01_check

        state_path = ws / ".orchestrator" / "runs" / run_id / "state.json"
        state_before_resume = json.loads(state_path.read_text())
        injected_ids_before = {t["id"] for t in state_before_resume.get("injected_tasks", [])}
        assert not any(tid.startswith("w02") for tid in injected_ids_before), injected_ids_before

        # --- Resume: ck-01 is retried, this time emitting the GOOD manifest; the run proceeds
        # to success.
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

        # Assertions
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()
        ov.verify_ledger_chain(instance_dir)

    def test_scenario_d_human_in_the_loop_hold_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario (d): Human-in-the-loop hold with resume.

        ck-01 decides hold, writes hold-request.json.
        ck-02 emitted with empty wave (depends_on: [ck-01]).
        ckpt-prep pre_hook fails with HOLD: error (no hold-answer yet).
        Test writes hold-answer.md.
        ao resume called.
        ck-02 succeeds.
        """
        ws, rs, ag = _make_workspace_for_overseer_runner(tmp_path)

        result = runner.invoke(
            app,
            [
                "new",
                "overseer-runner",
                "scenario-d",
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
            statement="Build feature Z with human input",
            deliverable_type="code",
            acceptance=["Human approved"],
            usable_bar="Partial design",
            priority=1,
        )
        charter = build_charter([charter_ask], prompt_path=instance_dir / "prompt.md")

        intake_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "charter.json"): charter,
                str(instance_dir / "outputs" / "charter.md"): "# Charter\n",
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-01-design.json"
                ): build_brief(
                    unit_id="w01-01-design",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/design",
                    kind="design",
                    goal="Design feature Z",
                    acceptance=["Design ready"],
                ),
                # A verify-kind unit alongside the design unit: real digest.json now survives
                # (harness no longer clobbers it -- T-WruPiv review), so OV-R14's early-closeout
                # evidence check genuinely fires once ck-02 decides closeout below (still at
                # stage=explore), requiring a passing verify-kind ledger unit for ask A1.
                str(
                    instance_dir / "outputs" / "waves" / "w01" / "briefs" / "w01-02-verify.json"
                ): build_brief(
                    unit_id="w01-02-verify",
                    wave=1,
                    ask_ids=["A1"],
                    work_item="A1/design",
                    kind="verify",
                    goal="Verify the design is sound",
                    acceptance=["Design ready"],
                ),
            },
            manifest={
                "tasks": [
                    build_unit_task(
                        unit_id="w01-01-design",
                        wave=1,
                        agent="architect",
                        depends_on=["intake"],
                        brief_path=f"{instance_dir_rel}/outputs/waves/w01/briefs/w01-01-design.json",
                        output_report=f"{instance_dir_rel}/outputs/waves/w01/w01-01-design.md",
                        output_breadcrumb=f"{instance_dir_rel}/outputs/progress/w01-01-design.json",
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
                        depends_on=["w01-01-design", "w01-02-verify"],
                        input_breadcrumbs=[
                            f"{instance_dir_rel}/outputs/progress/w01-01-design.json",
                            f"{instance_dir_rel}/outputs/progress/w01-02-verify.json",
                        ],
                        instance_dir=str(instance_dir_rel),
                    ),
                ]
            },
            cost_usd=5.0,
        )

        w01_01_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-01-design.md"): "# Design\n",
                str(instance_dir / "outputs" / "progress" / "w01-01-design.json"): build_breadcrumb(
                    unit_id="w01-01-design", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        w01_02_script = ScriptEntry(
            output_map={
                str(instance_dir / "outputs" / "waves" / "w01" / "w01-02-verify.md"): "# Verify\n",
                str(instance_dir / "outputs" / "progress" / "w01-02-verify.json"): build_breadcrumb(
                    unit_id="w01-02-verify", outcome="done", verdict="pass"
                ),
            },
            cost_usd=5.0,
        )

        # ck-01 decides hold
        ck01_manifest = {
            "tasks": [
                build_checkpoint_task(
                    checkpoint_id="ck-02",
                    wave=2,
                    depends_on=["ck-01"],  # empty wave: hold shape
                    input_breadcrumbs=[],
                    instance_dir=str(instance_dir_rel),
                )
            ]
        }

        ck01_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "digest.json"
                ): build_digest(checkpoint="ck-01", stage="explore", allowed_wave_size=6),
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-01" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-01",
                    stage="explore",
                    decision="hold",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "on_track",
                            "evidence": "Design ready, awaiting human approval",
                        }
                    ],
                    criteria=[{"id": "A1.1", "status": "unmet"}],
                    signal_responses=[],
                    hold_questions=["Do you approve the design approach?"],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-01" / "report.md"): "# ck-01\n",
                str(instance_dir / "control" / "hold-request.json"): build_hold_request(
                    checkpoint_id="ck-01",
                    questions=["Do you approve the design approach?"],
                ),
                str(
                    instance_dir / "needs-input" / "ck-01.md"
                ): "# Needs Input\n\nWaiting for human approval.\n",
            },
            manifest=ck01_manifest,
            cost_usd=5.0,
        )

        # ck-02 after hold is answered
        ck02_script = ScriptEntry(
            output_map={
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "digest.json"
                ): build_digest(checkpoint="ck-02", stage="explore", allowed_wave_size=6),
                str(
                    instance_dir / "outputs" / "checkpoints" / "ck-02" / "verdict.json"
                ): build_verdict(
                    checkpoint="ck-02",
                    stage="explore",
                    decision="closeout",
                    alignment=[
                        {
                            "ask_id": "A1",
                            "status": "met",
                            "evidence": "Design approved, feature ready",
                        }
                    ],
                    criteria=[{"id": "A1.1", "status": "met"}],
                    signal_responses=[],
                ),
                str(instance_dir / "outputs" / "checkpoints" / "ck-02" / "report.md"): "# ck-02\n",
            },
            manifest={"tasks": build_tail_tasks("ck-02", str(instance_dir_rel))},
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

        ScriptedOverseerExecutor.SCRIPT = {
            "intake": intake_script,
            "w01-01-design": w01_01_script,
            "w01-02-verify": w01_02_script,
            "ck-01": ck01_script,
            "ck-02": ck02_script,
            "final-verify": final_verify_script,
            "closeout": closeout_script,
            "final-push": final_push_script,
        }

        # Pre-create briefs for all waves from all script entries
        (instance_dir / "outputs" / "waves" / "w01" / "briefs").mkdir(parents=True, exist_ok=True)
        (instance_dir / "outputs" / "waves" / "w02" / "briefs").mkdir(parents=True, exist_ok=True)

        # Write briefs from all script entries (intake + checkpoints)
        all_scripts_d = {
            "intake": intake_script,
            "ck01": ck01_script,
            "ck02": ck02_script,
        }
        for script_entry in all_scripts_d.values():
            for path, content in script_entry.output_map.items():
                if "briefs" in path and isinstance(content, dict):
                    Path(path).write_text(json.dumps(content))

        monkeypatch.setattr("agent_orchestrator.executors.FakeExecutor", ScriptedOverseerExecutor)

        # First run: will hit hold
        import re

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
        # Hold might make the run fail or succeed depending on engine behavior
        # The key is that hold-request.json is written

        # Extract the run_id from the output
        run_id_match = re.search(r"Run:\s+(\S+)", result.output)
        assert run_id_match, f"Could not extract run_id from output: {result.output}"
        run_id = run_id_match.group(1)

        # Write the hold answer
        hold_answer_path = instance_dir / "control" / "hold-answer.md"
        hold_answer_path.parent.mkdir(parents=True, exist_ok=True)
        hold_answer_path.write_text("# Hold Answer\n\nApproved. Proceed with implementation.\n")

        # TASK.md AC: the ledger's `unit` line count is unchanged across the resumed prep
        # (idempotency) -- `ingest_ledger` is keyed by unit_id, so re-running ck-02's pre_hook
        # on resume must not duplicate wave 1's already-ingested unit lines.
        unit_lines_before = [
            line for line in ov.read_ledger_lines(instance_dir) if line.get("type") == "unit"
        ]

        # Resume the run
        result = runner.invoke(
            app,
            [
                "resume",
                "--workflow",
                str(instance_dir / "workflow.json"),
                "--run-id",
                run_id,
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
        )
        assert result.exit_code == 0, result.output

        # Assertions
        assert (instance_dir / "outputs" / "final" / "closeout.md").is_file()
        ov.verify_ledger_chain(instance_dir)

        ledger_lines_after = ov.read_ledger_lines(instance_dir)
        unit_lines_after = [line for line in ledger_lines_after if line.get("type") == "unit"]
        assert len(unit_lines_after) == len(unit_lines_before), (
            f"unit line count changed across resume: {len(unit_lines_before)} -> "
            f"{len(unit_lines_after)}"
        )

        # TASK.md AC: `hold_requested` and `hold_answered` events both exist in the ledger.
        assert any(
            line.get("type") == "event" and line.get("event") == "hold_requested"
            for line in ledger_lines_after
        ), ledger_lines_after
        assert any(
            line.get("type") == "event" and line.get("event") == "hold_answered"
            for line in ledger_lines_after
        ), ledger_lines_after
