import { describe, expect, it } from "vitest";
import { BADGE_LABELS, badgesFor } from "../graph/TaskNode";
import type { ViewNode } from "../types";

function node(origin: string): ViewNode {
  return {
    id: "n", label: "n", label_sanitized: false, origin, parent_task_id: null, route: null,
    loop_id: null, iteration: null, spawn_depth: 0, children_count: 0, is_emitter: false,
    is_router: false, is_loop_gate: false, exec_ordinal: null, missing: false, stat: null,
  };
}

describe("sentinel-origin graph styling (T-Sn6Brr AC5)", () => {
  it("gives a sentinel-origin node its own badge, not the unknown-origin catch-all", () => {
    const texts = badgesFor(node("sentinel")).map((b) => b.text);
    expect(texts).toContain(BADGE_LABELS.sentinel);
    expect(texts.some((t) => t.endsWith(" origin"))).toBe(false);
  });

  it("injected and static nodes are unchanged", () => {
    expect(badgesFor(node("static"))).toEqual([]);
    expect(badgesFor(node("injected")).map((b) => b.text)).toEqual([BADGE_LABELS.injected]);
  });
});
