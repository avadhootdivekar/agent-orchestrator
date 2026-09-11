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
