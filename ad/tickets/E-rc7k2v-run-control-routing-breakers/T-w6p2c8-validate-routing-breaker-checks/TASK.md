# TASK: T-w6p2c8-validate-routing-breaker-checks

## Metadata
- Task ID: `T-w6p2c8-validate-routing-breaker-checks`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2.5 days`

## Requirements Mapping
- NFR-3, R2. LLD §4.4, §0-R2.

## Description
Extend `cross_validate` (and/or a sibling `validate_run_control(workflow, graph)` invoked after `build_dag`) with
the static routing + breaker checks so `ao validate` catches wiring errors before a run — most importantly the
R2 inferred-cross-route-coupling rule that turns ambiguous cones into a named error. Reuse `compute_cones` and
the producer-map from T-c4w6p1.

## Acceptance Criteria
Each rule below rejects with a message naming the offending ids/path (Given a bad spec / When `ao validate` /
Then non-zero exit + specific error):
1. Unknown `router_task_id` or unknown route `entry`.
2. An entry not reachable from its router (can never be deactivated).
3. Route-entry non-disjointness (entry of A ∈ reach(B)).
4. **R2**: a task reachable from ≥2 routes of one router whose coupling includes an *inferred* edge not in
   `depends_on` → error names both tasks + the shared artifact path.
5. `any`-join unsatisfiable (every producer in a never-co-activatable cone).
6. Route with no sink in its exclusive cone (unreachable endpoint).
7. Nested router (router task inside another router's cone) rejected (MVP boundary).
8. Breaker: unknown `verdict.task_id`; duplicate breaker id; `condition` not implemented in this release;
   `stop_file.path` traversal.
9. A **valid** multi-endpoint + breaker spec passes cleanly (positive test).
10. `uv run ruff/mypy/pytest` clean; CliRunner test drives `ao validate` for at least the R2 and unreachable-endpoint
    cases (memory `engine-api-tests-dont-cover-cli`).

## Risks
- Validation must run AFTER `build_dag` (needs inferred edges) — sequence it in the CLI/validate path, not before.
- False positives on legitimate declared convergence (`depends_on` + `join`) — only inferred coupling fails (rule 4).
- Keep error messages actionable (name the fix: "give distinct output paths or declare join").

## Dependencies
- Upstream: T-c4w6p1 (cones/producer-map), T-b7q2m4 (specs).
- Downstream: T-d8w4v2 (docs), gives authors a clean-fail surface (contrasts memory `emit-tasks-skip-validation`).

## Pseudocode / Algorithm
See LLD §4.4 (rules 1-11).

## Schemas / Interface Notes
- Interface: `validate_run_control(workflow, graph)` raising `SpecValidationError` with `path`.
- Artifacts: none (static).
- Triggers/events: N/A.

## Handoff Boundary
- Upstream: LLD §4.4; T-c4w6p1.
- Downstream: none; this is a validation leaf.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-w6p2c8-validate-routing-breaker-checks/`
