/**
 * Run graph auto-layout via dagre.
 *
 * Pure: no React, no React Flow (HLD §8.5, ADR-0017 D5). Kept as its own module — separate
 * from `model.ts` — so the dagre dependency is isolated to one small, swappable file: D5's
 * whole point is that a future move to elkjs (async/worker) touches only this file, not any
 * caller, because `computeLayout` is already `async` by contract today even though dagre's own
 * `layout()` call is synchronous.
 *
 * NFR-5: no numeric literal other than `0`/`1` appears below; `NODE_WIDTH`/`NODE_HEIGHT` are
 * halved to convert dagre's center-point coordinates to the top-left corner React Flow expects
 * — the same `/2` the HLD's own pseudocode (§8.5) uses on these named constants.
 */

import dagre, { type EdgeLabel, type GraphLabel, type NodeLabel } from "@dagrejs/dagre";
import type { GraphView, LayoutResult } from "../types";
import { NODE_HEIGHT, NODE_SEP, NODE_WIDTH, RANK_SEP } from "./model";

/** Dependency view reads left-to-right (execution order); spawn view reads top-to-bottom (a tree). */
const RANKDIR_BY_VIEW: Record<GraphView, LayoutResult["direction"]> = {
  dependency: "LR",
  spawn: "TB",
};

/** The minimal shape `computeLayout` needs from a node — either `GraphNode` or `ViewNode` fits. */
interface LayoutableNode {
  id: string;
}

/** The minimal shape `computeLayout` needs from an edge — any of the view-model edge types fit. */
interface LayoutableEdge {
  source: string;
  target: string;
}

/**
 * Lay out `nodes`/`edges` with dagre and return each node's top-left position plus the
 * direction used. Async by contract (ADR-0017 D5), so a later swap to an async/worker layout
 * engine changes no caller.
 *
 * - Dependency view lays out LR, spawn view TB.
 * - Edges whose source or target isn't in `nodes` are skipped rather than thrown on — a
 *   defensive stance against partial/inconsistent input, independent of whatever guarantees
 *   the backend graph builder itself makes.
 * - Deterministic: the same `nodes`/`edges`/`view` always produce the same positions, because
 *   nodes are inserted in the given (input) order and dagre's layout has no randomness.
 * - Tolerates a cycle: dagre's own acyclic pass runs before ranking, so a cyclic edge set lays
 *   out (some way) rather than throwing.
 */
export async function computeLayout(
  nodes: LayoutableNode[],
  edges: LayoutableEdge[],
  view: GraphView,
): Promise<LayoutResult> {
  const direction = RANKDIR_BY_VIEW[view];

  const g = new dagre.graphlib.Graph<GraphLabel, NodeLabel, EdgeLabel>();
  g.setGraph({ rankdir: direction, ranksep: RANK_SEP, nodesep: NODE_SEP });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  const positions = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const laidOut = g.node(n.id);
    positions.set(n.id, {
      x: (laidOut.x ?? 0) - NODE_WIDTH / 2,
      y: (laidOut.y ?? 0) - NODE_HEIGHT / 2,
    });
  }

  return { direction, positions };
}
