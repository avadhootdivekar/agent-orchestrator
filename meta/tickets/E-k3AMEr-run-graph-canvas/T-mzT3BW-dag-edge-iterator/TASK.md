# TASK: T-mzT3BW-dag-edge-iterator

## Metadata
- Task ID: `T-mzT3BW-dag-edge-iterator`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev A). **Reviewer sign-off required before merge** (scheduling-order risk).
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done` (all 6 acceptance criteria verified; Gate G1 reviewer + tester sign-off both PASS — 1 reviewer SHOULD-FIX + 1 NIT resolved post-gate, see `STATUS.md`)
- Estimate: `8 focus hours (1 day)`

## Requirements Mapping
- Requirement IDs: FR-3 (foundation), NFR-2 · HLD §8.3.1 · ADR-0017 D3 · Risk R-2

## Description
Extract a single, typed implementation of dependency-edge derivation from `dag.py::build_dag`
(`dag.py:133-184`), so the dashboard and the engine can never disagree about edges:
- `iter_dependency_edges(workflow) -> Iterator[DependencyEdge]` yields
  `(source, target, kind, via)`, where `kind` is one of `"explicit" | "loop" | "inferred"`.
- `build_dag` is refactored to **consume** the iterator, and its behavior stays byte-identical.

This task was split out of the builder task (manager review), so the scheduling-sensitive refactor
is reviewed and merged on its own.

## Acceptance Criteria
1. `dag.py` exports `DependencyEdge` (NamedTuple: `source: str, target: str, kind: str, via: str | None`),
   the constants `EDGE_KIND_EXPLICIT`, `EDGE_KIND_LOOP`, and `EDGE_KIND_INFERRED`, and `iter_dependency_edges`.
2. **Oracle equality.** `tests/test_dag_edge_iterator_oracle.py` keeps the **pre-refactor
   `build_dag` body verbatim** as `_oracle_build_dag`. For every workflow spec loadable from
   `specs/`, test fixtures (`tests/**/*.json|yaml` that `load_workflow` accepts), and the builtin
   templates' rendered example specs, `json.dumps(new.adj) == json.dumps(oracle.adj)`
   **including list order**. `Graph.output_to_task` is also equal. At least 10 specs are covered
   (record the count in STATUS).
3. **Synthetic cases** (explicit expectations):
   - explicit dep → `kind="explicit"`, `via=None`
   - dep on a loop id with iterations materialized up to 3 → `source = body[-1]__iter3`,
     `kind="loop"`, `via=<loop id>`
   - input matching another task's output → `kind="inferred"`, `via=<path>`
   - the same pair explicit **and** inferred → exactly one edge, `kind="explicit"`
   - an unknown dep → an edge is yielded with that source, and `build_dag` still creates the
     phantom `adj` key (today's behavior)
   - a task whose input is its own output → no edge
   - the same dep listed twice → one edge
4. The inferred-but-undeclared warning text is byte-identical (a `caplog` assertion against the
   oracle's message format).
5. `topological_order()` output is identical to the oracle's for every spec in AC-2.
6. The full `pytest -q` passes, especially `tests/test_loop_construct.py`,
   `tests/test_dynamic_injection.py`, and every routing and parallel suite. `ruff` and `mypy src`
   are clean.

## Risks
- Subtle order change leads to a different topological order, which changes scheduling (R-2).
  AC-2 and AC-5 are the guard.

## Dependencies
- None. Blocks `T-M4qboy`.

## Pseudocode / Algorithm
HLD §8.3.1 (verbatim). Key rule: explicit edges are yielded first, in authored task order, then
inferred edges. A `seen` set on `(source, target)` provides first-writer-wins dedup, matching
today's `if task.id not in adj[...]` checks.

## Schemas / Interface Notes
- Interface: `dag.iter_dependency_edges(workflow: WorkflowSpec) -> Iterator[DependencyEdge]` (HLD §14.1).
- Spec / data schema: none. Triggers / events: N/A. Artifacts: none.

## Handoff Boundary
- Upstream: none.
- Downstream: `T-M4qboy` consumes the iterator. **Gate G1.**

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-mzT3BW-dag-edge-iterator/`
- Large outputs: N/A
