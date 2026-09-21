# TASK: T-DgheoA-spec-schema-validation

## Metadata
- Task ID: `T-DgheoA-spec-schema-validation`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: developer (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-1 (epic)

## Description
Add `$defs/hook` (the workflow-root registry entry) + `$defs/hookRef` (the task-level reference —
**Rev 2 shape, see HLD §3**) to `specs/workflow.schema.json`, add a `"hooks"` property to the
workflow-root schema, and reference `hookRef` from the task definition's `pre_hook`/`post_hook`
properties — so `ao validate` catches a malformed hook spec or an unresolvable hook name the same
way it catches every other malformed field. Also add the new `spec.py::cross_validate` rule this
Rev 2 shape requires (every `pre_hook.use`/`post_hook.use` must resolve to a `WorkflowSpec.hooks`
key) — **this cross-validation rule is new in Rev 2**; Rev 1 of this ticket assumed no
cross-validation change would be needed, which was corrected by early-gate `architect` review
once the registry design was adopted.

## Acceptance Criteria
1. `$defs/hook` (the command): `{"type": "object", "additionalProperties": false, "required":
   ["command"], "properties": {"type": {"const": "command"}, "command": {"type": "array",
   "items": {"type": "string"}, "minItems": 1}, "timeout_seconds": {"type": "integer",
   "minimum": 1}, "on_failure": {"enum": ["ignore", "fail_task"]}}}`.
2. `$defs/hookRef` (the task-level reference): `{"type": "object", "additionalProperties": false,
   "required": ["use"], "properties": {"use": {"type": "string"}, "on_failure": {"enum":
   ["ignore", "fail_task"]}}}`.
3. Workflow-root `properties` gains `"hooks": {"type": "object", "additionalProperties": {"$ref":
   "#/$defs/hook"}, "default": {}}` (same shape convention as how `agents`/`reposets` registries
   are declared in their own schema files — check `specs/agents.schema.json` for the exact
   registry-property idiom to mirror).
4. Task def's `properties` gains `"pre_hook": {"$ref": "#/$defs/hookRef"}`,
   `"post_hook": {"$ref": "#/$defs/hookRef"}` — same pattern as the existing `"retries": {"$ref":
   "#/$defs/retryPolicy"}` line.
5. Numeric/enum bounds in the JSON schema match `HookSpec`/`HookRef`'s pydantic bounds exactly —
   parity is a repo convention (see `models.py`'s "C-5" comments on `RegenerateRule`/
   `IntegrationSpec` re: schema/pydantic parity).
6. `spec.py::cross_validate`: new rule — every `TaskSpec.pre_hook.use`/`post_hook.use` value
   must be a key in `WorkflowSpec.hooks`, else `SpecValidationError` naming the task id and the
   unresolved hook name (same shape as the existing "unknown agent id" check).
7. `python -m json.tool specs/workflow.schema.json` (or equivalent) confirms the file is still
   valid JSON; `ao validate` (or the repo's schema-validation test) accepts a workflow with a
   `hooks` registry + `pre_hook`/`post_hook` references and rejects: an empty `command` array, an
   unknown `on_failure` value, AND (the new cross-validation case) a `pre_hook`/`post_hook` that
   references a hook name not present in `hooks`.

## Risks
- Low — additive JSON schema change + one new, narrowly-scoped cross-validation rule.

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
