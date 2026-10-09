/**
 * Pinned task detail panel (`T-pAi0Cv`, HLD §8.7, ADR-0017 D6).
 *
 * `<aside role="complementary">` -- a 360px right-hand panel at >= `RESPONSIVE_BREAKPOINT_PX`
 * viewport width, a bottom sheet under it (TASK.md item 2). Renders `model.ts::panelModel`'s
 * pure data shape section-by-section, per the HLD §8.7 field table. Every related-task link
 * calls the caller's `onNavigate` (`RunGraph.tsx`'s shared `useSelectAndCenter`), so the same
 * "jump to a node" behavior the toolbar search already uses now also drives the panel.
 *
 * Text-only (D-5): every id/label below is plain JSX text interpolation, never raw/unescaped
 * HTML injection -- ids/labels can be agent-authored.
 */
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { StatusChip } from "../components/common";
import {
  formatCost,
  formatCount,
  formatDuration,
  formatPercent,
  formatTimestamp,
} from "../format";
import type { RunGraph as RunGraphData, TaskStat } from "../types";
import { PathLink, PathText } from "../components/PathText";
import { OpenInNewTabButton } from "../tabs/TabLink";
import { PANEL_PENDING_STATUS, panelModel } from "./model";

export interface TaskDetailPanelProps {
  /** Node id to show, or `null` to render nothing. */
  nodeId: string | null;
  graph: RunGraphData;
  statsById: Map<string, TaskStat>;
  onClose: () => void;
  /** Called with a related-task id -- the caller re-points the panel and recenters the canvas. */
  onNavigate: (id: string) => void;
  /** Run id, so the panel can offer "open in new tab" / link output files (E-iafh2F). */
  runId?: string;
}

/** Below this viewport width the panel becomes a bottom sheet instead of a 360px side panel. */
const RESPONSIVE_BREAKPOINT_PX = 720;
/** Spawn children shown before the "show all (N)" toggle (HLD §8.7 Spawn row). */
const CHILDREN_PREVIEW_COUNT = 20;
/** How long the artifact-path copy button shows "Copied" before reverting. */
const COPY_FEEDBACK_MS = 1500;

