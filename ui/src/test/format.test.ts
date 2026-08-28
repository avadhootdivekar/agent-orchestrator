import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatCost,
  formatCount,
  formatDuration,
  formatTimestamp,
  languageFor,
  statusGlyph,
  statusTone,
} from "../format";

describe("formatBytes", () => {
  it("uses bytes below 1 KiB", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("steps up through the units", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(formatBytes(1024 ** 3)).toBe("1.0 GB");
  });

  it("drops the decimal once the value is large within a unit", () => {
    expect(formatBytes(1024 * 20)).toBe("20 KB");
  });

  it("renders nonsense input as an em dash rather than NaN", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });
});

describe("formatDuration", () => {
  it("never reports a real duration as 0s", () => {
    expect(formatDuration(0.4)).toBe("<1s");
  });

  it("formats seconds, minutes, and hours", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(200)).toBe("3m 20s");
    expect(formatDuration(8100)).toBe("2h 15m");
  });

  it("handles missing values", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
  });
});

describe("formatCost", () => {
  it("keeps 4 decimals under a cent so small costs never read as free", () => {
    expect(formatCost(0.0032)).toBe("$0.0032");
  });

  it("uses 2 decimals at and above a cent", () => {
    expect(formatCost(1.5)).toBe("$1.50");
    expect(formatCost(25.664)).toBe("$25.66");
  });

  it("renders exact zero plainly and missing values as an em dash", () => {
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(null)).toBe("—");
  });
});

describe("formatCount", () => {
  it("compacts thousands and millions", () => {
    expect(formatCount(999)).toBe("999");
    expect(formatCount(1284)).toBe("1.3K");
    expect(formatCount(2_400_000)).toBe("2.4M");
  });
});

describe("formatTimestamp", () => {
  it("renders an em dash for empty or invalid input", () => {
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp("")).toBe("—");
    expect(formatTimestamp("not-a-date")).toBe("—");
  });

  it("renders a real ISO timestamp", () => {
    expect(formatTimestamp("2026-07-24T10:30:00+00:00")).not.toBe("—");
  });
});

describe("statusTone / statusGlyph", () => {
  it("maps terminal success and failure to their tones", () => {
    expect(statusTone("succeeded")).toBe("good");
    expect(statusTone("failed")).toBe("critical");
    expect(statusTone("timed_out")).toBe("critical");
    expect(statusTone("running")).toBe("active");
  });

  it("treats every quiet status as neutral", () => {
    for (const status of ["pending", "skipped", "cancelled", "not_taken"]) {
      expect(statusTone(status)).toBe("neutral");
    }
  });

  it("gives every status a distinct glyph so color is never the only channel", () => {
    const statuses = [
      "succeeded",
      "failed",
      "running",
      "cancelled",
      "skipped",
      "not_taken",
      "timed_out",
    ];
    const glyphs = statuses.map(statusGlyph);
    expect(new Set(glyphs).size).toBe(statuses.length);
  });
});

describe("languageFor", () => {
  it("maps known extensions and falls back to text", () => {
    expect(languageFor("engine.py")).toBe("python");
    expect(languageFor("App.tsx")).toBe("typescript");
    expect(languageFor("workflow.json")).toBe("json");
    expect(languageFor("LICENSE")).toBe("text");
  });
});
