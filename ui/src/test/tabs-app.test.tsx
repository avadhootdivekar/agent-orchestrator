import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { serialize, STORAGE_KEY, makeTab, type Tab } from "../tabs/model";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const RUN_ID = "demo-20261002T110000Z";
const TASK = {
  id: "build",
  status: "succeeded",
  attempts: 1,
  started_at: "2026-10-02T11:00:00+00:00",
  ended_at: "2026-10-02T11:02:00+00:00",
  duration_seconds: 120,
  input_tokens: 1,
  output_tokens: 2,
  cost_usd: 0.1,
  origin: "static",
  route: null,
  output_artifact_path: "out/build.md",
  outputs: [],
  integration_status: null,
  tier_reached: null,
  conflicted_count: 0,
  cache_read_tokens: 0,
  cache_creation_tokens: 0,
  cache_hit_rate: null,
  dispatch_cycle: 1,
  not_taken_reason: null,
  model: "sonnet",
  effort: "high",
};
const SUMMARY = {
  run_id: RUN_ID,
  workflow_id: "demo",
  status: "succeeded",
  started_at: "2026-10-02T11:00:00+00:00",
  updated_at: "2026-10-02T11:05:00+00:00",
  task_count: 1,
  task_counts: { succeeded: 1 },
  cost_usd: 0.1,
  input_tokens: 1,
  output_tokens: 2,
  wall_seconds: 300,
  active_seconds: 120,
  is_terminal: true,
  is_live: false,
  launch_id: null,
};
const DETAIL = {
  summary: SUMMARY,
  tasks: [TASK],
  tripped_breakers: [],
  route_decisions: {},
  monitor_decisions: [],
  run_dir: "/ws/.orchestrator/runs/x",
  is_live: false,
  launch: null,
  integration: null,
  graph_version: null,
};
const STATS = {
  total_runs: 1,
  runs_by_status: { succeeded: 1 },
  total_tasks: 1,
  tasks_by_status: {},
  total_cost_usd: 0.1,
  total_input_tokens: 1,
  total_output_tokens: 2,
  total_wall_seconds: 1,
  total_active_seconds: 1,
};

const GRAPH = {
  schema_version: 1,
  run_id: RUN_ID,
  graph_version: "v1",
  source: "snapshot",
  spawn_data: "none",
  truncated: false,
  warnings: [],
  nodes: [
    {
      id: "build",
      label: "build",
      label_sanitized: false,
      origin: "static",
      parent_task_id: null,
      route: null,
      loop_id: null,
      iteration: null,
      spawn_depth: null,
      children_count: 0,
      is_emitter: false,
      is_router: false,
      is_loop_gate: false,
      exec_ordinal: 1,
      missing: false,
    },
  ],
  dependency_edges: [],
  spawn_edges: [],
  loops: [],
  routers: [],
};

let fetchCalls: string[] = [];

function installFetch() {
  fetchCalls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      fetchCalls.push(url);
      if (url.endsWith("/api/workspace")) return json({ workspace_root: "/ws" });
      if (url.endsWith("/api/runs/stats")) return json(STATS);
      if (url.endsWith("/api/runs")) return json([SUMMARY]);
      if (url.endsWith("/graph")) return json(GRAPH);
      if (url.includes("/activity")) return json({ schema_version: 1, run_id: RUN_ID, generated_at: "", tasks: {} });
      if (url.includes("/log")) return json({ run_id: RUN_ID, launch_id: null, text: "" });
      if (url.includes("/feedback")) return json({ entries: [] });
      if (url.includes("/signals")) return json({ signals: null });
      if (url.includes(`/api/runs/${RUN_ID}`)) return json(DETAIL);
      if (url.includes("/api/files/content")) {
        return json({ path: "out/build.md", root: "ws", size: 3, is_binary: false, truncated: false, text: "hi", kind: "text", mime: null, data_uri: null });
      }
      if (url.includes("/api/files")) return json({ root: "ws", path: "", absolute: "/ws", entries: [] });
      return json({ detail: "nope" }, 404);
    }),
  );
}

