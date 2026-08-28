"""Shared fixtures for the dashboard test suite (E-Ui7Kq2).

The stub supervisor here is what keeps the UI tests fast and hermetic: every test that
exercises run control gets deterministic launch/cancel behaviour without spawning a single
subprocess. The real :class:`ProcessSupervisor` is covered separately, including one test
that spawns an actual child.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.runstate import RunStateStore
from agent_orchestrator.ui.processes import LaunchError, LaunchRecord
from agent_orchestrator.ui.service import DashboardService


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """An empty workspace root for the dashboard to serve."""
    return tmp_path


def make_run_state(
    run_id: str = "demo-20260724T100000Z",
    workflow_id: str = "demo",
    status: str = "succeeded",
    tasks: dict[str, TaskRunState] | None = None,
    started_at: str = "2026-07-24T10:00:00+00:00",
    updated_at: str = "2026-07-24T10:05:00+00:00",
) -> RunState:
    """Build a RunState with deterministic timestamps and usage numbers."""
    return RunState(
        run_id=run_id,
        workflow_id=workflow_id,
        repo_set="demo-repos",
        started_at=started_at,
        updated_at=updated_at,
        status=status,  # type: ignore[arg-type]
        tasks=tasks
        if tasks is not None
        else {
            "build": TaskRunState(
                status="succeeded",
                attempts=1,
                started_at="2026-07-24T10:00:00+00:00",
                ended_at="2026-07-24T10:02:00+00:00",
                cumulative_input_tokens=1000,
                cumulative_output_tokens=250,
                cumulative_cost_usd=0.5,
            ),
            "test": TaskRunState(
                status="succeeded",
                attempts=2,
                started_at="2026-07-24T10:02:00+00:00",
                ended_at="2026-07-24T10:03:00+00:00",
                cumulative_input_tokens=500,
                cumulative_output_tokens=125,
                cumulative_cost_usd=0.25,
            ),
        },
    )


def write_run(workspace: Path, state: RunState) -> Path:
    """Persist *state* into *workspace* exactly the way the engine does.

    Goes through the real ``RunStateStore.save`` so status.json is derived the same way it
    is in production. ``save`` re-stamps ``updated_at`` from its clock, so a clock pinned to
    the state's own ``updated_at`` is injected — otherwise every fixture's wall-clock span
    would be "now minus the fixture start time", i.e. nondeterministic (CLAUDE.md's
    fixed-clock rule for anything time-dependent).
    """
    pinned = datetime.fromisoformat(state.updated_at)
    store = LocalFsArtifactStore(str(workspace))
    RunStateStore(str(workspace), store, clock=lambda: pinned).save(state)
    return workspace / ".orchestrator" / "runs" / state.run_id


def write_run_raw(workspace: Path, state: RunState) -> Path:
    """Write state.json directly, bypassing ``RunStateStore``.

    Needed only for states the store itself could never produce — e.g. a corrupt
    ``updated_at`` — which is exactly the input the dashboard's defensive parsing exists
    to survive.
    """
    run_dir = workspace / ".orchestrator" / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return run_dir


def write_workflow(
    path: Path,
    workflow_id: str = "demo",
    prompt_path: str | None = None,
    general_instructions: list[str] | None = None,
) -> Path:
    """Write a minimal but schema-valid workflow spec to *path*."""
    spec: dict = {
        "version": "1.0",
        "id": workflow_id,
        "name": f"Workflow {workflow_id}",
        "repo_set": "demo-repos",
        "tasks": [{"id": "build", "agent": "dev", "instruction": "instructions/build.md"}],
    }
    if prompt_path is not None:
        spec["prompt_path"] = prompt_path
    if general_instructions is not None:
        spec["general_instructions"] = general_instructions
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return path


class StubSupervisor:
    """In-memory stand-in for ProcessSupervisor — never spawns a process.

    Mirrors the real class's contract closely enough that DashboardService cannot tell the
    difference: launches produce LaunchRecords, `is_running` reflects liveness, and cancel
    raises LaunchError for a run this supervisor never launched.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.records: list[LaunchRecord] = []
        self.launch_calls: list[dict] = []
        self.cancelled: list[str] = []
        self.next_run_id: str | None = "launched-run"
        self.live_runs: set[str] = set()

    def _record(self, kind: str, run_id: str | None, **extra: object) -> LaunchRecord:
        record = LaunchRecord(
            launch_id=f"launch-{len(self.records) + 1}",
            kind=kind,
            pid=10_000 + len(self.records),
            argv=["stub", kind],
            started_at=datetime.now(UTC).isoformat(),
            log_path=str(self.workspace / "stub.log"),
            run_id=run_id,
            **extra,  # type: ignore[arg-type]
        )
        self.records.append(record)
        if run_id:
            self.live_runs.add(run_id)
        return record

    def launch_run(self, **kwargs: object) -> LaunchRecord:
        self.launch_calls.append(dict(kwargs))
        prompt = kwargs.get("prompt")
        return self._record(
            "run",
            self.next_run_id,
            workflow_path=kwargs.get("workflow_path"),  # type: ignore[arg-type]
            prompt_chars=len(prompt) if isinstance(prompt, str) else 0,
        )

    def launch_resume(self, run_id: str, **kwargs: object) -> LaunchRecord:
        self.launch_calls.append({"run_id": run_id, **kwargs})
        return self._record("resume", run_id, workflow_path=kwargs.get("workflow_path"))  # type: ignore[arg-type]

    def cancel(self, run_id: str) -> LaunchRecord:
        record = self.record_for_run(run_id)
        if record is None or run_id not in self.live_runs:
            raise LaunchError(f"run {run_id} was not launched from this dashboard")
        self.live_runs.discard(run_id)
        record.cancelled = True
        record.finished_at = datetime.now(UTC).isoformat()
        self.cancelled.append(run_id)
        return record

    def record_for_run(self, run_id: str) -> LaunchRecord | None:
        return next((r for r in self.records if r.run_id == run_id), None)

    def is_running(self, run_id: str) -> bool:
        return run_id in self.live_runs

    def reconcile(self) -> list[LaunchRecord]:
        return list(self.records)

    def read_log(self, launch_id: str, max_bytes: int = 200_000) -> str:
        return f"log for {launch_id}"[:max_bytes]


@pytest.fixture()
def stub_supervisor(workspace: Path) -> StubSupervisor:
    return StubSupervisor(workspace)


@pytest.fixture()
def service(workspace: Path, stub_supervisor: StubSupervisor) -> DashboardService:
    """DashboardService wired to the stub supervisor, serving an empty workspace."""
    return DashboardService(str(workspace), supervisor=stub_supervisor)  # type: ignore[arg-type]
