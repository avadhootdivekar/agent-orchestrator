/** Display formatting helpers. Pure functions — unit-tested in src/test/format.test.ts. */

/** Human-readable byte size (1024-based, matching what a file manager shows). */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** Compact duration: 45s, 3m 20s, 2h 15m. Sub-second durations read as "<1s", not "0s". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  if (seconds < 0) return "—";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;

  const totalMinutes = Math.floor(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m ${Math.round(seconds % 60)}s`;

  const hours = Math.floor(totalMinutes / 60);
  return `${hours}h ${totalMinutes % 60}m`;
}

/**
 * USD with enough precision to be useful at agent-run scale.
 *
 * Sub-cent amounts keep 4 decimals — a task that cost $0.0032 must not display as
 * "$0.00", which would read as free.
 */
export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined || !Number.isFinite(usd)) return "—";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/** Compact token/row counts: 1284 -> 1.3K, 2_400_000 -> 2.4M. */
export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  if (Math.abs(n) < 1000) return String(n);
  if (Math.abs(n) < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/**
 * Percentage with one decimal place (e.g. `42.3%`).
 *
 * `null`/`undefined` render as `"n/a"`, matching the CLI's own convention for the same
 * figure (`cli.py::hit_rate_str`, `ao report timing`) — a real, meaningful distinction from
 * a genuine 0% rate, never conflated with it by defaulting to "0%".
 */
export function formatPercent(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "n/a";
  return `${(ratio * 100).toFixed(1)}%`;
}

/** Local date-time for an ISO-8601 string; empty/invalid input renders as an em dash. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * Semantic bucket for a run/task status.
 *
 * Drives BOTH the color and the glyph of a status chip. The pairing is deliberate:
 * status color alone never carries meaning (dataviz status-palette rule), so every chip
 * ships an icon plus the status word.
 */
export type StatusTone = "good" | "critical" | "active" | "neutral";

export function statusTone(status: string): StatusTone {
  switch (status) {
    case "succeeded":
      return "good";
    case "failed":
    case "timed_out":
      return "critical";
    case "running":
      return "active";
    default:
      // pending / skipped / cancelled / not_taken — all "nothing happening here".
      return "neutral";
  }
}

export function statusGlyph(status: string): string {
  switch (status) {
    case "succeeded":
      return "✓";
    case "failed":
      return "✕";
    case "timed_out":
      return "⧗";
    case "running":
      return "▶";
    case "cancelled":
      return "⊘";
    case "skipped":
      return "↷";
    case "not_taken":
      return "⋯";
    default:
      return "○";
  }
}

/**
 * Language hint for the code viewer, derived from the file extension.
 *
 * Names match the curated highlight.js subset registered in `components/viewer/CodeView.tsx`
 * (see 1B.1) — only that subset ships in the bundle, so a name outside it just falls back to
 * plain text via `hljs.getLanguage` returning undefined.
 */
export function languageFor(path: string): string {
  const base = path.split("/").pop() ?? path;
  const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";

  // Dockerfiles are conventionally extensionless — check the bare name before falling
  // through to the extension map below.
  if (base.toLowerCase() === "dockerfile") return "dockerfile";

  const map: Record<string, string> = {
    py: "python",
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    md: "markdown",
    markdown: "markdown",
    sh: "bash",
    bash: "bash",
    toml: "toml",
    ini: "ini",
    html: "xml",
    htm: "xml",
    xml: "xml",
    svg: "xml",
    css: "css",
    rs: "rust",
    go: "go",
    c: "c",
    h: "c",
    cpp: "cpp",
    cc: "cpp",
    cxx: "cpp",
    hpp: "cpp",
    java: "java",
    sql: "sql",
    diff: "diff",
    patch: "diff",
    dockerfile: "dockerfile",
  };
  return map[ext] ?? "text";
}

/** True for extensions the dashboard treats as markdown (drives the Preview/Source toggle). */
export function isMarkdownPath(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return ext === "md" || ext === "markdown";
}

// ---------- usage + feedback helpers (E-Us9Kd4) ----------

/** Mirrors feedback.py::MAX_NOTE_CHARS; the server is authoritative, this is early UX. */
export const MAX_NOTE_CHARS = 2000;

export const FEEDBACK_REASONS = [
  "wrong",
  "incomplete",
  "unnecessary",
  "too-costly",
  "needed-hand-fixing",
] as const;

/** Null when the note is acceptable, else a human message. Counts the trimmed text, like the server. */
export function noteError(note: string): string | null {
  const length = note.trim().length;
  return length > MAX_NOTE_CHARS
    ? `Note is ${length} characters; the limit is ${MAX_NOTE_CHARS}.`
    : null;
}

/** "3g / 1o / 0b" feedback split; "—" when no task was rated at all. */
export function formatFeedbackSplit(g: {
  fb_good: number;
  fb_ok: number;
  fb_bad: number;
  fb_rated_tasks: number;
}): string {
  if (g.fb_rated_tasks === 0) return "—";
  return `${g.fb_good}g / ${g.fb_ok}o / ${g.fb_bad}b`;
}

/** "n/a" for a missing denominator, so 0 and "unknown" are never conflated. */
export function formatCountOrNA(n: number | null | undefined, denominator: number): string {
  return denominator > 0 && n !== null && n !== undefined ? String(n) : "n/a";
}

/** Number of leading sha256 hex chars shown in the UI. */
export const SHORT_SHA_LENGTH = 8;

/** Short form of a hex digest for display; empty input renders as an em dash. */
export function formatShortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, SHORT_SHA_LENGTH) : "—";
}

const PROMPT_SOURCE_LABELS: Record<string, string> = {
  "cli-prompt": "--prompt",
  "cli-prompt-file": "--prompt-file",
  "workflow-file": "workflow file",
};

/** Human label for a RunPrompt.source; unknown values pass through unchanged. */
export function formatPromptSource(source: string): string {
  return PROMPT_SOURCE_LABELS[source] ?? source;
}
