import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  briefRows,
  COMPACT_ROW_PX,
  FULL_ROW_PX,
  NowRunning,
  nowRunningRows,
  VISIBLE_ROWS,
  type NowRunningRow,
} from "../components/NowRunning";
import { RunDetail } from "../components/RunDetail";
import { RunsList } from "../components/RunsList";
import type { RunActivity, RunDetail as RunDetailData, TaskActivity, TaskStat } from "../types";
import { usePolling } from "../usePolling";

const NOW_MS = Date.parse("2026-10-02T12:00:00+00:00");

function row(i: number, over: Partial<NowRunningRow> = {}): NowRunningRow {
  return {
    id: `task-${i}`,
    status: "running",
    model: "sonnet",
    effort: "high",
    turns: 7,
    tokens: 12_345,
    tokensEstimated: true,
    costUsd: 0.25,
    elapsedSeconds: 95,
    lastAction: "Bash: pytest -q",
    idleSeconds: 12,
    stuck: false,
    ...over,
  };
}

const rows = (n: number) => Array.from({ length: n }, (_, i) => row(i));

describe("NowRunning fixed 3-row box (HARD UX requirement)", () => {
  it.each([0, 1, 3, 5, 30])("keeps the SAME fixed height with %i running tasks", (n) => {
    render(<NowRunning rows={rows(n)} />);
    const body = screen.getByTestId("now-running-body");
    const px = VISIBLE_ROWS * FULL_ROW_PX;
    expect(body.style.height).toBe(`${px}px`);
    expect(body.style.minHeight).toBe(`${px}px`);
    expect(body.style.maxHeight).toBe(`${px}px`);
    expect(body.style.overflowY).toBe("auto");
  });

  it("renders every row inside the scrolling box (overflow scrolls, nothing is dropped)", () => {
    render(<NowRunning rows={rows(5)} />);
    const items = within(screen.getByRole("list", { name: "Running tasks" })).getAllByRole(
      "listitem",
    );
    expect(items).toHaveLength(5);
    for (const item of items) expect(item.style.height).toBe(`${FULL_ROW_PX}px`);
  });

  it("is not collapsible: no buttons, no disclosure, no aria-expanded", () => {
    const { container } = render(<NowRunning rows={rows(5)} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(container.querySelector("details, summary, [aria-expanded]")).toBeNull();
  });

  it("empty state fills the same fixed height", () => {
    render(<NowRunning rows={[]} />);
    expect(screen.getByText("No tasks running")).toBeInTheDocument();
    expect(screen.getByTestId("now-running-body").style.height).toBe(
      `${VISIBLE_ROWS * FULL_ROW_PX}px`,
    );
  });

  it("shows every required field per row", () => {
    render(<NowRunning rows={[row(1)]} />);
    const item = screen.getByRole("listitem");
    expect(within(item).getByText("task-1")).toBeInTheDocument();
    expect(within(item).getByText("running")).toBeInTheDocument();
    expect(within(item).getByText("sonnet")).toBeInTheDocument();
    expect(within(item).getByText("high")).toBeInTheDocument();
    expect(within(item).getByText("7")).toBeInTheDocument();
    expect(within(item).getByText("~12.3K")).toBeInTheDocument(); // estimated tokens
    expect(within(item).getByText("$0.25")).toBeInTheDocument();
    expect(within(item).getByText("1m 35s")).toBeInTheDocument();
    expect(within(item).getByText(/Bash: pytest -q/)).toBeInTheDocument();
  });

  it("flags a stuck task and renders missing metrics as dashes", () => {
    render(
      <NowRunning
        rows={[
          row(1, {
            stuck: true,
            idleSeconds: 400,
            turns: null,
            tokens: null,
            costUsd: null,
            lastAction: null,
          }),
        ]}
      />,
    );
    const item = screen.getByRole("listitem");
    expect(within(item).getByText(/idle 6m 40s/)).toBeInTheDocument();
    expect(within(item).getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });

  it("renders agent-authored text as text, never markup", () => {
    render(<NowRunning rows={[row(1, { lastAction: "<img src=x onerror=alert(1)>" })]} />);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument();
  });

  it("compact variant has its own fixed 3-row height and no header", () => {
    render(<NowRunning rows={rows(8)} variant="compact" />);
    expect(screen.getByTestId("now-running-body").style.height).toBe(
      `${VISIBLE_ROWS * COMPACT_ROW_PX}px`,
    );
    expect(screen.queryByText("Now running")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
  });
});

describe("row builders", () => {
  const task = (id: string, status: string, started: string | null = null): TaskStat =>
    ({ id, status, started_at: started, model: "opus", effort: "xhigh" }) as unknown as TaskStat;
  const act = (over: Partial<TaskActivity>): TaskActivity => ({
    task_id: "a",
    status: "running",
    source: "transcript",
    attempt: 1,
    cycle: 1,
    turns: 3,
    input_tokens: 10,
    output_tokens: 90,
    cost_usd: null,
    last_action: "Read: a.py",
    idle_seconds: 2,
    elapsed_seconds: 50,
    stuck: false,
    approximate: false,
    tokens_estimated: true,
    ...over,
  });

  it("only running tasks, joined with activity", () => {
    const activity: RunActivity = {
      schema_version: 1,
      run_id: "r",
      generated_at: "",
      tasks: { a: act({}) },
    };
    const out = nowRunningRows([task("a", "running"), task("b", "succeeded")], activity, NOW_MS);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ id: "a", turns: 3, tokens: 100, model: "opus", effort: "xhigh" });
  });

  it("falls back to the client clock for elapsed and to nulls without activity", () => {
    const out = nowRunningRows(
      [task("a", "running", "2026-10-02T11:59:00+00:00")],
      null,
      NOW_MS,
    );
    expect(out[0].elapsedSeconds).toBe(60);
    expect(out[0].turns).toBeNull();
    expect(nowRunningRows([task("a", "running", "garbage")], null, NOW_MS)[0].elapsedSeconds).toBeNull();
  });

  it("briefRows maps state-derived briefs", () => {
    const out = briefRows(
      [{ id: "x", model: "m", effort: null, started_at: "2026-10-02T11:58:00+00:00" }],
      NOW_MS,
    );
    expect(out[0]).toMatchObject({ id: "x", elapsedSeconds: 120, turns: null });
  });
});

describe("usePolling", () => {
  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  });

  function Probe({ tick, enabled = true }: { tick: () => void; enabled?: boolean }) {
    usePolling(tick, 3000, enabled);
    return null;
  }
  const setHidden = (hidden: boolean) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    document.dispatchEvent(new Event("visibilitychange"));
  };

  it("ticks immediately and every interval", () => {
    vi.useFakeTimers();
    const tick = vi.fn();
    render(<Probe tick={tick} />);
    expect(tick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(9000);
    expect(tick).toHaveBeenCalledTimes(4);
  });

  it("pauses while document.hidden and refreshes on return", () => {
    vi.useFakeTimers();
    const tick = vi.fn();
    render(<Probe tick={tick} />);
    act(() => setHidden(true));
    tick.mockClear();
    vi.advanceTimersByTime(30000);
    expect(tick).not.toHaveBeenCalled();
    act(() => setHidden(false));
    expect(tick).toHaveBeenCalledTimes(1); // immediate refresh
    vi.advanceTimersByTime(3000);
    expect(tick).toHaveBeenCalledTimes(2);
  });

  it("does not poll at all when mounted hidden or disabled", () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    const tick = vi.fn();
    const { unmount } = render(<Probe tick={tick} />);
    vi.advanceTimersByTime(10000);
    expect(tick).not.toHaveBeenCalled();
    unmount();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    render(<Probe tick={tick} enabled={false} />);
    vi.advanceTimersByTime(10000);
    expect(tick).not.toHaveBeenCalled();
  });

  it("stops polling on unmount", () => {
    vi.useFakeTimers();
    const tick = vi.fn();
    const { unmount } = render(<Probe tick={tick} />);
    unmount();
    tick.mockClear();
    vi.advanceTimersByTime(10000);
    expect(tick).not.toHaveBeenCalled();
  });
});

