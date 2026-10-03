import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunSummaryPanel } from "../components/RunSummaryPanel";
import type { RunLiveSummary } from "../types";

const META = {
  updated_at: "2026-10-03T10:00:00+00:00",
  final: false,
  model: "haiku",
  run_cost_usd: 12.5,
  summary_cost_usd: 0.05,
  calls: 3,
  settled_tasks: 15,
  threshold_usd: 5,
};

describe("RunSummaryPanel", () => {
  it("renders nothing when no summary exists (cheap runs)", () => {
    const { container } = render(<RunSummaryPanel summary={null} />);
    expect(container).toBeEmptyDOMElement();
    const none: RunLiveSummary = { run_id: "r", available: false, text: "", meta: null };
    const again = render(<RunSummaryPanel summary={none} />);
    expect(again.container).toBeEmptyDOMElement();
  });

  it("shows the digest as plain text with live/final state and cost footer", () => {
    render(
      <RunSummaryPanel
        summary={{ run_id: "r", available: true, text: "## Done\n<b>x</b>", meta: META }}
      />,
    );
    const panel = screen.getByTestId("run-summary");
    expect(panel).toHaveTextContent("live");
    expect(panel).toHaveTextContent("3 calls");
    // Model output is untrusted: HTML must stay literal text, never become an element.
    expect(panel).toHaveTextContent("<b>x</b>");
    expect(panel.querySelector("b")).toBeNull();
  });

  it("labels the end-of-run summary as final", () => {
    render(
      <RunSummaryPanel
        summary={{ run_id: "r", available: true, text: "t", meta: { ...META, final: true } }}
      />,
    );
    expect(screen.getByTestId("run-summary")).toHaveTextContent("final");
  });
});
