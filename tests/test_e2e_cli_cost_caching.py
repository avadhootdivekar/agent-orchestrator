"""CliRunner e2e test for E-1cecSx (Epic B, cost & caching optimization).

Outermost-boundary coverage (CLAUDE.md's e2e rule): drives `ao run`, `ao report-timing`, and
`ao report-outcomes --grade` through `typer.testing.CliRunner` -- never calling `Orchestrator`/
`outcomes.grade_run`/`reporting.top_n_slowest_tasks` directly. Mirrors
`tests/test_e2e_cli_isolation.py`'s own `_write_specs`/`_env` pattern.

Exercises, in one real run:
- B2.1 (`ao report-timing`): a real multi-task run's timing report is non-empty and sensible.
- B2.2 (`ao report-timing --task`): the graceful "no transcript" path for a real `fake`-executor
  task (which never writes one), then the full activity-breakdown pipeline wired end-to-end by
  placing the committed fixture transcript at that task's real `output_artifact_path`.
- B3.1 (`ao report-outcomes`): the local outcome-count report is non-empty.
- B3.2 (`post_hook`, Epic A's existing mechanism): a dispatched task graded at dispatch time.
- B3.3 (`ao report-outcomes --grade`, ADR-0015 decision 2): the SAME run's post-run grading
  pass covers BOTH the freshly-dispatched task AND a `skip_if_outputs_exist`-skipped task
  (pre-seeded outputs before the run), with zero per-task `settlement_hook` wiring -- the whole
  point of the post-run-pass design over the rejected in-engine alternative.

B1 (`--exclude-dynamic-system-prompt-sections` argv injection) is deliberately NOT exercised
here: the `fake` executor never builds `claude` CLI argv at all, so a `fake`-executor e2e run
cannot demonstrate it. B1's own e2e-equivalent proof is the argv-construction unit tests in
`tests/test_executor.py` (`TestClaudeCliExecutorExcludeDynamicSectionsWiring`) -- see
`docs-md/cost-caching-optimization-hld.md` §6 for why that is the right boundary for this fix
(a live Claude CLI cache assertion is out of reach for CI regardless of executor).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.executors.claude_cli import TRANSCRIPT_FILE

runner = CliRunner()

_GRADE_HOOK_SCRIPT = (
    Path(__file__).resolve().parents[1] / "specs" / "examples" / "hooks" / "grade.py"
)


def _write_specs(tmp_path: Path) -> None:
    (tmp_path / "specs" / "instructions").mkdir(parents=True, exist_ok=True)
    for name in ("dispatched_task", "skip_task"):
        (tmp_path / "specs" / "instructions" / f"{name}.md").write_text(f"# {name}\n")

    # Pre-seed skip_task's declared output BEFORE the run, so `skip_if_outputs_exist` takes
    # the skip path on this very first run (not just on a resume) -- the exact scenario B3.3
    # exists to cover.
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "output" / "skip.txt").write_text("pre-existing output\n")

    workflow = {
        "version": "1.0",
        "id": "cost-caching-e2e-wf",
        "repo_set": "rs",
        "hooks": {
            "grade": {
                "type": "command",
                "command": ["python3", str(_GRADE_HOOK_SCRIPT)],
                "timeout_seconds": 30,
            }
        },
        "tasks": [
            {
                "id": "dispatched_task",
                "agent": "ag",
                "instruction": "specs/instructions/dispatched_task.md",
                "outputs": ["output/dispatched.txt"],
                "post_hook": {"use": "grade", "on_failure": "ignore"},
            },
            {
                "id": "skip_task",
                "agent": "ag",
                "instruction": "specs/instructions/skip_task.md",
                "outputs": ["output/skip.txt"],
                "skip_if_outputs_exist": True,
            },
        ],
    }
    (tmp_path / "workflow.json").write_text(json.dumps(workflow))

    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    reposets = {
        "version": "1.0",
        "repo_sets": {
            "rs": {
                "workspace_root": str(tmp_path),
                "repos": [{"id": "core", "path": "repo", "role": "primary"}],
            }
        },
    }
    (tmp_path / "reposets.json").write_text(json.dumps(reposets))

    agents = {"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
    (tmp_path / "agents.json").write_text(json.dumps(agents))


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "AO_WORKSPACE_ROOT": str(tmp_path),
        "HOME": str(tmp_path / "home"),
        "AO_STATE_DIR": str(tmp_path / "ao-state"),
    }


def _spec_args(tmp_path: Path) -> list[str]:
    return [
        "--workflow",
        str(tmp_path / "workflow.json"),
        "--reposets",
        str(tmp_path / "reposets.json"),
        "--agents",
        str(tmp_path / "agents.json"),
    ]


class TestCostCachingE2E:
    def test_run_then_report_timing_and_graded_outcomes(self, tmp_path: Path) -> None:
        assert _GRADE_HOOK_SCRIPT.exists(), "Epic A's example grading hook script must exist"
        _write_specs(tmp_path)

        result = runner.invoke(app, ["run", *_spec_args(tmp_path)], env=_env(tmp_path))
        assert result.exit_code == 0, f"ao run failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

        # dispatched_task's own output was written by FakeExecutor; skip_task's was
        # pre-seeded and never touched again (skip path genuinely taken).
        assert (tmp_path / "output" / "dispatched.txt").exists()
        assert (tmp_path / "output" / "skip.txt").read_text() == "pre-existing output\n"

        run_dir = next((tmp_path / ".orchestrator" / "runs").iterdir())
        run_id = run_dir.name
        state = json.loads((run_dir / "state.json").read_text())
        assert state["tasks"]["dispatched_task"]["status"] == "succeeded"
        assert state["tasks"]["skip_task"]["status"] == "skipped"

        # B3.2 (Epic A's existing post_hook mechanism, unchanged): the dispatch-scoped grade
        # is ALREADY recorded on the dispatched task's own TaskRunState -- fired inside
        # _run_with_retries during `ao run` itself, no extra command needed.
        post_hook_result = state["tasks"]["dispatched_task"]["post_hook_result"]
        assert post_hook_result is not None
        assert post_hook_result["status"] == "passed"
        assert post_hook_result["kind"] == "post_hook"
        # skip_task never dispatched, so it has no post_hook_result -- post_hook is
        # dispatch-scoped only; this is EXACTLY the coverage gap B3.3 exists to close below.
        assert state["tasks"]["skip_task"]["post_hook_result"] is None

        # --- B2: ao report-timing ---
        timing_result = runner.invoke(
            app,
            ["report-timing", "--run-id", run_id, *_spec_args(tmp_path)],
            env=_env(tmp_path),
        )
        assert timing_result.exit_code == 0, f"ao report-timing failed:\n{timing_result.output}"
        assert "dispatched_task" in timing_result.output
        assert "Top 10 slowest tasks" in timing_result.output

        # --- B2.2: ao report-timing --task (within-task activity breakdown) ---
        # FakeExecutor sets a real `output_artifact_path` (ctx.output_dir) and writes a
        # capture-parity transcript.jsonl stub (FR-4/FR-5), but -- unlike a real `claude` CLI
        # capture -- that stub's events carry no top-level `timestamp` field. This first
        # invocation exercises the CLI's/`task_activity_breakdown`'s graceful zero-timestamped-
        # events path against that REAL (stub) file, not a synthetic one.
        no_timestamps_result = runner.invoke(
            app,
            [
                "report-timing",
                "--run-id",
                run_id,
                "--task",
                "dispatched_task",
                *_spec_args(tmp_path),
            ],
            env=_env(tmp_path),
        )
        assert no_timestamps_result.exit_code == 0, (
            f"ao report-timing --task failed:\n{no_timestamps_result.output}"
        )
        assert "no timestamped events found in transcript" in no_timestamps_result.output.lower()

        # Now wire the full activity-breakdown pipeline end-to-end: place the committed,
        # redacted fixture transcript (tests/fixtures/transcript_activity_breakdown.jsonl) at
        # dispatched_task's REAL output_artifact_path, exactly where a genuine `claude_cli`
        # dispatch would have written its own transcript.jsonl.
        dispatched_output_dir = Path(state["tasks"]["dispatched_task"]["output_artifact_path"])
        fixture_text = (
            Path(__file__).parent / "fixtures" / "transcript_activity_breakdown.jsonl"
        ).read_text()
        (dispatched_output_dir / TRANSCRIPT_FILE).write_text(fixture_text)

        breakdown_result = runner.invoke(
            app,
            [
                "report-timing",
                "--run-id",
                run_id,
                "--task",
                "dispatched_task",
                *_spec_args(tmp_path),
            ],
            env=_env(tmp_path),
        )
        assert breakdown_result.exit_code == 0, (
            f"ao report-timing --task failed:\n{breakdown_result.output}"
        )
        assert "Activity breakdown for task 'dispatched_task'" in breakdown_result.output
        assert "build-or-test" in breakdown_result.output
        assert "file-edit" in breakdown_result.output
        assert "search-or-read" in breakdown_result.output

        # skip_task never dispatched (skip_if_outputs_exist took the skip path) -- it has no
        # output_artifact_path at all, exercising the OTHER graceful missing-transcript case.
        assert state["tasks"]["skip_task"]["output_artifact_path"] is None
        skip_task_result = runner.invoke(
            app,
            ["report-timing", "--run-id", run_id, "--task", "skip_task", *_spec_args(tmp_path)],
            env=_env(tmp_path),
        )
        assert skip_task_result.exit_code == 0, (
            f"ao report-timing --task failed:\n{skip_task_result.output}"
        )
        assert "no output_artifact_path recorded" in skip_task_result.output.lower()

        # --- B3.1: ao report-outcomes (no --grade) ---
        outcomes_result = runner.invoke(
            app,
            ["report-outcomes", "--run-id", run_id, *_spec_args(tmp_path)],
            env=_env(tmp_path),
        )
        assert outcomes_result.exit_code == 0, (
            f"ao report-outcomes failed:\n{outcomes_result.output}"
        )
        assert "dispatched_task" in outcomes_result.output
        assert "skip_task" in outcomes_result.output

        # --- B3.2/B3.3: ao report-outcomes --grade grade (ADR-0015 decision 2) ---
        # This is the load-bearing assertion for the whole epic's B3 workstream: ONE post-run
        # pass, with NO per-task settlement_hook wiring anywhere in the spec, grades BOTH the
        # dispatched task (which also separately got a dispatch-scoped grade above) AND the
        # skipped one (which never went through _run_with_retries at all).
        grade_result = runner.invoke(
            app,
            ["report-outcomes", "--run-id", run_id, "--grade", "grade", *_spec_args(tmp_path)],
            env=_env(tmp_path),
        )
        assert grade_result.exit_code == 0, (
            f"ao report-outcomes --grade failed:\n{grade_result.output}"
        )
        assert "Settlement grades" in grade_result.output
        assert "dispatched_task" in grade_result.output
        assert "skip_task" in grade_result.output
        assert "dispatched" in grade_result.output  # settle_reason column
        assert "skipped" in grade_result.output  # settle_reason column

        # The written report artifact itself carries both tasks, graded.
        report_path = tmp_path / ".orchestrator" / "runs" / run_id / "settlement_grades.json"
        assert report_path.exists()
        grades = json.loads(report_path.read_text())
        by_id = {g["task_id"]: g for g in grades}
        assert set(by_id) == {"dispatched_task", "skip_task"}
        assert by_id["dispatched_task"]["settle_reason"] == "dispatched"
        assert by_id["skip_task"]["settle_reason"] == "skipped"
        assert by_id["dispatched_task"]["outcome"]["status"] == "passed"
        assert by_id["skip_task"]["outcome"]["status"] == "passed"
        assert by_id["skip_task"]["outcome"]["kind"] == "settlement_hook"
