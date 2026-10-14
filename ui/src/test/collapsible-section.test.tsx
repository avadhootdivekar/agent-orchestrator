import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CollapsibleSection } from "../components/CollapsibleSection";
import { RunDetail } from "../components/RunDetail";
import {
  MAX_SECTION_KEYS,
  SECTIONS_STORAGE_KEY,
  readSectionState,
  writeSectionState,
} from "../sections";
import type { RunDetail as RunDetailData, TaskStat } from "../types";

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("sections storage", () => {
  it("returns empty state when nothing is stored", () => {
    expect(readSectionState()).toEqual({});
  });

  it("round-trips a flag", () => {
    writeSectionState("prompt", false);
    writeSectionState("tasks", true);
    expect(readSectionState()).toEqual({ prompt: false, tasks: true });
  });

  it("rejects non-object, non-boolean values, bad keys and malformed JSON", () => {
    for (const raw of ["not json", "[]", "null", "5", '"x"']) {
      window.localStorage.setItem(SECTIONS_STORAGE_KEY, raw);
      expect(readSectionState()).toEqual({});
    }
    window.localStorage.setItem(
      SECTIONS_STORAGE_KEY,
      JSON.stringify({ ok: true, "Bad Key": true, "../x": false, str: "yes", num: 1 }),
    );
    expect(readSectionState()).toEqual({ ok: true });
  });

  it("caps the number of stored keys", () => {
    const many = Object.fromEntries(Array.from({ length: 200 }, (_, i) => [`k${i}`, true]));
    window.localStorage.setItem(SECTIONS_STORAGE_KEY, JSON.stringify(many));
    expect(Object.keys(readSectionState())).toHaveLength(MAX_SECTION_KEYS);
  });

  it("ignores invalid ids on write and survives throwing storage", () => {
    writeSectionState("Bad Id", true);
    expect(window.localStorage.getItem(SECTIONS_STORAGE_KEY)).toBeNull();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(readSectionState()).toEqual({});
    expect(() => writeSectionState("prompt", true)).not.toThrow();
  });
});

describe("CollapsibleSection", () => {
  it("renders a button whose aria-expanded tracks state and controls the region", async () => {
    render(
      <CollapsibleSection id="demo" title="Demo">
        <p>body</p>
      </CollapsibleSection>,
    );
    const btn = screen.getByRole("button", { name: /Demo/ });
    expect(btn).toHaveAttribute("aria-expanded", "true");
    const region = document.getElementById(btn.getAttribute("aria-controls")!)!;
    expect(region).toHaveAttribute("role", "region");
    expect(region).not.toHaveAttribute("hidden");
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(region).toHaveAttribute("hidden");
  });

  it("toggles with Enter and Space", async () => {
    render(
      <CollapsibleSection id="demo" title="Demo">
        x
      </CollapsibleSection>,
    );
    const btn = screen.getByRole("button", { name: /Demo/ });
    btn.focus();
    await userEvent.keyboard("{Enter}");
    expect(btn).toHaveAttribute("aria-expanded", "false");
    await userEvent.keyboard(" ");
    expect(btn).toHaveAttribute("aria-expanded", "true");
  });

  it("honours defaultOpen when nothing is stored, then persists across remount", async () => {
    const { unmount } = render(
      <CollapsibleSection id="demo" title="Demo" defaultOpen={false}>
        x
      </CollapsibleSection>,
    );
    const btn = screen.getByRole("button", { name: /Demo/ });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(btn);
    unmount();
    render(
      <CollapsibleSection id="demo" title="Demo" defaultOpen={false}>
        x
      </CollapsibleSection>,
    );
    expect(screen.getByRole("button", { name: /Demo/ })).toHaveAttribute("aria-expanded", "true");
    expect(readSectionState()).toEqual({ demo: true });
  });

  it("lazy mounts children on first open and keeps them mounted", async () => {
    const mount = vi.fn();
    function Probe() {
      mount();
      return <span>probe</span>;
    }
    render(
      <CollapsibleSection id="lazy" title="Lazy" defaultOpen={false} lazy>
        <Probe />
      </CollapsibleSection>,
    );
    expect(mount).not.toHaveBeenCalled();
    const btn = screen.getByRole("button", { name: /Lazy/ });
    await userEvent.click(btn);
    expect(screen.getByText("probe")).toBeInTheDocument();
    await userEvent.click(btn);
    expect(screen.getByText("probe")).toBeInTheDocument();
  });

  it("actions live outside the toggle button and do not toggle", async () => {
    const onAct = vi.fn();
    render(
      <CollapsibleSection id="demo" title="Demo" actions={<button onClick={onAct}>Act</button>}>
        x
      </CollapsibleSection>,
    );
    const act = screen.getByRole("button", { name: "Act" });
    expect(screen.getByRole("button", { name: /Demo/ })).not.toContainElement(act);
    await userEvent.click(act);
    expect(onAct).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /Demo/ })).toHaveAttribute("aria-expanded", "true");
  });
});

