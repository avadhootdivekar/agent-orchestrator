"""Tests for the Typer CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_agents_fake(path: Path) -> None:
    """Write an agents config where all executors are 'fake'."""
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": {
                    "architect": {"executor": "fake"},
                    "developer": {"executor": "fake"},
                    "tester": {"executor": "fake"},
                },
            }
        )
    )


def _write_reposets(path: Path, workspace: str) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "repo_sets": {
                    "default-set": {
                        "workspace_root": workspace,
                        "repos": [{"id": "core", "path": ".", "role": "primary"}],
                    }
                },
            }
        )
    )


def _write_workflow(path: Path, tasks=None) -> None:
    if tasks is None:
        tasks = [
            {
                "id": "design",
                "agent": "architect",
                "instruction": "specs/examples/instructions/design.md",
                "outputs": ["output/design.md"],
            },
        ]
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "test-wf",
                "repo_set": "default-set",
                "tasks": tasks,
            }
        )
    )


# ---------------------------------------------------------------------------
# Test: validate command
# ---------------------------------------------------------------------------


class TestValidateCommand:
    def test_valid_specs_exit_0(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"

        _write_workflow(wf)
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_malformed_workflow_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "bad.json"
        wf.write_text('{"version": "1.0"}')  # missing required 'id', 'repo_set', 'tasks'
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 1

    def test_missing_reposets_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        _write_workflow(wf)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf)],
        )
        assert result.exit_code == 1

    def test_unknown_agent_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "t1",
                    "agent": "nonexistent-agent",
                    "instruction": "instr.md",
                }
            ],
        )
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Test: run command
# ---------------------------------------------------------------------------


class TestRunCommand:
    def test_successful_run_exits_0(self, tmp_path) -> None:
        # Create the instruction file so ArtifactStore.resolve doesn't error
        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"

        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "design",
                    "agent": "architect",
                    "instruction": "specs/examples/instructions/design.md",
                    "outputs": ["output/design.md"],
                }
            ],
        )
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0
        assert "succeeded" in result.output

    def test_failed_run_exits_1(self, tmp_path) -> None:
        """A workflow where FakeExecutor is configured to fail -> exit 1."""
        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        # agents.json with executor=fake but behavior hardcoded to fail won't work via CLI directly;
        # instead test missing input detection which also causes a failed run
        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"

        _write_workflow(
            wf,
            tasks=[
                {
                    "id": "design",
                    "agent": "architect",
                    "instruction": "specs/examples/instructions/design.md",
                    "inputs": ["no/such/file.txt"],
                    "outputs": ["output/design.md"],
                }
            ],
        )
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Test: status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_for_existing_run(self, tmp_path) -> None:
        from agent_orchestrator.artifacts import LocalFsArtifactStore
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.executors.fake import FakeExecutor
        from agent_orchestrator.runstate import RunStateStore

        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instruction")

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        executor = FakeExecutor()

        from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec

        wf = WorkflowSpec(
            version="1.0",
            id="test-wf",
            repo_set="rs",
            tasks=[
                TaskSpec(
                    id="design",
                    agent="ag",
                    instruction="specs/examples/instructions/design.md",
                    outputs=["output/design.md"],
                )
            ],
        )
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="core", path=".", role="primary")],
            )
        }
        agents = {"ag": AgentSpec(executor="fake")}
        orch = Orchestrator(executor, store, rs_store)
        state = orch.run(wf, reposets, agents)
        run_id = state.run_id

        # Now test CLI status command
        wf_path = tmp_path / "workflow.json"
        rs_path = tmp_path / "reposets.json"
        ag_path = tmp_path / "agents.json"
        _write_reposets(rs_path, str(tmp_path))
        _write_agents_fake(ag_path)
        wf_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "id": "test-wf",
                    "repo_set": "default-set",
                    "tasks": [
                        {
                            "id": "design",
                            "agent": "architect",
                            "instruction": "specs/examples/instructions/design.md",
                            "outputs": ["output/design.md"],
                        }
                    ],
                }
            )
        )

        result = runner.invoke(
            app,
            [
                "status",
                "--run-id",
                run_id,
                "--workflow",
                str(wf_path),
                "--reposets",
                str(rs_path),
                "--agents",
                str(ag_path),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0
        assert run_id in result.output
        assert "design" in result.output

    def test_status_missing_run_exits_1(self, tmp_path) -> None:
        wf = tmp_path / "workflow.json"
        rs = tmp_path / "reposets.json"
        ag = tmp_path / "agents.json"
        _write_workflow(wf)
        _write_reposets(rs, str(tmp_path))
        _write_agents_fake(ag)

        result = runner.invoke(
            app,
            [
                "status",
                "--run-id",
                "nonexistent-run-id",
                "--workflow",
                str(wf),
                "--reposets",
                str(rs),
                "--agents",
                str(ag),
            ],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1
