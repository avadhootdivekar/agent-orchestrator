# EPIC: E-rc7k2v-run-control-routing-breakers

## Metadata
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Title: Run-control — conditional routing, multi-endpoint success semantics & circuit-breaker framework
- Owner: architect
- Created: 2026-07-09
- Last Updated: 2026-07-09 (all 12 tickets complete + merged)
- Status: **Done** — all requirements implemented, tested ≥80% coverage, docs reconciled

## Summary
- Goal: Give the engine two related run-completion capabilities that today do not exist, landing on one shared mechanism (bounded control-file reads):
  1. **Conditional routing / multiple endpoints** — one `workflow.json` can hold several routes (epic / task / bug / documentation …) that share a head, diverge on a router task's verdict JSON, and terminate on different endpoint tasks. Untaken routes must not run and must not count as failures.
  2. **General circuit-breaker framework** — a spec-declared way to say "if condition X arises anywhere in the run, stop / fail / pause", replacing today's ad-hoc special-cased run-level stops with one trip → record → act path.
- Scope In (MVP): `branches` + `circuit_breakers` schema and models; `not_taken` terminal semantics; route cone computation + join policy; breaker framework at task boundaries with MVP conditions; re-framing existing budget/quota/estimate stops as built-in breakers; `ao validate` checks; resume replay; observability.
- Scope Out (non-MVP): branch-scoped breaker action (`scope: branch`), parallel scheduling & breaker evaluation under parallelism, the remainder of the §6 condition catalog beyond the MVP six, reset/half-open for declarative breakers, per-task `when:` expressions (ADR-0002 Option 4), UI/status visualisation beyond a single route/`not_taken` column.

## Traceability (design sources — decisions locked 2026-07-09)
- HLD: [`docs-md/multi-endpoint-circuit-breaker-hld.md`](../../../docs-md/multi-endpoint-circuit-breaker-hld.md) — §3 requirements, §4 routing sketch, §5 breaker sketch, §6 condition catalog, §7 out-of-scope, §8 epic shape.
- ADR: [`docs-md/adr/ADR-0002-conditional-branching-multi-endpoint.md`](../../../docs-md/adr/ADR-0002-conditional-branching-multi-endpoint.md) — Accepted: first-class gate-verdict `branches` construct; `emit_tasks` retained as escape hatch.
- **LLD: [`docs-md/lld-run-control-routing-breakers.md`](../../../docs-md/lld-run-control-routing-breakers.md)** — resolves R1/R2/R3; schema diffs, cone algorithm, breaker framework, six MVP conditions, re-frame plan, resume replay, events, ADR-RC-001..005, test matrix, sprint sequencing.

## MVP vs Non-MVP split
- **MVP (this epic)** — routing + multi-endpoint + breaker framework with the six MVP conditions, one shared bounded-JSON control reader, run-success redefinition, `ao validate` checks, resume replay, structured events, docs + tests.
- **Non-MVP (follow-on, schema leaves room)**:
  - Branch-scoped breaker action `scope: branch` (trip kills one branch → its tasks `cancelled`, run continues) — HLD §3 FR-CB2 (branch scope), §7.
  - Parallel scheduling and breaker evaluation under parallel workers (MVP evaluates at task boundaries in the sequential engine) — HLD §5, §7.
  - Remaining §6 catalog conditions beyond the MVP six (`failure_ratio`, `same_task_exhausted`, `token_rate` as declarative, `projected_cost_exceeds`, `provider_429_count`, `executor_spawn_failures`, `output_validation_failures`, `loop_max_iterations_no_converge`, `no_artifact_progress_seconds`, `injection_depth`, `dag_size`, `os_signal`, `workspace_disk_usage`, `git_workspace_dirty`) — declared in schema, land incrementally.
  - Reset / half-open semantics for declarative breakers (MVP is latch-only; transient 429/quota keep existing episode logic) — HLD §5.
  - Per-task `when:` expressions (ADR-0002 Option 4, deferred).

