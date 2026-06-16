# TASK: T-h7k3qm-engine-orchestration

## Metadata
- Task ID: `T-h7k3qm-engine-orchestration`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `2 days`

## Requirements Mapping
- Requirement IDs: FR-1, FR-6, NFR-1, NFR-3

## Description
Implement `engine.py` `Orchestrator` composing DAG + executor + artifact store + run-state + retries to
drive a workflow to completion, keeping orchestrator context to **paths/statuses only**. (LLD §8.)

## Acceptance Criteria
1. `run(workflow, reposets, agents, run_state=None)`: build DAG (raises `CycleError`), topo-order, iterate.
2. Per task: skip-if-resumable/idempotent; assert inputs exist (`MissingInputError`→failed); run-with-retries; verify declared outputs exist; persist state after each.
3. Stop run on first hard failure (MVP); finalize run status; return `RunState`.
4. Builds `TaskContext` with **paths/ids only**; engine never reads instruction/artifact contents (static + runtime test).
5. Tests with `FakeExecutor`: linear success; missing-input failure; output-not-produced→failed; resume continues; NFR-1 hygiene assertion.

## Risks
- Hidden content reads creeping in — enforce via hygiene test (T-f9j2vd) + code review.

## Dependencies
- Upstream: T-9xc2bk, T-p6m4qz, T-d3v7hn, T-w8s5lf, T-b2n6rk. Downstream: T-e4u8zx, T-f9j2vd.

## Pseudocode / Algorithm
```text
see LLD §8 run(): build_dag -> topo -> per-task skip/assert-inputs/run-retries/verify-outputs/save -> finalize
```

## Schemas / Interface Notes
- Interface: `Orchestrator.run/resume` (LLD §8).
- Artifacts: passes paths to executor; checks existence via ArtifactStore; no content in engine memory.

## Handoff Boundary
- Upstream: all core modules. Downstream: CLI + integration.

## Artifacts
- Docs/comments: this folder.
