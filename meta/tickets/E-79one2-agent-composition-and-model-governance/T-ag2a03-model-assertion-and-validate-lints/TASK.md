# TASK: T-ag2a03-model-assertion-and-validate-lints

## Metadata
- Task ID: `T-ag2a03-model-assertion-and-validate-lints`
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P1`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Description
Close the 'did it really run Haiku?' gap. Extend the existing model policy (`forbidden_task_models`, task model policy) rather than adding a parallel `policy` block: add `require_model` (exact) on agent/task, post-run assertion comparing the CLI-reported model to the resolved model (`failed` or `warn`, configurable, `model_mismatch: wanted X got Y`), pass `--fallback-model` only when explicitly set. `ao validate` lints: unpinned model with no workflow default; agent `model` vs `command_template --model` conflict; optional lint when a repo's `.claude/agents/*.md` `model:` differs from the `ao` agent of the same name. Also surface planned model beside actual in usage / `ao report-timing` (request item 4.8).

## Acceptance Criteria
1. A task resolved to `claude-haiku-5-5` whose CLI output reports another model ends `failed` with `model_mismatch`; matching run is `ok` (fake-executor test + one real-CLI smoke test).
2. `ao validate` warns on unpinned model and on `--model` vs `AgentSpec.model` conflict.
3. Usage/timing report shows planned vs actual model and cost by model.
4. Existing workflows run unchanged; warnings not promoted to errors by default.

## Risks
- OPEN: does `--output-format json` for `-p` runs expose the executing model reliably? What does the CLI do on an unknown alias, unavailable model or overload (error vs silent fallback)? Run the experiment and record results in this ticket before stating guarantees.

## Dependencies
Discovery spike first (see notes).

## Handoff Boundary
- Upstream: Discovery spike first (see notes).
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag2a03-model-assertion-and-validate-lints/`
- Large outputs: `output/...`
