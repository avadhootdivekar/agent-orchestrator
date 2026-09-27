/**
 * Run graph canvas core (`T-OjTS8O`, HLD §8.6, ADR-0017 D5/D7).
 *
 * `React Flow` + `dagre` (via `./layout`), wired to the pure view-model in `./model`. This file
 * owns: the fetch/refetch lifecycle (gated on `graphVersion`, D-3), the view toggle (dependency /
 * spawn, U-5), async layout with stale-result discarding, and edge styling from the HLD §8.6
 * table. It does NOT own the toolbar extras, legend, degraded banners, hover card, or detail
 * panel — those are `T-aHktGB`/`T-pAi0Cv`. `onNodeClick`/`onNodeMouseEnter` are exposed as props
 * so those tasks can hook node interaction without editing this file's internals.
 *
 * Loaded via `React.lazy` from `RunDetail.tsx`, so `@xyflow/react`/`@dagrejs/dagre` (and this
 * file's own CSS, including the `@xyflow/react/dist/base.css` import below) only download when
 * the Graph tab is opened (NFR-4).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useOnViewportChange,
  useReactFlow,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { api, ApiError } from "../api";
import { Empty, ErrorBanner } from "../components/common";
import type {
  DependencyEdge,
  GraphView,
  RunGraph as RunGraphData,
  SpawnEdge,
  TaskStat,
  ViewEdge,
} from "../types";
import { computeLayout } from "./layout";
import {
  EDGE_LABEL_MIN_ZOOM,
  edgesForView,
  joinNodes,
  LARGE_GRAPH_NODES,
  nodesForView,
  NODE_HEIGHT,
  NODE_WIDTH,
  readPrefs,
  writePrefs,
  type GraphPrefs,
} from "./model";
import {
  accessibleNodeName,
  statusToken,
  TaskNode,
  type TaskNodeData,
  type TaskNodeType,
} from "./TaskNode";

export interface RunGraphProps {
  runId: string;
  tasks: TaskStat[];
  graphVersion: string;
  /** Hook for `T-pAi0Cv`'s detail panel — this file never opens a panel itself. */
  onNodeClick?: (nodeId: string) => void;
  /** Hook for `T-pAi0Cv`'s hover card — this file never renders one itself. */
  onNodeMouseEnter?: (nodeId: string) => void;
}

// ---- Constants (NFR-5) ---------------------------------------------------------------------

/** `<ReactFlow>` zoom bounds (HLD §8.6 item 1, TASK.md item 1). */
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 2;
/** Cap on a truncated `via` artifact path shown in an inferred-dependency edge label. */
const EDGE_LABEL_PATH_MAX_CHARS = 24;
/**
 * Layout only needs node ids/edges, never live stats -- passing this fixed empty array into
 * `joinNodes` keeps the layout effect's inputs independent of the `tasks` prop entirely (AC-5).
 */
const NO_TASKS: TaskStat[] = [];

/** Stable across renders — a fresh object here would make React Flow re-diff node types. */
const NODE_TYPES = { task: TaskNode };

const VIEW_OPTIONS: { value: GraphView; label: string }[] = [
  { value: "dependency", label: "Execution order" },
  { value: "spawn", label: "Spawned by" },
];

/**
 * View toggle (AC-1, D-3/U-5): `role="radiogroup"` with roving tabindex and arrow-key support,
 * per the ARIA APG radiogroup pattern. Selection is reported to the parent, which persists it.
 */
