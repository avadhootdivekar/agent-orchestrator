# TASK: T-ag4c05-json-schema-output-posthook

## Metadata
- Task ID: `T-ag4c05-json-schema-output-posthook`
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P2`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Description
A built-in post-hook validating a task output against a JSON schema path; on failure retries once feeding the validator message back. Replaces project-written validators such as FinPlan's `validate-findings.py` for the common case.

## Acceptance Criteria
1. Hook validates output and fails the task with a clear message when invalid.
2. Retry-once re-prompts with the validator message; second failure is final.
3. Documented; tests with fake executor.

## Risks
- Do not break existing `post_hook` semantics.

## Dependencies
None.

## Handoff Boundary
- Upstream: None.
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag4c05-json-schema-output-posthook/`
- Large outputs: `output/...`
