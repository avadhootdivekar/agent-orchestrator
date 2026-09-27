# TASK: T-oroE5f-docs-refresh

## Metadata
- Task ID: `T-oroE5f-docs-refresh`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `architect` (or `developer`), with review by `reviewer`
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done` (**mandatory post-implementation task, runs last**). All 8 docs are reconciled, AC-1 to AC-5 are met, and the reviewer returned PASS. Evidence is in `STATUS.md`.
- Estimate: `6 focus hours (< 1 day)`

## Requirements Mapping
- Requirement IDs: all (documentation reconciliation) · HLD §25

## Description
After every MVP task is Done, reconcile the documentation with the **implemented** code, including
every deviation from the design. The design docs describe intent, and this task makes them
describe reality.

Documents to reconcile:
1. `docs-md/run-graph-canvas-hld.md`: set the status to Implemented, add an "Implementation outcome
   and deviations" section, and correct any signatures, constants, file:line anchors, or behaviors
   that changed.
2. `docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md`: move the status to
   Accepted (implemented) and add an implementation note.
3. `docs-md/dashboard-and-general-instructions-hld.md`: §2.5 Frontend (Graph tab, lazy chunk, new
   deps), §2.6 API (`/runs/{id}/graph`, `graph_version`, `TaskStat` additions), and §4 (the roadmap
   item marked partially delivered, with a link).
4. `meta/ROADMAP.md` §3.3: mark the read-only run-graph half as delivered. The editor half stays deferred.
5. `docs-md/guide-dynamic-task-injection.md`: spawn provenance (`spawned_by`), how to view it, and
   loop-clone parent semantics.
6. The root `README.md` dashboard section: a Graph tab usage paragraph.
7. `ui/README.md`: the layout (`src/graph/*`) and the **dependency-policy line**, updated to name
   `@xyflow/react`/`@dagrejs/dagre` with the rationale link (ADR-0017 D5).
8. `docs-md/adr/ADR-0011-untrusted-workspace-content-rendering.md` (or the file-preview HLD
   sensitivity inventory): add `workflow.snapshot.<sha12>.json` and the text-only rendering of
   agent-authored ids.

## Acceptance Criteria
1. Every factual claim added or changed in the 8 docs above cites code as `path:line` in this
   task's STATUS evidence table (claim → citation), checked against the merged code at the final
   commit sha (recorded).
2. The HLD "Implementation outcome" section lists every deviation from the design, or states "none"
   explicitly, and lists every follow-up (F-2 route-on-resume, non-MVP tasks, R-10) with its ticket ID or backlog note.
3. `grep -rn "launch_record\|write_workflow_snapshot" docs-md/` returns nothing, unless it is in an
   explicitly historical "rejected alternatives" context.
4. The epic `EPIC.md`/`STATUS.md` and every task's `TASK.md`/`STATUS.md` are consistent: the same
   status words, the same counts, and Done only with evidence.
5. A `reviewer` sign-off on the reconciled docs is recorded in STATUS with attribution.

## Risks
- Docs drifting from code again. Mitigated by AC-1's citation discipline.

## Dependencies
- All MVP tasks Done, plus Gate G3.

## Pseudocode / Algorithm
```text
FOR each doc in list: FOR each claim touching this epic: locate the implementing code; cite path:line; fix the doc if it differs
Record the deviations table; sync the ticket status files; request reviewer sign-off
```

## Schemas / Interface Notes
- N/A (documentation only).

## Handoff Boundary
- Upstream: every MVP task.
- Downstream: epic closure by `dev-epic`.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-oroE5f-docs-refresh/`
- Large outputs: N/A
