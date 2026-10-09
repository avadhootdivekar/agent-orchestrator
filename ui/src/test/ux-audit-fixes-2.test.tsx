import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Usage } from "../components/Usage";
import { RunsList } from "../components/RunsList";
import { Settings } from "../components/Settings";
import { ApiError } from "../api";
import { errorMessage, NETWORK_ERROR_MESSAGE } from "../errors";
import type { UsageReport } from "../types";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const STATS = {
  total_runs: 0, runs_by_status: {}, total_tasks: 0, tasks_by_status: {}, total_cost_usd: 0,
  total_input_tokens: 0, total_output_tokens: 0, total_wall_seconds: 0, total_active_seconds: 0,
};
const REPORT: UsageReport = {
  runs_scanned: 2, verdicts_found: 3, reviews_seen: 5, groups: [], outcomes: [],
  runs_rated: 1, feedback_errors: 0, skipped: [], survival_available: false,
  survival_unavailable_reason: null, survival_ref: null, run_signals: [],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("errorMessage (#6)", () => {
  it("keeps an ApiError's own message", () => {
    expect(errorMessage(new ApiError("run not found", 404))).toBe("run not found");
  });

  it("maps a network TypeError to a plain-language hint", () => {
    expect(errorMessage(new TypeError("Failed to fetch"))).toBe(NETWORK_ERROR_MESSAGE);
  });

  it("stringifies anything else", () => {
    expect(errorMessage(new Error("boom"))).toBe("Error: boom");
    expect(errorMessage("plain")).toBe("plain");
  });

  it("surfaces the hint in a view when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<Settings />);
    expect(await screen.findByText(NETWORK_ERROR_MESSAGE)).toBeTruthy();
    expect(screen.queryByText(/Failed to fetch/)).toBeNull();
  });
});

describe("RunsList Refresh feedback (#10)", () => {
  it("relabels and disables Refresh while the request is in flight", async () => {
    let release: () => void = () => {};
    let held = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/launches")) return json([]);
        if (url.endsWith("/api/runs/stats")) {
          // Hold only the second round (the manual click); the first poll resolves at once.
          if (held) await new Promise<void>((r) => { release = r; });
          return json(STATS);
        }
        return json([]);
      }),
    );
    render(<RunsList onOpen={() => {}} />);
    await screen.findByText(/No runs yet/);
    held = true;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const busy = await screen.findByRole("button", { name: "Refreshing…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    release();
    const idle = await screen.findByRole("button", { name: "Refresh" });
    expect((idle as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("Usage reload feedback (#10)", () => {
  it("shows 'Updating…' over the old report while a reload is in flight", async () => {
    let hold = false;
    let release: () => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/runs")) return json([{ run_id: "run-1" }, { run_id: "run-2" }]);
        if (hold) await new Promise<void>((r) => { release = r; });
        return json(REPORT);
      }),
    );
    render(<Usage />);
    await screen.findByText(/verdicts found 3\/5/);
    expect(screen.queryByText("Updating…")).toBeNull();

    hold = true;
    await userEvent.click(screen.getByLabelText(/include survival/));
    expect(await screen.findByText("Updating…")).toBeTruthy();
    expect(screen.getByText(/verdicts found 3\/5/)).toBeTruthy(); // old report stays visible
    release();
    await waitFor(() => expect(screen.queryByText("Updating…")).toBeNull());
  });
});
