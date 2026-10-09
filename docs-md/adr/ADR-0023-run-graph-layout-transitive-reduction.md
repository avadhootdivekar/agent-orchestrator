# ADR-0023 — Run-graph layout on the transitive reduction

- **Status:** Accepted — implemented in `ui/src/graph/layout.ts` (2026-10-09)
- **Date:** 2026-10-09
- **Deciders:** Avadhoot Divekar (owner, ask A4 of overseer run `o-vsg0u9-requirements`); architect (design note
  `w01-04-graph-layout`); developer (implementation)
- **Related:** ADR-0017 (D5: `computeLayout` is the single, swappable layout seam — amended here), HLD §8.5

## Context

On large overseer-style workflows the dependency graph grew into a "hyperbolic cone": later waves were wider than
their task count justified and rank columns drifted sideways, and appending a wave re-shuffled existing nodes.
Measured root cause: dagre routes every transitively redundant edge (e.g. a unit depending on both the previous
checkpoint and `intake`, or on a wave k-2 unit) through one dummy node per crossed rank; the dummies reserve lanes
in every rank. On a 10-wave fixture (6,6,5,5,4,4,3,3,2,2 units) the widest rank spanned 1025 px ("skip") and
1731 px ("charter") against an ideal 408 px, and appending a wave moved up to 38 existing nodes. Switching
dagre's `ranker` produced byte-identical output, so dagre tuning does not touch the cause.

## Decision

1. `computeLayout` feeds dagre only the **transitive reduction** of the edge set (`transitiveReduction` /
   `redundantEdges`, exported from `layout.ts`): O(V·E) reachability, input order preserved, duplicates /
   self-loops / unknown endpoints ignored. If the graph has a cycle nothing is reduced (dagre's acyclic pass
   copes, as before).
2. The public `computeLayout(nodes, edges, view)` contract and the layout trigger (graph version / view /
   show-unrelated only, never task-status polls) are unchanged. Layout stays a pure function of structure.
3. **No dependency information is dropped by the layout:** only the layout ignores the redundant edges. (Drawing is
   governed by the Amendment below.)

## Alternatives rejected / deferred

| Option | Outcome |
|---|---|
| dagre tuning (ranker, weights, separations) | Rejected as sole fix — no measured effect on the cause. |
| Force-directed (Obsidian-style) | Rejected for default: no order axis, continuous movement, violates "no unprompted node movement". |
| Wave / fan collapsing | Deferred: large (new node type, edge re-targeting); follow-up for >50-wide waves. |
| "Hide redundant edges" toggle, Relayout button | **Implemented (2026-10-09, w04-02)** — see "Amendment" below. |
| Previous-position pinning | Deferred. |

## Consequences / evidence

- Fixtures (plain, skip, charter, charter+skip) in `ui/src/test/graph-layout.test.ts`: every wave's span is
  ≤ `n·(NODE_HEIGHT+NODE_SEP) − NODE_SEP`; a wave with ≤ tasks than the previous is no wider; appending a wave
  moves 0 existing nodes; same input gives identical positions. The width/stability tests fail (6 failures)
  with the reduction disabled.
- Remaining limit: genuinely non-redundant long edges still cost lanes.
- Synthetic fixtures only; not yet checked visually against a captured live `/graph` payload.

## Amendment (w04-02): edge drawing and Relayout

Redundant edges are routed by the canvas as straight lines through intermediate nodes (90-109 of them on the large
fixture), so:

- New toolbar toggle **"Hide redundant edges (N)"** (`GraphPrefs.hideRedundantEdges`, persisted in the same
  localStorage prefs as the other graph prefs). **Default: on** — redundant edges are not drawn. `N` is the count over
  the currently visible nodes (`redundantEdges` from `layout.ts`). Unchecking draws every edge again. This affects drawing
  only; layout input is unchanged.
- New toolbar button **"Relayout"**: the only way to re-run `computeLayout` outside a graph / view / show-unrelated
  change. It clears drag offsets and refits the viewport. Nothing moves nodes unprompted. "Reset layout" (drop drag
  offsets only) is kept.
