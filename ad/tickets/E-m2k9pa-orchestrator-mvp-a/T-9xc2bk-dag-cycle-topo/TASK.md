# TASK: T-9xc2bk-dag-cycle-topo

## Metadata
- Task ID: `T-9xc2bk-dag-cycle-topo`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `1–2 days`

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, NFR-3

## Description
Implement `dag.py`: build the dependency graph from `depends_on` + inferred input/output edges, detect
cycles (Kahn), produce a deterministic topological order, and validate inputs. (LLD §3.)

## Acceptance Criteria
1. `build_dag(workflow)` returns a graph; edges = explicit `depends_on` ∪ inferred (input path == upstream output path).
2. Cyclic spec → `CycleError` listing the nodes in the cycle.
3. `topological_order()` is deterministic (tie-break by sorted task id) — same input → same order.
4. `validate_inputs()` raises `MissingInputError` when an input is neither produced upstream nor pre-existing.
5. Unit tests: linear chain, diamond, self-cycle, 3-node cycle, inferred-edge case, missing input.

## Risks
- Inferred edges masking author mistakes — warn when inferred edge not also declared.

## Dependencies
- Upstream: T-r4t8wd. Downstream: T-h7k3qm.

## Pseudocode / Algorithm
```text
adj = edges from depends_on; for each task, for each input, if some task outputs it -> add edge
Kahn: indeg map; queue zero-indeg (sorted); pop -> append -> decrement; leftover -> CycleError
```

## Schemas / Interface Notes
- Interface: `build_dag`, `Graph.topological_order()`, `Graph.validate_inputs()`.
- Artifacts: reasons over declared paths only (no content reads).

## Handoff Boundary
- Upstream: typed WorkflowSpec. Downstream: ordered task ids for the engine.

## Artifacts
- Docs/comments: this folder.
