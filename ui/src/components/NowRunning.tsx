import { formatCost, formatCount, formatDuration } from "../format";
import type { RunActivity, RunningTaskBrief, TaskActivity, TaskStat } from "../types";
import { StatusChip } from "./common";

/**
 * "Now running" box (E-iafh2F FR-4/FR-5).
 *
 * HARD UX REQUIREMENT (user): the list is exactly {@link VISIBLE_ROWS} rows tall. With more
 * running tasks the SAME fixed-height box scrolls internally; it never grows and it has no
 * collapse/expand control, so the page behaves identically whether 0, 3 or 30 tasks run.
 * The height is set INLINE (not only in CSS) so it is a property of the component, enforced
 * by tests, rather than of a stylesheet that could be dropped.
 */
export const VISIBLE_ROWS = 3;
/** Row heights in px: the box height is always VISIBLE_ROWS x one of these. */
export const FULL_ROW_PX = 44;
export const COMPACT_ROW_PX = 26;

export type NowRunningVariant = "full" | "compact";

/** One display row; every metric is nullable (null renders as an em dash). */
export interface NowRunningRow {
  id: string;
  status: string;
  model: string | null;
  effort: string | null;
  turns: number | null;
  tokens: number | null;
  /** Tokens are the live estimate (lower bound), shown with a "~" prefix. */
  tokensEstimated: boolean;
  costUsd: number | null;
  elapsedSeconds: number | null;
  lastAction: string | null;
  idleSeconds: number | null;
  stuck: boolean;
}

const DASH = "—";

function secondsSince(iso: string | null | undefined, nowMs: number): number | null {
  if (!iso) return null;
  const started = Date.parse(iso);
  return Number.isNaN(started) ? null : Math.max(0, (nowMs - started) / 1000);
}

/** Join the run detail's task stats with the activity payload into display rows (pure). */
export function nowRunningRows(
  tasks: TaskStat[],
  activity: RunActivity | null,
  nowMs: number,
): NowRunningRow[] {
  return tasks
    .filter((task) => task.status === "running")
    .map((task) => {
      const act: TaskActivity | undefined = activity?.tasks[task.id];
      const tokens =
        act && act.input_tokens !== null && act.output_tokens !== null
          ? act.input_tokens + act.output_tokens
          : null;
      return {
        id: task.id,
        status: task.status,
        model: task.model ?? null,
        effort: task.effort ?? null,
        turns: act?.turns ?? null,
        tokens,
        tokensEstimated: act?.tokens_estimated ?? false,
        costUsd: act?.cost_usd ?? null,
        elapsedSeconds: act?.elapsed_seconds ?? secondsSince(task.started_at, nowMs),
        lastAction: act?.last_action ?? null,
        idleSeconds: act?.idle_seconds ?? null,
        stuck: act?.stuck ?? false,
      };
    });
}

/** Compact rows for the runs list come from state alone (no activity fetch per run). */
export function briefRows(briefs: RunningTaskBrief[], nowMs: number): NowRunningRow[] {
  return briefs.map((brief) => ({
    id: brief.id,
    status: "running",
    model: brief.model,
    effort: brief.effort,
    turns: null,
    tokens: null,
    tokensEstimated: false,
    costUsd: null,
    elapsedSeconds: secondsSince(brief.started_at, nowMs),
    lastAction: null,
    idleSeconds: null,
    stuck: false,
  }));
}

const STUCK_TITLE =
  "No transcript output for 5+ minutes. The task may be stuck, or running one long command.";

function modelLabel(row: NowRunningRow): string {
  if (!row.model && !row.effort) return DASH;
  return [row.model ?? DASH, row.effort].filter(Boolean).join(" · ");
}

