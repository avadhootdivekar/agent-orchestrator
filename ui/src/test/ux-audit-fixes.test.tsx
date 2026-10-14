import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { FileBrowser } from "../components/FileBrowser";
import { RunsList } from "../components/RunsList";
import { Settings } from "../components/Settings";
import { makeTab } from "../tabs/model";
import { TabBar } from "../tabs/TabBar";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

/** A fetch that never resolves: the "first poll still in flight" state. */
const pending = () => new Promise<Response>(() => {});

const STATS = {
  total_runs: 0, runs_by_status: {}, total_tasks: 0, tasks_by_status: {}, total_cost_usd: 0,
  total_input_tokens: 0, total_output_tokens: 0, total_wall_seconds: 0, total_active_seconds: 0,
};
const LIVE_RUN = {
  run_id: "r1", workflow_id: "demo", status: "running", started_at: "2026-10-02T11:00:00+00:00",
  updated_at: "2026-10-02T11:05:00+00:00", task_count: 1, task_counts: {}, cost_usd: 0,
  input_tokens: 0, output_tokens: 0, wall_seconds: 1, active_seconds: 1, is_terminal: false,
  is_live: true, launch_id: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.title = "";
});

describe("RunsList", () => {
  it("shows Loading, not 'No runs yet', before the first fetch resolves", () => {
    vi.stubGlobal("fetch", vi.fn(pending));
    render(<RunsList onOpen={() => {}} />);
    expect(screen.getByText("Loading runs…")).toBeTruthy();
    expect(screen.queryByText(/No runs yet/)).toBeNull();
  });

  it("shows 'No runs yet' only after a successful empty fetch", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.endsWith("/api/runs/stats") ? json(STATS) : json([]))));
    render(<RunsList onOpen={() => {}} />);
    await screen.findByText(/No runs yet/);
  });

  function liveFetch() {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${url}`);
        if (url.endsWith("/api/runs/stats")) return json(STATS);
        if (url.includes("/cancel")) return json({});
        if (url.includes("/api/launches")) return json([]);
        return json([LIVE_RUN]);
      }),
    );
    return calls;
  }

  it("asks for confirmation before cancelling and does nothing on decline", async () => {
    const calls = liveFetch();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RunsList onOpen={() => {}} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Cancel" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(calls.some((c) => c.includes("/cancel"))).toBe(false);
  });

  it("cancels once confirmed", async () => {
    const calls = liveFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RunsList onOpen={() => {}} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.includes("/cancel"))).toBe(true));
  });

  it("explains the Wall / Actual column heads", async () => {
    liveFetch();
    render(<RunsList onOpen={() => {}} />);
    expect((await screen.findByRole("columnheader", { name: "Wall" })).getAttribute("title")).toMatch(/last update/);
    expect(screen.getByRole("columnheader", { name: "Actual" }).getAttribute("title")).toMatch(/task execution/);
  });
});

describe("Settings", () => {
  it("shows Loading, not 'None configured', until the fetch settles", async () => {
    vi.stubGlobal("fetch", vi.fn(pending));
    render(<Settings />);
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
    expect(screen.queryByText(/None configured/)).toBeNull();
  });

  it("shows 'None configured' after an empty load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.includes("instructions")
          ? json([])
          : json({ workspace_root: "/ws", config_path: null, workflow: null, reposets: null, agents: null }),
      ),
    );
    render(<Settings />);
    await screen.findByText(/None configured/);
  });
});

describe("FileBrowser", () => {
  const entry = { name: "a.py", path: "a.py", root: "ws", is_dir: false, size: 3, modified: 0, is_symlink: false, hidden: false };
  const content = { path: "a.py", root: "ws", size: 3, is_binary: false, truncated: false, text: "x", kind: "text", mime: null, data_uri: null };

  function install(fileResponse: () => Promise<Response>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.includes("/api/files/content")
          ? fileResponse()
          : json({ root: "ws", path: "", absolute: "/ws", entries: [entry] }),
      ),
    );
  }

  it("shows a loading message while a file is being fetched, and marks the row aria-current", async () => {
    install(pending);
    render(<FileBrowser />);
    const row = await screen.findByRole("button", { name: /a\.py/ });
    await userEvent.setup().click(row);
    expect(screen.getByText("Loading a.py…")).toBeTruthy();
    expect(screen.queryByText(/Select a file/)).toBeNull();
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(row.hasAttribute("aria-selected")).toBe(false);
  });

  it("shows 'Could not open' (not the idle prompt) after a read error", async () => {
    install(async () => new Response(JSON.stringify({ detail: "nope" }), { status: 500, headers: { "Content-Type": "application/json" } }));
    render(<FileBrowser />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /a\.py/ }));
    await screen.findByText("Could not open a.py");
    expect(screen.queryByText(/Select a file/)).toBeNull();
  });

  it("renders the file once loaded", async () => {
    install(async () => json(content));
    render(<FileBrowser />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /a\.py/ }));
    await screen.findByText("x");
  });
});

describe("TabBar", () => {
  it("keeps close buttons out of the tab order", () => {
    const tabs = [makeTab("runs", {}), makeTab("files", {})].filter((t): t is NonNullable<typeof t> => !!t);
    render(<TabBar tabs={tabs} activeId={tabs[0].id} onActivate={() => {}} onClose={() => {}} onMove={() => {}} />);
    const closes = screen.getAllByRole("button", { name: /Close tab/ });
    expect(closes.length).toBe(tabs.length);
    for (const c of closes) expect(c.getAttribute("tabindex")).toBe("-1");
  });
});

describe("App document.title", () => {
  it("follows the active tab", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.endsWith("/api/runs/stats") ? json(STATS) : json([]))));
    window.localStorage.clear();
    window.location.hash = "";
    await act(async () => {
      render(<App />);
    });
    await waitFor(() => expect(document.title).toMatch(/ · Agent Orchestrator$/));
    expect(document.title.length).toBeGreaterThan(" · Agent Orchestrator".length);
  });
});