/** Tracks the `RESPONSIVE_BREAKPOINT_PX` crossing so the panel can switch its own layout class. */
function useIsNarrowViewport(breakpointPx: number): boolean {
  const [isNarrow, setIsNarrow] = useState(() => window.innerWidth < breakpointPx);
  useEffect(() => {
    const handleResize = () => setIsNarrow(window.innerWidth < breakpointPx);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [breakpointPx]);
  return isNarrow;
}

/**
 * Display label for a related id: the node's own (already server-sanitized) label when the
 * graph carries it, else the raw id -- still literal text only (D-5), e.g. a dependency target
 * this graph payload never materialized as a node.
 */
function labelFor(graph: RunGraphData, id: string): string {
  return graph.nodes.find((n) => n.id === id)?.label ?? id;
}

function statusFor(statsById: Map<string, TaskStat>, id: string): string {
  return statsById.get(id)?.status ?? PANEL_PENDING_STATUS;
}

function RelatedLink({
  id,
  graph,
  statsById,
  onNavigate,
}: {
  id: string;
  graph: RunGraphData;
  statsById: Map<string, TaskStat>;
  onNavigate: (id: string) => void;
}) {
  return (
    <li>
      <button type="button" className="link" onClick={() => onNavigate(id)}>
        {labelFor(graph, id)}
      </button>
      <StatusChip status={statusFor(statsById, id)} />
    </li>
  );
}

function RelatedLinkList({
  ids,
  graph,
  statsById,
  onNavigate,
  emptyText,
}: {
  ids: string[];
  graph: RunGraphData;
  statsById: Map<string, TaskStat>;
  onNavigate: (id: string) => void;
  emptyText: string;
}) {
  if (ids.length === 0) return <p className="muted">{emptyText}</p>;
  return (
    <ul className="task-detail-link-list">
      {ids.map((id) => (
        <RelatedLink key={id} id={id} graph={graph} statsById={statsById} onNavigate={onNavigate} />
      ))}
    </ul>
  );
}

/** Spawn children list with the AC-5 preview cap -- the only section that needs its own state. */
function ChildrenList({
  ids,
  graph,
  statsById,
  onNavigate,
}: {
  ids: string[];
  graph: RunGraphData;
  statsById: Map<string, TaskStat>;
  onNavigate: (id: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  if (ids.length === 0) return <p className="muted">No children.</p>;
  const visible = showAll ? ids : ids.slice(0, CHILDREN_PREVIEW_COUNT);
  return (
    <>
      <ul className="task-detail-link-list">
        {visible.map((id) => (
          <RelatedLink key={id} id={id} graph={graph} statsById={statsById} onNavigate={onNavigate} />
        ))}
      </ul>
      {!showAll && ids.length > CHILDREN_PREVIEW_COUNT ? (
        <button type="button" className="link" onClick={() => setShowAll(true)}>
          show all ({ids.length})
        </button>
      ) : null}
    </>
  );
}

/** Copy-to-clipboard button for the Outcome section's output artifact path. Best-effort: a
 * missing/denied Clipboard API (jsdom, insecure context, permissions) is a silent no-op. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    if (!navigator.clipboard) return;
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), COPY_FEEDBACK_MS);
      })
      .catch(() => {
        /* clipboard write denied/unsupported -- nothing else to do */
      });
  }

  return (
    <button type="button" className="task-detail-copy" onClick={handleCopy}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

export function TaskDetailPanel({
  nodeId,
  graph,
  statsById,
  onClose,
  onNavigate,
  runId,
}: TaskDetailPanelProps) {
  const isNarrow = useIsNarrowViewport(RESPONSIVE_BREAKPOINT_PX);
  const asideRef = useRef<HTMLElement | null>(null);

  // Focus moves into the panel on open (TASK.md item 2) -- the aside itself is the target since
  // its content is a mix of headings/dl's/buttons, not one obvious first focusable control.
  useEffect(() => {
    if (nodeId) asideRef.current?.focus({ preventScroll: true });
  }, [nodeId]);

  if (!nodeId) return null;
  const panel = panelModel(nodeId, graph, statsById);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  }

  return (
    <aside
      ref={asideRef}
      role="complementary"
      aria-label="Task details"
      tabIndex={-1}
      className={`task-detail-panel ${
        isNarrow ? "task-detail-panel--sheet" : "task-detail-panel--side"
      }`}
      onKeyDown={handleKeyDown}
    >
      <div className="task-detail-panel-head">
        {runId ? (
          <OpenInNewTabButton
            target={{ kind: "task", params: { run: runId, id: nodeId } }}
            label={`task ${panel.header.label}`}
          />
        ) : null}
        <button type="button" className="task-detail-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>

      <header>
        {panel.header.missing ? (
          <p className="task-detail-missing-note" role="note">
            Unknown task (referenced by a dependency but never defined)
          </p>
        ) : null}
        <h2 className="task-detail-label">{panel.header.label}</h2>
        <div className="row">
          {panel.header.sanitized ? <span className="tag">hidden characters removed</span> : null}
          <StatusChip status={panel.header.status} />
          <span className="tag">{panel.header.origin}</span>
          {panel.header.route ? <span className="tag">route: {panel.header.route}</span> : null}
        </div>
      </header>

      <section aria-label="Timing">
        <h3>Timing</h3>
        {panel.hasStat ? (
          <dl>
            <div>
              <dt>Started</dt>
              <dd>{formatTimestamp(panel.timing.started)}</dd>
            </div>
            <div>
              <dt>Ended</dt>
              <dd>{formatTimestamp(panel.timing.ended)}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(panel.timing.duration)}</dd>
            </div>
            <div>
              <dt>Wait before start</dt>
              <dd>{formatDuration(panel.timing.wait)}</dd>
            </div>
            {panel.timing.ordinal !== null ? (
              <div>
                <dt>Order</dt>
                <dd>started #{panel.timing.ordinal} (latest dispatch)</dd>
              </div>
            ) : null}
          </dl>
        ) : (
          <p className="muted">Stats pending</p>
        )}
      </section>

      <section aria-label="Usage">
        <h3>Usage</h3>
        {panel.hasStat ? (
          <dl>
            <div>
              <dt>Cost</dt>
              <dd>{formatCost(panel.usage.cost)}</dd>
            </div>
            <div>
              <dt>Input tokens</dt>
              <dd>{formatCount(panel.usage.inputTokens)}</dd>
            </div>
            <div>
              <dt>Output tokens</dt>
              <dd>{formatCount(panel.usage.outputTokens)}</dd>
            </div>
            <div>
              <dt>Cache hit rate</dt>
              <dd>{formatPercent(panel.usage.cacheHitRate)}</dd>
            </div>
            <div>
              <dt>Cache read tokens</dt>
              <dd>{formatCount(panel.usage.cacheReadTokens)}</dd>
            </div>
            <div>
              <dt>Cache creation tokens</dt>
              <dd>{formatCount(panel.usage.cacheCreationTokens)}</dd>
            </div>
          </dl>
        ) : (
          <p className="muted">Stats pending</p>
        )}
      </section>

      <section aria-label="Retries">
        <h3>Retries</h3>
        {panel.hasStat ? (
          <dl>
            <div>
              <dt>Attempts</dt>
              <dd>{panel.retries.attempts}</dd>
            </div>
            <div>
              <dt>Retries</dt>
              <dd>{panel.retries.retries}</dd>
            </div>
            <div>
              <dt>Dispatches</dt>
              <dd>{panel.retries.dispatches}</dd>
            </div>
          </dl>
        ) : (
          <p className="muted">Stats pending</p>
        )}
      </section>

      <section aria-label="Spawn">
        <h3>Spawn</h3>
        <p>
          parent:{" "}
          {panel.spawn.parent ? (
            <button type="button" className="link" onClick={() => onNavigate(panel.spawn.parent!)}>
              {labelFor(graph, panel.spawn.parent)}
            </button>
          ) : (
            "none"
          )}
        </p>
        {panel.spawn.loopId ? (
          <p>
            loop: {panel.spawn.loopId} · iteration {panel.spawn.iteration}
          </p>
        ) : null}
        <h4>Children</h4>
        <ChildrenList
          ids={panel.spawn.children}
          graph={graph}
          statsById={statsById}
          onNavigate={onNavigate}
        />
      </section>

      <section aria-label="Dependencies">
        <h3>Dependencies</h3>
        <h4>Depends on</h4>
        <RelatedLinkList
          ids={panel.deps.dependsOn}
          graph={graph}
          statsById={statsById}
          onNavigate={onNavigate}
          emptyText="None."
        />
        <h4>Dependents</h4>
        <RelatedLinkList
          ids={panel.deps.dependents}
          graph={graph}
          statsById={statsById}
          onNavigate={onNavigate}
          emptyText="None."
        />
      </section>

      <section aria-label="Outcome">
        <h3>Outcome</h3>
        {panel.hasStat ? (
          <>
            {panel.outcome.notTakenReason ? (
              <p>
                Not taken: <PathText text={panel.outcome.notTakenReason} />
              </p>
            ) : null}
            {panel.outcome.integrationStatus ? (
              <p>
                Integration: {panel.outcome.integrationStatus}
                {panel.outcome.tierReached ? ` · tier ${panel.outcome.tierReached}` : ""}
                {panel.outcome.conflictedCount ? ` · ${panel.outcome.conflictedCount} conflict(s)` : ""}
              </p>
            ) : null}
            {panel.outcome.outputArtifactPath ? (
              <p className="mono row">
                <PathLink path={panel.outcome.outputArtifactPath} />
                <CopyButton text={panel.outcome.outputArtifactPath} />
              </p>
            ) : null}
            {panel.outcome.outputs.length > 0 ? (
              <ul className="task-detail-outputs">
                {panel.outcome.outputs.map((path) => (
                  <li key={path} className="mono">
                    <PathLink path={path} />
                  </li>
                ))}
              </ul>
            ) : null}
            {!panel.outcome.notTakenReason &&
            !panel.outcome.integrationStatus &&
            !panel.outcome.outputArtifactPath &&
            panel.outcome.outputs.length === 0 ? (
              <p className="muted">No outcome data.</p>
            ) : null}
          </>
        ) : (
          <p className="muted">Stats pending</p>
        )}
      </section>
    </aside>
  );
}

export default TaskDetailPanel;
