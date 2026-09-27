import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunGraph } from "../graph/RunGraph";
import { HOVER_CLOSE_DELAY_MS, HOVER_OPEN_DELAY_MS } from "../graph/model";
import type { RunGraph as RunGraphData, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `RunGraph` detail-reveal integration tests (`T-pAi0Cv` AC-1, AC-3's focus-return, AC-6's
 * missing-node smoke path). Renders the REAL, unmocked `@xyflow/react` (T-adVpTj's jsdom shims,
 * same pattern `task-node.test.tsx`/`run-detail-graph-tab.test.tsx` use) rather than the
 * toolbar-style full `<ReactFlow>` stub: the hover card's `<NodeToolbar>` only renders once its
 * node is registered in React Flow's own internal store, and AC-3's "returns focus to the u1
 * node" needs a real, focusable `.react-flow__node` DOM element to assert against.
 *
 * `run-graph-panel-navigation.test.tsx` covers AC-4 (the `setCenter` spy) with the OTHER,
 * fully-mocked `@xyflow/react` pattern `run-graph-toolbar.test.tsx` (`T-aHktGB`) already uses --
 * that assertion needs a `setCenter` spy, not real node DOM.
 */

const fixture = fixtureData as unknown as RunGraphData;

const BASE_STAT: TaskStat = {
  id: "n",
  status: "succeeded",
  attempts: 1,
  started_at: null,
  ended_at: null,
  duration_seconds: null,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: 0,
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

function makeTasks(): TaskStat[] {
  return fixture.nodes
    .filter((n) => !n.missing)
    .map((n) => ({
      ...BASE_STAT,
      id: n.id,
      origin: n.origin,
      route: n.route,
      attempts: n.id === "u1" ? 2 : 1,
      duration_seconds: n.id === "u1" ? 240 : null,
      cost_usd: n.id === "u1" ? 1.5 : 0,
    }));
}

/** The real React Flow node wrapper for `id` -- present once `<RunGraph>` finishes laying out. */
async function findNodeElement(id: string): Promise<HTMLElement> {
  return waitFor(() => {
    const el = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`);
    if (!el) throw new Error(`no rendered node "${id}" yet`);
    return el;
  });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("RunGraph hover card (AC-1)", () => {
  it("shows no card before HOVER_OPEN_DELAY_MS and shows it at >= that delay; closes after leave", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    const u1 = await findNodeElement("u1");

    act(() => {
      fireEvent.mouseEnter(u1);
    });
    expect(document.querySelector(".task-hover-card")).toBeNull();

    act(() => {
      vi.advanceTimersByTime(HOVER_OPEN_DELAY_MS - 1);
    });
    expect(document.querySelector(".task-hover-card")).toBeNull();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    const card = document.querySelector(".task-hover-card");
    expect(card).not.toBeNull();
    expect(card!.textContent).toContain("retries: 1"); // attempts: 2 -> retries: max(0, 2-1)

    act(() => {
      fireEvent.mouseLeave(u1);
    });
    act(() => {
      vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS - 1);
    });
    expect(document.querySelector(".task-hover-card")).not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(document.querySelector(".task-hover-card")).toBeNull();
  });

  it("keyboard focus also opens the card", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    const u1 = await findNodeElement("u1");

    act(() => {
      u1.focus();
    });
    act(() => {
      vi.advanceTimersByTime(HOVER_OPEN_DELAY_MS);
    });
    expect(document.querySelector(".task-hover-card")).not.toBeNull();

    act(() => {
      u1.blur();
    });
    act(() => {
      vi.advanceTimersByTime(HOVER_CLOSE_DELAY_MS);
    });
    expect(document.querySelector(".task-hover-card")).toBeNull();
  });
});

describe("RunGraph detail panel close + focus-return (AC-3)", () => {
  it("Esc closes the panel and returns focus to the u1 node", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    const u1 = await findNodeElement("u1");

    fireEvent.click(u1);
    const panel = await screen.findByRole("complementary", { name: "Task details" });
    expect(panel).toHaveFocus();

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("complementary", { name: "Task details" })).toBeNull();
    await waitFor(() => expect(u1).toHaveFocus());
  });

  it("a click on empty canvas (onPaneClick) closes the panel", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    const u1 = await findNodeElement("u1");

    fireEvent.click(u1);
    await screen.findByRole("complementary", { name: "Task details" });

    const pane = document.querySelector<HTMLElement>(".react-flow__pane");
    expect(pane).not.toBeNull();
    fireEvent.click(pane!);

    expect(screen.queryByRole("complementary", { name: "Task details" })).toBeNull();
  });
});

describe("RunGraph detail panel for a missing node (AC-6)", () => {
  it("clicking the phantom missing-dep node opens the panel with no crash", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    // The default view is "dependency", which shows every node -- missing-dep has a dependency
    // edge (missing-dep -> gate), so it's visible without toggling "show unrelated".
    const missingDep = await findNodeElement("missing-dep");

    fireEvent.click(missingDep);

    expect(
      await screen.findByText("Unknown task (referenced by a dependency but never defined)"),
    ).toBeInTheDocument();
  });
});
