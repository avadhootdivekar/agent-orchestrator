import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useMemo, useReducer } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TabActionsContext, type TabActions } from "../tabs/context";
import {
  encodeHash,
  makeTab,
  tabsReducer,
  viewKey,
  type Tab,
  type TabsState,
} from "../tabs/model";
import { TabView } from "../tabs/TabView";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const entry = (name: string) => ({
  name, path: name, root: "ws", is_dir: false, size: 3, modified: 0, is_symlink: false, hidden: false,
});

let reads: string[];
function installFetch() {
  reads = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/api/files/content")) {
        const path = new URL(url, "http://x").searchParams.get("path") ?? "";
        reads.push(path);
        return json({ path, root: "ws", size: 3, is_binary: false, truncated: false, text: `body:${path}`, kind: "text", mime: null, data_uri: null });
      }
      return json({ root: "ws", path: "", absolute: "/ws", entries: [entry("a.py"), entry("b.py")] });
    }),
  );
}

let latest: TabsState;
/** Real reducer + the same actions wiring as App.tsx, rendering the active tab via TabView. */
function Shell({ initial }: { initial: TabsState }) {
  const [state, dispatch] = useReducer(tabsReducer, initial);
  latest = state;
  const actions = useMemo<TabActions>(
    () => ({
      available: true,
      navigate: (t) => {
        const tab = makeTab(t.kind, t.params);
        if (tab) dispatch({ type: "navigate", tab });
      },
      open: (t, o) => {
        const tab = makeTab(t.kind, t.params);
        if (tab) dispatch({ type: "open", tab, activate: o?.activate ?? true });
      },
      retarget: (id, t) => {
        const tab = makeTab(t.kind, t.params);
        if (tab) dispatch({ type: "retarget", id, tab });
      },
    }),
    [],
  );
  const active = state.tabs.find((t) => t.id === state.activeId) as Tab;
  return (
    <TabActionsContext.Provider value={actions}>
      <TabView key={viewKey(active)} tab={active} />
    </TabActionsContext.Provider>
  );
}

const fileTab = (params: Record<string, string>, id = "t-file") => makeTab("file", params, id) as Tab;
const setup = (tab: Tab) => {
  installFetch();
  render(<Shell initial={{ tabs: [tab], activeId: tab.id }} />);
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FileBrowser plain click syncs its tab (A3)", () => {
  it("updates params, title and hash to the clicked file, in place (no new tab, no reload)", async () => {
    setup(fileTab({}));
    await userEvent.setup().click(await screen.findByRole("button", { name: /a\.py/ }));
    await screen.findByText("body:a.py");
    expect(latest.tabs).toHaveLength(1);
    expect(latest.tabs[0]).toMatchObject({ id: "t-file", params: { path: "a.py" }, title: "a.py" });
    expect(encodeHash(latest.tabs[0])).toBe("#/file?path=a.py");
    expect(reads).toEqual(["a.py"]); // the retargeted prop did not trigger a second read
    await userEvent.setup().click(screen.getByRole("button", { name: /b\.py/ }));
    await screen.findByText("body:b.py");
    expect(latest.tabs[0].params).toEqual({ path: "b.py" });
    expect(reads).toEqual(["a.py", "b.py"]);
  });

  it("preserves a non-default root", async () => {
    setup(fileTab({ root: "ws" }));
    await userEvent.setup().click(await screen.findByRole("button", { name: /a\.py/ }));
    await screen.findByText("body:a.py");
    expect(latest.tabs[0].params).toEqual({ path: "a.py", root: "ws" });
  });

  it("a later link to the same file focuses this tab instead of duplicating", async () => {
    setup(fileTab({}));
    await userEvent.setup().click(await screen.findByRole("button", { name: /a\.py/ }));
    await screen.findByText("body:a.py");
    const link = makeTab("file", { path: "a.py" }, "t-other") as Tab;
    expect(tabsReducer(latest, { type: "open", tab: link, activate: true })).toEqual(latest);
  });

  it("ctrl-click is unchanged: opens/focuses another tab and does not retarget this one", async () => {
    setup(fileTab({}));
    fireEvent.click(await screen.findByRole("button", { name: /a\.py/ }), { ctrlKey: true });
    expect(latest.tabs).toHaveLength(2);
    expect(latest.tabs[0].params).toEqual({}); // original tab untouched
    expect(latest.tabs[1].params).toEqual({ path: "a.py" });
  });

  it("opening a tab with a path still loads that file and its directory on mount", async () => {
    setup(fileTab({ path: "a.py" }));
    await screen.findByText("body:a.py");
    expect(reads).toEqual(["a.py"]);
  });
});
