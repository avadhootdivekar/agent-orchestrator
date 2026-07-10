"""Tier 2 — Deterministic/reproducible e2e tests for sum-of-array workflow.

Tests entire workflow via the `ao` CLI with FakeExecutor and pre-seeded control files.
Covers all six areas of functionality via CliRunner + deterministic assertions.

Never asserts LLM-generated content — only paths, structures, order, counts.
Always green in CI with zero token burn (FakeExecutor spawns no subprocess).

Covers requirements: FR-4, FR-7 (all six areas), NFR-2, NFR-3, NFR-4. Key design facts 1–5.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.models import EstimatorConfig
from tests.playground.harness import (
    agents_for,
    assert_tree,
    copy_example,
    load_expected,
    run_cli,
    seed_control_files,
)

_runner = CliRunner()

# Spine output paths that should exist after a successful run
SPINE_OUTPUTS = [
    "output/design.md",
    "output/design-review.md",
    "output/integrated.md",
    "output/bugfix.md",
    "output/final-review.md",
    "output/summary.md",
]

# Regex to extract run_id from CLI output
RUN_ID_REGEX = re.compile(r"Run:\s+(\S+)")


def _extract_run_id(cli_output: str) -> str:
    """Extract run_id from CLI stdout using Run: <run_id> pattern."""
    match = RUN_ID_REGEX.search(cli_output)
    if not match:
        raise ValueError(f"Could not extract run_id from CLI output:\n{cli_output}")
    return match.group(1)


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSON Lines file and return list of dicts."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_state(tmp_path: Path, run_id: str) -> dict:
    """Read state.json for a given run."""
    state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
    return json.loads(state_path.read_text())


def _read_run_log(tmp_path: Path, run_id: str) -> list[dict]:
    """Read run.log as a list of JSON objects."""
    run_log_path = tmp_path / ".orchestrator" / "runs" / run_id / "run.log"
    return _read_jsonl(run_log_path)


def _recompute_token_estimate(tmp_path: Path) -> int:
    """Recompute the expected token consumption using HeuristicTokenEstimator.

    This matches the CLI's estimator behavior for deterministic assertion.
    """
    store = LocalFsArtifactStore(str(tmp_path))
    cfg = EstimatorConfig()  # Defaults: chars_per_token=4, output_allowance=1000, buffer=1.3

    # Task paths and their instruction/input sources (from the workflow + manifest)
    # This must match the actual executed tasks in the workflow
    tasks_info = [
        # Spine tasks
        (
            "architect-design",
            "instructions/architect-design.md",
            [],
        ),
        (
            "design-review",
            "instructions/design-review.md",
            ["output/design.md"],
        ),
        (
            "architect-breakdown",
            "instructions/architect-breakdown.md",
            ["output/design.md", "output/design-review.md"],
        ),
        # Injected tasks (from manifest)
        (
            "impl-t1",
            "instructions/developer.md",
            ["output/design.md"],
        ),
        (
            "testwrite-t1",
            "instructions/test-writer.md",
            ["output/tasks/t1/solution.py"],
        ),
        (
            "taskreview-t1",
            "instructions/reviewer.md",
            ["output/tasks/t1/solution.py", "output/tasks/t1/test_solution.py"],
        ),
        # Post-injection spine
        (
            "integrate",
            "instructions/integrate.md",
            ["output/tasks/t1/review.md"],
        ),
        (
            "bugfix",
            "instructions/bugfix.md",
            ["output/integrated.md"],
        ),
        (
            "final-review",
            "instructions/final-review.md",
            ["output/bugfix.md"],
        ),
        (
            "done",
            "instructions/done.md",
            ["output/final-review.md"],
        ),
    ]

    total_estimate = 0
    for task_id, instr_path, inputs in tasks_info:
        # Task estimate: (instr_bytes + input_bytes) / chars_per_token +
        # output_allowance_tokens, then apply pessimism_buffer.
        instr_bytes = store.size(instr_path)
        input_bytes = sum(store.size(inp) for inp in inputs)
        raw = (instr_bytes + input_bytes) / cfg.chars_per_token + cfg.output_allowance_tokens
        estimate = math.ceil(raw * cfg.pessimism_buffer)
        total_estimate += estimate

    return total_estimate


class TestArea1DAGAndOutputs:
    """Area 1 — Workflow/DAG/dependencies."""

    def test_one_round_run_succeeds_and_outputs_exist(self, tmp_path: Path) -> None:
        """Given the 1-round workflow, when run with FakeExecutor, then exit 0 and outputs exist."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        run_id = _extract_run_id(result.output)
        state = _read_state(tmp_path, run_id)

        assert state["status"] == "succeeded"
        assert_tree(tmp_path, load_expected("sum-of-array")["output_artifacts"])

    def test_task_start_order_matches_expected(self, tmp_path: Path) -> None:
        """Given a 1-round run, when run.log is read, then task.start order matches expected."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log = _read_run_log(tmp_path, run_id)

        # Extract task.start events in order
        task_starts = [e for e in run_log if e.get("event") == "task.start"]
        actual_order = [e["task_id"] for e in task_starts]

        expected_order = load_expected("sum-of-array")["expected_events"]["one_round"][
            "task_start_order"
        ]
        assert actual_order == expected_order, (
            f"Task order mismatch.\nExpected: {expected_order}\nActual: {actual_order}"
        )

    def test_cyclic_workflow_fails_with_error(self, tmp_path: Path) -> None:
        """Given a cyclic workflow, when run, then nonzero exit + 'cycle' in output."""
        # Copy the example first to get the structure
        copy_example("sum-of-array", tmp_path)

        # Overwrite workflow.json with a cyclic version
        cyclic_workflow = {
            "version": "1.0",
            "id": "cyclic-test",
            "repo_set": "sum-set",
            "triggers": [{"type": "manual"}],
            "tasks": [
                {
                    "id": "a",
                    "agent": "architect",
                    "instruction": "instructions/architect-design.md",
                    "outputs": ["output/a.md"],
                    "depends_on": ["b"],
                },
                {
                    "id": "b",
                    "agent": "developer",
                    "instruction": "instructions/developer.md",
                    "inputs": ["output/a.md"],
                    "outputs": ["output/b.md"],
                    "depends_on": ["a"],
                },
            ],
        }
        (tmp_path / "workflow.json").write_text(json.dumps(cyclic_workflow))

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code != 0, "Cyclic workflow should fail"
        assert "cycle" in result.output.lower() or "circular" in result.output.lower(), (
            f"Expected 'cycle' or 'circular' in error output, got:\n{result.output}"
        )


class TestArea2TokenComputation:
    """Area 2 — Token computation/assumptions."""

    def test_budget_consumed_tokens_matches_estimate(self, tmp_path: Path) -> None:
        """Given --budget-total + --pessimism-buffer, then consumed_tokens == estimate."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
                "--budget-total",
                "100000000",
                "--pessimism-buffer",
                "1.3",
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        state = _read_state(tmp_path, run_id)

        # Check consumed tokens
        consumed = state["budget_counters"]["consumed_tokens"]
        expected = _recompute_token_estimate(tmp_path)
        assert consumed == expected, f"Token count mismatch. Expected: {expected}, Got: {consumed}"

    def test_budget_charge_and_reconcile_events_present(self, tmp_path: Path) -> None:
        """When run.log is read, then budget.charge and budget.reconcile events present."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
                "--budget-total",
                "100000000",
                "--pessimism-buffer",
                "1.3",
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log = _read_run_log(tmp_path, run_id)

        events = [e.get("event") for e in run_log]
        charge_count = sum(1 for e in events if e == "budget.charge")
        reconcile_count = sum(1 for e in events if e == "budget.reconcile")

        # 1-round: 10 tasks executed
        assert charge_count == 10, f"Expected 10 budget.charge events, got {charge_count}"
        assert reconcile_count == 10, f"Expected 10 budget.reconcile events, got {reconcile_count}"


class TestArea3DynamicInjectionAndLoop:
    """Area 3 — Dynamic inputs/dependency spec."""

    def test_injected_tasks_have_correct_origin(self, tmp_path: Path) -> None:
        """When state.json is read, then injected tasks have origin='injected'."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        state = _read_state(tmp_path, run_id)

        # Check injected task origins
        injected_ids = {"impl-t1", "testwrite-t1", "taskreview-t1"}
        for task_id in injected_ids:
            assert task_id in state["tasks"], f"Task {task_id} not in state"
            origin = state["tasks"][task_id]["origin"]
            assert origin == "injected", (
                f"Task {task_id} should have origin='injected', got {origin}"
            )

    def test_two_round_loop_creates_iter2_tasks(self, tmp_path: Path) -> None:
        """When state.json is read, then __iter2 tasks have origin='loop'."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=2)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        state = _read_state(tmp_path, run_id)

        # Check loop-cloned tasks (iter 2)
        loop_ids = {"bugfix__iter2", "final-review__iter2"}
        for task_id in loop_ids:
            assert task_id in state["tasks"], f"Task {task_id} not in state"
            assert state["tasks"][task_id]["origin"] == "loop", (
                f"Task {task_id} should have origin='loop'"
            )

    def test_two_round_task_order(self, tmp_path: Path) -> None:
        """Given a 2-round run, when run.log is read, then task order includes __iter2 tasks."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=2)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log = _read_run_log(tmp_path, run_id)

        task_starts = [e for e in run_log if e.get("event") == "task.start"]
        actual_order = [e["task_id"] for e in task_starts]

        expected_order = load_expected("sum-of-array")["expected_events"]["two_round"][
            "task_start_order"
        ]
        assert actual_order == expected_order, (
            f"Two-round task order mismatch.\nExpected: {expected_order}\nActual: {actual_order}"
        )


