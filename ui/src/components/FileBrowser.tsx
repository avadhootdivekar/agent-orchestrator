import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import { formatBytes, isMarkdownPath, languageFor } from "../format";
import type { DirListing, FileContent, HtmlPreview as HtmlPreviewData } from "../types";
import { useTabActions } from "../tabs/context";
import { OpenInNewTabButton } from "../tabs/TabLink";
import { Empty, ErrorBanner } from "./common";
import { CodeView } from "./viewer/CodeView";
import { HtmlPreview } from "./viewer/HtmlPreview";
import { ImageView } from "./viewer/ImageView";
import { MarkdownView } from "./viewer/MarkdownView";
import {
  loadTypography,
  saveTypography,
  TypographyControls,
  typographyCssVars,
  type Typography,
} from "./viewer/TypographyControls";

/** Preview/Source toggle (1B.2) — markdown defaults to Preview, markup defaults to Source. */
type ViewMode = "preview" | "source";

/**
 * Directory + code browser (FR-B1..FR-B4).
 *
 * Shows every entry the server returns — hidden dotfiles and binaries included — because
 * inspecting `.ao/`, `.orchestrator/`, and `.git/` is the point. Hidden entries are dimmed
 * rather than filtered so they read as secondary without being invisible.
 */
/** Tooltip on file rows: makes the optional new-tab gesture discoverable. */
const FILE_ROW_HINT = "Click to view here · Ctrl/Cmd-click or middle-click to open in a new tab";

