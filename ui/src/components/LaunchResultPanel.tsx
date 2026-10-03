import { useEffect, useState } from "react";
import { api } from "../api";
import {
  isWaiting,
  LAUNCH_MAX_POLLS,
  LAUNCH_POLL_MS,
  launchStatusOf,
} from "../launch";
import { useTabActions, useTabActive } from "../tabs/context";
import type { LaunchRecord } from "../types";

/** Shown while the launch POST is in flight (before any record exists). */
export function LaunchPending() {
  return (
    <div className="banner info launch-panel" role="status">
      <strong>Starting…</strong> Launching the engine process.
    </div>
  );
}

/**
 * The visible outcome of a launch (E-iafh2F launch-status; HLD §3). It NEVER navigates by
 * itself: every route out is an explicit button, because the old behaviour — jump to the runs
 * list as soon as the POST returned — hid an engine that died within two seconds.
 *
 *  - started              -> "Run started": Open run / Open in new tab / Start another
 *  - starting|unconfirmed -> "Starting… run id not yet visible" while polling the launch record
 *                            (bounded); then "Not confirmed yet": Open run list / Keep waiting
 *  - failed_to_start      -> alert with exit code, workflow, log tail, Edit & retry / Copy log /
 *                            Open run list
 *
 * Log text is rendered as a React text child (never HTML); the server also strips control
 * characters and bounds it.
 */
export function LaunchResultPanel({
  initial,
  onOpenRun,
  onOpenRunList,
  onStartAnother,
  onEditRetry,
  onRecord,
  retryHint,
  pollMs = LAUNCH_POLL_MS,
  maxPolls = LAUNCH_MAX_POLLS,
}: {
  initial: LaunchRecord;
  onOpenRun: (runId: string) => void;
  onOpenRunList: () => void;
  onStartAnother: () => void;
  onEditRetry: () => void;
  /** Called with every refreshed record so the owner can persist/clear what it remembers. */
  onRecord?: (record: LaunchRecord) => void;
  /** Extra guidance on the failure view (e.g. the template instance already exists). */
  retryHint?: string;
  pollMs?: number;
  maxPolls?: number;
}) {
  const actions = useTabActions();
  const tabActive = useTabActive();
  const [record, setRecord] = useState(initial);
  const [polls, setPolls] = useState(0);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">(
    "idle",
  );

  const status = launchStatusOf(record);
  const waiting = isWaiting(status);
  const expired = waiting && polls >= maxPolls;

  // Bounded poll of the launch record. One timeout per poll, re-armed by the poll counter, so
  // it stops by itself at `maxPolls` and while this workspace tab is hidden.
  useEffect(() => {
    if (!waiting || expired || !tabActive) return undefined;
    const timer = setTimeout(() => {
      api
        .launch(record.launch_id)
        .then((next) => {
          setRecord(next);
          onRecord?.(next);
        })
        .catch(() => {
          /* keep the last known state; the counter still advances so this stays bounded */
        })
        .finally(() => setPolls((count) => count + 1));
    }, pollMs);
    return () => clearTimeout(timer);
    // `onRecord` is an owner callback; re-arming the timer on its identity would defeat polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting, expired, tabActive, polls, record.launch_id, pollMs]);

  // A failure record without a log tail (older response shape) fetches it once.
  const needsLog =
    status === "failed_to_start" && record.log_tail === undefined;
  useEffect(() => {
    if (!needsLog) return;
    api
      .launch(record.launch_id)
      .then(setRecord)
      .catch(() => setRecord((prev) => ({ ...prev, log_tail: "" })));
  }, [needsLog, record.launch_id]);

  if (status === "started" && record.run_id) {
    const runId = record.run_id;
    return (
      <div className="banner info launch-panel" role="status">
        <h2>Run started</h2>
        <p>
          Run <code className="mono">{runId}</code> is running as a separate{" "}
          <code className="mono">ao run</code> process.
        </p>
        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={() => onOpenRun(runId)}
          >
            Open run
          </button>
          {actions.available ? (
            <button
              type="button"
              onClick={() =>
                actions.open(
                  { kind: "run", params: { id: runId } },
                  { activate: true },
                )
              }
            >
              Open in new tab
            </button>
          ) : null}
          <button type="button" onClick={onStartAnother}>
            Start another
          </button>
        </div>
      </div>
    );
  }

  if (status === "failed_to_start") {
    const log = record.log_tail ?? "";
    const copy = () => {
      navigator.clipboard
        .writeText(log)
        .then(() => setCopyState("copied"))
        .catch(() => setCopyState("failed"));
    };
    return (
      <div className="banner error launch-panel" role="alert">
        <h2>Failed to start</h2>
        <p>
          The engine process exited{" "}
          {record.exit_code === null
            ? "(exit code unknown)"
            : `with exit code ${record.exit_code}`}{" "}
          before creating a run, so no run exists.
        </p>
        <dl className="launch-facts">
          <dt>Exit code</dt>
          <dd className="mono">{record.exit_code ?? "unknown"}</dd>
          <dt>Workflow</dt>
          <dd className="mono">
            {record.workflow_path ?? "(from project config)"}
          </dd>
        </dl>
        {record.log_truncated ? (
          <div className="field-hint">Earlier output omitted.</div>
        ) : null}
        <pre
          className="launch-log"
          tabIndex={0}
          aria-label="Launch log (last lines)"
        >
          {record.log_tail === undefined
            ? "Loading log…"
            : log || "(the process printed nothing)"}
        </pre>
        {retryHint ? <p className="field-hint">{retryHint}</p> : null}
        <div className="row">
          <button type="button" className="primary" onClick={onEditRetry}>
            Edit &amp; retry
          </button>
          <button type="button" onClick={copy} disabled={!log}>
            Copy log
          </button>
          <button type="button" onClick={onOpenRunList}>
            Open run list
          </button>
          <span role="status" className="muted" style={{ fontSize: 12 }}>
            {copyState === "copied"
              ? "Log copied"
              : copyState === "failed"
                ? "Copy failed"
                : ""}
          </span>
        </div>
      </div>
    );
  }

  if (expired) {
    return (
      <div className="banner info launch-panel" role="status">
        <h2>Not confirmed yet — process still running</h2>
        <p>
          The process is alive but has not created a run directory. It may still
          be starting (large workspace, slow checkout); nothing has failed.
        </p>
        <div className="row">
          <button type="button" onClick={onOpenRunList}>
            Open run list
          </button>
          <button type="button" className="primary" onClick={() => setPolls(0)}>
            Keep waiting
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="banner info launch-panel" role="status">
      <strong>Starting… run id not yet visible</strong>
      <div className="field-hint">
        The process is alive; waiting for the run to appear.
      </div>
    </div>
  );
}