class TestArea4Logging:
    """Area 4 — Logging."""

    def test_run_log_exists(self, tmp_path: Path) -> None:
        """When run completes, then run.log exists under .orchestrator/runs/<run_id>/."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log_path = tmp_path / ".orchestrator" / "runs" / run_id / "run.log"
        assert run_log_path.exists(), f"Missing run.log at {run_log_path}"

    def test_run_log_all_lines_valid_json(self, tmp_path: Path) -> None:
        """Given run.log, when read, then every non-empty line is valid JSON."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log_path = tmp_path / ".orchestrator" / "runs" / run_id / "run.log"
        lines = run_log_path.read_text().splitlines()

        for i, line in enumerate(lines):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    pytest.fail(f"Line {i} in run.log is not valid JSON: {line}")

    def test_run_log_has_required_fields(self, tmp_path: Path) -> None:
        """Given run.log entries, when checked, then all have ts/level/logger/msg."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log = _read_run_log(tmp_path, run_id)

        for entry in run_log:
            assert "ts" in entry, f"Missing 'ts' in log entry: {entry}"
            assert "level" in entry, f"Missing 'level' in log entry: {entry}"
            assert "logger" in entry, f"Missing 'logger' in log entry: {entry}"
            assert "msg" in entry, f"Missing 'msg' in log entry: {entry}"

    def test_run_log_event_set_includes_required_events(self, tmp_path: Path) -> None:
        """Given run.log, when events are collected, then required events present."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log = _read_run_log(tmp_path, run_id)

        events = {e.get("event") for e in run_log}
        expected_events = set(
            load_expected("sum-of-array")["expected_events"]["one_round"]["event_types"]
        )

        missing = expected_events - events
        assert not missing, f"Missing expected events: {missing}"

    def test_task_events_have_task_id(self, tmp_path: Path) -> None:
        """Given task-related log entries, when checked, then they carry task_id."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        run_log = _read_run_log(tmp_path, run_id)

        task_events = [
            e for e in run_log if e.get("event") in ("task.start", "task.end", "task.injected")
        ]
        for entry in task_events:
            assert "task_id" in entry, f"Task event {entry.get('event')} missing task_id: {entry}"


class TestArea5OutputCapture:
    """Area 5 — Per-task output capture."""

    def test_stdout_stderr_exist_for_all_tasks(self, tmp_path: Path) -> None:
        """When run completes, stdout.txt+stderr.txt exist for all executed tasks."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)

        # Expected tasks for 1-round: 10 spine + injected
        expected_tasks = [
            "architect-design",
            "design-review",
            "architect-breakdown",
            "impl-t1",
            "testwrite-t1",
            "taskreview-t1",
            "integrate",
            "bugfix",
            "final-review",
            "done",
        ]

        for task_id in expected_tasks:
            # attempt-1: every task here succeeds on its first attempt (E-9h3m7k —
            # capture dirs are now attempt-suffixed so retries don't clobber each other).
            task_dir = tmp_path / ".orchestrator" / "runs" / run_id / task_id / "attempt-1"
            stdout = task_dir / "stdout.txt"
            stderr = task_dir / "stderr.txt"
            assert stdout.exists(), f"Missing stdout.txt for {task_id}"
            assert stderr.exists(), f"Missing stderr.txt for {task_id}"

    def test_transcript_and_result_captured_for_all_tasks(self, tmp_path: Path) -> None:
        """E2E via the CLI: transcript.jsonl (all turns) + result.json exist per task."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)

        expected_tasks = [
            "architect-design",
            "design-review",
            "architect-breakdown",
            "impl-t1",
            "testwrite-t1",
            "taskreview-t1",
            "integrate",
            "bugfix",
            "final-review",
            "done",
        ]

        for task_id in expected_tasks:
            task_dir = tmp_path / ".orchestrator" / "runs" / run_id / task_id / "attempt-1"
            transcript = task_dir / "transcript.jsonl"
            assert transcript.exists(), f"Missing transcript.jsonl for {task_id}"
            events = [
                json.loads(line) for line in transcript.read_text().splitlines() if line.strip()
            ]
            assert len(events) > 1, f"transcript.jsonl for {task_id} lost intermediate turns"
            assert (task_dir / "result.json").exists(), f"Missing result.json for {task_id}"

    def test_state_json_has_output_artifact_paths(self, tmp_path: Path) -> None:
        """Given state.json, when read, then each task has output_artifact_path set."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)
        state = _read_state(tmp_path, run_id)

        expected_tasks = [
            "architect-design",
            "design-review",
            "architect-breakdown",
            "impl-t1",
            "testwrite-t1",
            "taskreview-t1",
            "integrate",
            "bugfix",
            "final-review",
            "done",
        ]

        for task_id in expected_tasks:
            task_state = state["tasks"][task_id]
            assert "output_artifact_path" in task_state, (
                f"Task {task_id} missing output_artifact_path"
            )


