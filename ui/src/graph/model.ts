/**
 * Run graph domain model — view assembly, search, and preferences.
 *
 * Pure: no React, no React Flow, no fetch (HLD §8.5, ADR-0017 D5). Everything here is
 * unit-testable in vitest without rendering. `layout.ts` is the sibling module that owns
 * `computeLayout` (kept separate so the dagre import stays isolated to one small file).
 *
 * NFR-5: every cap/threshold/size/key below is a named constant. Besides those constants,
 * no numeric literal other than `0` or `1` appears in this module (see T-adVpTj STATUS for
 * the two documented exceptions: halving a named node dimension, and a named ms-per-second
 * conversion, both inherited directly from the HLD's own pseudocode/units).
 */

import type {
  GraphNode,
  GraphView,
  MetricMode,
  RunGraph,
  TaskStat,
  ViewEdge,
  ViewNode,
} from "../types";

// ---- Constants (NFR-5, HLD §8.5) -----------------------------------------------------------

/** Fixed node box size dagre lays out against — matches the React Flow node's own CSS box. */
export const NODE_WIDTH = 184;
export const NODE_HEIGHT = 48;
/** dagre `ranksep`/`nodesep` (px between ranks / between nodes in the same rank). */
export const RANK_SEP = 64;
export const NODE_SEP = 24;
/** Node count above which the canvas offers extra affordances (minimap emphasis, etc). */
export const LARGE_GRAPH_NODES = 300;
/** Below this zoom level, edge labels hide rather than overlap into noise. */
export const EDGE_LABEL_MIN_ZOOM = 0.6;
export const HOVER_OPEN_DELAY_MS = 250;
export const HOVER_CLOSE_DELAY_MS = 150;
/** Search results cap — a run can have thousands of tasks; the palette stays scannable. */
export const SEARCH_MAX_RESULTS = 50;
export const PREFS_STORAGE_KEY = "ao.runGraph.prefs.v1";

/** Unit conversion for `waitSeconds` (`Date.parse` returns epoch milliseconds). */
const MS_PER_SECOND = 1000;

// ---- View assembly (HLD §8.5) --------------------------------------------------------------

/**
 * Attach each node's live `TaskStat`, or `null` when the task hasn't reached
 * `RunDetail.tasks` yet — the UI renders that as "stats pending" rather than blocking on it.
 */
export function joinNodes(graph: RunGraph, tasks: TaskStat[]): ViewNode[] {
  const byId = new Map(tasks.map((t) => [t.id, t] as const));
  return graph.nodes.map((n) => ({ ...n, stat: byId.get(n.id) ?? null }));
}

/** Dependency- or spawn-edge set for the given view, tagged with a React-Flow-ready `id`. */
export function edgesForView(graph: RunGraph, view: GraphView): ViewEdge[] {
  if (view === "dependency") {
    return graph.dependency_edges.map(
      (e): ViewEdge => ({ id: `dep:${e.source}->${e.target}`, ...e, set: "dependency" }),
    );
  }
  return graph.spawn_edges.map(
    (e): ViewEdge => ({ id: `spawn:${e.source}->${e.target}`, ...e, set: "spawn" }),
  );
}

/**
 * Nodes visible for the current view. The dependency view always shows every node (it is the
 * "everything that exists" view). The spawn view hides nodes with no spawn edge unless
 * `showUnrelated` is set, since most runs have many static tasks that neither spawned nor were
 * spawned, and a 100-wide row of disconnected roots would swamp the actual spawn tree.
 */
export function nodesForView(
  nodes: ViewNode[],
  edges: ViewEdge[],
  view: GraphView,
  showUnrelated: boolean,
): { visible: ViewNode[]; hiddenCount: number } {
  if (view === "dependency" || showUnrelated) {
    return { visible: nodes, hiddenCount: 0 };
  }
  const related = new Set<string>();
  for (const e of edges) {
    related.add(e.source);
    related.add(e.target);
  }
  const visible = nodes.filter((n) => related.has(n.id));
  return { visible, hiddenCount: nodes.length - visible.length };
}

// ---- Metric strip (HLD §8.5) ----------------------------------------------------------------

/** Per-metric maxima the caller precomputes once over the visible node set. */
export interface MetricMaxima {
  duration: number;
  cost: number;
}

