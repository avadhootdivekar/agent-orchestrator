import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useMemo, useReducer, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuickLinks } from "../components/QuickLinks";
import { PathLink, PathText } from "../components/PathText";
import { RunLog } from "../components/RunLog";
import { MarkdownView } from "../components/viewer/MarkdownView";
import { TabActionsContext, type TabActions } from "../tabs/context";
import {
  decodeHash,
  defaultState,
  makeTab,
  MAX_TABS,
  parsePersisted,
  tabsReducer,
  type TabsState,
} from "../tabs/model";
import { resetPathProbeCache } from "../tabs/usePathProbe";

// Audit (A3/A5): every file-link site, driven through the REAL tabsReducer + the same
// navigate/open wiring App.tsx uses, so click semantics AND dedupe/MAX_TABS are checked together.

const WS = "/home/u/ws";
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

function installServer(ok: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/workspace")) return json({ workspace_root: WS, roots: [] });
      if (url.endsWith("/files/resolve")) {
        const { paths } = JSON.parse(init?.body as string) as { paths: string[] };
        return json({ results: paths.map((path) => ({ path, status: ok.includes(path) ? "file" : "missing" })) });
      }
      return json({});
    }),
  );
}

let latest: TabsState;
function Shell({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(tabsReducer, undefined, defaultState);
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
  return <TabActionsContext.Provider value={actions}>{children}</TabActionsContext.Provider>;
}

const fileTabs = () => latest.tabs.filter((t) => t.kind === "file").map((t) => t.params.path);
const aux = () => new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 });

beforeEach(() => resetPathProbeCache());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** One row per site: how to render a verified link to a/ok.md and find the primary anchor. */
const SITES: Array<[string, () => ReactNode, (c: HTMLElement) => HTMLElement | null]> = [
  ["PathLink (TaskDetail outputs)", () => <PathLink path="a/ok.md" />, () => screen.queryByRole("link", { name: "a/ok.md" })],
  ["PathText (PromptPanel/TaskDetail/Summary)", () => <PathText text="see a/ok.md now" />, () => screen.queryByRole("link", { name: "a/ok.md" })],
  ["QuickLinks", () => <QuickLinks links={[{ path: "a/ok.md" }]} />, () => screen.queryByRole("link", { name: "a/ok.md" })],
  ["RunLog", () => <RunLog text="wrote a/ok.md" />, () => screen.queryByRole("link", { name: "a/ok.md" })],
];

describe.each(SITES)("%s", (_name, node, find) => {
  it("plain click navigates the active tab; ctrl opens a background tab; middle-click too; dedupes", async () => {
    installServer(["a/ok.md"]);
    render(<Shell>{node()}</Shell>);
    const link = await screen.findByRole("link", { name: "a/ok.md" });
    expect(find(document.body)).toBe(link);
    const activeBefore = latest.activeId;

    fireEvent.click(link, { ctrlKey: true });
    expect(fileTabs()).toEqual(["a/ok.md"]);
    expect(latest.activeId).toBe(activeBefore); // background
    fireEvent.click(link, { metaKey: true });
    fireEvent(link, aux());
    expect(fileTabs()).toEqual(["a/ok.md"]); // deduped

    fireEvent.click(link); // plain: navigate -> focuses the existing identical tab
    expect(fileTabs()).toEqual(["a/ok.md"]);
    expect(latest.tabs.find((t) => t.id === latest.activeId)?.params.path).toBe("a/ok.md");
  });

  it("shift/alt click are left to the browser (no in-app action)", async () => {
    installServer(["a/ok.md"]);
    render(<Shell>{node()}</Shell>);
    const link = await screen.findByRole("link", { name: "a/ok.md" });
    const before = latest;
    fireEvent.click(link, { shiftKey: true });
    fireEvent.click(link, { altKey: true });
    expect(latest).toBe(before);
  });

  it("the ⧉ open-in-new-tab button opens AND activates, focusing (not duplicating) when already open", async () => {
    installServer(["a/ok.md"]);
    render(<Shell>{node()}</Shell>);
    const btn = await screen.findByRole("button", { name: "Open a/ok.md in new tab" });
    fireEvent.click(btn);
    expect(fileTabs()).toEqual(["a/ok.md"]);
    const id = latest.activeId;
    fireEvent.click(btn);
    expect(fileTabs()).toEqual(["a/ok.md"]);
    expect(latest.activeId).toBe(id);
  });
});

