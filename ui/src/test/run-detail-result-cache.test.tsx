import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "../components/RunDetail";
import type {
  ResultCacheRunBlock,
  ResultCacheTaskView,
  RunDetail as RunDetailData,
  TaskStat,
} from "../types";

/**
 * E-Rc4Hk8 (HLD 8.10, T-bLpoze D-1c): the cross-run RESULT cache surface -- a `cached` tag
 * on a hit and a "Result cache" tile. Display-only; every cache-derived string is text.
 */

const BASE_TASK: TaskStat = {
  id: "task-a",
  status: "succeeded",
  attempts: 1,
  started_at: "2026-10-05T10:00:00+00:00",
  ended_at: "2026-10-05T10:05:00+00:00",
  duration_seconds: 300,
  input_tokens: 1000,
  output_tokens: 200,
  cost_usd: 0.05,
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
};

const KEY = "a".repeat(64);

function view(over: Partial<ResultCacheTaskView> = {}): ResultCacheTaskView {
  return {
    hit: true,
    key: KEY,
    saved_cost_usd: 0.4123,
    saved_tokens: 15400,
    saved_seconds: 95.2,
    outcome: "hit",
    mode: "on",
    source_run_id: "doc-pipeline-20261005T101450Z",
    ...over,
  };
}

function block(over: Partial<ResultCacheRunBlock> = {}): ResultCacheRunBlock {
  return {
    hits: 2,
    saved_cost_usd: 0.8246,
    saved_tokens: 30800,
    saved_seconds: 190.4,
    would_hits: 0,
    misses: 1,
    ineligible: 0,
    stored: 1,
    lookups: 3,
    avoidable_cost_usd: 0,
    ...over,
  };
}

function makeDetail(
  tasks: TaskStat[],
  resultCache: ResultCacheRunBlock | null | undefined,
): RunDetailData {
  return {
    summary: {
      run_id: "run-1",
      workflow_id: "demo",
      status: "succeeded",
      started_at: "2026-10-05T10:00:00+00:00",
      updated_at: "2026-10-05T10:05:00+00:00",
      task_count: tasks.length,
      task_counts: { succeeded: tasks.length },
      cost_usd: 0.05,
      input_tokens: 1000,
      output_tokens: 200,
      wall_seconds: 300,
      active_seconds: 300,
      is_terminal: true,
      is_live: false,
      launch_id: null,
    },
    tasks,
    tripped_breakers: [],
    route_decisions: {},
    monitor_decisions: [],
    run_dir: "/ws/.orchestrator/runs/run-1",
    is_live: false,
    launch: null,
    integration: null,
    graph_version: null,
    ...(resultCache === undefined ? {} : { result_cache: resultCache }),
  };
}

function mockFetch(detail: RunDetailData) {
  return vi.fn(async (url: string) => {
    if (url.includes("/log")) {
      return new Response(JSON.stringify({ run_id: "run-1", launch_id: null, text: "" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(detail), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

async function renderRow(detail: RunDetailData) {
  vi.stubGlobal("fetch", mockFetch(detail));
  render(<RunDetail runId="run-1" onBack={() => {}} />);
  return (await screen.findByText("task-a")).closest("tr")!;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("result-cache `cached` tag", () => {
  it("renders on a hit, with the source run as plain-text tooltip", async () => {
    const row = await renderRow(
      makeDetail([{ ...BASE_TASK, result_cache: view() }], block({ hits: 1 })),
    );
    const tag = within(row).getByText("cached");
    expect(tag).toHaveClass("tag");
    expect(tag).toHaveAttribute("title", "served from run doc-pipeline-20261005T101450Z");
  });

  it("does not render for a non-hit record (miss, would-hit)", async () => {
    const miss = view({ hit: false, outcome: "miss", source_run_id: null, saved_cost_usd: 0 });
    const row = await renderRow(makeDetail([{ ...BASE_TASK, result_cache: miss }], block()));
    expect(within(row).queryByText("cached")).toBeNull();
  });

  it("does not render when the field is null or absent", async () => {
    const row = await renderRow(makeDetail([{ ...BASE_TASK, result_cache: null }], null));
    expect(within(row).queryByText("cached")).toBeNull();
  });

  it("renders a hostile source_run_id as text, never as an element", async () => {
    const hostile = '<img src=x onerror="window.__pwned = 1">';
    const row = await renderRow(
      makeDetail(
        [{ ...BASE_TASK, result_cache: view({ source_run_id: hostile }) }],
        block({ hits: 1 }),
      ),
    );
    const tag = within(row).getByText("cached");
    expect(tag).toHaveAttribute("title", `served from run ${hostile}`);
    expect(document.querySelector("img")).toBeNull();
    expect(row.querySelector("[onerror]")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("falls back to a generic tooltip when the source run is unknown", async () => {
    const row = await renderRow(
      makeDetail([{ ...BASE_TASK, result_cache: view({ source_run_id: null }) }], block()),
    );
    expect(within(row).getByText("cached")).toHaveAttribute("title", "served from cache");
  });
});

describe("result-cache tile", () => {
  it("shows hits and estimated savings", async () => {
    await renderRow(makeDetail([BASE_TASK], block({ hits: 2, saved_cost_usd: 0.8246 })));
    const tile = screen.getByText("Result cache").closest(".tile") as HTMLElement;
    expect(within(tile).getByText("2 hit(s)")).toBeInTheDocument();
    expect(within(tile).getByText("~$0.82 saved (est.)")).toBeInTheDocument();
  });

  it("shows zero hits as a tile of its own when lookups all missed", async () => {
    await renderRow(makeDetail([BASE_TASK], block({ hits: 0, saved_cost_usd: 0, misses: 3 })));
    const tile = screen.getByText("Result cache").closest(".tile") as HTMLElement;
    expect(within(tile).getByText("0 hit(s)")).toBeInTheDocument();
    expect(within(tile).getByText("~$0 saved (est.)")).toBeInTheDocument();
  });

  it("shows the shadow variant when there are would-hits", async () => {
    await renderRow(
      makeDetail(
        [BASE_TASK],
        block({ hits: 0, saved_cost_usd: 0, would_hits: 3, avoidable_cost_usd: 1.5 }),
      ),
    );
    const tile = screen.getByText("Result cache").closest(".tile") as HTMLElement;
    expect(within(tile).getByText("3 would-hit(s) (shadow)")).toBeInTheDocument();
    expect(within(tile).getByText("~$1.50 avoidable (est.)")).toBeInTheDocument();
    expect(within(tile).queryByText(/^\d+ hit\(s\)$/)).toBeNull();
  });

  it("renders nothing when the run block is null or absent", async () => {
    await renderRow(makeDetail([BASE_TASK], null));
    expect(screen.queryByText("Result cache")).toBeNull();
  });

  it("renders nothing for a backend that predates the field", async () => {
    await renderRow(makeDetail([BASE_TASK], undefined));
    expect(screen.queryByText("Result cache")).toBeNull();
    expect(screen.queryByText("cached")).toBeNull();
  });
});
