/** Typed client for the dashboard API. */

import { clearProof, readProof, writeProof } from "./auth/proof";
import type {
  PathProbeResponse,
  AggregateStats,
  AuthStatus,
  AuthStepResponse,
  CreateInstanceRequest,
  CreateInstanceResponse,
  DirListing,
  EnrollBeginBody,
  FeedbackRequest,
  FeedbackState,
  OperatorNoteRequest,
  OperatorNoteResponse,
  OperatorNotesState,
  FileContent,
  GeneralInstruction,
  HtmlPreview,
  KeepaliveResponse,
  LaunchRecord,
  LaunchStatus,
  ReauthBody,
  RecoveryCodesResponse,
  RunActivity,
  RunLiveSummary,
  RunDetail,
  RunGraph,
  RunOptions,
  RunSignalsResponse,
  RunSummary,
  TemplateInfo,
  TotpEnrollment,
  UsageReport,
  WorkflowInfo,
  WorkspaceInfo,
} from "./types";
import { SESSION_LOSS_CODES, SESSION_PROOF_HEADER } from "./types";

const BASE = "/api";

/**
 * Error carrying the HTTP status so callers can distinguish 404 from 409. Auth errors (HLD §2.2)
 * also carry the machine `code`, the `Retry-After` seconds and the full JSON body as `extra`
 * (per-code keys such as `attempts_remaining`); all three are optional, so
 * `new ApiError(message, status)` keeps working.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly retryAfterSeconds?: number,
    readonly extra?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type SessionLossHandler = (code: string) => void;
let sessionLossHandler: SessionLossHandler | null = null;

/** Register (or clear, with null) the one global "session is gone" callback — see `AuthGate`. */
export function setSessionLossHandler(fn: SessionLossHandler | null): void {
  sessionLossHandler = fn;
}

function isSessionLossCode(code: string): boolean {
  return (SESSION_LOSS_CODES as readonly string[]).includes(code);
}

/** `Retry-After` seconds from the header, else the body's `retry_after_seconds`; undefined if neither is positive. */
function parseRetryAfter(
  response: Response,
  body: Record<string, unknown> | undefined,
): number | undefined {
  // Optional chaining: hand-rolled test doubles (and some proxies' odd responses) may lack headers.
  const fromHeader = Number(response.headers?.get("Retry-After"));
  if (Number.isFinite(fromHeader) && fromHeader > 0) return fromHeader;
  const fromBody = body?.retry_after_seconds;
  return typeof fromBody === "number" && Number.isFinite(fromBody) && fromBody > 0
    ? fromBody
    : undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // D25: every /api call carries the session proof once one is stored. The proof is read on each
  // call (not cached) so a rotation made by another tab is picked up. NEVER add a cross-origin
  // `mode` option to this fetch: a plain same-origin fetch() sends the real Origin header the
  // server's CSRF check needs (§2.1), and fetch-mode-ban.test.ts enforces it.
  const proof = readProof();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (proof) headers[SESSION_PROOF_HEADER] = proof;
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // Merged after the spread so callers can add headers but never drop Content-Type.
    headers: { ...headers, ...((init?.headers as Record<string, string> | undefined) ?? {}) },
  });

  if (!response.ok) {
    // FastAPI puts the human-readable reason in `detail`; fall back to the status
    // text when a proxy or crash returns something that is not our JSON envelope.
    let detail = response.statusText;
    let code: string | undefined;
    let extra: Record<string, unknown> | undefined;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
      if (body && typeof body.code === "string") {
        code = body.code;
        extra = body as Record<string, unknown>;
      }
    } catch {
      /* non-JSON error body — keep statusText */
    }
    // Only a 401 whose code says the session is gone/incomplete triggers the global handler;
    // wrong credentials/codes and every 403 are the caller's to display.
    if (response.status === 401 && code !== undefined && isSessionLossCode(code)) {
      sessionLossHandler?.(code);
    }
    throw new ApiError(detail, response.status, code, parseRetryAfter(response, extra), extra);
  }

  const data = (await response.json()) as T;
  // Every session-issuing response rotates the proof (§2.3).
  const issued = (data as { session_proof?: unknown } | null)?.session_proof;
  if (typeof issued === "string") writeProof(issued);
  return data;
}

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

/** Typed auth endpoints (HLD §2.4). Every call goes through `request()`, so the proof header and rotation are automatic. */
export const authApi = {
  status: () => request<AuthStatus>("/auth/status"),
  login: (username: string, password: string) =>
    postJson<AuthStepResponse>("/auth/login", { username, password }),
  verifyTotp: (code: string) => postJson<AuthStepResponse>("/auth/totp/verify", { code }),
  verifyRecovery: (recoveryCode: string) =>
    postJson<AuthStepResponse>("/auth/totp/verify", { recovery_code: recoveryCode }),
  /** E4. Forced enrollment sends `{enrollment_token}`, voluntary `{current_password}`; never both. */
  enrollBegin: (body: EnrollBeginBody) =>
    postJson<TotpEnrollment>("/auth/totp/enroll/begin", body),
  enrollConfirm: (body: { code: string }) =>
    postJson<AuthStepResponse>("/auth/totp/enroll/confirm", body),
  disableTotp: (body: ReauthBody) => postJson<AuthStepResponse>("/auth/totp/disable", body),
  regenerateRecoveryCodes: (body: ReauthBody) =>
    postJson<RecoveryCodesResponse>("/auth/totp/recovery-codes", body),
  changePassword: (body: { current_password: string; new_password: string }) =>
    postJson<AuthStepResponse>("/auth/password", body),
  keepalive: () => postJson<KeepaliveResponse>("/auth/keepalive"),
  /** Idempotent. The local proof is dropped even if the request fails: the client is signed out either way. */
  logout: async (everywhere = false): Promise<{ state: "anonymous" }> => {
    try {
      return await postJson<{ state: "anonymous" }>(
        "/auth/logout",
        everywhere ? { everywhere: true } : {},
      );
    } finally {
      clearProof();
    }
  },
};

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

  /** Existence probe for link auto-detection (A5): status per path, never contents. */
  resolvePaths: (paths: string[], root?: string) =>
    request<PathProbeResponse>("/files/resolve", {
      method: "POST",
      body: JSON.stringify(root ? { paths, root } : { paths }),
    }),

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

  notes: (runId: string) =>
    request<OperatorNotesState>(`/runs/${encodeURIComponent(runId)}/notes`),

  postNote: (runId: string, body: OperatorNoteRequest) =>
    request<OperatorNoteResponse>(`/runs/${encodeURIComponent(runId)}/notes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  runSignals: (runId: string, survival: boolean) =>
    request<RunSignalsResponse>(
      `/runs/${encodeURIComponent(runId)}/signals?survival=${survival ? "true" : "false"}`,
    ),
};
