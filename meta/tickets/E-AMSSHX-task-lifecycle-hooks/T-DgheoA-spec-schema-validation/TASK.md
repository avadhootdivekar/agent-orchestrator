# TASK: T-DgheoA-spec-schema-validation

## Metadata
- Task ID: `T-DgheoA-spec-schema-validation`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: developer (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-1 (epic)

## Description
Add `$defs/hook` to `specs/workflow.schema.json` and reference it from the task definition's
`pre_hook`/`post_hook` properties, so `ao validate` catches a malformed hook spec the same way it
catches every other malformed task field. Confirm (do not duplicate) that pydantic is the
authoritative gate for an installed wheel (per this schema file's own existing convention — see
e.g. `RegenerateRule.timeout_seconds`'s `ge=1` comment in `models.py` about schema/pydantic
parity).

## Acceptance Criteria
1. `$defs/hook`: `{"type": "object", "additionalProperties": false, "required": ["command"],
   "properties": {"command": {"type": "array", "items": {"type": "string"}, "minItems": 1},
   "timeout_seconds": {"type": "integer", "minimum": 1}, "on_failure": {"enum": ["ignore",
   "fail_task"]}}}`.
2. Task def's `properties` gains `"pre_hook": {"$ref": "#/$defs/hook"}`,
   `"post_hook": {"$ref": "#/$defs/hook"}` — same pattern as the existing `"retries": {"$ref":
   "#/$defs/retryPolicy"}` line.
3. Numeric/enum bounds in the JSON schema match `HookSpec`'s pydantic bounds exactly (same
   min/ge, same enum values) — parity is a repo convention (see `models.py`'s "C-5" comments on
   `RegenerateRule`/`IntegrationSpec` re: schema/pydantic parity).
4. `python -m json.tool specs/workflow.schema.json` (or equivalent) confirms the file is still
   valid JSON; `ao validate` (or the repo's schema-validation test) accepts a workflow with
   `pre_hook`/`post_hook` and rejects one with an empty `command` array / unknown `on_failure`
   value.

## Risks
- Low — additive JSON schema change only.

## Dependencies
- T-AHvmYR (models) — bounds must match exactly.

## Pseudocode / Algorithm
```text
See docs-md/task-lifecycle-hooks-hld.md §3 for the exact HookSpec shape to mirror.
```

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema (JSON/YAML): `specs/workflow.schema.json`.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): N/A.

## Handoff Boundary
- Upstream: T-AHvmYR.
- Downstream: T-fbQIFX's example spec should validate cleanly against this; T-jI3P4p adds a
  schema-validation test case.

## Artifacts
- Docs/comments: `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-DgheoA-spec-schema-validation/`
- Large outputs: N/A