export function FileBrowser({
  initialPath,
  initialRoot,
  onFileOpened,
}: {
  /** Workspace-relative FILE to open on mount (workspace tab, `file` kind); omitted = browse the root. */
  initialPath?: string;
  /** Optional named root (the API's `root` param). */
  initialRoot?: string;
  /** Called after a plain row click opens a file in place, so the host tab can follow it. */
  onFileOpened?: (path: string) => void;
} = {}) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<FileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fileLoading, setFileLoading] = useState(false);

  const [mode, setMode] = useState<ViewMode>("preview");
  const [htmlPreview, setHtmlPreview] = useState<HtmlPreviewData | null>(null);
  const [htmlPreviewError, setHtmlPreviewError] = useState<string | null>(null);

  // Typography (1B.6): loaded once from localStorage (already clamped), then persisted on
  // every change so it survives a reload.
  const [typography, setTypography] = useState<Typography>(() => loadTypography());
  useEffect(() => {
    saveTypography(typography);
  }, [typography]);

  const load = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      setListing(await api.listDir(path, initialRoot));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [initialRoot]);

  // Explicit, optional new tab (ctrl/cmd- or middle-click on a row). `activate: true` so a file
  // that is already open in some tab is focused rather than duplicated (reducer dedups by target).
  const tabActions = useTabActions();
  const openInNewTab = (path: string) => {
    const target = {
      kind: "file" as const,
      params: { path, ...(initialRoot ? { root: initialRoot } : {}) },
    };
    if (tabActions.available) tabActions.open(target, { activate: true });
    else void openFile(path); // outside the workspace shell there are no tabs
  };

  // The `initialPath` this view is already in sync with (null = never synced). A plain click
  // opens a file and retargets the tab to it; the echoed prop must not reload what is on screen.
  const syncedPath = useRef<string | undefined | null>(null);

  const openFile = async (path: string) => {
    syncedPath.current = path;
    setSelected(path);
    setFileLoading(true);
    setContent(null);
    setError(null);
    setHtmlPreview(null);
    setHtmlPreviewError(null);
    try {
      const next = await api.readFile(path, initialRoot);
      setContent(next);
      // Markup previews of untrusted workspace content should be a deliberate click, not
      // automatic (1B.2) — everything else defaults to the rendered view.
      setMode(next.kind === "markup" ? "source" : "preview");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setFileLoading(false);
    }
  };

  // Location from the `file` tab's path (open it and show its directory), else the root. Runs on
  // mount and when the tab is navigated to another path; skipped when the path is the one a
  // plain click just opened (see `syncedPath`).
  useEffect(() => {
    if (syncedPath.current === initialPath) return;
    syncedPath.current = initialPath;
    if (!initialPath) {
      setSelected(null);
      setContent(null);
      void load("");
      return;
    }
    void load(initialPath.split("/").slice(0, -1).join("/"));
    void openFile(initialPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load/openFile are stable per mount (root is in the tab's view key)
  }, [initialPath]);

  // Fetches the sanitized HTML preview lazily, only once the user actually switches a
  // markup file to Preview mode — sanitizing/inlining assets is real server-side work that
  // the (source-by-default) common case shouldn't pay for.
  useEffect(() => {
    if (!content || content.kind !== "markup" || mode !== "preview" || htmlPreview) return;
    let cancelled = false;
    api
      .readFileHtml(content.path, initialRoot)
      .then((preview) => {
        if (!cancelled) setHtmlPreview(preview);
      })
      .catch((err) => {
        if (!cancelled) {
          setHtmlPreviewError(errorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [content, mode, htmlPreview, initialRoot]);

  const currentPath = listing?.path ?? "";
  const segments = currentPath ? currentPath.split("/") : [];

  const parentPath = () => segments.slice(0, -1).join("/");

  // Preview/Source toggle only makes sense for markdown text and markup (1B.2) — plain
  // text, images, and binaries have exactly one viewer each.
  const showModeToggle =
    content !== null &&
    (content.kind === "markup" || (content.kind === "text" && isMarkdownPath(content.path)));

  // Typography controls apply to CodeView/MarkdownView/plain text only — never to images,
  // binaries (no text to style), or the HtmlPreview iframe (see TypographyControls' own
  // comment for why).
  const showTypography =
    content !== null &&
    content.kind !== "image" &&
    content.kind !== "binary" &&
    !(content.kind === "markup" && mode === "preview");

  const renderViewerBody = (current: FileContent) => {
    if (current.kind === "image") return <ImageView content={current} />;
    if (current.kind === "binary") {
      return (
        <div className="banner info">
          Binary file — {formatBytes(current.size)}. Contents are not displayed.
        </div>
      );
    }
    if (current.kind === "markup") {
      if (mode === "source") {
        return <CodeView code={current.text ?? ""} language={languageFor(current.path)} />;
      }
      if (htmlPreviewError) return <ErrorBanner message={htmlPreviewError} />;
      if (!htmlPreview) return <Empty>Loading preview…</Empty>;
      return <HtmlPreview preview={htmlPreview} />;
    }
    // kind === "text"
    if (isMarkdownPath(current.path) && mode === "preview") {
      return (
        <MarkdownView source={current.text ?? ""} filePath={current.path} root={current.root} />
      );
    }
    return <CodeView code={current.text ?? ""} language={languageFor(current.path)} />;
  };

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
            <button title={listing?.root} onClick={() => void load("")}>
              {listing?.root ?? "root"}
            </button>
            {segments.map((segment, index) => (
              <span key={`${segment}-${index}`}>
                <span aria-hidden="true">/</span>
                <button
                  aria-current={index === segments.length - 1 ? "page" : undefined}
                  onClick={() => void load(segments.slice(0, index + 1).join("/"))}
                >
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
                  aria-current={selected === entry.path ? "true" : undefined}
                  title={entry.is_dir ? undefined : FILE_ROW_HINT}
                  onClick={(event) => {
                    if (entry.is_dir) void load(entry.path);
                    else if (event.ctrlKey || event.metaKey) openInNewTab(entry.path);
                    else {
                      // Plain click reuses this file tab: load in place, then sync the tab's
                      // params/title/hash so reload, copy-link and dedupe see this file.
                      void openFile(entry.path);
                      onFileOpened?.(entry.path);
                    }
                  }}
                  onAuxClick={(event) => {
                    if (event.button !== 1 || entry.is_dir) return;
                    event.preventDefault();
                    openInNewTab(entry.path);
                  }}
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
            <Empty>
              {fileLoading
                ? `Loading ${selected ?? "file"}…`
                : selected
                  ? `Could not open ${selected}`
                  : "Select a file to view its contents"}
            </Empty>
          ) : (
            <>
              <div className="page-head">
                <h2 className="mono">
                  {content.path}{" "}
                  <OpenInNewTabButton
                    target={{
                      kind: "file",
                      params: { path: content.path, ...(initialRoot ? { root: initialRoot } : {}) },
                    }}
                    label={content.path}
                  />
                </h2>
                <span className="muted" style={{ fontSize: 12 }}>
                  {formatBytes(content.size)} · {languageFor(content.path)}
                  {content.truncated ? " · truncated" : ""}
                </span>
              </div>

              {content.truncated && content.kind !== "binary" && content.kind !== "image" ? (
                <div className="banner info">
                  Showing the first {formatBytes(content.text?.length ?? 0)} of this file.
                </div>
              ) : null}

              {showModeToggle ? (
                <div className="tabs" role="tablist" aria-label="Viewer mode">
                  <button
                    type="button"
                    role="tab"
                    className="tab"
                    aria-selected={mode === "preview"}
                    onClick={() => setMode("preview")}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className="tab"
                    aria-selected={mode === "source"}
                    onClick={() => setMode("source")}
                  >
                    Source
                  </button>
                </div>
              ) : null}

              {showTypography ? (
                <TypographyControls value={typography} onChange={setTypography} />
              ) : null}

              {content.kind === "markup" && mode === "preview" ? (
                // HtmlPreview renders the file's own sanitized-but-original styling — wrapping
                // it in our typography vars would misrepresent what the document actually
                // looks like, so it deliberately sits outside `.viewer-typography` (1B.6).
                renderViewerBody(content)
              ) : (
                <div className="viewer-typography" style={typographyCssVars(typography)}>
                  {renderViewerBody(content)}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
