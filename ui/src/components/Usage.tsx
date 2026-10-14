import { useEffect, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import {
  formatCost,
  formatCountOrNA,
  formatFeedbackSplit,
  formatPercent,
} from "../format";
import type { RunSummary, UsageGroup, UsageReport } from "../types";
import { Empty, ErrorBanner } from "./common";

function groupKey(g: UsageGroup): string {
  return `${g.agent}|${g.model}|${g.effort}`;
}

/** Flags carry their meaning in text (a warning glyph + the flag name), never color alone. */
function Flags({ flags }: { flags: string[] }) {
  if (flags.length === 0) return <span className="muted">—</span>;
  return (
    <span className="row" style={{ gap: 4, flexWrap: "wrap" }}>
      {flags.map((flag) => (
        <span key={flag} className="tag">
          ⚠ {flag}
        </span>
      ))}
    </span>
  );
}

function GroupTable({ groups }: { groups: UsageGroup[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Agent</th>
            <th>Model</th>
            <th>Effort</th>
            <th className="num">Tasks</th>
            <th className="num">OK</th>
            <th className="num">Retry %</th>
            <th className="num">$/task</th>
            <th className="num">Reviewed</th>
            <th className="num">Fail %</th>
            <th className="num">Feedback g/o/b</th>
            <th className="num">Unnecessary</th>
            <th className="num">Reviewer disagreements</th>
            <th className="num">Survival</th>
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={groupKey(g)}>
              <td className="mono">{g.agent}</td>
              <td className="mono">{g.model}</td>
              <td>{g.effort}</td>
              <td className="num">{g.tasks}</td>
              <td className="num">{g.succeeded}</td>
              <td className="num">{formatPercent(g.retry_rate)}</td>
              <td className="num">{formatCost(g.mean_cost_usd)}</td>
              <td className="num">{g.reviewed}</td>
              <td className="num">{formatPercent(g.review_fail_rate)}</td>
              <td className="num">{formatFeedbackSplit(g)}</td>
              <td className="num">{formatCountOrNA(g.fb_unnecessary, g.fb_rated_tasks)}</td>
              <td
                className="num"
                title="Reviewer PASS but user rated bad / reviewer FAIL but user rated good"
              >
                {g.verdict_rated_pairs > 0
                  ? `${g.false_pass_candidates + g.false_fail_candidates}/${g.verdict_rated_pairs}`
                  : "n/a"}
              </td>
              <td className="num">{formatPercent(g.survival_rate)}</td>
              <td>
                <Flags flags={g.flags} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function survivalStatus(report: UsageReport, requested: boolean): string {
  if (!requested) return "survival: not requested";
  if (report.survival_available) {
    return `survival: computed${report.survival_ref ? ` (ref ${report.survival_ref})` : ""}`;
  }
  return `survival: unavailable${
    report.survival_unavailable_reason ? ` — ${report.survival_unavailable_reason}` : ""
  }`;
}

/** Outcome-vs-charter: overseer checkpoint/final-verify verdicts per run, when any exist. */
function Outcomes({ report }: { report: UsageReport }) {
  if (report.outcomes.length === 0) return null;
  return (
    <div>
      <h2>Outcome vs charter</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th className="num">Checkpoints</th>
              <th>Last decision</th>
              <th className="num">Criteria met / unmet / deferred</th>
              <th>Final verify (met / partial / not met)</th>
            </tr>
          </thead>
          <tbody>
            {report.outcomes.map((o) => (
              <tr key={o.run_id}>
                <td className="mono">{o.run_id}</td>
                <td className="num">
                  {o.checkpoints_found}/{o.checkpoints_seen}
                </td>
                <td>{o.last_decision ?? "n/a"}</td>
                <td className="num">
                  {o.criteria_met} / {o.criteria_unmet} / {o.criteria_deferred}
                </td>
                <td>
                  {o.final_verify
                    ? `${o.final_verify.met} / ${o.final_verify.partial} / ${o.final_verify.not_met}`
                    : "n/a"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Usage view: what each (agent, model, effort) group cost and how it was judged. */
export function Usage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string[] | null>(null); // null = all runs
  const [survival, setSurvival] = useState(false);
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .runs()
      .then(setRuns)
      .catch((err) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    if (selected !== null && selected.length === 0) return; // nothing to ask for
    let cancelled = false;
    setLoading(true);
    api
      .usage(selected ?? [], survival)
      .then((next) => {
        if (cancelled) return;
        setReport(next);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, survival]);

  const toggleRun = (id: string) =>
    setSelected((current) => {
      const base = current ?? runs.map((r) => r.run_id);
      const next = base.includes(id) ? base.filter((r) => r !== id) : [...base, id];
      return next.length === runs.length ? null : next;
    });

  const isChecked = (id: string) => selected === null || selected.includes(id);
  const noneSelected = selected !== null && selected.length === 0;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Usage</h1>
      </div>

      <ErrorBanner message={error} />

      <div className="card stack" style={{ gap: 8 }}>
        <strong>Runs</strong>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <button type="button" onClick={() => setSelected(null)} disabled={selected === null}>
            All runs
          </button>
          {runs.map((run) => (
            <label key={run.run_id} className="row" style={{ gap: 4, margin: 0 }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={isChecked(run.run_id)}
                onChange={() => toggleRun(run.run_id)}
              />
              <span className="mono">{run.run_id}</span>
            </label>
          ))}
        </div>
        <label className="row" style={{ gap: 6, margin: 0 }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={survival}
            onChange={(event) => setSurvival(event.target.checked)}
          />
          include survival (runs git)
        </label>
      </div>

      {noneSelected ? (
        <Empty>No runs selected — pick at least one, or choose All runs.</Empty>
      ) : loading && !report ? (
        <Empty>Loading…</Empty>
      ) : report ? (
        <>
          {loading ? (
            <div className="muted" role="status" style={{ fontSize: 12 }}>
              Updating…
            </div>
          ) : null}
          <div className="muted" style={{ fontSize: 12 }}>
            verdicts found {report.verdicts_found}/{report.reviews_seen} · feedback:{" "}
            {report.runs_rated} of {report.runs_scanned} runs rated · {survivalStatus(report, survival)}
            {report.feedback_errors > 0 ? ` · ${report.feedback_errors} unreadable feedback file(s)` : ""}
          </div>
          {report.skipped.length > 0 ? (
            <div className="banner info">
              Skipped {report.skipped.length} run(s): {report.skipped.join("; ")}
            </div>
          ) : null}
          {report.groups.length === 0 ? (
            <div className="card">
              <Empty>No task usage found for the selected runs.</Empty>
            </div>
          ) : (
            <GroupTable groups={report.groups} />
          )}
          <Outcomes report={report} />
        </>
      ) : null}
    </div>
  );
}
