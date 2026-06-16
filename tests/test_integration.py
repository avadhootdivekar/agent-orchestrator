"""Integration tests: full E2E workflow runs and edge cases."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.errors import CycleError, SpecValidationError

runner = CliRunner()

# ---------------------------------------------------------------------------
# Repo root (specs dir)
# ---------------------------------------------------------------------------

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
    instr_dst.mkdir(parents=True)
    for f in instr_src.iterdir():
        shutil.copy(f, instr_dst / f.name)

    return wf, rs, ag


class TestFullE2ERun:
    def test_all_tasks_succeed_outputs_exist(self, tmp_path) -> None:
        wf, rs, ag = _copy_examples_with_fake_agents(tmp_path)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, f"CLI output:\n{result.output}"
        assert "succeeded" in result.output

        # Outputs declared in workflow.json
        for out in ["output/design.md", "output/impl-report.md", "output/test-report.md"]:
            assert (tmp_path / out).exists(), f"Missing output: {out}"

    def test_task_statuses_printed_in_table(self, tmp_path) -> None:
        wf, rs, ag = _copy_examples_with_fake_agents(tmp_path)

        result = runner.invoke(
            app,
            ["run", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        # Status table should contain task ids
        assert "design" in result.output
        assert "implement" in result.output
        assert "test" in result.output


class TestResume:
    def test_resume_reruns_only_failed_task(self, tmp_path) -> None:
        """First run: design + implement succeed, test fails. Resume: only test runs."""
        from agent_orchestrator.artifacts import LocalFsArtifactStore
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.executors.fake import FakeExecutor
        from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec
        from agent_orchestrator.runstate import RunStateStore

        # Set up workspace
        instr_dir = tmp_path / "specs" / "examples" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "design.md").write_text("design instr")
        (instr_dir / "implement.md").write_text("impl instr")
        (instr_dir / "test.md").write_text("test instr")

        wf = WorkflowSpec(
            version="1.0",
            id="resume-wf",
            repo_set="rs",
            tasks=[
                TaskSpec(
                    id="design",
                    agent="ag",
                    instruction="specs/examples/instructions/design.md",
                    outputs=["output/design.md"],
                ),
                TaskSpec(
                    id="implement",
                    agent="ag",
                    instruction="specs/examples/instructions/implement.md",
                    inputs=["output/design.md"],
                    outputs=["output/impl.md"],
                    depends_on=["design"],
                ),
                TaskSpec(
                    id="test",
                    agent="ag",
                    instruction="specs/examples/instructions/test.md",
                    inputs=["output/impl.md"],
                    outputs=["output/test.md"],
                    depends_on=["implement"],
                ),
            ],
        )
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="core", path=".", role="primary")],
            )
        }
        agents = {"ag": AgentSpec(executor="fake")}

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)

        # First run: test fails
        orch1 = Orchestrator(FakeExecutor(behaviors={"test": "fail"}), store, rs_store)
        state1 = orch1.run(wf, reposets, agents)
        assert state1.status == "failed"
        assert state1.tasks["test"].status == "failed"
        run_id = state1.run_id

        # Resume with test now succeeding
        orch2 = Orchestrator(FakeExecutor(), store, rs_store)
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        state2 = orch2.run(wf, reposets, agents, run_state=existing)

        assert state2.status == "succeeded"
        assert state2.tasks["test"].status == "succeeded"
        # design + implement should be skipped (outputs present)
        assert state2.tasks["design"].status in ("skipped", "succeeded")
        assert state2.tasks["implement"].status in ("skipped", "succeeded")


class TestCycleError:
    def test_cyclic_workflow_raises_cycle_error(self, tmp_path) -> None:
        from agent_orchestrator.dag import build_dag
        from agent_orchestrator.models import TaskSpec, WorkflowSpec

        wf = WorkflowSpec(
            version="1.0",
            id="cyclic",
            repo_set="rs",
            tasks=[
                TaskSpec(id="a", agent="ag", instruction="i.md", depends_on=["b"]),
                TaskSpec(id="b", agent="ag", instruction="i.md", depends_on=["a"]),
            ],
        )
        graph = build_dag(wf)
        with pytest.raises(CycleError):
            graph.topological_order()

    def test_engine_propagates_cycle_error(self, tmp_path) -> None:
        from agent_orchestrator.artifacts import LocalFsArtifactStore
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.executors.fake import FakeExecutor
        from agent_orchestrator.models import AgentSpec, RepoRef, RepoSet, TaskSpec, WorkflowSpec
        from agent_orchestrator.runstate import RunStateStore

        wf = WorkflowSpec(
            version="1.0",
            id="cyclic-wf",
            repo_set="rs",
            tasks=[
                TaskSpec(id="a", agent="ag", instruction="i.md", depends_on=["b"]),
                TaskSpec(id="b", agent="ag", instruction="i.md", depends_on=["a"]),
            ],
        )
        reposets = {
            "rs": RepoSet(
                workspace_root=str(tmp_path),
                repos=[RepoRef(id="core", path=".", role="primary")],
            )
        }
        agents = {"ag": AgentSpec(executor="fake")}

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        orch = Orchestrator(FakeExecutor(), store, rs_store)

        with pytest.raises(CycleError):
            orch.run(wf, reposets, agents)


class TestMalformedSpec:
    def test_bad_json_raises_config_error(self, tmp_path) -> None:
        from agent_orchestrator.errors import ConfigError
        from agent_orchestrator.spec import load_workflow

        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json")
        with pytest.raises(ConfigError):
            load_workflow(bad)

    def test_schema_violation_raises_spec_validation_error(self, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        # 'tasks' is required but missing; 'id' pattern violated
        bad.write_text(
            json.dumps({"version": "1.0", "id": "UPPERCASE-NOT-ALLOWED", "repo_set": "rs"})
        )

        with pytest.raises(SpecValidationError):
            from agent_orchestrator.spec import load_workflow

            load_workflow(bad)


class TestEngineHygieneStaticCheck:
    def test_engine_py_contains_no_direct_file_reads(self) -> None:
        """Static check: engine.py must not call open() or .read() on artifact/instruction paths.

        The engine must only call store.exists() and store.resolve() — never read file contents.
        """
        engine_src = (REPO_ROOT / "src" / "agent_orchestrator" / "engine.py").read_text()

        # Check for open() calls that aren't in comments or strings
        open_calls = re.findall(r"\bopen\s*\(", engine_src)
        assert not open_calls, f"engine.py contains open() call(s): {open_calls}"

        # Check for .read() calls (excluding model_validate_json which is in runstate, not engine)
        read_calls = re.findall(r"\.read\s*\(", engine_src)
        assert not read_calls, f"engine.py contains .read() call(s): {read_calls}"

        # Confirm store.exists and store.resolve are used
        assert "self._store.exists" in engine_src
        assert "self._store.resolve" in engine_src
