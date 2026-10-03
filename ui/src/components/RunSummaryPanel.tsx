import { formatCost, formatTimestamp } from "../format";
import type { RunLiveSummary } from "../types";

/**
 * Live digest of an expensive run (written by the engine once cost crosses its fixed
 * threshold; refreshed as tasks settle and once at the end). Renders nothing for runs that
 * never crossed the threshold.
 *
 * The text is model-generated from run data, so like the prompt panel it is shown ONLY as a
 * React text child in a `pre-wrap` block -- no markdown/HTML interpretation.
 */
export function RunSummaryPanel({ summary }: { summary: RunLiveSummary | null }) {
  if (!summary || !summary.available) return null;
  const { meta } = summary;
  return (
    <details className="card run-summary-panel" open data-testid="run-summary">
      <summary>
        <h2 style={{ display: "inline", margin: 0 }}>Run summary</h2>
        {meta ? (
          <span className="muted" style={{ marginLeft: 8 }}>
            {meta.final ? "final" : "live"} · updated {formatTimestamp(meta.updated_at)}
          </span>
        ) : null}
      </summary>
      <div style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{summary.text}</div>
      {meta ? (
        <div className="muted" style={{ marginTop: 8 }}>
          Summarised by {meta.model} · {meta.calls} calls · {formatCost(meta.summary_cost_usd)} of{" "}
          {formatCost(meta.run_cost_usd)} run cost
        </div>
      ) : null}
    </details>
  );
}
