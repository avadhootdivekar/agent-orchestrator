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

/** Language hint for the code viewer, derived from the file extension. */
export function languageFor(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    py: "python",
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    md: "markdown",
    sh: "shell",
    toml: "toml",
    html: "html",
    css: "css",
  };
  return map[ext] ?? "text";
}