const stored = (): { tabs: Tab[]; activeId: string } => JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
const tabNames = () => within(screen.getByRole("tablist", { name: "Open tabs" })).getAllByRole("tab").map((t) => t.textContent);

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  installFetch();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("workspace tabs", () => {
  it("starts with a Runs tab, persists it and encodes it in the hash", async () => {
    render(<App />);
    expect(tabNames()).toEqual(["Runs"]);
    await screen.findByText(RUN_ID);
    expect(window.location.hash).toBe("#/runs");
    expect(stored().tabs.map((t) => t.kind)).toEqual(["runs"]);
  });

  it("sidebar entries open or focus tabs without duplicating", async () => {
    render(<App />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Usage" }));
    await user.click(screen.getByRole("button", { name: "Runs" }));
    await user.click(screen.getByRole("button", { name: "Usage" }));
    expect(tabNames()).toEqual(["Runs", "Usage"]);
    expect(window.location.hash).toBe("#/usage");
  });

  it("plain click on a run navigates the current tab; Back returns to the list", async () => {
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: RUN_ID }));
    expect(tabNames()).toEqual([RUN_ID]);
    expect(window.location.hash).toBe(`#/run?id=${RUN_ID}`);
    await user.click(await screen.findByRole("button", { name: /Back to runs/ }));
    expect(tabNames()).toEqual(["Runs"]);
  });

  it("ctrl-click and middle-click open a background tab; the active tab does not change", async () => {
    render(<App />);
    const link = await screen.findByRole("link", { name: RUN_ID });
    expect(link).toHaveAttribute("href", `#/run?id=${RUN_ID}`); // real, copyable URL
    fireEvent.click(link, { ctrlKey: true });
    expect(tabNames()).toEqual(["Runs", RUN_ID]);
    expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute("aria-selected", "true");
    fireEvent(link, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }));
    expect(tabNames()).toEqual(["Runs", RUN_ID]); // deduplicated
  });

  it("explicit 'open in new tab' on a run row opens AND activates it", async () => {
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `Open run ${RUN_ID} in new tab` }));
    expect(tabNames()).toEqual(["Runs", RUN_ID]);
    expect(screen.getByRole("tab", { name: RUN_ID })).toHaveAttribute("aria-selected", "true");
  });

  it("task rows and output file paths offer open-in-new-tab (task detail / file viewer)", async () => {
    window.history.replaceState(null, "", `#/run?id=${RUN_ID}`);
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Open task build in new tab" }));
    await waitFor(() => expect(tabNames()).toContain("build"));
    expect(window.location.hash).toBe(`#/task?run=${RUN_ID}&id=build`);

    await user.click(screen.getByRole("tab", { name: RUN_ID }));
    await user.click(screen.getByRole("button", { name: "Open out/build.md in new tab" }));
    await waitFor(() => expect(tabNames()).toContain("build.md"));
    expect(window.location.hash).toBe("#/file?path=out%2Fbuild.md");
    await screen.findByText("hi"); // the file viewer opened the file
  });

  it("closing tabs: x button, middle-click on the tab, Delete; never ends tab-less", async () => {
    render(<App />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Usage" }));
    await user.click(screen.getByRole("button", { name: "New run" }));
    expect(tabNames()).toEqual(["Runs", "Usage", "New run"]);
    await user.click(screen.getByRole("button", { name: "Close tab New run" }));
    expect(tabNames()).toEqual(["Runs", "Usage"]);
    fireEvent(screen.getByRole("tab", { name: "Usage" }).parentElement!, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    expect(tabNames()).toEqual(["Runs"]);
    screen.getByRole("tab", { name: "Runs" }).focus();
    await user.keyboard("{Delete}");
    expect(tabNames()).toEqual(["Runs"]); // last tab closed -> default Runs again
    expect(stored().tabs).toHaveLength(1);
  });

  it("reorders by Alt+Arrow and by drag-and-drop, and persists the order", async () => {
    render(<App />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Usage" }));
    await user.click(screen.getByRole("button", { name: "New run" }));
    screen.getByRole("tab", { name: "Runs" }).focus();
    await user.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(tabNames()).toEqual(["Usage", "Runs", "New run"]);

    const dt = { setData: vi.fn(), effectAllowed: "" };
    const items = () => screen.getAllByRole("tab").map((t) => t.parentElement!);
    fireEvent.dragStart(items()[2], { dataTransfer: dt });
    fireEvent.drop(items()[0], { dataTransfer: dt });
    expect(tabNames()).toEqual(["New run", "Usage", "Runs"]);
    expect(stored().tabs.map((t) => t.kind)).toEqual(["new", "usage", "runs"]);
  });

  it("restores tabs from localStorage on a fresh mount", async () => {
    const tabs = [makeTab("usage", {}, "t-u"), makeTab("run", { id: RUN_ID }, "t-r")] as Tab[];
    localStorage.setItem(STORAGE_KEY, serialize({ tabs, activeId: "t-u" }));
    render(<App />);
    expect(tabNames()).toEqual(["Usage", RUN_ID]);
    expect(screen.getByRole("tab", { name: "Usage" })).toHaveAttribute("aria-selected", "true");
  });

  it("a hash link opens (and focuses) its tab on load; a hostile hash is ignored", async () => {
    window.history.replaceState(null, "", `#/run?id=${RUN_ID}`);
    const first = render(<App />);
    expect(tabNames()).toEqual(["Runs", RUN_ID]);
    expect(screen.getByRole("tab", { name: RUN_ID })).toHaveAttribute("aria-selected", "true");
    first.unmount();
    localStorage.clear();
    window.history.replaceState(null, "", "#/run?id=../../etc/passwd");
    render(<App />);
    expect(tabNames()).toEqual(["Runs"]);
  });

  it("hashchange (pasted/edited link) opens the tab; invalid hashchange is ignored", async () => {
    render(<App />);
    act(() => {
      window.history.replaceState(null, "", "#/usage");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(tabNames()).toEqual(["Runs", "Usage"]);
    act(() => {
      window.history.replaceState(null, "", "#/bogus?x=1");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(tabNames()).toEqual(["Runs", "Usage"]);
  });

  it("corrupt or throwing localStorage still renders the default workspace", async () => {
    localStorage.setItem(STORAGE_KEY, "{not json");
    const first = render(<App />);
    expect(tabNames()).toEqual(["Runs"]);
    first.unmount();
    const original = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: () => {
          throw new Error("blocked");
        },
        setItem: () => {
          throw new Error("blocked");
        },
        removeItem: () => {
          throw new Error("blocked");
        },
      },
    });
    try {
      render(<App />);
      expect(tabNames()).toEqual(["Runs"]);
    } finally {
      Object.defineProperty(window, "localStorage", original);
    }
  });

  it("inactive tabs stay mounted but stop polling; the active tab polls", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<App />);
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      await screen.findByText(RUN_ID);
      await user.click(screen.getByRole("button", { name: "Usage" }));
      // Runs tab is now inactive but still in the DOM (hidden, state kept).
      const runsPanel = screen.getByRole("tab", { name: "Runs" }).getAttribute("aria-controls")!;
      expect(document.getElementById(runsPanel)).toHaveAttribute("hidden");
      expect(within(document.getElementById(runsPanel)!).getByText(RUN_ID)).toBeInTheDocument();

      fetchCalls.length = 0;
      await act(async () => {
        vi.advanceTimersByTime(20000);
      });
      expect(fetchCalls.filter((u) => u.endsWith("/api/runs"))).toHaveLength(0);

      await user.click(screen.getByRole("tab", { name: "Runs" }));
      await act(async () => {
        vi.advanceTimersByTime(8000);
      });
      expect(fetchCalls.filter((u) => u.endsWith("/api/runs")).length).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
