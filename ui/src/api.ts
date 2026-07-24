/** Typed client for the dashboard API. */

import type {
  AggregateStats,
  CreateInstanceRequest,
  CreateInstanceResponse,
  DirListing,
  FileContent,
  GeneralInstruction,
  LaunchRecord,
  RunDetail,
  RunOptions,
  RunSummary,
  TemplateInfo,
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
  runLog: (runId: string) =>
    request<{ run_id: string; launch_id: string | null; text: string }>(
      `/runs/${encodeURIComponent(runId)}/log`,
    ),

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
};
