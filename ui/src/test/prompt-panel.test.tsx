import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PromptPanel } from "../components/PromptPanel";
import { RunsList } from "../components/RunsList";
import { formatPromptSource, formatShortSha } from "../format";
import type { RunPrompt, RunSummary } from "../types";

const HOSTILE = '<script>window.__pwned = 1</script><img src=x onerror="window.__pwned=2"> **b**';

const PROMPT: RunPrompt = {
  text: "Add rate limiting\nto /orders",
  truncated: false,
  chars: 28,
  source: "cli-prompt-file",
  path: "prompts/run.md",
  sha256: "abcdef0123456789",
  captured_at: "2026-10-02T12:00:00+00:00",
};

describe("format helpers", () => {
  it("shortens sha and labels sources", () => {
    expect(formatShortSha("abcdef0123456789")).toBe("abcdef01");
    expect(formatShortSha("")).toBe("—");
    expect(formatPromptSource("workflow-file")).toBe("workflow file");
    expect(formatPromptSource("mystery")).toBe("mystery");
  });
});

describe("PromptPanel", () => {
  it("shows the empty state when no prompt was recorded", () => {
    render(<PromptPanel prompt={null} />);
    expect(screen.getByText("No prompt recorded for this run")).toBeInTheDocument();
  });

  it("renders text, source, chars and short sha", () => {
    const { container } = render(<PromptPanel prompt={PROMPT} />);
    expect(container.querySelector("pre.prompt-text")?.textContent).toBe(PROMPT.text);
    expect(screen.getByText("--prompt-file")).toBeInTheDocument();
    expect(screen.getByText(/28 chars/)).toBeInTheDocument();
    expect(screen.getByText(/abcdef01/)).toBeInTheDocument();
    expect(screen.queryByText(/Truncated/)).toBeNull();
    expect(screen.queryByText(/has changed/)).toBeNull();
  });

  it("shows truncated and changed notices", () => {
    render(
      <PromptPanel prompt={{ ...PROMPT, truncated: true, chars: 200000 }} changed={true} />,
    );
    expect(screen.getByText(/Truncated: showing the first/)).toBeInTheDocument();
    expect(screen.getByText(/prompt file has changed/)).toBeInTheDocument();
  });

  it("never parses hostile text as HTML", () => {
    const { container } = render(<PromptPanel prompt={{ ...PROMPT, text: HOSTILE }} />);
    expect(container.querySelector("pre.prompt-text")?.textContent).toBe(HOSTILE);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("copies the text to the clipboard", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    render(<PromptPanel prompt={PROMPT} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(PROMPT.text);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});

describe("RunsList prompt preview", () => {
  const row = (over: Partial<RunSummary>): RunSummary => ({
    run_id: "r1",
    workflow_id: "wf",
    status: "succeeded",
    started_at: "2026-10-02T12:00:00+00:00",
    updated_at: "2026-10-02T12:05:00+00:00",
    task_count: 1,
    task_counts: { succeeded: 1 },
    cost_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    wall_seconds: 1,
    active_seconds: 1,
    is_terminal: true,
    is_live: false,
    launch_id: null,
    ...over,
  });

  it("renders the preview as inert text and omits it when absent", async () => {
    const rows = [row({ prompt_preview: HOSTILE }), row({ run_id: "r2", prompt_preview: null })];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const body = url.endsWith("/runs")
          ? rows
          : {
              total_runs: 2,
              runs_by_status: {},
              total_tasks: 0,
              tasks_by_status: {},
              total_cost_usd: 0,
              total_input_tokens: 0,
              total_output_tokens: 0,
              total_wall_seconds: 0,
              total_active_seconds: 0,
            };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const { container } = render(<RunsList onOpen={() => {}} />);
    await screen.findByText("r2");
    const previews = container.querySelectorAll(".prompt-preview");
    expect(previews).toHaveLength(1);
    expect(previews[0].textContent).toBe(HOSTILE);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    vi.unstubAllGlobals();
  });
});
