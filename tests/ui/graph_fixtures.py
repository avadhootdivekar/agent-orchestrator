"""Deterministic synthetic run fixtures for graph e2e testing (E-k3AMEr T-F1caAt).

Provides `write_synthetic_run` to build reproducible run directories with spawn provenance
and snapshots, used for browser smoke tests and e2e validation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from agent_orchestrator.models import (
    RunState,
    SpawnRecord,
    SpecSession,
    TaskRunState,
)


def write_synthetic_run(root: Path, waves: int, fanout: int, *, clock: datetime) -> str:
    """Write a deterministic synthetic run directory.

    Creates an overseer-like shape: checkpoint → fanout units → checkpoint, repeated
    `waves` times. Spawn provenance and spec_sessions are populated.

    Args:
        root: workspace root where `.orchestrator/runs/<run_id>/` is created
        waves: number of checkpoint→fanout cycles (each wave has 1 + fanout tasks)
        fanout: number of units spawned per checkpoint
        clock: fixed datetime for deterministic timestamps

    Returns:
        run_id (a string UUID-like key)

    Artifacts:
        - `<root>/.orchestrator/runs/<run_id>/state.json` with `spawned_by` and `spec_sessions`
        - `<root>/.orchestrator/runs/<run_id>/workflow.snapshot.<sha12>.json`
    """
    run_id = "synth-run-001"
    run_dir = root / ".orchestrator" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Build task state: checkpoints + fanout units across all waves
    tasks: dict[str, TaskRunState] = {}
    spawned_by: dict[str, SpawnRecord] = {}

    # Fixed ISO timestamps for determinism
    t0 = clock
    checkpoint_start_offset = timedelta(seconds=0)
    unit_duration = timedelta(seconds=10)

    for wave in range(1, waves + 1):
        checkpoint_id = f"checkpoint__{wave}"
        checkpoint_started = t0 + checkpoint_start_offset
        checkpoint_ended = checkpoint_started + timedelta(seconds=60)

        # Add the checkpoint task
        tasks[checkpoint_id] = TaskRunState(
            status="succeeded",
            started_at=checkpoint_started.isoformat() + "Z",
            ended_at=checkpoint_ended.isoformat() + "Z",
            attempts=1,
            origin="static",
            cumulative_cost_usd=0.01,
        )

        # Spawn fanout units from this checkpoint
        depends_on_ids = []
        for unit_idx in range(1, fanout + 1):
            unit_id = f"unit__{wave}_{unit_idx}"
            unit_started = checkpoint_ended + timedelta(seconds=unit_idx * 5)
            unit_ended = unit_started + unit_duration

            tasks[unit_id] = TaskRunState(
                status="succeeded",
                started_at=unit_started.isoformat() + "Z",
                ended_at=unit_ended.isoformat() + "Z",
                attempts=1,
                origin="injected",
                cumulative_cost_usd=0.02,
            )

            # Record spawn provenance: this unit was created by the checkpoint
            spawned_by[unit_id] = SpawnRecord(
                parent_task_id=checkpoint_id,
                parent_dispatch_cycle=0,
                origin="injected",
                injected_at=checkpoint_ended.isoformat() + "Z",
                loop_id=None,
                iteration=None,
            )

            depends_on_ids.append(unit_id)

        # Add the next checkpoint (depends on all units of this wave)
        if wave < waves:
            checkpoint_start_offset += timedelta(seconds=300)

    # Add dependencies for checkpoints (each depends on the previous wave's units)
    for wave in range(2, waves + 1):
        checkpoint_id = f"checkpoint__{wave}"

        tasks[checkpoint_id] = TaskRunState(
            status="succeeded",
            started_at=(t0 + timedelta(seconds=(wave - 1) * 300 + 200)).isoformat() + "Z",
            ended_at=(t0 + timedelta(seconds=(wave - 1) * 300 + 260)).isoformat() + "Z",
            attempts=1,
            origin="static",
            cumulative_cost_usd=0.01,
        )

    # Create a minimal valid workflow spec for the snapshot with proper dependencies
    tasks_spec = []
    for wave in range(1, waves + 1):
        checkpoint_id = f"checkpoint__{wave}"
        task_dict = {
            "id": checkpoint_id,
            "agent": "test",
            "instruction": "test.md",
        }

        # Each checkpoint (except the first) depends on the previous wave's units
        if wave > 1:
            task_dict["depends_on"] = [f"unit__{wave - 1}_{i}" for i in range(1, fanout + 1)]

        tasks_spec.append(task_dict)

    minimal_workflow_spec = {
        "version": "1.0",
        "id": "synthetic-graph-test",
        "repo_set": "main",
        "tasks": tasks_spec,
    }

    # Write the spec snapshot
    spec_json = json.dumps(minimal_workflow_spec, sort_keys=True)
    spec_sha = hashlib.sha256(spec_json.encode()).hexdigest()  # Full 64-char digest

    # Filename uses only 12-char truncation (internal to the path layer)
    snapshot_path = run_dir / f"workflow.snapshot.{spec_sha[:12]}.json"
    snapshot_data = {
        "schema_version": 1,
        "run_id": run_id,
        "spec_sha256": spec_sha,
        "written_at": clock.isoformat() + "Z",
        "workflow": minimal_workflow_spec,
    }
    snapshot_path.write_text(
        json.dumps(snapshot_data),
        encoding="utf-8",
    )

    # Build RunState
    completed_at = t0 + timedelta(seconds=waves * 300)
    run_state = RunState(
        run_id=run_id,
        workflow_id="synthetic-graph-test",
        repo_set="main",
        status="succeeded",
        started_at=t0.isoformat() + "Z",
        updated_at=completed_at.isoformat() + "Z",
        tasks=tasks,
        spawned_by=spawned_by,
        spec_sessions=[
            SpecSession(
                session=0,
                started_at=t0.isoformat() + "Z",
                spec_sha256=spec_sha,
            )
        ],
        task_integration={},
        loop_iterations={},
    )

    # Write state.json
    state_path = run_dir / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "run_id": run_state.run_id,
                "status": run_state.status,
                "workflow_id": run_state.workflow_id,
                "repo_set": run_state.repo_set,
                "started_at": run_state.started_at,
                "updated_at": run_state.updated_at,
                "tasks": {
                    tid: {
                        "status": ts.status,
                        "started_at": ts.started_at,
                        "ended_at": ts.ended_at,
                        "attempts": ts.attempts,
                        "origin": ts.origin,
                        "cumulative_cost_usd": ts.cumulative_cost_usd,
                    }
                    for tid, ts in run_state.tasks.items()
                },
                "spawned_by": {
                    cid: {
                        "parent_task_id": sr.parent_task_id,
                        "parent_dispatch_cycle": sr.parent_dispatch_cycle,
                        "origin": sr.origin,
                        "injected_at": sr.injected_at,
                        "loop_id": sr.loop_id,
                        "iteration": sr.iteration,
                    }
                    for cid, sr in run_state.spawned_by.items()
                },
                "spec_sessions": [
                    {
                        "session": ss.session,
                        "started_at": ss.started_at,
                        "spec_sha256": ss.spec_sha256,
                    }
                    for ss in run_state.spec_sessions
                ],
                "task_integration": run_state.task_integration,
                "loop_iterations": run_state.loop_iterations,
            }
        ),
        encoding="utf-8",
    )

    return run_id
