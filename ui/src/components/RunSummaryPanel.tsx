import { formatCost, formatTimestamp } from "../format";
import type { RunLiveSummary } from "../types";
import { CollapsibleSection } from "./CollapsibleSection";
import { PathText } from "./PathText";

/**
 * Live digest of an expensive run (written by the engine once cost crosses its fixed
 * threshold; refreshed as tasks settle and once at the end). Renders nothing for runs that
 * never crossed the threshold.
 *
 * The text is model-generated from run data, so like the prompt panel it is shown ONLY as a
 * React text child in a `pre-wrap` block -- no markdown/HTML interpretation.
 */
export function RunSummaryPanel({
  summary,
}: {
  summary: RunLiveSummary | null;
}) {
  if (!summary || !summary.available) return null;
  const { meta } = summary;
  return (
    <CollapsibleSection
      id="live-summary"
      className="run-summary-panel"
      testId="run-summary"
      title={
        <>
          Run summary
          {meta ? (
            <span className="muted" style={{ marginLeft: 8, fontWeight: 400 }}>
              {meta.final ? "final" : "live"} · updated{" "}
              {formatTimestamp(meta.updated_at)}
            </span>
          ) : null}
        </>
      }
    >
      <div style={{ whiteSpace: "pre-wrap" }}>
        <PathText text={summary.text} />
      </div>
      {meta ? (
        <div className="muted" style={{ marginTop: 8 }}>
          Summarised by {meta.model} · {meta.calls} calls ·{" "}
          {formatCost(meta.summary_cost_usd)} of {formatCost(meta.run_cost_usd)}{" "}
          run cost
        </div>
      ) : null}
    </CollapsibleSection>
  );
}
