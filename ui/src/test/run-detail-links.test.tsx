import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "../components/RunDetail";
import { NETWORK_ERROR_MESSAGE } from "../errors";
import { resetPathProbeCache } from "../tabs/usePathProbe";
import type { RunDetail as RunDetailData } from "../types";

const WS = "/ws";
const RUN_DIR = "/ws/.orchestrator/runs/run-1";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const detail = {
  summary: {
    run_id: "run-1",
    workflow_id: "demo",
    status: "succeeded",
    started_at: "2026-09-21T10:00:00+00:00",
    updated_at: "2026-09-21T10:05:00+00:00",
    task_count: 1,
    task_counts: { succeeded: 1 },
    cost_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    wall_seconds: 1,
    active_seconds: 1,
    is_terminal: true,
    is_live: false,
    launch_id: null,
  },
  tasks: [
    {
      id: "task-a",
      status: "succeeded",
      attempts: 1,
      started_at: null,
      ended_at: null,
      duration_seconds: 1,
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      origin: "static",
      route: null,
      output_artifact_path: null,
      outputs: ["outputs/a.md"],
      integration_status: null,
      tier_reached: null,
      conflicted_count: 0,
      cache_read_tokens: 0,
      cache_creation_tokens: 0,
      cache_hit_rate: null,
      dispatch_cycle: 1,
      not_taken_reason: null,
    },
  ],
  tripped_breakers: [],
  route_decisions: {},
  monitor_decisions: [],
  run_dir: RUN_DIR,
  is_live: false,
  launch: null,
  integration: null,
  graph_version: null,
} as unknown as RunDetailData;

function install(opts: { log: string; probe?: "ok" | "fail" }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/workspace")) return json({ workspace_root: WS, roots: [] });
      if (url.endsWith("/files/resolve")) {
        if (opts.probe === "fail") throw new TypeError("Failed to fetch");
        const { paths } = JSON.parse(init?.body as string) as { paths: string[] };
        return json({
          results: paths.map((path) => ({
            path,
            status: path.endsWith("workflow.json") || path === "outputs/a.md" ? "file" : "missing",
          })),
        });
      }
      if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: opts.log });
      if (url.includes("/activity") || url.includes("/summary")) return json({}, 404);
      return json(detail);
    }),
  );
}

afterEach(() => {
  cleanup();
  resetPathProbeCache();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("RunDetail mounts RunLog and QuickLinks (A5)", () => {
  it("renders quick links for workflow.json and outputs, and the log in a pre block", async () => {
    install({ log: "wrote outputs/a.md done" });
    const { container } = render(<RunDetail runId="run-1" onBack={() => {}} />);
    const nav = await screen.findByRole("navigation", { name: "Quick links" });
    expect(nav.textContent).toContain("workflow.json");
    expect(nav.textContent).toContain("outputs/a.md");
    // Let the path probes settle: one still in flight would write into the shared probe cache
    // after the next test's resetPathProbeCache() and turn its plain text into a link.
    await waitFor(() => expect(nav.querySelectorAll("a")).toHaveLength(2));
    await waitFor(() => expect(container.querySelector("pre.code")?.textContent).toBe("wrote outputs/a.md done"));
  });

  it("degrades to plain text when the path probe fails", async () => {
    install({ log: "see outputs/a.md", probe: "fail" });
    const { container } = render(<RunDetail runId="run-1" onBack={() => {}} />);
    const nav = await screen.findByRole("navigation", { name: "Quick links" });
    expect(nav.textContent).toContain("workflow.json");
    expect(nav.querySelector("a")).toBeNull();
    expect(container.querySelector("pre.code")?.textContent).toBe("see outputs/a.md");
  });

  it("uses errorMessage for a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    expect(await screen.findByText(NETWORK_ERROR_MESSAGE)).toBeTruthy();
  });
});
