/** Shapes returned by the dashboard API (see src/agent_orchestrator/ui/app.py). */

export interface DirEntry {
  name: string;
  path: string;
  root: string;
  is_dir: boolean;
  size: number;
  modified: number;
  is_symlink: boolean;
  hidden: boolean;
}

export interface DirListing {
  root: string;
  path: string;
  absolute: string;
  entries: DirEntry[];
}

export interface FileContent {
  path: string;
  root: string;
  size: number;
  is_binary: boolean;
  truncated: boolean;
  text: string | null;
  /** See ui/htmlpreview + files.py classification. */
  kind: "text" | "image" | "markup" | "binary";
  /** Allowlisted MIME, set only when kind === "image". */
  mime: string | null;
  /** `data:<mime>;base64,...`, set only when kind === "image" and size is within the inline cap. */
  data_uri: string | null;
}

/** One asset/href the HTML sanitizer declined to inline (see `ui/htmlpreview.py`). */
export interface DroppedRef {
  /** Original ref, already truncated by the server for display safety. */
  url: string;
  reason:
    | "external"
    | "outside-root"
    | "not-found"
    | "too-large"
    | "budget-exhausted"
    | "unsupported-scheme"
    | "depth-exceeded";
}

/** Response of `GET /api/files/html` — sanitized, self-contained HTML for iframe srcdoc. */
export interface HtmlPreview {
  path: string;
  root: string;
  html: string;
  inlined: number;
  dropped: DroppedRef[];
  scripts_removed: number;
  truncated: boolean;
  budget_bytes: number;
  budget_used: number;
}

export interface WorkflowInfo {
  id: string;
  name: string;
  path: string;
  task_count: number;
  prompt_path: string | null;
  general_instructions: string[];
  error: string | null;
}

export interface RunSummary {
  run_id: string;
  workflow_id: string;
  status: string;
  started_at: string;
  updated_at: string;
  task_count: number;
  task_counts: Record<string, number>;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  wall_seconds: number;
  active_seconds: number;
  is_terminal: boolean;
  is_live: boolean;
  launch_id: string | null;
}

export interface TaskStat {
  id: string;
  status: string;
  attempts: number;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  origin: string;
  route: string | null;
  output_artifact_path: string | null;
  outputs: string[];
  /** Per-task git isolation (E-Wk9Tz3). Null/0 for a task that was never isolated. */
  integration_status: string | null;
  tier_reached: string | null;
  conflicted_count: number;
  /**
   * Prompt-cache effectiveness (E-1cecSx B4, design doc §4) — additive on this existing
   * per-task payload, surfaced on-demand in the dashboard (never a default table column).
   * `cache_hit_rate` is `null` for the zero-denominator case (no input tokens recorded yet
   * at all), distinct from a genuine 0.0 rate — see `reporting.py::cache_effectiveness`.
   */
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cache_hit_rate: number | null;
  /**
   * Run graph additions (E-k3AMEr, HLD §14.2). `dispatch_cycle` is the latest dispatch
   * attempt number; `not_taken_reason` explains a `not_taken` router-skip status.
   */
  dispatch_cycle: number;
  not_taken_reason: string | null;
}

/** Run-wide integration header (E-Wk9Tz3). Null for a run without isolation. */
export interface RunIntegration {
  active: boolean;
  branch: string | null;
  heads: Record<string, string>;
  tier_counts: Record<string, number>;
  degraded_reason: string | null;
}

export interface LaunchRecord {
  launch_id: string;
  kind: string;
  pid: number;
  argv: string[];
  started_at: string;
  log_path: string;
  run_id: string | null;
  workflow_path: string | null;
  prompt_chars: number;
  finished_at: string | null;
  exit_code: number | null;
  cancelled: boolean;
}

export interface RunDetail {
  summary: RunSummary;
  tasks: TaskStat[];
  tripped_breakers: Record<string, unknown>[];
  route_decisions: Record<string, string[]>;
  monitor_decisions: Record<string, unknown>[];
  run_dir: string;
  is_live: boolean;
  launch: LaunchRecord | null;
  integration: RunIntegration | null;
  /**
   * Run graph cache-busting token (E-k3AMEr, HLD §14.2 / ADR-0017 D4). `null` only for a
   * backend that predates the run graph feature; the graph tab then hides itself. The
   * client refetches `GET /runs/{id}/graph` only when this value changes between polls.
   */
  graph_version: string | null;
}

/**
 * Run graph topology (`GET /api/runs/{run_id}/graph`, E-k3AMEr).
 *
 * Mirrors `docs-md/run-graph-canvas-hld.md` §14.2 field-for-field — that section is frozen at
 * design time (ADR-0017 D4/D5), so this file is the contract the frontend builds against before
 * the backend (`T-AsQ77e`) exists. `ui/src/test/fixtures/run-graph.json` is the shared example
 * both sides test against.
 */

/** Closed enum (§14.2): how the run's static topology was recovered for this graph. */
export type GraphSource = "snapshot" | "unavailable";

/** Closed enum (§14.2): whether spawn (parent/child) provenance was recorded for this run. */
export type SpawnData = "recorded" | "not_recorded" | "none";

