import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { errorMessage } from "./errors";
import type { SentinelControlOutcome, SentinelControlRequest, SentinelStatus } from "./types";
import { SENTINEL_STATUS_SCHEMA } from "./types";
import { POLL_MS, usePolling } from "./usePolling";

/** How a queued entry's verdict is polled: the engine consumes it on its next tick (~15 s). */
export const OUTCOME_POLL_MS = 1500;
export const OUTCOME_MAX_POLLS = 40;

export interface PendingEntry {
  id: string;
  kind: string;
  /** `pending` until the engine consumed (`consumed`) or quarantined (`rejected`) the entry. */
  result: SentinelControlOutcome;
}

/**
 * Live sentinel status of a run plus a `submit` that queues one control entry and then follows its
 * verdict. `status` stays `null` for an old backend (404) or a non-status body, so the panel simply
 * hides; transport errors are surfaced via `error` and keep the last good status.
 */
export function useSentinel(runId: string) {
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [entry, setEntry] = useState<PendingEntry | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.sentinel(runId);
      if (next?.schema !== SENTINEL_STATUS_SCHEMA) {
        setStatus(null);
        return;
      }
      setStatus((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setStatus(null);
        setError(null);
      } else setError(errorMessage(err));
    }
  }, [runId]);
  usePolling(load, POLL_MS);

  // Follow the newest entry's verdict; stop at the first non-pending answer.
  const entryId = entry?.result.outcome === "pending" ? entry.id : null;
  const polls = useRef(0);
  useEffect(() => {
    if (entryId === null) return undefined;
    polls.current = 0;
    const timer = setInterval(() => {
      polls.current += 1;
      void api
        .sentinelControlOutcome(runId, entryId)
        .then((result) => {
          if (result.outcome !== "pending") {
            setEntry((cur) => (cur && cur.id === entryId ? { ...cur, result } : cur));
            void load();
          }
        })
        .catch(() => undefined); // transient: the next poll retries
      if (polls.current >= OUTCOME_MAX_POLLS) clearInterval(timer);
    }, OUTCOME_POLL_MS);
    return () => clearInterval(timer);
  }, [runId, entryId, load]);

  /** Resolves true when the entry was queued (so the caller may clear its form). */
  const submit = useCallback(
    async (body: SentinelControlRequest) => {
      setSubmitting(true);
      setError(null);
      try {
        const queued = await api.sentinelControl(runId, body);
        setEntry({ id: queued.id, kind: queued.kind, result: { outcome: "pending" } });
        void load();
        return true;
      } catch (err) {
        setError(errorMessage(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [runId, load],
  );

  return { status, error, submitting, entry, submit };
}
