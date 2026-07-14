# TASK: T-5bh03j-docs-refresh

## Metadata
- Task ID: `T-5bh03j-docs-refresh`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: architect
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Draft
- Estimate: `< 2 days`
- MVP: no (Phase 3 close-out)

## Requirements Mapping
- Post-implementation reconciliation of all epic docs against shipped behavior.

## Description
After the implementation tasks land, reconcile the design against what was actually built.
Update `docs-md/e2e-playground-testing.md` and `playground/README.md` so they describe the
real, shipped behavior — including any deviations from this design (e.g. if control-file
pre-seeding was replaced, if a marker name changed, if OQ-2 was resolved, if an engine gap
had to be filed/fixed separately). Add ADR entries for any decision that changed during
implementation. Mark the epic Done only after docs are verified against the code + green tests.

## Inputs / Outputs
- Inputs: shipped `playground/*`, `tests/playground/*`, `pyproject.toml` markers, final
  `uv run pytest` + coverage results, the as-built control-file/gating mechanism.
- Outputs: updated `docs-md/e2e-playground-testing.md`, `playground/README.md`, new/updated
  ADR entries (design doc §9 and/or `docs-md/adr/`), epic `EPIC.md`/`STATUS.md` rollup to Done.

## Acceptance Criteria
1. Given the shipped tests, When the design doc's tier table, area matrix, and workflow-shape
   diagram are compared to reality, Then every drift point is corrected and each of the six
   areas links to the actual test id/function that asserts it.
2. `playground/README.md` documents the final corpus, the four tiers, and the exact `uv run`
   commands (including the `AO_E2E_REAL_LLM=1` gated invocation) — verified by running them.
3. Any design deviation (pre-seeding approach, marker/env names, N=1 vs OQ-2, engine ticket
   filed) is recorded as an ADR update with Context/Decision/Consequences.
4. `EPIC.md` Task List all checked; `STATUS.md` records final pass count + coverage delta vs
   the regression baseline; epic State → Done, synchronized across `EPIC.md`/`STATUS.md`.
5. Docs contain no stale TODO/OPEN_QUESTION that the implementation actually resolved;
   remaining open items are explicitly restated as future work.
6. Verified: `uv run pytest -q` green (fake tiers) and, if run, the gated tier's last known result noted.

## Risks
- Docs lag code → this task is the explicit gate; do not close the epic until docs match the
  shipped mechanism and the six-area→test links resolve.

## Dependencies
- All implementation tasks: `T-7592ux`, `T-1vuzyi`, `T-ee8hzo`, `T-r21p4y`, `T-g7rjh0`,
  `T-92o31p`, `T-n477z9`, `T-94tepb`.

## Pseudocode / Algorithm
```text
diff design-doc §8/§19/§7 against shipped tests + playground assets
for area in 1..6: link area -> concrete test function id
update playground/README run commands; execute them to verify
record ADR deltas; update EPIC/STATUS rollups to Done (synchronized)
```

## Schemas / Interface Notes
- Interface / API: docs only.
- Spec / data schema: reflect any final control-file/schema notes.
- Triggers / events: N/A.
- Artifacts: `docs-md/e2e-playground-testing.md`, `playground/README.md`, ADR entries.

## Handoff Boundary
- Upstream: all implementation tasks.
- Downstream: none (epic close).

## Artifacts
- Docs/comments: this task folder + updated `docs-md/`.
- Large outputs: none.
