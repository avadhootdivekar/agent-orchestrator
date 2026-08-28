import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import {
  clampTypography,
  DEFAULT_TYPOGRAPHY,
  loadTypography,
  saveTypography,
  TYPOGRAPHY_STORAGE_KEY,
  TypographyControls,
} from "../components/viewer/TypographyControls";

afterEach(() => {
  localStorage.clear();
});

describe("clampTypography", () => {
  it("passes through a value already within range", () => {
    const value = { family: "serif" as const, size: 16, lineHeight: 1.8, letterSpacing: 0.02 };
    expect(clampTypography(value)).toEqual(value);
  });

  it("clamps out-of-range numbers to the nearest bound", () => {
    const clamped = clampTypography({
      family: "mono",
      size: 9999,
      lineHeight: -5,
      letterSpacing: 999,
    });
    expect(clamped.size).toBeLessThanOrEqual(22);
    expect(clamped.lineHeight).toBeGreaterThanOrEqual(1.1);
    expect(clamped.letterSpacing).toBeLessThanOrEqual(0.08);
  });

  it("falls back field-by-field for garbage types, rather than rejecting the whole object", () => {
    const clamped = clampTypography({
      family: "comic-sans",
      size: "not a number",
      lineHeight: null,
      letterSpacing: undefined,
    });
    expect(clamped.family).toBe(DEFAULT_TYPOGRAPHY.family);
    expect(clamped.size).toBe(DEFAULT_TYPOGRAPHY.size);
    expect(clamped.lineHeight).toBe(DEFAULT_TYPOGRAPHY.lineHeight);
    expect(clamped.letterSpacing).toBe(DEFAULT_TYPOGRAPHY.letterSpacing);
  });

  it("defaults entirely for non-object input", () => {
    expect(clampTypography(null)).toEqual(DEFAULT_TYPOGRAPHY);
    expect(clampTypography("garbage")).toEqual(DEFAULT_TYPOGRAPHY);
    expect(clampTypography(42)).toEqual(DEFAULT_TYPOGRAPHY);
  });
});

describe("loadTypography / saveTypography", () => {
  it("returns defaults when nothing is stored", () => {
    expect(loadTypography()).toEqual(DEFAULT_TYPOGRAPHY);
  });

  it("round-trips a valid value", () => {
    const value = { family: "mono" as const, size: 15, lineHeight: 1.4, letterSpacing: -0.01 };
    saveTypography(value);
    expect(loadTypography()).toEqual(value);
  });

  it("clamps a hand-edited, out-of-range value read back from storage", () => {
    localStorage.setItem(
      TYPOGRAPHY_STORAGE_KEY,
      JSON.stringify({ family: "wingdings", size: 500, lineHeight: 50, letterSpacing: -50 }),
    );
    const loaded = loadTypography();
    expect(loaded.family).toBe(DEFAULT_TYPOGRAPHY.family);
    expect(loaded.size).toBeLessThanOrEqual(22);
    expect(loaded.lineHeight).toBeLessThanOrEqual(2.4);
    expect(loaded.letterSpacing).toBeGreaterThanOrEqual(-0.02);
  });

  it("never crashes on malformed JSON, falling back to defaults", () => {
    localStorage.setItem(TYPOGRAPHY_STORAGE_KEY, "{not valid json");
    expect(loadTypography()).toEqual(DEFAULT_TYPOGRAPHY);
  });
});

describe("TypographyControls", () => {
  it("reports a font-family change via onChange without mutating its input", async () => {
    const seen: string[] = [];
    const { rerender } = render(
      <TypographyControls
        value={DEFAULT_TYPOGRAPHY}
        onChange={(next) => seen.push(next.family)}
      />,
    );
    await userEvent.selectOptions(screen.getByLabelText(/font/i, { selector: "select" }), [
      "serif",
    ]);
    expect(seen).toEqual(["serif"]);
    rerender(<TypographyControls value={DEFAULT_TYPOGRAPHY} onChange={() => {}} />);
  });
});
