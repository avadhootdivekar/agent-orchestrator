import { describe, expect, it } from "vitest";
import {
  decodeHash,
  defaultState,
  encodeHash,
  initialState,
  makeTab,
  MAX_HASH_CHARS,
  MAX_TABS,
  parsePersisted,
  sameTarget,
  serialize,
  STORAGE_KEY,
  tabsReducer,
  titleFor,
  validateTab,
  type Tab,
  type TabsState,
} from "../tabs/model";
import { readStoredTabs, writeStoredTabs } from "../tabs/storage";

const t = (kind: string, params: Record<string, unknown> = {}, id?: string): Tab =>
  makeTab(kind, params, id) as Tab;

function state(...tabs: Tab[]): TabsState {
  return { tabs, activeId: tabs[0].id };
}

describe("makeTab / validation (kind allowlist, param allowlist)", () => {
  it("accepts every allowlisted kind with valid params", () => {
    expect(makeTab("runs", {})?.title).toBe("Runs");
    expect(makeTab("run", { id: "wf-20261002T110000Z" })?.title).toBe("wf-20261002T110000Z");
    expect(makeTab("task", { run: "r1", id: "emit#1" })?.params).toEqual({ run: "r1", id: "emit#1" });
    expect(makeTab("graph", { run: "r1" })?.title).toBe("Graph · r1");
    expect(makeTab("file", { path: "docs/a/b.md" })?.title).toBe("b.md");
    expect(makeTab("file", {})?.title).toBe("Files");
    expect(makeTab("usage", {})?.title).toBe("Usage");
    expect(makeTab("new", {})?.title).toBe("New run");
    expect(makeTab("settings", {})?.title).toBe("Workspace");
  });

  it.each(["", "admin", "__proto__", "constructor", "RUN", "run ", 5, null, undefined, {}, []])(
    "rejects unknown kind %j",
    (kind) => {
      expect(makeTab(kind, { id: "x" })).toBeNull();
    },
  );

  it("rejects missing/invalid required params and drops unknown keys", () => {
    expect(makeTab("run", {})).toBeNull();
    expect(makeTab("run", { id: "" })).toBeNull();
    expect(makeTab("run", { id: 5 })).toBeNull();
    expect(makeTab("task", { run: "r1" })).toBeNull();
    expect(makeTab("run", { id: "r1", evil: "x", title: "FAKE" })?.params).toEqual({ id: "r1" });
    expect(makeTab("usage", { id: "r1" })?.params).toEqual({});
  });

  it.each(["../../etc", "..", ".", "a/b", "a b", "a\u0000b", "-flag", "r‮1", "x".repeat(201)])(
    "rejects run id %j",
    (id) => {
      expect(makeTab("run", { id })).toBeNull();
    },
  );

  it("rejects control chars and oversize in task ids and paths, and any invalid optional root", () => {
    expect(makeTab("task", { run: "r", id: "a\nb" })).toBeNull();
    expect(makeTab("file", { path: "a\u0000b" })).toBeNull();
    expect(makeTab("file", { path: "p".repeat(1025) })).toBeNull();
    expect(makeTab("file", { path: "a.md", root: "bad root!" })).toBeNull();
    expect(makeTab("file", { path: "a.md", root: "repo-1" })?.params.root).toBe("repo-1");
  });

  it("does not read inherited / prototype keys", () => {
    const hostile = JSON.parse('{"__proto__": {"id": "polluted"}, "constructor": {"id": "x"}}');
    expect(makeTab("run", hostile)).toBeNull();
    expect(({} as Record<string, unknown>).id).toBeUndefined();
  });

  it("titles are derived, sanitized and capped; a title in input is ignored", () => {
    const tab = validateTab({ id: "t-abc", kind: "file", params: { path: `d/${"x".repeat(100)}‮` }, title: "SPOOF" });
    // control/bidi chars in params make the whole tab invalid
    expect(tab).toBeNull();
    const long = makeTab("task", { run: "r", id: "y".repeat(150) }) as Tab;
    expect(long.title.length).toBeLessThanOrEqual(40);
    expect(validateTab({ id: "t-abc", kind: "runs", params: {}, title: "SPOOF" })?.title).toBe("Runs");
    expect(titleFor("runs", {})).toBe("Runs");
  });

  it("validateTab regenerates an invalid id", () => {
    const tab = validateTab({ id: "../../x", kind: "usage", params: {} });
    expect(tab?.id).toMatch(/^t-[a-z0-9]+$/);
    expect(validateTab("nope")).toBeNull();
    expect(validateTab(null)).toBeNull();
  });
});

