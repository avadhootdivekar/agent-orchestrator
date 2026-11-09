import { useState } from "react";
import { formatCost, formatDuration, formatTimestamp } from "../format";
import type { SentinelPanelV2, SentinelStatus } from "../types";
import { useSentinelPanel } from "../useSentinelPanel";
import { useSentinel } from "../useSentinel";
import { CollapsibleSection } from "./CollapsibleSection";
import { ErrorBanner } from "./common";

/**
 * Sentinel panel v1 (E-Sn7tL2 T-Sn5Ui1, FR-7/FR-20/FR-22): live-control state of a run -- enable
 * state, concurrency limit (live override), slot utilisation, running tasks with liveness age and
 * Kill, recovery-ladder state, LLM lane (generation / spend / pause reason), operator notes and
 * recent actions. Every mutation is one `POST /control`; the engine's tick applies it, so the
 * panel then shows the entry's verdict instead of assuming success.
 *
 * Hidden for an old backend (no endpoint); collapsed by default while the sentinel is off.
 */
export const NOT_LIVE_TIP = "Controls apply only while the run is running.";
const NOTE_MAX = 4000;

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Busy / idle-with-ready / idle-no-ready slot-seconds as one stacked bar; text carries numbers. */
export function SlotBar({ status }: { status: SentinelStatus }) {
  const u = status.utilisation;
  const busy = u.slot_seconds_busy ?? 0;
  const idleReady = u.slot_seconds_idle_with_ready ?? 0;
  const idleNone = u.slot_seconds_idle_no_ready ?? 0;
  const total = busy + idleReady + idleNone;
  const pct = u.pct ?? (total > 0 ? (busy / total) * 100 : null);
  if (pct === null) return <span className="muted">no data yet</span>;
  const seg = (v: number) => (total > 0 ? `${(v / total) * 100}%` : "0%");
  return (
    <span className="usage-bar-wrap" data-testid="sentinel-utilisation">
      <span
        className="slot-bar"
        role="progressbar"
        aria-label="Slot utilisation"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-valuetext={`${pct.toFixed(1)}% busy`}
      >
        <span className="slot-busy" style={{ width: seg(busy) }} />
        <span className="slot-idle-ready" style={{ width: seg(idleReady) }} />
        <span className="slot-idle-none" style={{ width: seg(idleNone) }} />
      </span>
      <span className="num">
        {pct.toFixed(1)}% busy · idle with work waiting {formatDuration(idleReady)} · idle, nothing
        ready {formatDuration(idleNone)}
      </span>
    </span>
  );
}

function LimitControl({
  status,
  disabled,
  busy,
  onApply,
}: {
  status: SentinelStatus;
  disabled: boolean;
  busy: boolean;
  onApply: (value: number) => Promise<boolean>;
}) {
  const { limit, ceiling } = status.concurrency;
  const [draft, setDraft] = useState("");
  const value = draft.trim() === "" ? null : Number(draft);
  const invalid =
    value === null ||
    !Number.isInteger(value) ||
    value < 1 ||
    (ceiling !== null && value > ceiling);
  return (
    <div className="row" style={{ gap: 8, alignItems: "center" }}>
      <label>
        New max parallel{" "}
        <input
          type="number"
          min={1}
          max={ceiling ?? undefined}
          value={draft}
          aria-label="New max parallel"
          placeholder={limit === null ? "" : String(limit)}
          disabled={disabled || busy}
          style={{ width: 80 }}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <button
        type="button"
        disabled={disabled || busy || invalid}
        title={disabled ? NOT_LIVE_TIP : undefined}
        onClick={() => {
          if (value !== null) void onApply(value).then((ok) => ok && setDraft(""));
        }}
      >
        Apply
      </button>
      {draft !== "" && invalid ? (
        <span className="muted" role="alert" style={{ fontSize: 12 }}>
          {ceiling !== null ? `Whole number from 1 to ${ceiling}.` : "Whole number, 1 or more."}
        </span>
      ) : null}
    </div>
  );
}

