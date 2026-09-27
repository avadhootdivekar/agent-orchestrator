/**
 * Collapsible legend card for the run graph canvas (`T-aHktGB`, HLD §8.6 "Legend").
 *
 * Purely presentational: edge styles for the CURRENT view (HLD §8.6 edge table), node badges
 * (reusing `TaskNode.tsx`'s own `BADGE_LABELS` -- the same text its `aria-label`s use, so the
 * legend can never drift from what a screen reader announces), status glyphs (reusing the
 * existing `StatusChip`, so status is never conveyed by color alone here either, D-7), and
 * source/degraded notes derived from the graph's own `source`/`spawn_data`/`truncated` fields
 * plus a "spec changed" note found in `warnings[]` (there is no dedicated typed field for that
 * one -- HLD §8.6 "Legend": "source, spawn_data, 'spec changed during run' ... and truncated").
 */
import { useState } from "react";
import { StatusChip } from "../components/common";
import type { GraphSource, GraphView, SpawnData } from "../types";
import { BADGE_LABELS } from "./TaskNode";

export interface LegendProps {
  view: GraphView;
  source: GraphSource;
  spawnData: SpawnData;
  truncated: boolean;
  warnings: string[];
}

interface EdgeLegendRow {
  key: string;
  /** Same class the canvas edge uses (`RunGraph.tsx::edgeVisualKind`) -- one CSS source of truth. */
  className: string;
  label: string;
}

/** HLD §8.6 edge table, dependency-view rows only. */
const DEPENDENCY_EDGE_ROWS: EdgeLegendRow[] = [
  { key: "explicit", className: "graph-edge-dependency-explicit", label: "Explicit — solid line, direct depends_on." },
  {
    key: "inferred",
    className: "graph-edge-dependency-inferred",
    label: "Inferred — dashed line, derived from a shared artifact path.",
  },
  {
    key: "loop",
    className: "graph-edge-dependency-loop",
    label: 'Loop — solid line, loop color, labeled "loop <id>".',
  },
];

/** HLD §8.6 edge table, spawn-view rows only. */
const SPAWN_EDGE_ROWS: EdgeLegendRow[] = [
  {
    key: "injected",
    className: "graph-edge-spawn-injected",
    label: "Injected — solid line, spawn color, one parent to each child.",
  },
  {
    key: "loop",
    className: "graph-edge-spawn-loop",
    label: 'Loop — dotted line, loop color, labeled "iter N".',
  },
];

/**
 * One representative instantiation of each `BADGE_LABELS` entry, paired with the glyph
 * `TaskNode.tsx::badgesFor` renders for it. Reuses the exact same strings the node's own
 * accessible name is built from (never a separately hand-written description).
 */
const NODE_BADGE_ROWS: { glyph: string; text: string }[] = [
  { glyph: "#1", text: BADGE_LABELS.ordinal(1) },
  { glyph: "◆", text: BADGE_LABELS.injected },
  { glyph: "↻2", text: BADGE_LABELS.loopIteration(2) },
  { glyph: "⤴3", text: BADGE_LABELS.emitter(3) },
  { glyph: "⑂", text: BADGE_LABELS.router },
  { glyph: "⚑", text: BADGE_LABELS.missing },
  { glyph: "◇", text: BADGE_LABELS.unknownOrigin("unknown") },
];

/** Every status `format.ts::statusTone`/`statusGlyph` recognize, in a fixed, scannable order. */
const STATUS_LEGEND_ORDER = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "timed_out",
  "cancelled",
  "skipped",
  "not_taken",
];

function sourceNote(source: GraphSource): string {
  return source === "unavailable"
    ? "Static dependencies unavailable — this run predates workflow snapshots, or its snapshot is missing or unreadable."
    : "Static dependencies loaded from the run's workflow snapshot.";
}

/** `null` for `"none"` (no injected/loop tasks at all) -- there is nothing degraded to report. */
function spawnDataNote(spawnData: SpawnData): string | null {
  if (spawnData === "not_recorded") {
    return "Spawn relationships were not recorded for this run (it predates spawn tracking).";
  }
  if (spawnData === "recorded") return "Spawn relationships recorded for this run.";
  return null;
}

/**
 * The backend has no dedicated typed field for "the spec changed mid-run" -- it only ever
 * surfaces as one particular warning string (see the HLD §8.5 `build_run_graph` pseudocode).
 * Surfacing that ACTUAL warning text (rather than a separately hand-written note) keeps this
 * derived note from drifting out of sync with whatever wording the backend uses.
 */
function specChangedNote(warnings: string[]): string | null {
  return warnings.find((warning) => /spec changed/i.test(warning)) ?? null;
}

export function Legend({ view, source, spawnData, truncated, warnings }: LegendProps) {
  const [collapsed, setCollapsed] = useState(false);
  const edgeRows = view === "dependency" ? DEPENDENCY_EDGE_ROWS : SPAWN_EDGE_ROWS;
  const specChanged = specChangedNote(warnings);
  const spawnNote = spawnDataNote(spawnData);

  return (
    <div className="graph-legend">
      <button
        type="button"
        className="graph-legend-toggle"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((prev) => !prev)}
      >
        Legend {collapsed ? "▸" : "▾"}
      </button>
      {collapsed ? null : (
        <div className="graph-legend-body">
          <section>
            <h3>Edges — {view === "dependency" ? "execution order" : "spawned by"}</h3>
            <ul className="graph-legend-list">
              {edgeRows.map((row) => (
                <li key={row.key}>
                  <span className={`legend-edge-swatch ${row.className}`} aria-hidden="true" />
                  {row.label}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3>Node badges</h3>
            <ul className="graph-legend-list">
              {NODE_BADGE_ROWS.map((row) => (
                <li key={row.text}>
                  <span className="tag" aria-hidden="true">
                    {row.glyph}
                  </span>{" "}
                  {row.text}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3>Status</h3>
            <ul className="graph-legend-list graph-legend-status">
              {STATUS_LEGEND_ORDER.map((status) => (
                <li key={status}>
                  <StatusChip status={status} />
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3>Source</h3>
            <ul className="graph-legend-list">
              <li>{sourceNote(source)}</li>
              {spawnNote ? <li>{spawnNote}</li> : null}
              {specChanged ? <li>{specChanged}</li> : null}
              {truncated ? <li>Graph truncated — not every task in this run is shown.</li> : null}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}

export default Legend;
