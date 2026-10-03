import { useState } from "react";
import { api, ApiError } from "../api";
import { formatTimestamp } from "../format";
import { dismissLaunch } from "../launch";
import type { LaunchRecord } from "../types";

/**
 * Recent launches whose engine process died before creating a run (E-iafh2F launch-status).
 *
 * Such a launch has no run directory, so it can never appear in the run table or the failure
 * list; this strip is the only place it can surface after the launch panel is gone. Each entry
 * stays until dismissed (remembered in localStorage) or it ages out of the server's 24 h window.
 * Details load the bounded log tail on demand — list rows carry none.
 */
export function FailedLaunches({
  launches,
  onDismiss,
}: {
  launches: LaunchRecord[];
  onDismiss: (remaining: string[]) => void;
}) {
  if (launches.length === 0) return null;
  return (
    <section
      className="banner error failed-launches"
      aria-label="Launches that failed to start"
    >
      <strong>
        {launches.length} launch{launches.length === 1 ? "" : "es"} failed to
        start
      </strong>
      <span className="muted">
        {" "}
        — no run was created, so nothing appears in the table below.
      </span>
      <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
        {launches.map((launch) => (
          <FailedLaunchItem
            key={launch.launch_id}
            launch={launch}
            onDismiss={onDismiss}
          />
        ))}
      </ul>
    </section>
  );
}

function FailedLaunchItem({
  launch,
  onDismiss,
}: {
  launch: LaunchRecord;
  onDismiss: (remaining: string[]) => void;
}) {
  const [log, setLog] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadLog = (open: boolean) => {
    if (!open || log !== null) return;
    api
      .launch(launch.launch_id)
      .then((full) => setLog(full.log_tail || "(the process printed nothing)"))
      .catch((err) =>
        setLoadError(
          (err instanceof ApiError ? err.message : String(err)) ||
            "Could not load the log",
        ),
      );
  };

  return (
    <li>
      <details
        onToggle={(event) =>
          loadLog((event.currentTarget as HTMLDetailsElement).open)
        }
      >
        <summary>
          <span className="mono">
            {launch.workflow_path ?? "(workflow from config)"}
          </span>{" "}
          · exit {launch.exit_code ?? "?"} ·{" "}
          {formatTimestamp(launch.started_at)}
        </summary>
        {loadError ? <div role="alert">{loadError}</div> : null}
        {log !== null ? (
          <pre
            className="launch-log"
            tabIndex={0}
            aria-label="Launch log (last lines)"
          >
            {log}
          </pre>
        ) : loadError ? null : (
          <div className="field-hint">Loading log…</div>
        )}
      </details>
      <button
        type="button"
        onClick={() => onDismiss(dismissLaunch(launch.launch_id))}
        aria-label={`Dismiss failed launch ${launch.launch_id}`}
      >
        Dismiss
      </button>
    </li>
  );
}