function RunningTasks({
  status,
  disabled,
  busy,
  onKill,
}: {
  status: SentinelStatus;
  disabled: boolean;
  busy: boolean;
  onKill: (taskId: string, reason: string, noRetry: boolean) => Promise<boolean>;
}) {
  const [killing, setKilling] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [noRetry, setNoRetry] = useState(false);
  const rows = status.tasks.running;
  if (rows.length === 0) return <p className="muted">No running tasks.</p>;
  const close = () => {
    setKilling(null);
    setReason("");
    setNoRetry(false);
  };
  return (
    <div className="table-wrap">
      <table data-testid="sentinel-running">
        <thead>
          <tr>
            <th>Task</th>
            <th>Quiet for</th>
            <th>Recovery</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const rec = status.ladder[row.id];
            return (
              <tr key={row.id}>
                <td className="mono">{row.id}</td>
                <td className="num">
                  {row.liveness_age_s === null ? "—" : formatDuration(row.liveness_age_s)}
                </td>
                <td>{rec ? `${rec.kills} kill(s), step ${rec.escalation_step}` : "—"}</td>
                <td>
                  {killing === row.id ? (
                    <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <input
                        aria-label={`Kill reason for ${row.id}`}
                        placeholder="Reason (required)"
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                      />
                      <label>
                        <input
                          type="checkbox"
                          checked={noRetry}
                          onChange={(event) => setNoRetry(event.target.checked)}
                        />{" "}
                        don&apos;t retry
                      </label>
                      <button
                        type="button"
                        className="danger"
                        disabled={busy || reason.trim() === ""}
                        onClick={() => void onKill(row.id, reason.trim(), noRetry).then(close)}
                      >
                        Confirm kill
                      </button>
                      <button type="button" onClick={close}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="danger"
                      disabled={disabled || busy}
                      title={disabled ? NOT_LIVE_TIP : undefined}
                      aria-label={`Kill ${row.id}`}
                      onClick={() => setKilling(row.id)}
                    >
                      Kill
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function NoteBox({
  disabled,
  busy,
  onSend,
}: {
  disabled: boolean;
  busy: boolean;
  onSend: (text: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState("");
  const length = draft.trim().length;
  return (
    <div className="stack" style={{ gap: 6 }}>
      <textarea
        aria-label="Sentinel note"
        rows={2}
        value={draft}
        disabled={disabled || busy}
        placeholder="Note recorded in the sentinel's run memory…"
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className="row" style={{ gap: 8 }}>
        <button
          type="button"
          disabled={disabled || busy || length === 0 || length > NOTE_MAX}
          onClick={() => void onSend(draft.trim()).then((ok) => ok && setDraft(""))}
        >
          Add note
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          {length}/{NOTE_MAX}
        </span>
      </div>
    </div>
  );
}

/** Panel v2 (T-Sn9Pow): generation timeline + spend, proposals (applied / quarantined), directives. */
export function SentinelV2({ panel }: { panel: SentinelPanelV2 }) {
  const { generations, proposals, directives } = panel;
  return (
    <div className="stack" style={{ gap: 14 }} data-testid="sentinel-v2">
      <section aria-label="Generations">
        <h3>Generations</h3>
        {generations.length === 0 ? (
          <p className="muted">No generations yet.</p>
        ) : (
          <>
            <p data-testid="sentinel-gen-spend">
              {generations.length} generation(s) · total spend {formatCost(panel.spend_usd)}
            </p>
            <div className="table-wrap">
              <table data-testid="sentinel-generations">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Started</th>
                    <th>Status</th>
                    <th>Triggers</th>
                    <th>Cost</th>
                    <th>End</th>
                  </tr>
                </thead>
                <tbody>
                  {generations.map((g) => (
                    <tr key={g.task_id}>
                      <td className="num">g{String(g.generation).padStart(2, "0")}</td>
                      <td>{g.started_at ? formatTimestamp(g.started_at) : "—"}</td>
                      <td>
                        {g.status}
                        {g.graceful === false ? " (ungraceful)" : ""}
                      </td>
                      <td>{g.triggers.length ? g.triggers.join(", ") : "—"}</td>
                      <td className="num">{formatCost(g.cost_usd)}</td>
                      <td>{g.ended_reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section aria-label="Proposals">
        <h3>Proposals</h3>
        <p data-testid="sentinel-proposal-counts">
          {proposals.applied.length} applied · {proposals.quarantined.length} quarantined
        </p>
        {proposals.applied.length + proposals.quarantined.length === 0 ? null : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }} data-testid="sentinel-proposals">
            {proposals.applied.map((p) => (
              <li key={`a-${p.id}`} data-state="applied">
                <span className="mono">
                  {p.actor} · {p.kind} · {p.id}
                </span>{" "}
                — applied
              </li>
            ))}
            {proposals.quarantined.map((p) => (
              <li key={`q-${p.id}`} data-state="quarantined">
                <span className="mono">
                  {p.actor} · {p.kind} · {p.id}
                </span>{" "}
                — quarantined by <span className="mono">{p.rule_id ?? "unknown rule"}</span>
                {p.detail ? `: ${p.detail}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Directives">
        <h3>Directives</h3>
        {directives.length === 0 ? (
          <p className="muted">None raised.</p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }} data-testid="sentinel-directives">
            {directives.map((d) => (
              <li key={d.id} data-severity={d.severity}>
                <span className="mono">
                  [{d.severity}] {d.actor} · {d.id}
                </span>
                <div style={{ whiteSpace: "pre-wrap" }}>{d.text}</div>
                <div className={d.ack ? "muted" : undefined} data-testid={`directive-ack-${d.id}`}>
                  {d.ack
                    ? `acknowledged by ${d.ack.checkpoint}: ${d.ack.response}${d.ack.rationale ? ` — ${d.ack.rationale}` : ""}`
                    : "awaiting checkpoint acknowledgement"}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export function SentinelPanel({ runId, live }: { runId: string; live: boolean }) {
  const { status, error, submitting, entry, submit } = useSentinel(runId);
  const { panel } = useSentinelPanel(runId);
  if (status === null) return null; // old backend (404) or not a status document
  const s = status.sentinel;
  const llm = status.llm;
  const disabled = !live || !s.enabled;
  const conc = status.concurrency;
  const overrides = Object.entries(conc.overrides);
  const orphanLadder = Object.entries(status.ladder).filter(
    ([id]) => !status.tasks.running.some((t) => t.id === id),
  );

  return (
    <CollapsibleSection
      id="sentinel"
      title="Sentinel"
      testId="section-sentinel"
      defaultOpen={s.enabled}
    >
      <ErrorBanner message={error} />
      {!s.enabled ? (
        <p className="muted" data-testid="sentinel-off">
          Sentinel is off for this run
          {s.disabled_reason ? ` (${s.disabled_reason})` : ""}. Enable it at launch to get live
          concurrency control, stuck-task recovery and the LLM lane.
        </p>
      ) : (
        <div className="stack" style={{ gap: 14 }} data-testid="sentinel-body">
          {s.systemic_alarm ? (
            <div className="banner error" role="alert" data-testid="sentinel-alarm">
              Systemic alarm: repeated automatic kills — automatic recovery has stopped and the LLM
              lane is paused. Investigate before resuming.
            </div>
          ) : null}
          {entry ? (
            <div
              className={entry.result.outcome === "rejected" ? "banner error" : "banner info"}
              role="status"
              data-testid="sentinel-entry"
            >
              {entry.result.outcome === "pending"
                ? `${entry.kind}: queued — the engine applies it on its next tick…`
                : entry.result.outcome === "consumed"
                  ? `${entry.kind}: applied.`
                  : `${entry.kind}: rejected by the engine [${entry.result.rule_id}] ${entry.result.detail}`}
            </div>
          ) : null}

          <dl className="kv">
            <Stat label="State">
              enabled
              {s.last_tick_at ? ` · last tick ${formatTimestamp(s.last_tick_at)}` : ""}
              {s.hold_requested ? " · hold requested" : ""}
            </Stat>
            <Stat label="Run">{status.run_status}</Stat>
            <Stat label="Inbox">
              {status.inbox.pending.length} pending · {status.inbox.consumed_count} applied
            </Stat>
          </dl>

          <section aria-label="Concurrency">
            <h3>Concurrency</h3>
            <p data-testid="sentinel-limit">
              Running {conc.running} · limit {conc.limit ?? "—"} · ceiling {conc.ceiling ?? "—"}
            </p>
            <LimitControl
              status={status}
              disabled={disabled}
              busy={submitting}
              onApply={(value) =>
                submit({ kind: "set", payload: { values: { max_parallel: value } } })
              }
            />
            {overrides.length > 0 ? (
              <ul className="muted" style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
                {overrides.map(([key, o]) => (
                  <li key={key}>
                    {key} = {String(o.value)} (live override by {o.actor || "unknown"},{" "}
                    {formatTimestamp(o.at)})
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section aria-label="Utilisation">
            <h3>Slot utilisation</h3>
            <SlotBar status={status} />
          </section>

          <section aria-label="Running tasks">
            <h3>Running tasks</h3>
            <RunningTasks
              status={status}
              disabled={disabled}
              busy={submitting}
              onKill={(taskId, reason, noRetry) =>
                submit({
                  kind: "kill",
                  payload: { task_id: taskId, no_retry: noRetry },
                  rationale: reason,
                })
              }
            />
            {orphanLadder.length > 0 ? (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }} data-testid="sentinel-ladder">
                {orphanLadder.map(([id, rec]) => (
                  <li key={id}>
                    <span className="mono">{id}</span>: {rec.kills} kill(s), last{" "}
                    {rec.last_reason ?? "—"} ({rec.last_source ?? "—"}), step {rec.escalation_step}
                    {rec.no_retry ? ", no retry" : ""}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section aria-label="LLM lane">
            <h3>LLM lane</h3>
            <dl className="kv" data-testid="sentinel-llm">
              <Stat label="Lane">
                {llm.enabled === false ? "off" : llm.paused ? "paused" : "active"}
                {llm.powers ? ` · powers: ${llm.powers}` : ""}
              </Stat>
              <Stat label="Generation">{llm.generation ?? "—"}</Stat>
              <Stat label="Spend">{llm.spend_usd === null ? "—" : formatCost(llm.spend_usd)}</Stat>
              {llm.paused && llm.pause_reason ? (
                <Stat label="Pause reason">{llm.pause_reason}</Stat>
              ) : null}
              {llm.budget_exhausted ? <Stat label="Budget">exhausted</Stat> : null}
            </dl>
            <button
              type="button"
              disabled={disabled || submitting}
              title={disabled ? NOT_LIVE_TIP : undefined}
              onClick={() => void submit({ kind: llm.paused ? "resume_llm" : "pause_llm" })}
            >
              {llm.paused ? "Resume LLM" : "Pause LLM"}
            </button>
          </section>

          {panel ? <SentinelV2 panel={panel} /> : null}

          <section aria-label="Notes">
            <h3>Note</h3>
            <NoteBox disabled={disabled} busy={submitting} onSend={(text) => submit({ kind: "note", payload: { text } })} />
          </section>

          <section aria-label="Recent actions">
            <h3>Recent actions</h3>
            {status.recent_actions.length === 0 ? (
              <p className="muted">None yet.</p>
            ) : (
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }} data-testid="sentinel-actions">
                {[...status.recent_actions].reverse().map((a) => (
                  <li key={a.id}>
                    <span className="mono">
                      {formatTimestamp(a.at)} · {a.actor} · {a.kind}
                    </span>
                    <div style={{ whiteSpace: "pre-wrap" }}>{a.text}</div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </CollapsibleSection>
  );
}
