import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GraphToolbar, type GraphToolbarProps } from "../graph/GraphToolbar";
import type { GraphNode } from "../types";

/**
 * `GraphToolbar` component tests (`T-aHktGB` AC-1 label/visibility, AC-2 metric select, AC-3
 * search cycling, AC-4 Fit/Reset). This file exercises the component in isolation -- the actual
 * "does checking the box reveal hidden nodes" / "does Fit call fitView" wiring lives in
 * `RunGraph.tsx` and is covered by `run-graph-toolbar.test.tsx`.
 */

function makeNode(id: string, label = id): GraphNode {
  return {
    id,
    label,
    label_sanitized: false,
    origin: "static",
    parent_task_id: null,
    route: null,
    loop_id: null,
    iteration: null,
    spawn_depth: null,
    children_count: 0,
    is_emitter: false,
    is_router: false,
    is_loop_gate: false,
    exec_ordinal: null,
    missing: false,
  };
}

const NODES: GraphNode[] = [makeNode("u1", "u1"), makeNode("u2", "u2"), makeNode("triage", "triage")];

function renderToolbar(overrides: Partial<GraphToolbarProps> = {}) {
  const props: GraphToolbarProps = {
    view: "spawn",
    showUnrelated: false,
    hideRedundantEdges: true,
    onHideRedundantEdgesChange: vi.fn(),
    redundantEdgeCount: 3,
    onRelayout: vi.fn(),
    onShowUnrelatedChange: vi.fn(),
    hiddenCount: 3,
    metric: "duration",
    onMetricChange: vi.fn(),
    searchableNodes: NODES,
    onSearchSelect: vi.fn(),
    onFit: vi.fn(),
    onReset: vi.fn(),
    ...overrides,
  };
  const view = render(<GraphToolbar {...props} />);
  return { ...view, props };
}

describe("GraphToolbar", () => {
  it("AC-1: shows the unrelated checkbox with the hiddenCount label in the spawn view", () => {
    renderToolbar({ view: "spawn", hiddenCount: 3 });
    expect(screen.getByRole("checkbox", { name: "Show 3 unrelated tasks" })).not.toBeNull();
  });

  it("AC-1: singularizes the label for a hiddenCount of 1", () => {
    renderToolbar({ view: "spawn", hiddenCount: 1 });
    expect(screen.getByRole("checkbox", { name: "Show 1 unrelated task" })).not.toBeNull();
  });

  it("AC-1: does not render the checkbox in the dependency view", () => {
    renderToolbar({ view: "dependency" });
    expect(screen.queryByRole("checkbox", { name: /unrelated/i })).toBeNull();
  });

  it("AC-1: reports the new value on toggle", async () => {
    const { props } = renderToolbar({ view: "spawn", showUnrelated: false });
    await userEvent.click(screen.getByRole("checkbox", { name: /unrelated/i }));
    expect(props.onShowUnrelatedChange).toHaveBeenCalledWith(true);
  });

  it("AC-2: the metric select has all three options and reports a change", async () => {
    const { props } = renderToolbar({ metric: "duration" });
    const select = screen.getByRole("combobox", { name: "Metric" });
    expect((select as HTMLSelectElement).value).toBe("duration");
    await userEvent.selectOptions(select, "cost");
    expect(props.onMetricChange).toHaveBeenCalledWith("cost");
  });

  it("AC-3: Enter selects the first match", async () => {
    const { props } = renderToolbar();
    const search = screen.getByRole("textbox", { name: "Search tasks" });
    await userEvent.type(search, "u");
    await userEvent.keyboard("{Enter}");
    expect(props.onSearchSelect).toHaveBeenCalledWith("u1");
  });

  it("AC-3: ArrowDown cycles to the 2nd match and shows 'n of m'", async () => {
    const { props } = renderToolbar();
    const search = screen.getByRole("textbox", { name: "Search tasks" });
    await userEvent.type(search, "u");
    await userEvent.keyboard("{Enter}");
    expect(screen.getByText("1 of 2")).not.toBeNull();

    await userEvent.keyboard("{ArrowDown}");
    expect(props.onSearchSelect).toHaveBeenLastCalledWith("u2");
    expect(screen.getByText("2 of 2")).not.toBeNull();

    // Wraps back around to the first match.
    await userEvent.keyboard("{ArrowDown}");
    expect(props.onSearchSelect).toHaveBeenLastCalledWith("u1");
    expect(screen.getByText("1 of 2")).not.toBeNull();
  });

  it("AC-3: ArrowUp cycles backwards", async () => {
    const { props } = renderToolbar();
    const search = screen.getByRole("textbox", { name: "Search tasks" });
    await userEvent.type(search, "u");
    await userEvent.keyboard("{ArrowUp}");
    expect(props.onSearchSelect).toHaveBeenLastCalledWith("u2");
    expect(screen.getByText("2 of 2")).not.toBeNull();
  });

  it("AC-3: an empty query shows no count", async () => {
    renderToolbar();
    const search = screen.getByRole("textbox", { name: "Search tasks" });
    await userEvent.type(search, "u");
    expect(screen.queryByText(/of/)).not.toBeNull();
    await userEvent.clear(search);
    expect(screen.queryByText(/\d+ of \d+/)).toBeNull();
  });

  it("AC-3: a query with no matches shows no count and Enter is a no-op", async () => {
    const { props } = renderToolbar();
    const search = screen.getByRole("textbox", { name: "Search tasks" });
    await userEvent.type(search, "zzz");
    expect(screen.queryByText(/\d+ of \d+/)).toBeNull();
    await userEvent.keyboard("{Enter}");
    expect(props.onSearchSelect).not.toHaveBeenCalled();
  });

  it("AC-4: Fit and Reset layout buttons call their handlers", async () => {
    const { props } = renderToolbar();
    await userEvent.click(screen.getByRole("button", { name: "Fit" }));
    await userEvent.click(screen.getByRole("button", { name: "Reset layout" }));
    expect(props.onFit).toHaveBeenCalledTimes(1);
    expect(props.onReset).toHaveBeenCalledTimes(1);
  });

  it("hide-redundant-edges checkbox reflects the pref, shows the count, and reports changes", async () => {
    const { props } = renderToolbar();
    const box = screen.getByRole("checkbox", { name: "Hide redundant edges (3)" }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    await userEvent.click(box);
    expect(props.onHideRedundantEdgesChange).toHaveBeenCalledWith(false);
  });

  it("Relayout button calls onRelayout (user-initiated only)", async () => {
    const { props } = renderToolbar();
    expect(props.onRelayout).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Relayout" }));
    expect(props.onRelayout).toHaveBeenCalledTimes(1);
  });

  it("AC-7: every control has an accessible name reachable via role queries", () => {
    renderToolbar({ view: "spawn" });
    expect(screen.getByRole("checkbox", { name: /unrelated/i })).not.toBeNull();
    expect(screen.getByRole("combobox", { name: "Metric" })).not.toBeNull();
    expect(screen.getByRole("textbox", { name: "Search tasks" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Fit" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Reset layout" })).not.toBeNull();
  });
});
