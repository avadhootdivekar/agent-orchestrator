import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "../components/RunDetail";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const DETAIL = {
  summary: {
    run_id: "run-1", workflow_id: "demo", status: "running", started_at: "2026-11-09T10:00:00+00:00",
    updated_at: "2026-11-09T10:05:00+00:00", task_count: 0, task_counts: {}, cost_usd: 0,
    input_tokens: 0, output_tokens: 0, wall_seconds: 1, active_seconds: 1, is_terminal: false,
    is_live: true, launch_id: null,
  },
  tasks: [], tripped_breakers: [], route_decisions: {}, monitor_decisions: [], run_dir: "/ws/run-1",
  is_live: true, launch: null, integration: null,
};

const STATUS = {
  schema: "ao.ctl.status/v1", run_id: "run-1", run_status: "running", updated_at: "x",
  sentinel: { enabled: true, disabled_reason: null, last_tick_at: null, llm_paused: false, hold_requested: false, systemic_alarm: false },
  concurrency: { running: 0, limit: 3, ceiling: 6, overrides: {} },
  utilisation: { pct: null, slot_seconds_busy: null, slot_seconds_idle_with_ready: null, slot_seconds_idle_no_ready: null },
  tasks: { counts: {}, running: [] }, ladder: {}, backlog: { held: [] },
  llm: { generation: null, spend_usd: null, paused: false, pause_reason: null, budget_exhausted: false, enabled: null, powers: null },
  inbox: { pending: [], consumed_count: 0 }, recent_actions: [],
};

function mock(sentinel: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/sentinel")) return sentinel();
      if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: "" });
      if (url.endsWith("/run-1")) return json(DETAIL);
      return json({}, 404);
    }),
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("RunDetail sentinel section", () => {
  it("renders the sentinel panel for a run that reports sentinel status", async () => {
    mock(() => json(STATUS));
    render(<RunDetail runId="run-1" onBack={() => undefined} />);
    expect(await screen.findByTestId("section-sentinel")).toBeInTheDocument();
    expect(await screen.findByTestId("sentinel-limit")).toHaveTextContent("limit 3");
  });

  it("omits the section on an old backend (404) without breaking the page", async () => {
    mock(() => json({ detail: "not found" }, 404));
    render(<RunDetail runId="run-1" onBack={() => undefined} />);
    expect(await screen.findByTestId("section-limits")).toBeInTheDocument();
    expect(screen.queryByTestId("section-sentinel")).toBeNull();
  });
});