/** Closed enum (§14.2): how a dependency edge was derived — see `dag.iter_dependency_edges`. */
export type EdgeKind = "explicit" | "loop" | "inferred";

export interface GraphNode {
  id: string;
  /** Sanitized display text (ADR-0017 §8.3 `display_text`) — never the join key, use `id` for that. */
  label: string;
  /** True when `label` differs from `id` because invisible/bidi characters were stripped. */
  label_sanitized: boolean;
  /**
   * Open set (ADR-0017 D1): known values are `"static"`, `"injected"`, `"loop"`, but a future
   * origin kind must render generically rather than be rejected — never a closed union.
   */
  origin: string;
  parent_task_id: string | null;
  route: string | null;
  loop_id: string | null;
  iteration: number | null;
  spawn_depth: number | null;
  children_count: number;
  is_emitter: boolean;
  is_router: boolean;
  is_loop_gate: boolean;
  /** Rank by latest dispatch's `started_at`; `null` for a task that never started. */
  exec_ordinal: number | null;
  /** True for a phantom node materialized from an unknown `depends_on` id (E-Grpp0X). */
  missing: boolean;
}

export interface DependencyEdge {
  source: string;
  target: string;
  kind: EdgeKind;
  /** `null` for `"explicit"`; the loop id for `"loop"`; the shared artifact path for `"inferred"`. */
  via: string | null;
}

export interface SpawnEdge {
  source: string;
  target: string;
  /** Open set (ADR-0017 D1), same as `GraphNode.origin` — copied from the same `SpawnRecord`. */
  origin: string;
  loop_id: string | null;
  iteration: number | null;
}

export interface GraphLoop {
  id: string;
  body: string[];
  gate_task_id: string;
  max_iterations: number;
  iterations_materialized: number;
}

export interface GraphRouter {
  id: string;
  router_task_id: string;
  selected: string[];
}

export interface RunGraph {
  schema_version: number;
  run_id: string;
  graph_version: string;
  source: GraphSource;
  spawn_data: SpawnData;
  truncated: boolean;
  warnings: string[];
  nodes: GraphNode[];
  dependency_edges: DependencyEdge[];
  spawn_edges: SpawnEdge[];
  loops: GraphLoop[];
  routers: GraphRouter[];
}

/** Which edge set the canvas currently renders — dependency (LR) or spawn (TB). */
export type GraphView = "dependency" | "spawn";

/** Which per-node metric strip is shown, or none. */
export type MetricMode = "none" | "duration" | "cost";

/**
 * A `GraphNode` joined with its live per-task stat (`ui/src/graph/model.ts::joinNodes`).
 * `stat` is `null` when the task hasn't appeared in `RunDetail.tasks` yet ("stats pending").
 * Components receive `ViewNode`/`ViewEdge`, never the raw `RunGraph` (dev-critic: keeps the
 * canvas reusable by a future editor — ADR-0017 consequences).
 */
export interface ViewNode extends GraphNode {
  stat: TaskStat | null;
}

/** A dependency or spawn edge tagged with a React-Flow-ready `id` and which set it came from. */
export type ViewEdge = (DependencyEdge | SpawnEdge) & { id: string; set: GraphView };

/** Output of `ui/src/graph/layout.ts::computeLayout` — async by contract (ADR-0017 D5 swap seam). */
export interface LayoutResult {
  positions: Map<string, { x: number; y: number }>;
  direction: "LR" | "TB";
}

export interface AggregateStats {
  total_runs: number;
  runs_by_status: Record<string, number>;
  total_tasks: number;
  tasks_by_status: Record<string, number>;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_wall_seconds: number;
  total_active_seconds: number;
}

export interface WorkspaceInfo {
  workspace_root: string;
  config_path: string | null;
  workflow: string | null;
  reposets: string | null;
  agents: string | null;
  general_instructions: string[];
  roots: { name: string; path: string; role: string }[];
}

export interface GeneralInstruction {
  path: string;
  resolved: string;
  exists: boolean;
}

export interface RunOptions {
  model?: string;
  effort?: string;
  max_attempts?: number;
  max_turns?: number;
  max_parallel?: number;
  budget_total?: number;
  self_heal?: boolean;
}

/**
 * Workflow-template dataclasses (see `agent_orchestrator/templates.py`, HLD §2.4),
 * serialized with `dataclasses.asdict` — field names are already snake_case.
 */
export interface TemplateParam {
  name: string;
  description: string;
  required: boolean;
  enum: string[] | null;
  default: string | null;
}

export interface TemplateInfo {
  name: string;
  description: string;
  path: string;
  source: "builtin" | "workspace" | "path";
  params: TemplateParam[];
  required_agents: string[];
  /** Rendered-with-placeholders preview of prompt.md; null when the template has none. */
  prompt_skeleton: string | null;
}

/** Body of `POST /api/templates/{name}/instances` (HLD §2.6). */
export interface CreateInstanceRequest {
  slug_or_id?: string;
  params: Record<string, string>;
  prompt?: string;
  start: boolean;
  options: RunOptions;
}

/** 201 response of `POST /api/templates/{name}/instances` (HLD §2.6). */
export interface CreateInstanceResponse {
  instance_dir: string;
  workflow_path: string;
  workflow: WorkflowInfo;
  launch: LaunchRecord | null;
}
