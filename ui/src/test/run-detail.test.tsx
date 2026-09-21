import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, afterEach, vi } from "vitest";
import { RunDetail } from "../components/RunDetail";
import type { RunDetail as RunDetailData, TaskStat } from "../types";

/**
 * E-1cecSx B4 (design doc §4): cache-hit-rate visibility must be on-demand/expandable,
 * never a new default column on the main task table. These tests exercise the actual
 * rendered task table, not the underlying formatter alone, so a regression that widens
 * the table into a default column would be caught here too.
 */

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
};

function makeDetail(tasks: TaskStat[]): RunDetailData {
  return {
    summary: {
      run_id: "run-1",
      workflow_id: "demo",
      status: "succeeded",
      started_at: "2026-09-21T10:00:00+00:00",
      updated_at: "2026-09-21T10:05:00+00:00",
      task_count: tasks.length,
      task_counts: { succeeded: tasks.length },
      cost_usd: 0.05,
      input_tokens: 1000,
      output_tokens: 200,
      wall_seconds: 300,
      active_seconds: 300,
      is_terminal: true,
      is_live: false,
      launch_id: null,
    },
    tasks,
    tripped_breakers: [],
    route_decisions: {},
    monitor_decisions: [],
    run_dir: "/ws/.orchestrator/runs/run-1",
    is_live: false,
    launch: null,
    integration: null,
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

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("RunDetail cache-effectiveness disclosure", () => {
  it("does not add a new column header to the main task table", async () => {
    vi.stubGlobal("fetch", mockFetch(makeDetail([BASE_TASK])));
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    await screen.findByText("task-a");
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toEqual([
      "Task",
      "Status",
      "Attempts",
      "Duration",
      "Tokens",
      "Cost",
      "Integration",
      "Outputs",
    ]);
  });

  it("renders the cache hit rate/read/creation figures inside an on-demand disclosure", async () => {
    const task: TaskStat = {
      ...BASE_TASK,
      cache_read_tokens: 4000,
      cache_creation_tokens: 1000,
      cache_hit_rate: 0.8,
    };
    vi.stubGlobal("fetch", mockFetch(makeDetail([task])));
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    const row = (await screen.findByText("task-a")).closest("tr")!;
    const disclosure = within(row).getByText("cache").closest("details")!;
    expect(disclosure.tagName).toBe("DETAILS");
    // Collapsed by default -- the whole point of "on-demand", not always-visible.
    expect(disclosure.hasAttribute("open")).toBe(false);

    expect(within(row).getByText(/hit rate: 80\.0%/)).toBeInTheDocument();
    expect(within(row).getByText(/cache read: 4\.0K/)).toBeInTheDocument();
    expect(within(row).getByText(/cache creation: 1\.0K/)).toBeInTheDocument();
  });

  it("renders n/a (not 0%) for the zero-denominator case", async () => {
    vi.stubGlobal("fetch", mockFetch(makeDetail([BASE_TASK])));
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    const row = (await screen.findByText("task-a")).closest("tr")!;
    expect(within(row).getByText(/hit rate: n\/a/)).toBeInTheDocument();
    expect(within(row).queryByText(/hit rate: 0\.0%/)).toBeNull();
  });
});