describe("MarkdownView links", () => {
  async function mount() {
    installServer(["a/ok.md"]);
    const { container } = render(
      <Shell>
        <MarkdownView source="see a/ok.md" filePath="r.md" root="workspace" />
      </Shell>,
    );
    return (await vi.waitFor(() => {
      const a = container.querySelector<HTMLAnchorElement>("a[data-ao-path]");
      if (!a) throw new Error("not linked yet");
      return a;
    })) as HTMLAnchorElement;
  }

  it("ctrl/cmd-click opens a background tab and dedupes; plain click navigates", async () => {
    const a = await mount();
    fireEvent.click(a, { ctrlKey: true });
    fireEvent.click(a, { metaKey: true });
    expect(fileTabs()).toEqual(["a/ok.md"]);
    fireEvent.click(a);
    expect(latest.tabs.find((t) => t.id === latest.activeId)?.params.path).toBe("a/ok.md");
    expect(fileTabs()).toEqual(["a/ok.md"]);
  });

  it("middle-click opens a background in-app tab and dedupes; browser default prevented", async () => {
    const a = await mount();
    const ev = aux();
    fireEvent(a, ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(fileTabs()).toEqual(["a/ok.md"]);
    const before = latest.activeId;
    fireEvent(a, aux());
    expect(fileTabs()).toEqual(["a/ok.md"]);
    expect(latest.activeId).toBe(before); // background: active tab unchanged
  });

  it("non-middle auxclick (right button) is ignored", async () => {
    const a = await mount();
    fireEvent(a, new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 2 }));
    expect(fileTabs()).toEqual([]);
  });

  it("the open-in-new-tab button opens and activates the file tab", async () => {
    const a = await mount();
    const btn = a.parentElement!.querySelector<HTMLButtonElement>("button[data-ao-path-open]")!;
    expect(btn.getAttribute("aria-label")).toBe("Open a/ok.md in new tab");
    fireEvent.click(btn);
    expect(fileTabs()).toEqual(["a/ok.md"]);
    expect(latest.tabs.find((t) => t.id === latest.activeId)?.params.path).toBe("a/ok.md");
  });
});

describe("invariants: MAX_TABS + hash/localStorage allowlists", () => {
  it("opening MAX_TABS+5 distinct verified files via ctrl-click never exceeds MAX_TABS and keeps the active tab", async () => {
    const paths = Array.from({ length: MAX_TABS + 5 }, (_, i) => `d/f${i}.md`);
    installServer(paths);
    render(
      <Shell>
        <QuickLinks links={paths.map((path) => ({ path }))} />
      </Shell>,
    );
    // QUICK_LINKS_MAX may cap rendered links; use the ⧉ buttons actually rendered.
    const buttons = await screen.findAllByRole("button", { name: /Open d\/f\d+\.md in new tab/ });
    for (const b of buttons) fireEvent.click(b);
    for (const b of buttons) fireEvent.click(b); // second pass: all dedupe/focus
    expect(latest.tabs.length).toBeLessThanOrEqual(MAX_TABS);
    expect(latest.tabs.some((t) => t.id === latest.activeId)).toBe(true);
    expect(new Set(fileTabs()).size).toBe(fileTabs().length); // no duplicate file tab
  });

  it("hostile hash / storage are rejected or stripped for file tabs", () => {
    const bad = [
      "#/file?path=" + encodeURIComponent("a\u0000b"),
      "#/file?path=" + "x".repeat(1025),
      "#/file?root=" + encodeURIComponent("../etc"),
      "#/exec?path=a",
      "#/" + "a".repeat(3000),
    ];
    for (const h of bad) expect(decodeHash(h), h).toBeNull();
    const t = decodeHash("#/file?path=a.md&evil=1&title=PWNED");
    expect(t?.params).toEqual({ path: "a.md" });
    expect(t?.title).toBe("a.md");

    const stored = JSON.stringify({
      v: 1,
      activeId: "t-a",
      tabs: [
        { id: "t-a", kind: "file", params: { path: "a.md", x: "y" }, title: "PWNED" },
        { id: "t-b", kind: "file", params: { path: "b\u0007" } },
        { id: "t-c", kind: "shell", params: {} },
        ...Array.from({ length: 40 }, (_, i) => ({ id: `t-f${i}`, kind: "file", params: { path: `f${i}` } })),
      ],
    });
    const s = parsePersisted(stored)!;
    expect(s.tabs.length).toBeLessThanOrEqual(MAX_TABS);
    expect(s.tabs[0]).toMatchObject({ kind: "file", params: { path: "a.md" }, title: "a.md" });
    expect(s.tabs.every((t) => t.kind === "file")).toBe(true);
    expect(s.tabs.find((t) => t.id === "t-b" || t.id === "t-c")).toBeUndefined();
  });
});
