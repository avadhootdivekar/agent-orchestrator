# EPIC: E-Grpp0X-injected-task-dag-validation-gap

## Metadata
- Epic ID: `E-Grpp0X-injected-task-dag-validation-gap`
- Title: Dynamically-injected tasks (`emit_tasks`) skip DAG cross-validation — unhandled crash on a bad `depends_on`
- Owner: dev-epic (spun off during Epic C / C0 audit, not implemented here)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft (backlog)

## Summary
- Goal: Track a real, verified engine gap — a runtime-injected `TaskSpec` (via
  `emit_tasks`/`task_manifest_path`) is never re-run through `spec.py::cross_validate`, so an
  injected manifest with a bad `depends_on` id degrades ungracefully instead of failing with a
  clean `SpecValidationError`, the same way the equivalent authoring mistake would fail at
  `ao validate` time for a static task.
- Found while: Epic C (`E-DOiDqE-workflow-authoring-skill`) C0 reliability/failure-mode audit.
  Originally flagged by `meta/learnings.md`'s `LRN-20260704-emit-tasks-skips-validation` entry
  (2026-07-04) as producing an uncaught `KeyError`; independently re-verified against current
  code in this pass (see Evidence) rather than taken on the learning's word alone, since the
  underlying code has moved substantially since 2026-07-04 (Epics A/B, task isolation, parallel
  execution all landed since). This is unrelated to Epic C's skill-authoring deliverable (it is
  an engine/DAG-validation defect, not spec-authoring guidance), so per Epic C's scope rules it
  is spun off here rather than folded into C's work. It IS, however, exactly the kind of failure
  mode the new workflow-authoring skill should warn authors about (routing/`emit_tasks` tasks
  need especially careful manifest construction since the engine does not double-check them) —
  cross-referenced from the skill, not fixed there.
- Scope In (MVP, if picked up): make an injected task's `depends_on`/`agent`/hook references
  fail cleanly (a structured, attributable error) instead of degrading into a generic crash or
  silent mis-scheduling.
- Scope Out: re-running full `cross_validate` (all its rules, e.g. router/breaker/loop
  cross-checks) against every injection — likely overkill and possibly wrong for some rules that
  are inherently whole-workflow-scoped; scope the fix to what's actually unsafe for an injected
  task specifically (see Requirements).

## Requirements

### Functional
- FR-1: `engine.py::_inject` (or a new validation step it calls) checks that every injected
  `TaskSpec.depends_on` entry resolves to either an already-existing task id (workflow or
  previously-injected) or a task id present in the *same* injection batch (self-referential
  batches are legitimate — an emitted manifest can declare internal dependencies). An
  unresolvable `depends_on` raises a structured `InjectionError` (the same exception `_inject`
  already raises for NFR-6 duplicate-id rejection — extend its use, don't invent a new
  exception type) naming the injecting task, the injected task id, and the unresolved dep —
  **before** the bad spec is appended to `workflow.tasks`/`state.tasks`, so a partially-injected,
  partially-invalid batch never reaches the scheduler.
- FR-2: Confirm (write a test either way) what currently happens end-to-end when an unknown
  `depends_on` reaches `dag.py::build_dag`/`topological_order` today — `build_dag` was observed
  (this audit) to tolerate an unknown dep by inserting a phantom adjacency entry rather than
  raising immediately (line ~162-164), deferring the failure to whatever downstream code first
  assumes every graph node has a matching `TaskSpec`. Document the actual failure mode (which
  exception, where, how "ugly") as a regression test BEFORE the fix, so FR-1's fix has a
  concrete "was broken like this, now fails clean like this" before/after.
- FR-3 (stretch, if cheap): the same clean-failure treatment for an injected task's `agent` id
  and `pre_hook`/`post_hook.use` references (the same class of "unchecked reference" gap
  `cross_validate` already guards for the static path — see `spec.py` lines ~129-149).
- NFR-1: The fix must not weaken or duplicate `cross_validate`'s existing static-path behavior —
  additive only, scoped to the injection call site.
- NFR-2: No change to the trust boundary AC-15 already establishes for injected tasks (an
  injected task may only *reference* pre-declared names — agent ids, hook names — never invent
  new argv/commands); this fix makes reference-checking *stricter* (fail clean instead of crash
  ugly), not looser.

## Task List
- [ ] `T-17PhBS-validate-injected-depends-on` — Reproduce the current failure mode with a test,
  then implement FR-1 (and FR-3 if cheap) with regression coverage.

## Risks and Dependencies
- Touches `engine.py::_inject`, a function shared by three injection origins (`emit_tasks`
  expansion, loop-gate cloning, and — per its own docstring — possibly router expansion); keep
  the added check generic across all three, not `emit_tasks`-specific, and verify no existing
  loop/router injection test starts failing once real validation is added (loop-cloned tasks'
  `depends_on` values are engine-constructed, not agent-authored, so they should already be
  valid — a regression here would itself be informative).
- Low urgency: no evidence of this manifesting in a real captured run in this repo's own
  history (time-boxed audit did not find one); it is a real, verified code-path gap, not an
  observed production incident.

## Links
- Source finding: `meta/learnings.md` `LRN-20260704-emit-tasks-skips-validation`.
- Re-verified against: `src/agent_orchestrator/engine.py:4084` (`_inject`),
  `src/agent_orchestrator/dag.py:133` (`build_dag`, phantom-adjacency tolerance at ~162-164),
  `src/agent_orchestrator/spec.py:82-136` (`cross_validate`'s existing static-path
  `depends_on`/agent/hook reference checks, confirmed present and NOT re-invoked after
  injection).
- Spun off from: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-trl41B-reliability-audit/`
  (Epic C, C0).
- Related: `docs-md/guide-dynamic-task-injection.md`, `docs-md/granular-task-decomposition-hld.md`
  (both describe the injection mechanism this gap lives in).
- Output artifacts (if any): none yet — not implemented in this pass.
