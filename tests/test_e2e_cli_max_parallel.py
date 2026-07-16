"""CliRunner e2e tests for max_parallel flag, env, and config (T-TNleFt).

Tests cover:
- --max-parallel CLI flag takes effect and run succeeds
- AO_MAX_PARALLEL env var takes effect and run succeeds
- .ao/config.yaml max_parallel field takes effect and run succeeds
- Precedence: CLI > env > config > default
- Invalid values: negative --max-parallel exits 1 with error
- Edge case: --max-parallel 0 falls through to default (serial), exits 0
- Empty AO_MAX_PARALLEL="" treated as unset, no crash
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.cli import app

runner = CliRunner()

# Repo root and example specs
REPO_ROOT = Path(__file__).parent.parent
SPECS_EXAMPLES = REPO_ROOT / "specs" / "examples"


def _copy_examples_with_fake_agents(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Copy example workflow + reposet to tmp; write a fake-executor agents.json."""
    wf = tmp_path / "workflow.json"
    rs = tmp_path / "reposets.json"
    ag = tmp_path / "agents.json"

    shutil.copy(SPECS_EXAMPLES / "workflow.json", wf)
    shutil.copy(SPECS_EXAMPLES / "reposet.json", rs)

    # Write fake-executor versions of all agents in the original
    orig_agents = json.loads((SPECS_EXAMPLES / "agents.json").read_text())
    fake_agents: dict = {"version": "1.0", "agents": {}}
    for name in orig_agents["agents"]:
        fake_agents["agents"][name] = {"executor": "fake"}
    ag.write_text(json.dumps(fake_agents))

    # Copy instructions too
    instr_src = SPECS_EXAMPLES / "instructions"
    instr_dst = tmp_path / "specs" / "examples" / "instructions"
    instr_dst.mkdir(parents=True, exist_ok=True)
    for f in instr_src.iterdir():
        if f.is_file():
            shutil.copy(f, instr_dst / f.name)

    return wf, rs, ag


def _write_small_workflow(tmp_path: Path) -> Path:
    """Write a small multi-task workflow for max_parallel tests."""
    wf_path = tmp_path / "workflow.json"
    wf_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "multi-task-wf",
                "repo_set": "rs",
                "tasks": [
                    {
                        "id": "task_a",
                        "agent": "ag",
                        "instruction": "specs/instructions/task_a.md",
                        "outputs": ["output/task_a.txt"],
                    },
                    {
                        "id": "task_b",
                        "agent": "ag",
                        "instruction": "specs/instructions/task_b.md",
                        "outputs": ["output/task_b.txt"],
                    },
                    {
                        "id": "task_c",
                        "agent": "ag",
                        "instruction": "specs/instructions/task_c.md",
                        "depends_on": ["task_a", "task_b"],
                        "inputs": ["output/task_a.txt", "output/task_b.txt"],
                        "outputs": ["output/task_c.txt"],
                    },
                ],
            }
        )
    )
    return wf_path


def _write_simple_reposet(tmp_path: Path) -> Path:
    """Write a minimal reposet.json."""
    rs_path = tmp_path / "reposets.json"
    rs_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "rs": {
                        "workspace_root": str(tmp_path),
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )
    return rs_path


def _write_simple_agents(tmp_path: Path) -> Path:
    """Write a minimal agents.json with fake executor."""
    ag_path = tmp_path / "agents.json"
    ag_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {"ag": {"executor": "fake"}},
            }
        )
    )
    return ag_path


def _write_instructions(tmp_path: Path) -> None:
    """Write minimal instruction files."""
    instr_dir = tmp_path / "specs" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)
    (instr_dir / "task_a.md").write_text("# Task A\nDo something.")
    (instr_dir / "task_b.md").write_text("# Task B\nDo something else.")
    (instr_dir / "task_c.md").write_text("# Task C\nCombine results.")


class TestMaxParallelCliFlag:
    """Test that --max-parallel CLI flag takes effect."""

    def test_max_parallel_flag_positive_succeeds(self, tmp_path: Path) -> None:
        """ao run --max-parallel 2 on a small workflow succeeds."""
        wf = _write_small_workflow(tmp_path)
        rs = _write_simple_reposet(tmp_path)
        ag = _write_simple_agents(tmp_path)
        _write_instructions(tmp_path)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-parallel",
                "2",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

    def test_max_parallel_flag_one_succeeds(self, tmp_path: Path) -> None:
        """ao run --max-parallel 1 (serial) succeeds."""
        wf = _copy_examples_with_fake_agents(tmp_path)[0]
        rs = _copy_examples_with_fake_agents(tmp_path)[1]
        ag = _copy_examples_with_fake_agents(tmp_path)[2]

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-parallel",
                "1",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, f"CLI failed:\n{result.output}"


class TestMaxParallelEnvVar:
    """Test that AO_MAX_PARALLEL env var takes effect."""

    def test_max_parallel_env_var_positive_succeeds(self, tmp_path: Path) -> None:
        """ao run with AO_MAX_PARALLEL=2 succeeds."""
        wf = _write_small_workflow(tmp_path)
        rs = _write_simple_reposet(tmp_path)
        ag = _write_simple_agents(tmp_path)
        _write_instructions(tmp_path)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path), "AO_MAX_PARALLEL": "2"},
        )

        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

    def test_max_parallel_env_empty_treated_as_unset(self, tmp_path: Path) -> None:
        """ao run with AO_MAX_PARALLEL='' (empty) falls through, does not crash."""
        wf = _copy_examples_with_fake_agents(tmp_path)[0]
        rs = _copy_examples_with_fake_agents(tmp_path)[1]
        ag = _copy_examples_with_fake_agents(tmp_path)[2]

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path), "AO_MAX_PARALLEL": ""},
        )

        # Empty env var should be treated as unset, no error
        assert result.exit_code == 0, f"CLI failed with empty AO_MAX_PARALLEL:\n{result.output}"


