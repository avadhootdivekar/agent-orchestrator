import { render } from "@testing-library/react";
import { ReactFlow, ReactFlowProvider } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import {
  accessibleNodeName,
  BADGE_LABELS,
  TaskNode,
  type TaskNodeData,
  type TaskNodeType,
} from "../graph/TaskNode";
import type { TaskStat, ViewNode } from "../types";

/**
 * `TaskNode` component tests (T-OjTS8O AC-2, AC-3, AC-4).
 *
 * Rendered inside a real `<ReactFlow>`/`<ReactFlowProvider>` rather than standalone: the
 * `<Handle>` sub-component reads React Flow's internal store, which only exists inside a
 * provider (matches T-adVpTj's own jsdom shims, which this suite relies on via
 * `src/test/setup.ts`).
 */

const NODE_TYPES = { task: TaskNode };

const BASE_STAT: TaskStat = {
  id: "n",
  status: "succeeded",
  attempts: 1,
  started_at: "2026-09-21T10:00:00+00:00",
  ended_at: "2026-09-21T10:05:00+00:00",
  duration_seconds: 42,
  input_tokens: 100,
  output_tokens: 50,
  cost_usd: 1.23,
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
  dispatch_cycle: 1,
  not_taken_reason: null,
};

function makeViewNode(overrides: Partial<ViewNode> & { id: string }): ViewNode {
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
    stat: null,
    ...overrides,
  };
}

/**
 * Builds the same `Node<TaskNodeData, "task">` shape `RunGraph.tsx`'s `rfNodes` builder does,
 * including `ariaLabel: accessibleNodeName(node)` -- React Flow puts that on the FOCUSABLE
 * `.react-flow__node` wrapper, not on `TaskNode`'s own root div (see `RunGraph.tsx`).
 */
function renderNode(node: ViewNode, direction: "LR" | "TB" = "LR") {
  const rfNode: TaskNodeType = {
    id: node.id,
    type: "task",
    position: { x: 0, y: 0 },
    data: { node, direction } satisfies TaskNodeData,
    width: 184,
    height: 48,
    ariaLabel: accessibleNodeName(node),
  };
  return render(
    <ReactFlowProvider>
      <div style={{ width: "400px", height: "300px" }}>
        <ReactFlow nodes={[rfNode]} edges={[]} nodeTypes={NODE_TYPES} />
      </div>
    </ReactFlowProvider>,
  );
}

/** `TaskNode`'s own rendered root (styling/text assertions -- NOT the accessible-name owner). */
function nodeRoot(container: HTMLElement): HTMLElement {
  const el = container.querySelector(".task-node");
  if (!el) throw new Error("expected a rendered TaskNode element");
  return el as HTMLElement;
}

/**
 * The actually-focusable React Flow node wrapper, which is what `Node.ariaLabel` lands on
 * (`role="group"` + `aria-label`, per `@xyflow/react`'s own `NodeWrapper`) -- the element a
 * screen reader / keyboard user actually reaches, not `TaskNode`'s inner div.
 */
function rfNodeWrapper(container: HTMLElement): HTMLElement {
  const el = container.querySelector(".react-flow__node");
  if (!el) throw new Error("expected a rendered React Flow node wrapper");
  return el as HTMLElement;
}

