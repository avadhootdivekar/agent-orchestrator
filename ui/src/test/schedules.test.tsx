import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Schedules } from "../components/Schedules";
import { TabActionsContext } from "../tabs/context";
import { formatRelative, isSuppressedEvent } from "../format";
import type { Schedule, SchedulesResponse } from "../types";

const NOW = Date.parse("2026-11-11T12:00:00Z");
const clock = () => NOW;

const SCHEDULE: Schedule = {
  id: "nightly",
  kind: "cron",
  enabled: true,
  workflow: "w.json",
  template: null,
  status: "active",
  next_fire_at: "2026-11-11T12:05:00Z",
  last_fire_at: "2026-11-11T11:00:00Z",
  last_run_id: "w-20261111T110000Z",
  last_status: "launched",
  consecutive_failures: 2,
  runs_count: 3,
  max_runs: 10,
  queued: false,
  active_fires: 0,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Calls {
  fn: ReturnType<typeof vi.fn>;
  urls: () => string[];
}

function mockFetch(
  list: SchedulesResponse,
  overrides: Record<string, Response> = {},
): Calls {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (overrides[key]) return overrides[key].clone();
    if (url === "/api/schedules") return json(list);
    if (url.includes("/history")) {
      return json({
        schedule_id: "nightly",
        events: [
          {
            ts: "2026-11-11T11:30:00Z",
            event: "schedule.governance.skipped",
            reason: "quota_exhausted",
            run_id: null,
            detail: null,
          },
          {
            ts: "2026-11-11T11:00:00Z",
            event: "schedule.fired",
            reason: null,
            run_id: "w-1",
            detail: null,
          },
        ],
      });
    }
    if (url.endsWith("/run-now"))
      return json({ queued: true, schedule_id: "nightly" }, 202);
    if (url.endsWith("/disable") || url.endsWith("/enable")) {
      return json({
        schedule_id: "nightly",
        enabled: url.endsWith("/enable"),
        restarted: false,
      });
    }
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fn);
  return {
    fn,
    urls: () => fn.mock.calls.map((c) => `${c[1]?.method ?? "GET"} ${c[0]}`),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Schedules panel", () => {
  it("renders rows with relative and absolute times, status glyph + word, and run progress", async () => {
    mockFetch({ daemon_seen: true, schedules: [SCHEDULE] });
    render(<Schedules clock={clock} />);
    const row = (await screen.findByText("nightly")).closest("tr")!;
    expect(row).toHaveTextContent("in 5m");
    expect(row).toHaveTextContent("1h ago");
    expect(row).toHaveTextContent("w-20261111T110000Z");
    expect(row).toHaveTextContent("active"); // word, not colour alone
    expect(row).toHaveTextContent("2 consecutive failures");
    expect(row).toHaveTextContent("3 / 10 runs");
    expect(screen.queryByText(/No scheduler service/)).toBeNull();
  });

  it("toggles enable via POST and refetches", async () => {
    const calls = mockFetch({ daemon_seen: true, schedules: [SCHEDULE] });
    render(<Schedules clock={clock} />);
    fireEvent.click(
      await screen.findByRole("switch", { name: "Disable nightly" }),
    );
    await waitFor(() =>
      expect(calls.urls()).toContain("POST /api/schedules/nightly/disable"),
    );
    await waitFor(() =>
      expect(
        calls.urls().filter((u) => u === "GET /api/schedules").length,
      ).toBeGreaterThan(1),
    );
  });

  it("Run now renders 'queued', never 'started'", async () => {
    const calls = mockFetch({ daemon_seen: true, schedules: [SCHEDULE] });
    render(<Schedules clock={clock} />);
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(
      await screen.findByText("queued — will fire within ~15 s"),
    ).toBeInTheDocument();
    expect(calls.urls()).toContain("POST /api/schedules/nightly/run-now");
    expect(screen.queryByText(/started/i)).toBeNull();
  });

  it("surfaces a 409 from run-now", async () => {
    mockFetch(
      { daemon_seen: true, schedules: [SCHEDULE] },
      {
        "POST /api/schedules/nightly/run-now": json(
          { detail: "schedule 'nightly' already has an active fire" },
          409,
        ),
      },
    );
    render(<Schedules clock={clock} />);
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(
      await screen.findByText(/already has an active fire/),
    ).toBeInTheDocument();
  });

  it("expands history including suppressed fires with their reason", async () => {
    const calls = mockFetch({ daemon_seen: true, schedules: [SCHEDULE] });
    render(<Schedules clock={clock} />);
    fireEvent.click(await screen.findByRole("button", { name: "History" }));
    expect(await screen.findByText("quota_exhausted")).toBeInTheDocument();
    expect(screen.getByText(/⊘ governance\.skipped/)).toBeInTheDocument();
    expect(calls.urls()).toContain(
      "GET /api/schedules/nightly/history?limit=20",
    );
    fireEvent.click(screen.getByRole("button", { name: "Hide history" }));
    expect(screen.queryByText("quota_exhausted")).toBeNull();
  });

  it("opens the last run through the tab mechanism", async () => {
    mockFetch({ daemon_seen: true, schedules: [SCHEDULE] });
    const open = vi.fn();
    render(
      <TabActionsContext.Provider
        value={{ available: true, navigate: vi.fn(), open, retarget: vi.fn() }}
      >
        <Schedules clock={clock} />
      </TabActionsContext.Provider>,
    );
    fireEvent.click(
      await screen.findByRole("link", { name: "w-20261111T110000Z" }),
    );
    expect(open).toHaveBeenCalledWith({
      kind: "run",
      params: { id: "w-20261111T110000Z" },
    });
  });

  it("warns when no scheduler service has ever run", async () => {
    mockFetch({
      daemon_seen: false,
      schedules: [
        {
          ...SCHEDULE,
          status: null,
          next_fire_at: null,
          last_fire_at: null,
          last_run_id: null,
          last_status: null,
          consecutive_failures: 0,
          runs_count: 0,
        },
      ],
    });
    render(<Schedules clock={clock} />);
    expect(
      await screen.findByText(/No scheduler service has run yet/),
    ).toBeInTheDocument();
    expect(screen.getByText("nightly")).toBeInTheDocument();
  });

  it("renders an empty state", async () => {
    mockFetch({ daemon_seen: true, schedules: [] });
    render(<Schedules clock={clock} />);
    expect(await screen.findByText(/No schedules/)).toBeInTheDocument();
  });

  it("shows the parse error for an invalid schedules file", async () => {
    mockFetch(
      { daemon_seen: true, schedules: [] },
      {
        "GET /api/schedules": json(
          { detail: "/ws/.ao/schedules.yaml: not valid YAML" },
          400,
        ),
      },
    );
    render(<Schedules clock={clock} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "not valid YAML",
    );
  });
});

describe("schedule format helpers", () => {
  it("formatRelative is pure over an injected clock", () => {
    expect(formatRelative("2026-11-11T12:05:00Z", NOW)).toBe("in 5m");
    expect(formatRelative("2026-11-11T09:00:00Z", NOW)).toBe("3h ago");
    expect(formatRelative("2026-11-13T12:00:00Z", NOW)).toBe("in 2d");
    expect(formatRelative("2026-11-11T11:59:50Z", NOW)).toBe("just now");
    expect(formatRelative(null, NOW)).toBe("—");
    expect(formatRelative("garbage", NOW)).toBe("—");
  });

  it("classifies suppressed events", () => {
    expect(isSuppressedEvent("schedule.skipped_overlap")).toBe(true);
    expect(isSuppressedEvent("schedule.governance.skipped")).toBe(true);
    expect(isSuppressedEvent("schedule.fired")).toBe(false);
  });
});
