# TASK: T-Pr7Wt3-run-prompt

## Metadata
- Task ID: `T-Pr7Wt3-run-prompt`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-P1, NFR-1`

## Description
Let a run carry a free-text prompt: `ao run --prompt "..."` writes it into the workflow's declared `prompt_path` before the run starts. This is the CLI mechanism the dashboard's prompt box drives.

## Acceptance Criteria
1. `WorkflowSpec.prompt_path` added to the model and JSON schema.
2. `--prompt` writes the text; `--prompt-file` copies a file's contents; the two are mutually exclusive.
3. The write happens BEFORE the run, so a task listing `prompt_path` in `inputs` sees it and its missing-inputs gate passes.
4. A prompt supplied for a workflow with no `prompt_path` is a hard error, never a silent drop.
5. A `prompt_path` escaping the workspace is rejected.
6. `ao resume` deliberately has NO `--prompt` (resumability invariant); documented in its docstring.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- T-Gi4Mn8-general-instructions

## Schemas / Interface Notes
- `models.WorkflowSpec.prompt_path`
- `cli._apply_prompt(wf, store, prompt, prompt_file)`
- `specs/workflow.schema.json` → `prompt_path`

## Handoff Boundary
- Upstream: `T-Gi4Mn8-general-instructions`
- Downstream: `T-Db2Hs5-dashboard-backend`

## Evidence
- `tests/test_e2e_cli_prompt_and_instructions.py` — 20 tests via `CliRunner`.

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Pr7Wt3-run-prompt/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
