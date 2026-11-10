import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SentinelPanel } from "../components/SentinelPanel";
import type { SentinelStatus } from "../types";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function status(over: Partial<SentinelStatus> = {}, sentinel: Partial<SentinelStatus["sentinel"]> = {}): SentinelStatus {
  return {
    schema: "ao.ctl.status/v1",
    run_id: "run-1",
    run_status: "running",
    updated_at: "2026-11-09T12:00:00+00:00",
    sentinel: {
      enabled: true, disabled_reason: null, last_tick_at: "2026-11-09T11:59:50+00:00",
      llm_paused: false, hold_requested: false, systemic_alarm: false, ...sentinel,
    },
    concurrency: { running: 2, limit: 2, ceiling: 4, overrides: {} },
    utilisation: {
      pct: 62.5, slot_seconds_busy: 100, slot_seconds_idle_with_ready: 40, slot_seconds_idle_no_ready: 20,
    },
    tasks: { counts: { running: 2 }, running: [{ id: "t-a", liveness_age_s: 90 }, { id: "t-b", liveness_age_s: null }] },
    ladder: {},
    backlog: { held: [] },
    llm: {
      generation: 3, spend_usd: 1.25, paused: false, pause_reason: null,
      budget_exhausted: false, enabled: true, powers: "act",
    },
    inbox: { pending: [], consumed_count: 4 },
    recent_actions: [],
    ...over,
  };
}

interface Setup {
  calls: { method: string; url: string; body?: any }[];
  set: (s: SentinelStatus | null) => void;
}
function setup(initial: SentinelStatus | null, opts: { statusCode?: number; postStatus?: number; outcome?: object } = {}): Setup {
  let current = initial;
  const calls: Setup["calls"] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url.endsWith("/sentinel")) {
        if (opts.statusCode) return json({ detail: "nope" }, opts.statusCode);
        return json(current);
      }
      if (url.endsWith("/control") && method === "POST") {
        if (opts.postStatus) return json({ detail: "run is finished" }, opts.postStatus);
        return json({ id: "entry-20261109t120000-abcd1234", kind: JSON.parse(String(init?.body)).kind, run_id: "run-1", actor: "dashboard:local", outcome: "queued" }, 202);
      }
      if (url.includes("/control/")) return json(opts.outcome ?? { outcome: "consumed" });
      return json({});
    }),
  );
  return { calls, set: (s) => { current = s; } };
}

