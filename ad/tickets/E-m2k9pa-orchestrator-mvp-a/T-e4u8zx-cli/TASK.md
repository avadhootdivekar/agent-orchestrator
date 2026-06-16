# TASK: T-e4u8zx-cli

## Metadata
- Task ID: `T-e4u8zx-cli`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `1 day`

## Requirements Mapping
- Requirement IDs: FR-9, NFR-3

## Description
Implement `cli.py` (typer): `validate`, `run`, `resume`, `status`. Config locations via flags/env only.
(LLD §10.)

## Acceptance Criteria
1. `ao validate --workflow ... [--reposets --agents]` → exit 0 / non-zero with failing schema path or cycle.
2. `ao run --workflow ...` executes and prints a compact per-task status table (no payloads).
3. `ao resume --run-id <id>` resumes from persisted state.
4. `ao status --run-id <id>` prints current RunState summary.
5. Tests: CLI invoked in-process (typer runner) over example specs with `FakeExecutor`; exit codes asserted.

## Risks
- Leaking payloads into stdout — print statuses/paths only.

## Dependencies
- Upstream: T-h7k3qm, T-w8s5lf. Downstream: T-f9j2vd.

## Pseudocode / Algorithm
```text
validate: load+validate -> ok/err
run: load -> Orchestrator.run -> print table
resume: load state -> Orchestrator.run(run_state=state)
status: load state -> print table
```

## Schemas / Interface Notes
- Interface: typer commands; flags/env for paths (`AO_*`).
- Artifacts: prints statuses/paths only.

## Handoff Boundary
- Upstream: engine + run-state. Downstream: users / integration tests.

## Artifacts
- Docs/comments: this folder.
