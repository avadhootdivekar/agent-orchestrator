# TASK: T-H8Mmog-docs-adr-examples-review

## Metadata
- Task ID: `T-H8Mmog-docs-adr-examples-review`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented + reviewer/tester subagent gates)
- Created: 2026-07-14
- Last Updated: 2026-07-15
- Status: Done
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: docs/ADR/examples (cross-cutting), late gate

## Description
1. `docs-md/lld-agent-monitoring-self-healing.md` (HLD+LLD combined, following existing doc
   style e.g. `docs-md/lld-run-control-routing-breakers.md`): design decisions D1-D8, exact
   engine hook sites with line references, `Monitor` ABC interface, config schema, event/RunState
   field catalog, edge cases per module, test strategy/acceptance matrix.
2. New ADR: `docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md` (next number after
   ADR-0003) capturing the key decisions (guardrail mode default, hard-never-consultable
   enforcement, all-or-nothing consult resolution, self-heal scope boundary, monotonic self_heal
   CLI precedence) with alternatives considered.
3. Cross-reference sweep: `grep` the WHOLE of `docs-md/hld-agent-orchestrator.md` and
   `docs-md/lld-run-control-routing-breakers.md` (not just the sections that seem related) for
   any claim this epic now contradicts (e.g. "breakers only ever fail/stop/pause", "no consult
   mechanism exists") and fix every instance found (memory `docs-refresh-must-search-whole-doc`).
4. `specs/examples/workflow-monitoring.json` (workflow `id` fully lowercase per memory
   `workflow-id-must-be-lowercase`) demonstrating a `mode: recommend` breaker + a config example
   snippet (either inline in the LLD or `specs/examples/` — a `.ao/config.yaml`-shaped snippet
   showing the `monitoring:` block).
5. Append genuinely new learnings to `meta/learnings.md` (statement/context/By/Role/Date fields) —
   e.g. the `evaluate_breakers`-untouched call-site-diffing technique, the wheel-packaging reason
   instruction templates must be baked constants, the D4 hook-placement rationale.
6. Request a `reviewer` pass on the FULL diff (not just the early-gate design pass) before
   declaring the epic done; incorporate findings.
7. Request a `tester` late-gate run: execute a representative workflow (the new example spec)
   end-to-end through `ao run`/`ao resume` via the real CLI, with evidence (artifacts + pass/fail),
   demonstrating the orchestration works with the implemented changes — not just unit tests.
8. Final verification: re-run `uv run pytest -q`, `--cov`, `ruff check .`, `ruff format --check
   .`, `mypy src`; record exact before/after numbers in the epic doc's Evidence Log and this
   ticket's STATUS.md.

## Acceptance Criteria
1. LLD doc exists, follows existing doc conventions, cross-links the epic/ADR.
2. ADR-0004 exists, is the next sequential number, documents alternatives considered.
3. A whole-document grep of both cross-referenced docs was performed (not just a targeted
   section edit) and every found contradiction is fixed, listed explicitly in this task's
   Completion/Evidence section.
4. `specs/examples/workflow-monitoring.json` validates via `ao validate` (lowercase id, schema
   round-trips).
5. `meta/learnings.md` has at least 1 new, clearly-separable entry with all required fields.
6. Reviewer pass completed; findings (if any) addressed or explicitly deferred with reasoning
   recorded.
7. Tester late-gate run completed with quantifiable evidence (exact command + pass/fail +
   artifact paths), not just "it should work."
8. Final pytest/coverage/ruff/mypy numbers recorded, compared explicitly against the captured
   baseline (686 passed/3 skipped, 91% coverage, 2 ruff errors, 4 mypy errors).

## Risks
- Low-medium: doc cross-referencing is easy to under-scope (memory: `docs-refresh-must-search-
  whole-doc` — this is a repeat-offender class of mistake in this repo's own history).

## Dependencies
- All 6 prior tasks (this is the closing task).

## Pseudocode / Algorithm
N/A (docs/process task).

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema (JSON/YAML): `specs/examples/workflow-monitoring.json`.
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): `docs-md/lld-agent-monitoring-self-healing.md`,
  `docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md`,
  `specs/examples/workflow-monitoring.json`, `meta/learnings.md` (appended).

## Handoff Boundary
- Upstream: all prior tasks.
- Downstream: none (closing task); feeds the epic's final completion handoff.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-H8Mmog-docs-adr-examples-review/`
- Large outputs: none.
