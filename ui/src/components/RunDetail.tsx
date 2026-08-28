import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { formatCost, formatCount, formatDuration, formatTimestamp } from "../format";
import type { RunDetail as RunDetailData } from "../types";
import { Empty, ErrorBanner, LiveBadge, StatusChip, Tile } from "./common";

const POLL_MS = 3000;

/** Per-run detail: stats, task table, and the CLI log (FR-R3, FR-R5.2). */
export function RunDetail({ runId, onBack }: { runId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<RunDetailData | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [next, logResponse] = await Promise.all([api.run(runId), api.runLog(runId)]);
      setDetail(next);
      setLog(logResponse.text);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!detail) {
    return (
      <div>
        <div className="page-head">
          <h1>Run</h1>
          <button onClick={onBack}>← Back to runs</button>
        </div>
        <ErrorBanner message={error} />
        {!error ? <Empty>Loading…</Empty> : null}
      </div>
    );
  }

  const { summary } = detail;

  return (
    <div className="stack">
      <div>
        <div className="page-head">
          <div>
            <h1 className="mono">{summary.run_id}</h1>
            <div className="row" style={{ gap: 8, marginTop: 8 }}>
              <StatusChip status={summary.status} />
              {detail.is_live ? <LiveBadge /> : null}
              <span className="tag">{summary.workflow_id}</span>
            </div>
          </div>
          <div className="row">
            <button onClick={onBack}>← Back to runs</button>
            {detail.is_live ? (
              <button disabled={busy} onClick={() => void act(() => api.cancelRun(runId))}>
                Cancel
              </button>
            ) : (
              <button
                disabled={busy || summary.status === "succeeded"}
                onClick={() => void act(() => api.resumeRun(runId))}
              >
                Resume
              </button>
            )}
          </div>
        </div>

        <ErrorBanner message={error} />

        <div className="tiles">
          <Tile
            label="Tasks"
            value={`${summary.task_counts.succeeded ?? 0}/${summary.task_count}`}
            hint={
              Object.entries(summary.task_counts)
                .map(([status, count]) => `${count} ${status}`)
                .join(" · ") || "no tasks"
            }
          />
          <Tile label="Cost" value={formatCost(summary.cost_usd)} />
          <Tile
            label="Tokens"
            value={formatCount(summary.input_tokens + summary.output_tokens)}
            hint={`${formatCount(summary.input_tokens)} in · ${formatCount(
              summary.output_tokens,
            )} out`}
          />
          <Tile
            label="Wall time"
            value={formatDuration(summary.wall_seconds)}
            hint="start to last update"
          />
          <Tile
            label="Actual time"
            value={formatDuration(summary.active_seconds)}
            hint="summed task execution"
          />
        </div>
      </div>

      <div>
        <h2>Tasks</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Status</th>
                <th className="num">Attempts</th>
                <th className="num">Duration</th>
                <th className="num">Tokens</th>
                <th className="num">Cost</th>
                <th>Outputs</th>
              </tr>
            </thead>
            <tbody>
              {detail.tasks.map((task) => (
                <tr key={task.id}>
                  <td className="mono">
                    {task.id}
                    {task.origin !== "static" ? (
                      <span className="tag" style={{ marginLeft: 6 }}>
                        {task.origin}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    <StatusChip status={task.status} />
                  </td>
                  <td className="num">{task.attempts}</td>
                  <td className="num">{formatDuration(task.duration_seconds)}</td>
                  <td className="num">
                    {formatCount(task.input_tokens + task.output_tokens)}
                  </td>
                  <td className="num">{formatCost(task.cost_usd)}</td>
                  <td className="mono muted" style={{ fontSize: 11 }}>
                    {task.output_artifact_path ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {detail.tripped_breakers.length > 0 ? (
        <div>
          <h2>Tripped breakers</h2>
          <div className="card">
            {detail.tripped_breakers.map((breaker, index) => (
              <div key={index} className="mono" style={{ fontSize: 12 }}>
                {String(breaker.id)} — {String(breaker.condition)} → {String(breaker.action)}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div>
        <h2>Run log</h2>
        {log ? (
          <pre className="code">{log}</pre>
        ) : (
          <div className="card">
            <Empty>
              No captured log — this run was not started from the dashboard. Task transcripts
              are on disk under{" "}
              <code className="mono">{detail.run_dir}</code>.
            </Empty>
          </div>
        )}
      </div>

      <div className="muted" style={{ fontSize: 12 }}>
        Started {formatTimestamp(summary.started_at)} · updated{" "}
        {formatTimestamp(summary.updated_at)} · <span className="mono">{detail.run_dir}</span>
      </div>
    </div>
  );
}
