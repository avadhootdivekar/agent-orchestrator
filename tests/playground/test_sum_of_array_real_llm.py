"""Tier 3 — Fuzzy / nondeterministic real-LLM harness and gated smoke test.

The SAME workflow spec runs against agents.claude.json (ClaudeCliExecutor).
No pre-seeding — real architect/reviewer agents WRITE the control files themselves.

Every test here is marked @pytest.mark.real_llm AND guarded by AO_E2E_REAL_LLM=1;
skipped by default so normal CI never burns tokens or flakes.

Only structure / exit / completion / path existence are asserted — NEVER content.

Covers requirements: FR-5, FR-8, NFR-1, NFR-3, NFR-4. ADR-003, ADR-005.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.playground.harness import (
    agents_for,
    copy_example,
    requires_claude,
    run_cli,
)

pytestmark = pytest.mark.real_llm

# Spine output paths that should exist after a successful run
SPINE_OUTPUTS = [
    "output/design.md",
    "output/design-review.md",
    "output/integrated.md",
    "output/bugfix.md",
    "output/final-review.md",
    "output/summary.md",
]


def _extract_run_id(cli_output: str) -> str:
    """Extract run_id from CLI stdout using Run: <run_id> pattern."""
    import re

    match = re.search(r"Run:\s+(\S+)", cli_output)
    if not match:
        raise ValueError(f"Could not extract run_id from CLI output:\n{cli_output}")
    return match.group(1)


def _read_state(tmp_path: Path, run_id: str) -> dict:
    """Read state.json for a given run."""
    state_path = tmp_path / ".orchestrator" / "runs" / run_id / "state.json"
    return json.loads(state_path.read_text())


class TestSumOfArrayRealLLM:
    """Real-LLM tier — single gated smoke test for sum-of-array workflow."""

    def test_sum_of_array_real_completes(self, real_llm_workspace: Path) -> None:
        """Smoke test: sum-of-array with real agents completes with exit 0.

        Verifies structure/completion only, never content.
        Real agents generate control files (no pre-seeding).
        """
        requires_claude()  # Skip if claude binary not available

        # Copy example (NO pre-seeding — real agents will write control files)
        copy_example("sum-of-array", real_llm_workspace)

        # Run with real agents
        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("real"),
            ],
            real_llm_workspace,
        )

        # Should exit 0
        assert result.exit_code == 0, (
            f"Real workflow failed with exit code {result.exit_code}:\n{result.output}"
        )

        # Extract run_id
        run_id = _extract_run_id(result.output)

        # Verify state.json says succeeded
        state = _read_state(real_llm_workspace, run_id)
        assert state["status"] == "succeeded", f"Expected status='succeeded', got {state['status']}"

    def test_sum_of_array_real_spine_outputs_exist(self, real_llm_workspace: Path) -> None:
        """Given a real run completes, when artifacts are checked, then spine outputs exist."""
        requires_claude()

        copy_example("sum-of-array", real_llm_workspace)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("real"),
            ],
            real_llm_workspace,
        )

        assert result.exit_code == 0

        # Verify spine outputs exist (structure only, no content assertions)
        for output_path in SPINE_OUTPUTS:
            full_path = real_llm_workspace / output_path
            assert full_path.exists(), f"Spine output missing: {output_path}"

    def test_sum_of_array_real_control_files_exist(self, real_llm_workspace: Path) -> None:
        """Given a real run completes, when control files are checked, then they parse correctly.

        We assert structure (JSON shape) only — never content.
        """
        requires_claude()

        copy_example("sum-of-array", real_llm_workspace)

        result = run_cli(
            [
                "run",
                "--workflow",
                "workflow.json",
                "--reposets",
                "reposet.json",
                "--agents",
                agents_for("real"),
            ],
            real_llm_workspace,
        )

        assert result.exit_code == 0

        # tasks-manifest.json should exist and parse as {"tasks":[...]}
        manifest_path = real_llm_workspace / "output" / "tasks-manifest.json"
        assert manifest_path.exists(), "output/tasks-manifest.json should exist"
        manifest = json.loads(manifest_path.read_text())
        assert "tasks" in manifest, "manifest should have 'tasks' key"
        assert isinstance(manifest["tasks"], list), "manifest.tasks should be a list"

        # final-verdict.json should exist and parse as {"continue":bool}
        verdict_path = real_llm_workspace / "output" / "final-verdict.json"
        assert verdict_path.exists(), "output/final-verdict.json should exist"
        verdict = json.loads(verdict_path.read_text())
        assert "continue" in verdict, "verdict should have 'continue' key"
        assert isinstance(verdict["continue"], bool), "verdict.continue should be bool"
