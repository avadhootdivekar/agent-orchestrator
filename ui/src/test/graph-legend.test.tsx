import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Legend, type LegendProps } from "../graph/Legend";

/** `Legend` component tests (`T-aHktGB` AC-6). Purely presentational -- no React Flow needed. */

function renderLegend(overrides: Partial<LegendProps> = {}) {
  const props: LegendProps = {
    view: "dependency",
    source: "snapshot",
    spawnData: "recorded",
    truncated: false,
    warnings: [],
    ...overrides,
  };
  return render(<Legend {...props} />);
}

function edgesSection() {
  const heading = screen.getByRole("heading", { name: /^Edges/ });
  // The heading's own list is its next sibling within the same <section>.
  return heading.closest("section") as HTMLElement;
}

describe("Legend", () => {
  it("AC-6: the dependency view lists explicit, inferred, and loop edge styles", () => {
    renderLegend({ view: "dependency" });
    const section = within(edgesSection());
    expect(section.getByText(/explicit/i)).not.toBeNull();
    expect(section.getByText(/inferred/i)).not.toBeNull();
    expect(section.getByText(/loop/i)).not.toBeNull();
    // Exactly the 3 dependency-table rows -- no spawn-only rows leaking into this view.
    expect(section.getAllByRole("listitem").length).toBe(3);
  });

  it("AC-6: the spawn view lists injected and loop spawn styles", () => {
    renderLegend({ view: "spawn" });
    const section = within(edgesSection());
    expect(section.getByText(/injected/i)).not.toBeNull();
    expect(section.getByText(/loop/i)).not.toBeNull();
    expect(section.getAllByRole("listitem").length).toBe(2);
  });

  it("AC-6: source=\"unavailable\" shows the 'static dependencies unavailable' note", () => {
    renderLegend({ source: "unavailable" });
    expect(screen.getByText(/static dependencies unavailable/i)).not.toBeNull();
  });

  it("does not show the unavailable note for source=\"snapshot\"", () => {
    renderLegend({ source: "snapshot" });
    expect(screen.queryByText(/static dependencies unavailable/i)).toBeNull();
  });

  it("shows the not-recorded spawn note only when spawn_data is not_recorded", () => {
    renderLegend({ spawnData: "not_recorded" });
    expect(screen.getByText(/were not recorded for this run/i)).not.toBeNull();
  });

  it("shows nothing extra for spawn_data \"none\"", () => {
    renderLegend({ spawnData: "none" });
    expect(screen.queryByText(/spawn relationships/i)).toBeNull();
  });

  it("surfaces the actual 'spec changed' warning text verbatim when present", () => {
    const warning = "The workflow spec changed during this run; showing the spec from session 2.";
    renderLegend({ warnings: [warning, "unrelated warning"] });
    expect(screen.getByText(warning)).not.toBeNull();
    expect(screen.queryByText("unrelated warning")).toBeNull();
  });

  it("shows a truncated note only when truncated is true", () => {
    renderLegend({ truncated: false });
    expect(screen.queryByText(/truncated/i)).toBeNull();
    renderLegend({ truncated: true });
    expect(screen.getByText(/truncated/i)).not.toBeNull();
  });

  it("reuses BADGE_LABELS text for the node badge rows", () => {
    renderLegend();
    expect(screen.getByText(/injected task/i)).not.toBeNull();
    expect(screen.getByText(/router/i)).not.toBeNull();
    expect(screen.getByText(/missing dependency/i)).not.toBeNull();
  });

  it("renders a status chip for every known status word (D-7: never color alone)", () => {
    renderLegend();
    for (const status of ["pending", "running", "succeeded", "failed", "timed_out", "cancelled", "skipped", "not_taken"]) {
      expect(screen.getByText(status)).not.toBeNull();
    }
  });

  it("collapses and expands via the toggle button", async () => {
    renderLegend();
    const toggle = screen.getByRole("button", { name: /legend/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/explicit/i)).not.toBeNull();

    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/explicit/i)).toBeNull();

    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/explicit/i)).not.toBeNull();
  });
});
