import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunGraph } from "../graph/RunGraph";
import { NODE_HEIGHT, NODE_WIDTH, PREFS_STORAGE_KEY } from "../graph/model";
import type { RunGraph as RunGraphData, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `RunGraph` + `GraphToolbar` integration (`T-aHktGB` AC-1, AC-2, AC-3, AC-4, AC-7). Both the
 * `ReactFlow` component AND `useReactFlow` are mocked here (unlike `run-graph.test.tsx`, which
 * only mocks the component): the TASK.md Risk note calls out that `setCenter` isn't observable
 * in jsdom, and AC-4's drag simulation needs a captured `onNodesChange` to invoke directly.
 */

const fixture = fixtureData as unknown as RunGraphData;

interface CapturedProps {
  nodes: { id: string; position: { x: number; y: number }; selected?: boolean; data: unknown }[];
  edges: { id: string }[];
  onNodesChange: (changes: unknown[]) => void;
}

const capturedProps: { current: CapturedProps | null } = { current: null };

const mockReactFlowInstance = {
  setCenter: vi.fn(),
  getZoom: vi.fn(() => 1),
  fitView: vi.fn(() => Promise.resolve(true)),
};

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    useReactFlow: () => mockReactFlowInstance,
    ReactFlow: (props: CapturedProps) => {
      capturedProps.current = props;
      return <div data-testid="rf-stub" />;
    },
  };
});

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

/** `cp1` gets the max cost, `triage` gets zero -- exercises both metricFraction extremes. */
function makeTasks(): TaskStat[] {
  return fixture.nodes
    .filter((n) => !n.missing)
    .map((n) => ({
      ...BASE_STAT,
      id: n.id,
      origin: n.origin,
      route: n.route,
      cost_usd: n.id === "cp1" ? 10 : n.id === "triage" ? 0 : 3,
    }));
}

function findNode(id: string) {
  const node = capturedProps.current?.nodes.find((n) => n.id === id);
  if (!node) throw new Error(`no captured node "${id}"`);
  return node;
}

async function findRadio(name: string) {
  const radios = await screen.findAllByRole("radio");
  const match = radios.find((radio) => radio.textContent === name);
  if (!match) throw new Error(`no radio option named "${name}"`);
  return match;
}

async function switchToSpawnView() {
  await userEvent.click(await findRadio("Spawned by"));
  await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));
}

beforeEach(() => {
  localStorage.clear();
  capturedProps.current = null;
  mockReactFlowInstance.setCenter.mockClear();
  mockReactFlowInstance.getZoom.mockClear().mockReturnValue(1);
  mockReactFlowInstance.fitView.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RunGraph unrelated filter (AC-1)", () => {
  it("hides the 3 spawn-unrelated fixture nodes by default and shows them once checked", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await switchToSpawnView();

    const checkbox = screen.getByRole("checkbox", { name: "Show 3 unrelated tasks" });
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    const idsBefore = capturedProps.current!.nodes.map((n) => n.id);
    expect(idsBefore).not.toContain("triage");
    expect(idsBefore).not.toContain("dev");
    expect(idsBefore).not.toContain("missing-dep");

    await userEvent.click(checkbox);

    await waitFor(() => {
      const idsAfter = capturedProps.current!.nodes.map((n) => n.id);
      expect(idsAfter).toContain("triage");
      expect(idsAfter).toContain("dev");
      expect(idsAfter).toContain("missing-dep");
    });
  });

  it("does not render the checkbox in the dependency view", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));
    expect(screen.queryByRole("checkbox", { name: /unrelated/i })).toBeNull();
  });
});

describe("RunGraph metric strip (AC-2)", () => {
  it("Cost gives the priciest task a fraction of 1 and the zero-cost task 0", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Metric" }), "cost");

    await waitFor(() => {
      expect((findNode("cp1").data as { metricFraction: number | null }).metricFraction).toBe(1);
      expect((findNode("triage").data as { metricFraction: number | null }).metricFraction).toBe(0);
    });
  });

  it("None renders no metric fraction (null) for any node", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Metric" }), "none");

    await waitFor(() => {
      for (const node of capturedProps.current!.nodes) {
        expect((node.data as { metricFraction: number | null }).metricFraction).toBeNull();
      }
    });
  });
});

describe("RunGraph search-to-focus (AC-3)", () => {
  it("Enter centers and selects the first (only) match", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    await userEvent.type(screen.getByRole("textbox", { name: "Search tasks" }), "u1");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(mockReactFlowInstance.setCenter).toHaveBeenCalledTimes(1));
    const u1 = findNode("u1");
    expect(mockReactFlowInstance.setCenter).toHaveBeenCalledWith(
      u1.position.x + NODE_WIDTH / 2,
      u1.position.y + NODE_HEIGHT / 2,
      { zoom: 1 },
    );
    expect(u1.selected).toBe(true);
  });

  it("a match hidden by the unrelated filter auto-enables it, then centers", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await switchToSpawnView();
    expect(capturedProps.current!.nodes.map((n) => n.id)).not.toContain("triage");

    await userEvent.type(screen.getByRole("textbox", { name: "Search tasks" }), "triage");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => {
      const checkbox = screen.getByRole("checkbox", { name: /unrelated/i }) as HTMLInputElement;
      expect(checkbox.checked).toBe(true);
    });
    await waitFor(() => expect(mockReactFlowInstance.setCenter).toHaveBeenCalledTimes(1));
    const triage = findNode("triage");
    expect(mockReactFlowInstance.setCenter).toHaveBeenCalledWith(
      triage.position.x + NODE_WIDTH / 2,
      triage.position.y + NODE_HEIGHT / 2,
      { zoom: 1 },
    );
    expect(triage.selected).toBe(true);
  });
});

