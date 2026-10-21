import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CodeView } from "../components/viewer/CodeView";
import { TabActionsContext, type TabActions } from "../tabs/context";
import {
  MAX_TABS,
  makeTab,
  parsePersisted,
  serialize,
  tabsReducer,
  type Tab,
  type TabsAction,
  type TabsState,
} from "../tabs/model";
import { TabBar } from "../tabs/TabBar";
import { resetPathProbeCache } from "../tabs/usePathProbe";

const mk = (id: string, runId = id): Tab => makeTab("run", { id: runId }, `t-${id}`) as Tab;
const state = (ids: string[], active = ids[0], pinned: string[] = []): TabsState => ({
  tabs: ids.map((id) => ({ ...mk(id), ...(pinned.includes(id) ? { pinned: true } : {}) })),
  activeId: `t-${active}`,
});
const ids = (s: TabsState) => s.tabs.map((t) => t.id.slice(2));
const run = (s: TabsState, ...actions: TabsAction[]) => actions.reduce(tabsReducer, s);

describe("pinning (A4.3)", () => {
  it("pin moves the tab to the end of the pinned block; unpin to just after it", () => {
    let s = state(["a", "b", "c", "d"]);
    s = run(s, { type: "pin", id: "t-c" });
    expect(ids(s)).toEqual(["c", "a", "b", "d"]);
    s = run(s, { type: "pin", id: "t-d" });
    expect(ids(s)).toEqual(["c", "d", "a", "b"]);
    s = run(s, { type: "unpin", id: "t-c" });
    expect(ids(s)).toEqual(["d", "c", "a", "b"]);
    expect(s.tabs.map((t) => Boolean(t.pinned))).toEqual([true, false, false, false]);
  });

  it("new tabs open after the pinned block, never before a pinned tab", () => {
    const s = run(state(["a", "b"], "a", ["b"]), { type: "pin", id: "t-a" });
    const next = run(s, { type: "open", tab: mk("n"), activate: true });
    expect(ids(next)).toEqual(["b", "a", "n"]);
    expect(next.activeId).toBe("t-n");
  });

  it("pin/unpin of unknown or already-in-state tabs returns the same state", () => {
    const s = state(["a", "b"], "a", ["a"]);
    expect(tabsReducer(s, { type: "pin", id: "t-a" })).toBe(s);
    expect(tabsReducer(s, { type: "unpin", id: "t-b" })).toBe(s);
    expect(tabsReducer(s, { type: "pin", id: "t-zz" })).toBe(s);
  });

  it("move cannot cross the pinned boundary", () => {
    const s = state(["a", "b", "c"], "a", ["a", "b"]);
    expect(tabsReducer(s, { type: "move", from: 2, to: 0 })).toBe(s);
    expect(tabsReducer(s, { type: "move", from: 0, to: 2 })).toBe(s);
    expect(ids(tabsReducer(s, { type: "move", from: 0, to: 1 }))).toEqual(["b", "a", "c"]);
  });

  it("eviction skips pinned tabs; with every other tab pinned a new tab is refused", () => {
    const all = Array.from({ length: MAX_TABS }, (_, i) => `x${i}`);
    const s = state(all, "x5", ["x0", "x1"]);
    const next = tabsReducer(s, { type: "open", tab: mk("new"), activate: true });
    expect(next.tabs).toHaveLength(MAX_TABS);
    expect(ids(next)).toContain("x0");
    expect(ids(next)).toContain("x1");
    expect(ids(next)).not.toContain("x2"); // oldest inactive UNPINNED tab went
    const full = state(all, "x11", all.slice(0, MAX_TABS - 1)); // only the active tab is unpinned
    expect(tabsReducer(full, { type: "open", tab: mk("new"), activate: true })).toBe(full);
  });

  it("navigate/retarget never change a pinned tab's content", () => {
    const s = state(["a", "b"], "a", ["a"]);
    const nav = tabsReducer(s, { type: "navigate", tab: mk("n") });
    expect(ids(nav)).toEqual(["a", "b", "n"]);
    expect(nav.activeId).toBe("t-n");
    expect(nav.tabs[0].params.id).toBe("a");
    expect(tabsReducer(s, { type: "retarget", id: "t-a", tab: mk("n") })).toBe(s);
    // unpinned active tab still navigates in place
    const free = state(["a", "b"], "b", ["a"]);
    const replaced = tabsReducer(free, { type: "navigate", tab: mk("n") });
    expect(replaced.tabs.map((t) => t.params.id)).toEqual(["a", "n"]); // same slot, new content
  });

  it("persistence: pinned round-trips, orders pinned-first, hostile values mean unpinned", () => {
    const s = run(state(["a", "b", "c"]), { type: "pin", id: "t-c" });
    const back = parsePersisted(serialize(s)) as TabsState;
    expect(back.tabs.map((t) => Boolean(t.pinned))).toEqual([true, false, false]);
    const raw = JSON.stringify({
      v: 1,
      activeId: "t-a",
      tabs: [
        { id: "t-a", kind: "usage", params: {} },
        { id: "t-b", kind: "settings", params: {}, pinned: true },
        { id: "t-c", kind: "new", params: {}, pinned: "yes" },
        { id: "t-d", kind: "runs", params: {}, pinned: 1 },
      ],
    });
    const parsed = parsePersisted(raw) as TabsState;
    expect(ids(parsed)).toEqual(["b", "a", "c", "d"]);
    expect(parsed.tabs.map((t) => Boolean(t.pinned))).toEqual([true, false, false, false]);
    // legacy data with no flag still loads
    expect(parsePersisted(JSON.stringify({ v: 1, tabs: [{ id: "t-a", kind: "usage", params: {} }] }))).not.toBeNull();
  });
});

