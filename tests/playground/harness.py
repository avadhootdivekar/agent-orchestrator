"""Shared test harness for the playground e2e test suite.

Provides:
  - copy_example  — isolate a playground example into tmp_path
  - run_cli       — invoke the `ao` CLI via CliRunner with AO_WORKSPACE_ROOT set
  - agents_for    — return the agents filename for a given tier ("fake" | "real")
  - seed_control_files — pre-seed manifest + gate verdict fixtures before ao run
  - discover_examples  — sorted list of example names under playground/
  - expanded_workflow  — spine + manifest tasks merged (for DAG acyclicity checks)
  - requires_claude    — skip (not fail) when the `claude` binary is absent

Usage::

    from tests.playground.harness import copy_example, run_cli, agents_for, seed_control_files

    def test_something(tmp_path):
        copy_example("sum-of-array", tmp_path)
        seed_control_files(tmp_path)
        result = run_cli(
            ["run",
             "--workflow", str(tmp_path / "workflow.json"),
             "--reposets", str(tmp_path / "reposet.json"),
             "--agents",   str(tmp_path / agents_for("fake"))],
            tmp_path,
        )
        assert result.exit_code == 0
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app

# Repo root — three levels up from tests/playground/harness.py
REPO_ROOT: Path = Path(__file__).parent.parent.parent

_runner = CliRunner()


# ---------------------------------------------------------------------------
# Example discovery and isolation
# ---------------------------------------------------------------------------


def discover_examples() -> list[str]:
    """Return sorted list of example directory names under playground/."""
    playground = REPO_ROOT / "playground"
    return sorted(
        d.name for d in playground.iterdir() if d.is_dir() and (d / "workflow.json").exists()
    )


def copy_example(name: str, tmp_path: Path) -> Path:
    """Copy playground/<name>/ into tmp_path for per-run isolation (ADR-004).

    After this call, tmp_path contains workflow.json, agents.*.json,
    instructions/, fixtures/, etc. directly — it IS the workspace root.

    Returns tmp_path for convenience.
    """
    src = REPO_ROOT / "playground" / name
    if not src.is_dir():
        raise FileNotFoundError(f"Playground example not found: {src}")
    shutil.copytree(src, tmp_path, dirs_exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


def run_cli(args: list[str], tmp_path: Path):
    """Invoke the `ao` Typer app via CliRunner with AO_WORKSPACE_ROOT=tmp_path.

    Converts relative paths in args to absolute paths if they don't start with -.
    Returns the typer.testing.Result object (has .exit_code, .output).
    """
    # Convert relative paths to absolute paths for workflow/reposets/agents
    processed_args = []
    spec_flags = {"--workflow", "--reposets", "--agents"}
    for i, arg in enumerate(args):
        if not arg.startswith("-") and i > 0 and args[i - 1] in spec_flags:
            # This is a path argument following one of the spec flags
            path = tmp_path / arg
            processed_args.append(str(path))
        else:
            processed_args.append(arg)

    return _runner.invoke(app, processed_args, env={"AO_WORKSPACE_ROOT": str(tmp_path)})


# ---------------------------------------------------------------------------
# Tier selector
# ---------------------------------------------------------------------------


def agents_for(tier: str) -> str:
    """Return the agents filename for a given tier.

    Args:
        tier: "fake" for the deterministic FakeExecutor tier,
              "real" for the ClaudeCliExecutor tier.
    """
    if tier == "real":
        return "agents.claude.json"
    return "agents.fake.json"


# ---------------------------------------------------------------------------
# Control-file pre-seeding  (ADR-002)
# ---------------------------------------------------------------------------


def seed_control_files(tmp_path: Path, *, rounds: int = 1) -> None:
    """Pre-seed manifest and gate verdict fixtures before invoking ao run.

    The CLI's FakeExecutor is payload-less and never writes control files.
    We copy them from the example's fixtures/ directory so the engine reads
    the expected values during the run (ADR-002).

    Args:
        tmp_path: The workspace root (result of copy_example).
        rounds:   Number of review-round iterations to simulate.
                  1 (default) — final-verdict.json = {"continue": false} (one round).
                  2           — iter 1 continues, iter 2 stops.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = tmp_path / "fixtures"

    # Task manifest — always needed for emit_tasks
    shutil.copy(
        fixtures_dir / "tasks-manifest.json",
        output_dir / "tasks-manifest.json",
    )

    if rounds == 1:
        # Happy path: first gate verdict stops the loop immediately
        shutil.copy(
            fixtures_dir / "final-verdict.json",
            output_dir / "final-verdict.json",
        )
    elif rounds == 2:
        # Two-round variant: iter 1 continues, iter 2 stops
        (output_dir / "final-verdict.json").write_text('{"continue": true}')
        shutil.copy(
            fixtures_dir / "final-verdict-iter2.json",
            output_dir / "final-verdict__iter2.json",
        )
    else:
        raise ValueError(f"seed_control_files: unsupported rounds={rounds!r}; expected 1 or 2")


# ---------------------------------------------------------------------------
# Expected-structure loaders
# ---------------------------------------------------------------------------


def load_expected(name: str) -> dict:
    """Load and merge expected_paths.json and expected_events.json for an example.

    Returns a dict with keys "output_artifacts" and "expected_events".
    """
    fixtures_dir = REPO_ROOT / "playground" / name / "fixtures"
    paths_data = json.loads((fixtures_dir / "expected_paths.json").read_text())
    events_data = json.loads((fixtures_dir / "expected_events.json").read_text())
    return {
        "output_artifacts": paths_data,
        "expected_events": events_data,
    }


# ---------------------------------------------------------------------------
# Expanded workflow builder (for DAG acyclicity checks in Tier 1)
# ---------------------------------------------------------------------------


def expanded_workflow(name: str):
    """Build a WorkflowSpec with spine + injected manifest tasks merged.

    Used by the fixture tier to assert the statically-expanded graph is
    acyclic with the expected edges, before any real run (R2 mitigation).

    Returns a WorkflowSpec with tasks = spine_tasks + manifest_tasks.
    """
    from agent_orchestrator.models import TaskSpec, WorkflowSpec
    from agent_orchestrator.spec import load_workflow

    base_dir = REPO_ROOT / "playground" / name
    wf = load_workflow(base_dir / "workflow.json")
    manifest_path = base_dir / "fixtures" / "tasks-manifest.json"
    manifest = json.loads(manifest_path.read_text())

    injected: list[TaskSpec] = []
    for task_data in manifest.get("tasks", []):
        injected.append(TaskSpec(**task_data))

    merged_tasks = list(wf.tasks) + injected
    return WorkflowSpec(
        version=wf.version,
        id=wf.id,
        repo_set=wf.repo_set,
        tasks=merged_tasks,
        loops=wf.loops,
        triggers=wf.triggers,
    )


# ---------------------------------------------------------------------------
# Real-LLM guard
# ---------------------------------------------------------------------------


def requires_claude() -> None:
    """Skip (not fail) the current test if the `claude` binary is not on PATH."""
    if shutil.which("claude") is None:
        pytest.skip("claude CLI binary not found on PATH; skipping real_llm tier")


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_tree(root: Path, expected_paths: list[str]) -> None:
    """Assert that every path in expected_paths exists under root and is non-empty.

    Args:
        root: The workspace root (typically the tmp_path from a run).
        expected_paths: List of relative paths that must exist and be non-empty.
                       Paths are relative to root.
    """
    for rel_path in expected_paths:
        full_path = root / rel_path
        assert full_path.exists(), f"Missing expected output: {rel_path}"
        assert full_path.stat().st_size > 0, f"Empty output file: {rel_path}"