const posts = (s: Setup) => s.calls.filter((c) => c.method === "POST");

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("SentinelPanel rendering", () => {
  it("shows state, limit/ceiling, utilisation, llm generation, spend and pause reason", async () => {
    const st = status({ llm: { ...status().llm, paused: true, pause_reason: "5 consecutive rejected proposals" } });
    setup(st);
    render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByTestId("sentinel-body")).toBeInTheDocument();
    expect(screen.getByTestId("sentinel-limit")).toHaveTextContent("limit 2");
    expect(screen.getByTestId("sentinel-limit")).toHaveTextContent("ceiling 4");
    const bar = screen.getByRole("progressbar", { name: "Slot utilisation" });
    expect(bar).toHaveAttribute("aria-valuenow", "63");
    const llm = screen.getByTestId("sentinel-llm");
    expect(llm).toHaveTextContent("paused");
    expect(llm).toHaveTextContent("powers: act");
    expect(llm).toHaveTextContent("3");
    expect(llm).toHaveTextContent("$1.25");
    expect(llm).toHaveTextContent("5 consecutive rejected proposals");
    expect(screen.getByRole("button", { name: "Resume LLM" })).toBeEnabled();
    const rows = within(screen.getByTestId("sentinel-running")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("t-a");
    expect(rows[1]).toHaveTextContent("1m 30s");
    expect(rows[2]).toHaveTextContent("—");
  });

  it("old run (sentinel off): renders a collapsed 'off' panel without controls", async () => {
    setup(status({}, { enabled: false, disabled_reason: "not enabled for this run" }));
    render(<SentinelPanel runId="run-1" live={false} />);
    expect(await screen.findByTestId("sentinel-off")).toHaveTextContent("off for this run");
    expect(screen.queryByTestId("sentinel-body")).toBeNull();
    // collapsed by default while off
    expect(screen.getByRole("button", { name: /Sentinel/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("old backend (404) or a non-status body: no panel", async () => {
    const s = setup(null, { statusCode: 404 });
    const { container, rerender } = render(<SentinelPanel runId="run-1" live />);
    await waitFor(() => expect(s.calls.length).toBeGreaterThan(0));
    expect(container).toBeEmptyDOMElement();
    rerender(<SentinelPanel runId="run-2" live />);
    vi.unstubAllGlobals();
    setup({ summary: {} } as unknown as SentinelStatus); // e.g. a catch-all mock returning other JSON
    rerender(<SentinelPanel runId="run-3" live />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the systemic alarm and orphaned ladder state", async () => {
    setup(status({
      ladder: { "t-gone": { kills: 2, last_reason: "idle", last_source: "mechanical", escalation_step: 1, no_retry: false } },
    }, { systemic_alarm: true }));
    render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByTestId("sentinel-alarm")).toHaveTextContent("Systemic alarm");
    expect(screen.getByTestId("sentinel-ladder")).toHaveTextContent("t-gone: 2 kill(s), last idle (mechanical)");
  });

  it("renders note and action text escaped, never as HTML", async () => {
    setup(status({ recent_actions: [{ id: "n1", kind: "note", actor: "cli:a", at: "2026-11-09T11:00:00+00:00", text: "<img src=x onerror=alert(1)>" }] }));
    const { container } = render(<SentinelPanel runId="run-1" live />);
    expect(await screen.findByTestId("sentinel-actions")).toHaveTextContent("<img src=x");
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("SentinelPanel controls", () => {
  it("applies a live max_parallel override and follows the engine's verdict", async () => {
    const s = setup(status());
    render(<SentinelPanel runId="run-1" live />);
    await screen.findByTestId("sentinel-body");
    const input = screen.getByLabelText("New max parallel");
    const apply = screen.getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    fireEvent.change(input, { target: { value: "9" } }); // above ceiling 4
    expect(apply).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("1 to 4");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(apply);
    await waitFor(() => expect(posts(s)).toHaveLength(1));
    expect(posts(s)[0].body).toEqual({ kind: "set", payload: { values: { max_parallel: 3 } } });
    expect(await screen.findByTestId("sentinel-entry")).toHaveTextContent("queued");
    await waitFor(() => expect(screen.getByTestId("sentinel-entry")).toHaveTextContent("set: applied."), { timeout: 4000 });
  });

  it("shows an engine rejection instead of assuming success", async () => {
    setup(status(), { outcome: { outcome: "rejected", rule_id: "CTL-CEILING", detail: "above ceiling" } });
    render(<SentinelPanel runId="run-1" live />);
    await screen.findByTestId("sentinel-body");
    fireEvent.change(screen.getByLabelText("New max parallel"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.getByTestId("sentinel-entry")).toHaveTextContent("CTL-CEILING"), { timeout: 4000 });
  });

  it("kill requires a reason and passes no_retry", async () => {
    const s = setup(status());
    render(<SentinelPanel runId="run-1" live />);
    await screen.findByTestId("sentinel-body");
    fireEvent.click(screen.getByRole("button", { name: "Kill t-a" }));
    const confirm = screen.getByRole("button", { name: "Confirm kill" });
    expect(confirm).toBeDisabled();
    expect(posts(s)).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Kill reason for t-a"), { target: { value: "  stuck in a loop " } });
    fireEvent.click(screen.getByLabelText("don't retry"));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(posts(s)).toHaveLength(1));
    expect(posts(s)[0].body).toEqual({
      kind: "kill", payload: { task_id: "t-a", no_retry: true }, rationale: "stuck in a loop",
    });
  });

  it("pause / resume LLM and notes post the matching entries", async () => {
    const s = setup(status());
    render(<SentinelPanel runId="run-1" live />);
    await screen.findByTestId("sentinel-body");
    fireEvent.click(screen.getByRole("button", { name: "Pause LLM" }));
    await waitFor(() => expect(posts(s)).toHaveLength(1));
    expect(posts(s)[0].body).toEqual({ kind: "pause_llm" });
    const add = screen.getByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Sentinel note"), { target: { value: " look at X " } });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    await waitFor(() => expect(posts(s)).toHaveLength(2));
    expect(posts(s)[1].body).toEqual({ kind: "note", payload: { text: "look at X" } });
  });

  it("a finished run keeps the panel readable but disables every control", async () => {
    setup(status({ run_status: "succeeded" }));
    render(<SentinelPanel runId="run-1" live={false} />);
    await screen.findByTestId("sentinel-body");
    for (const name of ["Apply", "Kill t-a", "Pause LLM", "Add note"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    expect(screen.getByLabelText("New max parallel")).toBeDisabled();
  });

  it("a rejected POST (409) surfaces the server message", async () => {
    setup(status(), { postStatus: 409 });
    render(<SentinelPanel runId="run-1" live />);
    await screen.findByTestId("sentinel-body");
    fireEvent.click(screen.getByRole("button", { name: "Pause LLM" }));
    expect(await screen.findByText(/run is finished/)).toBeInTheDocument();
  });
});
