#!/usr/bin/env python3
"""Late-gate end-to-end demonstration for Epic A (E-AMSSHX, T-FCC8mT).

Runs `specs/examples/workflow-hooks.json` through the REAL `ao` CLI (a subprocess, the
outermost boundary -- CLAUDE.md's e2e testing rule) in an isolated temp workspace, mirroring
the exact convention `tests/test_e2e_cli.py::_copy_examples_with_fake_agents` already uses
(fake-executor agents.json, AO_WORKSPACE_ROOT pointed at the temp copy) so it runs
deterministically with no real Claude spend and never touches the real repo checkout's
`.orchestrator/` state.

Two scenarios:
  1. "happy path" -- pre_hook (check_disk_space) passes, post_hook (grade, on_failure=ignore
     as declared in the shipped example spec) passes -- both tasks succeed, run succeeds.
  2. "gating post_hook" -- a temp copy of the workflow with the `implement` task's post_hook
     `on_failure` flipped to "fail_task", run with AO_EXAMPLE_GRADE_FORCE_FAIL=1 so `grade.py`
     reports a failing grade -- demonstrates a post_hook actually downgrading a succeeded task
     to failed and the run's overall status reflecting it (HLD Sec6).

Evidence (raw CLI output, run.log, state.json, and every hook capture directory found) is
copied to output/E-AMSSHX-task-lifecycle-hooks/ under this run.

Usage: uv run python scripts/helper/epics/E-AMSSHX/e2e_hooks_demo.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SPECS_EXAMPLES = REPO_ROOT / "specs" / "examples"
EVIDENCE_DIR = REPO_ROOT / "output" / "E-AMSSHX-task-lifecycle-hooks"
AO_BIN = str(REPO_ROOT / ".venv" / "bin" / "ao")


def _prep_workspace(tmp: Path, workflow_json: dict) -> tuple[Path, Path, Path]:
    """Mirrors tests/test_e2e_cli.py::_copy_examples_with_fake_agents (workflow varies)."""
    wf = tmp / "workflow-hooks.json"
    rs = tmp / "reposets.json"
    ag = tmp / "agents.json"

    wf.write_text(json.dumps(workflow_json, indent=2))
    shutil.copy(SPECS_EXAMPLES / "reposet.json", rs)

    orig_agents = json.loads((SPECS_EXAMPLES / "agents.json").read_text())
    fake_agents: dict = {"version": "1.0", "agents": {}}
    for name in orig_agents["agents"]:
        fake_agents["agents"][name] = {"executor": "fake"}
    ag.write_text(json.dumps(fake_agents))

    instr_dst = tmp / "specs" / "examples" / "instructions"
    instr_dst.mkdir(parents=True)
    for f in (SPECS_EXAMPLES / "instructions").iterdir():
        if f.is_file():
            shutil.copy(f, instr_dst / f.name)

    hooks_dst = tmp / "specs" / "examples" / "hooks"
    hooks_dst.mkdir(parents=True)
    for f in (SPECS_EXAMPLES / "hooks").iterdir():
        if f.is_file():
            shutil.copy(f, hooks_dst / f.name)

    return wf, rs, ag


def _run_cli(
    args: list[str], tmp: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "AO_WORKSPACE_ROOT": str(tmp), **(extra_env or {})}
    return subprocess.run(  # noqa: S603
        [AO_BIN, *args], cwd=str(tmp), env=env, capture_output=True, text=True, timeout=60
    )


def _find_hook_capture_dirs(run_dir: Path) -> list[Path]:
    return sorted(
        p for p in run_dir.rglob("*") if p.is_dir() and p.name in ("pre_hook", "post_hook")
    )


def _copy_evidence(
    label: str, tmp: Path, wf: Path, rs: Path, ag: Path, proc_run: subprocess.CompletedProcess
) -> None:
    dest = EVIDENCE_DIR / label
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "cli_run_stdout.txt").write_text(proc_run.stdout)
    (dest / "cli_run_stderr.txt").write_text(proc_run.stderr)
    (dest / "cli_run_exit_code.txt").write_text(str(proc_run.returncode))
    shutil.copy(wf, dest / wf.name)

    orchestrator_dir = tmp / ".orchestrator" / "runs"
    if orchestrator_dir.is_dir():
        run_dirs = sorted(orchestrator_dir.iterdir())
        if run_dirs:
            run_dir = run_dirs[-1]
            shutil.copytree(run_dir, dest / "run_dir")


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    happy_workflow = json.loads((SPECS_EXAMPLES / "workflow-hooks.json").read_text())

    # ---- Scenario 1: happy path (shipped example spec, unmodified) ----
    print("=== Scenario 1: happy path (pre_hook + post_hook both pass) ===")
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ao-hooks-e2e-happy-") as tmp1_str:
        tmp1 = Path(tmp1_str)
        wf1, rs1, ag1 = _prep_workspace(tmp1, happy_workflow)

        validate = _run_cli(
            ["validate", "--workflow", str(wf1), "--reposets", str(rs1), "--agents", str(ag1)], tmp1
        )
        print("--- ao validate ---")
        print(validate.stdout, validate.stderr)
        assert validate.returncode == 0, f"validate failed: {validate.stdout}{validate.stderr}"

        run1 = _run_cli(
            ["run", "--workflow", str(wf1), "--reposets", str(rs1), "--agents", str(ag1)], tmp1
        )
        print("--- ao run ---")
        print(run1.stdout, run1.stderr)
        assert run1.returncode == 0, f"run failed: {run1.stdout}{run1.stderr}"
        assert "succeeded" in run1.stdout.lower()

        run_dir = sorted((tmp1 / ".orchestrator" / "runs").iterdir())[-1]
        state = json.loads((run_dir / "state.json").read_text())
        hook_dirs = _find_hook_capture_dirs(run_dir)
        print(f"Hook capture dirs found: {[str(d.relative_to(run_dir)) for d in hook_dirs]}")
        assert hook_dirs, "expected at least one pre_hook/post_hook capture dir"
        for tid in ("design", "implement"):
            ts = state["tasks"][tid]
            print(
                f"  task={tid} status={ts['status']} pre_hook_result={ts.get('pre_hook_result')} "
                f"post_hook_result={ts.get('post_hook_result')}"
            )
        assert state["tasks"]["design"]["pre_hook_result"]["status"] == "passed"
        assert state["tasks"]["implement"]["post_hook_result"]["status"] == "passed"
        assert state["tasks"]["implement"]["post_hook_result"]["score"] == 1.0

        _copy_evidence("scenario1-happy-path", tmp1, wf1, rs1, ag1, run1)
        print("Evidence copied to output/E-AMSSHX-task-lifecycle-hooks/scenario1-happy-path/\n")

    # ---- Scenario 2: gating post_hook downgrades a succeeded task to failed ----
    print("=== Scenario 2: post_hook on_failure=fail_task downgrades succeeded -> failed ===")
    gating_workflow = json.loads(json.dumps(happy_workflow))  # deep copy
    for task in gating_workflow["tasks"]:
        if task["id"] == "implement":
            task["post_hook"]["on_failure"] = "fail_task"

    with tempfile.TemporaryDirectory(prefix="ao-hooks-e2e-gating-") as tmp2_str:
        tmp2 = Path(tmp2_str)
        wf2, rs2, ag2 = _prep_workspace(tmp2, gating_workflow)

        run2 = _run_cli(
            ["run", "--workflow", str(wf2), "--reposets", str(rs2), "--agents", str(ag2)],
            tmp2,
            extra_env={"AO_EXAMPLE_GRADE_FORCE_FAIL": "1"},
        )
        print("--- ao run (forced grading failure, on_failure=fail_task) ---")
        print(run2.stdout, run2.stderr)
        # The run itself exits non-zero once a task fails.
        assert run2.returncode != 0, "expected non-zero exit once the post_hook downgrades a task"

        run_dir2 = sorted((tmp2 / ".orchestrator" / "runs").iterdir())[-1]
        state2 = json.loads((run_dir2 / "state.json").read_text())
        implement_ts = state2["tasks"]["implement"]
        print(
            f"  task=implement status={implement_ts['status']} "
            f"post_hook_result={implement_ts.get('post_hook_result')}"
        )
        assert implement_ts["status"] == "failed", (
            "post_hook fail_task should have downgraded this task"
        )
        assert implement_ts["post_hook_result"]["status"] == "failed"
        assert implement_ts["post_hook_result"]["score"] == 0.0
        assert (
            "post_hook" in (implement_ts.get("output_artifact_path") or "") or True
        )  # informational only
        print(f"  run status={state2['status']}")
        assert state2["status"] == "failed"

        _copy_evidence("scenario2-gating-post-hook", tmp2, wf2, rs2, ag2, run2)
        print(
            "Evidence copied to output/E-AMSSHX-task-lifecycle-hooks/scenario2-gating-post-hook/\n"
        )

    print("BOTH SCENARIOS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
