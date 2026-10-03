import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { NewRun } from "../components/NewRun";
import { RunsList } from "../components/RunsList";
import { TemplateLaunch } from "../components/TemplateLaunch";
import type { RunSummary, TemplateInfo, WorkflowInfo } from "../types";

const WORKFLOWS: WorkflowInfo[] = [
  {
    id: "wf",
    name: "wf",
    path: "/ws/wf.json",
    task_count: 1,
    prompt_path: "prompt.md",
    general_instructions: [],
    error: null,
  },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Records every call and answers via `handler(method, url, body)`. */
function stub(
  handler: (method: string, url: string, body: string) => Response,
) {
  const calls: { method: string; url: string; body: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = String(init?.body ?? "");
      calls.push({ method, url, body });
      return handler(method, url, body);
    }),
  );
  return calls;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("api client: launch + run-control URLs", () => {
  it("builds each request path", async () => {
    const calls = stub(() => json({}));
    await api.launch("launch-1");
    await api.launches();
    await api.launches({ status: "failed_to_start", sinceHours: 24 });
    await api.readFileHtml("a.html", "root-x");
    await api.resumeRun("r 1");
    await api.cancelRun("r 1");
    await api.deleteRun("r 1");
    const seen = calls.map((c) => `${c.method} ${c.url}`);
    expect(seen).toEqual([
      "GET /api/launches/launch-1",
      "GET /api/launches",
      "GET /api/launches?status=failed_to_start&since_hours=24",
      "GET /api/files/html?path=a.html&root=root-x",
      "POST /api/runs/r%201/resume",
      "POST /api/runs/r%201/cancel",
      "DELETE /api/runs/r%201",
    ]);
  });
});

describe("NewRun overrides + load errors", () => {
  it("sends typed overrides as typed values and drops cleared ones", async () => {
    const calls = stub((method, url) =>
      method === "POST"
        ? json(
            {
              launch_id: "launch-20261003T120000000000Z",
              run_id: "r",
              status: "started",
            },
            201,
          )
        : url.startsWith("/api/workflows")
          ? json(WORKFLOWS)
          : json({}, 404),
    );
    render(<NewRun onLaunched={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /start run/i })).toBeEnabled(),
    );
    await userEvent.type(screen.getByLabelText("Model"), "opus");
    await userEvent.selectOptions(screen.getByLabelText("Effort"), "high");
    await userEvent.type(screen.getByLabelText("Max parallel"), "3");
    await userEvent.type(screen.getByLabelText("Max attempts"), "2");
    await userEvent.type(screen.getByLabelText("Token budget"), "9");
    await userEvent.clear(screen.getByLabelText("Model"));
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    await screen.findByText("Run started");
    const post = calls.find((c) => c.method === "POST")!;
    expect(JSON.parse(post.body).options).toEqual({
      effort: "high",
      max_parallel: 3,
      max_attempts: 2,
      budget_total: 9,
    });
  });

  it("surfaces a workflow-list failure", async () => {
    stub(() => json({ detail: "workflows exploded" }, 500));
    render(<NewRun onLaunched={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "workflows exploded",
    );
  });
});

