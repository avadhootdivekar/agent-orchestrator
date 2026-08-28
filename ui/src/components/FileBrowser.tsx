import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { formatBytes, languageFor } from "../format";
import type { DirListing, FileContent } from "../types";
import { Empty, ErrorBanner } from "./common";

/**
 * Directory + code browser (FR-B1..FR-B4).
 *
 * Shows every entry the server returns — hidden dotfiles and binaries included — because
 * inspecting `.ao/`, `.orchestrator/`, and `.git/` is the point. Hidden entries are dimmed
 * rather than filtered so they read as secondary without being invisible.
 */
export function FileBrowser() {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<FileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      setListing(await api.listDir(path));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load("");
  }, [load]);

  const openFile = async (path: string) => {
    setSelected(path);
    setContent(null);
    setError(null);
    try {
      setContent(await api.readFile(path));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const currentPath = listing?.path ?? "";
  const segments = currentPath ? currentPath.split("/") : [];

  const parentPath = () => segments.slice(0, -1).join("/");

  return (
    <div>
      <div className="page-head">
        <h1>Files</h1>
        {listing ? <span className="mono muted">{listing.absolute}</span> : null}
      </div>

      <ErrorBanner message={error} />

      <div className="browser">
        <div className="card">
          <nav className="breadcrumb" aria-label="Breadcrumb">
            <button onClick={() => void load("")}>{listing?.root ?? "root"}</button>
            {segments.map((segment, index) => (
              <span key={`${segment}-${index}`}>
                <span aria-hidden="true">/</span>
                <button onClick={() => void load(segments.slice(0, index + 1).join("/"))}>
                  {segment}
                </button>
              </span>
            ))}
          </nav>

          <ul className="file-list">
            {currentPath ? (
              <li>
                <button className="file-row" onClick={() => void load(parentPath())}>
                  <span className="file-icon" aria-hidden="true">
                    ↰
                  </span>
                  <span className="file-name">..</span>
                </button>
              </li>
            ) : null}

            {listing?.entries.map((entry) => (
              <li key={entry.path}>
                <button
                  className={`file-row${entry.hidden ? " is-hidden" : ""}`}
                  aria-selected={selected === entry.path}
                  onClick={() => (entry.is_dir ? void load(entry.path) : void openFile(entry.path))}
                >
                  <span className="file-icon" aria-hidden="true">
                    {entry.is_dir ? "▸" : entry.is_symlink ? "↗" : "·"}
                  </span>
                  <span className="file-name" title={entry.name}>
                    {entry.name}
                  </span>
                  {!entry.is_dir ? (
                    <span className="file-size">{formatBytes(entry.size)}</span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>

          {listing && listing.entries.length === 0 && !loading ? (
            <Empty>Empty directory</Empty>
          ) : null}
        </div>

        <div className="card">
          {!content ? (
            <Empty>Select a file to view its contents</Empty>
          ) : (
            <>
              <div className="page-head">
                <h2 className="mono">{content.path}</h2>
                <span className="muted" style={{ fontSize: 12 }}>
                  {formatBytes(content.size)} · {languageFor(content.path)}
                  {content.truncated ? " · truncated" : ""}
                </span>
              </div>
              {content.is_binary ? (
                <div className="banner info">
                  Binary file — {formatBytes(content.size)}. Contents are not displayed.
                </div>
              ) : (
                <>
                  {content.truncated ? (
                    <div className="banner info">
                      Showing the first {formatBytes(content.text?.length ?? 0)} of this file.
                    </div>
                  ) : null}
                  <pre className="code">{content.text}</pre>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
