# TASK: T-Dc1Yg7-docs-adr0009-reconcile

## Metadata
- Task ID: `T-Dc1Yg7-docs-adr0009-reconcile`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent (docs)
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Done
- Estimate: 1.5 days
- Note: **This is the mandatory post-implementation docs-refresh ticket** — mark complete only after confirming docs against the implemented code.

## Requirements Mapping
- FR-11 (README tiers/budgets/how-to-run + HLD extension + ADR-0009 → Accepted + learnings; reconciled to as-built)

## Description
Reconcile all documentation to the shipped implementation, including any deviations from this design. Update `benchmarks/README.md` with the tier model, budget model (USD `cost_budget_usd` vs token `budget_total`), how-to-run each tier, and spend expectations; extend the HLD with the new modules/seams/flows; flip ADR-0009 from Proposed → Accepted with an "Implementation notes (as-built)" section; capture learnings. Append the actual PLAN run results if the run executed.

## File ownership (exclusive)
- `benchmarks/README.md` — tiers/budgets/how-to-run-each-tier/spend + SWE-bench setup (`uv sync --extra swebench`, disk/cleanup notes) + xlarge-disabled note.
- `docs-md/benchmarking-framework-hld.md` — new sections for tiers, USD budget enforcement, parallel runner, workspace-provider seam, swebench provider+grader, campaign; update the layout/§9 + non-MVP/§12 + acceptance/§14.
- `docs-md/adr/ADR-0009-benchmark-tiers-external-suites-budgets-parallelism.md` — status Proposed → Accepted + as-built notes.
- `meta/learnings.md` + `meta/learning-compact.md` — new learnings (budget-vs-token-budget naming; disk-bounded SWE-bench; threads-not-processes; honor-system held-out tests).
- `meta/tickets/E-Bt4Xk9-complex-benchmark-tiers/EPIC.md` + `STATUS.md` — flip to Done; append real PLAN run numbers if run.
- (read-only) all shipped `bench/` code + committed suites/subjects/results.

## Inputs / Outputs
- Inputs: the merged implementation + green test suite + (optional) executed PLAN run results.
- Outputs: docs that match the code; ADR Accepted; learnings; epic closed.

## Acceptance Criteria
1. `benchmarks/README.md` documents: the four tiers + `tiers.json` caps; the two budget levels (per-run/per-model via `--cost-budget-usd`, whole-run via `campaign`) and their distinction from the token `budget_total`; `--max-parallel`; the SWE-bench extra + Docker/disk requirements + `make bench-medium`/`bench-large`; spend expectations per tier; xlarge disabled.
2. HLD reflects every shipped module/seam/flow **including deviations** (a "deviations from design" note per changed area, matching the predecessor HLD's convention).
3. ADR-0009 status = Accepted with an as-built notes section; any decision that shifted in implementation is recorded (not silently contradicted).
4. Learnings added to both `meta/learnings.md` and `meta/learning-compact.md` with attribution.
5. EPIC.md/STATUS.md flipped to Done; if the PLAN run executed, its real cost/solve-rate table is appended (replacing the estimates) and linked to committed `benchmarks/results/` artifacts.
6. Cross-file consistency: counts/wording match across README/HLD/ADR/EPIC (per `meta/tickets/README.md` rule 9).
7. **Docs verified against code, not against the design doc** — spot-check at least: the `tier` default, the `skipped_budget` status string, the `cost_budget_usd` flag name, the swebench extra name, the campaign command name.

## Risks
- Drift: if implementation deviated (e.g. grader-context change for swebench, or `source` field shape), the docs must describe what shipped, not what was planned. Read the code first.

## Pseudocode / Algorithm
```text
N/A (documentation reconciliation). Process:
  1. Read shipped bench/ code + committed specs + green test output.
  2. Diff against this epic's design; list deviations.
  3. Update README/HLD/ADR/learnings to as-built; flip statuses.
  4. If PLAN run done: replace estimate tables with real numbers + links.
  5. Verify cross-file consistency + code-vs-docs spot-checks.
```

## Schemas / Interface Notes
- N/A (docs). References every new schema/interface from the other tasks.

## Handoff Boundary
- Upstream: all tasks (this closes the epic).
- Downstream: none (epic closed). If the PLAN run is deferred, leave a clear "run pending" note and the exact commands to execute it.

## Artifacts
- Docs/comments: this folder + `docs-md/` + `benchmarks/README.md`. Large outputs: PLAN run results under `benchmarks/results/` (committed by the run, linked here).