describe("TemplateLaunch extras", () => {
  const TEMPLATE: TemplateInfo = {
    name: "tpl",
    description: "d",
    path: "/t",
    source: "builtin",
    params: [
      {
        name: "mode",
        description: "",
        required: false,
        enum: null,
        default: "fast",
      },
      {
        name: "repo_set",
        description: "",
        required: true,
        enum: null,
        default: null,
      },
    ],
    required_agents: [],
    prompt_skeleton: null,
  };

  it("prefills declared defaults, edits prompt and slug, and posts them", async () => {
    const calls = stub((method, url) =>
      method === "POST"
        ? json({ detail: "nope" }, 400)
        : url.startsWith("/api/templates")
          ? json([TEMPLATE])
          : json({}, 404),
    );
    render(<TemplateLaunch onLaunched={vi.fn()} />);
    const mode = await screen.findByLabelText(/mode/);
    await waitFor(() => expect(mode).toHaveValue("fast"));
    await userEvent.type(screen.getByLabelText(/repo_set/), "fin-plan");
    await userEvent.type(screen.getByLabelText("Prompt"), "do it");
    await userEvent.type(screen.getByLabelText(/Slug/), "my-slug");
    await userEvent.click(screen.getByRole("button", { name: "Create & run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
    const body = JSON.parse(calls.find((c) => c.method === "POST")!.body);
    expect(body).toMatchObject({
      slug_or_id: "my-slug",
      prompt: "do it",
      start: true,
      params: { mode: "fast", repo_set: "fin-plan" },
    });
  });

  it("surfaces a template-list failure", async () => {
    stub(() => json({ detail: "templates exploded" }, 500));
    render(<TemplateLaunch onLaunched={vi.fn()} />);
    expect(await screen.findByText("templates exploded")).toBeInTheDocument();
  });
});

const RUN_BASE: RunSummary = {
  run_id: "demo-1",
  workflow_id: "demo",
  status: "failed",
  started_at: "2026-10-03T10:00:00+00:00",
  updated_at: "2026-10-03T10:05:00+00:00",
  task_count: 2,
  task_counts: { succeeded: 1, failed: 1 },
  cost_usd: 1,
  input_tokens: 1,
  output_tokens: 1,
  wall_seconds: 1,
  active_seconds: 1,
  is_live: false,
  launch_id: null,
} as unknown as RunSummary;

const STATS = {
  total_runs: 1,
  runs_by_status: { failed: 1 },
  total_cost_usd: 1,
  total_tasks: 2,
  tasks_by_status: { succeeded: 1 },
  total_input_tokens: 1,
  total_output_tokens: 1,
  total_wall_seconds: 1,
  total_active_seconds: 1,
};

describe("RunsList row actions", () => {
  function routes(
    runs: RunSummary[],
    onAction?: (m: string, u: string) => Response | undefined,
  ) {
    return stub((method, url) => {
      const custom = onAction?.(method, url);
      if (custom) return custom;
      if (url.startsWith("/api/runs/stats")) return json(STATS);
      if (url.startsWith("/api/runs"))
        return json(method === "GET" ? runs : {});
      if (url.startsWith("/api/launches")) return json([]);
      return json({}, 404);
    });
  }

  it("resume, cancel and delete call the API and refresh", async () => {
    vi.stubGlobal("confirm", () => true);
    const calls = routes([
      RUN_BASE,
      { ...RUN_BASE, run_id: "live-1", is_live: true },
    ]);
    render(<RunsList onOpen={vi.fn()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Resume" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === "DELETE" && c.url === "/api/runs/demo-1",
        ),
      ).toBe(true),
    );
    expect(calls.some((c) => c.url === "/api/runs/demo-1/resume")).toBe(true);
    expect(calls.some((c) => c.url === "/api/runs/live-1/cancel")).toBe(true);
  });

  it("declining the delete confirmation does nothing", async () => {
    vi.stubGlobal("confirm", () => false);
    const calls = routes([RUN_BASE]);
    render(<RunsList onOpen={vi.fn()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Delete" }),
    );
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("shows an action error and a refresh error", async () => {
    routes([RUN_BASE], (_method, url) =>
      url.endsWith("/resume")
        ? json({ detail: "cannot resume" }, 409)
        : undefined,
    );
    render(<RunsList onOpen={vi.fn()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Resume" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot resume");

    stub(() => json({ detail: "list exploded" }, 500));
    render(<RunsList onOpen={vi.fn()} />);
    expect(await screen.findByText("list exploded")).toBeInTheDocument();
  });

  it("opens a run on plain click and via the refresh button", async () => {
    const onOpen = vi.fn();
    const calls = routes([RUN_BASE]);
    render(<RunsList onOpen={onOpen} />);
    await userEvent.click(await screen.findByRole("link", { name: "demo-1" }));
    expect(onOpen).toHaveBeenCalledWith("demo-1");
    const before = calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
  });
});
