import { CONTROL_RE, MAX_PATH_PARAM_CHARS } from "./model";

/**
 * Client-side path *candidate* extraction for auto-linking (A5). This module only guesses: the
 * server (`POST /api/files/resolve`, backed by `FileBrowser.resolve`) is the sole authority on
 * whether a candidate is inside the workspace and exists. Nothing here grants access, and `..`
 * is never collapsed client-side -- candidates containing a `..` segment are simply dropped.
 */

export interface PathCandidate {
  /** Half-open span of the path text within the source string. */
  start: number;
  end: number;
  /** The text as written (what is shown). */
  raw: string;
  /** Workspace-relative path to probe / open. */
  path: string;
}

/** Max candidates extracted from one text block (matches the server's per-request cap). */
export const MAX_CANDIDATES_PER_TEXT = 200;

/** A bare filename (no `/`) is only a candidate when it ends in one of these. */
const KNOWN_EXTENSIONS = new Set([
  "md", "json", "jsonl", "yaml", "yml", "toml", "txt", "log", "py", "ts", "tsx", "js", "jsx",
  "sh", "html", "css", "csv", "png", "jpg", "jpeg", "svg", "diff", "patch", "cfg", "ini",
]);

// Split on whitespace and common delimiters only; a token is then accepted just if EVERY char is
// in SAFE_TOKEN_RE, so a control/bidi/unicode char (or a `scheme:`) poisons the whole token
// instead of splitting it into innocent-looking halves.
const TOKEN_RE = /[^\s"'`<>()[\]{},;|]+/g;
const SAFE_TOKEN_RE = /^[A-Za-z0-9._@+~/-]+$/;
const LINE_SUFFIX_RE = /:\d+(?::\d+)?$/;
const TRAILING_PUNCT_RE = /[.:-]+$/;

/**
 * Turn one raw token into a workspace-relative path, or `null` when it is not a plausible
 * in-workspace path. `workspaceRoot` (absolute, from `/api/workspace`) lets absolute paths that
 * already live inside the workspace be rewritten relative; any other absolute path is dropped.
 */
export function normalizePath(raw: string, workspaceRoot: string | null): string | null {
  if (!raw || raw.length > MAX_PATH_PARAM_CHARS || CONTROL_RE.test(raw)) return null;
  if (raw.startsWith("//") || raw.startsWith("~")) return null;
  let path = raw;
  if (path.startsWith("/")) {
    const root = workspaceRoot?.replace(/\/+$/, "");
    if (!root || !path.startsWith(`${root}/`)) return null;
    path = path.slice(root.length + 1);
  }
  while (path.startsWith("./")) path = path.slice(2);
  path = path.replace(/\/+$/, "");
  if (!path) return null;
  const segments = path.split("/");
  if (segments.some((segment) => segment === ".." || segment === "")) return null;
  return path;
}

function looksLikePath(token: string): boolean {
  if (token.includes("/")) return true;
  const dot = token.lastIndexOf(".");
  if (dot <= 0) return false;
  return KNOWN_EXTENSIONS.has(token.slice(dot + 1).toLowerCase());
}

/** Find path-like spans in *text*. Pure; the result still needs a server probe before linking. */
export function findPathCandidates(text: string, workspaceRoot: string | null): PathCandidate[] {
  const found: PathCandidate[] = [];
  for (const match of text.matchAll(TOKEN_RE)) {
    if (found.length >= MAX_CANDIDATES_PER_TEXT) break;
    const start = match.index ?? 0;
    const token = match[0].replace(LINE_SUFFIX_RE, "").replace(TRAILING_PUNCT_RE, "");
    if (!token || !SAFE_TOKEN_RE.test(token) || !looksLikePath(token)) continue;
    const path = normalizePath(token, workspaceRoot);
    if (path === null) continue;
    found.push({ start, end: start + token.length, raw: token, path });
  }
  return found;
}