## Requirements (traceable to HLD §3)
### Functional — routing / multi-endpoint
- FR-B1 Routing gate: a task can be a *router*; after it succeeds the engine reads its bounded verdict JSON (`{"routes": [...]}`) and activates only the selected branches (multi-select allowed). [HLD §3 FR-B1, §4]
- FR-B2 Branch declaration: branches declared statically (branch id → entry task ids) so `ao validate` can check them; downstream membership derived from the DAG. [FR-B2]
- FR-B3 Not-taken semantics: tasks on unselected branches + their exclusive descendants reach a distinct terminal state `not_taken` (surfaced under `skipped`+reason or a new status — LLD decision); never dispatch, never consume budget/attempts. [FR-B3]
- FR-B4 Run success: a run succeeds when every *activated* task reaches `succeeded`/`skipped`; `not_taken` tasks do not block success; multiple sinks may be terminal per run. [FR-B4]
- FR-B5 Joins: a task depending on tasks from several branches declares `join`: `all` (default) or `any`. [FR-B5]
### Functional — circuit breaker
- FR-CB1 Breaker spec: workflow-level `circuit_breakers: [...]`, each with a §6 condition, threshold params, and an action. [FR-CB1]
- FR-CB2 Actions: `fail` / `stop` / `pause`, scope `run` (default). (Scope `branch` = non-MVP.) [FR-CB2]
- FR-CB3 Verdict breaker: any task declared a breaker gate trips when its verdict JSON carries e.g. `{"halt": true, "reason": "…"}`. [FR-CB3]
- FR-CB4 Observability: route decisions and breaker trips are structured events (`branch.route`, `breaker.trip`) and persisted in run state. [FR-CB4]
- FR-CB5 Resume: route decisions and tripped-breaker facts survive `ao resume`; resume does not re-run the router or re-trip a cleared condition. [FR-CB5]
### Non-functional
- NFR-1 preserved: engine reads only bounded verdict/control JSON, never payload artifacts — reuses the existing loop-gate reader; no new NFR-1 surface. [HLD §2, §3 NFR-1]
- NFR-2 Deterministic: same verdict files → same activation set and same topo order.
- NFR-3 Validated: `ao validate` rejects unknown branch entry tasks, unreachable endpoints, breakers referencing unknown tasks, and `any`-joins unsatisfiable on any single route.
- NFR-4 Injected tasks participate: an injected task inherits its branch; breakers count injected-task failures. (Ties to the granular-decomposition epic's caps.)
- NFR-5 Backward-compat: new `not_taken`/breaker fields on `RunState` need defaults so old `state.json` still loads (memory `persisted-model-fields-need-defaults`).

## Task tickets (LLD complete — created 2026-07-09; each ≤ 3 days)
Detail in each `T-*/TASK.md`; full design in the LLD. Dependency waves and 2-sprint capacity math in LLD §14.

| Wave | Task ticket | One-line summary | Depends on |
|---|---|---|---|
| 1 | `T-b7q2m4-branch-breaker-schema-models` | schema `branches`/`circuit_breakers`/task-`join` + pydantic models + `not_taken` status + defaulted RunState fields (FR-B2/B5, FR-CB1, NFR-5) | — |
| 1 | `T-k9r3n8-bounded-control-reader` | generalise loop-gate reader → one bounded `read_control` + typed helpers + size cap (FR-B1, FR-CB3, NFR-1) | b7q2m4 |
| 1 | `T-h5b2q7-nested-emission-verification` | integration test + doc note: injected emitter itself injects on success — **unblocks `E-gd8m4x`** (granular E1) | — |
| 2 | `T-c4w6p1-route-cone-computation` | `compute_cones` over the full `build_dag` graph (declared + inferred edges) + producer map (FR-B2/B5, NFR-2, R2) | b7q2m4 |
| 2 | `T-x8v4d3-breaker-framework-registry` | breaker ABC + registry + task-boundary eval (trip→record→act) + fail/stop/pause (FR-CB1/CB2/CB4) | b7q2m4 |
| 3 | `T-m2h5t7-routing-execution-run-success` | router activation, `not_taken` skip, join `all`/`any`, run-success redefinition (FR-B1/B3/B4/B5, NFR-4, R1) | c4w6p1, k9r3n8 |
| 3 | `T-q5n7k2-mvp-breaker-conditions` | the six MVP conditions incl. `injected_task_count` (**E2 for `E-gd8m4x`**) (FR-CB1/CB3, HLD §6) | x8v4d3, k9r3n8 |
| 3 | `T-w6p2c8-validate-routing-breaker-checks` | `ao validate` routing+breaker rules incl. R2 inferred-coupling rejection (NFR-3, R2) | c4w6p1 |
| 4 | `T-r3j9b6-reframe-existing-stops` | budget/quota/estimate stops → trip→record→act additively; characterization pins (HLD §5, R3) | x8v4d3 |
| 4 | `T-t4m8x1-resume-replay` | route decisions + tripped-breaker facts survive resume; not_taken re-derived (FR-CB5, NFR-2) | m2h5t7, x8v4d3 |
| 4 | `T-n9k3r5-observability-ao-status` | `branch.route`/`breaker.trip` wired; `ao status` route/not_taken column + breaker trailer (FR-CB4, HLD §7) | m2h5t7, x8v4d3, r3j9b6 |
| 5 | `T-d8w4v2-tests-docs-refresh` | e2e coverage + post-implementation docs reconciliation (HLD/ADR-0002/LLD + example specs) | all above |

## Dependencies between epics
- **Provides to `E-gd8m4x` (granular decomposition)**: nested-emission verification (granular HLD E1) and injection caps (`injected_task_count` MVP here; `injection_depth` is non-MVP here). The granular epic depends on these landing.
- **Coordinates with `E-st5p3q` (settings precedence)**: both add new `RunState` fields that need backward-compat defaults — share the `persisted-model-fields-need-defaults` migration pattern; otherwise independent.

## Risks and Dependencies (R1-R3 RESOLVED in LLD 2026-07-09)
- **R1 — RESOLVED (ADR-RC-001, LLD §0-R1/§5/§9)**: `not_taken` is a **distinct terminal status**, not `skipped`+reason, backed by a persisted `route_decisions` source-of-truth. Success math extends `("succeeded","skipped")`→ add `not_taken` at the finaliser only (not_taken never sets `failed`); `ao status` gains a `route` column and a `not_taken` count; resume preserves not_taken and re-derives it deterministically from `route_decisions` (no verdict re-read). Owned by T-b7q2m4/T-m2h5t7/T-t4m8x1/T-n9k3r5.
- **R2 — RESOLVED (ADR-RC-002, LLD §0-R2/§4)**: cones are computed over the graph `build_dag` returns (declared **and** inferred path edges), so validator == runtime graph. `ao validate` rejects any task reachable from ≥2 routes whose coupling includes an *inferred* (path-matching) edge not in `depends_on`, naming both tasks + the shared path; legitimate convergence must be declared (`depends_on` + `join`). Owned by T-c4w6p1/T-w6p2c8.
- **R3 — RESOLVED (ADR-RC-003, LLD §0-R3/§8)**: existing stops are re-framed **additively** — `trip_builtin` records a `builtin.*` breaker + `breaker.trip` event while the pre-existing `budget.exhausted`/`quota.max_wait_exceeded` events, final `status`, and exit code stay byte-identical. Parity is guarded by **characterization pins written before the refactor** (LLD §8.3), so drift is a failing test, not a claim. Owned by T-r3j9b6.
- **R4 — RESOLVED (LLD §5.5)**: injected tasks inherit the emitter's `route`; a not_taken emitter never dispatches so never injects (injected tasks are always on an activated branch by construction); breaker counting already iterates all `state.tasks`/`injected_tasks`, so injected failures count with no extra work. Verified by T-h5b2q7 (nested emission) + T-q5n7k2 (`injected_task_count`).
- **Deferred to implementation (non-blocking)**: `stop_file` absolute-path policy; dedicated `RunState.status` `stopped`/`paused` values (ADR-RC-004 keeps stop/pause on resumable `failed` for MVP). See LLD §15 open questions.

## Links
- HLD: `docs-md/multi-endpoint-circuit-breaker-hld.md`
- ADR: `docs-md/adr/ADR-0002-conditional-branching-multi-endpoint.md`
- LLD: `docs-md/lld-run-control-routing-breakers.md` (complete — schema/algorithms/ADR-RC-001..005/test matrix/sequencing)
- Sprint plan / capacity math: LLD §14 (2 sprints, 5 dependency waves; task tickets under this epic folder)
- Large outputs (if any): `output/E-rc7k2v-run-control-routing-breakers/`
