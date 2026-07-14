# STATUS

- ID: `T-c4w6p1-route-cone-computation`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §4.1-4.3. Algorithmic core: cones over the full `build_dag` graph (declared + inferred edges).

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-2. Cones MUST be computed on the graph `build_dag` returns, not `depends_on` alone (memory `build-dag-infers-edges-from-paths`, R2).

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-c4w6p1 route-cone-computation
DONE. Added `compute_cones(workflow, graph) -> (cones, membership)` and `forward_closure(adj,
entries)` to `dag.py`, computed strictly over `graph.adjacency()` (build_dag's declared +
inferred edges), never over `depends_on` alone. `Graph` gained three additive read accessors —
`adjacency()` (returns `self._adj`, same by-reference style as existing internals),
`output_to_task()` (the producer map, now built in `build_dag` and stored on the instance instead
of staying a local var), and `producer_of(inp)` (thin convenience wrapper for §5.4a's future join
relaxation) — `Graph.__init__` grew one new optional kwarg (`output_to_task`, default `{}`); no
existing constructor call sites or public behaviour changed (`build_dag` is the only caller).
`compute_cones` follows the LLD §4.2 pseudocode exactly: per router, per route, forward-closure
from `route.entry`; membership records every `(router_id, route_id)` reaching a task; a task is
in a route's exclusive cone iff exactly one route of *that* router reaches it (count scoped per
router, not globally, so route ids repeated across routers — e.g. two routers each with routes
"a"/"b" — never cross-contaminate). All 6 acceptance criteria verified by new tests in
`tests/test_route_cones.py` (12 tests): (1) shared-convergence-tail disjoint-cones test, (2) the
memory-pitfall regression test — an inferred (path-matching, non-`depends_on`) edge extends a
cone, with a load-bearing assertion that a hand-built depends_on-only adjacency would NOT reach
the same task, (3) membership with both routes recorded for the shared tail, (4) determinism
across 5 repeated calls, (5) `output_to_task()`/`producer_of()` accessor coverage, (6) empty
`branches`, single-router multi-route, and two-independent-routers cases. `uv run pytest -q` →
463 passed, 3 skipped (451 baseline + 12 new tests, zero regressions). `ruff check`, `ruff format
--check`, and `mypy` all clean on `src/agent_orchestrator/dag.py` and
`tests/test_route_cones.py` (repo-wide `ruff`/`mypy` runs surface pre-existing issues in
untouched files — `tests/test_e2e_cli.py`, `tests/test_engine.py`, `tests/test_executor.py`,
`tests/test_engine_budget.py`, `tests/test_project_config.py` — that predate this change and are
out of scope). No activation/`not_taken` logic, join resolution, or `ao validate` rules were
implemented (out of scope per Handoff Boundary — those are T-m2h5t7 and T-w6p2c8).

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §4, ADR-RC-002.
- Code: `src/agent_orchestrator/dag.py` (`Graph.adjacency`/`output_to_task`/`producer_of`,
  `forward_closure`, `compute_cones`).
- Tests: `tests/test_route_cones.py` (12 new tests, all 6 ACs traced above).

## Risks / Blockers
- None. Depends on T-b7q2m4 (`RouterSpec`) — already Done.

## Next actions
1. Done. Cones/membership ready for T-m2h5t7 (routing execution), T-w6p2c8 (validate rules),
   T-t4m8x1 (resume re-derivation) to consume.
