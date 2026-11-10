# TASK: T-Hq3vXd-docs-refresh

## Metadata
- Task ID: `T-Hq3vXd-docs-refresh`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: architect agent (sign-off) + developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-11
- Status: Partial (see STATUS)
- Estimate: 1.5 days

## Requirements Mapping
- Requirement IDs: all (post-implementation reconciliation, architect Phase 6)

## Description
Reconcile `docs-md/` with what was actually built, including every deviation from the HLD and every early/final-gate
correction. Mark complete only after checking docs against the merged code, not against the HLD.

Files you own:
- `docs-md/scheduled-run-governance-hld.md` (status line → "Implemented"; an "As built" section listing deviations)
- `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md` (status → Accepted; "Implementation outcome")
- `docs-md/ai-epics/E-Qp4Zm8-scheduled-run-governance.md` (evidence log, final traceability)
- `docs-md/scheduler-triggers-hld.md` (**cross-links only**: a note in §6.3 pointing to the governance step, and a
  §5.1 field-reference addendum row for `limits`/`prompt_sha256`/`week_start`. Coordinate with E-Sc9Rt4's
  `T-Dc6Zr2` if it is still open)
- `docs-md/run-options-and-limits-guide.md` (`--cost-cap-usd`, sticky-on-resume rule, overshoot bound)
- `docs-md/guide-sentinel.md` (the sentinel budget now also derives from `--cost-cap-usd`)
- `meta/ROADMAP.md` (status rows)
- this epic's `EPIC.md`/`STATUS.md` rollup

Do NOT touch: `src/`, `tests/`, `ui/`, other epics' tickets.

## Acceptance Criteria
1. Every public name in the docs (CLI flags, routes, event names, field names, constants) is greped against `src/`
   and exists exactly as written. The grep commands and results are recorded in STATUS.
2. The "As built" section lists each deviation with a reason and the task that made it, or states "none".
3. A user guide section ("Recurring jobs with limits") in `run-options-and-limits-guide.md` walks through the owner's
   original example end to end with real command output captured from a temp workspace.
4. The epic and all 11 task STATUS files agree on state.
5. **(L2, L4, L7, M1, M5) Guide text and refresh:** document overshoot (including that a run which finishes over its cap is
   `succeeded`, H1), the sentinel-kill under-count and the `partial` charge (M1, also in `guide-sentinel.md`),
   period-only reservation (L7), the sentinel-lane cap interaction (L4), `Option.sticky_on_resume` (M5), and the
   `record_trip` docstring/trailer if T-et7Edf deferred it. **Refresh the HLD §4 line references** (`_drain_remaining` is at
   `engine.py:3248`, not 3232; the boundary block is about 2728-2790).

## Risks
- Docs describing the HLD instead of the code. AC1 is the guard.

## Dependencies
- Upstream: all other tasks, including `T-lTW88y`'s final gate.

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: merged implementation.
- Downstream: epic close.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-Hq3vXd-docs-refresh/`
- Large outputs: N/A
