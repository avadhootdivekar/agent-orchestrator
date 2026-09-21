# TASK: T-AHvmYR-hook-schema-models

## Metadata
- Task ID: `T-AHvmYR-hook-schema-models`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: developer (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-1, FR-5 (epic)

## Description
Add the pydantic model surface for task lifecycle hooks to `src/agent_orchestrator/models.py`,
additive only (see HLD §3-4, §10 change-scope table). No existing field/behavior changes.

## Acceptance Criteria
1. `HookOnFailure = Literal["ignore", "fail_task"]`; `HookStatus = Literal["passed", "failed",
   "error", "timed_out"]`.
2. `DEFAULT_HOOK_TIMEOUT_SECONDS: int = 120`; `DEFAULT_PRE_HOOK_ON_FAILURE: HookOnFailure =
   "fail_task"`; `DEFAULT_POST_HOOK_ON_FAILURE: HookOnFailure = "ignore"` — named constants, no
   magic literals at call sites (CLAUDE.md rule).
3. `HookSpec(BaseModel)`: `command: list[str] = Field(min_length=1)`,
   `timeout_seconds: int = Field(default=DEFAULT_HOOK_TIMEOUT_SECONDS, ge=1)`,
   `on_failure: HookOnFailure | None = None`.
4. `resolve_hook_on_failure(hook: HookSpec, kind: Literal["pre_hook", "post_hook"]) ->
   HookOnFailure` — the ONE place the kind-specific default is resolved (mirrors
   `resolve_task_isolation`/`resolve_effective_agent`'s docstring convention: "the ONE place...").
5. `HookOutcome(BaseModel)`: `kind: Literal["pre_hook", "post_hook"]`, `status: HookStatus`,
   `exit_code: int | None = None`, `duration_ms: int | None = None`, `detail: dict = {}`,
   `error: str | None = None`.
6. `TaskSpec.pre_hook: HookSpec | None = None`, `TaskSpec.post_hook: HookSpec | None = None`.
7. `TaskResult.pre_hook_result: HookOutcome | None = None`,
   `TaskResult.post_hook_result: HookOutcome | None = None`.
8. `TaskRunState.pre_hook_result: HookOutcome | None = None`,
   `TaskRunState.post_hook_result: HookOutcome | None = None`.
9. Every new field defaults such that a pre-epic `WorkflowSpec`/`TaskSpec`/`TaskResult`/
   `TaskRunState` JSON (no `pre_hook`/`post_hook`/`*_result` keys) still parses unchanged
   (backward-compat / NFR-5-style guarantee already used elsewhere in this file).
10. `ruff check`/`ruff format --check`/`mypy` clean on the diff.

## Risks
- `models.py` is imported everywhere; a typo/incompatible default would break unrelated tests —
  mitigated by running the full suite after this change (see T-jI3P4p), not just new tests.

## Dependencies
- None (first task; everything else depends on this).

## Pseudocode / Algorithm
```text
See docs-md/task-lifecycle-hooks-hld.md §3-4 for exact field shapes/defaults.
```

## Schemas / Interface Notes
- Interface / API: pydantic v2 models, `models.py`.
- Spec / data schema (JSON/YAML): consumed by `specs/workflow.schema.json` (T-DgheoA, separate
  task — do not edit the JSON schema file here).
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): N/A (models only).

## Handoff Boundary
- Upstream: HLD `docs-md/task-lifecycle-hooks-hld.md` §3-4.
- Downstream: T-lzQEyy (engine dispatch) imports these models; T-DgheoA (schema) mirrors them in
  JSON Schema.

## Artifacts
- Docs/comments: `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-AHvmYR-hook-schema-models/`
- Large outputs: N/A