describe("hash codec", () => {
  it("round-trips every kind", () => {
    for (const tab of [
      t("runs"),
      t("run", { id: "r-1" }),
      t("task", { run: "r-1", id: "a b&c=d#e" }),
      t("graph", { run: "r-1" }),
      t("file", { path: "docs/x y/ü.md", root: "ws" }),
      t("usage"),
      t("new"),
      t("settings"),
    ]) {
      const decoded = decodeHash(encodeHash(tab));
      expect(decoded && sameTarget(decoded, tab)).toBe(true);
    }
  });

  it("encodes as #/<kind>?...", () => {
    expect(encodeHash(t("run", { id: "r1" }))).toBe("#/run?id=r1");
    expect(encodeHash(t("runs"))).toBe("#/runs");
  });

  it.each([
    "",
    "#",
    "#/",
    "#run?id=r1",
    "/run?id=r1",
    "#/admin?id=r1",
    "#/run",
    "#/run?id=",
    "#/run?id=../../etc",
    "#/__proto__?id=x",
    "#/run?id=r1%00",
    `#/run?id=${"a".repeat(MAX_HASH_CHARS)}`,
    "#/task?run=r1",
    "#/run?id=%E0%A4%A",
  ])("rejects hostile/malformed hash %j", (hash) => {
    expect(decodeHash(hash)).toBeNull();
  });

  it("ignores unknown keys, takes the first duplicate, never reads a title", () => {
    const tab = decodeHash("#/run?id=r1&id=r2&title=SPOOF&__proto__=x");
    expect(tab?.params).toEqual({ id: "r1" });
    expect(tab?.title).toBe("r1");
  });
});

describe("persistence codec", () => {
  it("round-trips and does not store titles", () => {
    const s = state(t("runs", {}, "t-1"), t("run", { id: "r1" }, "t-2"));
    const raw = serialize(s);
    expect(raw).not.toContain("title");
    const back = parsePersisted(raw);
    expect(back?.tabs.map((x) => x.id)).toEqual(["t-1", "t-2"]);
    expect(back?.activeId).toBe("t-1");
  });

  it.each([
    null,
    "",
    "not json",
    "[]",
    "null",
    '"x"',
    '{"v":2,"tabs":[]}',
    '{"v":1}',
    '{"v":1,"tabs":"x"}',
    '{"v":1,"tabs":[]}',
    '{"v":1,"tabs":[{"kind":"evil","params":{}}]}',
  ])("falls back for garbage %j", (raw) => {
    expect(parsePersisted(raw)).toBeNull();
    expect(initialState(raw, "").tabs.map((x) => x.kind)).toEqual(["runs"]);
  });

  it("filters invalid tabs, duplicate ids, duplicate singletons, caps count, repairs activeId", () => {
    const tabs = [
      { id: "t-1", kind: "runs", params: {} },
      { id: "t-1", kind: "usage", params: {} }, // dup id
      { id: "t-2", kind: "runs", params: {} }, // dup singleton
      { id: "t-3", kind: "bogus", params: {} },
      ...Array.from({ length: 30 }, (_, i) => ({ id: `t-x${i}`, kind: "run", params: { id: `r${i}` } })),
    ];
    const out = parsePersisted(JSON.stringify({ v: 1, activeId: "t-gone", tabs }));
    expect(out).not.toBeNull();
    expect(out!.tabs.length).toBeLessThanOrEqual(MAX_TABS);
    expect(out!.tabs.filter((x) => x.kind === "runs")).toHaveLength(1);
    expect(out!.tabs.some((x) => x.kind === ("bogus" as string))).toBe(false);
    expect(out!.activeId).toBe(out!.tabs[0].id);
  });

  it("initialState opens + focuses a valid hash tab on top of the persisted set", () => {
    const raw = serialize(state(t("runs", {}, "t-1")));
    const s = initialState(raw, "#/run?id=r9");
    expect(s.tabs.map((x) => x.kind)).toEqual(["runs", "run"]);
    expect(s.tabs.find((x) => x.id === s.activeId)?.params.id).toBe("r9");
    // hostile hash leaves the restored state alone
    expect(initialState(raw, "#/evil?x=1").tabs).toHaveLength(1);
  });
});

