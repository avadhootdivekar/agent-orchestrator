import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SentinelPanel } from "../components/SentinelPanel";
import type { SentinelPanelV2 } from "../types";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const STATUS = {
  schema: "ao.ctl.status/v1",
  run_id: "run-1",
  run_status: "running",
  updated_at: "2026-11-09T12:00:00+00:00",
  sentinel: { enabled: true, disabled_reason: null, last_tick_at: null, llm_paused: false, hold_requested: false, systemic_alarm: false },
  concurrency: { running: 0, limit: 2, ceiling: 4, overrides: {} },
  utilisation: { pct: null, slot_seconds_busy: null, slot_seconds_idle_with_ready: null, slot_seconds_idle_no_ready: null },
  tasks: { counts: {}, running: [] },
  ladder: {},
  backlog: { held: [] },
  llm: { generation: 2, spend_usd: 0.5, paused: false, pause_reason: null, budget_exhausted: false, enabled: true, powers: "act" },
  inbox: { pending: [], consumed_count: 0 },
  recent_actions: [],
};

const PANEL: SentinelPanelV2 = {
  schema: "ao.sentinel.panel/v2",
  run_id: "run-1",
  spend_usd: 0.75,
  generations: [
    { generation: 1, task_id: "sentinel-g01", status: "succeeded", started_at: "2026-11-09T10:00:00+00:00", ended_at: "2026-11-09T10:20:00+00:00", cost_usd: 0.5, triggers: ["idle_slots_no_backlog", "heartbeat"], graceful: true, ended_reason: "explicit_handoff" },
    { generation: 2, task_id: "sentinel-g02", status: "failed", started_at: "2026-11-09T11:00:00+00:00", ended_at: null, cost_usd: 0.25, triggers: [], graceful: false, ended_reason: "ttl" },
  ],
  proposals: {
    applied: [{ id: "g1-inj-0001", kind: "inject", actor: "sentinel-g01", at: null }],
    quarantined: [{ id: "g1-kill-001", kind: "kill", actor: "sentinel-g01", at: null, rule_id: "PWR-KILL-RATE", detail: "cap reached" }],
  },
  directives: [
    { id: "dir-open-1", at: null, actor: "sentinel-g01", severity: "warn", text: "slow unit t3", rationale: "r", ack: null },
    { id: "dir-acked1", at: null, actor: "sentinel-g01", severity: "info", text: "rescope A2", rationale: "r", ack: { response: "adopt", rationale: null, checkpoint: "ck-02", at: "t" } },
  ],
};

function stub(panel: unknown, panelStatus = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/sentinel/panel")) return json(panel, panelStatus);
      if (url.endsWith("/sentinel")) return json(STATUS);
      return json({});
    }),
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("Sentinel panel v2", () => {
  it("renders the generation timeline with triggers, cost, ungraceful flag and total spend", async () => {
    stub(PANEL);
    render(<SentinelPanel runId="run-1" live />);
    const table = await screen.findByTestId("sentinel-generations");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("g01");
    expect(rows[1]).toHaveTextContent("idle_slots_no_backlog, heartbeat");
    expect(rows[1]).toHaveTextContent("$0.50");
    expect(rows[2]).toHaveTextContent("failed (ungraceful)");
    expect(screen.getByTestId("sentinel-gen-spend")).toHaveTextContent("total spend $0.75");
  });

  it("separates applied from quarantined proposals and names the rule", async () => {
    stub(PANEL);
    render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByTestId("sentinel-proposal-counts")).toHaveTextContent("1 applied · 1 quarantined");
    const list = screen.getByTestId("sentinel-proposals");
    const applied = list.querySelector('[data-state="applied"]') as HTMLElement;
    const quarantined = list.querySelector('[data-state="quarantined"]') as HTMLElement;
    expect(applied).toHaveTextContent("g1-inj-0001");
    expect(quarantined).toHaveTextContent("PWR-KILL-RATE");
    expect(quarantined).toHaveTextContent("cap reached");
  });

  it("shows directives with their checkpoint acknowledgement or a waiting note", async () => {
    stub(PANEL);
    render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByTestId("directive-ack-dir-open-1")).toHaveTextContent("awaiting checkpoint acknowledgement");
    expect(screen.getByTestId("directive-ack-dir-acked1")).toHaveTextContent("acknowledged by ck-02: adopt");
  });

  it("hides the v2 sections on an old backend (404) and keeps the v1 panel", async () => {
    stub({ detail: "nope" }, 404);
    render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByTestId("sentinel-body")).toBeInTheDocument();
    expect(screen.queryByTestId("sentinel-v2")).toBeNull();
  });

  it("empty panel shows the no-generation / no-directive placeholders", async () => {
    stub({ ...PANEL, generations: [], directives: [], proposals: { applied: [], quarantined: [] }, spend_usd: 0 });
    render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByText("No generations yet.")).toBeInTheDocument();
    expect(screen.getByText("None raised.")).toBeInTheDocument();
  });
});
