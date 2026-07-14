# STATUS

- ID: `E-gd8m4x-granular-task-decomposition`
- Updated At: 2026-07-09
- State: Draft (Proposed / backlog — blocked on enablers from `E-rc7k2v` and `E-st5p3q`)
- Owner: architect

## This update
Epic drafted from the accepted 2026-07-09 design session (granular-decomposition HLD). High-level scope, MVP/non-MVP split, requirements traceability, and a cross-repo candidate task list only. No task tickets, no LLD — separate phase.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Created from the user-ACCEPTED granular-task-decomposition HLD (2026-07-09). Cross-repo: instructions/scaffold in `ao-runner/workflows/epic-runner/`, framework enablers in `agent-orchestrator`. Depends on `E-rc7k2v` (nested-emission verification E1 + injection caps E2) and `E-st5p3q` (per-task model/effort E3). E4 token telemetry already exists.

## Evidence
- Design input: `docs-md/granular-task-decomposition-hld.md`.
- HLD header cross-linked back to this epic.
- Enabler status (HLD §5): E1 verify (from `E-rc7k2v`), E2 injection caps (from `E-rc7k2v`), E3 per-task model/effort (from `E-st5p3q`), E4 telemetry exists.

## Risks / Blockers
- Blocked-until: nested-emission verification (E1) and injection caps (E2) from `E-rc7k2v`; per-task model/effort (E3) from `E-st5p3q`. Runner-side work can begin against interim workarounds (`developer-step` agent entries) but should not be defaulted-on before the enablers land.
- Handoff quality is the primary design risk (weakest-link summaries) — mitigated by rigid templates + done-checks + `review-<tid>` drift check.

## Next actions
1. Track landing of enablers E1/E2 (`E-rc7k2v`) and E3 (`E-st5p3q`).
2. LLD + task-breakdown phase (planner/step instructions → context contracts → breakdown-contract extension → `GRANULAR=1` toggle → A/B metrics → pilot).
3. Run one real epic in both modes; compare §7 metrics; decide default.
