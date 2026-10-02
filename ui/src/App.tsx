import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { api } from "./api";
import type { WorkspaceInfo } from "./types";
import { TabActionsContext, TabActiveContext, type TabActions } from "./tabs/context";
import {
  decodeHash,
  encodeHash,
  initialState,
  makeTab,
  tabsReducer,
  type TabKind,
} from "./tabs/model";
import { readStoredTabs, writeStoredTabs } from "./tabs/storage";
import { TabBar } from "./tabs/TabBar";
import { TabView } from "./tabs/TabView";

/** Sidebar entries: each opens (or focuses) the tab of its kind. */
const NAV: { kind: TabKind; label: string; glyph: string }[] = [
  { kind: "runs", label: "Runs", glyph: "▤" },
  { kind: "usage", label: "Usage", glyph: "∑" },
  { kind: "new", label: "New run", glyph: "＋" },
  { kind: "file", label: "Files", glyph: "▸" },
  { kind: "settings", label: "Workspace", glyph: "⚙" },
];

const THEME_KEY = "ao-theme";

/** Guarded theme read: blocked storage must not stop the dashboard from rendering. */
function readTheme(): "light" | "dark" | null {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null;
  }
}

export function App() {
  const [state, dispatch] = useReducer(tabsReducer, undefined, () =>
    initialState(readStoredTabs(), window.location.hash),
  );
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [theme, setTheme] = useState<"light" | "dark" | null>(readTheme);

  const active = state.tabs.find((t) => t.id === state.activeId) ?? state.tabs[0];

  useEffect(() => {
    api.workspace().then(setWorkspace).catch(() => setWorkspace(null));
  }, []);

  useEffect(() => {
    // No stored preference => follow the OS, which the stylesheet handles via
    // prefers-color-scheme. Only stamp data-theme when the user has chosen.
    try {
      if (theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem(THEME_KEY, theme);
      } else {
        document.documentElement.removeAttribute("data-theme");
        localStorage.removeItem(THEME_KEY);
      }
    } catch {
      /* storage blocked: the in-memory theme still applies */
    }
  }, [theme]);

  // Persist the tab set (guarded inside) whenever it changes.
  useEffect(() => {
    writeStoredTabs(state);
  }, [state]);

  // The active tab IS the URL (hash only: the backend SPA fallback is irrelevant to it).
  const activeHash = encodeHash(active);
  useEffect(() => {
    if (window.location.hash === activeHash) return;
    try {
      // replaceState: switching tabs must not flood the browser history, and it does not
      // fire `hashchange`, so there is no feedback loop with the listener below.
      window.history.replaceState(null, "", activeHash);
    } catch {
      /* history API unavailable */
    }
  }, [activeHash]);

  // A hash edited/pasted/followed by hand opens (or focuses) that tab -- validated first.
  useEffect(() => {
    const onHashChange = () => {
      const tab = decodeHash(window.location.hash);
      if (tab) dispatch({ type: "open", tab, activate: true });
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback<TabActions["navigate"]>((target) => {
    const tab = makeTab(target.kind, target.params);
    if (tab) dispatch({ type: "navigate", tab });
  }, []);
  const open = useCallback<TabActions["open"]>((target, opts) => {
    const tab = makeTab(target.kind, target.params);
    if (tab) dispatch({ type: "open", tab, activate: opts?.activate ?? true });
  }, []);
  const actions = useMemo<TabActions>(
    () => ({ available: true, navigate, open }),
    [navigate, open],
  );

  const go = (kind: TabKind) => open({ kind, params: {} }, { activate: true });

  return (
    <TabActionsContext.Provider value={actions}>
      <div className="app">
        <nav className="sidebar">
          <div className="brand">Agent Orchestrator</div>
          <div className="brand-sub" title={workspace?.workspace_root ?? ""}>
            {workspace?.workspace_root ?? "…"}
          </div>

          {NAV.map((item) => (
            <button
              key={item.kind}
              className="nav-item"
              aria-current={active.kind === item.kind ? "page" : undefined}
              onClick={() => go(item.kind)}
            >
              <span aria-hidden="true">{item.glyph}</span>
              {item.label}
            </button>
          ))}

          <div className="sidebar-footer">
            <button
              className="nav-item"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              title="Toggle light and dark theme"
            >
              <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
              {theme === "dark" ? "Light theme" : "Dark theme"}
            </button>
          </div>
        </nav>

        <main className="main">
          <TabBar
            tabs={state.tabs}
            activeId={active.id}
            onActivate={(id) => dispatch({ type: "activate", id })}
            onClose={(id) => dispatch({ type: "close", id })}
            onMove={(from, to) => dispatch({ type: "move", from, to })}
          />
          {/* Every tab stays mounted (its state survives a switch); inactive ones are hidden
              and, via TabActiveContext, stop polling. The key remounts on a target change. */}
          {state.tabs.map((tab) => (
            <div
              key={tab.id}
              id={`tabpanel-${tab.id}`}
              role="tabpanel"
              aria-labelledby={`tab-${tab.id}`}
              className="tab-panel"
              hidden={tab.id !== active.id}
            >
              <TabActiveContext.Provider value={tab.id === active.id}>
                <TabView key={encodeHash(tab)} tab={tab} />
              </TabActiveContext.Provider>
            </div>
          ))}
        </main>
      </div>
    </TabActionsContext.Provider>
  );
}