const TASK: TaskStat = {
  id: "task-a", status: "succeeded", attempts: 1, started_at: null, ended_at: null,
  duration_seconds: 1, input_tokens: 1, output_tokens: 1, cost_usd: 0, origin: "static",
  route: null, output_artifact_path: null, outputs: [], integration_status: null,
  tier_reached: null, conflicted_count: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
  cache_hit_rate: null, dispatch_cycle: 1, not_taken_reason: null,
} as unknown as TaskStat;

const DETAIL = {
  summary: {
    run_id: "run-1", workflow_id: "demo", status: "succeeded", started_at: null, updated_at: null,
    task_count: 1, task_counts: { succeeded: 1 }, cost_usd: 0, input_tokens: 1, output_tokens: 1,
    wall_seconds: 1, active_seconds: 1, is_terminal: true, is_live: false, launch_id: null,
  },
  tasks: [TASK], tripped_breakers: [], route_decisions: {}, monitor_decisions: [],
  run_dir: "/ws/run-1", is_live: false, launch: null, integration: null, graph_version: null,
  prompt: { source: "inline", text: "hello prompt", chars: 12, truncated: false, sha256: "a".repeat(64), path: "p", captured_at: "2026-09-21T10:00:00+00:00" },
} as unknown as RunDetailData;

function stubRunFetch() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      const body = url.includes("/log") ? { run_id: "run-1", launch_id: null, text: "" } : DETAIL;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

describe("RunDetail section order and collapse", () => {
  it("orders Live inputs above Now running, Summary, Prompt and Tasks", async () => {
    stubRunFetch();
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    await screen.findByText("task-a");
    const ids = ["section-live-inputs", "section-summary", "section-prompt", "section-tasks"];
    const nodes = ids.map((id) => screen.getByTestId(id));
    for (let i = 0; i < nodes.length - 1; i++) {
      expect(
        nodes[i].compareDocumentPosition(nodes[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: /Resume/ })).toBeInTheDocument();
  });

  it("collapses the summary, persists it across remount, and defaults Feedback/Signals closed", async () => {
    const calls = stubRunFetch();
    const { unmount } = render(<RunDetail runId="run-1" onBack={() => {}} />);
    await screen.findByText("task-a");
    const toggle = (name: RegExp) => screen.getByRole("button", { name });
    expect(toggle(/Feedback/)).toHaveAttribute("aria-expanded", "false");
    expect(toggle(/Implicit signals/)).toHaveAttribute("aria-expanded", "false");
    expect(calls.some((u) => u.includes("signals"))).toBe(false);
    const summary = toggle(/^▾Summary|Summary$/);
    await userEvent.click(summary);
    expect(summary).toHaveAttribute("aria-expanded", "false");
    unmount();
    render(<RunDetail runId="run-1" onBack={() => {}} />);
    await screen.findByText("task-a");
    expect(toggle(/Summary$/)).toHaveAttribute("aria-expanded", "false");
  });
});