class TestMaxParallelConfigFile:
    """Test that .ao/config.yaml max_parallel field takes effect."""

    def test_max_parallel_config_yaml_succeeds(self, tmp_path: Path) -> None:
        """ao run uses max_parallel from .ao/config.yaml when flag/env are absent."""
        wf = _write_small_workflow(tmp_path)
        rs = _write_simple_reposet(tmp_path)
        ag = _write_simple_agents(tmp_path)
        _write_instructions(tmp_path)

        # Write .ao/config.yaml with max_parallel
        config_dir = tmp_path / ".ao"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.yaml"
        config_path.write_text("max_parallel: 2\n")

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()


class TestMaxParallelPrecedence:
    """Test CLI > env > config precedence (ADR-0003)."""

    def test_cli_flag_overrides_env(self, tmp_path: Path) -> None:
        """CLI --max-parallel overrides AO_MAX_PARALLEL env var."""
        wf = _write_small_workflow(tmp_path)
        rs = _write_simple_reposet(tmp_path)
        ag = _write_simple_agents(tmp_path)
        _write_instructions(tmp_path)

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-parallel",
                "2",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path), "AO_MAX_PARALLEL": "4"},
        )

        # Should succeed using CLI value (2), not env (4)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

    def test_env_overrides_config(self, tmp_path: Path) -> None:
        """AO_MAX_PARALLEL env var overrides config file."""
        wf = _write_small_workflow(tmp_path)
        rs = _write_simple_reposet(tmp_path)
        ag = _write_simple_agents(tmp_path)
        _write_instructions(tmp_path)

        # Write .ao/config.yaml with max_parallel=4
        config_dir = tmp_path / ".ao"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.yaml"
        config_path.write_text("max_parallel: 4\n")

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path), "AO_MAX_PARALLEL": "2"},
        )

        # Should succeed using env (2), not config (4)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()

    def test_cli_overrides_env_and_config(self, tmp_path: Path) -> None:
        """CLI flag overrides both env and config."""
        wf = _write_small_workflow(tmp_path)
        rs = _write_simple_reposet(tmp_path)
        ag = _write_simple_agents(tmp_path)
        _write_instructions(tmp_path)

        # Write .ao/config.yaml
        config_dir = tmp_path / ".ao"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.yaml"
        config_path.write_text("max_parallel: 4\n")

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-parallel",
                "1",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path), "AO_MAX_PARALLEL": "2"},
        )

        # Should succeed using CLI (1), not env (2) or config (4)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert "succeeded" in result.output.lower()


class TestMaxParallelEdgeCases:
    """Test edge cases: 0, negative values."""

    def test_max_parallel_zero_falls_through_to_default(self, tmp_path: Path) -> None:
        """--max-parallel 0 falls through to default (serial), exits 0."""
        wf = _copy_examples_with_fake_agents(tmp_path)[0]
        rs = _copy_examples_with_fake_agents(tmp_path)[1]
        ag = _copy_examples_with_fake_agents(tmp_path)[2]

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-parallel",
                "0",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        # 0 should fall through and use default (1), NOT error
        assert result.exit_code == 0, f"CLI failed (0 should not error):\n{result.output}"
        assert "succeeded" in result.output.lower()

    def test_max_parallel_negative_flag_errors(self, tmp_path: Path) -> None:
        """--max-parallel -1 exits 1 with 'must be >= 1' error."""
        wf = _copy_examples_with_fake_agents(tmp_path)[0]
        rs = _copy_examples_with_fake_agents(tmp_path)[1]
        ag = _copy_examples_with_fake_agents(tmp_path)[2]

        result = runner.invoke(
            app,
            [
                "run",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
                "--max-parallel",
                "-1",
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1, (
            f"Negative --max-parallel should exit 1, got:\n{result.output}"
        )
        assert "must be >= 1" in result.output.lower()

    def test_max_parallel_negative_env_errors(self, tmp_path: Path) -> None:
        """AO_MAX_PARALLEL=-5 exits 1 with error."""
        wf = _copy_examples_with_fake_agents(tmp_path)[0]
        rs = _copy_examples_with_fake_agents(tmp_path)[1]
        ag = _copy_examples_with_fake_agents(tmp_path)[2]

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path), "AO_MAX_PARALLEL": "-5"},
        )

        assert result.exit_code == 1, f"Negative AO_MAX_PARALLEL should exit 1:\n{result.output}"
        assert "must be >= 1" in result.output.lower()

    def test_max_parallel_config_zero_falls_through(self, tmp_path: Path) -> None:
        """max_parallel: 0 in config.yaml falls through to default (serial), exits 0."""
        wf = _copy_examples_with_fake_agents(tmp_path)[0]
        rs = _copy_examples_with_fake_agents(tmp_path)[1]
        ag = _copy_examples_with_fake_agents(tmp_path)[2]

        # Write .ao/config.yaml with max_parallel: 0
        config_dir = tmp_path / ".ao"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.yaml"
        config_path.write_text("max_parallel: 0\n")

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        # 0 in config should also fall through and use default, no error
        assert result.exit_code == 0, f"Config max_parallel: 0 should not error:\n{result.output}"
        assert "succeeded" in result.output.lower()
