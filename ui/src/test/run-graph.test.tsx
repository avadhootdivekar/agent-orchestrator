import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { edgesForView, readPrefs } from "../graph/model";
import * as layoutModule from "../graph/layout";
import { RunGraph } from "../graph/RunGraph";
import type { RunGraph as RunGraphData, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `RunGraph` component tests (T-OjTS8O AC-1, AC-5).
 *
 * `@xyflow/react`'s `ReactFlow` is mocked to capture the exact `nodes`/`edges` props it
 * receives -- per TASK.md's own R-3 risk note, jsdom edge-path rendering is unreliable, so the
 * edge MODEL is asserted at the prop level rather than by inspecting SVG DOM.
 */

const fixture = fixtureData as unknown as RunGraphData;

const capturedProps: { current: { nodes: { id: string }[]; edges: { id: string }[] } | null } = {
  current: null,
};

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    ReactFlow: (props: { nodes: { id: string }[]; edges: { id: string }[] }) => {
      capturedProps.current = { nodes: props.nodes, edges: props.edges };
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

function sortedIds(items: { id: string }[]): string[] {
  return items.map((item) => item.id).sort();
}

beforeEach(() => {
  localStorage.clear();
  capturedProps.current = null;
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function findRadio(name: string) {
  const radios = await screen.findAllByRole("radio");
  const match = radios.find((radio) => radio.textContent === name);
  if (!match) throw new Error(`no radio option named "${name}"`);
  return match;
}

describe("RunGraph view toggle (AC-1)", () => {
  it("starts on Execution order, checked, with the dependency edge model", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);

    const executionOrder = await findRadio("Execution order");
    const spawnedBy = await findRadio("Spawned by");
    expect(executionOrder.getAttribute("aria-checked")).toBe("true");
    expect(spawnedBy.getAttribute("aria-checked")).toBe("false");

    await waitFor(() => expect(capturedProps.current).not.toBeNull());
    await waitFor(() => expect(capturedProps.current!.edges.length).toBeGreaterThan(0));
    expect(sortedIds(capturedProps.current!.edges)).toEqual(
      sortedIds(edgesForView(fixture, "dependency")),
    );
  });

  it("ArrowRight switches to Spawned by and persists it via writePrefs", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);

    const executionOrder = await findRadio("Execution order");
    executionOrder.focus();
    await userEvent.keyboard("{ArrowRight}");

    await waitFor(() => expect(readPrefs().view).toBe("spawn"));
    expect((await findRadio("Spawned by")).getAttribute("aria-checked")).toBe("true");
    expect((await findRadio("Execution order")).getAttribute("aria-checked")).toBe("false");
  });

  it("clicking Spawned by switches the view and the edge model passed to React Flow", async () => {
    vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    render(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion={fixture.graph_version} />);

    await userEvent.click(await findRadio("Spawned by"));

    await waitFor(() => expect(readPrefs().view).toBe("spawn"));
    await waitFor(() => {
      expect(sortedIds(capturedProps.current!.edges)).toEqual(
        sortedIds(edgesForView(fixture, "spawn")),
      );
    });
  });
});

describe("RunGraph refetch gating (AC-5)", () => {
  it("fetches api.runGraph exactly once across repeated renders with the same graphVersion", async () => {
    const spy = vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    const { rerender } = render(
      <RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v1" />,
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    rerender(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v1" />);
    rerender(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v1" />);
    rerender(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v1" />);

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("fetches exactly one more time when graphVersion changes", async () => {
    const spy = vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    const { rerender } = render(
      <RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v1" />,
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    rerender(<RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v2" />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });

  it("changing only tasks triggers no refetch and no computeLayout call", async () => {
    const fetchSpy = vi.spyOn(api, "runGraph").mockResolvedValue(fixture);
    const layoutSpy = vi.spyOn(layoutModule, "computeLayout");
    const { rerender } = render(
      <RunGraph runId="run-1" tasks={makeTasks()} graphVersion="v1" />,
    );
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(capturedProps.current!.nodes.length).toBeGreaterThan(0));
    const layoutCallsAfterMount = layoutSpy.mock.calls.length;
    // Sanity check: the spy must have actually intercepted the mount-time layout call, or the
    // "unchanged call count" assertion below would be vacuously true.
    expect(layoutCallsAfterMount).toBeGreaterThan(0);
    const positionBefore = (
      capturedProps.current!.nodes[0] as unknown as { position: { x: number; y: number } }
    ).position;

    const changedTasks = makeTasks().map((task) => ({ ...task, status: "running" }));
    rerender(<RunGraph runId="run-1" tasks={changedTasks} graphVersion="v1" />);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(layoutSpy.mock.calls.length).toBe(layoutCallsAfterMount);
    // The layout Map itself is unreplaced, so the SAME position object comes back -- a
    // second, independent signal (beyond the spy count) that no re-layout happened.
    const positionAfter = (
      capturedProps.current!.nodes[0] as unknown as { position: { x: number; y: number } }
    ).position;
    expect(positionAfter).toBe(positionBefore);
  });
});