/**
 * Fraction in `[0, 1]` for a node's metric strip, or `null` when there is nothing to show:
 * metric mode is "none", the task has no stat yet, the stat's value is null (still running, or
 * never recorded), or the maximum over the visible set is non-positive (nothing to compare
 * against).
 */
export function metricFraction(
  stat: TaskStat | null,
  mode: MetricMode,
  maxima: MetricMaxima,
): number | null {
  if (mode === "none" || stat === null) return null;
  const value = mode === "duration" ? stat.duration_seconds : stat.cost_usd;
  const max = maxima[mode];
  if (value === null || max <= 0) return null;
  return Math.min(1, Math.max(0, value / max));
}

// ---- Search (HLD §8.5) -----------------------------------------------------------------------

/**
 * Case-insensitive substring match over node id and label. Stable (input) order, capped at
 * `SEARCH_MAX_RESULTS`. An empty query intentionally returns no results rather than "everything"
 * — the search box is opt-in, not a default listing.
 */
export function searchNodes(nodes: GraphNode[], query: string): string[] {
  if (query.length === 0) return [];
  const needle = query.toLowerCase();
  const results: string[] = [];
  for (const n of nodes) {
    if (n.id.toLowerCase().includes(needle) || n.label.toLowerCase().includes(needle)) {
      results.push(n.id);
      if (results.length >= SEARCH_MAX_RESULTS) break;
    }
  }
  return results;
}

// ---- Detail panel links (HLD §8.5, ADR-0017 D6) ----------------------------------------------

export interface RelatedIds {
  /** Spawn parent, from `spawn_edges` (`null` for a task that was never spawned). */
  parent: string | null;
  /** Spawn children, from `spawn_edges`. */
  children: string[];
  /** Dependency sources this task waits on, from `dependency_edges`. */
  dependsOn: string[];
  /** Dependency targets that wait on this task, from `dependency_edges`. */
  dependents: string[];
}

/**
 * Every id related to `id`, drawn from both edge sets: spawn parent/children from
 * `spawn_edges`, dependency neighbors from `dependency_edges`. Powers the detail panel's
 * navigable links (ADR-0017 D6). Array fields are sorted for a stable, scannable panel.
 */
export function relatedIds(graph: RunGraph, id: string): RelatedIds {
  let parent: string | null = null;
  const children = new Set<string>();
  for (const e of graph.spawn_edges) {
    if (e.target === id) parent = e.source;
    if (e.source === id) children.add(e.target);
  }
  const dependsOn = new Set<string>();
  const dependents = new Set<string>();
  for (const e of graph.dependency_edges) {
    if (e.target === id) dependsOn.add(e.source);
    if (e.source === id) dependents.add(e.target);
  }
  return {
    parent,
    children: Array.from(children).sort(),
    dependsOn: Array.from(dependsOn).sort(),
    dependents: Array.from(dependents).sort(),
  };
}

/**
 * Wait time before a task started: `started_at − max(ended_at of its dependency sources)`.
 * `null` when the task never started, or when any dependency source lacks a stat or an
 * `ended_at` (still running, or its own stat hasn't arrived yet — this must not render as a
 * misleadingly confident number). A task with no dependencies waited `0` seconds. The result
 * is clamped to `≥ 0`: retries can shift `started_at` earlier than a source's `ended_at` from a
 * prior attempt, and a negative wait would misreport that as "waited before the parent finished".
 */
export function waitSeconds(
  id: string,
  graph: RunGraph,
  statsById: Map<string, TaskStat>,
): number | null {
  const stat = statsById.get(id);
  if (!stat || !stat.started_at) return null;
  const started = Date.parse(stat.started_at);
  if (Number.isNaN(started)) return null;

  const sourceIds = graph.dependency_edges.filter((e) => e.target === id).map((e) => e.source);
  if (sourceIds.length === 0) return 0;

  let latestEnded = -Infinity;
  for (const sourceId of sourceIds) {
    const sourceStat = statsById.get(sourceId);
    if (!sourceStat || !sourceStat.ended_at) return null;
    const ended = Date.parse(sourceStat.ended_at);
    if (Number.isNaN(ended)) return null;
    latestEnded = Math.max(latestEnded, ended);
  }
  return Math.max(0, (started - latestEnded) / MS_PER_SECOND);
}

