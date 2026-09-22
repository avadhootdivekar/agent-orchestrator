# TASK: T-17PhBS-validate-injected-depends-on

## Metadata
- Task ID: `T-17PhBS-validate-injected-depends-on`
- Epic ID: `E-Grpp0X-injected-task-dag-validation-gap`
- Owner: unassigned
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft (not started)
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-3 (stretch), NFR-1, NFR-2 (see `EPIC.md`)

## Description
1. **Reproduce first (FR-2).** Write an integration test using the `fake` executor that drives an
   `emit_tasks` task whose manifest (`task_manifest_path`) declares an injected `TaskSpec` with a
   `depends_on` value that matches no existing/injected task id. Run it against current code,
   capture and document the actual failure (exception type, where it's raised, how "ugly" —
   this is the concrete "before" evidence FR-1's fix is measured against).
2. **Fix (FR-1).** Add a reference check inside (or called from) `engine.py::_inject`: every
   `depends_on` entry in a newly-injected batch must resolve against
   `{existing task ids} | {ids in this same batch}`. On failure, raise `InjectionError` (reuse
   the existing exception type already used for NFR-6 duplicate-id rejection) naming the
   injecting task id, the bad injected task id, and the unresolved dependency — raised BEFORE any
   task from the batch is appended to `workflow.tasks`/`state.tasks` (all-or-nothing batch
   injection, not partial).
3. **Stretch (FR-3), only if cheap given the above:** extend the same check to an injected task's
   `agent` id and `pre_hook.use`/`post_hook.use` references against the already-declared
   `agents`/`workflow.hooks` registries.
4. Re-run the FR-2 reproduction test — it should now assert a clean `InjectionError` with the
   expected message, not the old ugly failure.

## Acceptance Criteria
1. A committed test demonstrates the pre-fix failure mode (FR-2), then a post-fix test asserts a
   clean, attributable `InjectionError` for the same scenario.
2. A batch injection with one bad `depends_on` among otherwise-valid tasks injects nothing from
   that batch (all-or-nothing), not a partial injection.
3. Existing `emit_tasks`/loop-injection/router tests (`tests/test_engine_routing.py` and any
   dynamic-injection tests) still pass unmodified — no behavior change for valid injections.
4. `ruff check`/`ruff format --check`/`mypy src` clean on touched files.
5. Full existing suite passes (`.venv/bin/python -m pytest -q -m "not real_llm and not
   swebench"`), pass/fail counts reported.

## Risks
- `_inject` is shared across `emit_tasks`, loop-gate cloning, and router expansion origins — the
  added check must not reject an engine-constructed (not agent-authored) loop-clone `depends_on`
  that happens to reference a same-batch sibling; verify against existing loop tests before
  considering this done.

## Dependencies
- None — independently pickup-able.

## Pseudocode / Algorithm
```text
def _inject(self, new, workflow, state, origin, route=None):
    existing = {t.id for t in workflow.tasks}
    batch_ids = {t.id for t in new}
    known = existing | batch_ids
    for spec in new:
        if spec.id in existing:
            raise InjectionError(f"Cannot inject task {spec.id!r}: id already exists")
        for dep in spec.depends_on:
            if dep not in known and dep not in {lp.id for lp in workflow.loops}:
                raise InjectionError(
                    f"Injected task {spec.id!r} (from {origin}) depends_on unknown "
                    f"task {dep!r} — not in the existing workflow or this injection batch"
                )
    # only after the whole batch validates clean: append/register each spec
    for spec in new:
        workflow.tasks.append(spec)
        existing.add(spec.id)
        state.injected_tasks.append(spec)
        state.tasks[spec.id] = TaskRunState(origin=origin, route=route)
```

## Schemas / Interface Notes
- Interface / API (Python protocol/ABC or CLI/HTTP): `Orchestrator._inject` (engine.py); no
  public CLI/schema surface change.
- Spec / data schema (JSON/YAML): no `specs/*.schema.json` change — `task_manifest_path`'s
  `{"tasks": [<TaskSpec>...]}` shape is unchanged, this only adds validation of its content.
- Triggers / events (cron/event): N/A
- Artifacts (inputs/outputs by path): N/A — internal validation logic only.

## Handoff Boundary
- Upstream: `meta/learnings.md` `LRN-20260704-emit-tasks-skips-validation` (original finding);
  `src/agent_orchestrator/spec.py::cross_validate` (the equivalent static-path check to mirror).
- Downstream: none — leaf fix.

## Artifacts
- Docs/comments: `meta/tickets/E-Grpp0X-injected-task-dag-validation-gap/T-17PhBS-validate-injected-depends-on/`
- Large outputs: none expected.
