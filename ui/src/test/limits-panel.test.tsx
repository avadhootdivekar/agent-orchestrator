import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  formatLimitValue,
  LimitsPanel,
  limitFraction,
  TaskLimitsSection,
} from "../components/LimitsPanel";
import { briefRows, NowRunning, nowRunningRows } from "../components/NowRunning";
import type { RunLimit, TaskLimits, TaskStat } from "../types";

const NOW_MS = Date.parse("2026-11-09T12:00:00+00:00");

function limit(over: Partial<RunLimit> & Pick<RunLimit, "key" | "label">): RunLimit {
  return { value: null, source: "default", used: null, cap: null, unit: "", note: null, ...over };
}

const LIMITS: TaskLimits = {
  thinking_effort: "high",
  turn_budget: "medium",
  max_turns: 30,
  autocompact: "auto",
  timeout_seconds: 1800,
  task_cost_cap_usd: null,
  max_attempts: 3,
};

function task(over: Partial<TaskStat> = {}): TaskStat {
  return {
    id: "t1", status: "running", attempts: 2, started_at: "2026-11-09T11:50:00+00:00",
    ended_at: null, duration_seconds: null, input_tokens: 0, output_tokens: 0, cost_usd: 1.5,
    origin: "static", route: null, output_artifact_path: null, outputs: [],
    integration_status: null, tier_reached: null, conflicted_count: 0, cache_read_tokens: 0,
    cache_creation_tokens: 0, cache_hit_rate: null, dispatch_cycle: 1, not_taken_reason: null,
    model: "sonnet", effort: "medium", limits: LIMITS, ...over,
  };
}

describe("LimitsPanel: running and completed runs", () => {
  const rows: RunLimit[] = [
    limit({ key: "setting.max_parallel", label: "Max parallel", value: 4, source: "cli" }),
    limit({ key: "run.cost", label: "Run cost", used: 6.8, cap: 10, unit: "usd", source: "workflow" }),
    limit({ key: "breaker.w", label: "Wall clock cap (w)", used: 3500, cap: 3600, unit: "s", source: "workflow" }),
    limit({ key: "budget.tokens", label: "Token budget", used: 100, cap: 1000, unit: "tokens" }),
    limit({ key: "setting.autocompact", label: "Autocompact", value: null, source: "unset" }),
  ];

  it("shows value + source and progress bars against caps with the numbers in text", () => {
    render(<LimitsPanel limits={rows} settingsRecorded={true} />);
    const maxPar = screen.getByTestId("limit-setting.max_parallel");
    expect(maxPar).toHaveTextContent("4");
    expect(maxPar).toHaveTextContent("cli");

    const cost = screen.getByTestId("limit-run.cost");
    const bar = within(cost).getByRole("progressbar", { name: "Run cost" });
    expect(bar).toHaveAttribute("aria-valuenow", "68");
    expect(bar).toHaveAttribute("aria-valuetext", expect.stringContaining("$6.80 / $10.00"));
    expect(cost).toHaveTextContent("68%");
    expect(bar.className).toContain("ok");

    const wall = within(screen.getByTestId("limit-breaker.w")).getByRole("progressbar");
    expect(wall.className).toContain("crit"); // 97% of the wall-clock cap
    expect(screen.getByTestId("limit-budget.tokens")).toHaveTextContent("10%");
  });

  it("an unset value is a dash, and a recorded run has no pre-upgrade notice", () => {
    render(<LimitsPanel limits={rows} settingsRecorded={true} />);
    expect(screen.getByTestId("limit-setting.autocompact")).toHaveTextContent("—");
    expect(screen.queryByTestId("limits-pre-upgrade")).toBeNull();
  });

  it("sentinel-style rows render only when present", () => {
    const { rerender } = render(<LimitsPanel limits={rows} settingsRecorded={true} />);
    expect(screen.queryByTestId("limit-sentinel.max_injected")).toBeNull();
    rerender(
      <LimitsPanel
        limits={[...rows, limit({ key: "sentinel.max_injected", label: "Sentinel cap", used: 3, cap: 12, unit: "tasks", source: "sentinel" })]}
        settingsRecorded={true}
      />,
    );
    expect(screen.getByTestId("limit-sentinel.max_injected")).toHaveTextContent("3 / 12");
  });
});