// ---- Detail panel model (T-pAi0Cv, HLD §8.7, ADR-0017 D6) -----------------------------------

/** Status shown when a task has no stat yet. The one shared constant for this -- Gate G3
 * Warning #2 found it independently redeclared in `TaskNode.tsx`/`TaskHoverCard.tsx` too;
 * both now import this instead of keeping their own "mirrored" copy. */
export const PANEL_PENDING_STATUS = "pending";

/** `max(0, attempts - 1)` -- the shared "retries" formula (T-pAi0Cv AC-2, HLD §8.7). Gate G3
 * Warning #2 found `TaskHoverCard.tsx` computing this inline instead of calling `panelModel`;
 * both now use this one function. */
export function retriesFromAttempts(attempts: number | null | undefined): number {
  return Math.max(0, (attempts ?? 0) - 1);
}

export interface PanelHeader {
  label: string;
  sanitized: boolean;
  status: string;
  origin: string;
  route: string | null;
  /** True for a phantom node materialized from an unknown `depends_on` id (`GraphNode.missing`). */
  missing: boolean;
}

export interface PanelTiming {
  started: string | null;
  ended: string | null;
  duration: number | null;
  /** Seconds waited before this task started (`waitSeconds`); `null` when it can't be known yet. */
  wait: number | null;
  ordinal: number | null;
}

export interface PanelUsage {
  cost: number;
  inputTokens: number;
  outputTokens: number;
  cacheHitRate: number | null;
  cacheReadTokens: number;
  cacheCreationTokens: number;
}

export interface PanelRetries {
  attempts: number | null;
  retries: number;
  dispatches: number | null;
}

export interface PanelSpawn {
  parent: string | null;
  /** Every spawned child id, unbounded -- the caller (`TaskDetailPanel`) applies the "show all" cap. */
  children: string[];
  loopId: string | null;
  iteration: number | null;
}

export interface PanelDependencies {
  dependsOn: string[];
  dependents: string[];
}

export interface PanelOutcome {
  notTakenReason: string | null;
  integrationStatus: string | null;
  tierReached: string | null;
  conflictedCount: number | null;
  outputArtifactPath: string | null;
  outputs: string[];
}

export interface PanelModel {
  id: string;
  /** `false` when `stat` is `null` -- the caller renders "Stats pending" for every stat-derived section. */
  hasStat: boolean;
  header: PanelHeader;
  timing: PanelTiming;
  usage: PanelUsage;
  retries: PanelRetries;
  spawn: PanelSpawn;
  deps: PanelDependencies;
  outcome: PanelOutcome;
}

const DEFAULT_PANEL_USAGE: PanelUsage = {
  cost: 0,
  inputTokens: 0,
  outputTokens: 0,
  cacheHitRate: null,
  cacheReadTokens: 0,
  cacheCreationTokens: 0,
};

const DEFAULT_PANEL_OUTCOME: PanelOutcome = {
  notTakenReason: null,
  integrationStatus: null,
  tierReached: null,
  conflictedCount: null,
  outputArtifactPath: null,
  outputs: [],
};

/**
 * Assembles the detail panel's full data shape for one node (HLD §8.7 table, ADR-0017 D6).
 * Pure -- every link field (`spawn.parent`/`children`, `deps.dependsOn`/`dependents`) is a bare
 * node id, exactly as `relatedIds` returns them; resolving an id to a display label/status is
 * the caller's job (`TaskDetailPanel.tsx`), the same division of labor `selectAndCenter` already
 * uses for navigation.
 *
 * `id` is expected to name a node present in `graph.nodes` -- the only way to open the panel is
 * clicking/Entering an actual rendered node. A lookup miss still returns a well-formed (if
 * mostly empty) model rather than throwing, since a pure function must never crash on a bad key.
 */
