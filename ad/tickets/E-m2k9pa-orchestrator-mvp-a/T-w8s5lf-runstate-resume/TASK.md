# TASK: T-w8s5lf-runstate-resume

## Metadata
- Task ID: `T-w8s5lf-runstate-resume`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `1–2 days`

## Requirements Mapping
- Requirement IDs: FR-6, NFR-3

## Description
Implement `runstate.py`: `RunState`/`TaskRunState` models, atomic JSON persistence, and resume/idempotency
logic. (LLD §6.)

## Acceptance Criteria
1. Run-state persisted to `{workspace_root}/.orchestrator/runs/{run_id}/state.json` via atomic write (temp + `os.replace`).
2. `new_run(workflow)` creates a run with deterministic `run_id = f"{wf.id}-{utc_compact}"`.
3. `resume(run_id)`: a task is **skipped** iff `status=="succeeded"` AND all declared outputs exist; else re-queued.
4. Idempotency on fresh run: if `skip_if_outputs_exist` and all outputs exist → `skipped`.
5. Tests (fixed clock): persist→reload round-trip; resume skips done tasks; idempotent skip; partial-failure resume reruns only failed/pending.

## Risks
- Stale state vs disk truth — always re-check output existence, don't trust status alone.

## Dependencies
- Upstream: T-d3v7hn (exists). Downstream: T-h7k3qm, T-e4u8zx.

## Pseudocode / Algorithm
```text
save(state): write tmp; os.replace(tmp, state.json)
should_skip(task,state): state.tasks[id].status==succeeded and all(exists(o) for o in outputs)
```

## Schemas / Interface Notes
- Interface: `RunStateStore.new_run/load/save`, `should_skip` (LLD §6).
- Artifacts: stores only statuses/paths/timestamps — no payload contents (NFR-1).

## Handoff Boundary
- Upstream: artifact existence. Downstream: engine run/resume; CLI status.

## Artifacts
- Docs/comments: this folder.