function FullRow({ row }: { row: NowRunningRow }) {
  return (
    <div
      className="now-running-row"
      role="listitem"
      style={{ height: FULL_ROW_PX, minHeight: FULL_ROW_PX, maxHeight: FULL_ROW_PX }}
    >
      <span className="mono now-running-id" title={row.id}>
        {row.id}
      </span>
      <span>
        <StatusChip status={row.status} />
      </span>
      <span className="muted" title="model · effort">
        {row.model ?? DASH}
      </span>
      <span className="muted">{row.effort ?? DASH}</span>
      <span className="num" title="turns so far">
        {row.turns ?? DASH}
      </span>
      <span
        className="num"
        title={row.tokensEstimated ? "Live estimate: a lower bound until the task settles" : "tokens"}
      >
        {row.tokens === null ? DASH : `${row.tokensEstimated ? "~" : ""}${formatCount(row.tokens)}`}
      </span>
      <span className="num" title="Cost of finished attempts so far; the final cost lands at settle">
        {formatCost(row.costUsd)}
      </span>
      <span className="num">{formatDuration(row.elapsedSeconds)}</span>
      <span className="now-running-action" title={row.lastAction ?? ""}>
        {row.lastAction ?? <span className="muted">{DASH}</span>}
        {row.idleSeconds !== null && !row.stuck ? (
          <span className="muted"> · {formatDuration(row.idleSeconds)} ago</span>
        ) : null}
      </span>
      <span>
        {row.stuck ? (
          <span className="chip critical" title={STUCK_TITLE}>
            <span className="chip-glyph" aria-hidden="true">
              !
            </span>
            idle {formatDuration(row.idleSeconds)}
          </span>
        ) : null}
      </span>
    </div>
  );
}

function CompactRow({ row }: { row: NowRunningRow }) {
  return (
    <div
      className="now-running-row compact"
      role="listitem"
      style={{ height: COMPACT_ROW_PX, minHeight: COMPACT_ROW_PX, maxHeight: COMPACT_ROW_PX }}
    >
      <span className="live-dot" aria-hidden="true" />
      <span className="mono now-running-id" title={row.id}>
        {row.id}
      </span>
      <span className="muted">{modelLabel(row)}</span>
      <span className="num">{formatDuration(row.elapsedSeconds)}</span>
    </div>
  );
}

/**
 * Fixed 3-row "Now running" list. `rows` is the full set; the box scrolls, never resizes.
 * The empty state occupies the same height, so the page never jumps when a task starts.
 */
export function NowRunning({
  rows,
  variant = "full",
}: {
  rows: NowRunningRow[];
  variant?: NowRunningVariant;
}) {
  const compact = variant === "compact";
  const rowPx = compact ? COMPACT_ROW_PX : FULL_ROW_PX;
  const boxPx = VISIBLE_ROWS * rowPx;

  return (
    <section
      className={`now-running ${compact ? "compact" : "full"}`}
      aria-label="Now running"
      data-testid="now-running"
    >
      {compact ? null : (
        <>
          <h2 className="now-running-title">
            Now running
            <span className="muted now-running-count"> {rows.length}</span>
          </h2>
          <div className="now-running-head" aria-hidden="true">
            <span>Task</span>
            <span>Status</span>
            <span>Model</span>
            <span>Effort</span>
            <span className="num">Turns</span>
            <span className="num">Tokens</span>
            <span className="num">Cost</span>
            <span className="num">Elapsed</span>
            <span>Last action</span>
            <span />
          </div>
        </>
      )}
      <div
        className="now-running-body"
        role="list"
        aria-label="Running tasks"
        data-testid="now-running-body"
        // tabIndex so keyboard users can scroll the box when it overflows.
        tabIndex={0}
        style={{ height: boxPx, minHeight: boxPx, maxHeight: boxPx, overflowY: "auto" }}
      >
        {rows.length === 0 ? (
          <div className="now-running-empty muted" style={{ height: boxPx }}>
            No tasks running
          </div>
        ) : (
          rows.map((row) =>
            compact ? <CompactRow key={row.id} row={row} /> : <FullRow key={row.id} row={row} />,
          )
        )}
      </div>
    </section>
  );
}