export function panelModel(
  id: string,
  graph: RunGraph,
  statsById: Map<string, TaskStat>,
): PanelModel {
  const node = graph.nodes.find((n) => n.id === id);
  const stat = statsById.get(id) ?? null;
  const rel = relatedIds(graph, id);
  const hasStat = stat !== null;

  return {
    id,
    hasStat,
    header: {
      label: node?.label ?? id,
      sanitized: node?.label_sanitized ?? false,
      status: stat?.status ?? PANEL_PENDING_STATUS,
      origin: node?.origin ?? "unknown",
      route: node?.route ?? null,
      missing: node?.missing ?? true,
    },
    timing: {
      started: stat?.started_at ?? null,
      ended: stat?.ended_at ?? null,
      duration: stat?.duration_seconds ?? null,
      wait: waitSeconds(id, graph, statsById),
      ordinal: node?.exec_ordinal ?? null,
    },
    usage: stat
      ? {
          cost: stat.cost_usd,
          inputTokens: stat.input_tokens,
          outputTokens: stat.output_tokens,
          cacheHitRate: stat.cache_hit_rate,
          cacheReadTokens: stat.cache_read_tokens,
          cacheCreationTokens: stat.cache_creation_tokens,
        }
      : DEFAULT_PANEL_USAGE,
    retries: {
      attempts: stat?.attempts ?? null,
      retries: retriesFromAttempts(stat?.attempts),
      dispatches: stat?.dispatch_cycle ?? null,
    },
    spawn: {
      parent: rel.parent,
      children: rel.children,
      loopId: node?.loop_id ?? null,
      iteration: node?.iteration ?? null,
    },
    deps: {
      dependsOn: rel.dependsOn,
      dependents: rel.dependents,
    },
    outcome: stat
      ? {
          notTakenReason: stat.not_taken_reason,
          integrationStatus: stat.integration_status,
          tierReached: stat.tier_reached,
          conflictedCount: stat.conflicted_count,
          outputArtifactPath: stat.output_artifact_path,
          outputs: stat.outputs,
        }
      : DEFAULT_PANEL_OUTCOME,
  };
}

// ---- Preferences (HLD §8.5) -------------------------------------------------------------------

/** Which top-level dashboard tab is active. "graph" is the tab this epic adds (FR-8). */
export type GraphTab = "table" | "graph";

export interface GraphPrefs {
  tab: GraphTab;
  view: GraphView;
  metric: MetricMode;
  showUnrelated: boolean;
  /** Hide transitively redundant edges (drawn straight through intermediate nodes). */
  hideRedundantEdges: boolean;
}

export const DEFAULT_GRAPH_PREFS: GraphPrefs = {
  tab: "table",
  view: "dependency",
  metric: "duration",
  showUnrelated: false,
  hideRedundantEdges: true,
};

function isGraphTab(value: unknown): value is GraphTab {
  return value === "table" || value === "graph";
}

function isGraphView(value: unknown): value is GraphView {
  return value === "dependency" || value === "spawn";
}

function isMetricMode(value: unknown): value is MetricMode {
  return value === "none" || value === "duration" || value === "cost";
}

/**
 * Read persisted graph preferences from `localStorage`. Every access is wrapped: a throwing
 * `localStorage` (private browsing, quota, or a hardened browser policy) and invalid/garbage
 * JSON both fall back to `DEFAULT_GRAPH_PREFS`. Each field is validated independently against
 * its enum, so one corrupted field doesn't discard an otherwise-valid preference set.
 */
export function readPrefs(): GraphPrefs {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);
    if (raw === null) return { ...DEFAULT_GRAPH_PREFS };

    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return { ...DEFAULT_GRAPH_PREFS };
    const candidate = parsed as Partial<Record<keyof GraphPrefs, unknown>>;

    return {
      tab: isGraphTab(candidate.tab) ? candidate.tab : DEFAULT_GRAPH_PREFS.tab,
      view: isGraphView(candidate.view) ? candidate.view : DEFAULT_GRAPH_PREFS.view,
      metric: isMetricMode(candidate.metric) ? candidate.metric : DEFAULT_GRAPH_PREFS.metric,
      showUnrelated:
        typeof candidate.showUnrelated === "boolean"
          ? candidate.showUnrelated
          : DEFAULT_GRAPH_PREFS.showUnrelated,
      hideRedundantEdges:
        typeof candidate.hideRedundantEdges === "boolean"
          ? candidate.hideRedundantEdges
          : DEFAULT_GRAPH_PREFS.hideRedundantEdges,
    };
  } catch {
    return { ...DEFAULT_GRAPH_PREFS };
  }
}

/** Best-effort persist; a write failure (quota, private mode) just means the pref won't stick. */
export function writePrefs(prefs: GraphPrefs): void {
  try {
    localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage unavailable — preference silently doesn't persist, nothing else to do */
  }
}
