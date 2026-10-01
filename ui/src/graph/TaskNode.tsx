/**
 * Run graph node renderer (`T-OjTS8O`, HLD §8.6 "Node (`TaskNode`, memoized)").
 *
 * A 184x48 rounded rectangle: a status stripe + glyph, the task label as plain text (U-6, D-5 —
 * rendered via ordinary JSX text interpolation only, never raw/unescaped HTML injection, since
 * ids/labels can be agent-authored), and top-right micro-badges. Every badge carries a text
 * alternative (D-7 — status/structure is never color-or-glyph alone), rolled into the node's
 * own accessible name alongside the label and status word (see `accessibleNodeName` below).
 *
 * `BADGE_LABELS` lives here (not `model.ts`, which T-OjTS8O must not touch) so the legend
 * (`T-aHktGB`) can import the same map this node renders from — one source of truth for badge
 * text, per the HLD's own instruction that the legend reuse it.
 */
import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { statusGlyph, statusTone, type StatusTone } from "../format";
import { PANEL_PENDING_STATUS } from "./model";
import type { ViewNode } from "../types";

/**
 * Data carried by every `task`-typed React Flow node (HLD §8.6 interface note).
 *
 * Extends `Record<string, unknown>` (rather than a bare object literal) because React Flow's
 * `Node<NodeData>` generic constrains `NodeData extends Record<string, unknown>`, and a plain
 * `interface` gets no implicit index signature under `strict` — see the `@xyflow/react` own
 * `NodeProps` doc example, which uses an inline object type for the same reason.
 */
export interface TaskNodeData extends Record<string, unknown> {
  node: ViewNode;
  direction: "LR" | "TB";
}

export type TaskNodeType = Node<TaskNodeData, "task">;

/** Known `GraphNode.origin`/`SpawnEdge.origin` values (ADR-0017 D1 — origin is an open set). */
const KNOWN_ORIGINS = new Set(["static", "injected", "loop"]);

/** Statuses rendered dimmed + dashed (HLD §8.6: "not_taken or skipped ... reduced opacity ... dashed"). */
const DASHED_DIMMED_STATUSES = new Set(["not_taken", "skipped"]);

/**
 * Text alternative for every glyph badge (reviewer finding, HLD §8.6). The legend (`T-aHktGB`)
 * renders the same text next to the same glyph, so this is the one place both read from.
 */
export const BADGE_LABELS = {
  ordinal: (n: number) => `execution order ${n}`,
  injected: "injected task",
  loopIteration: (n: number) => `loop iteration ${n}`,
  emitter: (childrenCount: number) =>
    `spawned ${childrenCount} task${childrenCount === 1 ? "" : "s"}`,
  router: "router",
  missing: "missing dependency",
  unknownOrigin: (origin: string) => `${origin} origin`,
} as const;

interface Badge {
  glyph: string;
  text: string;
}

/** One node's badge set, in the fixed HLD §8.6 order: `#n ◆ ↻N ⤴k ⑂ ⚑`, plus an open-origin catch-all. */
export function badgesFor(node: ViewNode): Badge[] {
  const badges: Badge[] = [];
  if (node.exec_ordinal !== null) {
    badges.push({ glyph: `#${node.exec_ordinal}`, text: BADGE_LABELS.ordinal(node.exec_ordinal) });
  }
  if (node.origin === "injected") {
    badges.push({ glyph: "◆", text: BADGE_LABELS.injected });
  }
  if (node.origin === "loop" && node.iteration !== null) {
    badges.push({
      glyph: `↻${node.iteration}`,
      text: BADGE_LABELS.loopIteration(node.iteration),
    });
  }
  if (node.is_emitter) {
    badges.push({
      glyph: `⤴${node.children_count}`,
      text: BADGE_LABELS.emitter(node.children_count),
    });
  }
  if (node.is_router) {
    badges.push({ glyph: "⑂", text: BADGE_LABELS.router });
  }
  if (node.missing) {
    badges.push({ glyph: "⚑", text: BADGE_LABELS.missing });
  }
  if (!KNOWN_ORIGINS.has(node.origin)) {
    badges.push({ glyph: "◇", text: BADGE_LABELS.unknownOrigin(node.origin) });
  }
  return badges;
}

