/** Typed client for the dashboard API. */

import type {
  AggregateStats,
  CreateInstanceRequest,
  CreateInstanceResponse,
  DirListing,
  FeedbackRequest,
  FeedbackState,
  FileContent,
  GeneralInstruction,
  HtmlPreview,
  LaunchRecord,
  LaunchStatus,
  RunActivity,
  RunLiveSummary,
  RunDetail,
  RunGraph,
  RunOptions,
  RunSignalsResponse,
  RunSummary,
  TemplateInfo,
  UsageReport,
  WorkflowInfo,
  WorkspaceInfo,
} from "./types";

const BASE = "/api";

/** Error carrying the HTTP status so callers can distinguish 404 from 409. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    // FastAPI puts the human-readable reason in `detail`; fall back to the status
    // text when a proxy or crash returns something that is not our JSON envelope.
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  workspace: () => request<WorkspaceInfo>("/workspace"),
  generalInstructions: () => request<GeneralInstruction[]>("/general-instructions"),

  listDir: (path: string, root?: string) => {
    const params = new URLSearchParams({ path });
    if (root) params.set("root", root);
    return request<DirListing>(`/files?${params}`);
  },

  readFile: (path: string, root?: string) => {
    const params = new URLSearchParams({ path });
    if (root) params.set("root", root);
    return request<FileContent>(`/files/content?${params}`);
  },

  /** Sanitized, self-contained HTML for markup files (`.html`/`.svg`/etc) — see 1A.2. */
  readFileHtml: (path: string, root?: string) => {
    const params = new URLSearchParams({ path });
    if (root) params.set("root", root);
    return request<HtmlPreview>(`/files/html?${params}`);
  },

  workflows: () => request<WorkflowInfo[]>("/workflows"),

  templates: () => request<TemplateInfo[]>("/templates"),
  createInstance: (name: string, body: CreateInstanceRequest) =>
    request<CreateInstanceResponse>(`/templates/${encodeURIComponent(name)}/instances`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  runs: () => request<RunSummary[]>("/runs"),
  runStats: () => request<AggregateStats>("/runs/stats"),
  run: (runId: string) => request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  /** Run graph topology (E-k3AMEr, HLD §14.2). Fetch on mount and on `graph_version` change. */
  runGraph: (runId: string) => request<RunGraph>(`/runs/${encodeURIComponent(runId)}/graph`),
  /** Live per-task activity (turns, tokens, last action, stuck hint). 404 on an old backend. */
  runActivity: (runId: string) =>
    request<RunActivity>(`/runs/${encodeURIComponent(runId)}/activity`),
  /** Engine-written digest of an expensive run; `available: false` below the cost threshold. */
  runSummary: (runId: string) =>
    request<RunLiveSummary>(`/runs/${encodeURIComponent(runId)}/summary`),
  runLog: (runId: string) =>
    request<{ run_id: string; launch_id: string | null; text: string }>(
      `/runs/${encodeURIComponent(runId)}/log`,
    ),

  /** One launch with its bounded log tail (404 for an unknown or malformed id). */
  launch: (launchId: string) => request<LaunchRecord>(`/launches/${encodeURIComponent(launchId)}`),

  /** Launch records (no log tails), optionally filtered by derived status / recency. */
  launches: (filter: { status?: LaunchStatus; sinceHours?: number } = {}) => {
    const params = new URLSearchParams();
    if (filter.status) params.set("status", filter.status);
    if (filter.sinceHours !== undefined) params.set("since_hours", String(filter.sinceHours));
    const query = params.toString();
    return request<LaunchRecord[]>(`/launches${query ? `?${query}` : ""}`);
  },

  startRun: (workflowPath: string, prompt: string, options: RunOptions) =>
    request<LaunchRecord>("/runs", {
      method: "POST",
      body: JSON.stringify({ workflow_path: workflowPath, prompt, options }),
    }),

  resumeRun: (runId: string, options: RunOptions = {}) =>
    request<LaunchRecord>(`/runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST",
      body: JSON.stringify({ options }),
    }),

  cancelRun: (runId: string) =>
    request<LaunchRecord>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),

  deleteRun: (runId: string) =>
    request<{ deleted: string }>(`/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),

  usage: (runIds: string[], survival: boolean) => {
    const params = new URLSearchParams();
    for (const id of runIds) params.append("run_id", id);
    if (survival) params.set("survival", "true");
    const query = params.toString();
    return request<UsageReport>(`/usage${query ? `?${query}` : ""}`);
  },

  feedback: (runId: string) =>
    request<FeedbackState>(`/runs/${encodeURIComponent(runId)}/feedback`),

  postFeedback: (runId: string, body: FeedbackRequest) =>
    request<unknown>(`/runs/${encodeURIComponent(runId)}/feedback`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  runSignals: (runId: string, survival: boolean) =>
    request<RunSignalsResponse>(
      `/runs/${encodeURIComponent(runId)}/signals?survival=${survival ? "true" : "false"}`,
    ),
};