describe("close policies (A4.4)", () => {
  it("single close of a pinned tab is refused until it is unpinned", () => {
    const s = state(["a", "b"], "a", ["a"]);
    expect(tabsReducer(s, { type: "close", id: "t-a" })).toBe(s);
    expect(ids(run(s, { type: "unpin", id: "t-a" }, { type: "close", id: "t-a" }))).toEqual(["b"]);
  });

  it("closeAll keeps pinned tabs and falls back to a surviving tab", () => {
    const s = state(["a", "b", "c", "d"], "d", ["a", "b"]);
    const next = tabsReducer(s, { type: "closeAll" });
    expect(ids(next)).toEqual(["a", "b"]);
    expect(next.activeId).toBe("t-b");
    // active pinned tab stays active
    expect(tabsReducer(state(["a", "b"], "a", ["a"]), { type: "closeAll" }).activeId).toBe("t-a");
  });

  it("closeAll with nothing pinned yields the default (never tab-less); all pinned is a no-op", () => {
    const next = tabsReducer(state(["a", "b"]), { type: "closeAll" });
    expect(next.tabs).toHaveLength(1);
    expect(next.tabs[0].kind).toBe("runs");
    const pinned = state(["a", "b"], "a", ["a", "b"]);
    expect(tabsReducer(pinned, { type: "closeAll" })).toBe(pinned);
  });

  it("closeToRight closes only unpinned tabs after the given one", () => {
    const s = state(["p", "a", "b", "c", "d"], "d", ["p"]);
    const next = tabsReducer(s, { type: "closeToRight", id: "t-a" });
    expect(ids(next)).toEqual(["p", "a"]);
    expect(next.activeId).toBe("t-a"); // active tab was closed -> nearest survivor
    expect(tabsReducer(s, { type: "closeToRight", id: "t-d" })).toBe(s); // nothing to the right
    expect(tabsReducer(s, { type: "closeToRight", id: "t-nope" })).toBe(s);
    // from a pinned tab: closes every unpinned tab, keeps pinned
    const second = state(["p", "q", "a", "b"], "b", ["p", "q"]);
    expect(ids(tabsReducer(second, { type: "closeToRight", id: "t-p" }))).toEqual(["p", "q"]);
  });

  it("closeToRight keeps the active tab when it is to the left", () => {
    const s = state(["a", "b", "c"], "a");
    const next = tabsReducer(s, { type: "closeToRight", id: "t-b" });
    expect(ids(next)).toEqual(["a", "b"]);
    expect(next.activeId).toBe("t-a");
  });
});

