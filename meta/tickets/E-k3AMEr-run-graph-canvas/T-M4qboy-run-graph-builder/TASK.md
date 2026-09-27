# TASK: T-M4qboy-run-graph-builder

## Metadata
- Task ID: `T-M4qboy-run-graph-builder`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev A)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done` (implementation + tests complete; Gate G1 reviewer/tester sign-off pending — see `STATUS.md`)
- Estimate: `14 focus hours (< 2 days)`

## Requirements Mapping
- Requirement IDs: FR-3, FR-7, U-1, U-3, U-4, D-4, D-5, NFR-2, NFR-3, NFR-5 · HLD §8.3.2 · ADR-0017 D3/D4

## Description
Create `src/agent_orchestrator/ui/graph.py`: a **pure** (no I/O, no clock) builder that turns
`RunState` plus the latest-session `WorkflowSnapshot | None` into a `RunGraph`. `RunGraph` holds
nodes, dependency edges (with kind), spawn edges, loop/router annotations, execution ordinals,
warnings, and a truncation flag.

The task also adds:
- `compute_graph_version(state)`, the **only** version derivation, reading `state` alone
- `display_text(raw)`, the server-side label sanitizer that strips Unicode Cc/Cf (bidi overrides,
  zero-width characters) and caps at 200 characters

The pseudocode in HLD §8.3.2 is authoritative.

## Acceptance Criteria
1. **Dataclasses** (frozen, `asdict`-serializable, same style as `ui/runs.py`): `RunGraph`,
   `GraphNode`, `GraphDependencyEdge`, `GraphSpawnEdge`, `GraphLoop`, and `GraphRouter`, with field
   names exactly as in HLD §14.2, including `schema_version`, `label_sanitized`, and `truncated`.
   The constants `GRAPH_SCHEMA_VERSION=1`, `GRAPH_MAX_NODES=5000`, `GRAPH_LABEL_MAX_CHARS=200`,
   `GRAPH_VERSION_HEX_CHARS=16`, `GRAPH_SOURCE_SNAPSHOT`, and `GRAPH_SOURCE_UNAVAILABLE` are named.
2. **Edges come only from `dag.iter_dependency_edges`.** A grep test asserts that `ui/graph.py`
   contains no `depends_on` iteration and no output-to-input matching logic (DRY, ADR-0017 D3).
3. **Fixture expectations** (each is its own test, with fixed timestamps):
   - (a) static linear `a→b→c`, with a snapshot → 3 nodes, dependency edges `[(a,b),(b,c)]`, no spawn edges, `spawn_data="none"`
   - (b) overseer-shape: `cp1` emits `u1,u2,u3,cp2`, and `cp2` depends on `u1..u3` → the spawn
     edges are exactly `cp1→{u1,u2,u3,cp2}`, and the dependency edges include `u1→cp2`, `u2→cp2`,
     and `u3→cp2`. **The two sets differ** (U-4).
   - (c) nested emit → `spawn_depth` values 0, 1, and 2
   - (d) loop ×3 → the clones' `iteration`/`loop_id` are set, iteration-1 body nodes have
     `iteration=1` and `loop_id=L`, and a `kind="loop"` edge exists when a downstream task depends
     on the loop id
   - (e) router with a `not_taken` cone → `is_router` true on the router task, and routers
     `[{id, router_task_id, selected}]`
   - (f) unknown dep `zzz` → a phantom node `zzz` with `missing=true`, plus a warning
   - (g) legacy (no `spec_sessions`, no `spawned_by`, but injected tasks exist) →
     `source="unavailable"`, `spawn_data="not_recorded"`, the injected tasks' edges present, two warnings
   - (h) `spec_sessions` present but snapshot `None` → `source="unavailable"`, with the
     "missing or unreadable" warning text
   - (i) spec changed mid-run (two different shas) → a warning names the latest session number
   - (j) execution ordinal: three tasks with `started_at` t2 < t1 < t3 → ordinals by time. Two
     tasks with equal `started_at` → ordered by id. A never-started task → `None`.
   - (k) origin fallback: a task in `spawned_by` → origin from the record. An orphan in
     `state.tasks` only → origin from `ts`. A phantom → `"static"`.
4. **Sanitizer.**
   - `display_text("a‮b​c")` → `("abc", True)`.
   - A 1,000-char id → a label of length ≤ 200 ending in "…", with `label_sanitized=True`.
   - A plain id → `(id, False)`.
   - The node `id` field stays **raw** (it is the join key).
5. **Early cap** (dev-security MEDIUM). A state with 50,000 tasks builds in < 1 s (timer test at
   3× headroom: assert < 3 s). It returns exactly `GRAPH_MAX_NODES` nodes, sets `truncated=True`,
   and invents no phantom nodes from cut tasks.
6. **Forged spawn cycle** (`a→b`, `b→a` in `spawned_by`) terminates, with both depths `None` and no exception.
7. **Determinism.** Building twice from the same inputs yields byte-identical
   `json.dumps(asdict(g), sort_keys=False)`.
8. **Version sensitivity.** `compute_graph_version` changes when an injected task is added, when a
   `spec_sessions` entry with a new sha is appended, or when `loop_iterations` changes. It does
   **not** change when any task's `status`, `attempts`, `started_at`, or `cumulative_cost_usd`
   changes.
9. **Perf** (NFR-3). A generated 200-node/500-edge state builds in ≤ 150 ms on the dev machine
   (value recorded in STATUS). The CI test asserts ≤ 450 ms.
10. `ui/graph.py` imports nothing from fastapi and does no file I/O, which keeps it importable
    without the `[ui]` extra (grep test). Line and branch coverage of `ui/graph.py` is ≥ 95%.

## Risks
- Misreading the loop-dep semantics. Mitigated by reusing `dag.iter_dependency_edges` (T-mzT3BW).
- Performance of repeated `loop_of_body_task` lookups: precompute a `body_id → loop_id` map once.

## Dependencies
- `T-AZzgT8` (`SpawnRecord`), `T-l7t6TT` (`SpecSession`, `WorkflowSnapshot`), and `T-mzT3BW`
  (iterator), all after Gate G1.

## Pseudocode / Algorithm
HLD §8.3.2 (verbatim, authoritative): `build_run_graph`, `label_for`, `display_text`, and
`compute_graph_version`.

## Schemas / Interface Notes
- Interface: `build_run_graph(state, snapshot) -> RunGraph`,
  `compute_graph_version(state) -> str`, `display_text(raw) -> tuple[str, bool]`.
- Data schema: HLD §14.2 `RunGraph`.
- Triggers / events: N/A. Artifacts: none (pure).

## Handoff Boundary
- Upstream: engine data models plus the iterator.
- Downstream: `T-AsQ77e` wraps it in I/O and HTTP. `T-VcN4pt` changes only `label_for`.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-M4qboy-run-graph-builder/`
- Large outputs: N/A
