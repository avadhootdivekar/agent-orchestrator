# TASK: T-Gi4Mn8-general-instructions

## Metadata
- Task ID: `T-Gi4Mn8-general-instructions`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-GI1, FR-GI2, NFR-1`

## Description
Add general instructions: instruction files declared once per workspace and applied to every task of every run, even when `ao run` is invoked with no related flag.

## Acceptance Criteria
1. `general_instructions` accepted in `.ao/config.yaml`, `AO_GENERAL_INSTRUCTIONS` (os.pathsep-separated), repeatable `--general-instruction`, and `WorkflowSpec.general_instructions`.
2. All four layers are merged as a **union** (not a precedence chain), de-duplicated, broadest-scope-first.
3. Every task's prompt names the instruction paths — including for agent configs whose `prompt_template` has no `{general_instructions}` placeholder.
4. Paths resolve through `ArtifactStore.resolve`; a path escaping the workspace is dropped with a log warning and does NOT fail the run.
5. Instruction bytes count toward the token estimate so the budget gate stays honest.
6. A workspace configuring none is byte-identical to pre-feature behaviour.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- None

## Schemas / Interface Notes
- `models.WorkflowSpec.general_instructions`, `models.TaskContext.general_instruction_paths`
- `cli.resolve_general_instructions(cli_paths, workflow_paths) -> list[str]`
- `engine.Orchestrator(general_instructions=...)` + `_resolve_general_instructions(workflow)`
- `executors/prompt.py::build_prompt(ctx) -> str` (shared by both executors)
- `specs/workflow.schema.json` → `general_instructions`

## Handoff Boundary
- Upstream: `Epic requirements (user prompt)`
- Downstream: `T-Pr7Wt3-run-prompt`

## Evidence
- `tests/test_general_instructions.py` — 25 tests (layer merge, prompt assembly, engine wiring, estimator, traversal drop).

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Gi4Mn8-general-instructions/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
