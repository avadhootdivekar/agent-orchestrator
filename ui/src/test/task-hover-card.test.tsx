import { render } from "@testing-library/react";
import { ReactFlow, ReactFlowProvider, type Node as RFNode } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { TaskHoverCard } from "../graph/TaskHoverCard";
import { formatCost, formatDuration } from "../format";
import type { GraphNode, RunGraph, TaskStat } from "../types";

/**
 * `TaskHoverCard` presentational tests (`T-pAi0Cv` AC-1 content, AC-7 D-5).
 *
 * `<NodeToolbar>` (real, unmocked) only renders once its `nodeId` resolves in React Flow's own
 * internal store, so this mirrors `task-node.test.tsx`'s own real-`<ReactFlow>` render pattern
 * (T-adVpTj's jsdom shims) rather than the toolbar/legend "fully stub ReactFlow" pattern -- the
 * TASK.md Risk note ("assert visibility and content, not position") is exactly this case.
 */

const BASE_STAT: TaskStat = {
  id: "n",
  status: "succeeded",
  attempts: 1,
  started_at: "2026-09-21T10:00:00+00:00",
  ended_at: "2026-09-21T10:05:00+00:00",
  duration_seconds: 240,
  input_tokens: 100,
  output_tokens: 50,
  cost_usd: 1.5,
  origin: "static",
  route: null,
  output_artifact_path: null,
  outputs: [],
  integration_status: null,
  tier_reached: null,
  conflicted_count: 0,
  cache_read_tokens: 0,
  cache_creation_tokens: 0,
  cache_hit_rate: null,
  dispatch_cycle: 2,
  not_taken_reason: null,
};

function makeNode(overrides: Partial<GraphNode> & { id: string }): GraphNode {
  return {
    label: overrides.id,
    label_sanitized: false,
    origin: "static",
    parent_task_id: null,
    route: null,
    loop_id: null,
    iteration: null,
    spawn_depth: 0,
    children_count: 0,
    is_emitter: false,
    is_router: false,
    is_loop_gate: false,
    exec_ordinal: null,
    missing: false,
    ...overrides,
  };
}

function makeGraph(nodes: GraphNode[]): RunGraph {
  return {
    schema_version: 1,
    run_id: "r1",
    graph_version: "v1",
    source: "snapshot",
    spawn_data: "recorded",
    truncated: false,
    warnings: [],
    nodes,
    dependency_edges: [],
    spawn_edges: [],
    loops: [],
    routers: [],
  };
}

/** Registers `nodeId` in React Flow's real internal store so `<NodeToolbar>` finds it. */
function renderHoverCard(props: Parameters<typeof TaskHoverCard>[0]) {
  const rfNode: RFNode = props.nodeId
    ? { id: props.nodeId, type: "default", position: { x: 0, y: 0 }, data: {} }
    : { id: "placeholder", type: "default", position: { x: 0, y: 0 }, data: {} };
  return render(
    <ReactFlowProvider>
      <div style={{ width: "400px", height: "300px" }}>
        <ReactFlow nodes={[rfNode]} edges={[]}>
          <TaskHoverCard {...props} />
        </ReactFlow>
      </div>
    </ReactFlowProvider>,
  );
}

describe("TaskHoverCard", () => {
  it("AC-1: shows formatDuration/formatCost and retries: 1 when attempts == 2", () => {
    const graph = makeGraph([makeNode({ id: "u1", label: "u1" })]);
    const statsById = new Map([["u1", { ...BASE_STAT, id: "u1", attempts: 2 }]]);
    const { container } = renderHoverCard({
      nodeId: "u1",
      isVisible: true,
      graph,
      statsById,
      direction: "LR",
    });

    expect(container.textContent).toContain(formatDuration(240));
    expect(container.textContent).toContain(formatCost(1.5));
    expect(container.textContent).toContain("retries: 1");
    expect(container.textContent).toContain("Click for details");
  });

  it("renders nothing when isVisible is false", () => {
    const graph = makeGraph([makeNode({ id: "u1", label: "u1" })]);
    const { container } = renderHoverCard({
      nodeId: "u1",
      isVisible: false,
      graph,
      statsById: new Map(),
      direction: "LR",
    });
    expect(container.querySelector(".task-hover-card")).toBeNull();
  });

  it("renders nothing when nodeId is null", () => {
    const graph = makeGraph([makeNode({ id: "u1", label: "u1" })]);
    const { container } = renderHoverCard({
      nodeId: null,
      isVisible: true,
      graph,
      statsById: new Map(),
      direction: "LR",
    });
    expect(container.querySelector(".task-hover-card")).toBeNull();
  });

  it("shows 'hidden characters removed' for a sanitized label", () => {
    const graph = makeGraph([makeNode({ id: "u1", label: "leaf", label_sanitized: true })]);
    const { container } = renderHoverCard({
      nodeId: "u1",
      isVisible: true,
      graph,
      statsById: new Map(),
      direction: "LR",
    });
    expect(container.textContent).toContain("hidden characters removed");
  });

  it("degrades to em dashes with no crash when there is no stat yet (AC-6-adjacent)", () => {
    const graph = makeGraph([makeNode({ id: "u1", label: "u1" })]);
    const { container } = renderHoverCard({
      nodeId: "u1",
      isVisible: true,
      graph,
      statsById: new Map(),
      direction: "LR",
    });
    expect(container.textContent).toContain(formatDuration(null));
    expect(container.textContent).toContain(formatCost(null));
    expect(container.textContent).toContain("retries: —");
  });

  it("AC-7/D-5: a label with markup renders literally, never as HTML", () => {
    const malicious = "<img src=x onerror=alert(1)>";
    const graph = makeGraph([makeNode({ id: "evil", label: malicious })]);
    const { container } = renderHoverCard({
      nodeId: "evil",
      isVisible: true,
      graph,
      statsById: new Map(),
      direction: "LR",
    });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".task-hover-card-label")?.textContent).toBe(malicious);
  });
});