describe("tabsReducer", () => {
  const a = t("runs", {}, "t-a");
  const b = t("run", { id: "r1" }, "t-b");
  const c = t("usage", {}, "t-c");

  it("open appends + activates, dedupes by target, background keeps active", () => {
    let s = state(a);
    s = tabsReducer(s, { type: "open", tab: b, activate: true });
    expect(s.tabs.map((x) => x.id)).toEqual(["t-a", "t-b"]);
    expect(s.activeId).toBe("t-b");
    const dup = t("run", { id: "r1" }, "t-zzz");
    const s2 = tabsReducer(s, { type: "open", tab: dup, activate: false });
    expect(s2).toBe(s); // identical target: no-op
    const s3 = tabsReducer({ ...s, activeId: "t-a" }, { type: "open", tab: dup, activate: true });
    expect(s3.tabs).toHaveLength(2);
    expect(s3.activeId).toBe("t-b");
    const s4 = tabsReducer(s, { type: "open", tab: c, activate: false });
    expect(s4.activeId).toBe("t-b");
    expect(s4.tabs).toHaveLength(3);
  });

  it("open past MAX_TABS evicts the oldest INACTIVE tab, never the active one", () => {
    let s = state(t("runs", {}, "t-0"));
    for (let i = 1; i < MAX_TABS + 3; i++) {
      s = tabsReducer(s, { type: "open", tab: t("run", { id: `r${i}` }, `t-${i}`), activate: i % 2 === 0 });
    }
    expect(s.tabs.length).toBeLessThanOrEqual(MAX_TABS);
    expect(s.tabs.some((x) => x.id === s.activeId)).toBe(true);
  });

  it("navigate replaces the active tab in place (keeping its id) or reuses an identical tab", () => {
    const s = { tabs: [a, b], activeId: "t-a" };
    const n = tabsReducer(s, { type: "navigate", tab: t("usage", {}, "ignored") });
    expect(n.tabs.map((x) => [x.id, x.kind])).toEqual([
      ["t-a", "usage"],
      ["t-b", "run"],
    ]);
    const reuse = tabsReducer(s, { type: "navigate", tab: t("run", { id: "r1" }) });
    expect(reuse.activeId).toBe("t-b");
    expect(reuse.tabs).toHaveLength(2);
  });

  it("close picks a neighbour, handles background and last tab", () => {
    const s = { tabs: [a, b, c], activeId: "t-b" };
    expect(tabsReducer(s, { type: "close", id: "t-b" }).activeId).toBe("t-c");
    expect(tabsReducer({ ...s, activeId: "t-c" }, { type: "close", id: "t-c" }).activeId).toBe("t-b");
    const bg = tabsReducer(s, { type: "close", id: "t-c" });
    expect(bg.activeId).toBe("t-b");
    expect(bg.tabs).toHaveLength(2);
    expect(tabsReducer(s, { type: "close", id: "nope" })).toBe(s);
    const last = tabsReducer(state(a), { type: "close", id: "t-a" });
    expect(last.tabs.map((x) => x.kind)).toEqual(["runs"]); // never tab-less
  });

  it("move reorders and ignores out-of-range / no-op", () => {
    const s = { tabs: [a, b, c], activeId: "t-a" };
    expect(tabsReducer(s, { type: "move", from: 0, to: 2 }).tabs.map((x) => x.id)).toEqual(["t-b", "t-c", "t-a"]);
    expect(tabsReducer(s, { type: "move", from: 2, to: 1 }).tabs.map((x) => x.id)).toEqual(["t-a", "t-c", "t-b"]);
    for (const [from, to] of [[0, 0], [-1, 1], [0, 3], [5, 0]]) {
      expect(tabsReducer(s, { type: "move", from, to })).toBe(s);
    }
  });

  it("activate ignores an unknown id", () => {
    const s = { tabs: [a], activeId: "t-a" };
    expect(tabsReducer(s, { type: "activate", id: "x" })).toBe(s);
    expect(defaultState().tabs).toHaveLength(1);
  });
});

describe("storage guards", () => {
  it("never throws when localStorage throws", () => {
    const spy = (name: "getItem" | "setItem") =>
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        value: {
          getItem: name === "getItem" ? () => { throw new Error("blocked"); } : () => null,
          setItem: name === "setItem" ? () => { throw new Error("quota"); } : () => {},
        },
      });
    const original = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    try {
      spy("getItem");
      expect(readStoredTabs()).toBeNull();
      spy("setItem");
      expect(() => writeStoredTabs(defaultState())).not.toThrow();
    } finally {
      Object.defineProperty(window, "localStorage", original);
    }
    expect(STORAGE_KEY).toBe("ao-tabs");
  });
});
