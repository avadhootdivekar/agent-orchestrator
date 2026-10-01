import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { RunDetail } from "../components/RunDetail";
import { readPrefs } from "../graph/model";
import type { RunDetail as RunDetailData, RunGraph as RunGraphData, TaskStat } from "../types";
import fixtureData from "./fixtures/run-graph.json";

/**
 * `RunDetail`'s Table|Graph tab switch (T-OjTS8O AC-6). `../graph/RunGraph` is loaded via the
 * REAL `React.lazy` dynamic import here (not mocked) -- `run-graph.test.tsx` covers the view
 * toggle/refetch behavior in isolation with a mocked `ReactFlow`; this file only checks the tab
 * switch's own contract: feature detection, default tab, and that opening Graph is what
 * triggers the lazy load and the first `api.runGraph` call.
 */

const graphFixture = fixtureData as unknown as RunGraphData;

const BASE_TASK: TaskStat = {
  id: "task-a",
  status: "succeeded",
  attempts: 1,
  started_at: "2026-09-21T10:00:00+00:00",
  ended_at: "2026-09-21T10:05:00+00:00",
  duration_seconds: 300,
  input_tokens: 1000,
  output_tokens: 200,
  cost_usd: 0.05,
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

function makeDetail(graphVersion: string | null): RunDetailData {
  return {
    summary: {
      run_id: "run-1",
      workflow_id: "demo",
      status: "succeeded",
      started_at: "2026-09-21T10:00:00+00:00",
      updated_at: "2026-09-21T10:05:00+00:00",
      task_count: 1,
      task_counts: { succeeded: 1 },
      cost_usd: 0.05,
      input_tokens: 1000,
      output_tokens: 200,
      wall_seconds: 300,
      active_seconds: 300,
      is_terminal: true,
      is_live: false,
      launch_id: null,
    },
    tasks: [BASE_TASK],
    tripped_breakers: [],
    route_decisions: {},
    monitor_decisions: [],
    run_dir: "/ws/.orchestrator/runs/run-1",
    is_live: false,
    launch: null,
    integration: null,
    graph_version: graphVersion,
  };
}

function mockFetch(detail: RunDetailData) {
  return vi.fn(async (url: string) => {
    if (url.includes("/log")) {
      return new Response(JSON.stringify({ run_id: "run-1", launch_id: null, text: "" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(detail), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("RunDetail Table|Graph tab switch (AC-6)", () => {
  it("shows no Graph tab when graph_version is null (feature detection against an older backend)", async () => {
    vi.stubGlobal("fetch", mockFetch(makeDetail(null)));
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    await screen.findByText("task-a");
    expect(screen.queryByRole("tablist", { name: "Tasks view" })).toBeNull();
    expect(screen.getAllByRole("columnheader").length).toBeGreaterThan(0);
  });

  it("defaults to the Table tab when a Graph tab is available", async () => {
    vi.stubGlobal("fetch", mockFetch(makeDetail(graphFixture.graph_version)));
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    await screen.findByText("task-a");
    const tablist = await screen.findByRole("tablist", { name: "Tasks view" });
    expect(within(tablist).getByRole("tab", { name: "Table" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getAllByRole("columnheader").length).toBeGreaterThan(0);
  });

  it("lazily mounts RunGraph and fetches api.runGraph only once the Graph tab is opened", async () => {
    vi.stubGlobal("fetch", mockFetch(makeDetail(graphFixture.graph_version)));
    const runGraphSpy = vi.spyOn(api, "runGraph").mockResolvedValue(graphFixture);
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    await screen.findByText("task-a");
    expect(runGraphSpy).not.toHaveBeenCalled();

    const tablist = await screen.findByRole("tablist", { name: "Tasks view" });
    await userEvent.click(within(tablist).getByRole("tab", { name: "Graph" }));

    await waitFor(() => expect(runGraphSpy).toHaveBeenCalledWith("run-1"));
    expect(readPrefs().tab).toBe("graph");
    // Table markup is gone while Graph is the active tab.
    expect(screen.queryAllByRole("columnheader").length).toBe(0);

    await userEvent.click(within(tablist).getByRole("tab", { name: "Table" }));
    expect(readPrefs().tab).toBe("table");
    expect(screen.getAllByRole("columnheader").length).toBeGreaterThan(0);
  });
});
