import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunGraph } from "../graph/RunGraph";
import type { RunGraph as RunGraphData } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * Degraded banners + unsupported-schema hard stop (`T-aHktGB` AC-5, FR-7, D-4). `ReactFlow` is
 * mocked to a stub div (per `run-graph.test.tsx`'s own R-3-driven precedent) purely so its
 * presence/absence is a simple `queryByTestId` check -- the edge/node model isn't the point here.
 */

const fixture = fixtureData as unknown as RunGraphData;

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    ReactFlow: () => <div data-testid="rf-stub" />,
  };
});

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RunGraph degraded banners (AC-5)", () => {
  it("renders one banner line per warnings[] entry, as literal text -- never HTML", async () => {
    const graph: RunGraphData = { ...fixture, warnings: ["<b>x</b>", "second"] };
    vi.spyOn(api, "runGraph").mockResolvedValue(graph);
    const { container } = render(
      <RunGraph runId="run-1" tasks={[]} graphVersion={graph.graph_version} />,
    );

    await waitFor(() => expect(screen.getByTestId("rf-stub")).not.toBeNull());

    // Two distinct banner lines.
    expect(screen.getByText("<b>x</b>")).not.toBeNull();
    expect(screen.getByText("second")).not.toBeNull();
    // The first warning's markup-looking text must never become a real element.
    expect(container.querySelector("b")).toBeNull();
  });

  it("renders no banners when warnings[] is empty", async () => {
    const graph: RunGraphData = { ...fixture, warnings: [] };
    vi.spyOn(api, "runGraph").mockResolvedValue(graph);
    render(<RunGraph runId="run-1" tasks={[]} graphVersion={graph.graph_version} />);

    await waitFor(() => expect(screen.getByTestId("rf-stub")).not.toBeNull());
    expect(document.querySelector(".graph-banners")).toBeNull();
  });

  it("an unsupported schema_version shows the hard-stop message and renders no canvas", async () => {
    const graph: RunGraphData = { ...fixture, schema_version: 2 };
    vi.spyOn(api, "runGraph").mockResolvedValue(graph);
    render(<RunGraph runId="run-1" tasks={[]} graphVersion={graph.graph_version} />);

    await waitFor(() => expect(screen.getByText(/unsupported graph schema/i)).not.toBeNull());
    expect(screen.queryByTestId("rf-stub")).toBeNull();
    // The toolbar/legend, which both assume a schema this build understands, are also absent.
    expect(screen.queryByRole("radiogroup", { name: "Graph view" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Fit" })).toBeNull();
  });

  it("the supported schema_version (1) renders the canvas normally", async () => {
    const graph: RunGraphData = { ...fixture, schema_version: 1 };
    vi.spyOn(api, "runGraph").mockResolvedValue(graph);
    render(<RunGraph runId="run-1" tasks={[]} graphVersion={graph.graph_version} />);

    await waitFor(() => expect(screen.getByTestId("rf-stub")).not.toBeNull());
    expect(screen.queryByText(/unsupported graph schema/i)).toBeNull();
  });
});
