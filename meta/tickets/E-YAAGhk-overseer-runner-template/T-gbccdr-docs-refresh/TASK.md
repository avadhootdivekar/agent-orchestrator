# TASK: T-gbccdr-docs-refresh

## Metadata
- Task ID: `T-gbccdr-docs-refresh`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: architect
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft (post-implementation, mandatory)
- Estimate: 1 day (8 h)

## Requirements Mapping
- Requirement IDs: FR-14 (docs accuracy); the Phase 6 reconciliation mandate
- Design: `docs-md/overseer-runner-hld.md` §25

## Description
After every other task (except the optional T-zLHc7Q) has merged, reconcile the documentation with
the **implemented** code. Record each deviation from the design explicitly rather than silently
rewriting history.

1. `docs-md/overseer-runner-hld.md`: set the status to "Implemented". Add a "Deviations from design"
   section listing each difference found by the tasks (for example, the actual e2e (e) behavior and
   the rule set as shipped), and update the §8/§13 shapes to match the code.
2. `docs-md/adr/ADR-0016-…md`: set the status to Accepted (implemented) and add any follow-ups.
3. `docs-md/workflow-templates-hld.md`: add `overseer-runner` to the builtin list, with a pointer.
4. `docs-md/guide-dynamic-task-injection.md`: document the G5 settle ordering (injection persisted
   before breakers) and the resume consequence.
5. `.claude/skills/workflow-authoring/SKILL.md`, in "Common failure modes" or "Static vs. dynamic":
   the recursive wave pattern (no static tail after recursion, D8), the breaker-latch-on-resume
   caution, and when to reach for `overseer-runner`.
6. `routed-runner/README.md`: one line noting that G5 fixed its latent emitter-boundary exposure.
7. Fold in the tuning notes from T-23yMMB and the security notes from T-3FlD46.

## Acceptance Criteria
1. Every file listed is updated. Each claim added is verified against the merged code, with
   file:line cites in STATUS.md for at least the G5 ordering, the hook names, the rule ids, and the
   param defaults.
2. The "Deviations from design" section exists (it may say "none", with evidence).
3. A grep check shows no stale Rev 1 statements remain. In particular, no claim that plain resume
   after a backstop reaches close-out, and no `wave_signature_repeat` shown as shipped.
4. Epic `EPIC.md` and `STATUS.md` are updated to Done only after this task, consistent with all
   task statuses.

## Risks
- Doc drift if done before all merges. It is sequenced last.

## Dependencies
- All implementation and test tasks, T-23yMMB, and T-3FlD46.

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: all tasks.
- Downstream: epic closure (dev-epic).

## Artifacts
- The docs listed above.