function ViewToggle({
  value,
  onChange,
}: {
  value: GraphView;
  onChange: (next: GraphView) => void;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const forward = event.key === "ArrowRight" || event.key === "ArrowDown";
    const backward = event.key === "ArrowLeft" || event.key === "ArrowUp";
    if (!forward && !backward) return;
    event.preventDefault();
    const index = VIEW_OPTIONS.findIndex((option) => option.value === value);
    const delta = forward ? 1 : -1;
    const next = VIEW_OPTIONS[(index + delta + VIEW_OPTIONS.length) % VIEW_OPTIONS.length];
    onChange(next.value);
  };

  return (
    <div
      className="graph-view-toggle"
      role="radiogroup"
      aria-label="Graph view"
      onKeyDown={handleKeyDown}
    >
      {VIEW_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className="graph-view-toggle-option"
          tabIndex={option.value === value ? 0 : -1}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** Truncates a long artifact path to its tail (the filename end is the informative part). */
function truncatePath(path: string): string {
  if (path.length <= EDGE_LABEL_PATH_MAX_CHARS) return path;
  return `…${path.slice(-(EDGE_LABEL_PATH_MAX_CHARS - 1))}`;
}

/**
 * CSS class suffix keyed on `set`/`kind`/`origin` (HLD §8.6 edge table). An open-set spawn
 * `origin` outside `injected`/`loop` (e.g. the fixture's `"manual"`) falls back to a neutral
 * style rather than throwing — the same defensive stance as the node's unknown-origin badge.
 */
function edgeVisualKind(edge: ViewEdge): string {
  if (edge.set === "dependency") return `dependency-${(edge as DependencyEdge).kind}`;
  const origin = (edge as SpawnEdge).origin;
  if (origin === "loop") return "spawn-loop";
  if (origin === "injected") return "spawn-injected";
  return "spawn-other";
}

/** On-path text per the HLD §8.6 table's Label column; `null` rows show nothing. */
function edgeLabelText(edge: ViewEdge): string | null {
  if (edge.set === "dependency") {
    const dep = edge as DependencyEdge;
    if (dep.kind === "loop" && dep.via) return `loop ${dep.via}`;
    if (dep.kind === "inferred" && dep.via) return truncatePath(dep.via);
    return null;
  }
  const spawn = edge as SpawnEdge;
  if (spawn.origin === "loop" && spawn.iteration !== null) return `iter ${spawn.iteration}`;
  return null;
}

/** `ViewEdge[]` -> React Flow `Edge[]`. Labels are gated on `showLabels` (zoom >= threshold). */
function toRfEdges(edges: ViewEdge[], showLabels: boolean): RFEdge[] {
  return edges.map((edge) => {
    const kind = edgeVisualKind(edge);
    const label = showLabels ? edgeLabelText(edge) : null;
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      className: `graph-edge graph-edge-${kind}`,
      label: label ?? undefined,
      markerEnd: { type: MarkerType.ArrowClosed },
    };
  });
}

/** The actual canvas — split from `RunGraph` because `useReactFlow`/`useOnViewportChange` need a `ReactFlowProvider` ancestor. */
function RunGraphCanvas({
  runId,
  tasks,
  graphVersion,
  onNodeClick,
  onNodeMouseEnter,
}: RunGraphProps) {
  const [prefs, setPrefs] = useState<GraphPrefs>(() => readPrefs());
  const [graph, setGraph] = useState<RunGraphData | null>(null);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [layout, setLayout] = useState<Awaited<ReturnType<typeof computeLayout>> | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  // Edge labels hide below EDGE_LABEL_MIN_ZOOM; the default view starts at zoom 1 (>= 0.6).
  const [showEdgeLabels, setShowEdgeLabels] = useState(true);

  const lastFetchedVersionRef = useRef<string | null>(null);
  const layoutRequestRef = useRef(0);
  const recenterPendingRef = useRef(false);
  const reactFlow = useReactFlow();

  // ---- Fetch / refetch, gated on graphVersion only (D-3, AC-5) ----------------------------
  useEffect(() => {
    if (!graphVersion || graphVersion === lastFetchedVersionRef.current) return;
    let cancelled = false;
    api
      .runGraph(runId)
      .then((data) => {
        if (cancelled) return;
        lastFetchedVersionRef.current = data.graph_version;
        setGraph(data);
        setGraphError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Keep the last good graph on a fetch failure (HLD §8.5 refetch rule) -- only the
        // error banner changes, the canvas never blanks out from a transient poll failure.
        setGraphError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [runId, graphVersion]);

  // ---- View-model (cheap; recomputes on every `tasks` change, but never triggers layout) --
  const viewNodes = useMemo(() => (graph ? joinNodes(graph, tasks) : []), [graph, tasks]);
  const edges = useMemo(() => (graph ? edgesForView(graph, prefs.view) : []), [graph, prefs.view]);
  const visible = useMemo(
    () => nodesForView(viewNodes, edges, prefs.view, prefs.showUnrelated).visible,
    [viewNodes, edges, prefs.view, prefs.showUnrelated],
  );

  // ---- Layout: depends on graph/view/showUnrelated ONLY, never on `tasks` (AC-5) ----------
  useEffect(() => {
    if (!graph) return;
    const requestId = ++layoutRequestRef.current;
    const structuralEdges = edgesForView(graph, prefs.view);
    const structuralNodes = nodesForView(
      joinNodes(graph, NO_TASKS),
      structuralEdges,
      prefs.view,
      prefs.showUnrelated,
    ).visible;
    computeLayout(structuralNodes, structuralEdges, prefs.view)
      .then((result) => {
        // A stale request (view/graph changed again while this one was in flight) is dropped.
        if (layoutRequestRef.current === requestId) setLayout(result);
      })
      .catch(() => {
        /* computeLayout has no documented failure mode today; a defensive no-op keeps a
           future async layout engine (ADR-0017 D5) from surfacing an unhandled rejection. */
      });
  }, [graph, prefs.view, prefs.showUnrelated]);

  // ---- Keep the selection centered across a view toggle (Description item 2) --------------
  useEffect(() => {
    if (!recenterPendingRef.current || !layout) return;
    recenterPendingRef.current = false;
    if (!selectedNodeId) return;
    const position = layout.positions.get(selectedNodeId);
    if (!position) return;
    void reactFlow.setCenter(position.x + NODE_WIDTH / 2, position.y + NODE_HEIGHT / 2, {
      zoom: reactFlow.getZoom(),
    });
  }, [layout, selectedNodeId, reactFlow]);

  useOnViewportChange({
    onChange: (viewport) => {
      setShowEdgeLabels((prev) => {
        const next = viewport.zoom >= EDGE_LABEL_MIN_ZOOM;
        return prev === next ? prev : next;
      });
    },
  });

  const handleViewChange = useCallback((nextView: GraphView) => {
    recenterPendingRef.current = true;
    setPrefs((current) => {
      const next = { ...current, view: nextView };
      writePrefs(next);
      return next;
    });
  }, []);

  const handleNodeClick: NodeMouseHandler<TaskNodeType> = useCallback(
    (_event, node) => {
      setSelectedNodeId(node.id);
      onNodeClick?.(node.id);
    },
    [onNodeClick],
  );

  const handleNodeMouseEnter: NodeMouseHandler<TaskNodeType> = useCallback(
    (_event, node) => {
      onNodeMouseEnter?.(node.id);
    },
    [onNodeMouseEnter],
  );

  const rfNodes = useMemo<RFNode<TaskNodeData, "task">[]>(() => {
    if (!layout) return [];
    return visible.map((node) => {
      const position = layout.positions.get(node.id) ?? { x: 0, y: 0 };
      return {
        id: node.id,
        type: "task",
        position,
        data: { node, direction: layout.direction },
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        selected: node.id === selectedNodeId,
        // D-7/AC-4: React Flow renders THIS wrapper (not TaskNode's own root div) as the
        // focusable `role="group"` element, so the accessible name goes here.
        ariaLabel: accessibleNodeName(node),
      };
    });
  }, [visible, layout, selectedNodeId]);

  const rfEdges = useMemo(() => toRfEdges(edges, showEdgeLabels), [edges, showEdgeLabels]);

  return (
    <div className="graph-root">
      <ErrorBanner message={graphError} />
      <div className="graph-toolbar-row">
        <ViewToggle value={prefs.view} onChange={handleViewChange} />
      </div>
      <div className="graph-canvas-wrap">
        {graph && layout ? (
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={NODE_TYPES}
            onNodeClick={handleNodeClick}
            onNodeMouseEnter={handleNodeMouseEnter}
            fitView
            minZoom={MIN_ZOOM}
            maxZoom={MAX_ZOOM}
            nodesDraggable
            nodesConnectable={false}
            elementsSelectable
            onlyRenderVisibleElements={rfNodes.length > LARGE_GRAPH_NODES}
            defaultMarkerColor="var(--text-secondary)"
          >
            <MiniMap pannable zoomable nodeColor={statusToken} />
            <Controls showInteractive={false} />
            <Background variant={BackgroundVariant.Dots} />
          </ReactFlow>
        ) : (
          <Empty>Loading graph…</Empty>
        )}
      </div>
    </div>
  );
}

/** Public entry point — wraps the canvas in the `ReactFlowProvider` its hooks require. */
export function RunGraph(props: RunGraphProps) {
  return (
    <ReactFlowProvider>
      <RunGraphCanvas {...props} />
    </ReactFlowProvider>
  );
}

export default RunGraph;
