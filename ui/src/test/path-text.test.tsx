import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskDetailPanel } from "../graph/TaskDetailPanel";
import { PathText } from "../components/PathText";
import { TabActionsContext, type TabActions } from "../tabs/context";
import { resetPathProbeCache } from "../tabs/usePathProbe";
import type { RunGraph, TaskStat } from "../types";

const WS = "/home/u/ws";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** Fake server: `status` maps path -> probe status (default missing); `fail` makes resolve 500. */
function installServer(status: Record<string, string>, fail = false) {
  const probes: string[][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/workspace")) return json({ workspace_root: WS, roots: [] });
      if (url.endsWith("/files/resolve")) {
        if (fail) return json({ detail: "boom" }, 500);
        const { paths } = JSON.parse(init?.body as string) as { paths: string[] };
        probes.push(paths);
        return json({ results: paths.map((path) => ({ path, status: status[path] ?? "missing" })) });
      }
      return json({}, 404);
    }),
  );
  return probes;
}

function renderWith(node: React.ReactNode, actions?: TabActions) {
  const value = actions ?? { available: true, navigate: vi.fn(), open: vi.fn(), retarget: vi.fn() };
  render(<TabActionsContext.Provider value={value}>{node}</TabActionsContext.Provider>);
  return value;
}

beforeEach(() => resetPathProbeCache());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PathText (A5)", () => {
  it("links only server-verified paths; missing/denied stay plain text", async () => {
    const probes = installServer({ "a/ok.md": "file", "a/dir": "dir", "x/secret.md": "denied" });
    renderWith(<PathText text="read a/ok.md, a/dir, x/secret.md and a/gone.md now" />);
    const link = await screen.findByRole("link", { name: "a/ok.md" });
    expect(link.getAttribute("href")).toBe("#/file?path=a%2Fok.md");
    expect(screen.getByRole("link", { name: "a/dir" })).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(document.body.textContent).toContain("x/secret.md and a/gone.md now");
    expect(probes).toHaveLength(1); // one batched request for the whole block
  });

  it("renders plain text before the probe settles and after a network error", async () => {
    installServer({}, true);
    renderWith(<PathText text="see a/ok.md" />);
    expect(screen.queryByRole("link")).toBeNull();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.body.textContent).toBe("see a/ok.md");
  });

  it("never sends out-of-root or traversal candidates to the server", async () => {
    const probes = installServer({});
    renderWith(<PathText text="/etc/passwd ../../x/y.md http://h/z.md" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(probes).toEqual([]);
  });

  it("caches results: a second block with the same path does not refetch", async () => {
    const probes = installServer({ "a/ok.md": "file" });
    renderWith(<PathText text="a/ok.md" />);
    await screen.findByRole("link", { name: "a/ok.md" });
    cleanup();
    renderWith(<PathText text="again a/ok.md" />);
    await screen.findByRole("link", { name: "a/ok.md" });
    expect(probes).toHaveLength(1);
  });

  it("honours ctrl/cmd-click (background tab) and the explicit new-tab button", async () => {
    installServer({ "a/ok.md": "file" });
    const actions = renderWith(<PathText text="a/ok.md" />);
    const link = await screen.findByRole("link", { name: "a/ok.md" });
    fireEvent.click(link, { ctrlKey: true });
    expect(actions.open).toHaveBeenLastCalledWith(
      { kind: "file", params: { path: "a/ok.md" } },
      { activate: false },
    );
    fireEvent.click(screen.getByRole("button", { name: /Open a\/ok\.md in new tab/ }));
    expect(actions.open).toHaveBeenLastCalledWith(
      { kind: "file", params: { path: "a/ok.md" } },
      { activate: true },
    );
  });
});

describe("TaskDetailPanel path links (A5)", () => {
  const graph = {
    schema_version: 1, run_id: "r1", graph_version: "v1", source: "snapshot", spawn_data: "recorded",
    truncated: false, warnings: [], dependency_edges: [], spawn_edges: [], loops: [], routers: [],
    nodes: [{
      id: "t1", label: "t1", label_sanitized: false, origin: "static", parent_task_id: null, route: null,
      loop_id: null, iteration: null, spawn_depth: 0, children_count: 0, is_emitter: false,
      is_router: false, is_loop_gate: false, exec_ordinal: null, missing: false,
    }],
  } as unknown as RunGraph;
  const stat = {
    id: "t1", status: "succeeded", attempts: 1, started_at: null, ended_at: null, duration_seconds: null,
    input_tokens: 0, output_tokens: 0, cost_usd: 0, origin: "static", route: null,
    output_artifact_path: `${WS}/runs/t1/out.md`, outputs: ["runs/t1/out.md", "runs/t1/gone.md", "../../etc/passwd"],
    integration_status: null, tier_reached: null, conflicted_count: 0, cache_read_tokens: 0,
    cache_creation_tokens: 0, cache_hit_rate: null, dispatch_cycle: 1, not_taken_reason: null,
  } as TaskStat;

  it("links the artifact path and existing outputs; missing and traversal outputs stay text", async () => {
    installServer({ "runs/t1/out.md": "file" });
    const actions = renderWith(
      <TaskDetailPanel nodeId="t1" graph={graph} statsById={new Map([["t1", stat]])} onClose={() => {}} onNavigate={() => {}} />,
    );
    const links = await screen.findAllByRole("link", { name: /out\.md/ });
    expect(links.length).toBeGreaterThanOrEqual(2); // artifact path + declared output
    expect(screen.queryByRole("link", { name: /gone\.md/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /passwd/ })).toBeNull();
    expect(document.body.textContent).toContain("../../etc/passwd");
    fireEvent.click(links[0], { metaKey: true });
    expect(actions.open).toHaveBeenCalledWith(
      { kind: "file", params: { path: "runs/t1/out.md" } },
      { activate: false },
    );
  });
});
