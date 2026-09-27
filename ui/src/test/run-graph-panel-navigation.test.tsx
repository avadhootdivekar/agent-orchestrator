import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunGraph } from "../graph/RunGraph";
import { NODE_HEIGHT, NODE_WIDTH } from "../graph/model";
import type { RunGraph as RunGraphData, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `RunGraph` detail-panel navigation integration test (`T-pAi0Cv` AC-4). Mocks `<ReactFlow>`
 * (captures its props) AND `useReactFlow` (a `setCenter` spy) -- the SAME pattern
 * `run-graph-toolbar.test.tsx` (`T-aHktGB`) already uses for its own AC-3 search-to-focus test,
 * per the TASK.md Risk note that `setCenter` isn't otherwise observable in jsdom.
 *
 * `run-graph-detail-reveal.test.tsx` covers the hover card / focus-return with a REAL
 * `<ReactFlow>` instead -- that needs real node DOM, this needs a `setCenter` spy; the two don't
 * fit in one mocking strategy.
 */

const fixture = fixtureData as unknown as RunGraphData;

interface CapturedProps {
  nodes: { id: string; position: { x: number; y: number }; selected?: boolean }[];
  edges: { id: string }[];
  onNodeClick?: (event: unknown, node: { id: string }) => void;
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

function makeTasks(): TaskStat[] {
  return fixture.nodes
    .filter((n) => !n.missing)
    .map((n) => ({ ...BASE_STAT, id: n.id, origin: n.origin, route: n.route }));
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

beforeEach(() => {
  localStorage.clear();
  capturedProps.current = null;
  mockReactFlowInstance.setCenter.mockClear();
  mockReactFlowInstance.getZoom.mockClear().mockReturnValue(1);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Simulates clicking node `id` the same way the real `onNodeClick` prop would be invoked. */
function clickNode(id: string) {
  act(() => {
    capturedProps.current!.onNodeClick?.({}, { id });
  });
}

describe("RunGraph detail panel navigation (AC-4)", () => {
  it("clicking the cp1 parent link inside u1's panel selects cp1 and calls setCenter", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    clickNode("u1");
    const panel = await screen.findByRole("complementary", { name: "Task details" });
    expect(within(panel).getByRole("heading", { level: 2 }).textContent).toBe("u1");

    const spawnSection = within(panel).getByRole("region", { name: "Spawn" });
    await userEvent.click(within(spawnSection).getByRole("button", { name: "cp1" }));

    await waitFor(() => expect(mockReactFlowInstance.setCenter).toHaveBeenCalledTimes(1));
    const cp1 = findNode("cp1");
    expect(mockReactFlowInstance.setCenter).toHaveBeenCalledWith(
      cp1.position.x + NODE_WIDTH / 2,
      cp1.position.y + NODE_HEIGHT / 2,
      { zoom: 1 },
    );
    expect(cp1.selected).toBe(true);
    await waitFor(() =>
      expect(within(panel).getByRole("heading", { level: 2 }).textContent).toBe("cp1"),
    );
  });

  it("works in the spawn view too", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);
    await userEvent.click(await findRadio("Spawned by"));
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));

    clickNode("u1");
    const panel = await screen.findByRole("complementary", { name: "Task details" });
    const spawnSection = within(panel).getByRole("region", { name: "Spawn" });
    await userEvent.click(within(spawnSection).getByRole("button", { name: "cp1" }));

    await waitFor(() => expect(mockReactFlowInstance.setCenter).toHaveBeenCalledTimes(1));
    expect(findNode("cp1").selected).toBe(true);
  });
});
