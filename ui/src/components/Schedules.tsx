import { Fragment, useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import {
  formatRelative,
  formatTimestamp,
  isSuppressedEvent,
  RUN_NOW_QUEUED_TEXT,
  SCHEDULE_HISTORY_LIMIT,
  scheduleEventLabel,
} from "../format";
import { TabLink } from "../tabs/TabLink";
import { useTabActions } from "../tabs/context";
import type { Schedule, ScheduleEvent, SchedulesResponse } from "../types";
import { usePolling } from "../usePolling";
import { Empty, ErrorBanner, StatusChip, Tile } from "./common";
import { ScheduleUsage } from "./ScheduleUsage";

/**
 * Schedules change on the order of minutes (cron granularity, daemon tick of seconds), so the
 * 3-4 s run cadence would be wasteful here: poll every 10 s.
 */
export const SCHEDULES_POLL_MS = 10000;

/** Row-level controls only ever act on schedules already declared in `.ao/schedules.yaml`. */
export function Schedules({ clock = Date.now }: { clock?: () => number }) {
  const [data, setData] = useState<SchedulesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [usageOpen, setUsageOpen] = useState<string | null>(null);
  const [history, setHistory] = useState<
    Record<string, ScheduleEvent[] | string>
  >({});
  const actions = useTabActions();

  const load = useCallback(async () => {
    try {
      setData(await api.schedules());
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);
  usePolling(load, SCHEDULES_POLL_MS);

  const toggle = async (s: Schedule) => {
    try {
      await api.setScheduleEnabled(s.id, !s.enabled);
      await load();
    } catch (err) {
      setNotice((n) => ({ ...n, [s.id]: errorMessage(err) }));
    }
  };

  const runNow = async (s: Schedule) => {
    try {
      await api.runScheduleNow(s.id);
      setNotice((n) => ({ ...n, [s.id]: RUN_NOW_QUEUED_TEXT }));
    } catch (err) {
      setNotice((n) => ({ ...n, [s.id]: errorMessage(err) }));
    }
  };

  const toggleHistory = async (id: string) => {
    if (open === id) {
      setOpen(null);
      return;
    }
    setOpen(id);
    try {
      const res = await api.scheduleHistory(id, SCHEDULE_HISTORY_LIMIT);
      setHistory((h) => ({ ...h, [id]: res.events }));
    } catch (err) {
      setHistory((h) => ({ ...h, [id]: errorMessage(err) }));
    }
  };

  const schedules = data?.schedules ?? [];
  const now = clock();
  return (
    <div className="stack">
      <h1>Schedules</h1>
      <ErrorBanner message={error} />
      {data && !data.daemon_seen ? (
        <div className="banner info" role="status">
          ⚠ No scheduler service has run yet, so these schedules will not fire.
          Start one with <code>ao service start</code>. Launches that failed to
          start are listed on the{" "}
          <TabLink target={{ kind: "runs", params: {} }} className="link">
            Runs tab
          </TabLink>
          .
        </div>
      ) : null}
      {data ? (
        <div className="tiles">
          <Tile label="Schedules" value={schedules.length} />
          <Tile
            label="Enabled"
            value={schedules.filter((s) => s.enabled).length}
          />
        </div>
      ) : null}
      {schedules.length === 0 ? (
        <div className="card">
          {data ? (
            <Empty>No schedules — declare them in .ao/schedules.yaml.</Empty>
          ) : error ? (
            <Empty>Schedules could not be loaded.</Empty>
          ) : (
            <Empty>Loading schedules…</Empty>
          )}
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Schedule</th>
                <th>Kind</th>
                <th>Next fire</th>
                <th>Last fire</th>
                <th>Last run</th>
                <th>Status</th>
                <th>Enabled</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <Fragment key={s.id}>
                  <tr>
                    <td>
                      <span className="mono">{s.id}</span>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {s.workflow ?? s.template ?? "—"}
                      </div>
                    </td>
                    <td>{s.kind}</td>
                    <td title={s.next_fire_at ?? undefined}>
                      {formatRelative(s.next_fire_at, now)}
                      <div className="muted" style={{ fontSize: 11 }}>
                        {formatTimestamp(s.next_fire_at)}
                      </div>
                    </td>
                    <td title={s.last_fire_at ?? undefined}>
                      {formatRelative(s.last_fire_at, now)}
                      <div className="muted" style={{ fontSize: 11 }}>
                        {formatTimestamp(s.last_fire_at)}
                      </div>
                    </td>
                    <td>
                      {s.last_run_id ? (
                        <TabLink
                          target={{
                            kind: "run",
                            params: { id: s.last_run_id },
                          }}
                          className="link"
                          onPlainClick={() =>
                            actions.open({
                              kind: "run",
                              params: { id: s.last_run_id as string },
                            })
                          }
                        >
                          {s.last_run_id}
                        </TabLink>
                      ) : (
                        "—"
                      )}
                      {s.last_status ? (
                        <div className="muted" style={{ fontSize: 11 }}>
                          {s.last_status}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      {s.status ? (
                        <StatusChip
                          status={s.enabled ? s.status : "disabled"}
                        />
                      ) : (
                        "—"
                      )}
                      {s.consecutive_failures > 0 ? (
                        <div className="muted" style={{ fontSize: 11 }}>
                          {s.consecutive_failures} consecutive failure
                          {s.consecutive_failures === 1 ? "" : "s"}
                        </div>
                      ) : null}
                      <div className="muted" style={{ fontSize: 11 }}>
                        {s.runs_count}
                        {s.max_runs !== null ? ` / ${s.max_runs}` : ""} runs
                      </div>
                    </td>
                    <td>
                      <label className="row" style={{ gap: 6 }}>
                        <input
                          type="checkbox"
                          role="switch"
                          checked={s.enabled}
                          onChange={() => void toggle(s)}
                          aria-label={`${s.enabled ? "Disable" : "Enable"} ${s.id}`}
                        />
                        {s.enabled ? "on" : "off"}
                      </label>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button type="button" onClick={() => void runNow(s)}>
                          Run now
                        </button>
                        <button
                          type="button"
                          aria-expanded={open === s.id}
                          onClick={() => void toggleHistory(s.id)}
                        >
                          {open === s.id ? "Hide history" : "History"}
                        </button>
                        <button
                          type="button"
                          aria-expanded={usageOpen === s.id}
                          onClick={() =>
                            setUsageOpen(usageOpen === s.id ? null : s.id)
                          }
                        >
                          {usageOpen === s.id ? "Hide usage" : "Usage"}
                        </button>
                      </div>
                      {notice[s.id] ? (
                        <div
                          className="muted"
                          style={{ fontSize: 11 }}
                          role="status"
                        >
                          {notice[s.id]}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                  {usageOpen === s.id ? (
                    <tr>
                      <td colSpan={8}>
                        <ScheduleUsage
                          id={s.id}
                          pollMs={SCHEDULES_POLL_MS}
                          clock={clock}
                        />
                      </td>
                    </tr>
                  ) : null}
                  {open === s.id ? (
                    <tr>
                      <td colSpan={8}>
                        <HistoryRows entries={history[s.id]} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function HistoryRows({
  entries,
}: {
  entries: ScheduleEvent[] | string | undefined;
}) {
  if (entries === undefined) return <Empty>Loading history…</Empty>;
  if (typeof entries === "string") return <ErrorBanner message={entries} />;
  if (entries.length === 0) return <Empty>No events recorded yet.</Empty>;
  return (
    <ul className="schedule-history">
      {entries.map((e, i) => (
        <li
          key={`${e.ts}-${i}`}
          className={isSuppressedEvent(e.event) ? "suppressed" : undefined}
        >
          <span className="muted">{formatTimestamp(e.ts)}</span>{" "}
          <strong>{scheduleEventLabel(e.event)}</strong>
          {e.reason ? <span className="tag">{e.reason}</span> : null}
          {e.run_id ? <span className="mono"> {e.run_id}</span> : null}
          {e.detail ? <span className="muted"> {e.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}