/** Status tone -> the CSS token that colors the stripe (never a hardcoded hex, per ui/README.md). */
function toneToken(tone: StatusTone): string {
  switch (tone) {
    case "good":
      return "var(--status-good)";
    case "critical":
      return "var(--status-critical)";
    case "active":
      return "var(--accent)";
    default:
      return "var(--text-muted)";
  }
}

/**
 * `<MiniMap nodeColor>` callback — the SAME status palette as the node's own stripe, so the
 * minimap and the canvas never disagree about what color means what.
 *
 * Typed against the generic `Node` (not `TaskNodeType`): `MiniMap`'s `nodeColor` prop is
 * contravariant over every node React Flow holds, and every node this canvas ever creates is
 * `type: "task"` with `TaskNodeData` — safe to narrow at runtime, just not at the type level.
 */
export function statusToken(node: Node): string {
  const data = node.data as Partial<TaskNodeData>;
  const status = data.node?.stat?.status ?? PANEL_PENDING_STATUS;
  return toneToken(statusTone(status));
}

/**
 * `"<label>, <status>, <badge texts>"` (D-7) — status and structure are never conveyed by
 * color/glyph alone. React Flow renders the FOCUSABLE node wrapper itself (not this
 * component's own root div) with `role="group"`, so this string is set as that wrapper's
 * `Node.ariaLabel` by `RunGraph.tsx`'s `rfNodes` builder, not as an `aria-label` in here —
 * otherwise a screen reader would land on an unlabeled outer group with a redundant, separately
 * labeled inner one.
 */
export function accessibleNodeName(node: ViewNode): string {
  const status = node.stat?.status ?? PANEL_PENDING_STATUS;
  const badgeText = badgesFor(node)
    .map((badge) => badge.text)
    .join(", ");
  return [node.label, status, badgeText].filter(Boolean).join(", ");
}

function TaskNodeComponent({ data, selected }: NodeProps<TaskNodeType>) {
  const { node, direction } = data;
  const status = node.stat?.status ?? PANEL_PENDING_STATUS;
  const tone = statusTone(status);
  const isDashedDimmed = DASHED_DIMMED_STATUSES.has(status);
  const dashed = node.missing || isDashedDimmed;
  const badges = badgesFor(node);
  const isLR = direction === "LR";

  const className = [
    "task-node",
    dashed ? "task-node--dashed" : null,
    isDashedDimmed ? "task-node--dimmed" : null,
    selected ? "task-node--selected" : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    // `title` gives the full label on hover when CSS ellipsis truncates it (HLD §8.6): the
    // node's ACCESSIBLE name (`accessibleNodeName`) is set on the focusable wrapper above this
    // div, via `Node.ariaLabel` in `RunGraph.tsx`, not here.
    <div className={className} data-tone={tone} title={node.label}>
      <Handle
        type="target"
        position={isLR ? Position.Left : Position.Top}
        className="task-node-handle"
        isConnectable={false}
      />
      <span className="task-node-stripe" aria-hidden="true" />
      <span className="task-node-glyph" aria-hidden="true">
        {statusGlyph(status)}
      </span>
      {/* Text node only (D-5): the label is agent-authored and must never reach innerHTML. */}
      <span className="task-node-label">{node.label}</span>
      {badges.length > 0 ? (
        <span className="task-node-badges" aria-hidden="true">
          {badges.map((badge) => (
            <span key={badge.text} className="tag task-node-badge">
              {badge.glyph}
            </span>
          ))}
        </span>
      ) : null}
      <Handle
        type="source"
        position={isLR ? Position.Right : Position.Bottom}
        className="task-node-handle"
        isConnectable={false}
      />
    </div>
  );
}

/** Memoized per HLD §8.6 — a run can have 160+ nodes, most of which never change per re-render. */
export const TaskNode = memo(TaskNodeComponent);
