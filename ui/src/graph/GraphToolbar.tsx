/**
 * Toolbar extras for the run graph canvas (`T-aHktGB`, HLD §8.6 "Toolbar" items 2-5).
 *
 * Placed after `RunGraph.tsx`'s existing view toggle (item 1, `T-OjTS8O`). Purely presentational
 * plus the search box's own local match-cycling state -- every other piece of state (prefs,
 * layout, the actual "jump to a node" behavior) is owned by the caller and threaded in as props,
 * so this component has no knowledge of `localStorage`, React Flow, or the layout engine.
 */
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import type { GraphNode, GraphView, MetricMode } from "../types";
import { searchNodes } from "./model";

export interface GraphToolbarProps {
  view: GraphView;
  showUnrelated: boolean;
  onShowUnrelatedChange: (value: boolean) => void;
  /** Nodes hidden by the unrelated filter in the CURRENT view (always 0 in the dependency view). */
  hiddenCount: number;
  metric: MetricMode;
  onMetricChange: (value: MetricMode) => void;
  /** Full node set for the current view -- `searchNodes` matches on id/label (model.ts). */
  searchableNodes: GraphNode[];
  /** Called with the id of the match under focus, on Enter or an Up/Down cycle. */
  onSearchSelect: (id: string) => void;
  onFit: () => void;
  onReset: () => void;
}

const METRIC_OPTIONS: { value: MetricMode; label: string }[] = [
  { value: "none", label: "None" },
  { value: "duration", label: "Duration" },
  { value: "cost", label: "Cost" },
];

export function GraphToolbar({
  view,
  showUnrelated,
  onShowUnrelatedChange,
  hiddenCount,
  metric,
  onMetricChange,
  searchableNodes,
  onSearchSelect,
  onFit,
  onReset,
}: GraphToolbarProps) {
  const [query, setQuery] = useState("");
  const [matchIndex, setMatchIndex] = useState(0);

  const matches = useMemo(() => searchNodes(searchableNodes, query), [searchableNodes, query]);

  // A fresh query always restarts at the first match (Enter then centers matches[0], per AC-3).
  useEffect(() => {
    setMatchIndex(0);
  }, [query]);

  function selectMatch(index: number) {
    const id = matches[index];
    if (id === undefined) return;
    setMatchIndex(index);
    onSearchSelect(id);
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (matches.length === 0) return;
    if (event.key === "Enter") {
      event.preventDefault();
      selectMatch(matchIndex);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      selectMatch((matchIndex + 1) % matches.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      selectMatch((matchIndex - 1 + matches.length) % matches.length);
    }
  }

  return (
    <div className="graph-toolbar">
      {/* "Show unrelated" only makes sense in the spawn view -- the dependency view never
          hides anything (model.ts::nodesForView). */}
      {view === "spawn" ? (
        <label className="graph-toolbar-checkbox">
          <input
            type="checkbox"
            checked={showUnrelated}
            onChange={(event) => onShowUnrelatedChange(event.target.checked)}
          />
          Show {hiddenCount} unrelated task{hiddenCount === 1 ? "" : "s"}
        </label>
      ) : null}

      <div className="graph-toolbar-field">
        <label htmlFor="graph-metric">Metric</label>
        <select
          id="graph-metric"
          value={metric}
          onChange={(event) => onMetricChange(event.target.value as MetricMode)}
        >
          {METRIC_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="graph-toolbar-field graph-toolbar-search">
        <label htmlFor="graph-search">Search tasks</label>
        <input
          id="graph-search"
          type="text"
          value={query}
          placeholder="id or label…"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleSearchKeyDown}
        />
        {/* An empty query shows no count -- searchNodes itself returns [] for one, so this
            gate also covers the "no matches yet" case without a separate branch. */}
        {query.length > 0 && matches.length > 0 ? (
          <span className="graph-toolbar-search-count" aria-live="polite">
            {matchIndex + 1} of {matches.length}
          </span>
        ) : null}
      </div>

      <button type="button" onClick={onFit}>
        Fit
      </button>
      <button type="button" onClick={onReset}>
        Reset layout
      </button>
    </div>
  );
}

export default GraphToolbar;
