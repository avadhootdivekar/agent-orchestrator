import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import { formatCost, formatRelative, formatTimestamp } from "../format";
import type { ScheduleEvent, ScheduleUsageDoc } from "../types";
import { usePolling } from "../usePolling";
import { Empty, ErrorBanner } from "./common";

/** Governance feed length (T-8knqVe AC6). */
export const GOVERNANCE_FEED_LIMIT = 50;

/**
 * Quota/cost usage meters per window plus the recent governance events for one schedule.
 * Numbers carry the information (not colour alone); `⚠` marks partial/unknown cost confidence.
 */
export function ScheduleUsage({
  id,
  pollMs,
  clock = Date.now,
}: {
  id: string;
  pollMs: number;
  clock?: () => number;
}) {
  const [usage, setUsage] = useState<ScheduleUsageDoc | null>(null);
  const [events, setEvents] = useState<ScheduleEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, g] = await Promise.all([
        api.scheduleUsage(id),
        api.governanceEvents(id, GOVERNANCE_FEED_LIMIT),
      ]);
      setUsage(u);
      setEvents(g.events);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [id]);
  usePolling(load, pollMs);

  const now = clock();
  return (
    <div className="stack" aria-label={`Usage for ${id}`}>
      <ErrorBanner message={error} />
      {usage === null ? (
        error ? null : (
          <Empty>Loading usage…</Empty>
        )
      ) : usage.windows.length === 0 ? (
        <Empty>No quota or cost limits declared.</Empty>
      ) : (
        <ul className="schedule-usage">
          {usage.windows.map((w) => (
            <li key={w.window_key}>
              <strong>{w.period}</strong>{" "}
              {w.runs_max !== null ? (
                <span>
                  runs {w.runs_used}/{w.runs_max}
                </span>
              ) : (
                <span>runs {w.runs_used}</span>
              )}
              {w.max_usd !== null ? (
                <span>
                  {" "}
                  · spend {formatCost(w.actual_usd)}
                  {w.reserved_usd > 0
                    ? ` (+${formatCost(w.reserved_usd)} reserved)`
                    : ""}{" "}
                  / {formatCost(w.max_usd)}
                </span>
              ) : null}
              {w.confidence !== "actual" ? (
                <span
                  className="tag"
                  title={`cost confidence: ${w.confidence}`}
                >
                  ⚠ {w.confidence}
                </span>
              ) : null}
              <span className="muted" title={w.resets_at ?? undefined}>
                {" "}
                · resets {formatRelative(w.resets_at, now)} (
                {formatTimestamp(w.resets_at)})
              </span>
              {w.skipped > 0 ? (
                <span className="tag">
                  {w.quota_exhausted} quota_exhausted / {w.skipped} skipped
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {events !== null && events.length === 0 ? (
        <Empty>No governance events.</Empty>
      ) : null}
      {events !== null && events.length > 0 ? (
        <ul className="schedule-history" aria-label="Governance events">
          {events.map((e, i) => (
            <li key={`${e.ts}-${i}`}>
              <span className="muted">{formatTimestamp(e.ts)}</span>{" "}
              <strong>{e.event.replace("schedule.governance.", "")}</strong>
              {e.reason ? <span className="tag">{e.reason}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