function runningTask(id: string, over: Partial<TaskStat> = {}): TaskStat {
  return {
    id,
    status: "running",
    attempts: 1,
    started_at: "2026-10-02T11:58:00+00:00",
    ended_at: null,
    duration_seconds: null,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    origin: "static",
    route: null,
    output_artifact_path: null,
    outputs: [],
    integration_status: null,
    tier_reached: null,
    conflicted_count: 0,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    cache_hit_rate: null,
    dispatch_cycle: 1,
    not_taken_reason: null,
    model: "sonnet",
    effort: "high",
    ...over,
  };
}

function detailWith(tasks: TaskStat[]): RunDetailData {
  return {
    summary: {
      run_id: "run-1",
      workflow_id: "demo",
      status: "running",
      started_at: "2026-10-02T11:00:00+00:00",
      updated_at: "2026-10-02T11:59:00+00:00",
      task_count: tasks.length,
      task_counts: { running: tasks.length },
      cost_usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      wall_seconds: 100,
      active_seconds: 100,
      is_terminal: false,
      is_live: true,
      launch_id: null,
    },
    tasks,
    tripped_breakers: [],
    route_decisions: {},
    monitor_decisions: [],
    run_dir: "/ws/.orchestrator/runs/run-1",
    is_live: true,
    launch: null,
    integration: null,
    graph_version: null,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("RunDetail Now running section", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows 5 concurrent running tasks in the fixed box, with live metrics and table columns", async () => {
    const tasks = Array.from({ length: 5 }, (_, i) => runningTask(`worker-${i}`));
    const activity: RunActivity = {
      schema_version: 1,
      run_id: "run-1",
      generated_at: "",
      tasks: Object.fromEntries(
        tasks.map((t, i) => [
          t.id,
          {
            task_id: t.id,
            status: "running",
            source: "transcript",
            attempt: 1,
            cycle: 1,
            turns: 10 + i,
            input_tokens: 100,
            output_tokens: 900,
            cost_usd: null,
            last_action: "Edit: src/a.py",
            idle_seconds: 3,
            elapsed_seconds: 120,
            stuck: false,
            approximate: false,
            tokens_estimated: true,
          } satisfies TaskActivity,
        ]),
      ),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/activity")) return json(activity);
        if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: "" });
        return json(detailWith(tasks));
      }),
    );
    render(<RunDetail runId="run-1" onBack={() => {}} />);

    const list = await screen.findByRole("list", { name: "Running tasks" });
    expect(await within(list).findAllByText("~1.0K")).toHaveLength(5);
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByTestId("now-running-body").style.height).toBe(
      `${VISIBLE_ROWS * FULL_ROW_PX}px`,
    );
    // Table gained Model + Turns columns.
    const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
    expect(headers).toContain("Model");
    expect(headers).toContain("Turns");
  });

  it("a failing activity endpoint degrades silently (older backend)", async () => {
    const tasks = [runningTask("worker-0")];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/activity")) return json({ detail: "nope" }, 404);
        if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: "" });
        return json(detailWith(tasks));
      }),
    );
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const list = await screen.findByRole("list", { name: "Running tasks" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("RunsList compact now-running", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a compact fixed box only for runs that have running tasks", async () => {
    const summary = (id: string, running: number) => ({
      run_id: id,
      workflow_id: "wf",
      status: "running",
      started_at: "2026-10-02T11:00:00+00:00",
      updated_at: "2026-10-02T11:01:00+00:00",
      task_count: 6,
      task_counts: { running },
      cost_usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      wall_seconds: 60,
      active_seconds: 60,
      is_terminal: false,
      is_live: true,
      launch_id: null,
      running_tasks: Array.from({ length: running }, (_, i) => ({
        id: `t${i}`,
        model: "sonnet",
        effort: "low",
        started_at: "2026-10-02T11:00:30+00:00",
      })),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/runs/stats"))
          return json({
            total_runs: 2,
            runs_by_status: {},
            total_tasks: 0,
            tasks_by_status: {},
            total_cost_usd: 0,
            total_input_tokens: 0,
            total_output_tokens: 0,
            total_wall_seconds: 0,
            total_active_seconds: 0,
          });
        return json([summary("run-a", 5), summary("run-b", 0)]);
      }),
    );
    render(<RunsList onOpen={() => {}} />);
    const bodies = await screen.findAllByTestId("now-running-body");
    expect(bodies).toHaveLength(1);
    expect(bodies[0].style.height).toBe(`${VISIBLE_ROWS * COMPACT_ROW_PX}px`);
    expect(within(bodies[0]).getAllByRole("listitem")).toHaveLength(5);
  });
});
