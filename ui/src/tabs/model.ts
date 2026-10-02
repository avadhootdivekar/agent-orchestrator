/**
 * Tabbed-workspace model (E-iafh2F Phase 2, ADR-0018 D4). PURE: no React, no DOM, no storage.
 *
 * Trust model: both the URL hash and localStorage are UNTRUSTED input (a pasted link, an
 * extension, another page on the same origin). Everything that crosses in goes through
 * `validateTab` / `decodeHash`, which (1) check `kind` against the closed {@link TAB_KINDS}
 * allowlist, (2) rebuild `params` from the per-kind key allowlist, never copying unknown keys,
 * (3) bound every string, and (4) RECOMPUTE `title` -- a title is never read from input, so a
 * crafted link cannot spoof what a tab is called. Params are only ever used through the typed
 * API client, whose server side re-validates paths (ADR-0011).
 */

export const TAB_KINDS = [
  "runs",
  "run",
  "task",
  "graph",
  "file",
  "usage",
  "new",
  "settings",
] as const;
export type TabKind = (typeof TAB_KINDS)[number];

export type TabParams = Record<string, string>;

export interface Tab {
  id: string;
  kind: TabKind;
  params: TabParams;
  /** Derived (`titleFor`), never accepted from storage or the URL. */
  title: string;
}

export interface TabsState {
  tabs: Tab[];
  activeId: string;
}

// ---- Constants (no magic literals) ------------------------------------------------------------

/** Hard cap on open tabs: all tabs stay mounted, so this bounds memory. Oldest inactive evicted. */
export const MAX_TABS = 12;
export const STORAGE_KEY = "ao-tabs";
export const STORAGE_VERSION = 1;
export const MAX_HASH_CHARS = 2048;
export const MAX_ID_PARAM_CHARS = 200;
export const MAX_PATH_PARAM_CHARS = 1024;
export const MAX_TITLE_CHARS = 40;
/** Kinds that exist at most once (their sidebar entry focuses the existing tab). */
export const SINGLETON_KINDS: ReadonlySet<TabKind> = new Set<TabKind>([
  "runs",
  "usage",
  "new",
  "settings",
]);

type ParamKind = "runId" | "taskId" | "path" | "root";
/** Per-kind param allowlist: key -> [validator, required]. Anything else is dropped. */
const PARAM_SCHEMA: Record<TabKind, Record<string, [ParamKind, boolean]>> = {
  runs: {},
  run: { id: ["runId", true] },
  task: { run: ["runId", true], id: ["taskId", true] },
  graph: { run: ["runId", true] },
  file: { path: ["path", false], root: ["root", false] }, // no path = browse the root
  usage: {},
  new: {},
  settings: {},
};

// Run ids are workflow-id + timestamp; a leading alphanumeric also rules out "." / "..".
const RUN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@+-]*$/;
const ROOT_RE = /^[A-Za-z0-9._@+-]{1,64}$/;
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]/;
const CONTROL_RE_G = new RegExp(CONTROL_RE.source, "g");
const TAB_ID_RE = /^t-[a-z0-9]{1,16}$/;

function validParam(kind: ParamKind, value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0) return false;
  if (CONTROL_RE.test(value)) return false;
  switch (kind) {
    case "runId":
      return value.length <= MAX_ID_PARAM_CHARS && RUN_ID_RE.test(value);
    case "taskId":
      return value.length <= MAX_ID_PARAM_CHARS;
    case "path":
      return value.length <= MAX_PATH_PARAM_CHARS;
    case "root":
      return ROOT_RE.test(value);
  }
}

/** Rebuild params from the allowlist; `null` when a required key is missing or any value is invalid. */
function cleanParams(kind: TabKind, raw: unknown): TabParams | null {
  const source = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const out: TabParams = {};
  for (const [key, [type, required]] of Object.entries(PARAM_SCHEMA[kind])) {
    // Own properties only: `__proto__`/`constructor` keys in hostile JSON never reach us.
    const value = Object.prototype.hasOwnProperty.call(source, key) ? source[key] : undefined;
    if (value === undefined) {
      if (required) return null;
      continue;
    }
    // A supplied-but-invalid value (even for an optional param) rejects the whole tab: silently
    // dropping a hostile `path` would turn the link into "browse the root", a different target.
    if (!validParam(type, value)) return null;
    out[key] = value;
  }
  return out;
}

