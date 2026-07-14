# TASK: T-d8w4v2-tests-docs-refresh

## Metadata
- Task ID: `T-d8w4v2-tests-docs-refresh`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: tester
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2.5 days`

## Description
Close-out: end-to-end integration coverage + reconcile the docs with shipped behaviour (post-implementation
docs-refresh per the architect contract). Add e2e tests spanning the whole feature and update HLD/ADR-0002/LLD +
example specs to match what actually shipped (including any deviations). Mark done only after docs are confirmed
against implemented code.

## Requirements Mapping
- All epic FRs/NFRs (integration surface). LLD §13 (test matrix). CLAUDE.md docs + coverage rules.

## Acceptance Criteria
1. e2e (CliRunner) tests: (a) a multi-endpoint workflow run selecting one route (others `not_taken`, run succeeds);
   (b) a breaker trip per action (`fail`/`stop`/`pause`) recording `tripped_breakers` + `breaker.trip`;
   (c) resume replay of a routed + tripped run.
2. An example spec under `specs/examples/` demonstrates `branches` + `circuit_breakers` + `join` and passes
   `ao validate` (id lowercase — memory `workflow-id-must-be-lowercase`).
3. Coverage on new modules ≥80%; `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q`
   all green (re-run independently — memory `subagent-lint-not-self-certifying`).
4. Docs reconciled: `docs-md/multi-endpoint-circuit-breaker-hld.md` (Draft → shipped notes), ADR-0002 consequences
   confirmed, `lld-run-control-routing-breakers.md` updated for any deviation, dynamic-injection guide updated for
   the nested-emission note if not already done in T-h5b2q7.
5. Epic `EPIC.md`/`STATUS.md` updated to Done once evidence attached.

## Risks
- Real-LLM e2e (if any) must use the repo-local workspace fixture, not `tmp_path` (memory
  `real-llm-tests-not-tmp-path`); fixtures must not auto-delete (memory `e2e-fixtures-must-not-auto-delete`).
- Docs drift if written before code settles — do this last.

## Dependencies
- Upstream: ALL other tasks in this epic.
- Downstream: none (close-out); confirms E-gd8m4x enablers documented.

## Pseudocode / Algorithm
See LLD §13 (acceptance matrix) — one e2e per row group.

## Schemas / Interface Notes
- Interface: CliRunner-driven; no new production interface.
- Artifacts: example specs + run artifacts (persisted, not auto-deleted).
- Triggers/events: exercises `branch.route`, `breaker.trip`, existing events.

## Handoff Boundary
- Upstream: all implementation tasks.
- Downstream: epic close-out + docs source-of-truth.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-d8w4v2-tests-docs-refresh/`
- Large outputs (if any): `output/E-rc7k2v-run-control-routing-breakers/`
