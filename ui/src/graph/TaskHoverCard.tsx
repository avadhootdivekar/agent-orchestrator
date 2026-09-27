/**
 * Hover preview card (`T-pAi0Cv`, HLD §8.7, ADR-0017 D6).
 *
 * A React Flow `<NodeToolbar isVisible>` beside the node, not scaled by zoom. Open/close delay
 * timing and the pointer-hover / keyboard-focus wiring that decides WHEN to show it live in
 * `RunGraph.tsx` (the file that already owns all other node interaction) -- this component is
 * purely presentational: given a node id and a visibility flag, it looks up the node/stat and
 * renders the fixed HLD §8.7 hover content, or nothing at all.
 *
 * Text-only (D-5): every dynamic value below is plain JSX text interpolation, never raw/
 * unescaped HTML injection -- an agent-authored label/id must never reach the DOM as markup.
 */
import { NodeToolbar, Position } from "@xyflow/react";
import { StatusChip } from "../components/common";
import { formatCost, formatDuration } from "../format";
import type { RunGraph as RunGraphData, TaskStat } from "../types";

export interface TaskHoverCardProps {
  /** Node id to preview, or `null` when nothing is hovered/focused right now. */
  nodeId: string | null;
  /** True once the caller's open delay has elapsed; suppressed while panning/dragging. */
  isVisible: boolean;
  graph: RunGraphData;
  statsById: Map<string, TaskStat>;
  /** Layout direction (`computeLayout`'s own) -- picks which side of the node the card sits on. */
  direction: "LR" | "TB";
}

/** Status shown when a task has no stat yet -- mirrors `TaskNode.tsx`'s own `PENDING_STATUS`. */
const HOVER_PENDING_STATUS = "pending";

export function TaskHoverCard({
  nodeId,
  isVisible,
  graph,
  statsById,
  direction,
}: TaskHoverCardProps) {
  if (!nodeId) return null;
  const node = graph.nodes.find((n) => n.id === nodeId);
  if (!node) return null;
  const stat = statsById.get(nodeId) ?? null;
  const retries = stat ? Math.max(0, stat.attempts - 1) : null;

  return (
    <NodeToolbar
      nodeId={nodeId}
      isVisible={isVisible}
      position={direction === "LR" ? Position.Right : Position.Bottom}
      className="task-hover-card"
    >
      <div className="task-hover-card-label">{node.label}</div>
      {node.label_sanitized ? (
        <div className="task-hover-card-note">hidden characters removed</div>
      ) : null}
      <StatusChip status={stat?.status ?? HOVER_PENDING_STATUS} />
      <dl className="task-hover-card-stats">
        <div>
          <dt>Duration</dt>
          <dd>{formatDuration(stat?.duration_seconds)}</dd>
        </div>
        <div>
          <dt>Cost</dt>
          <dd>{formatCost(stat?.cost_usd)}</dd>
        </div>
        <div>
          <dt>Attempts</dt>
          <dd>{stat?.attempts ?? "—"}</dd>
        </div>
      </dl>
      <div className="task-hover-card-retries">retries: {retries ?? "—"}</div>
      <div className="task-hover-card-hint">Click for details</div>
    </NodeToolbar>
  );
}

export default TaskHoverCard;