describe("TabBar pin / close UI", () => {
  afterEach(cleanup);
  const base = () => ({
    onActivate: vi.fn(),
    onClose: vi.fn(),
    onMove: vi.fn(),
    onPin: vi.fn(),
    onUnpin: vi.fn(),
    onCloseAll: vi.fn(),
    onCloseToRight: vi.fn(),
  });

  it("pin buttons toggle, pinned tabs have no close button and ignore Delete/middle-click", async () => {
    const h = base();
    const tabs = state(["a", "b"], "a", ["a"]).tabs;
    render(<TabBar tabs={tabs} activeId="t-a" {...h} />);
    const user = userEvent.setup();
    expect(screen.queryByRole("button", { name: "Close tab a" })).toBeNull();
    expect(screen.getByRole("button", { name: "Close tab b" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unpin tab a" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Unpin tab a" }));
    expect(h.onUnpin).toHaveBeenCalledWith("t-a");
    await user.click(screen.getByRole("button", { name: "Pin tab b" }));
    expect(h.onPin).toHaveBeenCalledWith("t-b");

    const [a, b] = screen.getAllByRole("tab");
    a.focus();
    await user.keyboard("{Delete}");
    fireEvent(a.parentElement!, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 }));
    expect(h.onClose).not.toHaveBeenCalled();
    b.focus();
    await user.keyboard("p");
    expect(h.onPin).toHaveBeenCalledTimes(2);
    a.focus();
    await user.keyboard("P");
    expect(h.onUnpin).toHaveBeenCalledTimes(2);
    b.focus();
    await user.keyboard("{Delete}");
    expect(h.onClose).toHaveBeenCalledWith("t-b");
  });

  it("Alt+arrow does not try to move a tab across the pinned boundary", async () => {
    const h = base();
    render(<TabBar tabs={state(["a", "b"], "a", ["a"]).tabs} activeId="t-a" {...h} />);
    const user = userEvent.setup();
    screen.getAllByRole("tab")[0].focus();
    await user.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(h.onMove).not.toHaveBeenCalled();
  });

  it("Close all / Close to the right fire and are disabled when nothing is closable", async () => {
    const h = base();
    const { rerender } = render(<TabBar tabs={state(["a", "b", "c"], "a").tabs} activeId="t-a" {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Close to the right" }));
    expect(h.onCloseToRight).toHaveBeenCalledWith("t-a");
    await user.click(screen.getByRole("button", { name: "Close all" }));
    expect(h.onCloseAll).toHaveBeenCalledTimes(1);

    rerender(<TabBar tabs={state(["a", "b"], "a", ["a", "b"]).tabs} activeId="t-a" {...h} />);
    expect(screen.getByRole("button", { name: "Close all" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close to the right" })).toBeDisabled();
    rerender(<TabBar tabs={state(["a", "b", "c"], "c").tabs} activeId="t-c" {...h} />);
    expect(screen.getByRole("button", { name: "Close to the right" })).toBeDisabled(); // last tab
  });
});

describe("CodeView path links (A4.5, workspace-only)", () => {
  const WS = "/home/u/ws";
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  const probed: string[] = [];

  beforeEach(() => {
    resetPathProbeCache();
    probed.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/workspace")) return json({ workspace_root: WS, roots: [] });
        if (url.endsWith("/files/resolve")) {
          const { paths } = JSON.parse(init?.body as string) as { paths: string[] };
          probed.push(...paths);
          return json({
            results: paths.map((path) => ({ path, status: path === "docs/guide.md" ? "file" : "missing" })),
          });
        }
        return json({});
      }),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const actions = (): TabActions => ({ available: true, navigate: vi.fn(), open: vi.fn(), retarget: vi.fn() });

  it("links only server-confirmed paths, opens them via the tab actions, never links out-of-root or ..", async () => {
    const a = actions();
    const code = [
      "# see docs/guide.md for details",
      "# also src/missing.py and /etc/passwd and ../secret.txt and <b>&amp;x/y.md",
    ].join("\n");
    render(
      <TabActionsContext.Provider value={a}>
        <CodeView code={code} language="bash" />
      </TabActionsContext.Provider>,
    );
    const link = await screen.findByRole("link", { name: "docs/guide.md" });
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(link).toHaveAttribute("href", "#/file?path=docs%2Fguide.md");
    expect(probed).not.toContain("/etc/passwd");
    expect(probed.some((p) => p.includes(".."))).toBe(false);
    // file text is still escaped, not interpreted
    expect(document.querySelector(".code-lines b")).toBeNull();

    fireEvent.click(link);
    expect(a.navigate).toHaveBeenCalledWith({ kind: "file", params: { path: "docs/guide.md" } });
    fireEvent.click(link, { ctrlKey: true });
    expect(a.open).toHaveBeenCalledWith({ kind: "file", params: { path: "docs/guide.md" } }, { activate: false });
  });

  it("an absolute path inside the workspace root is probed as a relative path", async () => {
    render(<CodeView code={`open ${WS}/docs/guide.md now`} language="plaintext" />);
    await waitFor(() => expect(screen.getByRole("link", { name: `${WS}/docs/guide.md` })).toBeInTheDocument());
    expect(probed).toContain("docs/guide.md");
  });
});
