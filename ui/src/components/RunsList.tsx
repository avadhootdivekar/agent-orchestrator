import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import { formatCost, formatCount, formatDuration, formatTimestamp } from "../format";
import { FAILED_LAUNCH_WINDOW_HOURS, readDismissedLaunches } from "../launch";
import type { AggregateStats, LaunchRecord, RunSummary } from "../types";
import { OpenInNewTabButton, TabLink } from "../tabs/TabLink";
import { usePolling } from "../usePolling";
import { Empty, ErrorBanner, LiveBadge, StatusChip, Tile } from "./common";
import { FailedLaunches } from "./FailedLaunches";
import { briefRows, NowRunning } from "./NowRunning";

/** Poll interval for the runs list. Fast enough to feel live, slow enough to stay cheap. */
const POLL_MS = 4000;

/**
 * Runs list plus workspace-wide aggregate stats (FR-R3, FR-R5.1).
 *
 * The hero figure is the run count — exactly one hero per view — with the money/time
 * totals as supporting tiles.
 */
export function RunsList({ onOpen }: { onOpen: (runId: string) => void }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [stats, setStats] = useState<AggregateStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failedLaunches, setFailedLaunches] = useState<LaunchRecord[]>([]);
  const [dismissed, setDismissed] = useState<string[]>(readDismissedLaunches);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [runList, aggregate, failed] = await Promise.all([
        api.runs(),
        api.runStats(),
        // Independent of the run table: an older backend (404) must not break the list.
        api
          .launches({ status: "failed_to_start", sinceHours: FAILED_LAUNCH_WINDOW_HOURS })
          .catch(() => [] as LaunchRecord[]),
      ]);
      setRuns(runList);
      setStats(aggregate);
      setFailedLaunches(Array.isArray(failed) ? failed : []);
      setLoaded(true);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  usePolling(refresh, POLL_MS);

  const manualRefresh = async () => {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  };

  const act = async (runId: string, action: () => Promise<unknown>) => {
    setBusy(runId);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const remove = (runId: string) => {
    if (!window.confirm(`Delete run ${runId}? Its artifacts and logs are removed for good.`)) {
      return;
    }
    void act(runId, () => api.deleteRun(runId));
  };

  const cancel = (runId: string) => {
    if (!window.confirm(`Cancel run ${runId}? Its running tasks are stopped.`)) return;
    void act(runId, () => api.cancelRun(runId));
  };

  return (
    <div>
      <div className="page-head">
        <h1>Runs</h1>
        <button onClick={() => void manualRefresh()} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <ErrorBanner message={error} />

      <FailedLaunches
        launches={failedLaunches.filter((l) => !dismissed.includes(l.launch_id))}
        onDismiss={setDismissed}
      />

      {stats ? (
        <>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="tile-label">Total runs</div>
            <div className="hero">{stats.total_runs}</div>
            <div className="tile-hint">
              {Object.entries(stats.runs_by_status)
                .map(([status, count]) => `${count} ${status}`)
                .join(" · ") || "no runs yet"}
            </div>
          </div>

          <div className="tiles">
            <Tile label="Total cost" value={formatCost(stats.total_cost_usd)} />
            <Tile
              label="Total tasks"
              value={formatCount(stats.total_tasks)}
              hint={`${stats.tasks_by_status.succeeded ?? 0} succeeded`}
            />
            <Tile
              label="Tokens"
              value={formatCount(stats.total_input_tokens + stats.total_output_tokens)}
              hint={`${formatCount(stats.total_input_tokens)} in · ${formatCount(
                stats.total_output_tokens,
              )} out`}
            />
            <Tile
              label="Wall time"
              value={formatDuration(stats.total_wall_seconds)}
              hint="start to last update"
            />
            <Tile
              label="Actual time"
              value={formatDuration(stats.total_active_seconds)}
              hint="summed task execution"
            />
          </div>
        </>
      ) : null}

      {runs.length === 0 ? (
        <div className="card">
          {loaded ? (
            <Empty>No runs yet — start one from “New run”.</Empty>
          ) : error ? (
            <Empty>Runs could not be loaded.</Empty>
          ) : (
            <Empty>Loading runs…</Empty>
          )}
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th className="num">Tasks</th>
                <th className="num">Cost</th>
                <th className="num">Tokens</th>
                <th className="num" title="Start to last update">
                  Wall
                </th>
                <th className="num" title="Summed task execution">
                  Actual
                </th>
                <th>Started</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <TabLink
                      target={{ kind: "run", params: { id: run.run_id } }}
                      className="link"
                      onPlainClick={() => onOpen(run.run_id)}
                    >
                      {run.run_id}
                    </TabLink>
                    <OpenInNewTabButton
                      target={{ kind: "run", params: { id: run.run_id } }}
                      label={`run ${run.run_id}`}
                    />
                    <div className="muted" style={{ fontSize: 11 }}>
                      {run.workflow_id}
                    </div>
                    {run.prompt_preview ? (
                      // Plain text child: React escapes it, so a hostile prompt is never parsed.
                      <div className="prompt-preview" title={run.prompt_preview}>
                        {run.prompt_preview}
                      </div>
                    ) : null}
                    {run.running_tasks && run.running_tasks.length > 0 ? (
                      <NowRunning
                        variant="compact"
                        rows={briefRows(run.running_tasks, Date.now())}
                      />
                    ) : null}
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <StatusChip status={run.status} />
                      {run.is_live ? <LiveBadge /> : null}
                    </div>
                  </td>
                  <td className="num">
                    {run.task_counts.succeeded ?? 0}/{run.task_count}
                  </td>
                  <td className="num">{formatCost(run.cost_usd)}</td>
                  <td className="num">
                    {formatCount(run.input_tokens + run.output_tokens)}
                  </td>
                  <td className="num">{formatDuration(run.wall_seconds)}</td>
                  <td className="num">{formatDuration(run.active_seconds)}</td>
                  <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                    {formatTimestamp(run.started_at)}
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      {run.is_live ? (
                        <button
                          disabled={busy === run.run_id}
                          onClick={() => cancel(run.run_id)}
                        >
                          Cancel
                        </button>
                      ) : (
                        <button
                          disabled={busy === run.run_id || run.status === "succeeded"}
                          title={
                            run.status === "succeeded"
                              ? "Nothing left to do — this run already succeeded"
                              : "Resume from completed artifacts"
                          }
                          onClick={() => void act(run.run_id, () => api.resumeRun(run.run_id))}
                        >
                          Resume
                        </button>
                      )}
                      <button
                        className="danger"
                        disabled={busy === run.run_id || run.is_live}
                        title={run.is_live ? "Cancel the run before deleting it" : "Delete run"}
                        onClick={() => remove(run.run_id)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
