import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Usage } from "../components/Usage";
import type { UsageGroup, UsageReport } from "../types";

const GROUP: UsageGroup = {
  agent: "dev", model: "sonnet", effort: "medium", tasks: 4, succeeded: 3, failed: 1, retried: 1,
  cost_usd: 2, input_tokens: 0, output_tokens: 0, reviewed: 2, review_fail: 1, critical: 0,
  major: 0, minor: 0, must_fix: 0, fb_good: 2, fb_ok: 1, fb_bad: 0, fb_unnecessary: 1,
  fb_rated_tasks: 3, false_pass_candidates: 1, false_fail_candidates: 0, verdict_rated_pairs: 2,
  lines_added: 100, lines_survived: 80, survival_tasks: 2, survival_low_tasks: 0,
  flags: ["often-unnecessary"], mean_cost_usd: 0.5, retry_rate: 0.25, review_fail_rate: 0.5,
  survival_rate: 0.8, fb_bad_rate: 0, reviewer_disagreement_rate: 0.5,
};

const REPORT: UsageReport = {
  runs_scanned: 2, verdicts_found: 3, reviews_seen: 5, groups: [GROUP], outcomes: [],
  runs_rated: 1, feedback_errors: 0, skipped: [], survival_available: false,
  survival_unavailable_reason: null, survival_ref: null, run_signals: [],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockFetch(report: UsageReport | Response) {
  const fn = vi.fn(async (url: string) => {
    if (url.startsWith("/api/runs")) {
      return json([{ run_id: "run-1" }, { run_id: "run-2" }]);
    }
    return report instanceof Response ? report.clone() : json(report);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Usage view", () => {
  it("renders groups, rates, flags and the coverage line", async () => {
    mockFetch(REPORT);
    render(<Usage />);
    expect(await screen.findByText("sonnet")).toBeInTheDocument();
    const row = screen.getByText("sonnet").closest("tr")!;
    expect(row).toHaveTextContent("25.0%"); // retry
    expect(row).toHaveTextContent("2g / 1o / 0b");
    expect(row).toHaveTextContent("1/2"); // reviewer disagreements
    expect(row).toHaveTextContent("80.0%");
    expect(screen.getByText(/often-unnecessary/)).toHaveTextContent("⚠");
    expect(screen.getByText(/verdicts found 3\/5/)).toHaveTextContent("feedback: 1 of 2 runs rated");
    expect(screen.getByText(/survival: not requested/)).toBeInTheDocument();
    expect(screen.queryByText("Outcome vs charter")).toBeNull();
  });

  it("shows n/a rather than 0 when nothing is rated", async () => {
    mockFetch({
      ...REPORT,
      groups: [{ ...GROUP, fb_rated_tasks: 0, fb_good: 0, fb_ok: 0, fb_unnecessary: 0, verdict_rated_pairs: 0, survival_rate: null, flags: [] }],
    });
    render(<Usage />);
    const row = (await screen.findByText("sonnet")).closest("tr")!;
    expect(row).toHaveTextContent("n/a");
    expect(row).not.toHaveTextContent("0g");
  });

  it("shows an honest empty state", async () => {
    mockFetch({ ...REPORT, groups: [], runs_scanned: 0, reviews_seen: 0, verdicts_found: 0 });
    render(<Usage />);
    expect(await screen.findByText(/No task usage found/)).toBeInTheDocument();
  });

  it("surfaces API errors", async () => {
    mockFetch(json({ detail: "boom" }, 500));
    render(<Usage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("renders the outcome section when outcomes exist", async () => {
    mockFetch({
      ...REPORT,
      outcomes: [{ run_id: "run-1", checkpoints_seen: 2, checkpoints_found: 1, last_decision: "continue",
        criteria_met: 3, criteria_unmet: 1, criteria_deferred: 0, alignment: {},
        final_verify: { met: 4, partial: 1, not_met: 0 } }],
    });
    render(<Usage />);
    expect(await screen.findByText("Outcome vs charter")).toBeInTheDocument();
    expect(screen.getByText("4 / 1 / 0")).toBeInTheDocument();
  });

  it("toggling survival re-requests with survival=true", async () => {
    const fn = mockFetch(REPORT);
    render(<Usage />);
    await screen.findByText("sonnet");
    fireEvent.click(screen.getByLabelText(/include survival/));
    await waitFor(() =>
      expect(fn.mock.calls.some(([u]) => String(u).includes("/api/usage?survival=true"))).toBe(true),
    );
  });

  it("filtering runs sends run_id params", async () => {
    const fn = mockFetch(REPORT);
    render(<Usage />);
    await screen.findByText("sonnet");
    fireEvent.click(await screen.findByLabelText("run-2"));
    await waitFor(() =>
      expect(fn.mock.calls.some(([u]) => String(u).endsWith("/api/usage?run_id=run-1"))).toBe(true),
    );
  });
});
