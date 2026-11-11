import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PausedRuns } from "../components/PausedRuns";
import type { PausedRun } from "../types";

const NOW = Date.parse("2026-11-11T12:00:00Z");

const RUN: PausedRun = {
  run_id: "run-1",
  paused_at: "2026-11-11T11:30:00+00:00",
  pause_expires_at: "2026-11-11T22:00:00+00:00",
  spent_usd: 1.5,
  cap_usd: 2,
  overshoot_usd: 0,
  default_add_usd: 1,
  max_add_usd: 1,
  resumable: true,
  refusal: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** GET paused-runs -> `list`; POST resume-capped -> `post` (a fresh clone per call). */
function mockFetch(list: PausedRun[], post?: Response) {
  const fn = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") return (post ?? json({})).clone();
    return json({ schedule_id: "nightly", paused: list });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const postCalls = (fn: ReturnType<typeof mockFetch>) =>
  fn.mock.calls.filter(
    (c) => (c[1] as RequestInit | undefined)?.method === "POST",
  );

afterEach(() => vi.unstubAllGlobals());

describe("PausedRuns", () => {
  it("renders nothing when no run is paused", async () => {
    const fn = mockFetch([]);
    const { container } = render(<PausedRuns id="nightly" pollMs={60000} />);
    await waitFor(() => expect(fn).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("shows the overshoot only when spend is above the cap", async () => {
    mockFetch([{ ...RUN, spent_usd: 2.75, overshoot_usd: 0.75 }]);
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    expect(await screen.findByText(/\(overshoot \$0\.75\)/)).toBeTruthy();
  });

  it("lists spend vs cap, the expiry countdown and a default amount", async () => {
    mockFetch([RUN]);
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    expect(
      await screen.findByText(/spent \$1\.50 \/ cap \$2\.00/),
    ).toBeTruthy();
    expect(screen.getByText(/pause expires in 10h/)).toBeTruthy();
    const input = screen.getByLabelText(
      "Amount to add for run-1",
    ) as HTMLInputElement;
    expect(input.value).toBe("1");
    expect(screen.getByRole("link", { name: "run-1" })).toBeTruthy();
  });

  it("posts the bounded amount and reports it as queued", async () => {
    const fn = mockFetch(
      [RUN],
      json(
        {
          queued: true,
          schedule_id: "nightly",
          run_id: "run-1",
          add_usd: 0.5,
          new_cap_usd: 2,
        },
        202,
      ),
    );
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    const input = await screen.findByLabelText("Amount to add for run-1");
    fireEvent.change(input, { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(
      await screen.findByText(/queued: \+\$0\.50, new cap \$2\.00/),
    ).toBeTruthy();
    const [call] = postCalls(fn);
    expect(call[0]).toBe("/api/schedules/nightly/runs/run-1/resume-capped");
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
      add_usd: 0.5,
    });
  });

  it("disables Resume with the reason when the pause expired", async () => {
    mockFetch([{ ...RUN, resumable: false, refusal: "expired" }]);
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    const button = (await screen.findByRole("button", {
      name: "Resume",
    })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("(expired)")).toBeTruthy();
  });

  it("disables Resume for an amount above the per-resume maximum", async () => {
    const fn = mockFetch([RUN]);
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    const input = await screen.findByLabelText("Amount to add for run-1");
    fireEvent.change(input, { target: { value: "5" } });
    const button = screen.getByRole("button", {
      name: "Resume",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/amount must be > 0 and ≤ \$1\.00/)).toBeTruthy();
    fireEvent.click(button);
    expect(postCalls(fn)).toHaveLength(0);
  });

  it("shows 'bounded by period headroom' and no ceiling when max_add_usd is null", async () => {
    const fn = mockFetch([{ ...RUN, max_add_usd: null }]);
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    const input = await screen.findByLabelText("Amount to add for run-1");
    expect(screen.getByText(/bounded by period headroom/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "5000" } });
    const button = screen.getByRole("button", {
      name: "Resume",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(screen.queryByText(/100,?000/)).toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(postCalls(fn)).toHaveLength(1));
  });

  it("surfaces a 409 refusal reason inline", async () => {
    mockFetch(
      [RUN],
      json(
        {
          detail: "only $0.20 left in the day window",
          reason: "no_headroom",
          remaining_usd: 0.2,
        },
        409,
      ),
    );
    render(<PausedRuns id="nightly" pollMs={60000} clock={() => NOW} />);
    await screen.findByLabelText("Amount to add for run-1");
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(
      await screen.findByText(/refused \(no_headroom\): only \$0\.20 left/),
    ).toBeTruthy();
  });
});
