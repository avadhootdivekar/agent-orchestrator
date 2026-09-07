# TASK: T-Sc7Rm2-isolation-schema-models

## Metadata
- Task ID: `T-Sc7Rm2-isolation-schema-models`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-10 (field), NFR-5 (see `../EPIC.md`) · Design: HLD §10, §11 M2

## Description
Add every new field this epic needs to the pydantic models, the JSON Schema, the run state and the
cross-validator — **inert**: nothing consumes them yet. Landing this first unblocks every other task
and keeps the schema change in one reviewable diff.

Files you own:
- `src/agent_orchestrator/models.py` (edit — new Literals, `TaskSpec.isolation`/`touches`,
  `WorkflowDefaults.isolation`, `IntegrationSpec`, `ResolverConfig`, `RegenerateRule`,
  `SchedulingSpec`, `WorkflowSpec.integration`/`scheduling`, `TaskContext.env`,
  `TaskIntegrationState`, `RunIntegrationState`, `RunState.integration`/`task_integration`,
  `resolve_task_isolation`, new constants)
- `specs/workflow.schema.json` (edit)
- `src/agent_orchestrator/spec.py` (edit — cross-validation rules V1-V8 only)
- `src/agent_orchestrator/runstate.py` (edit — `prepare_resume` preservation + `write_status`
  additions only)
- `tests/test_isolation_models.py`, `tests/test_isolation_spec_validation.py` (new)

Do NOT touch: `engine.py`, `artifacts.py`, `cli.py`, `executors/`, `templates/`, or any `isolation/`
module.

## Acceptance Criteria
1. Models exactly as HLD §10.2, with `DEFAULT_VERIFY_TIMEOUT_SECONDS = 1800`,
   `DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS = 1800`, `DEFAULT_HOTSPOTS_PATH = ".ao/hotspots.json"`
   as named module constants (no magic literals). Defaults are `TaskSpec.isolation = "inherit"`,
   `WorkflowDefaults.isolation = "none"`, `IntegrationSpec.ladder = ["auto","mechanical","llm","rerun"]`.
2. `specs/workflow.schema.json` accepts every new field and **rejects** unknown ones
   (`additionalProperties: false` preserved at every level). A test loads a spec using all new fields
   and one using an unknown field, asserting accept/reject respectively.
3. `resolve_task_isolation(task, workflow)` implements HLD §11 M2: `emit_tasks`, a router task, or a
   loop-gate task always resolves to `"none"` (with a single warning when the task explicitly asked
   for `"worktree"`); `"inherit"` takes `workflow.defaults.isolation`; otherwise the task's own value.
   Table-driven test over all 3 x 4 combinations.
4. Cross-validation rules V1-V8 from HLD §10.4 are implemented in `spec.cross_validate` with
   **fatal vs warning** exactly as tabled, each with its own test:
   - V1 `strategy: "merge"` → fatal, message names it as reserved;
   - V2 `"llm"` in ladder without `resolver_agent` → fatal;
   - V3 unknown `resolver_agent` → fatal;
   - V4 structural task asking for `worktree` → warning;
   - V5 `integration` configured but nothing isolated → warning;
   - V6 `touches` entry that is absolute or contains `..` → fatal;
   - V7 `max_resolver_attempts > 0` with `"llm"` absent → warning;
   - V8 a `RepoRef` path inside another member's worktree → fatal;
   - V9 a task id that sanitizes to the reserved component `integration` → fatal (it would
     collide with the integration branch `ao/<run_id>/integration`).
5. **NFR-5 both directions**, each with an explicit test: (a) a `state.json` captured before this
   change loads and yields `RunState.integration.active is False` and `task_integration == {}`;
   (b) a new `RunState` serializes and re-loads identically (round trip); (c) a workflow JSON with no
   new keys produces the documented defaults.
6. `prepare_resume` **preserves** `state.integration` and `state.task_integration` verbatim, and
   normalizes any `task_integration[tid].status == "integrating"` to `"pending"` while keeping `mode`.
   Test: build a state with one `integrating` task, resume, assert status/mode.
7. `write_status` emits the new top-level `integration` block (`active`, `branch`, `heads`, and counts
   of `integrated`/`conflict`/`failed`) and per-task `integration_status`, `tier_reached`,
   `conflicted_count`. A test asserts the exact key set so a later dashboard change cannot drift.
8. An `emit_tasks` manifest entry carrying `isolation` and `touches` survives
   `artifacts.read_task_manifest`'s `TaskSpec(**t)` construction unchanged (test with a real manifest
   file) — **no parser change should be needed; confirm by test rather than by assumption**.
9. A loop-body clone (`engine._clone_body`, `__iter` ids) copies `isolation` and `touches` from its
   source task. If it does not today, fix it in `models.py`-adjacent code only and flag the
   `engine.py` line in your handoff for `T-En8Hd4` — **do not edit `engine.py` in this task**.
10. `uv run pytest -q` fully green with a recorded before/after count; `ruff` clean; `uv run mypy src`
    zero new errors. **No behaviour change**: the pre-existing engine suite passes unedited.

## Risks
- `additionalProperties: false` means a partial schema edit silently breaks every spec that uses a new
  field. Mitigation: AC-2's accept/reject pair.
- `specs/*.schema.json` is **not packaged into the wheel** and `config._validate_against_schema`
  silently no-ops when the file is absent — so for an installed `ao`, pydantic and `cross_validate`
  are the only gates. Every rule must exist in Python, not only in JSON Schema. State this explicitly
  in your handoff.
- `prepare_resume` currently replaces non-terminal `TaskRunState` objects wholesale
  (`runstate.py:212`); putting integration data on `TaskRunState` would silently lose it. That is why
  it lives in `RunState.task_integration`. Do not "simplify" it back.

## Dependencies
- Upstream: none (parallel with `T-Gt4Pw8`).
- Downstream: every other task in the epic.

## Pseudocode / Algorithm
```text
See HLD §10.2 (models), §10.3 (run state), §10.4 (validation) and §11 M2
(resolve_task_isolation) — those are the implementation contract, field for field.
```

## Schemas / Interface Notes
- Interface / API: `resolve_task_isolation(task, workflow) -> Literal["none","worktree"]` — locked name.
- Spec / data schema: `specs/workflow.schema.json` `$defs/integration`, `$defs/scheduling`, plus
  `isolation`/`touches` on `$defs/task` and `isolation` on `defaults` — HLD §10.1 verbatim.
- Triggers / events: none.
- Artifacts: none.

## Handoff Boundary
- Upstream: none.
- Downstream: publish the final model field names and defaults in `STATUS.md`. `T-En8Hd4` and
  `T-Ov9Bt5` read the merged `models.py`.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Sc7Rm2-isolation-schema-models/`
- Large outputs: none
