import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScheduleUsage } from "../components/ScheduleUsage";
import type { ScheduleUsageDoc } from "../types";

const NOW = Date.parse("2026-11-11T12:00:00Z");

const USAGE: ScheduleUsageDoc = {
  schema: "ao.schedule.usage/v1",
  schedule_id: "nightly",
  at: "2026-11-11T12:00:00+00:00",
  windows: [
    {
      period: "day",
      window_key: "day:2026-11-11",
      window_start: "2026-11-11T00:00:00+00:00",
      resets_at: "2026-11-12T00:00:00+00:00",
      runs_used: 2,
      runs_max: 2,
      actual_usd: 1.5,
      reserved_usd: 0.5,
      max_usd: 5,
      confidence: "partial",
      skipped: 3,
      quota_exhausted: 2,
    },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(usage: Response, events: Response) {
  const fn = vi.fn(async (url: string) =>
    (url.includes("/usage") ? usage : events).clone(),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("ScheduleUsage", () => {
  it("renders quota/spend numbers, confidence, and quota_exhausted skips", async () => {
    const fn = mockFetch(
      json(USAGE),
      json({
        events: [
          {
            ts: "2026-11-11T11:30:00Z",
            event: "schedule.governance.skipped",
            reason: "quota_exhausted",
          },
        ],
      }),
    );
    render(<ScheduleUsage id="nightly" pollMs={60000} clock={() => NOW} />);
    expect(await screen.findByText("runs 2/2")).toBeTruthy();
    expect(screen.getByText(/spend \$1\.50/)).toBeTruthy();
    expect(screen.getByText(/\$0\.50 reserved/)).toBeTruthy();
    expect(screen.getByText("⚠ partial")).toBeTruthy();
    expect(screen.getByText("2 quota_exhausted / 3 skipped")).toBeTruthy();
    const feed = await screen.findByRole("list", { name: "Governance events" });
    expect(feed.textContent).toContain("skipped");
    expect(feed.textContent).toContain("quota_exhausted");
    const urls = fn.mock.calls.map((c) => c[0] as string);
    expect(urls).toContain("/api/schedules/nightly/usage");
    expect(urls).toContain(
      "/api/schedules/governance-events?limit=50&id=nightly",
    );
  });

  it("shows an empty-limits message and an empty feed", async () => {
    mockFetch(json({ ...USAGE, windows: [] }), json({ events: [] }));
    render(<ScheduleUsage id="nightly" pollMs={60000} clock={() => NOW} />);
    expect(await screen.findByText(/No quota or cost limits/)).toBeTruthy();
    expect(await screen.findByText("No governance events.")).toBeTruthy();
  });

  it("surfaces an API error", async () => {
    mockFetch(
      json({ detail: "no such schedule 'nightly'" }, 404),
      json({ events: [] }),
    );
    render(<ScheduleUsage id="nightly" pollMs={60000} clock={() => NOW} />);
    await waitFor(() =>
      expect(screen.getByText(/no such schedule/)).toBeTruthy(),
    );
  });
});
