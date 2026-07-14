# TASK: T-f9j2vd-integration-ci-docs

## Metadata
- Task ID: `T-f9j2vd-integration-ci-docs`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `1–2 days`

## Requirements Mapping
- Requirement IDs: NFR-1, NFR-3, NFR-4 + end-to-end of FR-1..FR-9

## Description
End-to-end integration: run the sample multi-repo workflow to completion with `FakeExecutor`, prove
resume/cycle/malformed handling, assert the NFR-1 hygiene invariant, and finalize CI gates + docs sync.
(LLD §11.)

## Acceptance Criteria
1. E2E: `specs/examples/workflow.json` runs design→implement→test to `succeeded` with `FakeExecutor` writing declared outputs.
2. Resume test: kill after `implement`, resume → only `test` runs.
3. Negative tests: cyclic spec → `CycleError`; malformed spec → `SpecValidationError`; missing input → failure.
4. **Hygiene invariant**: static check that `engine.py` performs no content read of artifact/instruction paths; `RecordingExecutor` confirms `TaskContext` carries no file contents.
5. CI green: `ruff`, `mypy`, `pytest` (unit+integration), schema-validation; README/HLD/LLD links updated.

## Risks
- Flaky subprocess tests — integration uses `FakeExecutor`, not real `claude`.

## Dependencies
- Upstream: T-h7k3qm, T-e4u8zx (all prior). Downstream: epic done.

## Pseudocode / Algorithm
```text
tmp workspace -> write reposet/agents/workflow -> ao run -> assert outputs + statuses
resume scenario; negative specs; hygiene assertions; wire CI steps
```

## Schemas / Interface Notes
- Interface: exercises CLI + engine end-to-end.
- Artifacts: temp workspace; outputs under `output/` per example.

## Handoff Boundary
- Upstream: full stack. Downstream: epic completion / demo.

## Artifacts
- Docs/comments: this folder. Large outputs: `output/` (run artifacts).
