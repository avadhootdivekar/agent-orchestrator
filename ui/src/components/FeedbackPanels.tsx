import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { MAX_NOTE_CHARS, formatPercent, formatTimestamp } from "../format";
import type {
  FeedbackEntry,
  FeedbackRequest,
  OperatorNote,
  OperatorNotesState,
  RunSignalsResponse,
} from "../types";
import { errorMessage } from "../errors";
import { POLL_MS, usePolling } from "../usePolling";
import { CollapsibleSection } from "./CollapsibleSection";
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
      setError(errorMessage(err));
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
        setError(errorMessage(err));
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
    <CollapsibleSection id="feedback" title="Feedback" defaultOpen={false}>
      <div className="stack" style={{ gap: 12 }}>
        <ErrorBanner message={error} />
        <FeedbackForm label="Rate this run" scope="run" disabled={posting} onSubmit={onSubmit} />
        <div>
          <strong style={{ fontSize: 12 }}>History</strong>
          <FeedbackHistory entries={entries} />
        </div>
      </div>
    </CollapsibleSection>
  );
}

/** Implicit signals + diff survival. Survival runs git, so it is fetched only on request. */
export function SignalsPanel({ runId }: { runId: string }) {
  // Lazy: the body (and its mount-time fetch) only exists once the section has been opened.
  return (
    <CollapsibleSection id="signals" title="Implicit signals" defaultOpen={false} lazy>
      <SignalsBody runId={runId} />
    </CollapsibleSection>
  );
}

function SignalsBody({ runId }: { runId: string }) {
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
      setError(errorMessage(err));
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
    <div className="stack" style={{ gap: 8, fontSize: 12 }}>
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
  );
}

/** Shared load/post state for a run's queued operator notes (live steering). */
export function useOperatorNotes(runId: string, live: boolean) {
  const [state, setState] = useState<OperatorNotesState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [postError, setPostError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.notes(runId);
      // Keep the previous object when nothing changed so a poll tick causes no re-render.
      setState((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
      setLoadError(null);
    } catch (err) {
      // An old backend (404) simply has no notes; anything else is shown.
      if (err instanceof ApiError && err.status === 404) {
        setState(null);
        setLoadError(null);
      } else setLoadError(errorMessage(err));
    }
  }, [runId]);

  // Same cadence as the run page's live inputs, so CLI / other-tab notes appear without a reload.
  // `live` is in the tick identity: usePolling refetches at once when the run goes live/stops
  // (`accepting` is decided server-side from liveness).
  const tick = useCallback(() => load(), [load, live]); // eslint-disable-line react-hooks/exhaustive-deps
  usePolling(tick, POLL_MS);

  /** Resolves true when the note was accepted (so the caller may clear its draft). */
  const post = useCallback(
    async (text: string) => {
      setPosting(true);
      setPostError(null);
      try {
        const res = await api.postNote(runId, { text });
        setState(res.notes);
        return true;
      } catch (err) {
        setPostError(errorMessage(err));
        return false;
      } finally {
        setPosting(false);
      }
    },
    [runId],
  );

  return { state, error: postError ?? loadError, posting, post };
}

/** Notes are React text children, so they are escaped — never parsed as HTML. */
function NotesList({ notes }: { notes: OperatorNote[] }) {
  if (notes.length === 0) return <div className="muted">No notes sent yet.</div>;
  return (
    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }} data-testid="notes-list">
      {[...notes].reverse().map((note) => (
        <li key={note.id}>
          <span className="mono">
            {formatTimestamp(note.ts)} · {note.source}
          </span>
          <div style={{ whiteSpace: "pre-wrap" }} data-testid="operator-note">
            {note.text}
          </div>
        </li>
      ))}
    </ul>
  );
}

/**
 * Free-form guidance for a running workflow. It reaches only tasks dispatched AFTER it is
 * sent — never one already running — and that limitation is always shown (live-steering.md).
 */
export function OperatorNotesBox({
  state,
  posting,
  error,
  onSubmit,
}: {
  state: OperatorNotesState | null;
  posting: boolean;
  error: string | null;
  onSubmit: (text: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState("");
  if (!state && !error) return null; // old backend: no notes API, no box
  const limit = state?.max_chars ?? MAX_NOTE_CHARS;
  const length = draft.trim().length;
  const tooLong = length > limit;
  const accepting = state?.accepting ?? false;
  const submit = async () => {
    if (await onSubmit(draft.trim())) setDraft("");
  };
  return (
    <div className="stack" style={{ gap: 8 }} data-testid="operator-notes">
      <strong style={{ fontSize: 12 }}>Notes for the run</strong>
      <span className="muted" style={{ fontSize: 12 }} data-testid="notes-limitation">
        A note reaches tasks that start after you send it, not tasks already running. It is
        advisory guidance, not a command.
      </span>
      <ErrorBanner message={error} />
      <textarea
        aria-label="Operator note"
        rows={3}
        value={draft}
        disabled={!accepting || posting}
        placeholder={accepting ? "Guidance for upcoming tasks…" : "Notes are accepted only while the run is live."}
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className="row" style={{ gap: 8 }}>
        <button
          type="button"
          disabled={!accepting || posting || length === 0 || tooLong}
          onClick={() => void submit()}
        >
          {posting ? "Sending…" : "Send note"}
        </button>
        <span className="muted" role={tooLong ? "alert" : undefined} style={{ fontSize: 12 }}>
          {length}/{limit}
          {tooLong ? " — too long" : ""}
        </span>
      </div>
      <NotesList notes={state?.notes ?? []} />
    </div>
  );
}
