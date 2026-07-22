# TASK: T-Dcs2Rk-docs-adr-reconcile

## Metadata
- Task ID: `T-Dcs2Rk-docs-adr-reconcile`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Draft · Estimate: 1.0 day (≤3)
- **Mandatory post-implementation docs-refresh** (reconcile docs to actual behavior, including deviations).

## Requirements Mapping
- Epic docs/traceability; design §19.

## Description
After implementation, reconcile the design docs and ADR to the as-built code, add a benchmarks README, and capture learnings. Grep the WHOLE doc for contradicting claims, not just the fix site (learnings §35).

Deliverables:
- Update `docs-md/benchmarking-framework-hld.md` and `docs-md/adr/ADR-0008-benchmark-harness-approach.md` to as-built (status → Accepted-Implemented; note any deviations from the design; resolve Q1/Q2/Q3 to their final answers).
- Add a feature pointer to `docs-md/hld-agent-orchestrator.md`'s feature-docs list: `Benchmarking framework — benchmarking-framework-hld.md / benchmark-landscape-survey.md / ADR-0008`.
- Add `benchmarks/README.md`: how to author a suite/task, run `ao-bench`/`make bench-*`, read results, add a subject/grader, and the non-MVP roadmap (§12).
- Update root `README.md` (a short "Benchmarking" section pointing to the above).
- Add learnings to `meta/learnings.md` (+ refresh `meta/learning-compact.md`) — e.g. cost-plumbing reuse, permission-mode default, ao-subject single-run-id attribution, SI-1 import isolation.
- Update epic `EPIC.md`/`STATUS.md` to Done with the final task rollup + evidence (coverage/pass numbers from T-Tst4Ln).

## Acceptance Criteria
1. Design doc + ADR-0008 status/claims match the shipped code; every deviation is noted; Q1/Q2/Q3 resolved. A grep for the original open-question wording finds no stale contradicting claim.
2. `benchmarks/README.md` lets a new developer author a task and run a bench without reading the source.
3. `hld-agent-orchestrator.md` feature list + root `README.md` reference the benchmarking framework.
4. `meta/learnings.md` + `meta/learning-compact.md` updated (separable entries with attribution).
5. Epic `EPIC.md`/`STATUS.md` marked Done with counts consistent across files; all 9 task STATUS.md files reflect their final state.
6. Docs verified against implemented code (not assumed) — cite the specific modules/tests checked.

## Risks
- Docs drifting from a late implementation change → do this LAST, after T-Tst4Ln is green.

## Dependencies
- All other tasks (T-Sc4Hm2..T-Tst4Ln).

## Pseudocode / Algorithm
```text
grep design/ADR for "Non-MVP"/"OPEN_QUESTION"/"Proposed"/"Draft" → reconcile to as-built
verify each §4 module's pseudocode matches the shipped function; note deltas
```

## Schemas / Interface Notes
- N/A (documentation task).
- Artifacts: `docs-md/**`, `benchmarks/README.md`, `README.md`, `meta/learnings*.md`, epic/task tickets.

## Handoff Boundary
- Upstream: all tasks.
- Downstream: none (epic close). Phase-2 (actual Sonnet-vs-Opus-vs-ao run + report) is a separate follow-up, not this epic.

## Artifacts
- Docs/comments: this folder + repo docs. Large outputs: none.