export function isTabKind(value: unknown): value is TabKind {
  return typeof value === "string" && (TAB_KINDS as readonly string[]).includes(value);
}

// ---- Titles -------------------------------------------------------------------------------------

function clip(text: string, max = MAX_TITLE_CHARS): string {
  const clean = text.replace(CONTROL_RE_G, "");
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

/** Derived tab title: from the kind and VALIDATED params only. */
export function titleFor(kind: TabKind, params: TabParams): string {
  switch (kind) {
    case "runs":
      return "Runs";
    case "run":
      return clip(params.id ?? "Run");
    case "task":
      return clip(params.id ?? "Task");
    case "graph":
      return clip(`Graph · ${params.run ?? ""}`);
    case "file": {
      const base = (params.path ?? "").split("/").filter(Boolean).pop();
      return clip(base ?? "Files");
    }
    case "usage":
      return "Usage";
    case "new":
      return "New run";
    case "settings":
      return "Workspace";
  }
}

let idCounter = 0;
/** A fresh, storage-safe tab id (`t-` + base36 counter + random suffix). */
export function newTabId(): string {
  idCounter += 1;
  return `t-${idCounter.toString(36)}${Math.random().toString(36).slice(2, 7)}`.slice(0, 18);
}

/** Build a tab from a kind + params, validating both. `null` when invalid. */
export function makeTab(kind: unknown, params: unknown, id: string = newTabId()): Tab | null {
  if (!isTabKind(kind)) return null;
  const clean = cleanParams(kind, params);
  if (!clean) return null;
  return { id, kind, params: clean, title: titleFor(kind, clean) };
}

/** Validate one untrusted tab-shaped value (from storage). Any `title` in it is ignored. */
export function validateTab(raw: unknown): Tab | null {
  if (typeof raw !== "object" || raw === null) return null;
  const rec = raw as Record<string, unknown>;
  const id = typeof rec.id === "string" && TAB_ID_RE.test(rec.id) ? rec.id : newTabId();
  return makeTab(rec.kind, rec.params, id);
}

export function sameTarget(a: Pick<Tab, "kind" | "params">, b: Pick<Tab, "kind" | "params">): boolean {
  if (a.kind !== b.kind) return false;
  const ak = Object.keys(a.params);
  return ak.length === Object.keys(b.params).length && ak.every((k) => a.params[k] === b.params[k]);
}

// ---- URL hash codec -----------------------------------------------------------------------------

/** `#/<kind>?<k>=<v>…` for a tab (values percent-encoded by URLSearchParams). */
export function encodeHash(tab: Pick<Tab, "kind" | "params">): string {
  const query = new URLSearchParams(tab.params).toString();
  return `#/${tab.kind}${query ? `?${query}` : ""}`;
}

/** Parse a location hash into a validated tab, or `null` (empty, malformed, hostile, oversize). */
export function decodeHash(hash: string, id: string = newTabId()): Tab | null {
  if (!hash || hash.length > MAX_HASH_CHARS || !hash.startsWith("#/")) return null;
  const rest = hash.slice(2);
  const q = rest.indexOf("?");
  const kind = q === -1 ? rest : rest.slice(0, q);
  let params: Record<string, string> = {};
  try {
    const search = new URLSearchParams(q === -1 ? "" : rest.slice(q + 1));
    for (const key of Object.keys(PARAM_SCHEMA[isTabKind(kind) ? kind : "runs"])) {
      const value = search.get(key); // first occurrence wins; unknown keys are never read
      if (value !== null) params = { ...params, [key]: value };
    }
  } catch {
    return null;
  }
  return makeTab(kind, params, id);
}

// ---- Persistence codec --------------------------------------------------------------------------

export function defaultState(): TabsState {
  const runs = makeTab("runs", {}) as Tab;
  return { tabs: [runs], activeId: runs.id };
}

/** Restore from a raw stored string; ANY problem yields `null` (caller uses the default). */
export function parsePersisted(raw: string | null): TabsState | null {
  if (!raw) return null;
  try {
    const data: unknown = JSON.parse(raw);
    if (typeof data !== "object" || data === null) return null;
    const rec = data as Record<string, unknown>;
    if (rec.v !== STORAGE_VERSION || !Array.isArray(rec.tabs)) return null;
    const tabs: Tab[] = [];
    const seen = new Set<string>();
    for (const item of rec.tabs.slice(0, MAX_TABS)) {
      const tab = validateTab(item);
      if (!tab || seen.has(tab.id)) continue;
      if (SINGLETON_KINDS.has(tab.kind) && tabs.some((t) => t.kind === tab.kind)) continue;
      seen.add(tab.id);
      tabs.push(tab);
    }
    if (tabs.length === 0) return null;
    const activeId = tabs.some((t) => t.id === rec.activeId) ? (rec.activeId as string) : tabs[0].id;
    return { tabs, activeId };
  } catch {
    return null;
  }
}

export function serialize(state: TabsState): string {
  return JSON.stringify({
    v: STORAGE_VERSION,
    activeId: state.activeId,
    // `title` is derived, so it is not stored.
    tabs: state.tabs.map(({ id, kind, params }) => ({ id, kind, params })),
  });
}

// ---- Reducer ------------------------------------------------------------------------------------

export type TabsAction =
  | { type: "open"; tab: Tab; activate: boolean }
  | { type: "navigate"; tab: Tab }
  | { type: "activate"; id: string }
  | { type: "close"; id: string }
  | { type: "move"; from: number; to: number };

function evictIfFull(tabs: Tab[], activeId: string): Tab[] {
  if (tabs.length < MAX_TABS) return tabs;
  const victim = tabs.findIndex((t) => t.id !== activeId); // oldest INACTIVE tab
  return victim === -1 ? tabs : tabs.filter((_, i) => i !== victim);
}

export function tabsReducer(state: TabsState, action: TabsAction): TabsState {
  switch (action.type) {
    case "open": {
      const existing = state.tabs.find((t) => sameTarget(t, action.tab));
      if (existing) {
        return action.activate ? { ...state, activeId: existing.id } : state;
      }
      const tabs = [...evictIfFull(state.tabs, state.activeId), action.tab];
      return { tabs, activeId: action.activate ? action.tab.id : state.activeId };
    }
    case "navigate": {
      // Plain in-tab navigation: reuse an identical existing tab, else replace the active one.
      const existing = state.tabs.find((t) => sameTarget(t, action.tab));
      if (existing) return { ...state, activeId: existing.id };
      return {
        ...state,
        tabs: state.tabs.map((t) =>
          t.id === state.activeId ? { ...action.tab, id: t.id } : t,
        ),
      };
    }
    case "activate":
      return state.tabs.some((t) => t.id === action.id) ? { ...state, activeId: action.id } : state;
    case "close": {
      const index = state.tabs.findIndex((t) => t.id === action.id);
      if (index === -1) return state;
      const tabs = state.tabs.filter((t) => t.id !== action.id);
      if (tabs.length === 0) return defaultState(); // never leave the workspace tab-less
      if (action.id !== state.activeId) return { ...state, tabs };
      return { tabs, activeId: tabs[Math.min(index, tabs.length - 1)].id };
    }
    case "move": {
      const { from, to } = action;
      const n = state.tabs.length;
      if (from === to || from < 0 || to < 0 || from >= n || to >= n) return state;
      const tabs = [...state.tabs];
      const [moved] = tabs.splice(from, 1);
      tabs.splice(to, 0, moved);
      return { ...state, tabs };
    }
  }
}

/** Initial state: persisted tabs (validated) or the default, then the URL hash opened+focused. */
export function initialState(persisted: string | null, hash: string): TabsState {
  let state = parsePersisted(persisted) ?? defaultState();
  const fromHash = decodeHash(hash);
  if (fromHash) state = tabsReducer(state, { type: "open", tab: fromHash, activate: true });
  return state;
}
