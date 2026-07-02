"""Shared pytest fixtures for the agent-orchestrator test suite.

Area-1 fixtures (structured logging, status artifact, output capture) live here.
Area-2 fixtures (dynamic injection, loop) will be added by T-5isej3.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    LoopSpec,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Fixed clock (NFR-2)
# ---------------------------------------------------------------------------

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture()
def fixed_clock() -> Callable[[], datetime]:
    """Returns a deterministic clock that always returns 2026-01-01 00:00:00 UTC."""
    return lambda: _FIXED_DT


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean workspace root with AO_WORKSPACE_ROOT set in the environment."""
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Artifact + run-state stores
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(workspace: Path) -> LocalFsArtifactStore:
    """LocalFsArtifactStore rooted at the workspace fixture."""
    return LocalFsArtifactStore(str(workspace))


@pytest.fixture()
def rs_store(
    workspace: Path,
    store: LocalFsArtifactStore,
    fixed_clock: Callable[[], datetime],
) -> RunStateStore:
    """RunStateStore with a fixed clock for deterministic run IDs and timestamps."""
    return RunStateStore(str(workspace), store, clock=fixed_clock)


# ---------------------------------------------------------------------------
# WorkflowSpec factory
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_workflow(workspace: Path) -> Callable[..., WorkflowSpec]:
    """Factory: build a WorkflowSpec from a compact task-dict list.

    Usage::

        wf = make_workflow([
            {"id": "a", "outputs": ["output/a.txt"]},
            {
                "id": "b",
                "depends_on": ["a"],
                "inputs": ["output/a.txt"],
                "outputs": ["output/b.txt"],
            },
        ])

    Creates stub instruction files under workspace/specs/instructions/<id>.md so
    the engine can resolve them without real files being required.
    """

    def _factory(
        tasks: list[dict],
        wf_id: str = "test-wf",
        defaults: WorkflowDefaults | None = None,
        loops: list[dict] | None = None,
    ) -> WorkflowSpec:
        instr_dir = workspace / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)

        task_specs = []
        for t in tasks:
            task_id = t["id"]
            instr_path = f"specs/instructions/{task_id}.md"
            # Use an existing instruction file if already created (loop clones share ids)
            if not (workspace / instr_path).exists():
                (workspace / instr_path).write_text(f"instruction for {task_id}")

            task_specs.append(
                TaskSpec(
                    id=task_id,
                    agent="ag",
                    instruction=instr_path,
                    inputs=t.get("inputs", []),
                    outputs=t.get("outputs", []),
                    output_manifest=t.get("output_manifest"),
                    depends_on=t.get("depends_on", []),
                    retries=t.get("retries"),
                    timeout_seconds=t.get("timeout_seconds"),
                    skip_if_outputs_exist=t.get("skip_if_outputs_exist", True),
                    emit_tasks=t.get("emit_tasks", False),
                    task_manifest_path=t.get("task_manifest_path"),
                )
            )

        loop_specs = [LoopSpec(**lp) for lp in (loops or [])]

        return WorkflowSpec(
            version="1.0",
            id=wf_id,
            repo_set="rs",
            tasks=task_specs,
            loops=loop_specs,
            defaults=defaults or WorkflowDefaults(),
        )

    return _factory


# ---------------------------------------------------------------------------
# Log / status assertion helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def read_jsonl() -> Callable[[Path], list[dict]]:
    """Parse a JSON-lines file; asserts every non-empty line is valid JSON."""

    def _read(path: Path) -> list[dict]:
        records = []
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                pytest.fail(f"Line {lineno} of {path} is not valid JSON: {exc}\n  {line!r}")
        return records

    return _read


@pytest.fixture()
def read_status() -> Callable[[Path], dict]:
    """Load and return status.json from a run directory as a dict."""

    def _read(run_dir: Path) -> dict:
        p = run_dir / "status.json"
        assert p.exists(), f"status.json not found in {run_dir}"
        return json.loads(p.read_text())

    return _read


# ---------------------------------------------------------------------------
# Orchestrator factory (Area 2)
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_orchestrator(
    store: LocalFsArtifactStore,
    rs_store: RunStateStore,
    workspace: Path,
) -> Callable[..., tuple[Orchestrator, dict, dict]]:
    """Factory: return (orchestrator, reposets, agents) for a FakeExecutor run.

    Usage::

        orch, reposets, agents = make_orchestrator()
        orch, reposets, agents = make_orchestrator(FakeExecutor(behaviors={"t": "fail"}))
    """

    def _factory(
        executor: FakeExecutor | None = None,
    ) -> tuple[Orchestrator, dict, dict]:
        exec_ = executor or FakeExecutor()
        orch = Orchestrator(exec_, store, rs_store)
        reposets = {
            "rs": RepoSet(
                workspace_root=str(workspace),
                repos=[RepoRef(id="core", path=".", role="primary")],
            )
        }
        agents: dict = {"ag": AgentSpec(executor="fake")}
        return orch, reposets, agents

    return _factory
