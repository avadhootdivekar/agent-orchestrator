import { useCallback, useState } from "react";
import { ApiError, api } from "../api";
import { errorMessage } from "../errors";
import { formatCost, formatRelative, formatTimestamp } from "../format";
import { TabLink } from "../tabs/TabLink";
import type { PausedRun } from "../types";
import { usePolling } from "../usePolling";
import { ErrorBanner } from "./common";

/** `reason` of a refused resume (409/422 body) -> the text shown inline (T-8knqVe AC5). */
function refusalText(err: unknown): string {
  if (err instanceof ApiError && typeof err.extra?.reason === "string") {
    return `refused (${err.extra.reason}): ${err.message}`;
  }
  return errorMessage(err);
}

function PausedRow({
  id,
  run,
  now,
  onDone,
}: {
  id: string;
  run: PausedRun;
  now: number;
  onDone: () => void;
}) {
  const [amount, setAmount] = useState(String(run.default_add_usd));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);

  const value = Number(amount);
  const amountOk =
    amount.trim() !== "" &&
    Number.isFinite(value) &&
    value > 0 &&
    value <= run.max_add_usd;
  const disabledReason = !run.resumable
    ? (run.refusal ?? "not resumable")
    : !amountOk
      ? `amount must be > 0 and ≤ ${formatCost(run.max_add_usd)}`
      : null;

  const resume = async () => {
    setBusy(true);
    setRefused(null);
    try {
      const r = await api.resumeCapped(id, run.run_id, value);
      setNote(
        `queued: +${formatCost(r.add_usd)}, new cap ${formatCost(r.new_cap_usd)} — the daemon resumes it within one evaluation interval`,
      );
      onDone();
    } catch (err) {
      setRefused(refusalText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li>
      <TabLink
        target={{ kind: "run", params: { id: run.run_id } }}
        className="link"
      >
        {run.run_id}
      </TabLink>{" "}
      <span>
        spent {formatCost(run.spent_usd)} / cap {formatCost(run.cap_usd)}
      </span>
      <span className="muted" title={run.pause_expires_at ?? undefined}>
        {" "}
        · pause expires {formatRelative(run.pause_expires_at, now)} (
        {formatTimestamp(run.pause_expires_at)})
      </span>{" "}
      <label>
        add $
        <input
          type="number"
          min="0"
          step="0.01"
          value={amount}
          aria-label={`Amount to add for ${run.run_id}`}
          onChange={(e) => setAmount(e.target.value)}
          disabled={!run.resumable}
        />
      </label>{" "}
      <button
        type="button"
        onClick={() => void resume()}
        disabled={busy || disabledReason !== null}
        title={disabledReason ?? undefined}
      >
        Resume
      </button>
      {disabledReason ? (
        <span className="muted"> ({disabledReason})</span>
      ) : null}
      {refused ? (
        <div className="error" role="alert">
          {refused}
        </div>
      ) : null}
      {note ? <div className="muted">{note}</div> : null}
    </li>
  );
}

/** Cost-cap-paused runs of one schedule with a bounded Resume (T-8knqVe AC5). */
export function PausedRuns({
  id,
  pollMs,
  clock = Date.now,
}: {
  id: string;
  pollMs: number;
  clock?: () => number;
}) {
  const [runs, setRuns] = useState<PausedRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRuns((await api.schedulePausedRuns(id)).paused ?? []);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [id]);
  usePolling(load, pollMs);

  if (runs === null || runs.length === 0) {
    return error ? <ErrorBanner message={error} /> : null;
  }
  const now = clock();
  return (
    <div className="stack">
      <ErrorBanner message={error} />
      <strong>Paused by cost cap</strong>
      <ul className="schedule-usage" aria-label="Paused runs">
        {runs.map((r) => (
          <PausedRow
            key={r.run_id}
            id={id}
            run={r}
            now={now}
            onDone={() => void load()}
          />
        ))}
      </ul>
    </div>
  );
}
