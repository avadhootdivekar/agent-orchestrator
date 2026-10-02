import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { formatPercent, formatTimestamp } from "../format";
import type { FeedbackEntry, FeedbackRequest, RunSignalsResponse } from "../types";
import { Empty, ErrorBanner } from "./common";
import { FeedbackForm } from "./FeedbackControls";

function describe(entry: FeedbackEntry): string {
  const target = entry.scope === "run" ? "run" : `task ${entry.task_id}`;
  const reasons = entry.reasons.length ? ` [${entry.reasons.join(", ")}]` : "";
  return `${formatTimestamp(entry.ts)} · ${target} · ${entry.rating}${reasons} · ${entry.source}`;
}

/** One history line. The note is a React text child, so it is escaped — never parsed as HTML. */
export function FeedbackHistory({ entries }: { entries: FeedbackEntry[] }) {
  if (entries.length === 0) return <div className="muted">No feedback recorded yet.</div>;
  return (
    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
      {[...entries].reverse().map((entry, index) => (
        <li key={`${entry.ts}-${index}`}>
          <span className="mono">{describe(entry)}</span>
          {entry.note ? (
            <div style={{ whiteSpace: "pre-wrap" }} data-testid="feedback-note">
              {entry.note}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** Shared load/post state for a run's feedback. Posting errors surface via `error`. */
export function useFeedback(runId: string) {
  const [entries, setEntries] = useState<FeedbackEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);

  const load = useCallback(async () => {
    try {
      setEntries((await api.feedback(runId)).entries ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const post = useCallback(
    async (body: FeedbackRequest) => {
      setPosting(true);
      setError(null);
      try {
        await api.postFeedback(runId, body);
        await load();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setPosting(false);
      }
    },
    [runId, load],
  );

  return { entries, error, posting, post };
}

export function RunFeedbackPanel({
  entries,
  posting,
  error,
  onSubmit,
}: {
  entries: FeedbackEntry[];
  posting: boolean;
  error: string | null;
  onSubmit: (body: FeedbackRequest) => Promise<void>;
}) {
  return (
    <div>
      <h2>Feedback</h2>
      <div className="card stack" style={{ gap: 12 }}>
        <ErrorBanner message={error} />
        <FeedbackForm label="Rate this run" scope="run" disabled={posting} onSubmit={onSubmit} />
        <div>
          <strong style={{ fontSize: 12 }}>History</strong>
          <FeedbackHistory entries={entries} />
        </div>
      </div>
    </div>
  );
}

/** Implicit signals + diff survival. Survival runs git, so it is fetched only on request. */
export function SignalsPanel({ runId }: { runId: string }) {
  const [data, setData] = useState<RunSignalsResponse | null>(null);
  const [withSurvival, setWithSurvival] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingSurvival, setLoadingSurvival] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSignals = async (survival: boolean) => {
    setLoading(true);
    setLoadingSurvival(survival);
    setError(null);
    try {
      setData(await api.runSignals(runId, survival));
      setWithSurvival(survival);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
      setLoadingSurvival(false);
    }
  };

  useEffect(() => {
    void fetchSignals(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const s = data?.signals;
  return (
    <div>
      <h2>Implicit signals</h2>
      <div className="card stack" style={{ gap: 8, fontSize: 12 }}>
        <ErrorBanner message={error} />
        {s ? (
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span className="tag">landed: {s.landed ?? "unknown"}</span>
            <span className="tag">follow-up commits: {s.followup_commits ?? "n/a"}</span>
            <span className="tag">reverted commits: {s.reverted_commits ?? "n/a"}</span>
            <span className="tag">killed: {s.killed ? "yes" : "no"}</span>
            <span className="tag">breakers tripped: {s.tripped_breakers}</span>
          </div>
        ) : loading ? (
          <Empty>Loading…</Empty>
        ) : null}
        <div>
          <button type="button" disabled={loading} onClick={() => void fetchSignals(true)}>
            {loadingSurvival ? "Computing survival…" : "Compute survival (runs git)"}
          </button>
        </div>
        {withSurvival ? (
          !data?.survival.available ? (
            <div>
              Survival unavailable
              {data?.survival.reason ? ` — ${data.survival.reason}` : ""}
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Task</th>
                    <th className="num">Added</th>
                    <th className="num">Survived</th>
                    <th className="num">Survival</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(data.survival.total ? [data.survival.total] : []), ...data.survival.tasks].map(
                    (row, index) => (
                      <tr key={`${row.task_id ?? "run"}-${index}`}>
                        <td className="mono">{row.task_id ?? "(run total)"}</td>
                        <td className="num">{row.lines_added}</td>
                        <td className="num">{row.lines_survived}</td>
                        <td className="num">{formatPercent(row.survival_rate)}</td>
                        <td>
                          {row.unavailable ? `unavailable: ${row.unavailable}` : null}
                          {row.flags.map((flag) => (
                            <span key={flag} className="tag" style={{ marginLeft: 4 }}>
                              ⚠ {flag}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </div>
    </div>
  );
}