class TestArea6CLIFlagsAndCommands:
    """Area 6 — CLI flags and commands."""

    def test_validate_good_specs_succeeds(self, tmp_path: Path) -> None:
        """Given valid specs, when ao validate runs, then exit 0 + 'OK' in output."""
        copy_example("sum-of-array", tmp_path)

        result = run_cli(
            [
                "validate",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        assert "OK" in result.output or "valid" in result.output.lower()

    def test_validate_malformed_workflow_fails(self, tmp_path: Path) -> None:
        """Given malformed workflow, when ao validate runs, then nonzero + 'ERROR' in output."""
        copy_example("sum-of-array", tmp_path)

        # Overwrite with invalid JSON
        (tmp_path / "workflow.json").write_text("{invalid json")

        result = run_cli(
            [
                "validate",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code != 0
        assert "ERROR" in result.output or "error" in result.output.lower()

    def test_invalid_rate_window_flag_fails(self, tmp_path: Path) -> None:
        """When --rate-window is invalid (without --rate-tokens), nonzero + 'ERROR'."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
                "--rate-window",
                "invalid-window",
            ],
            tmp_path,
        )

        assert result.exit_code != 0, "Invalid rate-window should fail"
        assert "ERROR" in result.output or "error" in result.output.lower()

    def test_status_command_prints_table(self, tmp_path: Path) -> None:
        """Given a completed run, when ao status runs, then it prints a task table."""
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path, rounds=1)

        # First, run the workflow
        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
            ],
            tmp_path,
        )

        assert result.exit_code == 0
        run_id = _extract_run_id(result.output)

        # Now run status command (use str(tmp_path) for workspace)
        runner_result = _runner.invoke(
            app,
            [
                "status",
                "--workspace",
                str(tmp_path),
                "--run-id",
                run_id,
            ],
        )

        assert runner_result.exit_code == 0, f"Status command failed:\n{runner_result.output}"
        # Should contain task ids from the workflow
        assert "architect-design" in runner_result.output


class TestDeterminism:
    """Determinism: running twice yields identical asserted values."""

    def test_same_run_twice_yields_same_results(self, tmp_path: Path) -> None:
        """Given two identical runs, when assertions are checked, then they're identical."""
        # First run
        copy_example("sum-of-array", tmp_path / "run1")
        seed_control_files(tmp_path / "run1", rounds=1)

        result1 = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
                "--budget-total",
                "100000000",
                "--pessimism-buffer",
                "1.3",
            ],
            tmp_path / "run1",
        )

        assert result1.exit_code == 0
        run_id1 = _extract_run_id(result1.output)
        state1 = _read_state(tmp_path / "run1", run_id1)
        run_log1 = _read_run_log(tmp_path / "run1", run_id1)

        # Second run (in a different workspace)
        copy_example("sum-of-array", tmp_path / "run2")
        seed_control_files(tmp_path / "run2", rounds=1)

        result2 = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("fake"),
                "--budget-total",
                "100000000",
                "--pessimism-buffer",
                "1.3",
            ],
            tmp_path / "run2",
        )

        assert result2.exit_code == 0
        run_id2 = _extract_run_id(result2.output)
        state2 = _read_state(tmp_path / "run2", run_id2)
        run_log2 = _read_run_log(tmp_path / "run2", run_id2)

        # Extract task.start order from both runs
        order1 = [e["task_id"] for e in run_log1 if e.get("event") == "task.start"]
        order2 = [e["task_id"] for e in run_log2 if e.get("event") == "task.start"]
        assert order1 == order2, "Task orders differ between runs"

        # Compare consumed tokens
        tokens1 = state1["budget_counters"]["consumed_tokens"]
        tokens2 = state2["budget_counters"]["consumed_tokens"]
        assert tokens1 == tokens2, f"Token consumption differs: {tokens1} vs {tokens2}"

        # Compare status
        assert state1["status"] == state2["status"], "Status differs between runs"
