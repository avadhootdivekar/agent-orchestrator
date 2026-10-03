import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isWideParam, TemplateLaunch } from "../components/TemplateLaunch";
import type { TemplateInfo, TemplateParam } from "../types";

const p = (over: Partial<TemplateParam> & { name: string }): TemplateParam => ({
  description: "",
  required: false,
  enum: null,
  default: null,
  ...over,
});

describe("isWideParam", () => {
  it("keeps numbers, ids, short model names and enums compact", () => {
    expect(isWideParam(p({ name: "wave_size", default: "6" }))).toBe(false);
    expect(isWideParam(p({ name: "m", default: "claude-sonnet-4-5" }))).toBe(false);
    expect(isWideParam(p({ name: "e", enum: ["a", "b"], default: "a" }))).toBe(false);
    expect(isWideParam(p({ name: "x" }))).toBe(false);
  });

  it("gives free-text and long-default params a full row", () => {
    expect(
      isWideParam(p({ name: "branch_policy", description: "Optional free-text guidance" })),
    ).toBe(true);
    expect(isWideParam(p({ name: "long", default: "x".repeat(40) }))).toBe(true);
  });

  it("never widens an enum even with a free-text description", () => {
    expect(isWideParam(p({ name: "e", enum: ["a"], description: "free text" }))).toBe(false);
  });
});

const TEMPLATE: TemplateInfo = {
  name: "tpl",
  description: "d",
  path: "/t",
  source: "builtin",
  params: [
    p({ name: "wave_size", default: "6", description: "Tasks per wave." }),
    p({ name: "max_waves", default: "12" }),
    p({ name: "branch_policy", description: "Optional free-text operator guidance." }),
  ],
  required_agents: [],
  prompt_skeleton: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("TemplateLaunch parameter grid", () => {
  it("renders params in one grid, descriptions as info tips, free text spanning the row", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([TEMPLATE]), { status: 200 })),
    );
    render(<TemplateLaunch onLaunched={() => {}} />);
    const grid = await screen.findByTestId("param-grid");
    expect(grid.children).toHaveLength(3);

    const tip = within(grid).getByRole("button", { name: "Description", description: "Tasks per wave." });
    expect(tip).toHaveAttribute("data-tip", "Tasks per wave.");
    // The description is no longer a block under the input.
    expect(grid.querySelectorAll(".field-hint")).toHaveLength(0);

    const cells = Array.from(grid.children);
    expect(cells[0]).not.toHaveClass("param-wide");
    expect(cells[2]).toHaveClass("param-wide");
    expect(cells[2].querySelector("textarea")).not.toBeNull();
    expect(cells[0].querySelector("input")).not.toBeNull();
  });
});