describe("LimitsPanel: pre-upgrade run", () => {
  it("shows a dash with an explanatory tooltip, and tolerates an absent limits field", () => {
    render(<LimitsPanel limits={undefined} settingsRecorded={undefined} />);
    const note = screen.getByTestId("limits-pre-upgrade");
    expect(note).toHaveTextContent("—");
    expect(note).toHaveAttribute("title", expect.stringContaining("before settings and limits"));
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});

describe("formatters", () => {
  it("formats by unit and guards against zero/absent caps", () => {
    expect(formatLimitValue(90, "s")).not.toBe("90");
    expect(formatLimitValue(null, "usd")).toBe("—");
    expect(formatLimitValue(true, "")).toBe("on");
    expect(limitFraction(5, 0)).toBeNull();
    expect(limitFraction(null, 10)).toBeNull();
    expect(limitFraction(5, 10)).toBe(0.5);
  });
});

describe("TaskLimitsSection", () => {
  it("running task: effective fields, turns/timeout/attempts against caps", () => {
    render(
      <TaskLimitsSection
        stat={task()}
        activity={{ turns: 12 } as never}
        nowMs={NOW_MS}
      />,
    );
    expect(screen.getByText("high")).toBeInTheDocument(); // thinking effort
    expect(screen.getByText("medium")).toBeInTheDocument(); // turn budget
    expect(screen.getByRole("progressbar", { name: "Turns used" })).toHaveAttribute("aria-valuenow", "40");
    // 10 minutes elapsed of 30
    expect(screen.getByRole("progressbar", { name: "Elapsed time" })).toHaveAttribute("aria-valuenow", "33");
    expect(screen.getByRole("progressbar", { name: "Attempts used" })).toHaveAttribute("aria-valuenow", "67");
    expect(screen.getByText("auto")).toBeInTheDocument();
  });

  it("completed task uses its recorded duration, not the clock", () => {
    render(
      <TaskLimitsSection
        stat={task({ status: "succeeded", ended_at: "x", duration_seconds: 900, attempts: 1 })}
        nowMs={NOW_MS + 99_999_000}
      />,
    );
    expect(screen.getByRole("progressbar", { name: "Elapsed time" })).toHaveAttribute("aria-valuenow", "50");
  });

  it("pre-upgrade task shows a dash with tooltip", () => {
    render(<TaskLimitsSection stat={task({ limits: undefined })} nowMs={NOW_MS} />);
    expect(screen.getByTestId("task-limits-pre-upgrade")).toHaveAttribute("title", expect.stringContaining("before"));
  });
});

describe("NowRunning rows carry the effective limits", () => {
  it("shows turns/max, elapsed/timeout and spells out turn budget vs thinking effort", () => {
    const rows = nowRunningRows(
      [task()],
      { schema_version: 1, run_id: "r", generated_at: "", tasks: { t1: { turns: 12, elapsed_seconds: 600 } as never } },
      NOW_MS,
    );
    render(<NowRunning rows={rows} />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("12/30");
    expect(item).toHaveTextContent("10m");
    expect(within(item).getByTitle(/turn budget: medium · thinking effort: high/)).toBeInTheDocument();
  });

  it("pre-upgrade running task (no limits) falls back to the legacy effort with a tooltip", () => {
    const rows = nowRunningRows([task({ limits: undefined })], null, NOW_MS);
    render(<NowRunning rows={rows} />);
    expect(screen.getByRole("listitem")).toHaveTextContent("medium");
    expect(screen.getByTitle(/not recorded for this task/)).toBeInTheDocument();
  });

  it("compact brief rows tolerate an old backend without the new fields", () => {
    const rows = briefRows([{ id: "a", model: "m", effort: null, started_at: null }], NOW_MS);
    expect(rows[0].limits).toBeNull();
  });
});
