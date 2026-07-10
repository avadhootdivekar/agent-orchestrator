# ADR-0002 — Conditional branching & multi-endpoint runs: first-class gate-verdict branches

- Status: **Accepted** (user approved recommendations 2026-07-09)
- Date: 2026-07-09
- Deciders: avadhoot (user), Claude (architect role)
- Related: HLD [`multi-endpoint-circuit-breaker-hld.md`](../multi-endpoint-circuit-breaker-hld.md) · LLD [`lld-run-control-routing-breakers.md`](../lld-run-control-routing-breakers.md) · [`guide-dynamic-task-injection.md`](../guide-dynamic-task-injection.md) · ADR-0001 · Epic [`E-rc7k2v-run-control-routing-breakers`](../../ad/tickets/E-rc7k2v-run-control-routing-breakers/EPIC.md)

## Context

A single workflow file should be able to hold several routes (epic / task / bug / documentation …) that share a head, diverge on the *content* of an artifact (e.g. `prompt.md`), and end on different endpoint tasks. Today the engine runs every task and succeeds only if all succeed — there is no routing and no notion of "endpoint not applicable to this run".

Constraints: NFR-1 (engine reads only paths + bounded control JSON), determinism/resume, `ao validate` must keep catching wiring errors statically.

## Decision

Adopt **Option 1 — first-class `branches` construct driven by a router task's verdict JSON**:

- A declared router task writes a small verdict file (`{"routes": [...]}`); the engine reads it exactly the way it already reads `LoopSpec` gate verdicts.
- Routes declare only their **entry tasks**; the engine derives each route's cone of exclusive descendants from the DAG.
- Unselected cones are marked `not_taken` (terminal, never dispatched, never billed) and count as satisfied for run success — giving true multiple endpoints per file with per-run subsets active.
- Shared/converging tasks declare `join: all` (default) or `join: any`.

## Alternatives considered

- **Option 2 — router as `emit_tasks` emitter (status quo, no engine change).** The router agent emits only the chosen branch's tasks as a manifest. Already works today (dynamic-injection + fixed-aggregator conventions). Rejected as the *primary* mechanism because: branches invisible to `ao validate` (manifest bugs "fail ugly, not clean" — see memory `emit-tasks-skip-validation`); every run pays an LLM call to re-derive statically-knowable structure; route topology lives in instruction prose instead of the spec. **Retained as the escape hatch** for genuinely dynamic shapes; composes with Option 1 (an emitter may sit inside a route cone).
- **Option 3 — one workflow file per route + a dispatcher run.** Simple engine; rejected: contradicts the stated goal (single file), splinters shared head/tail tasks, and cross-file resume/status is a worse problem than branching.
- **Option 4 — per-task `when:` expressions (Airflow/GitHub-Actions style).** Maximum flexibility; rejected for MVP: needs an expression language + evaluation context (violates "engine knows only paths/statuses"), and per-task conditions scattered across a big file are harder to audit than one routes table. Could layer on later without conflicting.

## Consequences

- New spec surface: `branches` block + `join` field; JSON schema + `ao validate` checks (unknown entry ids, cones overlapping ambiguously, `any`-join satisfiability); new terminal state/reason `not_taken` persisted in run state (needs a default so old `state.json` still loads — memory `persisted-model-fields-need-defaults`).
- Run success redefined: all *activated* tasks succeeded/skipped (multi-endpoint becomes natural).
- The loop-gate verdict reader generalizes into one shared bounded-JSON control-file reader (loops, routers, verdict breakers).
- Resume must replay the recorded route decision rather than re-reading the verdict file.
- Deferred to LLD — **now resolved** in [`lld-run-control-routing-breakers.md`](../lld-run-control-routing-breakers.md): `not_taken` is a distinct status backed by persisted `route_decisions` (ADR-RC-001); nested routers are rejected by `ao validate` for MVP (§4.4 rule 9); `build_dag` inferred cross-route edges are computed into the cones and rejected when they couple routes ambiguously (ADR-RC-002).
