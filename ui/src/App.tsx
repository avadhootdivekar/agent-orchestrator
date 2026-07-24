import { useEffect, useState } from "react";
import { api } from "./api";
import { FileBrowser } from "./components/FileBrowser";
import { NewRun } from "./components/NewRun";
import { RunDetail } from "./components/RunDetail";
import { RunsList } from "./components/RunsList";
import { Settings } from "./components/Settings";
import type { WorkspaceInfo } from "./types";

type View = "runs" | "new" | "files" | "settings";

const NAV: { id: View; label: string; glyph: string }[] = [
  { id: "runs", label: "Runs", glyph: "▤" },
  { id: "new", label: "New run", glyph: "＋" },
  { id: "files", label: "Files", glyph: "▸" },
  { id: "settings", label: "Workspace", glyph: "⚙" },
];

const THEME_KEY = "ao-theme";

export function App() {
  const [view, setView] = useState<View>("runs");
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [theme, setTheme] = useState<"light" | "dark" | null>(
    () => (localStorage.getItem(THEME_KEY) as "light" | "dark" | null) ?? null,
  );

  useEffect(() => {
    api.workspace().then(setWorkspace).catch(() => setWorkspace(null));
  }, []);

  useEffect(() => {
    // No stored preference => follow the OS, which the stylesheet handles via
    // prefers-color-scheme. Only stamp data-theme when the user has chosen.
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem(THEME_KEY, theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
      localStorage.removeItem(THEME_KEY);
    }
  }, [theme]);

  const go = (next: View) => {
    setOpenRun(null);
    setView(next);
  };

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">Agent Orchestrator</div>
        <div className="brand-sub" title={workspace?.workspace_root ?? ""}>
          {workspace?.workspace_root ?? "…"}
        </div>

        {NAV.map((item) => (
          <button
            key={item.id}
            className="nav-item"
            aria-current={view === item.id && !openRun ? "page" : undefined}
            onClick={() => go(item.id)}
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
        {openRun ? (
          <RunDetail runId={openRun} onBack={() => setOpenRun(null)} />
        ) : view === "runs" ? (
          <RunsList onOpen={setOpenRun} />
        ) : view === "new" ? (
          <NewRun
            onLaunched={(runId) => {
              // Jump straight to the run that was just started; a launch whose run id was
              // not observable yet falls back to the list, where polling will surface it.
              setView("runs");
              setOpenRun(runId);
            }}
          />
        ) : view === "files" ? (
          <FileBrowser />
        ) : (
          <Settings />
        )}
      </main>
    </div>
  );
}