describe("RunGraph fit and reset (AC-4)", () => {
  it("Fit calls fitView", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: "Fit" }));

    expect(mockReactFlowInstance.fitView).toHaveBeenCalledTimes(1);
  });

  it("Reset restores the computeLayout position after a simulated drag", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    const original = { ...findNode("cp1").position };
    const dragged = { x: original.x + 500, y: original.y + 500 };

    act(() => {
      capturedProps.current!.onNodesChange([{ id: "cp1", type: "position", position: dragged }]);
    });
    await waitFor(() => expect(findNode("cp1").position).toEqual(dragged));

    await userEvent.click(screen.getByRole("button", { name: "Reset layout" }));

    await waitFor(() => expect(findNode("cp1").position).toEqual(original));
  });
});

describe("RunGraph toolbar keyboard reachability (AC-7)", () => {
  it("every toolbar control is a Tab stop, in visual order, with an accessible name", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await switchToSpawnView();

    const toolbarRow = document.querySelector(".graph-toolbar-row") as HTMLElement;
    const spawnedByRadio = within(toolbarRow).getByRole("radio", { name: "Spawned by" });
    const checkbox = within(toolbarRow).getByRole("checkbox", { name: /unrelated/i });
    const metric = within(toolbarRow).getByRole("combobox", { name: "Metric" });
    const search = within(toolbarRow).getByRole("textbox", { name: "Search tasks" });
    const fit = within(toolbarRow).getByRole("button", { name: "Fit" });
    const reset = within(toolbarRow).getByRole("button", { name: "Reset layout" });

    // The checked radio is the group's one Tab stop (roving tabindex, ARIA APG radiogroup).
    spawnedByRadio.focus();
    expect(document.activeElement).toBe(spawnedByRadio);

    await userEvent.tab();
    expect(document.activeElement).toBe(checkbox);
    await userEvent.tab();
    expect(document.activeElement).toBe(
      within(toolbarRow).getByRole("checkbox", { name: /redundant edges/i }),
    );
    await userEvent.tab();
    expect(document.activeElement).toBe(metric);
    await userEvent.tab();
    expect(document.activeElement).toBe(search);
    await userEvent.tab();
    expect(document.activeElement).toBe(fit);
    await userEvent.tab();
    expect(document.activeElement).toBe(reset);
    await userEvent.tab();
    expect(document.activeElement).toBe(
      within(toolbarRow).getByRole("button", { name: "Relayout" }),
    );
  });
});

describe("RunGraph redundant-edge toggle and Relayout (A4)", () => {
  // cp1 -> triage -> leaf, plus the redundant shortcut cp1 -> leaf.
  const redundantFixture = {
    ...fixture,
    dependency_edges: [
      ...fixture.dependency_edges,
      { source: "cp1", target: "leaf\u200b", kind: "explicit", via: null },
    ],
  } as unknown as RunGraphData;
  const edgeIds = () => capturedProps.current!.edges.map((e) => e.id);

  async function renderGraph() {
    vi.spyOn(api, "runGraph").mockResolvedValue(redundantFixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));
  }

  it("hides redundant edges by default, shows them when unchecked, and persists the pref", async () => {
    await renderGraph();
    const box = screen.getByRole("checkbox", { name: "Hide redundant edges (1)" }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    const hiddenCount = capturedProps.current!.edges.length;

    await userEvent.click(box);
    await waitFor(() => expect(capturedProps.current!.edges.length).toBe(hiddenCount + 1));
    expect(edgeIds().some((id) => id.includes("cp1") && id.includes("leaf"))).toBe(true);
    expect(JSON.parse(localStorage.getItem(PREFS_STORAGE_KEY)!).hideRedundantEdges).toBe(false);

    await userEvent.click(box);
    await waitFor(() => expect(capturedProps.current!.edges.length).toBe(hiddenCount));
  });

  it("Relayout is user-initiated: no relayout on render, one fitView + cleared drags on click", async () => {
    await renderGraph();
    const original = { ...findNode("cp1").position };
    const dragged = { x: original.x + 300, y: original.y + 300 };
    act(() => {
      capturedProps.current!.onNodesChange([{ id: "cp1", type: "position", position: dragged }]);
    });
    await waitFor(() => expect(findNode("cp1").position).toEqual(dragged));
    mockReactFlowInstance.fitView.mockClear();

    await userEvent.click(screen.getByRole("button", { name: "Relayout" }));
    await waitFor(() => expect(findNode("cp1").position).toEqual(original));
    expect(mockReactFlowInstance.fitView).toHaveBeenCalledTimes(1);
  });
});
