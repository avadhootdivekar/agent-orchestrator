import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NewRun } from "../components/NewRun";
import { RunsList } from "../components/RunsList";
import { StatusChip } from "../components/common";
import type { AggregateStats, RunSummary, WorkflowInfo } from "../types";

const WORKFLOWS: WorkflowInfo[] = [
  {
    id: "promptable",
    name: "Promptable workflow",
    path: "/ws/promptable.json",
    task_count: 3,
    prompt_path: "prompts/run.md",
    general_instructions: [],
    error: null,
  },
  {
    id: "no-prompt",
    name: "No prompt workflow",
    path: "/ws/no-prompt.json",
    task_count: 1,
    prompt_path: null,
    general_instructions: [],
    error: null,
  },
];

const RUN: RunSummary = {
  run_id: "demo-20260724T100000Z",
  workflow_id: "demo",
  status: "running",
  started_at: "2026-07-24T10:00:00+00:00",
  updated_at: "2026-07-24T10:05:00+00:00",
  task_count: 4,
  task_counts: { succeeded: 2, running: 1, pending: 1 },
  cost_usd: 1.2345,
  input_tokens: 12000,
  output_tokens: 3400,
  wall_seconds: 300,
  active_seconds: 240,
  is_terminal: false,
  is_live: true,
  launch_id: "launch-1",
};

const STATS: AggregateStats = {
  total_runs: 3,
  runs_by_status: { succeeded: 2, running: 1 },
  total_tasks: 12,
  tasks_by_status: { succeeded: 9, pending: 3 },
  total_cost_usd: 25.66,
  total_input_tokens: 500000,
  total_output_tokens: 90000,
  total_wall_seconds: 5400,
  total_active_seconds: 4800,
};

/** Route fetch by URL so components exercise their real api.ts client. */
function mockFetch(routes: Record<string, unknown>, onPost?: (url: string, body: string) => void) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      onPost?.(url, String(init.body ?? ""));
      return new Response(JSON.stringify({ launch_id: "launch-x", run_id: "new-run" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      });
    }
    const key = Object.keys(routes).find((route) => url.startsWith(route));
    if (!key) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(routes[key]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeEach(() => {
  vi.stubGlobal("confirm", () => true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("StatusChip", () => {
  it("renders the status word alongside the glyph, so color is never the only cue", () => {
    render(<StatusChip status="failed" />);
    const chip = screen.getByText(/failed/);
    expect(chip).toBeInTheDocument();
    expect(chip.className).toContain("critical");
  });
});

describe("NewRun", () => {
  it("enables the prompt box for a workflow that declares prompt_path", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/api/workflows": WORKFLOWS }));
    render(<NewRun onLaunched={() => {}} />);

    const prompt = await screen.findByLabelText("Prompt");
    await waitFor(() => expect(prompt).toBeEnabled());
    expect(screen.getByText(/prompts\/run\.md/)).toBeInTheDocument();
  });

  it("disables the prompt box and explains why when the workflow declares none", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/api/workflows": WORKFLOWS }));
    render(<NewRun onLaunched={() => {}} />);

    const select = await screen.findByLabelText("Workflow");
    await userEvent.selectOptions(select, "/ws/no-prompt.json");

    const prompt = screen.getByLabelText("Prompt");
    await waitFor(() => expect(prompt).toBeDisabled());
    expect(prompt).toHaveAttribute(
      "placeholder",
      expect.stringContaining("declares no prompt_path"),
    );
  });

  it("posts the typed prompt with the selected workflow", async () => {
    const posted: { url: string; body: string }[] = [];
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/workflows": WORKFLOWS }, (url, body) => posted.push({ url, body })),
    );

    const onLaunched = vi.fn();
    render(<NewRun onLaunched={onLaunched} />);

    const prompt = await screen.findByLabelText("Prompt");
    await waitFor(() => expect(prompt).toBeEnabled());
    await userEvent.type(prompt, "Add rate limiting");
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => expect(posted).toHaveLength(1));
    const body = JSON.parse(posted[0].body);
    expect(body.workflow_path).toBe("/ws/promptable.json");
    expect(body.prompt).toBe("Add rate limiting");
    expect(onLaunched).toHaveBeenCalledWith("new-run");
  });

  it("switches to the template form under the 'From template' tab, leaving the workflow form intact", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/workflows": WORKFLOWS, "/api/templates": [] }),
    );
    render(<NewRun onLaunched={() => {}} />);

    await screen.findByLabelText("Workflow");
    await userEvent.click(screen.getByRole("tab", { name: "From template" }));

    expect(screen.queryByLabelText("Workflow")).not.toBeInTheDocument();
    expect(await screen.findByText(/No templates registered/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "From workflow" }));
    expect(await screen.findByLabelText("Workflow")).toBeInTheDocument();
  });
});

describe("RunsList", () => {
  it("shows aggregate stats and a row per run", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/runs/stats": STATS, "/api/runs": [RUN] }),
    );

    render(<RunsList onOpen={() => {}} />);

    expect(await screen.findByText("3")).toBeInTheDocument(); // hero: total runs
    expect(screen.getByText("$25.66")).toBeInTheDocument();
    expect(screen.getByText(RUN.run_id)).toBeInTheDocument();

    const row = screen.getByText(RUN.run_id).closest("tr")!;
    expect(within(row).getByText("$1.23")).toBeInTheDocument();
    expect(within(row).getByText("2/4")).toBeInTheDocument(); // succeeded / total tasks
    expect(within(row).getByText("5m 0s")).toBeInTheDocument(); // wall time
    expect(within(row).getByText("4m 0s")).toBeInTheDocument(); // actual (active) time
  });

  it("offers Cancel (not Resume) for a live run, and blocks deleting it", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/api/runs/stats": STATS, "/api/runs": [RUN] }));
    render(<RunsList onOpen={() => {}} />);

    const row = (await screen.findByText(RUN.run_id)).closest("tr")!;
    expect(within(row).getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(within(row).queryByRole("button", { name: "Resume" })).toBeNull();
    // A live run must be cancelled before it can be deleted.
    expect(within(row).getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("offers Resume for an interrupted run and allows deleting it", async () => {
    const failed: RunSummary = {
      ...RUN,
      run_id: "failed-run",
      status: "failed",
      is_live: false,
      is_terminal: true,
    };
    vi.stubGlobal("fetch", mockFetch({ "/api/runs/stats": STATS, "/api/runs": [failed] }));
    render(<RunsList onOpen={() => {}} />);

    const row = (await screen.findByText("failed-run")).closest("tr")!;
    expect(within(row).getByRole("button", { name: "Resume" })).toBeEnabled();
    expect(within(row).getByRole("button", { name: "Delete" })).toBeEnabled();
  });

  it("does not offer Resume for a run that already succeeded", async () => {
    const done: RunSummary = {
      ...RUN,
      run_id: "done-run",
      status: "succeeded",
      is_live: false,
      is_terminal: true,
    };
    vi.stubGlobal("fetch", mockFetch({ "/api/runs/stats": STATS, "/api/runs": [done] }));
    render(<RunsList onOpen={() => {}} />);

    const row = (await screen.findByText("done-run")).closest("tr")!;
    expect(within(row).getByRole("button", { name: "Resume" })).toBeDisabled();
  });
});
