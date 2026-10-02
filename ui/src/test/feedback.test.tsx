import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "../components/RunDetail";
import { MAX_NOTE_CHARS, formatCountOrNA, formatFeedbackSplit, noteError } from "../format";

const TASK = {
  id: "task-a", status: "succeeded", attempts: 1, started_at: null, ended_at: null,
  duration_seconds: 1, input_tokens: 1, output_tokens: 1, cost_usd: 0.01, origin: "static",
  route: null, output_artifact_path: null, outputs: [], integration_status: null,
  tier_reached: null, conflicted_count: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
  cache_hit_rate: null,
};
const DETAIL = {
  summary: { run_id: "run-1", workflow_id: "demo", status: "succeeded", started_at: "2026-09-21T10:00:00+00:00",
    updated_at: "2026-09-21T10:05:00+00:00", task_count: 1, task_counts: { succeeded: 1 }, cost_usd: 0.01,
    input_tokens: 1, output_tokens: 1, wall_seconds: 1, active_seconds: 1, is_terminal: true, is_live: false, launch_id: null },
  tasks: [TASK], tripped_breakers: [], route_decisions: {}, monitor_decisions: [], run_dir: "/ws/run-1",
  is_live: false, launch: null, integration: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Opts { entries?: unknown[]; postStatus?: number; signals?: unknown }
function setup(opts: Opts = {}) {
  const posts: { url: string; body: unknown }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST" && url.endsWith("/feedback")) {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      return opts.postStatus && opts.postStatus >= 400
        ? json({ detail: "note rejected" }, opts.postStatus)
        : json({ ok: true });
    }
    if (url.endsWith("/feedback")) return json({ entries: opts.entries ?? [], effective: {}, tasks: {} });
    if (url.includes("/signals")) {
      return json(opts.signals ?? { signals: { run_status: "succeeded", killed: false, tripped_breakers: 0, breaker_pauses: 0, breaker_kills: 0 } });
    }
    if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: "" });
    return json(DETAIL);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, posts };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("run-level feedback", () => {
  it("posts the exact body", async () => {
    const { posts } = setup();
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const group = await screen.findByRole("group", { name: "Rate this run" });
    fireEvent.click(within(group).getByRole("button", { name: /bad/ }));
    fireEvent.click(within(group).getByRole("button", { name: "wrong" }));
    fireEvent.change(within(group).getByLabelText("Rate this run note"), { target: { value: "  needs work " } });
    fireEvent.click(within(group).getByRole("button", { name: /Submit run rating/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toBe("/api/runs/run-1/feedback");
    expect(posts[0].body).toEqual({ scope: "run", rating: "bad", reasons: ["wrong"], note: "needs work" });
  });

  it("requires a rating and blocks over-long notes", async () => {
    const { posts } = setup();
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const group = await screen.findByRole("group", { name: "Rate this run" });
    const submit = within(group).getByRole("button", { name: /Submit run rating/ });
    expect(submit).toBeDisabled();
    fireEvent.click(within(group).getByRole("button", { name: /good/ }));
    expect(submit).toBeEnabled();
    fireEvent.change(within(group).getByLabelText("Rate this run note"), { target: { value: "x".repeat(MAX_NOTE_CHARS + 1) } });
    expect(submit).toBeDisabled();
    expect(within(group).getByRole("alert")).toHaveTextContent(/limit is 2000/);
    expect(posts).toHaveLength(0);
  });

  it("surfaces API errors in the banner", async () => {
    setup({ postStatus: 400 });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const group = await screen.findByRole("group", { name: "Rate this run" });
    fireEvent.click(within(group).getByRole("button", { name: /ok/ }));
    fireEvent.click(within(group).getByRole("button", { name: /Submit run rating/ }));
    expect(await screen.findByText("note rejected")).toBeInTheDocument();
  });

  it("posts a task rating with task_id", async () => {
    const { posts } = setup();
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const group = await screen.findByRole("group", { name: "Rate task task-a" });
    fireEvent.click(within(group).getByRole("button", { name: /good/ }));
    fireEvent.click(within(group).getByRole("button", { name: /Submit task rating/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ scope: "task", task_id: "task-a", rating: "good", reasons: [] });
  });

  it("renders notes as text, never HTML", async () => {
    setup({ entries: [{ ts: "2026-09-21T10:00:00+00:00", scope: "run", task_id: null, rating: "bad", reasons: ["wrong"], note: "<img src=x onerror=alert(1)><b>hi</b>", source: "dashboard" }] });
    const { container } = render(<RunDetail runId="run-1" onBack={() => {}} />);
    const note = await screen.findByTestId("feedback-note");
    expect(note.textContent).toBe("<img src=x onerror=alert(1)><b>hi</b>");
    expect(container.querySelector("img")).toBeNull();
    expect(note.querySelector("b")).toBeNull();
  });
});

describe("implicit signals + survival", () => {
  it("fetches survival only on demand and shows unavailable state", async () => {
    const { fn } = setup({ signals: { signals: { run_status: "succeeded", killed: false, tripped_breakers: 1, breaker_pauses: 0, breaker_kills: 0 }, survival: { requested: true, available: false, reason: "not a git repo", ref: null, total: null, tasks: [] } } });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    expect(await screen.findByText(/breakers tripped: 1/)).toBeInTheDocument();
    expect(fn.mock.calls.some(([u]) => String(u).includes("survival=true"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Compute survival/ }));
    expect(await screen.findByText(/Survival unavailable — not a git repo/)).toBeInTheDocument();
    expect(fn.mock.calls.some(([u]) => String(u).includes("survival=true"))).toBe(true);
  });

  it("renders survival rows", async () => {
    setup({ signals: { signals: { run_status: "succeeded", killed: false, tripped_breakers: 0, breaker_pauses: 0, breaker_kills: 0 }, survival: { requested: true, available: true, reason: null, ref: null, total: null, tasks: [{ run_id: "run-1", task_id: "task-a", attribution: "task", confidence: null, commits: 1, files: 1, lines_added: 10, lines_survived: 5, survival_rate: 0.5, flags: ["likely_worthless"], unavailable: null, note: null }] } } });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Compute survival/ }));
    expect(await screen.findByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText(/likely_worthless/)).toBeInTheDocument();
  });
});

describe("feedback helpers", () => {
  it("noteError counts trimmed length", () => {
    expect(noteError("x".repeat(MAX_NOTE_CHARS))).toBeNull();
    expect(noteError(`  ${"x".repeat(MAX_NOTE_CHARS)}  `)).toBeNull();
    expect(noteError("x".repeat(MAX_NOTE_CHARS + 1))).toMatch(/2001/);
  });
  it("formats split and n/a", () => {
    expect(formatFeedbackSplit({ fb_good: 1, fb_ok: 2, fb_bad: 3, fb_rated_tasks: 6 })).toBe("1g / 2o / 3b");
    expect(formatFeedbackSplit({ fb_good: 0, fb_ok: 0, fb_bad: 0, fb_rated_tasks: 0 })).toBe("—");
    expect(formatCountOrNA(0, 0)).toBe("n/a");
    expect(formatCountOrNA(0, 3)).toBe("0");
  });
});
