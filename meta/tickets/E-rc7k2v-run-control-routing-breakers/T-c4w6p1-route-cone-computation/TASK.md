# TASK: T-c4w6p1-route-cone-computation

## Metadata
- Task ID: `T-c4w6p1-route-cone-computation`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2.5 days`

## Requirements Mapping
- FR-B2, FR-B5, NFR-2. LLD §4.1-4.3, §0-R2.

## Description
Add `compute_cones(workflow, graph)` to `dag.py`: over the graph `build_dag` actually returns (declared **and**
inferred edges), compute each route's exclusive cone (tasks reachable only via that route of one router) and a
`membership` map (task → routes reaching it). Expose a forward-closure helper and a read accessor for adjacency /
the `output_to_task` producer map so downstream tasks (routing execution, validation) reuse them. Pure,
deterministic, no clock. This is the algorithmic core the routing + validation tasks build on.

## Acceptance Criteria
1. For a two-route workflow sharing a head + a declared convergence tail, `compute_cones` returns disjoint
   exclusive cones and marks the tail as shared (not in any exclusive cone).
2. Cones are computed over `build_dag`'s adjacency including inferred path-matching edges — a test where an
   inferred edge extends a cone proves the cone reflects the runtime graph, not `depends_on` alone
   (memory `build-dag-infers-edges-from-paths`).
3. `membership[t]` lists every `(router_id, route_id)` that reaches `t`; a task reachable from 2 routes of one
   router has both entries.
4. Determinism: two calls on the same spec return identical cones/membership (NFR-2); sets are order-independent.
5. A producer-map accessor (`inp -> producing task id`) is exposed for reuse (join input relaxation, §5.4a).
6. `uv run ruff/mypy/pytest` clean; unit tests cover empty branches, single-router multi-route, two independent
   routers, and inferred-edge extension.

## Risks
- Off-by-one in "exclusive vs shared": a task reachable from exactly one route of a router is exclusive; from ≥2
  is shared — assert both.
- Do not mutate `Graph`/`build_dag` behaviour; add read accessors only.

## Dependencies
- Upstream: T-b7q2m4 (`RouterSpec`).
- Downstream: T-m2h5t7 (activation), T-w6p2c8 (validation), T-t4m8x1 (resume re-derivation).

## Pseudocode / Algorithm
See LLD §4.2 (`compute_cones`) and §4.1 (definitions).

## Schemas / Interface Notes
- Interface: `compute_cones(workflow, graph) -> (cones, membership)`; `forward_closure(adj, entries)`;
  `Graph.adjacency()` / producer-map accessor.
- Artifacts: none (graph in memory).
- Triggers/events: N/A.

## Handoff Boundary
- Upstream: LLD §4; T-b7q2m4.
- Downstream: consumers import cones/membership; do not add activation or event logic here.

## Artifacts
- Docs/comments: `meta/tickets/E-rc7k2v-run-control-routing-breakers/T-c4w6p1-route-cone-computation/`
