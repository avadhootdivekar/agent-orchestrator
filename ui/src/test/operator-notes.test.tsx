import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api";
import { RunDetail } from "../components/RunDetail";

const TASK = {
  id: "task-a", status: "running", attempts: 1, started_at: null, ended_at: null,
  duration_seconds: 1, input_tokens: 1, output_tokens: 1, cost_usd: 0.01, origin: "static",
  route: null, output_artifact_path: null, outputs: [], integration_status: null,
  tier_reached: null, conflicted_count: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
  cache_hit_rate: null,
};
function detail(live: boolean) {
  return {
    summary: { run_id: "run-1", workflow_id: "demo", status: live ? "running" : "failed",
      started_at: "2026-09-21T10:00:00+00:00", updated_at: "2026-09-21T10:05:00+00:00",
      task_count: 1, task_counts: { running: 1 }, cost_usd: 0.01, input_tokens: 1, output_tokens: 1,
      wall_seconds: 1, active_seconds: 1, is_terminal: !live, is_live: live, launch_id: null },
    tasks: [TASK], tripped_breakers: [], route_decisions: {}, monitor_decisions: [], run_dir: "/ws/run-1",
    is_live: live, launch: null, integration: null,
  };
}
const NOTE = { id: "n1", ts: "2026-09-21T10:01:00+00:00", source: "dashboard", text: "prefer <b>small</b> diffs" };
const notesState = (over: object = {}) => ({
  run_id: "run-1", accepting: true, max_chars: 2000, max_notes: 100, notes: [], ...over,
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

interface Opts { live?: boolean; state?: object; postStatus?: number; notesStatus?: number }
function setup(opts: Opts = {}) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  let current = notesState(opts.state);
  let netDown = false;
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/notes")) {
      if (method === "POST") {
        if (opts.postStatus && opts.postStatus >= 400) return json({ detail: "run is not live" }, opts.postStatus);
        current = notesState({ notes: [NOTE] });
        return json({ note: NOTE, notes: current }, 201);
      }
      if (netDown) throw new TypeError("Failed to fetch");
      if (opts.notesStatus) return json({ detail: "nope" }, opts.notesStatus);
      return json(current);
    }
    if (url.endsWith("/feedback")) return json({ entries: [] });
    if (url.includes("/log")) return json({ run_id: "run-1", launch_id: null, text: "" });
    if (url.includes("/cancel")) return json({});
    return json(detail(opts.live ?? true));
  });
  vi.stubGlobal("fetch", fn);
  return {
    calls,
    setNotes: (notes: object[]) => { current = notesState({ ...opts.state, notes }); },
    failNotes: (down: boolean) => { netDown = down; },
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("api.notes / api.postNote", () => {
  it("hit the run-scoped notes route with an encoded id and a text-only body", async () => {
    const { calls } = setup();
    await api.notes("r 1");
    await api.postNote("r 1", { text: "hi" });
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "GET /api/runs/r%201/notes",
      "POST /api/runs/r%201/notes",
    ]);
    expect(calls[1].body).toEqual({ text: "hi" });
  });

  it("surfaces the server's reason as an ApiError", async () => {
    setup({ postStatus: 409 });
    await expect(api.postNote("run-1", { text: "x" })).rejects.toMatchObject({ status: 409 });
    await expect(api.postNote("run-1", { text: "x" })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("operator notes box", () => {
  it("always shows the limitation and submits a trimmed note, then lists it as plain text", async () => {
    const { calls } = setup();
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const box = await screen.findByTestId("operator-notes");
    expect(within(box).getByTestId("notes-limitation").textContent).toMatch(/after you send it, not tasks already running/);
    fireEvent.change(within(box).getByLabelText("Operator note"), { target: { value: "  prefer <b>small</b> diffs " } });
    fireEvent.click(within(box).getByRole("button", { name: "Send note" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ text: "prefer <b>small</b> diffs" });
    const item = await screen.findByTestId("operator-note");
    expect(item.textContent).toBe("prefer <b>small</b> diffs");
    expect(item.querySelector("b")).toBeNull(); // escaped, not parsed as HTML
    expect((screen.getByLabelText("Operator note") as HTMLTextAreaElement).value).toBe("");
  });

  it("blocks empty and over-limit drafts client-side", async () => {
    setup({ state: { max_chars: 5 } });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const box = await screen.findByTestId("operator-notes");
    const send = within(box).getByRole("button", { name: "Send note" });
    expect(send).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("Operator note"), { target: { value: "123456" } });
    expect(send).toBeDisabled();
    expect(within(box).getByRole("alert").textContent).toMatch(/too long/);
    fireEvent.change(within(box).getByLabelText("Operator note"), { target: { value: "12345" } });
    expect(send).toBeEnabled();
  });

  it("shows the server error and keeps the draft when the post is rejected", async () => {
    setup({ postStatus: 409 });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const box = await screen.findByTestId("operator-notes");
    fireEvent.change(within(box).getByLabelText("Operator note"), { target: { value: "keep me" } });
    fireEvent.click(within(box).getByRole("button", { name: "Send note" }));
    expect(await screen.findByText(/run is not live/)).toBeInTheDocument();
    expect((screen.getByLabelText("Operator note") as HTMLTextAreaElement).value).toBe("keep me");
  });

  it("is disabled when the server says the run is not accepting notes", async () => {
    setup({ live: false, state: { accepting: false } });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    const box = await screen.findByTestId("operator-notes");
    expect(within(box).getByLabelText("Operator note")).toBeDisabled();
    expect(within(box).getByRole("button", { name: "Send note" })).toBeDisabled();
  });

  it("picks up notes added elsewhere on the next poll, without a reload or a write", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { calls, setNotes } = setup();
      render(<RunDetail runId="run-1" onBack={() => {}} />);
      await screen.findByTestId("operator-notes");
      expect(screen.queryByTestId("operator-note")).toBeNull();
      setNotes([{ ...NOTE, id: "cli1", source: "cli", text: "from the CLI" }]);
      await vi.advanceTimersByTimeAsync(3100);
      expect((await screen.findByTestId("operator-note")).textContent).toBe("from the CLI");
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a network failure as a plain-language hint, then clears it on recovery", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { failNotes } = setup();
      render(<RunDetail runId="run-1" onBack={() => {}} />);
      await screen.findByTestId("operator-notes");
      failNotes(true);
      await vi.advanceTimersByTimeAsync(3100);
      expect(await screen.findByText(/Can't reach the dashboard server/)).toBeInTheDocument();
      failNotes(false);
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(screen.queryByText(/Can't reach the dashboard server/)).toBeNull());
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders nothing on an old backend without the notes route (404)", async () => {
    setup({ notesStatus: 404 });
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    await screen.findByTestId("section-live-inputs");
    await waitFor(() => expect(screen.queryByTestId("operator-notes")).toBeNull());
  });
});

describe("Cancel confirmation on the run page", () => {
  it("does nothing when the operator declines", async () => {
    const { calls } = setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(calls.some((c) => c.url.includes("/cancel"))).toBe(false);
  });

  it("cancels once confirmed", async () => {
    const { calls } = setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/runs/run-1/cancel")).toBe(true));
  });
});