describe("TaskNode", () => {
  it("AC-2: the label element's text equals `label` exactly, no cost/duration in the body", () => {
    const node = makeViewNode({
      id: "cp1",
      label: "cp1",
      stat: { ...BASE_STAT, id: "cp1", status: "succeeded" },
    });
    const { container } = renderNode(node);
    const label = container.querySelector(".task-node-label");
    expect(label?.textContent).toBe("cp1");
    // formatCost/formatDuration output ($1.23 / 42s) must never appear in the node body.
    expect(container.textContent).not.toContain("$");
    expect(container.textContent).not.toMatch(/\b42s\b/);
  });

  it("AC-3/D-5: an agent-authored label renders as literal text, never HTML", () => {
    const malicious = "<img src=x onerror=alert(1)>";
    const node = makeViewNode({ id: "evil", label: malicious });
    const { container } = renderNode(node);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".task-node-label")?.textContent).toBe(malicious);
  });

  it("AC-4: accessible name includes the label, status word, and badge text (injected)", () => {
    const node = makeViewNode({
      id: "u1",
      label: "u1",
      origin: "injected",
      stat: { ...BASE_STAT, id: "u1", status: "succeeded" },
    });
    const { container } = renderNode(node);
    const name = rfNodeWrapper(container).getAttribute("aria-label");
    expect(name).toContain("u1");
    expect(name).toContain("succeeded");
    expect(name).toContain(BADGE_LABELS.injected);
  });

  it("AC-4: status is never conveyed by color alone -- the glyph and status word are always present", () => {
    const node = makeViewNode({ id: "f1", stat: { ...BASE_STAT, id: "f1", status: "failed" } });
    const { container } = renderNode(node);
    expect(rfNodeWrapper(container).getAttribute("aria-label")).toContain("failed");
    expect(container.querySelector(".task-node-glyph")?.textContent).not.toBe("");
  });

  it("includes the loop-iteration badge text for a loop-origin node", () => {
    const node = makeViewNode({ id: "dev-iter2", origin: "loop", iteration: 2 });
    const { container } = renderNode(node);
    expect(rfNodeWrapper(container).getAttribute("aria-label")).toContain(
      BADGE_LABELS.loopIteration(2),
    );
  });

  it("includes the emitter badge text for a node that spawned children", () => {
    const node = makeViewNode({ id: "cp1", is_emitter: true, children_count: 4 });
    const { container } = renderNode(node);
    expect(rfNodeWrapper(container).getAttribute("aria-label")).toContain(
      BADGE_LABELS.emitter(4),
    );
  });

  it("includes the router badge text for a router node", () => {
    const node = makeViewNode({ id: "triage", is_router: true });
    const { container } = renderNode(node);
    expect(rfNodeWrapper(container).getAttribute("aria-label")).toContain(BADGE_LABELS.router);
  });

  it("includes the missing badge text and dashed styling for a phantom missing node", () => {
    const node = makeViewNode({ id: "missing-dep", missing: true });
    const { container } = renderNode(node);
    expect(rfNodeWrapper(container).getAttribute("aria-label")).toContain(BADGE_LABELS.missing);
    expect(nodeRoot(container).className).toContain("task-node--dashed");
  });

  it("renders an unknown origin as a neutral badge rather than throwing (ADR-0017 D1)", () => {
    const node = makeViewNode({ id: "leaf", origin: "manual" });
    const { container } = renderNode(node);
    expect(rfNodeWrapper(container).getAttribute("aria-label")).toContain(
      BADGE_LABELS.unknownOrigin("manual"),
    );
  });

  it("dims and dashes not_taken/skipped nodes", () => {
    const node = makeViewNode({
      id: "skipped-1",
      stat: { ...BASE_STAT, id: "skipped-1", status: "skipped" },
    });
    const { container } = renderNode(node);
    const root = nodeRoot(container);
    expect(root.className).toContain("task-node--dashed");
    expect(root.className).toContain("task-node--dimmed");
  });

  it("positions handles left/right for the LR (dependency) direction", () => {
    const node = makeViewNode({ id: "a" });
    const { container } = renderNode(node, "LR");
    expect(container.querySelector(".react-flow__handle-left")).not.toBeNull();
    expect(container.querySelector(".react-flow__handle-right")).not.toBeNull();
  });

  it("positions handles top/bottom for the TB (spawn) direction", () => {
    const node = makeViewNode({ id: "a" });
    const { container } = renderNode(node, "TB");
    expect(container.querySelector(".react-flow__handle-top")).not.toBeNull();
    expect(container.querySelector(".react-flow__handle-bottom")).not.toBeNull();
  });

  it("has no trailing separator artifact when a node has no applicable badges", () => {
    const node = makeViewNode({ id: "plain" });
    const { container } = renderNode(node);
    const name = rfNodeWrapper(container).getAttribute("aria-label");
    expect(name).not.toBeNull();
    expect(name?.endsWith(",")).toBe(false);
  });
});
